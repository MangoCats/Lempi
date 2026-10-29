//! The audio path supervisor `[SPEC-APS-060]`.
//!
//! One component owns the device's whole life: opening it, noticing it has
//! stopped being audible, releasing it, waiting, and opening it again. The
//! engine holds only a [`PathHandle`] -- a ring to write into and a channel to
//! ask things of -- and can therefore neither open a device, nor wait for one,
//! nor shell out to ask about one.
//!
//! **That inability is the point `[GDE-FBD-090]`.** All of this logic used to
//! live inside `Engine::tick`, and three separate times something blocking got
//! into it: a `wpctl` fork/exec, then a 700 ms settle, then nearly again. Each
//! starved the ring and presented as underruns and dropouts that looked like
//! hardware failure. The rule "nothing blocking on the tick" was written down
//! and broken anyway, because nothing enforced it. Here the enforcement is that
//! the engine has no such capability to misuse.
//!
//! On this thread, by contrast, blocking is free: it may sleep through the
//! settle, poll `wpctl`, and take as long as it likes, because nothing is
//! waiting on it to fill a buffer.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, Sender};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::output::{Output, OutputRing};
use crate::sink::{SinkObserver, Tristate};

/// Base spacing between recovery attempts, doubling to `RETRY_MAX`.
///
/// Retries are spaced rather than continuous because the usual reason a sink is
/// gone is that someone carried the speaker out of range, and reopening an
/// absent ALSA device several thousand times a second costs a core for nothing.
const RETRY: Duration = Duration::from_secs(2);
const RETRY_MAX: Duration = Duration::from_secs(30);
/// How long to leave the device closed between releasing and reopening.
///
/// A stream rebuilt in the same breath as the old one is dropped loses a
/// Bluetooth speaker about twenty-two seconds later, every time, where one
/// opened fresh holds indefinitely: PipeWire needs a moment to finish tearing
/// the old one down `[PI3-OPEN-010]`.
const SETTLE: Duration = Duration::from_millis(700);
/// How often to confirm the audio is still reaching something real.
const WATCH: Duration = Duration::from_secs(20);
/// How long the loop waits for a request before going round again.
const IDLE: Duration = Duration::from_millis(200);
/// How long a process-wide refusal is waited out before the player asks to be
/// restarted `[GDE-HST-230]`.
///
/// Three measured, on the desktop: about ten minutes on 2026-10-18 and
/// 2 m 28 s on 2026-10-28, each clearing by itself, and 7 m 30 s on
/// 2026-10-27, ended by a restart that opened the device at once. None cleared
/// sooner than two minutes, so waiting longer buys nothing but silence; this is
/// short of that by enough to let a refusal that merely blips go, and reached
/// on the fourth refused attempt, at about 28 s.
const RESTART_AFTER: Duration = Duration::from_secs(20);

/// What the supervisor calls, once, when a refusal has outlasted
/// [`RESTART_AFTER`]: whoever started the player, asked to start it again. The
/// supervisor cannot do that itself -- it knows neither the command line nor
/// the engine that must save its place first.
pub type Restart = Box<dyn FnOnce() + Send>;

/// Things only the supervisor can do, asked for from anywhere.
pub enum PathRequest {
    /// Start or stop the consumer. Pausing feeds silence rather than stopping
    /// the device, so the Bluetooth link survives it `[PI3-OPEN-020]`.
    SetPlaying(bool),
    /// Reopen because the sink changed under us, not because it failed
    /// `[PI3-API-010]`.
    Reopen,
    /// Release the device for a bulk transfer, or take it back `[PI3-AIM-175]`.
    /// Paused, the stream still sends a Bluetooth speaker its silence, which
    /// costs the shared radio exactly what music does; released, PipeWire
    /// suspends the sink and nothing goes on the air. Taken back, the device
    /// is reopened whether or not anything is playing, so a long pause after
    /// the transfer still holds the link `[PI3-OPEN-020]`.
    Quiet(bool),
    Shutdown,
}

/// What the engine holds: somewhere to put audio, and a way to ask for things.
///
/// Deliberately without any method that can block. `ring` is `None` for the
/// discard sink -- the full pipeline running into nothing, which is what the
/// tests use -- and for a device that could not be opened, which `open_failed`
/// tells apart `[PI3-FOUND-790]`.
#[derive(Clone)]
pub struct PathHandle {
    pub ring: Option<OutputRing>,
    tx: Option<Sender<PathRequest>>,
    recoveries: Arc<AtomicU64>,
    /// No ring because the first open FAILED, not because none was wanted.
    open_failed: bool,
}

