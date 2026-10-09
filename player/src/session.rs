//! Starting and replenishing a listening session.
//!
//! Every binary that plays from the library needs the same three things: the
//! library open, the resume point recovered, and the queue kept full. Written
//! once here so `station` and `lempi` cannot drift apart on what "start
//! playing" means — they differ only in whether a browser is watching.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::collections::{HashMap, VecDeque};
use std::path::Path;
use std::sync::{Arc, Mutex};

use crate::db::{DbError, Library, PlayerStore};
use crate::director::library::{Director, Explanation, QueuedNote, Rng};
use crate::engine::Engine;
use crate::playback::{Outcome, Playback};
use crate::switch::Progress;

/// What the next pick follows, for flow `[SPEC-DIR-160]`: the last passage
/// already queued, or, with nothing queued, the one on air.
///
/// The queue empties at every admission when the depth is 1 -- a setting the
/// panel allows -- and after a person clears it by hand. Taking the tail from
/// the queue alone left every such pick with nothing to follow, so at depth 1
/// flow never applied at all. Only the very first pick of a session, with
/// nothing on air either, has no flow order.
fn flow_tail(chosen: &[i64], sounding: Option<i64>) -> Option<i64> {
    chosen.last().copied().or(sounding)
}

fn unix_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Why each recently chosen passage was chosen, shared with the web UI.
///
/// The engine thread writes, the server reads. Kept in memory rather than read
/// back from the database on every push: the answer is already in hand at the
/// moment of choosing, and a request path that re-queries for it would be a
/// second source of truth for the same fact.
pub type Explanations = Arc<Mutex<ExplanationLog>>;

/// Programme selection, shared between the web thread and the engine thread.
///
/// The Director lives on the engine thread and is not `Sync`, so the browser
/// cannot reach it directly. It writes an intent here instead, and the engine
/// applies it on its next refill. One shared cell rather than another command
/// channel: the state is small, idempotent, and the browser needs to *read* it
/// back to show what is active.
#[derive(Default)]
pub struct Controls {
    /// Chosen by hand, overriding time of day until cleared `[SPEC-DIR-185]`.
    pub manual_program: Option<i64>,
    /// `(id, name, start_time)`, for the browser to offer.
    pub programs: Vec<(i64, String, String)>,
    /// The programme actually in force, as the engine last resolved it.
    pub active: Option<String>,
    /// Asks for a live Director rebuild, so music imported into the library
    /// becomes selectable without restarting the player `[IMPL-SUI-075]`.
    ///
    /// The same intent-cell pattern as `manual_program`, and for the same
    /// reason: the Director is not `Sync`, so the browser cannot reach it.
    pub reload_requested: bool,
    /// What the rebuild is doing, for the browser to show. Set by the engine.
    pub reload_status: Option<String>,
    /// Which backend is sounding, for the browser to show `[SPEC-BK-025]`.
    /// `None` until the engine has said, which is a starting player rather than
    /// an absent one.
    pub backend: Option<String>,
    /// Whether a guest is attached at all. Without one the control is not
    /// offered, rather than offered and refused.
    pub guest_available: bool,
    /// Whether the side now sounding can move inside a passage
    /// `[REQ-VIS-225]`. Published from the live backend's own capabilities
    /// each pass, so it follows a switch rather than describing whichever
    /// side happened to start first `[SPEC-BK-040]`.
    pub can_seek: bool,
    /// What the guest *is* — "MPD at 127.0.0.1:6600" rather than "MPD". An
    /// option naming a category tells a listener nothing about whether the
    /// thing behind it is the one they are looking at.
    pub guest_name: Option<String>,
    /// A request to write cue sheets `[REQ-VIS-205]`, and what came of it.
    /// The intent-cell pattern again: generation touches the music folder and
    /// belongs on the engine thread, not in a request handler.
    pub cue_requested: Option<bool>,
    pub cue_status: Option<String>,
    /// The same for cover art `[REQ-VIS-210]`.
    pub covers_requested: Option<bool>,
    pub covers_status: Option<String>,
    /// The same for per-song lyrics `[REQ-VIS-215]`.
    pub lyrics_requested: Option<bool>,
    pub lyrics_status: Option<String>,
    /// The same for the sidecar beside the audio `[REQ-VIS-220]`.
    pub sidecar_requested: Option<bool>,
    pub sidecar_status: Option<String>,

    /// Where the listener asked to move to inside the current passage, in ms
    /// `[REQ-VIS-225]`.
    ///
    /// An intent cell rather than a command, because a command reaches the
    /// **local engine** and a seek has to reach whichever side is sounding.
    /// The engine channel would have moved Lempi's own playback while MPD
    /// carried on regardless.
    ///
    /// The passage, when the client named one, travels with it: the side
    /// that is sounding refuses a seek aimed at a passage that is no longer
    /// on air, rather than moving the next one (review O-01).
    pub seek_requested: Option<(Option<i64>, u64)>,

    /// A request to change sides. The same intent-cell pattern as
    /// `reload_requested`, and for the same reason: the backends are not `Sync`
    /// and the browser cannot reach them.
    pub switch_requested: Option<String>,
    /// What the last switch did, including what it could not carry
    /// `[SPEC-BK-045]`.
    pub switch_status: Option<String>,

    /// The Director's pool as `(eligible, total)`, refreshed on adoption.
    ///
    /// Here so a rebuild's effect is **observable** rather than asserted:
    /// importing music and reloading moves `total`, and without a number to
    /// look at "it reloaded" would be a claim with nothing behind it
    /// `[GDE-CHT-030]`.
    pub pool: Option<(usize, usize)>,
}

pub type SharedControls = Arc<Mutex<Controls>>;

/// A passage with less than this left is not carried across a handoff.
///
/// It would arrive with a second or two to run, and MPD stops a song seeked
/// past the end of its span outright `[SPEC-BK-055]`. Starting the incoming
/// side at the next passage is the better answer, and the report says so.
const TAIL_NOT_WORTH_CARRYING_MS: u64 = 2_000;

/// How long a handoff will wait for the incoming side to be heard.
///
/// MPD was measured at 14-27 ms `[SPEC-BK-055]`, so this is not a budget but
/// a backstop: a guest that has died should not hold the loop, and the ring
/// holds about 14 s, which is two orders of magnitude more than this.
const SOUNDING_WAIT_MS: u64 = 1_500;

/// What a seamless handoff did, in enough detail to say it honestly
/// `[PI3-API-030]`.
#[derive(Debug, Default)]
pub struct Handoff {
    pub carried: crate::switch::Carried,
    /// How the outgoing side stopped. `None` when there was nothing to stop
    /// because the session was already on the side asked for — which is not
    /// a fade and must not be reported as one `[PI3-API-030]`.
    pub stopped: Option<crate::switch::Stopped>,
    /// The passage that crossed mid-play, and the position it resumed at.
    /// `None` when nothing was sounding, or when too little was left of it.
    pub resumed: Option<(i64, u64)>,
    /// How long the incoming side took to become audible. `None` means it
    /// never did within the backstop, and the changeover had a gap.
    pub took_ms: Option<u64>,
}

/// How much queued audio must be in hand before a live Director rebuild starts
/// `[IMPL-SUI-075]`.
///
/// Measured by `dircheck` over 8,330 radio passages, 2026-09-12: **9.1-11.4 s
/// on the appliance**, against 11.3-12.1 s for the same library before the work
/// tier `[GDE-WRK-035]` added a third history map. The ranges overlap, so the
/// tier costs nothing measurable; a single cold run of either reaches 14 s.
/// Three minutes covers the slow case a dozen times over. The margin is not
/// really about time — the rebuild is off the audio path and cannot glitch a
/// note. It is about **I/O**, heavy SQLite reading from an SD card, and
/// starting it only when decode is well ahead keeps the two from contending
/// for the same card.
///
/// **Two numbers were wrong here before, the same way.** 9.86 s was measured on
/// a smaller library and then read as a baseline for a much later change. Its
/// replacement, "12.9 s against 11.5 s", compared two single cold runs taken at
/// different times and reported their difference as a 1.4 s cost. Only
/// interleaved repeats of both binaries, sharing cache state, showed the
/// spread between runs to be larger than the difference between versions
/// `[GOV-SRC-020]`. One sample off an SD card is an anecdote.
///
/// The default depth of five passages holds far more than this, so in ordinary
/// running the rebuild starts at once; the threshold bites only when the queue
/// is short, which is exactly when the Director is needed for something else.
pub const RELOAD_MIN_QUEUE_MS: u64 = 180_000;

