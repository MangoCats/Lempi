//! Where diagnostics go, and how they are written `[GDE-HST-070]`.
//!
//! **The library emits through `tracing`; this module is the one place a line
//! is written.** Every binary installs it through `cli::Spec::parse`, so none
//! of the seventeen can forget, and a future host -- a phone -- installs its
//! own instead: a logcat layer, where an app's stdout and stderr are discarded
//! `[GDE-HST-020]`.
//!
//! What it writes is decided by four constraints `[GDE-HST-120..150]`, each
//! answering something that would otherwise break:
//!
//! * **Message only, and byte-identical.** `build/verify-playing.sh` anchors on
//!   `^clock: frames=` and two radio tests match `error|recover`. A line is its
//!   message, then any fields as ` name=value` -- no timestamp, level or
//!   target, which the journal already records or the reader does not want.
//! * **A priority only where it differs, and only into the journal.** Under
//!   systemd, `SyslogLevelPrefix=yes` turns a leading `<4>` into a real
//!   priority and strips it. Warnings get `<4>`, errors `<3>`; **info gets
//!   nothing**, because info is the stream's own priority -- so an info line
//!   is exactly what `println!` wrote, and the strict reader sees no change on
//!   any node, whatever its `SyslogLevelPrefix` says. The prefix is written
//!   only when stderr *is* the journal, which `JOURNAL_STREAM` identifies;
//!   elsewhere a literal `<4>` would land in `lempi-local.log`.
//! * **The engine tick never blocks** `[GDE-FBD-090]`, `[SPEC-APS-070]`. A
//!   thread that marks itself with [`this_thread_must_not_block`] hands its
//!   lines to a bounded queue drained by a writer thread, and when the queue
//!   is full it drops rather than waits -- counting what it dropped, which the
//!   writer reports, because a lost line nobody counted is a silent success.
//!   Every other thread writes synchronously: a message printed just before
//!   `process::exit` must not be lost in a queue the exit never drains.
//! * **Filtered by crate, without a regex engine** `[GDE-HST-150]`: this
//!   project's crates at info, everything else at warning. `symphonia` and
//!   `tungstenite` speak `log`, carried here by `tracing-log`; before this,
//!   no `log` logger existed and their warnings went nowhere.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::cell::Cell;
use std::fmt::{self, Write as _};
use std::io::Write as _;
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, SyncSender};
use std::sync::OnceLock;
use std::time::Duration;

use tracing::field::{Field, Visit};
use tracing::{Event, Level, Subscriber};
use tracing_log::NormalizeEvent;
use tracing_subscriber::filter::{LevelFilter, Targets};
use tracing_subscriber::layer::{Context, Layer, SubscriberExt};

/// How many lines a thread that may not block can have in flight.
///
/// Sized for the appliance, not a server: the engine thread speaks rarely --
/// a clock line, an echo decision -- and a thousand lines is minutes of that
/// even at startup, at a few hundred bytes each. `tracing-appender`'s default
/// is 128,000 lines, which is why its writer was not used `[GDE-HST-380]`.
const QUEUE_LINES: usize = 1024;

thread_local! {
    static MUST_NOT_BLOCK: Cell<bool> = const { Cell::new(false) };
    /// The underflow count of the file this thread is decoding right now, if
    /// it is inside a call into symphonia; see [`decoding`].
    static DECODING: Cell<Option<u32>> = const { Cell::new(None) };
}

/// **How many bit-reservoir underflows one opened file may produce before they
/// are unusual** `[GDE-HST-190]`.
///
/// An MP3 frame may borrow up to 511 bytes of data from the frames before it
/// (`main_data_begin` is nine bits). Starting mid-file -- which Lempi does by
/// design, since it plays spans -- means those earlier frames were never
/// decoded, and symphonia warns once per frame whose borrowing it cannot
/// satisfy. At 128 kbps 511 bytes reaches back about a frame and a half, so a
/// start can explain at most two; measured on `lempi02w` over twelve passage
/// starts, 2026-09-25: zero, one or two each, never more. Lower bitrates have
/// smaller frames and reach further back -- about three at 64 kbps -- so the
/// budget is five: every normal music file sits under it with margin, and a
/// damaged file, which underflows frame after frame, crosses it at once.
///
/// Within the budget the line is info, not a warning: expected, still in the
/// journal, not in `journalctl -p warning` -- where, as a warning at every
/// passage start, it was 9 of 9 dependency lines and about 45 an hour.
pub const RESERVOIR_BUDGET: u32 = 5;