impl PathHandle {
    /// A handle with no device at all.
    pub fn silent() -> Self {
        Self { ring: None, tx: None, recoveries: Arc::new(AtomicU64::new(0)), open_failed: false }
    }

    /// A handle over a real ring with no supervisor behind it `[REQ-VIS-250]`.
    ///
    /// For the engine's own tests, which otherwise run entirely against
    /// `silent()` -- a ring of `None` reports zero frames buffered, so
    /// nothing exercised there can tell "mixed" from "heard" apart. This
    /// gives a test a ring it can fill and simply never drain, which is
    /// indistinguishable from a real device that is buffering audio slower
    /// than the mixer is producing it.
    #[cfg(test)]
    pub(crate) fn with_ring(ring: crate::output::OutputRing) -> Self {
        Self { ring: Some(ring), tx: None, recoveries: Arc::new(AtomicU64::new(0)), open_failed: false }
    }

    fn ask(&self, r: PathRequest) {
        if let Some(t) = &self.tx {
            let _ = t.send(r);
        }
    }

    pub fn set_playing(&self, on: bool) {
        self.ask(PathRequest::SetPlaying(on));
    }
    pub fn reopen(&self) {
        self.ask(PathRequest::Reopen);
    }
    pub fn quiet(&self, on: bool) {
        self.ask(PathRequest::Quiet(on));
    }
    pub fn shutdown(&self) {
        self.ask(PathRequest::Shutdown);
    }

    /// A handle as `start` leaves it when the device would not open: no ring,
    /// and not audible. For the engine's tests of exactly that.
    #[cfg(test)]
    pub(crate) fn failed_open() -> Self {
        Self { open_failed: true, ..Self::silent() }
    }

    /// Could anyone hear us? Observed, never assumed `[GDE-FBD-100]`.
    ///
    /// A discard sink answers `true`: it is not a fault, and treating it as one
    /// would stop the queue advancing in a test. A device that would not open
    /// answers `false` `[PI3-FOUND-790]`. It used to answer `true` as well,
    /// and the engine, with no device to pace it, mixed into nothing as fast
    /// as it could decode: on lp3-wifi, 2026-10-10, after a boot that started
    /// the player before PipeWire, 35 "plays" and skips in seven minutes,
    /// written to the listener's history as heard.
    pub fn audible(&self) -> bool {
        match &self.ring {
            Some(r) => !r.failed(),
            None => !self.open_failed,
        }
    }

    pub fn recoveries(&self) -> u64 {
        self.recoveries.load(Ordering::Relaxed)
    }

    pub fn sample_rate(&self) -> u32 {
        self.ring.as_ref().map_or(44_100, |r| r.sample_rate())
    }
    pub fn channels(&self) -> usize {
        self.ring.as_ref().map_or(2, |r| r.channels())
    }
}

/// Open the device on a thread of the supervisor's own and keep it alive.
///
/// `cpal::Stream` is `!Send`, so the device cannot be opened here and moved
/// there; the supervisor opens it itself and reports back what happened. The
/// returned string is for the startup line -- it says which device, or why not.
pub fn start(device: Option<String>, ring_capacity: usize) -> (PathHandle, String) {
    start_restartable(device, ring_capacity, None)
}

/// [`start`], with somewhere to ask for a restart when Windows goes on
/// refusing the output `[GDE-HST-230]`. Without one, a refusal is retried and
/// reported, and lasts as long as Windows makes it.
pub fn start_restartable(device: Option<String>, ring_capacity: usize, restart: Option<Restart>)
    -> (PathHandle, String)
{
    let (ready_tx, ready_rx) = sync_channel::<(Option<OutputRing>, String)>(1);
    let (tx, rx) = std::sync::mpsc::channel::<PathRequest>();
    let recoveries = Arc::new(AtomicU64::new(0));
    let counter = Arc::clone(&recoveries);

    std::thread::Builder::new()
        .name("lempi-path".into())
        .spawn(move || supervise(device, ring_capacity, ready_tx, rx, counter, restart))
        .expect("spawn path supervisor");

    // Wait for the first open attempt, so the caller can print an honest line
    // about it. Only this one moment blocks, and it is before any audio flows.
    let (ring, why) = ready_rx.recv().unwrap_or((None, "path supervisor failed to start".into()));
    let open_failed = ring.is_none();
    (PathHandle { ring, tx: Some(tx), recoveries, open_failed }, why)
}