/// Put the calling thread at the back of both queues `[PI3-FOUND-220]`.
///
/// The Director rebuild is the one piece of genuinely heavy work Lempi does
/// while audio is playing -- a flavor index over 8,330 radio passages, about
/// ten seconds of CPU and SD card on a Pi Zero 2W. `RELOAD_MIN_QUEUE_MS`
/// already decides *when* it may start, and decides it well; what neither it
/// nor anything else decided was how hard it should push once running. It ran
/// at the same priority as the thread feeding the speaker.
///
/// Measured on lempi02w: the rebuild pulls about 256 MB off the SD card at
/// ~15 MB/s while using half a core, and it used to leave no log line at all
/// to connect that to anything.
///
/// It was blamed at the time for stutters heard at 76 s, 89 s and 96 s after
/// power-up. **That attribution was withdrawn** -- those were the appliance's
/// antenna, not its scheduler `[PI3-FOUND-320]`. What survives is the direct
/// measurement rather than the story: with this thread stepped aside, a full
/// 10.3 s rebuild added exactly zero underruns (72,590 before, 72,590 after).
/// Keeping the largest non-audio consumer on the machine out of the way at the
/// moment audio starts is worth doing on its own evidence.
///
/// Both calls are per-**thread** on Linux despite their names: `PRIO_PROCESS`
/// and `IOPRIO_WHO_PROCESS` with a pid of `0` mean the calling thread. That is
/// exactly the granularity wanted -- the engine keeps everything it has, and
/// only the rebuild gives way. A process-wide `Nice=` in the unit file could
/// not express this, which is why it is here and not there.
#[cfg(target_os = "linux")]
fn step_aside() {
    // `IOPRIO_CLASS_IDLE` is 3, in the top three bits of the priority word:
    // the card is touched only when nothing else wants it. Failures are
    // ignored on purpose -- a rebuild that could not lower itself is still a
    // rebuild worth doing, and refusing to run would cost the listener their
    // programmes to protect a few seconds of audio.
    const IOPRIO_WHO_PROCESS: libc::c_long = 1;
    const IOPRIO_CLASS_IDLE: libc::c_long = 3;
    unsafe {
        libc::setpriority(libc::PRIO_PROCESS, 0, 10);
        libc::syscall(libc::SYS_ioprio_set, IOPRIO_WHO_PROCESS, 0, IOPRIO_CLASS_IDLE << 13);
    }
}

/// Nothing to do where the notion does not exist; the rebuild simply runs.
#[cfg(not(target_os = "linux"))]
fn step_aside() {}

/// Bounded on purpose. Only the queue and what is playing can be asked about,
/// so a handful is plenty and an unbounded map would grow for the life of the
/// process.
const KEEP_EXPLANATIONS: usize = 32;

/// How often the OS's UTC offset is asked again `[SPEC-DIR-180]`. A change of
/// daylight saving time is then at most this late, which is well inside any
/// programme's own length; asking costs a syscall and one small read.
const UTC_RESYNC: std::time::Duration = std::time::Duration::from_secs(3600);

/// How long a marked passage may be neither queued nor sounding, with no word
/// of what became of it, before its mark is settled as a play. Every backend
/// says within a tick or two; this bounds a path that never does.
const NOTE_GRACE: std::time::Duration = std::time::Duration::from_secs(120);

/// How long selection rests after finding nothing it may play.
const FALLBACK_REST: std::time::Duration = std::time::Duration::from_secs(30);

/// The marks the Director holds for one passage, oldest first -- a person may
/// queue one passage twice -- and since when it has been out of sight.
#[derive(Default)]
struct Marks {
    notes: VecDeque<QueuedNote>,
    unseen_since: Option<std::time::Instant>,
}

#[derive(Default)]
pub struct ExplanationLog {
    by_passage: HashMap<i64, Explanation>,
    order: VecDeque<i64>,
}

impl ExplanationLog {
    pub fn get(&self, passage_id: i64) -> Option<&Explanation> {
        self.by_passage.get(&passage_id)
    }
    fn insert(&mut self, why: Explanation) {
        let id = why.passage_id;
        if self.by_passage.insert(id, why).is_none() {
            self.order.push_back(id);
        }
        while self.order.len() > KEEP_EXPLANATIONS {
            if let Some(old) = self.order.pop_front() {
                self.by_passage.remove(&old);
            }
        }
    }
}

pub struct Session {
    pub lib: Library,
    store: Option<PlayerStore>,
    /// Where the saved passage left off, if there was one `[REQ-AUD-140]`.
    pub resume_ms: u64,
    resume_id: Option<i64>,
    /// Was it playing when it last stopped? `[PI5-PWR-030]`
    resume_playing: bool,
    depth: usize,
    director: Option<Director>,
    rng: Rng,
    /// A second connection, because `prime` hands the resume store to the
    /// engine. Two handles on one SQLite file is the cheaper answer than
    /// sharing one across a thread boundary for a write this rare.
    decisions: Option<PlayerStore>,
    explanations: Explanations,
    controls: SharedControls,
    /// The Director's mark on each passage coming, held until the backend says
    /// what became of it `[REQ-PD-112]`: played, and the mark stays; declined,
    /// dropped or lost, and it is taken back -- a rejection earns no mark
    /// `[SPEC-PLAY-050]`. Bounded by the queue and `NOTE_GRACE`.
    notes: HashMap<i64, Marks>,
    /// The listener-side file, so a rebuild can open its own connection
    /// `[IMPL-SUI-075]`. A path rather than a shared handle, for the reason
    /// `Ui` keeps one: `rusqlite`'s `Connection` is not `Sync`.
    db: std::path::PathBuf,
    /// The catalog-side file. Every installation is split
    /// `[IMPL-DBSPLIT-025]`, so this differs from `db` except in a test fixture
    /// that holds both halves in one file; carried separately here for the
    /// same reason `db` is -- the rebuild thread needs both to reopen
    /// `Library::open_split`, not just one.
    library: std::path::PathBuf,
    /// A rebuild in flight. `Director` is `Send`, asserted at compile time in
    /// `dircheck`, so it is built on its own thread and handed back here —
    /// the running one keeps answering selections throughout, and there is
    /// never a window with none.
    rebuild: Option<std::sync::mpsc::Receiver<Result<Box<Director>, String>>>,
    /// The queue as last written to `player_queue` `[SPEC-DIR-225]`, so a
    /// refill that changed nothing does not write. Empty at open rather than
    /// primed from the database: the first refill then reconciles whatever
    /// the restore could not rebuild, instead of leaving the table naming
    /// passages that are not queued.
    saved_queue: Vec<i64>,
    /// The random-refill failure being repeated, and how many times. Refill
    /// runs every tick, so a query that cannot succeed -- a catalogue missing
    /// a column, measured 2026-09-26 on the phone spike -- wrote the same line
    /// **1,724 times in about twenty seconds**. Said once, again only if the
    /// message changes, and once more with the count when it clears.
    refill_failing: Option<(String, u64)>,
    /// Until when selection rests after finding nothing -- the Director's,
    /// even with rotation set aside, or the no-Director fallback's -- or after
    /// the fallback failed: each is a pass over the whole pool, and refill
    /// runs on every tick.
    fallback_rest: Option<std::time::Instant>,
    /// When the OS's UTC offset was last read `[SPEC-DIR-180]`; see
    /// `resync_utc_offset`.
    utc_synced: std::time::Instant,
}

impl Session {
    /// `depth` is how many passages to keep queued ahead. `library` is the
    /// catalog-side file -- equal to `db` only in a single-file test fixture;
    /// every installation is split `[IMPL-DBSPLIT-025]`.
    pub fn open(db: &Path, library: &Path, depth: usize) -> Result<Self, DbError> {
        // **`[PI3-FOUND-210]` The phases are timed because guessing at them
        // cost a whole evening.** Measured on lempi02w: nineteen seconds pass
        // inside this function before the audio device is even opened, and
        // not one instrument on the appliance could say which statement spent
        // them. Four separate plausible culprits were proposed and measured
        // away first -- the Director build (already deferred, below), the tag
        // scan (a no-op once `file_tags` is complete), `count_radio` (82 ms),
        // and PipeWire xruns (zero). Boot-to-audio is a headline number for a
        // machine whose power switch is the speaker's own, so the phases that
        // make it up are now reported at startup rather than reconstructed
        // from a journal afterwards.
        let started = std::time::Instant::now();
        let lib = Library::open_split(db, library)?;
        let after_lib = started.elapsed();
        // A resume point that cannot be opened is a first run, not a failure:
        // playback must never be blocked by the loss of a convenience.
        let store = PlayerStore::open_split(db, library)
            .map_err(|e| tracing::warn!("resume state unavailable ({e}); continuing without it"))
            .ok();
        let after_store = started.elapsed();
        let saved = store.as_ref().and_then(|s| s.load().ok()).flatten();
        let after_load = started.elapsed();
        // The saved play state is carried, not discarded. It was read and
        // thrown away here for as long as the row has existed, which is why an
        // appliance that lost power came back silent even though it had been
        // playing `[PI5-PWR-030]`.
        let (resume_id, resume_ms, resume_playing) = match saved {
            Some((Some(id), pos, playing)) => (Some(id), pos, playing),
            _ => (None, 0, false),
        };
        // Before the Director reads it, so a corrected offset takes effect on
        // this same startup rather than the next one `[REQ-VIS-255]`.
        if let Some(s) = &store {
            s.sync_utc_offset();
        }
        // Cumulative marks, differenced here, so each line is the cost of one
        // step rather than a running total the reader has to subtract.
        let after_utc = started.elapsed();
        tracing::info!(
            "session open: library {}ms, store {}ms, resume-load {}ms, utc-sync {}ms (total {}ms)",
            after_lib.as_millis(),
            (after_store - after_lib).as_millis(),
            (after_load - after_store).as_millis(),
            (after_utc - after_load).as_millis(),
            after_utc.as_millis(),
        );
        // The Director is not built here, synchronously -- loading its
        // flavor index over 8,330 radio passages measures ~10s on this
        // appliance `[IMPL-SUI-075]`'s own recorded figure, and a resumed
        // passage is already known: it needs no selection at all to start
        // playing. `reload_requested` below asks for the *same* background
        // build `tend_rebuild` already runs for a live `/library/reload`,
        // through the *same* queue-depth gate `[IMPL-SUI-075]` already
        // reasons about for exactly this SD-card-contention concern --
        // cold start has no queued audio yet either, so it is the right
        // gate to reuse, not a new one to invent. Until it adopts, a refill
        // that finds the queue short is served by the no-Director fallback,
        // `Library::fallback_radio` -- every rule but rotation, without the
        // listener's per-recording dials or character shaping. `prime`
        // restores the remembered queue first so that an ordinary start never
        // reaches it.
        let controls = SharedControls::default();
        if let Ok(mut c) = controls.lock() {
            c.reload_requested = true;
            // Restored here, not left to default to `None` (time of day):
            // an appliance with no realtime clock and no at-home relevance
            // to a truck's driving hours must never fall back to schedule-
            // based selection, only ever to whatever was chosen last
            // `[SPEC-DIR-185]`. `active()`'s own stale-id fallback already
            // handles a program that no longer exists, so this is safe to
            // set blind, before the Director that would validate it exists.
            c.manual_program = store.as_ref().and_then(|s| s.load_manual_program());
        }
        Ok(Self {
            lib,
            store,
            resume_ms,
            resume_id,
            resume_playing,
            depth,
            director: None,
            rng: Rng::from_clock(),
            decisions: PlayerStore::open_split(db, library).ok(),
            explanations: Explanations::default(),
            controls,
            notes: HashMap::new(),
            db: db.to_path_buf(),
            library: library.to_path_buf(),
            rebuild: None,
            saved_queue: Vec::new(),
            refill_failing: None,
            fallback_rest: None,
            // `open` has just synced it, above.
            utc_synced: std::time::Instant::now(),
        })
    }