/// The warning's target and text, exactly as `symphonia-bundle-mp3` 0.5.5
/// writes them (`layer3/mod.rs:86`), up to the byte count. Matched on text
/// because the record carries nothing else to match on. If a later symphonia
/// rewords it, the match fails **towards noise, not silence**: the lines go
/// back to being warnings, which is visible, rather than being hidden.
const RESERVOIR_TARGET: &str = "symphonia_bundle_mp3";
const RESERVOIR_UNDERFLOW: &str = "mpa: invalid main_data_begin, underflow by ";

/// Run `f` -- a call into symphonia -- as decoding the file whose underflow
/// count so far is `seen`, and leave the updated count there afterwards.
///
/// The count belongs to one opened file, so a passage's decoder carries its
/// own and a crossfade's two decoders, interleaved on one thread, never share
/// one. Nesting restores whatever was in force outside.
pub fn decoding<R>(seen: &mut u32, f: impl FnOnce() -> R) -> R {
    let outer = DECODING.with(|d| d.replace(Some(*seen)));
    let r = f();
    if let Some(n) = DECODING.with(|d| d.replace(outer)) {
        *seen = n;
    }
    r
}

/// How many of a file's underflows were counted rather than shown: everything
/// past the budget and the one warning that surfaced.
pub fn unshown(seen: u32) -> u32 {
    seen.saturating_sub(RESERVOIR_BUDGET + 1)
}

/// Report, as a file closes, the underflows that were counted rather than
/// shown -- once, so a damaged file costs two warning lines, not hundreds.
pub fn reservoir_closed(path: &std::path::Path, seen: u32) {
    let hidden = unshown(seen);
    if hidden > 0 {
        tracing::warn!(
            "decode: {}: {seen} bit-reservoir underflows in all, {hidden} of them not shown one by \
             one; a start mid-file explains up to {RESERVOIR_BUDGET}, so this many suggests damage",
            path.display()
        );
    }
}

/// What the reservoir policy does with one line.
enum Reservoir {
    /// Within the budget: expected, written as info.
    Demote,
    /// The first past it: written as the warning it is, saying what follows.
    Surface,
    /// Past that: counted for the summary, not written.
    Count,
}

/// Apply the policy to a line, if it is the reservoir warning and this thread
/// is decoding a file. Outside a decode it is left alone -- still a warning --
/// because nothing says it is the expected kind.
fn reservoir(target: &str, message: &str) -> Option<Reservoir> {
    if !target.starts_with(RESERVOIR_TARGET) || !message.starts_with(RESERVOIR_UNDERFLOW) {
        return None;
    }
    DECODING.with(|d| {
        let n = d.get()? + 1;
        d.set(Some(n));
        Some(if n <= RESERVOIR_BUDGET {
            Reservoir::Demote
        } else if n == RESERVOIR_BUDGET + 1 {
            Reservoir::Surface
        } else {
            Reservoir::Count
        })
    })
}

/// Lines dropped because a thread that may not block found the queue full.
static DROPPED: AtomicU64 = AtomicU64::new(0);
/// Lines queued and not yet written, for [`flush`] to wait on.
static PENDING: AtomicUsize = AtomicUsize::new(0);
static QUEUE: OnceLock<SyncSender<String>> = OnceLock::new();

/// Mark the calling thread as one that may never wait on a write -- the
/// engine's tick thread, in every binary that runs one.
///
/// Called just before the tick loop rather than at the thread's start, so
/// that a failure opening the session -- written, then exited on -- is still
/// written synchronously.
pub fn this_thread_must_not_block() {
    MUST_NOT_BLOCK.with(|c| c.set(true));
}

/// How many lines threads that may not block have dropped so far.
pub fn dropped() -> u64 {
    DROPPED.load(Ordering::Relaxed)
}