fn supervise(
    device: Option<String>,
    ring_capacity: usize,
    ready: std::sync::mpsc::SyncSender<(Option<OutputRing>, String)>,
    rx: Receiver<PathRequest>,
    recoveries: Arc<AtomicU64>,
    mut restart: Option<Restart>,
) {
    let mut out = match Output::open_device(device.as_deref(), ring_capacity) {
        Ok(o) => {
            let why = format!("output: {} @ {} Hz, {} ch",
                              o.device_name, o.ring.sample_rate(), o.ring.channels());
            let _ = ready.send((Some(o.ring()), why));
            Some(o)
        }
        Err(e) => {
            // A missing device must not stop the process: the UI still needs to
            // come up and say so, which is more use than exiting silently.
            let _ = ready.send((None, format!(
                "no audio device ({e}); holding playback, and trying again until one opens")));
            None
        }
    };
    let Some(out) = out.as_mut() else {
        await_device(device.as_deref(), ring_capacity, &rx);
        return;
    };

    // Said once, at start: whether this host can tell a silent output from a
    // heard one at all `[GDE-HST-350]`. Where it cannot, the dummy watch below
    // has nothing to act on, and a speaker switched off mid-play goes
    // unnoticed -- a property of the host rather than a fault, so info, but a
    // stated one: a check that cannot run says so `[GDE-DEP-060]`. After the
    // ready signal, so the subprocess never delays the startup line.
    announce(crate::sink::observer());

    let mut playing = false;
    // A quiet's end asks for the device back even while paused; cleared once
    // it is open again `[PI3-AIM-175]`.
    let mut reattach = false;
    let mut backoff = RETRY;
    let mut retry_at: Option<Instant> = None;
    let mut watch_at = Instant::now() + WATCH;
    let mut refused: Option<Refused> = None;

    loop {
        match rx.recv_timeout(IDLE) {
            Ok(PathRequest::SetPlaying(on)) => {
                playing = on;
                out.set_playing(on);
                // Check for a dummy AT ONCE when playback starts, rather than
                // up to WATCH later. A speaker that is not there is never more
                // likely than at the moment someone presses play -- and never
                // more so than when the machine has just booted and resumed
                // `[PI5-PWR-030]`. Until it is noticed, the ring drains into
                // the discard sink and the clock advances through music nobody
                // can hear: exactly the lie `[PI3-API-030]` exists to stop.
                if on {
                    watch_at = Instant::now();
                }
            }
            Ok(PathRequest::Reopen) => {
                // Same path as a recovery: a reopen landing on a device that is
                // not ready yet -- a speaker still completing its connection is
                // the normal case -- should keep trying rather than fail once
                // and leave the listener with a selection that did nothing.
                out.mark_failed();
                retry_at = None;
                backoff = RETRY;
            }
            // Released as a failure is, so the engine mixes nothing into it
            // and a listener who presses play mid-transfer gets the device
            // back by the ordinary recovery -- their choice, not overruled.
            Ok(PathRequest::Quiet(true)) => {
                reattach = false;
                if !out.released() {
                    out.release();
                    tracing::info!("output released for a transfer: nothing on the air until it ends [PI3-AIM-175]");
                }
            }
            Ok(PathRequest::Quiet(false)) => {
                if out.released() {
                    reattach = true;
                    retry_at = None;
                    backoff = RETRY;
                }
            }
            Ok(PathRequest::Shutdown) | Err(RecvTimeoutError::Disconnected) => return,
            Err(RecvTimeoutError::Timeout) => {}
        }

        watch(out, playing, &mut watch_at);
        recover(out, playing, playing || reattach, &mut retry_at, &mut backoff, &mut refused, &recoveries);
        restart_if_refused(&refused, playing, &mut restart);
        if reattach && !out.failed() {
            reattach = false;
        }
    }
}