    /// Ask the OS for its UTC offset again, at most every `UTC_RESYNC`
    /// `[SPEC-DIR-180]`.
    ///
    /// The offset used to be read once, at start, so an appliance that stayed
    /// up across a change of daylight saving time chose every programme an hour
    /// off until something restarted it (review O-06, 2026-10-08).
    fn resync_utc_offset(&mut self) {
        if self.utc_synced.elapsed() < UTC_RESYNC {
            return;
        }
        self.utc_synced = std::time::Instant::now();
        self.apply_utc_offset();
    }

    /// Store what the OS says, and give it to the Director in hand: a rebuild
    /// would read it from the store, but the running Director read it once,
    /// when it was built.
    fn apply_utc_offset(&mut self) {
        let Some(offset) = self.decisions.as_ref().and_then(|s| s.sync_utc_offset()) else {
            return;
        };
        if let Some(d) = self.director.as_mut() {
            let programs = d.programs_mut();
            if programs.utc_offset_minutes != offset {
                tracing::info!(
                    "utc offset is now {offset:+} min (was {:+}); programmes follow it",
                    programs.utc_offset_minutes
                );
                programs.utc_offset_minutes = offset;
            }
        }
    }

    /// Start a rebuild when asked, and adopt one that has finished
    /// `[IMPL-SUI-075]`.
    ///
    /// Called before the shortfall check, because a rebuild is exactly what
    /// must **not** happen while the queue is short: the Director is needed to
    /// refill it, and the SD card is needed to decode from it.
    /// Driven through `Playback` rather than `Engine` `[SPEC-BK-020]`: a
    /// rebuild waits on how much is queued, and that is true of any backend.
    fn tend_rebuild(&mut self, engine: &dyn crate::switch::Backend) {
        if let Some(rx) = &self.rebuild {
            match rx.try_recv() {
                Err(std::sync::mpsc::TryRecvError::Empty) => return, // still building
                Ok(Ok(fresh)) => {
                    self.adopt(*fresh, engine);
                    self.say_reload("rebuilt");
                }
                Ok(Err(e)) => self.say_reload(&format!("rebuild failed: {e}")),
                Err(std::sync::mpsc::TryRecvError::Disconnected) => {
                    // The thread died without answering. The old Director is
                    // untouched and still selecting, so this costs nothing but
                    // the attempt -- which is the whole point of building a
                    // replacement rather than dropping the incumbent first.
                    self.say_reload("rebuild thread stopped without answering");
                }
            }
            self.rebuild = None;
            return;
        }

        let asked = match self.controls.lock() {
            Ok(mut c) => {
                // First time through, publish the pool so there is a number to
                // compare a rebuild against.
                if c.pool.is_none() {
                    drop(c);
                    self.publish_pool();
                    match self.controls.lock() {
                        Ok(mut c) => std::mem::take(&mut c.reload_requested),
                        Err(_) => false,
                    }
                } else {
                    std::mem::take(&mut c.reload_requested)
                }
            }
            Err(_) => false,
        };
        if !asked {
            return;
        }
        // Enough audio in hand, or a queue already as full as it will get --
        // waiting past that point would be waiting for something that is not
        // coming.
        let queued_ms: u64 = engine.queued_ms();
        if queued_ms < RELOAD_MIN_QUEUE_MS && engine.shortfall() > 0 {
            self.say_reload(&format!(
                "waiting for {} s of queue before rebuilding ({} s in hand)",
                RELOAD_MIN_QUEUE_MS / 1000,
                queued_ms / 1000
            ));
            // Put the request back: it has not been served, only deferred.
            if let Ok(mut c) = self.controls.lock() {
                c.reload_requested = true;
            }
            return;
        }

        let path = self.db.clone();
        let library = self.library.clone();
        let (tx, rx) = std::sync::mpsc::channel();
        match std::thread::Builder::new()
            .name("director-rebuild".into())
            .spawn(move || {
                step_aside();
                let began = std::time::Instant::now();
                let built = Library::open_split(&path, &library)
                    .and_then(|l| l.director())
                    .map(Box::new)
                    .map_err(|e| format!("{e:?}"));
                // Said out loud because it was not: this thread spends about
                // ten seconds of a Pi Zero 2W and left no trace at all, which
                // is why the stutters it caused were attributed to the radio
                // for most of an evening `[PI3-FOUND-220]`.
                tracing::info!("director rebuild finished in {}ms", began.elapsed().as_millis());
                let _ = tx.send(built);
            }) {
            Ok(_) => {
                self.rebuild = Some(rx);
                self.say_reload("rebuilding");
            }
            Err(e) => self.say_reload(&format!("could not start rebuild: {e}")),
        }
    }

    /// Swap a freshly built Director in, carrying the queue's bookkeeping over.
    ///
    /// The replacement's `last_played` comes from `listener_play_history`,
    /// which does not know about passages that are queued but have not played.
    /// Without re-noting them it would consider their recordings and artists
    /// un-suppressed and could pick a sibling that rotation had ruled out
    /// `[REQ-PD-112]`. The notes are rebuilt rather than moved because each one
    /// holds the *previous* value from the Director that issued it.
    fn adopt(&mut self, fresh: Director, engine: &dyn crate::switch::Backend) {
        self.director = Some(fresh);
        self.notes.clear();
        // A new Director may have something to choose where the last had not.
        self.fallback_rest = None;
        let now = unix_now();
        // **The sounding passage first, and it is not in `queued_ids`.**
        //
        // A passage leaves the queue the moment it is admitted to the mixer,
        // and `record_play` does not write its history row until it crosses the
        // counted threshold minutes later. Between those two points it is in
        // neither place a fresh Director reads, so it was silently un-noted --
        // and a restart mid-passage left its recording, its work and its artist
        // looking as though they had last played whenever they previously did.
        //
        // Found live on 2026-09-11: `lempi02w` was restarted 3m46s into
        // "Funeral for a Friend / Love Lies Bleeding", and the replacement
        // Director read Elton John's last play as 21 days earlier. Thirteen
        // minutes later it queued another recording of the same song, through
        // an 8-hour artist block that never saw a reason to fire
        // `[GDE-WRK-010]`.
        let sounding = engine.head_position().map(|(id, _)| id);
        let ids: Vec<i64> = sounding.into_iter().chain(engine.queued_ids()).collect();
        for id in ids {
            self.mark(id, now);
        }
        self.publish_pool();
    }

    /// Mark a passage as coming, in the running Director `[REQ-PD-112]`.
    fn mark(&mut self, passage: i64, now: i64) {
        if let Some(note) = self.director.as_mut().and_then(|d| d.note_queued(passage, now)) {
            self.notes.entry(passage).or_default().notes.push_back(note);
        }
    }

    /// Resolve the oldest mark on a passage: kept as a play, or taken back.
    fn resolve_mark(&mut self, passage: i64, played: bool) {
        let Some(marks) = self.notes.get_mut(&passage) else { return };
        let note = marks.notes.pop_front();
        if marks.notes.is_empty() {
            self.notes.remove(&passage);
        }
        if let (Some(note), Some(d)) = (note, self.director.as_mut()) {
            if played {
                d.settle(note);
            } else {
                d.forget_queued(note);
            }
        }
    }