/// End the calling thread's no-blocking stretch, and wait -- at most `limit`
/// -- for what it queued to be written. Returns whether everything was.
///
/// **For the moment a tick loop ends and the process is about to.** A queued
/// line is written by the `lempi-log` thread; a program that exits straight
/// after its loop can outrun it, and the last lines -- often the ones that
/// say why it stopped -- are the ones lost. `play` on a missing file wrote its
/// `skipping` line three times in three, which is a race won, not a guarantee;
/// this makes it one. Waiting here is allowed, because the tick is over.
pub fn flush(limit: Duration) -> bool {
    MUST_NOT_BLOCK.with(|c| c.set(false));
    let until = std::time::Instant::now() + limit;
    while PENDING.load(Ordering::Acquire) > 0 {
        if std::time::Instant::now() >= until {
            return false;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    true
}

/// Install this process's subscriber. The first call wins; later ones, and a
/// process that already has a subscriber, are left as they are.
///
/// `program` is the binary's own name, which is also the target its own
/// events carry, so its lines are treated as this project's rather than as a
/// dependency's.
pub fn install(program: &str) {
    static DONE: OnceLock<()> = OnceLock::new();
    DONE.get_or_init(|| {
        let journal = stderr_is_journal();
        match spawn_writer(journal) {
            Some(tx) => {
                let _ = QUEUE.set(tx);
            }
            // With no writer thread, a marked thread writes synchronously --
            // a breach of `[GDE-FBD-090]`, said out loud rather than turned
            // into lines silently vanishing into a queue nobody drains.
            None => {
                let _ = std::io::stderr().write_all(
                    b"log: no writer thread; the engine tick will write synchronously\n");
            }
        }
        let own: Vec<String> = ["lempi_player", "lempi_core", program]
            .iter()
            .map(|s| s.replace('-', "_"))
            .collect();
        let mut targets = Targets::new().with_default(LevelFilter::WARN);
        for t in &own {
            targets = targets.with_target(t.clone(), LevelFilter::INFO);
        }
        let subscriber = tracing_subscriber::registry()
            .with(Lines { journal, own }.with_filter(targets));
        let _ = tracing::subscriber::set_global_default(subscriber);
        let _ = tracing_log::LogTracer::init_with_filter(log::LevelFilter::Warn);
    });
}

/// The layer: format, then write or queue.
struct Lines {
    journal: bool,
    own: Vec<String>,
}

impl<S: Subscriber> Layer<S> for Lines {
    fn on_event(&self, event: &Event<'_>, _: Context<'_, S>) {
        let Some(line) = format_line(event, self.journal, &self.own) else { return };
        if MUST_NOT_BLOCK.with(Cell::get) {
            if let Some(q) = QUEUE.get() {
                offer(q, line, &DROPPED, &PENDING);
                return;
            }
        }
        let _ = std::io::stderr().lock().write_all(line.as_bytes());
    }
}

/// Queue a line without waiting; if the queue is full or gone, count it.
///
/// `pending` is raised before the send, so the writer can never lower it
/// past zero by writing a line this has not yet counted.
fn offer(q: &SyncSender<String>, line: String, dropped: &AtomicU64, pending: &AtomicUsize) {
    pending.fetch_add(1, Ordering::AcqRel);
    if q.try_send(line).is_err() {
        pending.fetch_sub(1, Ordering::AcqRel);
        dropped.fetch_add(1, Ordering::Relaxed);
    }
}

fn spawn_writer(journal: bool) -> Option<SyncSender<String>> {
    let (tx, rx) = sync_channel::<String>(QUEUE_LINES);
    std::thread::Builder::new()
        .name("lempi-log".into())
        .spawn(move || drain(rx, journal))
        .ok()
        .map(|_| tx)
}

/// The writer thread: write what arrives, and say when lines were lost.
///
/// Wakes at least once a second, so a drop is reported even when nothing
/// follows it -- the queue overflowing and then going quiet is exactly the
/// case in which no later line would carry the news.
fn drain(rx: Receiver<String>, journal: bool) {
    let mut reported = 0u64;
    loop {
        let next = rx.recv_timeout(Duration::from_secs(1));
        let lost = DROPPED.load(Ordering::Relaxed);
        if lost != reported || next.is_ok() {
            let mut out = std::io::stderr().lock();
            if lost != reported {
                let _ = writeln!(
                    out,
                    "{}log: {} line(s) dropped by a thread that may not block; its queue of {} was full",
                    if journal { "<4>" } else { "" },
                    lost - reported,
                    QUEUE_LINES
                );
                reported = lost;
            }
            if let Ok(line) = &next {
                let _ = out.write_all(line.as_bytes());
                let _ = out.flush();
                PENDING.fetch_sub(1, Ordering::AcqRel);
            }
        }
        if let Err(RecvTimeoutError::Disconnected) = next {
            return;
        }
    }
}

/// syslog priority for a level: what `SyslogLevelPrefix=yes` reads from `<N>`.
fn priority(level: &Level) -> u8 {
    match *level {
        Level::ERROR => 3,
        Level::WARN => 4,
        Level::INFO => 6,
        _ => 7,
    }
}

/// One event as one line.
///
/// This project's events are the message and any fields, nothing else. A
/// dependency's are attributed -- `symphonia_core::…: <message>` -- because a
/// bare "invalid main_data offset" says nothing about where it came from.
/// Events bridged from `log` carry their real target in `log.*` fields;
/// `normalized_metadata` recovers it, and those fields are not repeated.
///
/// `None` when the reservoir policy counts a line rather than writing it
/// `[GDE-HST-190]`.
fn format_line(event: &Event<'_>, journal: bool, own: &[String]) -> Option<String> {
    let mut v = Fields::default();
    event.record(&mut v);
    let normalized = event.normalized_metadata();
    let meta = normalized.as_ref().unwrap_or_else(|| event.metadata());
    let mut level = *meta.level();
    let target = meta.target();

    let mut note = String::new();
    match reservoir(target, &v.message) {
        Some(Reservoir::Demote) => level = Level::INFO,
        Some(Reservoir::Surface) => {
            note = format!(
                " -- more than {RESERVOIR_BUDGET} in one file, which a start mid-file does not \
                 explain; any further ones are counted and reported when it closes"
            );
        }
        Some(Reservoir::Count) => return None,
        None => {}
    }

    let mut line = String::with_capacity(v.message.len() + v.rest.len() + note.len() + 8);
    if journal && level != Level::INFO {
        let _ = write!(line, "<{}>", priority(&level));
    }
    if !own.iter().any(|o| target == o || target.starts_with(&format!("{o}::"))) {
        let _ = write!(line, "{target}: ");
    }
    line.push_str(&v.message);
    line.push_str(&note);
    line.push_str(&v.rest);
    line.push('\n');
    Some(line)
}

#[derive(Default)]
struct Fields {
    message: String,
    rest: String,
}

impl Visit for Fields {
    fn record_str(&mut self, field: &Field, value: &str) {
        match field.name() {
            "message" => self.message.push_str(value),
            n if n.starts_with("log.") => {}
            n => {
                let _ = write!(self.rest, " {n}={value}");
            }
        }
    }
    fn record_debug(&mut self, field: &Field, value: &dyn fmt::Debug) {
        match field.name() {
            "message" => {
                let _ = write!(self.message, "{value:?}");
            }
            n if n.starts_with("log.") => {}
            n => {
                let _ = write!(self.rest, " {n}={value:?}");
            }
        }
    }
}

/// Is stderr the journal's stream? systemd sets `JOURNAL_STREAM` to
/// `device:inode` of the stream it connected, and this compares it with
/// stderr's own -- the documented test, rather than assuming that the
/// variable's presence means stderr specifically.
fn stderr_is_journal() -> bool {
    #[cfg(unix)]
    {
        use std::os::fd::AsFd;
        use std::os::unix::fs::MetadataExt;
        let Ok(value) = std::env::var("JOURNAL_STREAM") else { return false };
        let Ok(fd) = std::io::stderr().as_fd().try_clone_to_owned() else { return false };
        let Ok(meta) = std::fs::File::from(fd).metadata() else { return false };
        stream_matches(&value, meta.dev(), meta.ino())
    }
    #[cfg(not(unix))]
    {
        false
    }
}

/// `JOURNAL_STREAM` is `device:inode`, both decimal.
#[cfg_attr(not(unix), allow(dead_code))]
fn stream_matches(value: &str, dev: u64, ino: u64) -> bool {
    let Some((d, i)) = value.trim().split_once(':') else { return false };
    matches!((d.parse::<u64>(), i.parse::<u64>()), (Ok(d), Ok(i)) if d == dev && i == ino)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Mutex};

    /// A layer that formats exactly as `Lines` does, into a vector.
    struct Capture {
        journal: bool,
        lines: Arc<Mutex<Vec<String>>>,
    }

    impl<S: Subscriber> Layer<S> for Capture {
        fn on_event(&self, event: &Event<'_>, _: Context<'_, S>) {
            let own = vec!["lempi_player".to_string(), "lempi_core".to_string()];
            if let Some(line) = format_line(event, self.journal, &own) {
                self.lines.lock().unwrap().push(line);
            }
        }
    }

    fn capture(journal: bool, f: impl FnOnce()) -> Vec<String> {
        let lines = Arc::new(Mutex::new(Vec::new()));
        let sub = tracing_subscriber::registry().with(Capture { journal, lines: lines.clone() });
        tracing::subscriber::with_default(sub, f);
        let out = lines.lock().unwrap().clone();
        out
    }

    /// `[GDE-HST-120]`, `[GDE-HST-130]`: an info line is byte-identical to
    /// what `println!` wrote, in the journal or out of it -- which is what
    /// keeps `verify-playing.sh`'s `^clock: frames=` true on every node.
    #[test]
    fn an_info_line_is_exactly_its_message_everywhere() {
        for journal in [false, true] {
            let got = capture(journal, || {
                tracing::info!("clock: frames={} at_nanos={}", 151_552, 7);
            });
            assert_eq!(got, vec!["clock: frames=151552 at_nanos=7\n".to_string()],
                       "journal={journal}");
        }
    }

    #[test]
    fn warnings_and_errors_carry_a_priority_only_into_the_journal() {
        let j = capture(true, || {
            tracing::warn!("output recovery failed, retrying: {}", "busy");
            tracing::error!("cannot open {}", "x");
        });
        assert_eq!(j, vec!["<4>output recovery failed, retrying: busy\n".to_string(),
                           "<3>cannot open x\n".to_string()]);
        let plain = capture(false, || tracing::warn!("output recovery failed, retrying: busy"));
        assert_eq!(plain, vec!["output recovery failed, retrying: busy\n".to_string()],
                   "outside the journal a literal <4> would land in a log file");
    }

    #[test]
    fn fields_follow_the_message() {
        let got = capture(false, || tracing::info!(passage = 3218, "echo-schedule"));
        assert_eq!(got, vec!["echo-schedule passage=3218\n".to_string()]);
    }

    /// A dependency's line says whose it is; this project's never do.
    #[test]
    fn a_dependency_is_attributed_and_this_project_is_not() {
        let got = capture(false, || {
            tracing::warn!(target: "symphonia_core::probe", "invalid main_data offset");
            tracing::warn!(target: "lempi_core::relink", "hasher missing");
        });
        assert_eq!(got, vec!["symphonia_core::probe: invalid main_data offset\n".to_string(),
                             "hasher missing\n".to_string()]);
    }

    /// `[GDE-HST-140]`: a thread that may not block never waits. A queue of
    /// two, nobody reading it: the third line is dropped and counted, and the
    /// call returns rather than hanging -- which, if it did not, would hang
    /// this test, as it would hang the engine tick.
    #[test]
    fn a_full_queue_drops_and_counts_rather_than_waits() {
        let (tx, _rx) = sync_channel::<String>(2);
        let dropped = AtomicU64::new(0);
        let pending = AtomicUsize::new(0);
        for i in 0..3 {
            offer(&tx, format!("line {i}\n"), &dropped, &pending);
        }
        assert_eq!(dropped.load(Ordering::Relaxed), 1);
        assert_eq!(pending.load(Ordering::Relaxed), 2,
                   "a dropped line is not pending -- flush must not wait for it");
    }

    fn underflow(bytes: u32) {
        tracing::warn!(target: "symphonia_bundle_mp3::layer3",
                       "mpa: invalid main_data_begin, underflow by {} bytes", bytes);
    }

    /// `[GDE-HST-190]`, the whole policy on one file: five demoted to info,
    /// the sixth surfaced as a warning that says what follows, the rest
    /// counted and not written -- and the count left with the file.
    #[test]
    fn a_files_first_five_underflows_are_info_the_sixth_warns_the_rest_are_counted() {
        let mut seen = 0;
        let got = capture(true, || {
            decoding(&mut seen, || {
                for i in 0..9 {
                    underflow(200 + i);
                }
            });
        });
        assert_eq!(seen, 9, "every underflow is counted, shown or not");
        assert_eq!(got.len(), 6, "five demoted and one surfaced; three counted: {got:#?}");
        for (i, l) in got[..5].iter().enumerate() {
            assert_eq!(l, &format!(
                "symphonia_bundle_mp3::layer3: mpa: invalid main_data_begin, underflow by {} bytes\n",
                200 + i), "within the budget: info, so no <4> even into the journal");
        }
        assert!(got[5].starts_with(
            "<4>symphonia_bundle_mp3::layer3: mpa: invalid main_data_begin, underflow by 205 bytes -- more than 5"),
            "the sixth is a warning and says so: {}", got[5]);
        assert_eq!(unshown(seen), 3);
    }

    /// Each opened file has its own budget: a crossfade's two decoders,
    /// interleaved on one thread, do not spend each other's.
    #[test]
    fn each_file_has_its_own_budget() {
        let (mut a, mut b) = (0, 0);
        let got = capture(true, || {
            for _ in 0..4 {
                decoding(&mut a, || underflow(1));
                decoding(&mut b, || underflow(2));
            }
        });
        assert_eq!((a, b), (4, 4));
        assert!(got.iter().all(|l| !l.starts_with("<4>")),
                "four each is inside either budget: {got:#?}");
    }

    /// Outside a decode the policy does not apply: nothing says the line is
    /// the expected kind, so it stays a warning. And symphonia's *other*
    /// warnings are never demoted, inside a decode or out.
    #[test]
    fn only_the_reservoir_warning_and_only_while_decoding_is_demoted() {
        let got = capture(true, || {
            underflow(7);
            let mut seen = 0;
            decoding(&mut seen, || {
                tracing::warn!(target: "symphonia_bundle_mp3::layer3", "mpa: invalid block_type");
            });
        });
        assert_eq!(got, vec![
            "<4>symphonia_bundle_mp3::layer3: mpa: invalid main_data_begin, underflow by 7 bytes\n".to_string(),
            "<4>symphonia_bundle_mp3::layer3: mpa: invalid block_type\n".to_string(),
        ]);
    }

    #[test]
    fn a_closing_file_reports_only_what_was_counted_and_not_shown() {
        for n in 0..=RESERVOIR_BUDGET + 1 {
            assert_eq!(unshown(n), 0, "{n} underflows were all written, as info or the one warning");
        }
        assert_eq!(unshown(RESERVOIR_BUDGET + 2), 1);
        assert_eq!(unshown(40), 34);
    }

    #[test]
    fn journal_stream_is_matched_by_device_and_inode() {
        assert!(stream_matches("8:12345", 8, 12345));
        assert!(stream_matches(" 8:12345\n", 8, 12345));
        assert!(!stream_matches("8:12345", 8, 99));
        assert!(!stream_matches("8", 8, 12345));
        assert!(!stream_matches("x:y", 8, 12345));
    }

    /// `[GDE-HST-120]`: the lines scripts read, pinned at their source.
    ///
    /// `verify-playing.sh` anchors on `^clock: frames=`; the radio tests match
    /// `error|recover`. The formatter tests above prove how a line is written;
    /// this proves the lines still say what the readers look for -- and that
    /// the clock line is info, the one level written with no prefix.
    #[test]
    fn the_lines_scripts_read_still_say_what_they_look_for() {
        let engine = include_str!("engine/mod.rs");
        let at = engine.find("\"clock: frames={} at_nanos={} delay={} rate={} ts={:?} callbacks={}\"")
            .expect("verify-playing.sh anchors on ^clock: frames= -- do not reword this line");
        let before = &engine[at.saturating_sub(120)..at];
        let macro_at = before.rfind('!').map(|i| &before[..i]).unwrap_or("");
        assert!(macro_at.ends_with("println") || macro_at.ends_with("eprintln") || macro_at.ends_with("info"),
                "the clock line must stay unprefixed: info!, not warn! or error! -- {:?}",
                &before[before.len().saturating_sub(40)..]);
        let path = include_str!("path.rs");
        assert!(path.contains("output recovered on"), "the radio tests match 'recover'");
        assert!(path.contains("output recovery failed"), "the radio tests match 'recover'");
    }
}