/// The first open failed: keep trying, and restart once a device opens
/// `[PI3-FOUND-790]`.
///
/// This thread used to return here, so every later reopen -- the speaker
/// keeper asks for one every thirty seconds -- went nowhere, and the player
/// stayed without output until someone restarted it. The engine cannot adopt a
/// ring that appears later: its rate and channel count are read once, when it
/// is built. So a device that opens means a restart, which is how the player
/// comes up with it. Under systemd (`INVOCATION_ID` is set) the player asks
/// for that itself, by the same `sudo -n systemctl restart lempi` as the
/// settings page's Restart; anywhere else it says so and leaves it to whoever
/// started it.
///
/// Spaced as `recover` spaces its retries, and at once on a reopen request,
/// which is what arrives when a speaker connects.
fn await_device(device: Option<&str>, ring_capacity: usize, rx: &Receiver<PathRequest>) {
    let mut backoff = RETRY;
    let mut next = Instant::now() + backoff;
    loop {
        match rx.recv_timeout(next.saturating_duration_since(Instant::now()).min(IDLE)) {
            Ok(PathRequest::Reopen) => next = Instant::now(),
            Ok(PathRequest::SetPlaying(_) | PathRequest::Quiet(_)) | Err(RecvTimeoutError::Timeout) => {}
            Ok(PathRequest::Shutdown) | Err(RecvTimeoutError::Disconnected) => return,
        }
        if Instant::now() < next {
            continue;
        }
        match Output::open_device(device, ring_capacity) {
            Ok(o) => {
                drop(o);
                restart_for_device();
                return;
            }
            Err(e) => {
                backoff = (backoff * 2).min(RETRY_MAX);
                next = Instant::now() + backoff;
                tracing::debug!("still no audio device ({e}); next try in {}s", backoff.as_secs());
            }
        }
    }
}

/// A device opens now that did not at start: restart to use it.
fn restart_for_device() {
    if std::env::var_os("INVOCATION_ID").is_none() {
        tracing::warn!("an audio device opens now, but this player started without one \
                        and cannot take it up: restart the player to play");
        return;
    }
    tracing::warn!("an audio device opens now; this player started without one, \
                    so it is restarting to use it [PI3-FOUND-790]");
    match std::process::Command::new("sudo").args(["-n", "systemctl", "restart", "lempi"]).status() {
        Ok(s) if s.success() => {}
        Ok(s) => tracing::error!("could not restart to take up the audio device: systemctl {s}"),
        Err(e) => tracing::error!("could not restart to take up the audio device: {e}"),
    }
}

/// The start line for audibility: which observer, and whether it can see.
fn announce(observer: &dyn SinkObserver) {
    let seen = observer.observe();
    if seen.known {
        tracing::info!("audibility: observed through {}", observer.name());
    } else {
        tracing::info!(
            "audibility: NOT observable here ({}: {}); a sink that falls silent \
             will not be noticed [SPEC-APS-030]",
            observer.name(),
            seen.note.as_deref().unwrap_or("no reason given")
        );
    }
}

/// Notice a sink that became a dummy without anyone reporting an error
/// `[PI3-API-030]`.
///
/// The failure path covers a stream that breaks. This covers the quieter one: a
/// speaker switched off during normal playback, whose stream PipeWire moves to
/// the `Dummy Output` with no error at all. Nothing is wrong at that moment --
/// the callback runs, the ring drains, the clock advances -- and nobody can
/// hear a thing.
///
/// It costs a subprocess, which is exactly why it belongs here and nowhere near
/// the mixer `[GDE-FBD-110]`.
fn watch(out: &Output, playing: bool, watch_at: &mut Instant) {
    if !playing || out.failed() || Instant::now() < *watch_at {
        return;
    }
    *watch_at = Instant::now() + WATCH;
    // Only an observed `No` acts. `Unknown` -- a host that cannot see its
    // output -- was said once at start, and is not a fault to recover from.
    if crate::sink::current().audible == Tristate::No {
        tracing::warn!("audio is going nowhere audible; looking for a sink");
        out.mark_failed();
    }
}