    /// What became of passages since the last pass, as the backend saw it.
    fn hear_outcomes(&mut self, engine: &mut dyn crate::switch::Backend, now: i64) {
        // Never played: it could not be opened, or a person took out their own
        // pick. Either way its mark goes, and nothing else happens.
        for id in engine.take_dropped() {
            self.resolve_mark(id, false);
        }
        for outcome in engine.take_outcomes() {
            match outcome {
                Outcome::Queued { passage } => self.mark(passage, now),
                Outcome::Played { passage } => self.resolve_mark(passage, true),
                // A rejection earns no play, so the mark set when it was queued
                // is taken back, and its window starts now rather than at the
                // next rebuild `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`.
                Outcome::Rejected { passage, kind, mbid, at } => {
                    self.resolve_mark(passage, false);
                    if let (Some(d), Some(mbid)) = (self.director.as_mut(), mbid) {
                        d.note_rejection(kind, &mbid, at);
                    }
                }
            }
        }
        self.settle_unseen(&*engine);
    }

    /// Settle, as played, the marks of a passage gone from sight for
    /// `NOTE_GRACE` with no word of it. Rotation errs toward what it already
    /// believed was coming, and the map stays bounded.
    fn settle_unseen(&mut self, engine: &dyn crate::switch::Backend) {
        if self.notes.is_empty() {
            return;
        }
        let seen: std::collections::HashSet<i64> = engine
            .queued_ids()
            .into_iter()
            .chain(engine.head_position().map(|(id, _)| id))
            .collect();
        let now = std::time::Instant::now();
        let mut gone = Vec::new();
        for (id, marks) in self.notes.iter_mut() {
            if seen.contains(id) {
                marks.unseen_since = None;
            } else if now.duration_since(*marks.unseen_since.get_or_insert(now)) >= NOTE_GRACE {
                gone.push(*id);
            }
        }
        for id in gone {
            while self.notes.contains_key(&id) {
                self.resolve_mark(id, true);
            }
        }
    }

    /// Put the pool size where the browser can see it.
    fn publish_pool(&self) {
        let Some(c) = self.census() else { return };
        if let Ok(mut ctl) = self.controls.lock() {
            ctl.pool = Some((c.eligible, c.total()));
        }
    }

    fn say_reload(&self, what: &str) {
        if let Ok(mut c) = self.controls.lock() {
            c.reload_status = Some(what.to_string());
        }
    }

    /// Was the player playing when it last saved? `[PI5-PWR-030]`
    ///
    /// A caller decides what to do with that: `lempi` resumes playback, while
    /// `station` starts when told to and has no use for it.
    pub fn resume_playing(&self) -> bool {
        self.resume_playing
    }

    /// Name a passage before it is shown `[REQ-VIS-170]`.
    ///
    /// MusicBrainz first, then the file's own tags for whatever it did not
    /// answer -- which today is every album name, the release tables being
    /// empty until Vipunen fills them. Done once per passage, on the way into the
    /// queue, rather than per render: it touches the disk, and a snapshot goes
    /// out twice a second.
    /// Takes the library rather than `&self` so it can be called while the
    /// Director holds a mutable borrow of its own field -- disjoint fields,
    /// which the compiler will allow only if the borrow is spelled out.
    fn describe(lib: &Library, e: &mut crate::queue::QueueEntry) {
        lib.describe(e);
        if e.naming.mb_title.is_none()
            || e.naming.mb_artist.is_none()
            || e.naming.mb_album.is_none()
        {
            // The scanned copy first: reading tags means probing the file, and
            // this runs on the way into the queue while music is playing
            // `[REQ-VIS-180]`. Falling back to the file keeps an unscanned
            // library working, just more slowly.
            let tags = lib
                .stored_tags(e.passage_id)
                .unwrap_or_else(|| crate::tags::read(&e.path));
            e.naming.apply_tags(tags);
        }
    }

    /// Hand the engine its store, its resume offset, and a full queue.
    pub fn prime(&mut self, engine: &mut Engine) {
        // Before the store is handed over, since it is the thing that holds
        // them: volume and the skip shape as they were last left
        // `[REQ-VIS-155]`. Volume is resolved for the active output
        // `[SPEC-POV-030.4]`.
        if let Some(store) = self.store.as_ref() {
            let active = store.get_active_output();
            if let Some(mut saved) = store.load_settings() {
                let current_db = crate::output::Volume::db_for(saved.volume);
                let resolved_db = store.resolve_output_volume(&active, current_db);
                saved.volume = crate::output::Volume::amplitude_at_db(resolved_db);
                engine.apply_settings(&saved);
            }
        }
        if let Some(s) = self.store.take() {
            engine.attach_store(s);
        }
        // Read before the `take` below, for the duplicate check after it.
        let resumed = self.resume_id;
        if let Some(id) = self.resume_id.take() {
            match self.lib.passage(id) {
                Ok(mut e) => {
                    Self::describe(&self.lib, &mut e);
                    tracing::info!("resuming passage {id} at {:.1}s", self.resume_ms as f64 / 1000.0);
                    engine.resume_at(self.resume_ms);
                    engine.enqueue(e);
                }
                // The library was rebuilt and the passage renumbered away.
                Err(_) => tracing::warn!("saved passage {id} is no longer in the library"),
            }
        }
        // The queue as it stood `[SPEC-DIR-225]`, and **before** the refill
        // below. The Director is built on its own thread and is not here yet
        // `[IMPL-SUI-075]`, so the only selector this refill would have is the
        // library's fallback, which keeps every rule but rotation and has none
        // of the listener's dials or the programme's character -- harmless
        // for a live rebuild, where the queue is full and it cannot fire, and
        // a poorer start, where the queue is empty and it fills every slot.
        // Restoring first leaves it nothing to fill.
        //
        // Entries arrive with `selected_by` at its `Library::passage` default
        // of `None`, which already means "no selection event to report at all
        // -- a resumed or reconstructed entry". Nothing new to say.
        let remembered: Vec<i64> =
            self.decisions.as_ref().map(|s| s.load_queue()).unwrap_or_default();
        for id in remembered {
            // The head can be named here as well as in `player_state`, if the
            // process stopped between a passage being admitted and the next
            // refill writing the queue without it. Resumed above and queued
            // again here, it would play twice.
            if Some(id) == resumed {
                continue;
            }
            match self.lib.passage(id) {
                Ok(mut e) => {
                    Self::describe(&self.lib, &mut e);
                    engine.enqueue(e);
                }
                // Renumbered away by a rescan `[SPEC-SC-095]`. One passage
                // short is a gap the refill below closes; refusing the rest of
                // the queue over it would not be.
                Err(_) => tracing::warn!("queued passage {id} is no longer in the library"),
            }
        }
        let suppress = engine.snapshot_suppress_h();
        self.refill(engine, suppress);
    }

    /// Top the queue back up to `depth`. Called every tick in a continuous
    /// station; a no-op when the queue is already full, so it is cheap enough
    /// to call unconditionally rather than guessing when it is needed.
    ///
    /// Picks one at a time, telling the Director about each as it goes. Asking
    /// for five at once would weigh all five against the same stale history and
    /// could queue five recordings by one artist `[SPEC-DIR-115]`.
    /// Takes a **backend**, not the engine `[SPEC-BK-020]`. The suppression
    /// windows arrive as an argument because they are the *listener's*
    /// settings, not the backend's: whoever is playing, they are the same.
    pub fn refill(&mut self, engine: &mut dyn crate::switch::Backend, suppress: (u64, u64)) {
        // Before anything else, and deliberately before the shortfall check:
        // a rebuild must not start while the queue is short `[IMPL-SUI-075]`.
        self.tend_rebuild(&*engine);
        self.resync_utc_offset();

        // Apply the browser's programme choice before selecting, and report
        // back what is actually in force -- "auto" resolves to a name only the
        // Director can supply.
        let now = unix_now();
        if let (Some(d), Ok(mut c)) = (&mut self.director, self.controls.lock()) {
            if c.programs.is_empty() {
                c.programs = d
                    .programs()
                    .all()
                    .iter()
                    .map(|p| (p.id, p.name.clone(), format!("{:02}:{:02}", p.start_minute / 60, p.start_minute % 60)))
                    .collect();
            }
            if d.programs().manual() != c.manual_program {
                d.programs_mut().set_manual(c.manual_program);
            }
            c.active = d.programs().active(now).map(|p| p.name.clone());
            // The listener's suppression window lives with the other settings
            // and is persisted by the engine `[REQ-VIS-155]`; the Director is
            // told when it moves `[SPEC-PLAY-050]`.
            if d.suppress_h() != suppress {
                d.set_suppress_h(suppress);
            }
        }

        // What became of each passage the Director marked as coming: a play
        // keeps its mark, anything else gives it back, and a rejection starts
        // its window now `[REQ-PD-112]`, `[SPEC-PLAY-050]`.
        self.hear_outcomes(engine, now);

        let short = engine.shortfall();
        if short == 0 {
            self.remember_queue(&*engine);
            return;
        }
        let mut chosen: Vec<i64> = engine.queued_ids();
        // Read once, before the loop: the passage on air does not change while
        // this refill picks, and `engine` is borrowed mutably below.
        let sounding = engine.head_position().map(|(id, _)| id);
        let mut starved = false;
        // A fill that found nothing rests before trying again: the answer is
        // a pass over the whole pool, and refill runs on every tick.
        let resting = self.fallback_rest.is_some_and(|until| std::time::Instant::now() < until);

        if let (Some(d), false) = (&mut self.director, resting) {
            for _ in 0..short {
                // The tail is what this passage will follow, so flow is
                // measured from it [SPEC-DIR-160] -- see `flow_tail`.
                let tail = flow_tail(&chosen, sounding);
                // Everything eligible blocked by rotation: a repeat is a better
                // answer than silence [REQ-PD-100], so rotation and recovery
                // are set aside, and nothing else is (`decide_relaxed`).
                let Some(mut decision) = d
                    .decide(now, &mut self.rng, &chosen, tail)
                    .or_else(|| d.decide_relaxed(now, &mut self.rng, &chosen))
                else {
                    starved = true;
                    break;
                };
                let entry = decision.entry;
                if let Some(note) = d.note_queued(entry.passage_id, now) {
                    self.notes.entry(entry.passage_id).or_default().notes.push_back(note);
                }
                chosen.push(entry.passage_id);

                // The Director's own bulk pool load never resolves a real
                // artist name for every candidate -- the same reasoning
                // `Library::describe()`'s doc comment gives for not running
                // five correlated subqueries per row, eight thousand times
                // over, to answer a question the selection weighting itself
                // never asks. A runner-up in "It beat" is exactly the
                // handful actually shown on screen once a decision is made
                // `[REQ-VIS-285]`, so it gets the same enrichment a queued
                // entry already does, just for a few rows instead of the
                // whole pool -- before this is recorded or cached, so both
                // sinks carry the same real names the queue would.
                for r in &mut decision.why.runners_up {
                    if let Some(mbid) = &r.mbid {
                        let (title, artist, artist_mbid) = self.lib.recording_names(mbid);
                        if let Some(t) = title {
                            r.title = t;
                        }
                        r.artist = artist;
                        r.artist_mbid = artist_mbid;
                    }
                }

                // Captured before `decision.why` moves into the log below
                // `[REQ-VIS-300]` -- the same field the runner-up
                // enrichment above already reads. `"auto"` marks a genuine
                // Director pick with no program in force, distinct from
                // `None` on the entry itself, which means "no selection
                // event to report at all" (a resumed or reconstructed
                // entry, never this one).
                let picked_by = decision.why.program.clone().unwrap_or_else(|| "auto".into());

                // Recording the reasoning must never be able to stop the
                // music, so both sinks are best-effort [SPEC-DIR-190].
                if let Some(store) = &self.decisions {
                    match serde_json::to_string(&decision.why) {
                        Ok(json) => {
                            if let Err(e) = store.record_decision(now, entry.passage_id, &json) {
                                tracing::error!("record decision: {e}");
                            }
                        }
                        Err(e) => tracing::error!("encode decision: {e}"),
                    }
                }
                // The reasoning, encoded before the log consumes it.
                let why_json = serde_json::to_string(&decision.why).ok();
                if let Ok(mut log) = self.explanations.lock() {
                    log.insert(decision.why);
                }
                let mut entry = entry;
                entry.selected_by = Some(picked_by);
                Self::describe(&self.lib, &mut entry);
                // A short human reading of the flavor, for clients that can
                // show a string and nothing else `[SPEC-MPD-050]`.
                // `d` is the director already borrowed for this pass; asking
                // self.director again here would borrow it twice.
                let flavor = entry
                    .mbid
                    .as_deref()
                    .and_then(|m| d.flavor_summary(m, 3))
                    .unwrap_or_default();
                let passage_id = entry.passage_id;
                // What a guest cannot say for itself `[SPEC-MPD-052]`: the
                // title comes from MusicBrainz and a capture's file tags have
                // none, so a third of the library would arrive unnamed.
                let title = entry.title();
                let artist = entry.artist().unwrap_or_default();
                engine.enqueue(entry);
                // **After** the enqueue: a sticker is addressed by the URI the
                // backend has only just chosen for this passage.
                if let Some(json) = why_json {
                    engine.publish(&crate::switch::Published {
                        passage_id,
                        why: &json,
                        flavor: &flavor,
                        title: &title,
                        artist: &artist,
                        chosen_at: now,
                    });
                }
            }
        }

        if starved {
            // Nothing may be played, even with rotation set aside: every radio
            // passage is held, declined, out of its season, or turned down.
            // Silence is then the honest answer, and the pool on the page
            // reads zero `[REQ-PD-100]`.
            self.fallback_rest = Some(std::time::Instant::now() + FALLBACK_REST);
            self.refill_failed(
                "nothing may be chosen: every radio passage is held, declined, out of its \
                 season or turned down"
                    .into(),
            );
        }

        // No Director yet: the library's own fallback, with every rule but
        // rotation `[SPEC-DIR-230]`. Rested after a fill that found nothing,
        // so an empty answer is not recomputed on every tick.
        let still_short = engine.shortfall();
        if still_short > 0 && self.director.is_none() && !resting {
            let queued = engine.queued_ids();
            match self.lib.fallback_radio(still_short, now, suppress, &queued, &mut self.rng) {
                Ok(entries) if entries.is_empty() => {
                    self.fallback_rest = Some(std::time::Instant::now() + FALLBACK_REST);
                    self.refill_failed(
                        "no radio passage may be played until the Director is built: every \
                         one is held, declined or out of its season"
                            .into(),
                    );
                }
                Ok(entries) => {
                    self.fallback_rest = None;
                    self.refill_recovered();
                    entries.into_iter().for_each(|mut e| {
                        // Also an auto-selection, with no program steering it
                        // `[REQ-VIS-300]` -- the same "auto" the main loop
                        // above uses when no program is in force.
                        e.selected_by = Some("auto".into());
                        Self::describe(&self.lib, &mut e);
                        engine.enqueue(e);
                    })
                }
                Err(e) => {
                    self.fallback_rest = Some(std::time::Instant::now() + FALLBACK_REST);
                    self.refill_failed(e.to_string());
                }
            }
        } else if !starved && still_short == 0 {
            self.refill_recovered();
        }
        self.remember_queue(&*engine);
    }

    /// Say why the queue could not be filled -- once, and again only if the
    /// reason changes; repeats are counted.
    fn refill_failed(&mut self, msg: String) {
        match &mut self.refill_failing {
            Some((last, n)) if *last == msg => *n += 1,
            _ => {
                tracing::error!("refill: {msg} (repeats are counted, not logged, until it clears)");
                self.refill_failing = Some((msg, 1));
            }
        }
    }

    fn refill_recovered(&mut self) {
        if let Some((_, n)) = self.refill_failing.take() {
            tracing::info!("refill: recovered after {n} failed attempt(s)");
        }
    }

    /// Write the queue down when it has changed `[SPEC-DIR-225]`.
    ///
    /// Called from **both** of `refill`'s exits, not only the one that queued
    /// something: a passage the listener added or removed by hand changes the
    /// queue with no shortfall to notice it, and a queue remembered only when
    /// the Director touches it would forget exactly the entries chosen by a
    /// person.
    ///
    /// Gated on the ids actually differing, the way `Engine::persist` gates
    /// the resume point: `refill` runs every tick, and a write per tick would
    /// dominate a loop that is otherwise sub-millisecond.
    ///
    /// Best-effort. Failing to remember the queue costs a restart its place;
    /// interrupting the music over it would cost more.
    fn remember_queue(&mut self, engine: &dyn Playback) {
        let ids: Vec<i64> = engine
            .queued_ids()
            .into_iter()
            .filter(|&id| id > 0)
            .collect();
        if ids == self.saved_queue {
            return;
        }
        let Some(store) = &self.decisions else { return };
        if let Err(e) = store.save_queue(&ids) {
            tracing::error!("save queue: {e}");
            return;
        }
        self.saved_queue = ids;
    }