/// Release, wait, reopen -- and only call it recovered if it is audible.
///
/// Gated on `playing` for the same reason `watch()` already is: nothing
/// here previously consulted it at all, so an already-failed device kept
/// releasing and reopening on its own backoff regardless of whether
/// anything was even asking to play -- endlessly re-hunting a device that
/// simply isn't there while a plain pause, or nothing chosen to play at
/// all, made the answer not matter either way. `watch()` stops *starting*
/// a new failure while `!playing`; this stops an *already-armed* one from
/// continuing to run. The next `SetPlaying(true)` re-arms `watch_at`
/// immediately and this resumes exactly as before.
///
/// `wanted` is that gate, and is `playing` but for one case: the end of a
/// transfer's quiet, which wants the device back while paused, so the link
/// is held through the pause after it `[PI3-AIM-175]`.
fn recover(
    out: &mut Output,
    playing: bool,
    wanted: bool,
    retry_at: &mut Option<Instant>,
    backoff: &mut Duration,
    refused: &mut Option<Refused>,
    recoveries: &Arc<AtomicU64>,
) {
    if !wanted || !out.failed() {
        return;
    }
    let now = Instant::now();
    if retry_at.is_some_and(|t| now < t) {
        return;
    }
    *backoff = (*backoff * 2).min(RETRY_MAX).max(RETRY);
    *retry_at = Some(now + *backoff);
    recoveries.fetch_add(1, Ordering::Relaxed);

    if !out.released() {
        out.release();
        // Free to sleep: nothing is waiting on this thread to fill a buffer.
        // In the engine this same wait starved the ring and turned a dropout
        // into a stutter.
        std::thread::sleep(SETTLE);
    }
    let refusal = match out.recover() {
        Ok(name) => return opened(out, &name, "", playing, retry_at, backoff, refused),
        Err(e) if e.is_com_refusal() => e,
        Err(e) => {
            tracing::warn!("output recovery failed, retrying: {e}");
            return;
        }
    };
    // Refused on this thread: the same open, at once, on a new one. Which of
    // the two opens it is the measurement `[GDE-HST-230]` still lacked --
    // whether the refusal belongs to the thread or to the whole process --
    // and it is written either way.
    let (fresh, process_wide) = match out.recover_on_fresh_thread() {
        Ok(name) => return opened(out, &name, " on a fresh thread, after this thread was refused -- \
                                              so the refusal belonged to the thread",
                                  playing, retry_at, backoff, refused),
        Err(e) if e.is_com_refusal() => ("refused on a fresh thread too, so it is process-wide".to_string(), true),
        Err(e) => (format!("and a fresh thread failed otherwise: {e}"), false),
    };
    // Said differently from an absent device, because it means something
    // different: the speaker is there and this process may not have it
    // `[GDE-HST-230]`. A line that read like "still searching" for ten
    // minutes is what made it look like a stall rather than a refusal.
    match refused {
        None => {
            *refused = Some(Refused { since: now, attempts: 1, process_wide });
            tracing::warn!("output refused by Windows, not missing: {refusal} -- the device is there \
                            but this process cannot open it; {fresh}; retrying, and restarting the \
                            player should clear it [GDE-HST-230]");
        }
        Some(r) => {
            r.attempts += 1;
            r.process_wide = process_wide;
            tracing::warn!("output still refused by Windows, {}; {fresh} -- restarting the player \
                            should clear it [GDE-HST-230]", r.describe(now));
        }
    }
}

/// Ask, once, for a new process when a refusal has lasted `[GDE-HST-230]`.
fn restart_if_refused(refused: &Option<Refused>, playing: bool, restart: &mut Option<Restart>) {
    let now = Instant::now();
    let Some(r) = refused.as_ref().filter(|r| r.wants_restart(playing, now)) else { return };
    if let Some(ask) = restart.take() {
        tracing::warn!("output refused by Windows, {}, process-wide: asking for a restart, \
                        which has opened it at once [GDE-HST-230]", r.describe(now));
        ask();
    }
}

/// A reopen that opened -- on this thread, or on a fresh one, which `how`
/// says.
fn opened(
    out: &mut Output,
    name: &str,
    how: &str,
    playing: bool,
    retry_at: &mut Option<Instant>,
    backoff: &mut Duration,
    refused: &mut Option<Refused>,
) {
    // Opening succeeded, which says nothing about whether anyone can
    // hear it: the dummy accepts audio perfectly forever. Treat that as
    // a failure so we keep looking for a real sink `[PI3-API-030]`.
    if crate::sink::current().audible == Tristate::No {
        tracing::warn!("output opened onto a dummy -- still silent, retrying");
        out.mark_failed();
        return;
    }
    // The stream's own error callback writes only the first error
    // of a failure and counts the rest; here is the count.
    let errors = match out.take_stream_errors() {
        0 | 1 => String::new(),
        n => format!(" ({n} stream errors while it was down)"),
    };
    // How long a refusal lasted is the one measurement of it
    // there is, so the line that ends one carries it.
    match refused.take() {
        Some(r) => tracing::info!("output recovered on {name}{how}{errors}, after {} [GDE-HST-230]",
                                  r.describe(Instant::now())),
        None if !how.is_empty() => tracing::info!("output recovered on {name}{how}{errors} [GDE-HST-230]"),
        None => tracing::info!("output recovered on {name}{errors}"),
    }
    *retry_at = None;
    *backoff = RETRY;
    // The stream comes back stopped; only resume it if the listener
    // had not paused in the meantime.
    out.set_playing(playing);
}