    /// Hand over **without restarting the passage that is playing**
    /// `[SPEC-BK-065]`.
    ///
    /// The session is the only thing here holding a library, which is why the
    /// transfer lives on it: `[SPEC-BK-030]` carries **passage ids**, and only
    /// the library can turn one back into something playable. Spans are read
    /// again on arrival rather than carried, because a span belongs to the
    /// passage and not to whichever backend last played it. What did not make
    /// it comes back in `carried.lost` -- see `switch::carry_queue`.
    ///
    /// **The only handoff there is.** There was a queue-only one beside it until
    /// this shipped, and it was the wrong answer kept alive: a queue is what is
    /// *waiting*, while the passage actually sounding lives elsewhere and never
    /// crossed at all, so a switch mid-song lost the song. This carries it, at
    /// the position it had reached, and orders the two sides so there is no
    /// silence between them:
    ///
    /// 1. read the outgoing side's head **and** its queue;
    /// 2. build them into the incoming side while the outgoing one still sounds;
    /// 3. tell the incoming side where to start;
    /// 4. wait until it is actually sounding — measured at 14–27 ms for MPD;
    /// 5. only then fade the outgoing side out.
    ///
    /// **Step 4 is why this blocks.** The alternative is to spread the stages
    /// over several passes of the caller's loop, which buys nothing: the ring
    /// holds about 14 seconds and the wait is capped two orders of magnitude
    /// inside that. A handoff that returns before the other side is audible
    /// would be reporting something that has not happened `[PI3-API-030]`.
    ///
    /// **`lead_ms` is added to the position**, because the incoming side starts
    /// in a moment rather than now. Two independent players are not sample
    /// aligned and this does not pretend to be — the promise is that no music is
    /// repeated and none is skipped, not that the seam is inaudible.
    pub fn hand_over_seamless(
        &mut self,
        sw: &mut crate::switch::Switching,
        target: crate::switch::Side,
        fade_ms: u64,
        lead_ms: u64,
    ) -> Result<Handoff, String> {
        use crate::switch::Side;
        if target == sw.active() {
            return Ok(Handoff::default());
        }
        if target == Side::Guest && !sw.has_guest() {
            return Err("no guest backend is attached".into());
        }

        sw.refresh();
        let head = sw.head_position();
        // Whether the outgoing side has already written this passage's play,
        // so the incoming one does not write a second `[SPEC-BK-065]`.
        let counted = sw.head_counted();
        // The sounding passage goes first, then what was waiting behind it.
        let sounding = head.map(|(id, _)| id);
        let mut ids: Vec<i64> = sounding.into_iter().collect();
        ids.extend(sw.queued_ids().into_iter().filter(|id| Some(*id) != sounding));

        let lib = &self.lib;
        let build = |id: i64| {
            lib.passage(id)
                .map(|mut e| {
                    lib.describe(&mut e);
                    e
                })
                .ok()
        };
        // A passage with almost nothing left is not worth carrying: it would
        // arrive already over, and MPD ends a song seeked past its span
        // `[SPEC-BK-055]`. Dropping it starts the handoff at the next one.
        let resume = match head {
            Some((id, pos)) => build(id).and_then(|e| {
                let span = e.end_ms.saturating_sub(e.start_ms);
                let at = pos + lead_ms;
                (at + TAIL_NOT_WORTH_CARRYING_MS < span).then_some((id, at))
            }),
            None => None,
        };
        if head.is_some() && resume.is_none() {
            ids.remove(0);
        }

        let Some(into) = sw.side_mut(target) else {
            return Err("no guest backend is attached".into());
        };
        let mut carried = crate::switch::carry_queue(&ids, into, build);
        carried.moved.dedup();
        if let Some((id, at)) = resume {
            into.resume_at(at);
            if counted {
                into.adopt_counted(id);
            }
        }

        // Wait for the incoming side to be audible, then stop the outgoing one.
        let began = std::time::Instant::now();
        let mut took_ms = None;
        while began.elapsed() < std::time::Duration::from_millis(SOUNDING_WAIT_MS) {
            into.tick();
            into.refresh();
            if into.head_position().is_some() {
                took_ms = Some(began.elapsed().as_millis() as u64);
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        let stopped = sw.stop_and_flip(target, fade_ms)?;

        for id in &carried.lost {
            self.resolve_mark(*id, false);
        }
        Ok(Handoff { carried, stopped: Some(stopped), resumed: resume, took_ms })
    }

    /// How the pool looks right now — for the panel, and for diagnosing a
    /// station that has gone quiet `[SPEC-DIR-190]`.
    pub fn census(&self) -> Option<crate::director::library::Census> {
        self.director.as_ref().map(|d| d.census(unix_now()))
    }

    /// Share the reasoning with a UI. Cloning the handle, not the data.
    pub fn explanations(&self) -> Explanations {
        Arc::clone(&self.explanations)
    }

    /// Share programme control with a UI.
    pub fn controls(&self) -> SharedControls {
        Arc::clone(&self.controls)
    }

    /// The programme in force `[SPEC-DIR-180]`.
    pub fn program(&self) -> Option<String> {
        let d = self.director.as_ref()?;
        d.programs().active(unix_now()).map(|p| p.name.clone())
    }

    pub fn depth(&self) -> usize {
        self.depth
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::Engine;
    use crate::path::PathHandle;

    /// A library with enough radio passages that a uniform draw reproducing a
    /// given order is not something a test could mistake for a restore: six
    /// passages give 120 possible orderings of any three.
    fn library_on_disk(name: &str) -> std::path::PathBuf {
        let tmp = std::env::temp_dir()
            .join(format!("lempi-sess-{name}-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        let c = rusqlite::Connection::open(&tmp).unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT NOT NULL,
                 size_bytes INTEGER, mtime REAL, format TEXT, duration_ms INTEGER,
                 first_seen TEXT, last_seen TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                 lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL, boundary_src TEXT,
                 fade_in_ms INTEGER NOT NULL DEFAULT 20, fade_out_ms INTEGER NOT NULL DEFAULT 20,
                 fade_in_curve TEXT NOT NULL DEFAULT 'exponential',
                 fade_out_curve TEXT NOT NULL DEFAULT 'exponential');
             CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT,
                 weight REAL DEFAULT 1.0, source TEXT);",
        )
        .unwrap();
        for id in 1..=6i64 {
            c.execute(
                "INSERT INTO files VALUES (?1,'md5',?2,1,1.0,'mp3',300000,'t','t')",
                rusqlite::params![id, format!("/m/{id}.mp3")],
            )
            .unwrap();
            c.execute(
                "INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src)
                 VALUES (?1, ?1, 'radio', 0, 300000, 'src')",
                [id],
            )
            .unwrap();
        }
        drop(c);
        // Opened once so the listener-side tables exist, exactly as they do on
        // a real installation before a session is ever started.
        drop(PlayerStore::open(&tmp).unwrap());
        tmp
    }

    /// **The startup bypass, in a test** `[SPEC-DIR-225]`.
    ///
    /// The Director is built on its own thread and is not there yet when
    /// `prime` runs `[IMPL-SUI-075]`, so without a remembered queue the only
    /// selector available is the library's fallback. When it was uniform
    /// random, that is how 149 children's passages, excluded from every
    /// weighted selection, reached the appliance's speakers anyway; it keeps
    /// the occasion floor now, but a remembered queue is still the better
    /// start.
    #[test]
    fn a_remembered_queue_is_restored_rather_than_drawn_at_random() {
        let tmp = library_on_disk("restore");
        PlayerStore::open(&tmp).unwrap().save_queue(&[5, 2, 6]).unwrap();

        let mut session = Session::open(&tmp, &tmp, 5).unwrap();
        let (mut engine, _h) = Engine::new(PathHandle::silent(), 5);
        session.prime(&mut engine);

        let queued: Vec<i64> = engine.queued().map(|e| e.passage_id).collect();
        assert_eq!(
            &queued[..3],
            &[5, 2, 6],
            "the remembered queue comes back in play order, ahead of any refill"
        );
        let _ = std::fs::remove_file(&tmp);
    }

    /// A file-backed library with the tables `Director::load` needs.
    ///
    /// Separate from `library_on_disk`, deliberately: adding these to the
    /// shared fixture would give every other session test a real Director
    /// where it currently exercises the no-Director fallback, which is a
    /// different thing from what those tests are about.
    fn director_library_on_disk(name: &str) -> std::path::PathBuf {
        let tmp = std::env::temp_dir()
            .join(format!("lempi-dir-{name}-{}.db", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        let c = rusqlite::Connection::open(&tmp).unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT NOT NULL,
                                 duration_ms INTEGER);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                 lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL);
             CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL DEFAULT 1.0);
             CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT);
             CREATE TABLE recording_relations (mbid TEXT, related_mbid TEXT, strength REAL);
             CREATE TABLE works (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE recording_works (mbid TEXT, work_mbid TEXT);
             CREATE TABLE listener_preferences (subject_kind TEXT, subject_id TEXT,
                 rotation REAL, recovery REAL, restraint REAL);
             CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             CREATE TABLE listener_settings (id INTEGER PRIMARY KEY,
                 artist_time_scale REAL, recording_time_scale REAL, updated_at TEXT);
             CREATE TABLE listener_occasions (characteristic TEXT, class TEXT, interp TEXT);
             CREATE TABLE listener_occasion_points (characteristic TEXT, class TEXT,
                 month INTEGER, day INTEGER, multiplier REAL);
             CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT,
                 class TEXT, value REAL, source TEXT, accuracy REAL);
             INSERT INTO files VALUES (1, '/m/a.mp3', 600000);
             -- three 180 s radio passages, one album passage that must never appear
             INSERT INTO passages VALUES (1,1,'radio',0,180000,0,0,0.0);
             INSERT INTO passages VALUES (2,1,'radio',0,180000,0,0,0.0);
             INSERT INTO passages VALUES (3,1,'radio',0,180000,0,0,0.0);
             INSERT INTO passages VALUES (4,1,'album',0,180000,0,0,0.0);
             INSERT INTO passage_recordings VALUES (1,'rec-a',1.0),(2,'rec-b',1.0),(3,'rec-c',1.0);
             INSERT INTO recording_artists VALUES ('rec-a','art-1'),('rec-b','art-2'),
                                                  ('rec-c','art-3');
             -- Added after the fact, exactly like the real migration
             -- (`tools/add_fade_columns.py`) adds them to a live database
             -- `[SPEC-SUI-226]` -- so every existing bare `INSERT INTO
             -- passages VALUES (...)` above keeps working unmodified,
             -- backfilled with the same default a real ALTER TABLE gives
             -- every existing row.
             ALTER TABLE passages ADD COLUMN fade_in_ms INTEGER NOT NULL DEFAULT 20;
             ALTER TABLE passages ADD COLUMN fade_out_ms INTEGER NOT NULL DEFAULT 20;
             ALTER TABLE passages ADD COLUMN fade_in_curve TEXT NOT NULL DEFAULT 'exponential';
             ALTER TABLE passages ADD COLUMN fade_out_curve TEXT NOT NULL DEFAULT 'exponential';",
        )
        .unwrap();
        drop(c);
        tmp
    }

    /// A backend that is **sounding but has an empty queue** -- the shape a
    /// restart leaves behind, and the one `queued_ids` alone cannot describe.
    struct Sounding(i64);

    impl Playback for Sounding {
        fn capabilities(&self) -> crate::playback::Capabilities {
            crate::playback::Capabilities::FULL
        }
        fn enqueue(&mut self, _e: crate::queue::QueueEntry) {}
        fn queued_ids(&self) -> Vec<i64> {
            Vec::new() // admitted to the mixer, so no longer queued
        }
        fn queued_ms(&self) -> u64 {
            0
        }
        fn shortfall(&self) -> usize {
            0
        }
        fn take_dropped(&mut self) -> Vec<i64> {
            Vec::new()
        }
        fn resume_at(&mut self, _position_ms: u64) {}
        fn tick(&mut self) -> usize {
            0
        }
        fn is_shutdown(&self) -> bool {
            false
        }
    }
    impl crate::switch::FadeOut for Sounding {
        fn fade_out(&mut self, _ms: u64) -> crate::switch::Stopped {
            crate::switch::Stopped::Cut
        }
    }
    impl crate::switch::Publish for Sounding {
        fn publish(&mut self, _p: &crate::switch::Published<'_>) {}
    }
    impl crate::switch::Progress for Sounding {
        fn head_position(&self) -> Option<(i64, u64)> {
            Some((self.0, 1_000))
        }
    }

    /// The first half of the 2026-09-11 incident `[GDE-WRK-010]`. A passage
    /// leaves the queue when it is admitted to the mixer and does not reach
    /// `listener_play_history` until it crosses the counted threshold minutes
    /// later; a Director rebuilt in between reads neither, so the passage
    /// sounding *right now* looked as though it had never played.
    #[test]
    fn adopting_notes_the_sounding_passage_which_is_in_no_queue() {
        let tmp = director_library_on_disk("sounding");
        let mut session = Session::open(&tmp, &tmp, 5).unwrap();
        let lib = crate::db::Library::open_split(&tmp, &tmp).unwrap();

        let before = lib.director().unwrap();
        assert!(
            before.weigh_all(unix_now()).iter().any(|(e, w)| e.passage_id == 3
                && w.is_eligible()),
            "passage 3 starts eligible, or the test proves nothing"
        );

        session.adopt(lib.director().unwrap(), &Sounding(3));
        let held = session
            .director
            .as_ref()
            .unwrap()
            .weigh_all(unix_now())
            .into_iter()
            .filter(|(e, w)| e.passage_id == 3 && !w.is_eligible())
            .count();
        assert_eq!(held, 1, "the sounding passage must be noted, not left un-suppressed");
        let _ = std::fs::remove_file(&tmp);
    }

    /// The fallback is still the answer when there is nothing to restore --
    /// a fresh image, or a library rescan that renumbered every saved id
    /// `[SPEC-DIR-230]`. Silence would be the worse answer `[REQ-PD-100]`.
    #[test]
    fn a_session_with_nothing_remembered_still_fills_its_queue() {
        let tmp = library_on_disk("empty");
        let mut session = Session::open(&tmp, &tmp, 5).unwrap();
        let (mut engine, _h) = Engine::new(PathHandle::silent(), 5);
        session.prime(&mut engine);
        assert_eq!(engine.queued().count(), 5, "a station with no memory still plays");

        // And what it filled is written down, so the *next* start has
        // something to restore -- the save half of `[SPEC-DIR-225]`, which the
        // refill at the end of `prime` is responsible for.
        let queued: Vec<i64> = engine.queued().map(|e| e.passage_id).collect();
        assert_eq!(
            PlayerStore::open(&tmp).unwrap().load_queue(),
            queued,
            "the queue is remembered as it stands, not as it was asked for"
        );
        let _ = std::fs::remove_file(&tmp);
    }

    /// A passage renumbered away by a rescan `[SPEC-SC-095]` costs its own
    /// slot and nothing else: the rest of the remembered order survives, and
    /// the refill closes the gap.
    #[test]
    fn a_renumbered_passage_is_skipped_not_fatal() {
        let tmp = library_on_disk("gone");
        PlayerStore::open(&tmp).unwrap().save_queue(&[4, 999, 1]).unwrap();

        let mut session = Session::open(&tmp, &tmp, 5).unwrap();
        let (mut engine, _h) = Engine::new(PathHandle::silent(), 5);
        session.prime(&mut engine);

        let queued: Vec<i64> = engine.queued().map(|e| e.passage_id).collect();
        assert_eq!(&queued[..2], &[4, 1], "the survivors keep their order");
        assert!(!queued.contains(&999));
        assert_eq!(queued.len(), 5, "and the refill makes the count up");
        let _ = std::fs::remove_file(&tmp);
    }

    /// A running Director takes a changed UTC offset without a rebuild
    /// `[SPEC-DIR-180]` -- the daylight-saving case, where it used to keep the
    /// offset it was built with until the player restarted.
    #[test]
    fn a_changed_utc_offset_reaches_the_running_director() {
        let Some(os) = crate::director::program::os_utc_offset_minutes() else {
            return; // nothing to prove on a platform this cannot ask
        };
        let tmp = director_library_on_disk("utc");
        let mut session = Session::open(&tmp, &tmp, 5).unwrap();
        let lib = crate::db::Library::open_split(&tmp, &tmp).unwrap();
        session.director = Some(lib.director().unwrap());
        session.director.as_mut().unwrap().programs_mut().utc_offset_minutes = os + 60;

        session.apply_utc_offset();

        assert_eq!(session.director.as_ref().unwrap().programs().utc_offset_minutes, os);
        let _ = std::fs::remove_file(&tmp);
    }

    /// A backend that says what it is told to: what became of passages, and
    /// nothing queued or sounding.
    #[derive(Default)]
    struct Scripted {
        dropped: Vec<i64>,
        outcomes: Vec<Outcome>,
    }

    impl Playback for Scripted {
        fn capabilities(&self) -> crate::playback::Capabilities {
            crate::playback::Capabilities::FULL
        }
        fn enqueue(&mut self, _e: crate::queue::QueueEntry) {}
        fn queued_ids(&self) -> Vec<i64> {
            Vec::new()
        }
        fn queued_ms(&self) -> u64 {
            0
        }
        fn shortfall(&self) -> usize {
            0
        }
        fn take_dropped(&mut self) -> Vec<i64> {
            std::mem::take(&mut self.dropped)
        }
        fn take_outcomes(&mut self) -> Vec<Outcome> {
            std::mem::take(&mut self.outcomes)
        }
        fn resume_at(&mut self, _position_ms: u64) {}
        fn tick(&mut self) -> usize {
            0
        }
        fn is_shutdown(&self) -> bool {
            false
        }
    }
    impl crate::switch::FadeOut for Scripted {
        fn fade_out(&mut self, _ms: u64) -> crate::switch::Stopped {
            crate::switch::Stopped::Cut
        }
    }
    impl crate::switch::Publish for Scripted {
        fn publish(&mut self, _p: &crate::switch::Published<'_>) {}
    }
    impl crate::switch::Progress for Scripted {
        fn head_position(&self) -> Option<(i64, u64)> {
            None
        }
    }

    /// The running Director hears what becomes of each passage as it happens
    /// `[REQ-PD-112]`, `[SPEC-PLAY-050]`, and ends where a Director rebuilt
    /// from what the backend wrote would be. Until 2026-10-08 a skip kept its
    /// queueing marks and opened no window until the next rebuild, and a
    /// person's pick marked nothing.
    #[test]
    fn the_running_director_hears_each_outcome_as_it_happens() {
        let tmp = director_library_on_disk("outcomes");
        // A second passage by art-1, to show the artist mark come and go.
        let c = rusqlite::Connection::open(&tmp).unwrap();
        c.execute_batch(
            "INSERT INTO passages VALUES (5,1,'radio',0,180000,0,0,0.0,20,20,'exponential','exponential');
             INSERT INTO passage_recordings VALUES (5,'rec-d',1.0);
             INSERT INTO recording_artists VALUES ('rec-d','art-1');
             CREATE TABLE listener_rejections (rejection_id INTEGER PRIMARY KEY,
                 rejected_at INTEGER, kind TEXT, passage_id INTEGER, mbid TEXT,
                 heard_ms INTEGER, span_ms INTEGER);",
        )
        .unwrap();
        let mut session = Session::open(&tmp, &tmp, 5).unwrap();
        let lib = crate::db::Library::open_split(&tmp, &tmp).unwrap();
        session.director = Some(lib.director().unwrap());
        let now = unix_now();
        let eligible = |s: &Session, id: i64| {
            s.director.as_ref().unwrap().weigh_all(now).iter()
                .any(|(e, w)| e.passage_id == id && w.is_eligible())
        };
        let mut b = Scripted::default();

        // The Director queues 1; art-1's other passage is held by rotation.
        session.mark(1, now);
        assert!(!eligible(&session, 5), "queued 1 holds its artist");
        // It is skipped: the artist mark goes, and 1 is held by its window.
        b.outcomes.push(Outcome::Rejected {
            passage: 1, kind: crate::db::Rejection::Skip, mbid: Some("rec-a".into()), at: now,
        });
        session.hear_outcomes(&mut b, now);
        assert!(eligible(&session, 5), "a skip earns no artist mark");
        assert!(!eligible(&session, 1), "and its window starts at once");

        // A person queues 2: marked like a Director's pick, then taken back out.
        b.outcomes.push(Outcome::Queued { passage: 2 });
        session.hear_outcomes(&mut b, now);
        assert!(!eligible(&session, 2), "a person's pick is coming, and rotation knows");
        b.dropped.push(2);
        session.hear_outcomes(&mut b, now);
        assert!(eligible(&session, 2), "their change of mind leaves nothing behind");

        // The Director queues 3 and it plays: the mark stays as the play.
        session.mark(3, now);
        b.outcomes.push(Outcome::Played { passage: 3 });
        session.hear_outcomes(&mut b, now);
        assert!(!eligible(&session, 3));
        assert_eq!(session.director.as_ref().unwrap().open_marks(), 0, "everything judged");
        assert!(session.notes.is_empty());

        // What a backend wrote for that session, read by a fresh Director.
        c.execute("INSERT INTO listener_rejections (rejected_at, kind, passage_id, mbid)
                   VALUES (?1, 'skip', 1, 'rec-a')", [now]).unwrap();
        c.execute("INSERT INTO listener_play_history (played_at, passage_id, mbid)
                   VALUES (?1, 3, 'rec-c')", [now]).unwrap();
        let rebuilt = lib.director().unwrap();
        let shape = |d: &Director| -> Vec<(i64, bool)> {
            d.weigh_all(now).iter().map(|(e, w)| (e.passage_id, w.is_eligible())).collect()
        };
        assert_eq!(shape(session.director.as_ref().unwrap()), shape(&rebuilt),
                   "the running Director agrees with one rebuilt from the history");
        drop(c);
        let _ = std::fs::remove_file(&tmp);
    }

    /// A mark whose passage has gone from sight with no word of it is settled
    /// after `NOTE_GRACE`, so the map cannot grow for the life of the process.
    #[test]
    fn a_mark_with_no_word_is_settled_after_the_grace() {
        let tmp = director_library_on_disk("grace");
        let mut session = Session::open(&tmp, &tmp, 5).unwrap();
        let lib = crate::db::Library::open_split(&tmp, &tmp).unwrap();
        session.director = Some(lib.director().unwrap());
        let now = unix_now();
        session.mark(1, now);
        let b = Scripted::default();
        session.settle_unseen(&b);
        assert_eq!(session.notes.len(), 1, "unseen, but not for long yet");
        session.notes.get_mut(&1).unwrap().unseen_since =
            Some(std::time::Instant::now() - NOTE_GRACE);
        session.settle_unseen(&b);
        assert!(session.notes.is_empty());
        assert_eq!(session.director.as_ref().unwrap().open_marks(), 0);
        let _ = std::fs::remove_file(&tmp);
    }

    /// The Director fixture, with every hard rule represented: 1 played just
    /// now (rotation only), 2 held `[SPEC-HOLD-010]`, 3 skipped just now
    /// `[SPEC-PLAY-050]`, 5 a children's recording under the occasion floor
    /// `[SPEC-DIR-134]`, 6 never played. Opened as a session.
    fn every_rule(name: &str) -> (std::path::PathBuf, Session, i64) {
        let tmp = director_library_on_disk(name);
        let now = unix_now();
        let c = rusqlite::Connection::open(&tmp).unwrap();
        c.execute_batch(
            "ALTER TABLE passages ADD COLUMN director_hold TEXT;
             UPDATE passages SET director_hold = 'damaged' WHERE passage_id = 2;
             INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms,
                 lead_out_ms, gain_db) VALUES (5,1,'radio',0,180000,0,0,0.0),
                                              (6,1,'radio',0,180000,0,0,0.0);
             INSERT INTO passage_recordings VALUES (5,'rec-e',1.0),(6,'rec-f',1.0);
             INSERT INTO recording_artists VALUES ('rec-e','art-5'),('rec-f','art-6');
             INSERT INTO listener_occasions VALUES ('user.childrens','childrens','step');
             INSERT INTO listener_occasion_points VALUES ('user.childrens','childrens',1,1,0.000001);
             INSERT INTO flavor VALUES ('recording','rec-e','user.childrens','childrens',
                 1.0,'inherited:mulib',NULL);
             CREATE TABLE listener_rejections (rejection_id INTEGER PRIMARY KEY,
                 rejected_at INTEGER, kind TEXT, passage_id INTEGER, mbid TEXT,
                 heard_ms INTEGER, span_ms INTEGER);",
        )
        .unwrap();
        c.execute("INSERT INTO listener_rejections (rejected_at, kind, passage_id, mbid)
                   VALUES (?1, 'skip', 3, 'rec-c')", [now]).unwrap();
        let session = Session::open(&tmp, &tmp, 5).unwrap();
        c.execute("INSERT INTO listener_play_history (played_at, passage_id, mbid)
                   VALUES (?1, 1, 'rec-a')", [now]).unwrap();
        (tmp, session, now)
    }

    /// With no Director yet, the library's fill keeps every rule but rotation
    /// (the maintainer, 2026-10-08) -- where `random_radio`, until then,
    /// drew from every radio passage -- and favours the longest unplayed.
    #[test]
    fn the_fallback_without_a_director_keeps_every_rule_but_rotation() {
        let (tmp, mut session, now) = every_rule("fallback-lib");
        let suppress = (crate::SKIP_SUPPRESS_H, crate::DEQUEUE_SUPPRESS_H);
        let all = session.lib.fallback_radio(10, now, suppress, &[], &mut session.rng).unwrap();
        let mut ids: Vec<i64> = all.iter().map(|e| e.passage_id).collect();
        ids.sort_unstable();
        assert_eq!(ids, vec![1, 6], "not held, not declined, not children's, not the album");
        for _ in 0..20 {
            let one = session.lib.fallback_radio(1, now, suppress, &[], &mut session.rng).unwrap();
            assert_eq!(one[0].passage_id, 6, "the never-played over the one played just now");
        }
        let rest = session.lib.fallback_radio(10, now, suppress, &[6], &mut session.rng).unwrap();
        assert_eq!(rest.iter().map(|e| e.passage_id).collect::<Vec<_>>(), vec![1],
                   "nothing already queued");
        let _ = std::fs::remove_file(&tmp);
    }

    /// With every eligible passage blocked by rotation, the Director sets
    /// rotation aside and nothing else `[REQ-PD-100]`.
    #[test]
    fn the_director_sets_aside_rotation_and_nothing_else() {
        let (tmp, mut session, now) = every_rule("fallback-dir");
        let c = rusqlite::Connection::open(&tmp).unwrap();
        c.execute("INSERT INTO listener_play_history (played_at, passage_id, mbid)
                   VALUES (?1, 6, 'rec-f')", [now]).unwrap();
        let d = crate::db::Library::open_split(&tmp, &tmp).unwrap().director().unwrap();
        assert!(d.decide(now, &mut session.rng, &[], None).is_none(),
                "everything eligible is blocked, or this proves nothing");
        for _ in 0..30 {
            let pick = d.decide_relaxed(now, &mut session.rng, &[]).expect("a repeat over silence");
            assert!([1, 6].contains(&pick.entry.passage_id), "picked {}", pick.entry.passage_id);
            assert!(pick.why.stages.starts_with("fallback"), "and the panel says so");
        }
        assert!(d.decide_relaxed(now, &mut session.rng, &[1, 6]).is_none(),
                "nothing queued twice, and no hard rule relaxed to fill the gap");
        drop(c);
        let _ = std::fs::remove_file(&tmp);
    }

    /// Flow follows the queue's tail, and with an empty queue the passage on
    /// air `[SPEC-DIR-160]` -- the depth-1 case, where every pick used to be
    /// made with nothing to follow.
    #[test]
    fn flow_follows_the_queue_and_then_what_is_on_air() {
        assert_eq!(flow_tail(&[4, 7], Some(2)), Some(7), "the queue's tail first");
        assert_eq!(flow_tail(&[], Some(2)), Some(2), "nothing queued: what is on air");
        assert_eq!(flow_tail(&[], None), None, "the very first pick follows nothing");
    }
}