/// A run of reopens Windows has refused `[GDE-HST-230]`, kept until one opens.
struct Refused {
    since: Instant,
    attempts: u32,
    /// The latest attempt was refused on a fresh thread too.
    process_wide: bool,
}

impl Refused {
    /// Whether a new process should take this refusal over. Measured on
    /// 2026-10-28: the refusal belongs to the process, so no open from inside
    /// it gets past one, and a new process does. Only while playing: a
    /// transfer's end wanting the device back during a pause is not worth a
    /// restart, and the restarted player resumes what was playing.
    fn wants_restart(&self, playing: bool, now: Instant) -> bool {
        self.process_wide && playing && now.saturating_duration_since(self.since) >= RESTART_AFTER
    }

    fn describe(&self, now: Instant) -> String {
        let s = now.saturating_duration_since(self.since).as_secs();
        format!("{} refused attempt{} over {}m{:02}s",
                self.attempts, if self.attempts == 1 { "" } else { "s" }, s / 60, s % 60)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_silent_handle_asks_for_nothing_and_answers_honestly() {
        let h = PathHandle::silent();
        // No device is not a fault: a discard sink must let the queue advance,
        // or a host with no audio would sit forever on one passage.
        assert!(h.audible());
        assert_eq!(h.recoveries(), 0);
        // Requests are dropped rather than panicking on a missing supervisor.
        h.set_playing(true);
        h.reopen();
        h.quiet(true);
        h.quiet(false);
        h.shutdown();
    }

    #[test]
    fn the_handle_carries_no_way_to_block() {
        // A compile-time-ish guard expressed as intent: the handle exposes only
        // sends and atomic loads. If a method here ever starts opening devices
        // or sleeping, `[GDE-FBD-090]` has been broken and this test's name is
        // the place someone will look.
        let h = PathHandle::silent();
        let before = std::time::Instant::now();
        for _ in 0..10_000 {
            let _ = h.audible();
            let _ = h.channels();
        }
        assert!(before.elapsed() < Duration::from_millis(500),
                "reading path state must be a load, not a query");
    }

    #[test]
    fn a_windows_refusal_is_told_apart_from_an_absent_device() {
        use crate::output::OutputError;
        // Verbatim from lempi-local.log, 2026-10-18 `[GDE-HST-230]`.
        let seen = OutputError::Config("Failed to get audio client: Cannot change thread mode \
                                        after it is set. (os error -2147417850)".into());
        assert!(seen.is_com_refusal());
        assert!(!OutputError::NoDevice.is_com_refusal());
        assert!(!OutputError::Config("Failed to get audio client: Element not found. \
                                      (os error -2147023728)".into()).is_com_refusal());
    }

    #[test]
    fn only_a_lasting_process_wide_refusal_while_playing_asks_for_a_restart() {
        let since = Instant::now();
        let r = Refused { since, attempts: 4, process_wide: true };
        assert!(r.wants_restart(true, since + RESTART_AFTER));
        assert!(!r.wants_restart(true, since + RESTART_AFTER - Duration::from_secs(1)), "a blip is waited out");
        assert!(!r.wants_restart(false, since + RESTART_AFTER * 10), "a paused player is not restarted into playing");
        let other = Refused { process_wide: false, ..r };
        assert!(!other.wants_restart(true, since + RESTART_AFTER * 10),
                "a fresh thread's other failure is not a process-wide refusal");
    }

    #[test]
    fn a_refusal_reports_its_count_and_its_length() {
        let since = Instant::now();
        let r = Refused { since, attempts: 15, process_wide: true };
        assert_eq!(r.describe(since + Duration::from_secs(605)), "15 refused attempts over 10m05s");
        let one = Refused { since, attempts: 1, process_wide: true };
        assert_eq!(one.describe(since), "1 refused attempt over 0m00s");
    }
}
