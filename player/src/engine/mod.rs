//! The playback engine: the one place that owns queue, decoders, mixer and output.
//!
//! Everything below it is a component with no opinion about time — the fader
//! knows curves, the mixer knows addition, the queue knows ordering. The engine
//! is where they meet, and deliberately the only place, so there is one pump
//! rather than one per binary.
//!
//! [`Engine::tick`] performs exactly one pump iteration and is public, so the
//! whole engine is testable without a thread, an audio device, or real time.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::sync::mpsc::{channel, Receiver, Sender, TryRecvError};
use std::sync::{Arc, Mutex};

use std::time::Instant;

use crate::db::PlayerStore;
use crate::decoder::PassageDecoder;
use crate::fade::{Curve, Envelope, Fade};
use crate::mixer::{mix, Stream};
use crate::queue::{should_admit_nudged, Queue, QueueEntry};
use crate::resample::Resampler;
use crate::BUFFER_FRAMES;

struct Live {
    dec: PassageDecoder,
    stream: Stream,
    resampler: Resampler,
    converted: Vec<f32>,
    entry: QueueEntry,
    frames_mixed: u64,
    /// Where in the passage this decode began. Non-zero only when resuming, so
    /// reported position stays position-WITHIN-THE-PASSAGE rather than
    /// restarting at zero and making the next save move backwards.
    origin_ms: u64,
    /// `gain_db` as a linear factor, applied per passage `[REQ-AUD-130]`.
    /// Per passage rather than at the mix, so each side of a crossfade carries
    /// its own level -- applying it after mixing would level the blend, not the
    /// passages, and the whole point is that they meet at a matched loudness.
    gain: f32,
}

/// A play already written to history, whose passage finished decoding but may
/// still be sounding out of the output ring `[REQ-VIS-250]`.
///
/// **Why this waits rather than reading `heard_ms` on the spot.** A passage
/// leaves `live` the instant its decoder is exhausted, which is up to a
/// ring's depth -- `BUFFER_FRAMES`, ~15 s here -- before its last sample
/// actually reaches the speaker `[REQ-VIS-240]`. Finalising there froze the
/// figure at "decoded", not "heard": a passage played all the way through and
/// left with 15 s of itself still queued behind it read as ~94%, never 100%,
/// however completely it was listened to. `at_ms`/`since` are the same pair
/// `draining` carries, so the estimate advances exactly as the position
/// display already does -- one clock, trusted by both.
struct PendingFinish {
    play_id: i64,
    span_ms: u64,
    at_ms: u64,
    since: Instant,
}

impl PendingFinish {
    /// How much would have been heard by now, capped at the passage's own
    /// length -- the clock must not run past the music.
    fn estimate(&self) -> u64 {
        (self.at_ms + self.since.elapsed().as_millis() as u64).min(self.span_ms)
    }
}

/// Whether a host names this very node `[SPEC-ECHO-050]`.
///
/// Deliberately shallow: the hostname, `localhost`, and the loopback
/// addresses. It will not catch every alias a network can invent, and it is
/// not trying to -- it catches the ones a person actually types, and the
/// feedback loop it prevents is obvious enough in the log if one slips past.
fn is_self(host: &str) -> bool {
    let h = host.trim().trim_end_matches('.').to_ascii_lowercase();
    let h = h.split(':').next().unwrap_or(&h);
    if matches!(h, "localhost" | "127.0.0.1" | "::1" | "0.0.0.0" | "") {
        return true;
    }
    std::env::var("HOSTNAME").ok()
        .or_else(|| std::fs::read_to_string("/etc/hostname").ok())
        .map(|n| n.trim().to_ascii_lowercase())
        .is_some_and(|n| !n.is_empty() && (h == n || h == n.split('.').next().unwrap_or(&n)))
}

/// What one offset correction spends, and through which actuator
/// `[GDE-ARC-051]`.
///
/// Three fields rather than a pair because the two directions genuinely need
/// different machinery: a contiguous ring can be overlapped into but cannot
/// be waited into `[GDE-ARC-052]`.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub(crate) struct Shift {
    /// Admission nudge, ms. Positive admits earlier, overlapping more of the
    /// outgoing passage and keeping all of the incoming one.
    pub admit_ms: i64,
    /// How far into the incoming passage to open it, ms. This *discards*
    /// that much of its start, so it only ever carries the sub-chunk
    /// remainder admission cannot express.
    pub origin_ms: u64,
    /// Silence before the incoming passage, ms. The only way to sound later.
    pub gap_ms: u64,
}

/// What the delay and follow controls need to render honestly.
#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct EchoNode {
    /// The hand-set trim, ms `[SPEC-DLY-010]`.
    pub trim_ms: i64,
    /// How far either way that trim may go `[SPEC-DLY-010]`.
    ///
    /// **Sent rather than assumed**, for the reason `[REQ-AUD-156]` gives for
    /// the fader's own floor: a control that keeps its own copy of an engine
    /// limit is a second model of one quantity, and the two drift without
    /// anything noticing `[GDE-ARC-033]`. This number was enforced here, again
    /// in the store on load, and hardcoded a third time in the skin's HTML.
    pub trim_limit_ms: i64,
    /// The device's own reported delay in frames, and whether that reading
    /// rests on real hardware timestamps `[GDE-ECHO-290]`.
    ///
    /// `None` is the whole point: a node whose verdict is not `Hardware` has
    /// no measured half at all, and showing 0 would be a measured zero rather
    /// than an absent one `[GOV-SRC-040]`.
    pub measured_frames: Option<u64>,
    /// `measured + trim`, clamped at zero, in frames `[SPEC-DLY-030]`.
    pub offset_frames: u64,
    /// True when the clamp is biting, so the panel can say so instead of
    /// quietly showing a number the user did not ask for.
    pub clamped: bool,
    /// The output rate these frame counts are in.
    ///
    /// Sent rather than assumed: a browser dividing by 44.1 is right until a
    /// node runs at 48 kHz, and then it is quietly wrong by 9 % in a figure
    /// someone is about to calibrate by ear.
    pub rate: u32,
    /// The node being followed, bare host; empty is independent.
    pub follow_host: String,
    /// Whether entering follower mode joins at once **and then keeps this
    /// node aligned continuously** `[SPEC-ECHO-030]`, `[GDE-ARC-041]`.
    ///
    /// Named for the join because that is all it used to govern. It now
    /// governs both halves, which is what a listener reads it as: with it
    /// on, a residual the passage boundary will not take is shed by the
    /// frame trim mid-passage rather than waiting minutes for the master's
    /// next track. Off, alignment is corrected only at a boundary.
    pub join_now: bool,
    /// How much further on the next join should aim, in ms, from what this
    /// node's past joins actually landed at `[GDE-ARC-061]`.
    ///
    /// **Sent rather than recomputed by the follower**, for the reason
    /// `trim_limit_ms` is: a second copy of one quantity drifts from the
    /// first without anything noticing `[GDE-ARC-033]`. The engine owns it
    /// because the engine is what persists it.
    pub join_bias_ms: i64,
    /// What following is actually doing, in the follower's own words
    /// `[SPEC-ECHO-020]`. Empty while independent.
    pub follow_status: String,
    /// How long before a commanded start should sound this node has to begin
    /// arranging it `[GDE-ECHO-375]`.
    ///
    /// `skip_lead_ms` plus the preparation this node has actually **measured**
    /// on itself `[GDE-ECHO-342]`. Published because the follower has to aim
    /// its mid-passage join past it, and it was previously guessing with a
    /// constant: a one-second budget against an engine that fires early by
    /// 500 ms plus a measured figure permitted to reach two seconds, so a node
    /// that paid for one slow seek had every later join declined as `TooLate`
    /// -- and the log said the passage was missed, which reads like the
    /// master's fault. Two models of one quantity, now one.
    pub start_lead_ms: u64,
}

/// Derived in every field but one.
///
/// `trim_limit_ms` is a **constant, not a state**, so a default-constructed
/// node carries the real limit rather than a zero that reads as "no limit".
/// Written out rather than derived for that single field: a snapshot built by
/// any path -- the engine's `publish`, a test fixture, a future caller --
/// then tells the control the same number the engine will enforce, which is
/// the whole point of sending it `[GDE-ARC-033]`.
impl Default for EchoNode {
    fn default() -> Self {
        Self {
            trim_ms: 0,
            trim_limit_ms: crate::db::ECHO_TRIM_LIMIT_MS,
            measured_frames: None,
            offset_frames: 0,
            clamped: false,
            rate: 0,
            follow_host: String::new(),
            join_now: false,
            join_bias_ms: 0,
            follow_status: String::new(),
            start_lead_ms: 0,
        }
    }
}

/// What the UI and the persistence layer read. Cheap to clone.
#[derive(Debug, Clone, Default)]
pub struct PlayerState {
    pub playing: bool,
    pub current: Option<QueueEntry>,
    /// Audible position, not mixed position. The output ring holds ~14 s, so
    /// what has been mixed runs well ahead of what is being heard; saving the
    /// mixed figure would resume ~14 s late every time.
    pub position_ms: u64,
    pub queue_len: usize,
    /// What an echo node needs from this one `[GDE-ECHO-310]`. Empty on a
    /// node that cannot place itself in time, which is a fact rather than an
    /// omission -- see `EchoState::voided_by`.
    pub echo: crate::echo::EchoState,
    /// This node's place in the fleet, as the settings panel shows it
    /// `[SPEC-DLY-050]`, `[SPEC-ECHO-020]`.
    pub echo_node: EchoNode,
    /// What is coming, in play order.
    pub queue: Vec<QueueEntry>,
    /// How many of those the mixer already holds, and so cannot be edited
    /// `[REQ-VIS-185]`. They sit at the front of `queue`.
    pub mixing_ahead: usize,
    /// Master volume, 0.0 to 1.0.
    pub volume: f32,
    /// Skip transition shape `[REQ-AUD-162]`, so a UI can show what it will do.
    pub skip_fade_ms: u64,
    pub skip_lead_ms: u64,
    pub resume_save_ms: u64,
    /// Skip suppression window in hours `[SPEC-PLAY-050]`.
    pub skip_suppress_h: u64,
    /// Queue-removal suppression window in hours `[SPEC-PLAY-055]`.
    pub dequeue_suppress_h: u64,
    /// Passages kept ahead, and how often a guest samples `[SPEC-MPD-105]`.
    pub queue_depth: usize,
    pub sample_interval_ms: u64,
    /// `[REQ-VIS-205]`
    pub cue_sheets: bool,
    /// `[REQ-VIS-210]`
    pub covers: bool,
    /// `[REQ-VIS-215]`
    pub lyrics_cache: bool,
    /// `[REQ-VIS-220]`
    pub lyrics_sidecar: bool,
    /// Reported to the browser so the interface can show it `[PI-SET-016]`.
    pub dev_mode: bool,
    pub active_streams: usize,
    pub underrun_samples: u64,
    /// The count **since the baseline**, which is what the interface shows
    /// `[REQ-VIS-230]`. `underrun_samples` stays cumulative for the life of
    /// the process, because that answers a different question and something
    /// should still be able to ask it.
    pub underruns_since_reset: u64,
    /// When that baseline was taken, as a unix time. The process start until
    /// somebody asks for a new one.
    pub underruns_since: i64,
    /// Times the callback could not take the output lock at all. Distinct from
    /// an underrun because the remedy differs: contention argues for a
    /// lock-free ring, starvation for more buffering. Surfaced rather than
    /// merely counted -- a diagnostic nobody can read is not one.
    pub lock_failures: u64,
    /// Output reopenings after a failure `[IMPL-AUD-020]`.
    pub out_recoveries: u64,
    /// Samples handed to the device but not yet played. Playback is NOT over
    /// while this is non-zero: the output ring holds ~14 s, so a short passage
    /// can be fully submitted before a single sample is audible.
    pub output_buffered: usize,
}

impl PlayerState {
    /// Nothing left to decode, queue, or play. The output check is what stops
    /// a caller exiting mid-passage and truncating the tail.
    pub fn is_idle(&self) -> bool {
        self.active_streams == 0 && self.queue_len == 0 && self.output_buffered == 0
    }
}

/// Playback has exactly **two** states, playing and paused. There is no
/// "stopped": pausing halts only the *consumer*, while decoders keep filling
/// their buffers, so resuming is instant and the pipeline stays primed after
/// the initial power-on fill.
/// Where a batch of passages goes `[REQ-VIS-195]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Placement {
    /// Front of the queue, then skip into it. The only one that interrupts.
    Now,
    /// After the current passage.
    Next,
    /// Behind everything already waiting.
    Last,
}

#[derive(Debug)]
pub enum Command {
    Play,
    Pause,
    /// Drop the playing passage and start the next immediately.
    Skip,
    /// Master volume, clamped to 0.0..=1.0.
    SetVolume(f32),
    /// Hold the output ring below capacity so this node's submit-to-air total
    /// matches the fleet's `[LOG-ECHO-030]`.
    ///
    /// Both figures are calibrated presentation offsets `[GDE-ECHO-430]`, in
    /// frames -- never a live `delay` reading, which on some nodes wanders
    /// milliseconds while the sound does not `[LOG-P4-100]`.
    SetEchoDepth { own_offset_frames: u64, fleet_min_offset_frames: u64 },
    /// This node's hand-set delay trim, ms, clamped to +/-2000
    /// `[SPEC-DLY-010]`.
    SetEchoDelayTrim(i64),
    /// The node to follow, as a bare host. Empty means independent
    /// `[SPEC-ECHO-010]`.
    SetEchoFollow(String),
    /// Join at once, or wait for the followed node's next passage
    /// `[SPEC-ECHO-030]`.
    SetEchoJoinNow(bool),
    /// What a join actually landed at, in ms, positive when it left this node
    /// behind `[GDE-ARC-061]`. Sent by the follower once, after each join it
    /// could measure cleanly.
    RecordJoinLanding(i64),
    /// The measured relative rate error against the node being followed, in
    /// ppm, positive when this node is falling behind `[GDE-ECHO-340]`.
    ///
    /// A *rate*, fitted over an hour, never an instantaneous position: the
    /// engine turns it into an interval and trims one frame each time it
    /// comes round. Zero stops trimming, which is what a node that stops
    /// following sends.
    SetEchoRate(f64),
    /// Replace what is coming with the node being followed `[GDE-ECHO-500]`.
    ///
    /// The whole upcoming queue, already resolved against this node's own
    /// library. Passages the mixer is already holding are untouched, because
    /// they are not in the queue -- they have been admitted.
    ///
    /// **This is what stops a follower skipping into every passage.** Without
    /// it the node keeps choosing its own next track and is yanked off it at
    /// each boundary, which cuts the ring and is audible; with it the node
    /// simply flows into the same passage the master does, and alignment is
    /// left to the overlap `[GDE-ECHO-340]`.
    EchoSetQueue(Vec<QueueEntry>),
    /// Shed this many ms of position by trimming frames `[GDE-ECHO-349]`.
    ///
    /// For the endgame only: an error smaller than the mix quantum, which no
    /// passage-boundary knob can reach. Paid off one frame at a time, at a
    /// bounded rate, superimposed on the rate trim they share an actuator
    /// with.
    EchoShedOffset(i64),
    /// Start the next passage this many ms earlier, negative for later, to
    /// shed an offset `[GDE-ECHO-340]`.
    ///
    /// Spends the transition's overlap rather than the passage's content:
    /// nothing is skipped or repeated, and it works in both directions
    /// `[should_admit_nudged]`. Applied once, to the next admission.
    EchoCorrectNextStart(i64),
    /// Begin this passage, this far in, at this wall-clock instant
    /// `[GDE-ECHO-330]`.
    ///
    /// The caller resolves the master's passage id against the local library
    /// and hands over a whole `QueueEntry`, exactly as `PlayNow` does: the
    /// engine owns no library and must not learn to look one up.
    EchoStartAt { entry: QueueEntry, start_sample: u64, at_nanos: u64 },
    /// How long a skip fades the outgoing passage out, in ms `[REQ-AUD-158]`.
    SetSkipFade(u64),
    /// How long after a skip the next passage starts, in ms `[REQ-AUD-162]`.
    SetSkipLead(u64),
    /// How often the resume point is written, in ms `[REQ-VIS-155]`.
    SetResumeSave(u64),
    /// How long a skipped passage stays out of selection, in hours
    /// `[SPEC-PLAY-050]`. Zero turns suppression off.
    SetSkipSuppress(u64),
    /// The same for a passage removed from the queue unheard
    /// `[SPEC-PLAY-055]`.
    SetDequeueSuppress(u64),
    /// How many passages to keep queued ahead `[SPEC-MPD-105]`.
    SetQueueDepth(usize),
    /// Whether Lempi may write cue sheets into the music folder
    /// `[REQ-VIS-205]`. Turning it on is what asks for them to be written.
    SetCueSheets(bool),
    /// Whether Lempi may write cover art into the music folder `[REQ-VIS-210]`.
    SetCovers(bool),
    /// Whether Lempi may write per-song lyrics into a local client's cache
    /// `[REQ-VIS-215]`. Turning it on is what asks for them to be written.
    SetLyricsCache(bool),
    /// Whether Lempi may write lyrics beside the audio `[REQ-VIS-220]`.
    SetLyricsSidecar(bool),
    /// How often a guest backend samples `status`, in ms `[SPEC-MPD-105]`.
    SetSampleInterval(u64),
    /// Start the underrun count again from here `[REQ-VIS-230]`.
    RestartUnderruns,
    Enqueue(QueueEntry),
    /// Put a passage next rather than last, for a browsed choice
    /// `[REQ-VIS-180]`.
    EnqueueNext(QueueEntry),
    /// Play a passage at once: to the front of the queue, then skip into it.
    PlayNow(QueueEntry),
    /// Several passages at once, in the order given `[REQ-VIS-195]`.
    EnqueueMany(Vec<QueueEntry>, Placement),
    /// Drop a queued passage `[REQ-VIS-185]`.
    RemoveQueued(u64),
    /// Move a queued passage earlier (negative) or later (positive).
    ShiftQueued(u64, isize),
    /// Rebuild the output stream against the current default sink
    /// `[PI3-API-010]`.
    ///
    /// Needed because the ALSA bridge binds a stream to whichever node was
    /// default when it opened: changing the default afterwards does not move an
    /// existing stream, so choosing a speaker in the settings panel is
    /// cosmetic -- it looks like it worked, and is silent -- unless the output
    /// is reopened `[PI3-WHY-020]`.
    ReopenOutput,
    /// Write the resume point NOW, ignoring the save interval `[REQ-VIS-155]`.
    ///
    /// For the moments the interval was not designed for: the machine is about
    /// to be powered off deliberately `[PI5-PWR-010]`, and losing the last few
    /// seconds of position to a timer would be a shame in exactly the case a
    /// person took care over.
    Persist,
    /// Terminate the process. Deliberately NOT a playback state -- it ends the
    /// engine rather than putting playback into a third mode.
    Shutdown,
}

/// The control surface, safe to share across threads.
///
/// `tx` is behind a mutex so the handle is `Sync` and can sit in an `Arc` that
/// every web request holds. An `mpsc::Sender` is `Send` but not `Sync`, and
/// commands arrive at human rates, so the lock is never contended.
pub struct EngineHandle {
    tx: Mutex<Sender<Command>>,
    pub state: Arc<Mutex<PlayerState>>,
}

impl EngineHandle {
    pub fn send(&self, c: Command) {
        if let Ok(tx) = self.tx.lock() {
            let _ = tx.send(c);
        }
    }
    pub fn snapshot(&self) -> PlayerState {
        self.state.lock().map(|s| s.clone()).unwrap_or_default()
    }
}

pub struct Engine {
    queue: Queue,
    live: Vec<Live>,
    /// The next passage, opened and decoded ahead of need `[REQ-AUD-160]`.
    ///
    /// Held OUTSIDE `live` because `live` is what the mixer sums: a passage in
    /// there is sounding. This one is merely ready, and `top_up_decoders` keeps
    /// it fed so promoting it costs nothing but the move.
    ready: Option<Live>,
    /// Room in the output ring as of the last submit. See `mix_and_submit`.
    out_room: usize,
    /// When the shared snapshot may next be written. See `publish`.
    publish_at: Option<std::time::Instant>,
    /// When the frame clock may next be logged. Separate from `publish_at`
    /// because the two answer different questions at different rates: one
    /// serves a browser, the other serves a measurement `[GDE-ECHO-280]`.
    clock_log_at: Option<std::time::Instant>,
    /// Whether the frame clock may still be compared against an echo anchor,
    /// and the counters that decide it `[GDE-ECHO-360]`.
    ///
    /// Derived from counters rather than hooked into each event, deliberately:
    /// a device reopen, an underrun and a pause are raised in three different
    /// places and a fourth could be added without anyone remembering this.
    /// Watching the numbers move catches every path, including ones not
    /// anticipated here.
    echo_basis: crate::echo::Basis,
    /// The most recent forward schedule, republished until superseded. Every
    /// message is absolute and idempotent `[GDE-ECHO-320]`, so repeating one
    /// costs nothing and a node that missed the first simply uses this.
    echo_schedule: Option<crate::echo::Schedule>,
    /// Samples of the output ring this node must leave EMPTY `[LOG-ECHO-030]`.
    ///
    /// Zero means "fill to capacity", which is what a node with the fleet's
    /// shortest device delay does. Every other node runs shallower by its own
    /// excess over that minimum, so that `depth + device_delay` comes to the
    /// same total everywhere and one sample sounds at one instant across the
    /// fleet. `bose` at 46.3 ms behind `lempiplay3`'s 42.2 ms leaves 181
    /// frames `[LOG-P4-140]`.
    echo_depth_shortfall: usize,
    /// This node's hand-set delay trim, ms `[SPEC-DLY-010]`.
    pub(crate) echo_delay_trim_ms: i64,
    /// The node this one follows, bare host; empty is independent
    /// `[SPEC-ECHO-010]`.
    pub(crate) echo_follow_host: String,
    /// Join at once, or wait for the followed node's next passage
    /// `[SPEC-ECHO-030]`.
    pub(crate) echo_join_now: bool,
    /// The fitted relative rate error, ppm `[GDE-ECHO-340]`.
    pub(crate) echo_rate_ppm: f64,
    /// How long a commanded start takes to become audible, ms
    /// `[GDE-ECHO-342]`.
    ///
    /// Measured, not assumed. The lead a skip applies is a constant, but the
    /// work before it -- opening the file, seeking, building the resampler,
    /// and topping the decoder up so the overlay is not silence
    /// `[PI-CHR-075]` -- takes as long as the card and the passage make it.
    /// That time lands directly on the air, so a start fired early by a
    /// constant is late by however long the work took: ~900 ms on this fleet,
    /// which was the whole of the lag a listener could hear.
    ///
    /// Smoothed, because the next join's prep is better predicted by the last
    /// few than by any constant, and the first one has to guess something.
    pub(crate) echo_prep_ms: u64,
    /// Position still to shed by trimming, in frames `[GDE-ECHO-349]`.
    ///
    /// Positive when this node is late and must advance. Counted in frames
    /// rather than milliseconds because that is the resolution the actuator
    /// actually has -- 23 us -- and rounding to a millisecond here would
    /// throw away forty times the precision the endgame exists to reach.
    pub(crate) echo_debt_frames: i64,
    /// When a frame was last trimmed. Monotonic, because a wall clock can
    /// step `[GDE-ECHO-365]` and this is an interval.
    echo_last_trim: Option<std::time::Instant>,
    /// Whether the last trim the mixer tried was refused, so a refusal is
    /// reported once rather than twenty times a second `[GDE-ECHO-378]`.
    echo_trim_refused: bool,
    /// Frames of silence still owed before the next passage's audio
    /// `[GDE-ARC-052]`.
    ///
    /// **The actuator a follower running ahead never had.** The ring is
    /// contiguous, so declining to submit does not delay anything — it only
    /// makes the buffer shallower, and the audio airs at the same instant
    /// either way `[a_pause_in_writing_leaves_no_gap_in_the_audio]`. Moving
    /// sound later means putting something in front of it, and silence is
    /// the only thing that can go there without inventing content.
    ///
    /// Spent by the mixer a block at a time. While it is outstanding the
    /// live streams are not mixed at all, so their decoded audio waits in
    /// their own rings and the passage lands whole, only later.
    /// The instant a commanded start's sample 0 must **sound**, held from
    /// `fire_echo_start` until the ring is cut `[GDE-ARC-058]`.
    ///
    /// Carried rather than converted to a depth at the point it is learned,
    /// because the depth is only correct if it is computed after the
    /// preparation -- which is the entire difference between this and the
    /// `echo_prep_ms` guess it replaces `[GDE-ECHO-342]`.
    echo_join_at: Option<u64>,
    /// What this node's past joins landed at `[GDE-ARC-061]`.
    pub(crate) echo_join_bias: crate::echo::JoinBias,
    /// Whether `shown` was promoted by `advance_shown`'s own audibility test
    /// rather than set eagerly by a cut `[GDE-ARC-059]`.
    shown_is_sounding: bool,
    pub(crate) echo_gap_frames: u64,
    /// An offset correction waiting for the next admission, ms earlier
    /// `[GDE-ECHO-340]`. Zero is no correction, which is also the resting
    /// state of a node that is already level.
    pub(crate) echo_next_shift_ms: i64,
    /// A start instant committed to but not yet reached `[GDE-ECHO-330]`.
    echo_start: Option<(QueueEntry, u64, u64)>,
    echo_seen_recoveries: u64,
    echo_seen_underruns: u64,
    /// The audible passage as last published, so a change can bypass the clock.
    published: Option<i64>,
    /// Set when a command rearranged the queue, so that too can bypass the
    /// clock. A listener who removes a passage is waiting on the answer; the
    /// throttle exists for the position counter ticking along, and making an
    /// edit wait behind it is what made the controls feel slow to obey.
    queue_edited: bool,
    /// Misses already reported, so each is logged once.
    last_lock_failures: u64,
    /// The audio path, held at arm's length `[SPEC-APS-070]`.
    ///
    /// A ring to write into and a channel to ask things of -- and deliberately
    /// nothing that can open a device, wait for one, or ask the system about
    /// one. The device's whole lifecycle belongs to the supervisor, on its own
    /// thread, because every time this loop was allowed to do that work it
    /// eventually did some of it blocking `[GDE-FBD-090]`.
    path: crate::path::PathHandle,
    out_rate: u32,
    out_channels: usize,
    scratch: Vec<f32>,
    state: Arc<Mutex<PlayerState>>,
    rx: Receiver<Command>,
    playing: bool,
    shutdown: bool,
    volume: f32,
    /// Skip transition shape, adjustable while playing `[REQ-AUD-162]`.
    skip_fade_ms: u64,
    skip_lead_ms: u64,
    store: Option<PlayerStore>,
    /// One-shot: the offset the NEXT admitted passage opens at. Consumed on
    /// use, so only the resumed passage is seeked and everything after it
    /// starts where the library says it starts.
    pending_resume: Option<u64>,
    last_save: Instant,
    /// How often the resume point is written `[REQ-VIS-155]`. Configurable
    /// because every one of these writes lands on the appliance's most
    /// volatile partition `[PI-C-010]`.
    resume_save_ms: u64,
    /// How long a skipped passage stays out of selection `[SPEC-PLAY-050]`.
    skip_suppress_h: u64,
    /// How long a passage removed from the queue unheard stays out
    /// `[SPEC-PLAY-055]`.
    dequeue_suppress_h: u64,
    /// How often a guest backend should sample `status` `[SPEC-MPD-105]`. The
    /// local engine does not poll; it holds the value so one row owns every
    /// listener setting and the settings page has one place to read.
    sample_interval_ms: u64,
    /// Whether Lempi may write cue sheets into the music folder
    /// `[REQ-VIS-205]`. Held here for the same reason: one row, one page.
    cue_sheets: bool,
    /// `[REQ-VIS-210]`
    covers: bool,
    /// `[REQ-VIS-215]`
    lyrics_cache: bool,
    /// `[REQ-VIS-220]`
    lyrics_sidecar: bool,
    /// Set only while a handoff's fade is running, so the departing head is
    /// not written down as a rejection `[SPEC-BK-065]`.
    handing_over: bool,
    /// The passage that has finished mixing but is still being heard: which
    /// one, where it had got to, and when that was `[REQ-VIS-240]`.
    ///
    /// A passage leaves `live` when its decoder is exhausted, which is up to a
    /// ring's depth before its last sample reaches the speaker. Without this
    /// the displayed position simply stopped there — fifteen seconds short of
    /// the end, every passage.
    ///
    /// **Advanced by the clock, not by the ring.** The obvious measure — what
    /// it had mixed, less what is still buffered — is wrong here: during a
    /// crossfade the incoming passage is filling that same ring, so its depth
    /// says nothing about how much of the outgoing one is left. What is left
    /// is simply time, and audio is played at one second per second.
    draining: Option<(i64, u64, Instant)>,

    /// A passage arriving mid-play whose play another backend has already
    /// recorded. Judged as recorded the moment it becomes the head, so it
    /// earns neither a second play nor a rejection `[SPEC-BK-065]`.
    counted_elsewhere: Option<i64>,

    /// How much of the sounding passage has actually been **heard**
    /// `[SPEC-PLAY-012]`.
    ///
    /// Not its position. While playback only ever ran forwards the two were
    /// the same number and the position was used directly; a seek separates
    /// them, and using position would let a drag to the last chorus earn a
    /// play nobody listened to.
    heard_ms: u64,
    /// The position at the previous sample, or `None` when there is no
    /// previous position to measure from — a new passage, or the far side of
    /// a seek. Time is credited from the gap between samples, so a `None`
    /// here is what makes a jump cost nothing.
    heard_from: Option<u64>,
    saved: Option<(i64, bool)>,
    /// The last passage written to play history, so a passage is recorded
    /// once however many ticks it sounds for.
    /// Whether the passage at `head` has been written to history yet.
    recorded: bool,
    /// The passage currently at the head of `live`, so `recorded` can reset.
    head: Option<i64>,
    /// Its MBID, kept because a skip is written *after* the passage has gone
    /// and the entry it came from is no longer reachable `[SPEC-PLAY-050]`.
    head_mbid: Option<String>,
    /// The head's passage span, kept for the same reason as `head_mbid`: a
    /// skip's percentage-played is written after the passage has gone, and by
    /// then `live` no longer holds it `[REQ-VIS-250]`.
    head_span_ms: u64,
    /// The row `record_play` wrote for the head, if any -- so the eventual
    /// departure can go back and fill in how much was actually heard, rather
    /// than freezing the figure at the instant the play was earned
    /// `[REQ-VIS-250]`. `None` once there is nothing left to finish: cleared
    /// after use and whenever the head changes.
    pending_play_id: Option<i64>,
    /// A play whose passage exhausted naturally and is still draining
    /// through the ring, waiting for the clock to say how much of the tail
    /// was actually heard `[REQ-VIS-250]`. At most one at a time, the same
    /// simplification `draining` makes for the display.
    pending_finish: Option<PendingFinish>,
    /// Passages chosen but never opened, waiting to be reported `[REQ-PD-112]`.
    ///
    /// The engine drops them; only the Director can undo having counted them,
    /// and it lives on the other side of `Session`. Kept here until asked for.
    dropped: Vec<i64>,
    /// The passage the LISTENER is on, which is not the one being mixed
    /// `[REQ-AUD-164]`. Held here because it outlives `live`: a passage stays
    /// audible for a ring's depth after the mixer has finished with it.
    shown: Option<(QueueEntry, u64)>,
    /// Underruns that happened while PLAYING. Under the two-state model the
    /// device callback drains continuously, so a paused player underruns
    /// forever -- counting those would bury the fault this number exists to
    /// expose [REQ-AUD-142].
    underruns_playing: u64,
    /// What `underruns_playing` read when the count was last restarted, and
    /// when that was `[REQ-VIS-230]`.
    ///
    /// **In memory, never persisted.** The cumulative counter starts at zero
    /// in every process, so a baseline restored from a previous one would be
    /// subtracting a number that no longer exists.
    underrun_baseline: u64,
    underrun_since: i64,
    last_raw_underruns: u64,
}

/// Seconds since the epoch, for stamping when a count was restarted.
fn unix_now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}


mod persist;

impl Engine {
    /// Least worth mixing in one pass, in **frames** `[GDE-FBD-090]`.
    ///
    /// ~46 ms at 44.1 kHz, against a ring holding ~14 s. Sized to be large
    /// enough that the pass earns its lock acquisition and small enough that
    /// it is a rounding error against the buffer it feeds.
    ///
    /// **In frames, not samples, and that is the fix for `[GDE-ECHO-373]`.**
    /// It used to be 4096 samples while `scratch` was `2048 * channels` --
    /// the same number on a stereo device, so every pass handed `apply_trim`
    /// a block exactly as long as its own buffer and every duplicate was
    /// refused for want of one frame. Both readers of this figure are about
    /// *time* -- how much audio is worth one lock acquisition, and the
    /// quantum admission timing can shift by `[GDE-ECHO-347]` -- and a figure
    /// in samples means a different length of audio on every channel count.
    /// In frames it is 46 ms whatever the device is, and `scratch` is sized
    /// from it rather than beside it.
    const MIX_FRAMES: usize = 2048;

    /// The mix block in samples, for the channel count this engine is running
    /// at. Always strictly shorter than `scratch`, by the one frame
    /// `apply_trim` needs to put a duplicate in `[GDE-ECHO-373]`.
    fn min_submit(&self) -> usize {
        Self::MIX_FRAMES * self.out_channels.max(1)
    }

    /// How often the shared snapshot is rewritten.
    ///
    /// Browsers are pushed to every 500 ms (`web::PUSH_EVERY`), so publishing
    /// on every tick rewrote the state some two hundred times for each read of
    /// it -- and took the output lock to do so. A tenth of a second is well
    /// inside what any consumer can perceive and two orders of magnitude less
    /// work.
    /// How often the frame clock is written to the log.
    ///
    /// Not a display rate: this exists so a drift figure can be taken from two
    /// lines far apart, and so a power cut costs one interval rather than the
    /// whole window. Five minutes matches the sampler already reading
    /// `/proc/asound` on `bose`, which is the independent instrument this has
    /// to be checked against `[LOG-DRIFT-010]`.
    pub(crate) const CLOCK_LOG_EVERY: std::time::Duration =
        std::time::Duration::from_secs(300);

    pub(crate) const PUBLISH_EVERY: std::time::Duration =
        std::time::Duration::from_millis(100);

    /// `out` of `None` runs the full pipeline into a discard sink — useful for
    /// tests and headless hosts, but note it reports no device rate and so
    /// cannot catch a resampling fault `[REQ-HW-147]`.
    pub fn new(path: crate::path::PathHandle, min_depth: usize) -> (Self, EngineHandle) {
        let (tx, rx) = channel();
        let state = Arc::new(Mutex::new(PlayerState::default()));
        let out_rate = path.sample_rate();
        let out_channels = path.channels();
        let engine = Self {
            queue: Queue::new(min_depth),
            live: Vec::new(),
            ready: None,
            path,
            out_room: 0,
            publish_at: None,
            clock_log_at: None,
            echo_basis: crate::echo::Basis::default(),
            echo_schedule: None,
            echo_depth_shortfall: 0,
            echo_delay_trim_ms: 0,
            echo_follow_host: String::new(),
            echo_join_now: true,
            echo_rate_ppm: 0.0,
            echo_debt_frames: 0,
            echo_prep_ms: Self::ECHO_PREP_GUESS_MS,
            echo_last_trim: None,
            echo_trim_refused: false,
            echo_join_at: None,
            echo_join_bias: crate::echo::JoinBias::default(),
            shown_is_sounding: false,
            echo_gap_frames: 0,
            echo_next_shift_ms: 0,
            echo_start: None,
            echo_seen_recoveries: 0,
            echo_seen_underruns: 0,
            published: None,
            queue_edited: false,
            last_lock_failures: 0,
            out_rate,
            out_channels,
            // One frame longer than the block that is ever mixed into it, so
            // a duplicate always has somewhere to go `[GDE-ECHO-373]`.
            scratch: vec![0.0; (Self::MIX_FRAMES + 1) * out_channels.max(1)],
            state: Arc::clone(&state),
            rx,
            playing: false,
            shutdown: false,
            volume: 1.0,
            skip_fade_ms: crate::SKIP_FADE_MS,
            skip_lead_ms: crate::SKIP_LEAD_MS,
            store: None,
            pending_resume: None,
            last_save: Instant::now(),
            resume_save_ms: crate::RESUME_SAVE_MS,
            skip_suppress_h: crate::SKIP_SUPPRESS_H,
            dequeue_suppress_h: crate::DEQUEUE_SUPPRESS_H,
            sample_interval_ms: crate::SAMPLE_INTERVAL_MS,
            cue_sheets: false,
            covers: false,
            lyrics_cache: false,
            lyrics_sidecar: false,
            handing_over: false,
            counted_elsewhere: None,
            draining: None,
            heard_ms: 0,
            heard_from: None,
            saved: None,
            recorded: false,
            head: None,
            head_mbid: None,
            head_span_ms: 0,
            pending_play_id: None,
            pending_finish: None,
            shown: None,
            dropped: Vec::new(),
            underruns_playing: 0,
            underrun_baseline: 0,
            // Seeded now, so a fresh player reads "since 09:14" rather than
            // "since never" -- and that is the honest label for the count it
            // is already showing.
            underrun_since: unix_now(),
            last_raw_underruns: 0,
        };
        (engine, EngineHandle { tx: Mutex::new(tx), state })
    }

    /// Persist playback state to `lempi.db` `[REQ-AUD-140]`. Optional: without
    /// it the engine runs identically and simply forgets across restarts.
    pub fn attach_store(&mut self, store: PlayerStore) {
        self.store = Some(store);
    }

    /// Open the next admitted passage `position_ms` in, for resuming.
    pub fn resume_at(&mut self, position_ms: u64) {
        self.pending_resume = Some(position_ms);
    }

    pub fn enqueue(&mut self, e: QueueEntry) {
        self.queue.push(e);
    }
    /// What is queued ahead, in play order. The engine's queue is the only
    /// answer to "what plays next"; a caller that drew its own preview from
    /// the library would be describing a different evening.
    pub fn queued(&self) -> impl Iterator<Item = &QueueEntry> {
        self.queue.iter()
    }
    pub fn shortfall(&self) -> usize {
        self.queue.shortfall()
    }
    /// The engine has been told to terminate. Distinct from paused, which is a
    /// playback state and leaves the pipeline running.
    pub fn is_shutdown(&self) -> bool {
        self.shutdown
    }

    /// One pump iteration: commands, admission, decode, mix, submit, publish.
    ///
    /// Everything here is a load, a lock the callback only ever *tries* for, or
    /// a channel send. Nothing opens a device, sleeps, or asks the system a
    /// question -- those belong to the supervisor `[SPEC-APS-070]`, and the
    /// engine no longer holds anything that could do them `[GDE-FBD-090]`.
    pub fn tick(&mut self) -> usize {
        self.drain_commands();
        if self.shutdown {
            return 0;
        }
        self.fire_echo_start();
        self.admit_due();
        // Prepare AFTER admitting, so this readies the passage that is next
        // once the admission has moved the queue on.
        self.prepare_next();
        // Producers run in BOTH states. Pausing stops the consumer only, so
        // buffers stay full and resuming does not re-incur a fill.
        self.top_up_decoders();
        // Submitting while paused would be audible -- the callback drains
        // continuously -- so only the consumer side is gated.
        // Nothing audible means nothing advances. Mixing on into a failed or
        // dummy-bound output consumed the queue at whatever speed the decoders
        // managed, so the clock raced while the room stayed silent -- a player
        // that lies about what it is doing, which is the fault this whole
        // effort exists to remove `[PI3-API-030]`.
        let audible = self.path.audible();
        let submitted = if self.playing && audible { self.mix_and_submit() } else { 0 };
        self.retire_finished();
        self.record_play();
        // Independent of `self.playing`, the same as `advance_shown` below:
        // the ring drains at the device's own pace regardless of pause
        // `[REQ-VIS-250]`.
        self.finalize_draining_plays();
        self.advance_shown();
        // Throttled by time, but never at the cost of a late answer to the
        // question anyone actually asks: a change of audible passage is
        // published the moment it happens, and the clock only governs the
        // position ticking along in between.
        let now = std::time::Instant::now();
        let changed = self.shown.as_ref().map(|(e, _)| e.passage_id) != self.published;
        if changed || self.queue_edited || self.publish_at.is_none_or(|t| now >= t) {
            self.queue_edited = false;
            self.publish_at = Some(now + Self::PUBLISH_EVERY);
            self.publish();
        }
        self.update_echo_basis();
        self.log_clock(now);
        self.persist(false);
        submitted
    }

    /// Void the echo basis if anything has happened that the frame clock
    /// cannot be compared across `[GDE-ECHO-360]`.
    ///
    /// Only a clean passage boundary re-establishes it, which is what makes a
    /// disturbance force a rejoin rather than a silent continuation on stale
    /// state. Each cause is recorded rather than collapsed into "invalid", so
    /// a node that stops correcting can say why.
    fn update_echo_basis(&mut self) {
        use crate::echo::Voided;
        let r = self.path.recoveries();
        if r != self.echo_seen_recoveries {
            self.echo_seen_recoveries = r;
            self.echo_basis.void(Voided::DeviceReopen);
        }
        if let Some(ring) = self.path.ring.as_ref() {
            let u = ring.counts.underruns();
            if u != self.echo_seen_underruns {
                self.echo_seen_underruns = u;
                self.echo_basis.void(Voided::Underrun);
            }
        }
        if !self.playing {
            self.echo_basis.void(Voided::Pause);
        }
    }

    /// Where the current passage is **in the air**, for an echo anchor.
    ///
    /// `audible_ms` stops at the ring; this goes on to subtract the device's
    /// own delay, which is what separates "left for the device" from "was
    /// heard" -- 46 ms on `bose` and 355 on `lempi02w` `[LOG-CPAL-060]`.
    /// `audible_ms` is left alone on purpose: it drives the display and the
    /// resume point, and shifting those to serve echo would be backwards.
    ///
    /// `None` while the delay is unknown. A node whose timestamps are not
    /// `Hardware` cannot place itself in time, and guessing zero would put it
    /// a third of a second out while looking exactly like a measurement
    /// `[GOV-SRC-040]`.
    pub(crate) fn air_position(&self) -> Option<crate::echo::AirPosition> {
        let r = self.path.ring.as_ref()?;
        if r.clock.timestamps() != crate::output::Timestamps::Hardware {
            return None;
        }
        // `shown`, not `live.first()`. They differ for a whole ring's depth
        // after every admission, and during that window `live.first()` is a
        // passage nobody can hear yet -- `[REQ-AUD-164]` is explicit that a
        // passage becomes current when its first sample LEAVES the ring.
        //
        // Observed on `bose` before this was fixed: an anchor naming passage
        // 7830 at position 0, taken fourteen seconds before that passage's own
        // schedule said it would sound. An echo node would have declined to
        // act on it -- the follower refuses to compare across passages -- so
        // the fault was safe, silent, and wrong.
        //
        // `shown` also outlives `live`, which is the other half of being
        // right: a passage stays audible for a ring's depth after the mixer
        // has finished with it.
        //
        // **But not while it is only a promise** `[GDE-ARC-059]`. A cut sets
        // `shown` to the incoming passage at once so the button stays honest
        // `[REQ-AUD-164]`, and for the whole lead after that cut -- a computed
        // 886 ms on one measured join -- the old audio is still sounding.
        // Answering here in that window reports a position this node has not
        // reached, publishes it as an anchor, and feeds it to the residual
        // filter a join has just cleared. There is no honest answer to give
        // until the passage is really sounding, and `None` is how this
        // function says so everywhere else `[GOV-SRC-040]`.
        if !self.shown_is_sounding {
            return None;
        }
        let (entry, audible_ms) = self.shown.as_ref()?;
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .ok()?
            .as_nanos() as u64;
        // **The node's own offset, not the raw reading** `[GDE-ARC-065]`.
        // `[SPEC-DLY-010]` is explicit that there is one notion of this
        // node's delay -- "not a second notion of delay layered on the first:
        // `presentation_offset` is measured delay + calibrated residual" --
        // and this passed the measured half alone while every scheduling path
        // used the sum `[GDE-ARC-033]`.
        //
        // Latent while the trim is zero, which it is on every node today, and
        // that is exactly why it survived: the two agreed. It bites the first
        // time somebody calibrates a node by ear, which is the entire purpose
        // of that control -- the node would then schedule against a corrected
        // delay while telling every follower a position computed from the
        // uncorrected one.
        let measured = (r.clock.timestamps() == crate::output::Timestamps::Hardware)
            .then(|| r.clock.delay_frames());
        let (offset_frames, _) = self.echo_offset_frames(measured);
        Some(crate::echo::air_position(
            entry.passage_id,
            // Already ring-subtracted, so there is no ring left to remove --
            // only the device's own delay stands between this and the air.
            *audible_ms,
            0,
            offset_frames,
            r.sample_rate(),
            now,
        ))
    }

    /// Write the frame clock out, periodically.
    ///
    /// Deliberately a log line and not a snapshot field: the snapshot is
    /// pushed to every browser twice a second and this is read twice a day.
    /// The pair `frames`/`at_nanos` is emitted together because a rate is
    /// regressed from both and either alone says nothing; `delay` is the
    /// measured half of this node's presentation offset `[GDE-ECHO-430]`; and
    /// `ts` reports whether any of it rests on real hardware timestamps or on
    /// cpal's silent software substitute `[GDE-ECHO-290]`, which is the one
    /// thing a reader cannot otherwise tell.
    fn log_clock(&mut self, now: std::time::Instant) {
        if self.clock_log_at.is_some_and(|t| now < t) {
            return;
        }
        // Read everything out before arming the timer, so the borrow ends.
        let Some(snap) = self.path.ring.as_ref().map(|r| {
            let (frames, at_nanos) = r.clock.sample();
            (frames, at_nanos, r.clock.delay_frames(), r.sample_rate(),
             r.clock.timestamps(), r.clock.callbacks())
        }) else {
            return;
        };
        // Nothing has left the device yet -- a stream that has just opened, or
        // one that never will. Saying so beats logging a zero that reads like
        // a measurement `[GOV-SRC-040]`.
        //
        // The timer is armed only once there is something to say. It used to
        // be armed first, so a player whose clock was still empty on the very
        // first tick went quiet for a full five minutes and looked identical
        // to one with nothing to report.
        if snap.0 == 0 {
            return;
        }
        self.clock_log_at = Some(now + Self::CLOCK_LOG_EVERY);
        tracing::info!(
            "clock: frames={} at_nanos={} delay={} rate={} ts={:?} callbacks={}",
            snap.0, snap.1, snap.2, snap.3, snap.4, snap.5
        );
        // The backward anchor `[GDE-ECHO-310]`, at the same cadence for now.
        // Phase 3 raises this to twice a second on the snapshot's own
        // WebSocket; logging it first makes the arithmetic observable on a
        // real node before anything depends on it being right.
        match (self.echo_basis.is_valid(), self.air_position()) {
            (true, Some(a)) => tracing::info!(
                "echo-anchor: passage={} position_ms={} at_nanos={}",
                a.passage_id, a.position_ms, a.at),
            // Saying why nothing was emitted beats emitting nothing, which
            // reads the same as a node that is simply quiet `[GOV-SRC-040]`.
            (false, _) => tracing::info!(
                "echo-anchor: withheld, basis voided by {:?} until the next passage",
                self.echo_basis.voided_by()),
            (true, None) => {}
        }
    }

    fn drain_commands(&mut self) {
        loop {
            match self.rx.try_recv() {
                Ok(Command::Play) => self.set_playing(true),
                Ok(Command::Pause) => self.set_playing(false),
                Ok(Command::ReopenOutput) => self.path.reopen(),
                Ok(Command::Skip) => self.skip(),
                Ok(Command::SetEchoDelayTrim(ms)) => {
                    self.echo_delay_trim_ms =
                        ms.clamp(-crate::db::ECHO_TRIM_LIMIT_MS, crate::db::ECHO_TRIM_LIMIT_MS);
                    self.remember_settings();
                }
                Ok(Command::SetEchoFollow(host)) => {
                    // Trimmed, and a node refuses to follow itself
                    // `[SPEC-ECHO-050]`: its own address makes a socket to its
                    // own snapshot and a join triggered by its own admission,
                    // which skips forever and is baffling to watch.
                    let host = host.trim().to_string();
                    if !host.is_empty() && is_self(&host) {
                        tracing::warn!("echo-follow: {host} is this node; refusing to follow itself");
                    } else {
                        // **A new master means relearning** `[GDE-ARC-061]`.
                        // The bias mixes this node's own pipeline with
                        // whatever its master's reported position is worth,
                        // and only the first half travels. Swapping master is
                        // rare, so a few joins of relearning costs less than
                        // carrying a number that is quietly about somebody
                        // else. Re-setting the SAME host is not a change and
                        // keeps the history -- a reconnect must not wipe it.
                        if host != self.echo_follow_host && !self.echo_join_bias.is_empty() {
                            tracing::info!("echo-join: now following {host}; forgetting {} landing(s) learned against {}",
                                      self.echo_join_bias.len(),
                                      if self.echo_follow_host.is_empty() { "nobody" } else { &self.echo_follow_host });
                            self.echo_join_bias = crate::echo::JoinBias::default();
                        }
                        self.echo_follow_host = host;
                        self.remember_settings();
                    }
                }
                Ok(Command::SetEchoRate(ppm)) => {
                    let want = if ppm.is_finite() { ppm } else { 0.0 };
                    if want.abs() > Self::ECHO_RATE_CEILING_PPM {
                        tracing::warn!("echo-rate: {want:+.1} ppm is not a crystal; clamping to {:+.0} and carrying on", Self::ECHO_RATE_CEILING_PPM.copysign(want));
                    }
                    self.echo_rate_ppm =
                        want.clamp(-Self::ECHO_RATE_CEILING_PPM, Self::ECHO_RATE_CEILING_PPM);
                    // Starting or stopping the clock, never resetting it
                    // mid-run: a rate that is merely refined should not push
                    // the next trim back by a whole interval each time.
                    if self.echo_rate_ppm == 0.0 && self.echo_debt_frames == 0 {
                        self.echo_last_trim = None;
                    } else if self.echo_last_trim.is_none() {
                        self.echo_last_trim = Some(std::time::Instant::now());
                    }
                }
                Ok(Command::EchoSetQueue(entries)) => {
                    self.queue.replace_upcoming(entries);
                    self.queue_edited = true;
                }
                Ok(Command::EchoShedOffset(ms)) => {
                    let rate = self.out_rate.max(1) as i64;
                    self.echo_debt_frames = ms.saturating_mul(rate) / 1000;
                    // The trim clock runs for a debt as well as for a rate.
                    if self.echo_debt_frames != 0 && self.echo_last_trim.is_none() {
                        self.echo_last_trim = Some(std::time::Instant::now());
                    }
                    tracing::info!("echo-offset: shedding {ms} ms by trimming ({} frames)",
                              self.echo_debt_frames);
                }
                Ok(Command::EchoCorrectNextStart(ms)) => {
                    self.echo_next_shift_ms = ms;
                    // **A new correction is a new plan, and it supersedes the
                    // outstanding debt** `[GDE-ARC-041]`. The follower
                    // measures once per master passage and sends the pair --
                    // this shift for the boundary, then whatever the boundary
                    // will not take for the trim. Without clearing here, the
                    // remainder `admit_due` adds when a boundary under-delivers
                    // would accumulate across passages into a debt nobody
                    // measured. The follower's own `EchoShedOffset` follows
                    // this command and sets the new base.
                    self.echo_debt_frames = 0;
                }
                Ok(Command::RecordJoinLanding(ms)) => {
                    // **Persisted immediately, not at the next settings
                    // write** `[GDE-ARC-061]`. Joins are rare on a quiet
                    // follower -- three in seven hours was typical -- so a
                    // sample lost to a restart can be a day's learning, and
                    // that is exactly how `echo_prep_ms` never converged
                    // `[GDE-ARC-058]`.
                    let was = self.echo_join_bias.correction_ms();
                    if self.echo_join_bias.record(ms) {
                        let now = self.echo_join_bias.correction_ms();
                        tracing::info!("echo-join: landed {ms:+} ms; {} sample(s), aiming {now:+} ms further on from now (was {was:+})",
                                  self.echo_join_bias.len());
                        self.remember_settings();
                    } else {
                        tracing::warn!("echo-join: landed {ms:+} ms, which is past the credible range; not learned from");
                    }
                }
                Ok(Command::SetEchoJoinNow(now)) => {
                    self.echo_join_now = now;
                    self.remember_settings();
                }
                Ok(Command::EchoStartAt { entry, start_sample, at_nanos }) => {
                    self.echo_start = Some((entry, start_sample, at_nanos));
                }
                Ok(Command::SetEchoDepth { own_offset_frames, fleet_min_offset_frames }) => {
                    self.set_echo_depth(own_offset_frames, fleet_min_offset_frames);
                }
                Ok(Command::SetSkipFade(ms)) => {
                    self.skip_fade_ms = ms.min(crate::SKIP_FADE_MAX_MS);
                    self.remember_settings();
                }
                Ok(Command::SetResumeSave(ms)) => {
                    self.resume_save_ms =
                        ms.clamp(crate::RESUME_SAVE_MIN_MS, crate::RESUME_SAVE_MAX_MS);
                    self.remember_settings();
                }
                Ok(Command::SetSkipSuppress(h)) => {
                    self.skip_suppress_h =
                        h.clamp(crate::SKIP_SUPPRESS_MIN_H, crate::SKIP_SUPPRESS_MAX_H);
                    self.remember_settings();
                }
                Ok(Command::SetDequeueSuppress(h)) => {
                    self.dequeue_suppress_h =
                        h.clamp(crate::DEQUEUE_SUPPRESS_MIN_H, crate::DEQUEUE_SUPPRESS_MAX_H);
                    self.remember_settings();
                }
                Ok(Command::SetQueueDepth(n)) => {
                    self.queue.min_depth =
                        n.clamp(crate::QUEUE_DEPTH_MIN, crate::QUEUE_DEPTH_MAX);
                    self.remember_settings();
                }
                Ok(Command::SetCueSheets(on)) => {
                    self.cue_sheets = on;
                    self.remember_settings();
                }
                Ok(Command::SetCovers(on)) => {
                    self.covers = on;
                    self.remember_settings();
                }
                Ok(Command::SetLyricsCache(on)) => {
                    self.lyrics_cache = on;
                    self.remember_settings();
                }
                Ok(Command::SetLyricsSidecar(on)) => {
                    self.lyrics_sidecar = on;
                    self.remember_settings();
                }
                Ok(Command::RestartUnderruns) => {
                    // The real counter is untouched; only the mark moves.
                    self.underrun_baseline = self.underruns_playing;
                    self.underrun_since = unix_now();
                }
                Ok(Command::SetSampleInterval(ms)) => {
                    self.sample_interval_ms =
                        ms.clamp(crate::SAMPLE_INTERVAL_MIN_MS, crate::SAMPLE_INTERVAL_MAX_MS);
                    self.remember_settings();
                }
                Ok(Command::SetSkipLead(ms)) => {
                    self.skip_lead_ms =
                        ms.clamp(crate::SKIP_LEAD_MIN_MS, crate::SKIP_LEAD_MAX_MS);
                    self.remember_settings();
                }
                Ok(Command::SetVolume(v)) => {
                    self.volume = v.clamp(0.0, 1.0);
                    // Straight to the device: the callback applies it, so the
                    // change is heard now rather than a ring-depth later.
                    if let Some(r) = &self.path.ring {
                        r.volume.set(self.volume);
                    }
                    self.remember_settings();
                }
                Ok(Command::Enqueue(e)) => {
                    self.queue.push(e);
                    self.queue_edited = true;
                }
                Ok(Command::EnqueueNext(e)) => {
                    self.queue.push_front(e);
                    self.queue_edited = true;
                }
                Ok(Command::EnqueueMany(entries, place)) => {
                    self.queue_edited = true;
                    if !entries.is_empty() {
                        match place {
                            Placement::Now => {
                                self.queue.insert_at(0, entries);
                                self.skip();
                            }
                            // Position ONE of the queue, which is the top of
                            // "Coming up". The queue holds only what is still
                            // to come -- the sounding passage lives in `live`
                            // and is not in it -- so index 0 is already after
                            // the current one. Inserting at 1 to "leave what is
                            // playing alone" put everything one place too late.
                            Placement::Next => self.queue.insert_at(0, entries),
                            Placement::Last => {
                                for e in entries {
                                    self.queue.push(e);
                                }
                            }
                        }
                    }
                }
                Ok(Command::PlayNow(e)) => {
                    // Front, then skip: skip takes the front of the queue, so
                    // anything less than the front would play the passage that
                    // was already next instead.
                    self.queue.push_front(e);
                    self.skip();
                    self.queue_edited = true;
                }
                Ok(Command::RemoveQueued(id)) => {
                    // Taken out by hand before it ever played: a weaker
                    // statement than a skip, and it earns the shorter window
                    // `[SPEC-PLAY-055]`. Read before the removal, since after it
                    // there is nothing left to name.
                    //
                    // Deliberately NOT the same path as a passage the engine
                    // could not open: that is a failure, not a preference, and
                    // `[REQ-PD-112]` requires it leave no mark at all.
                    let declined = self
                        .queue
                        .iter()
                        .find(|e| e.qid == id)
                        .map(|e| (e.passage_id, e.mbid.clone()));
                    self.queue_edited = true;
                    if self.queue.remove(id) {
                        if let Some((passage_id, mbid)) = declined {
                            self.note_rejection(
                                crate::db::Rejection::Dequeue,
                                passage_id,
                                mbid.as_deref(),
                                None,
                                None,
                            );
                        }
                    }
                }
                Ok(Command::ShiftQueued(id, delta)) => {
                    self.queue.shift(id, delta);
                    self.queue_edited = true;
                }
                Ok(Command::Persist) => self.persist(true),
                Ok(Command::Shutdown) | Err(TryRecvError::Disconnected) => {
                    self.shutdown = true;
                    return;
                }
                Err(TryRecvError::Empty) => return,
            }
        }
    }

    /// Stopping the consumer means stopping the DEVICE, not just declining to
    /// submit: the output ring would otherwise play on for its full depth.
    /// Producers are untouched, so the buffers stay primed [REQ-AUD-142].
    fn set_playing(&mut self, on: bool) {
        self.playing = on;
        self.path.set_playing(on);
    }

    /// Begin playing because the saved state said so `[PI5-PWR-030]`.
    ///
    /// Public where `set_playing` is not, and named for the occasion rather
    /// than the mechanism: this is the one caller that is not a person pressing
    /// something, and reading `engine.play_on_resume()` at the call site says
    /// why it is happening where `engine.set_playing(true)` would not.
    pub fn play_on_resume(&mut self) {
        self.set_playing(true);
    }

    /// Fade the sounding passage out and cross into the next `[REQ-AUD-162]`.
    ///
    /// Dropping the passage here is not enough on its own, and the comment that
    /// used to sit on this function claiming otherwise was wrong: it discards
    /// the *decoder's* buffer, but the output ring still holds every sample
    /// already mixed. Measured, that was **14.0 s** from button to new music.
    ///
    /// So the ring is cut to the length of the fade, and the next passage --
    /// already decoded `[REQ-AUD-160]` -- is summed over its tail. The listener
    /// hears the outgoing passage fall away over `skip_fade_ms` while the
    /// incoming one rises from `skip_lead_ms`, the two overlapping for the
    /// difference.
    fn skip(&mut self) {
        // The ring is cut `[REQ-AUD-158]`, so frames already counted were
        // never heard and no anchor may be compared across this.
        self.echo_basis.void(crate::echo::Voided::Skip);
        // **Taken before the early return, not after** `[GDE-ARC-058]`. A
        // target left sitting here would be picked up by whatever skipped
        // next -- an ordinary listener's skip, minutes later -- and placed
        // against an instant that had long passed. Consumed on every path
        // through this function, including the one that does nothing.
        let join_at = self.echo_join_at.take();
        // The eager `shown` below is a display promise, not an audibility
        // claim `[GDE-ARC-059]`.
        self.shown_is_sounding = false;
        // **The alignment plan does not survive a cut either** `[GDE-ARC-067]`.
        // `admit_due` below takes the `(Some(_), None)` arm and admits at
        // once, which would spend `echo_next_shift_ms` against *this*
        // transition -- and the shift was measured for the master's next
        // boundary, minutes away, not for a listener pressing skip. In the
        // "later" direction it would become silence and be emitted into the
        // opening moments of the track that was just asked for: a reviewer
        // measured 19756 frames of it, about 450 ms of dead air.
        //
        // Same reasoning as the basis voiding above `[GDE-ECHO-360]`: after a
        // cut the queue has advanced and the ring is gone, so no plan made
        // before it is worth acting on. A gap already in flight goes with it,
        // having been aimed at a ring that no longer exists.
        //
        // Deliberately NOT done in `EchoCorrectNextStart`, which clears the
        // debt but must leave the gap alone: a debt is an outstanding
        // obligation, while a gap is a delivery already in progress against
        // an admission that has happened. They have different lifetimes and
        // only one of them is superseded by a new plan.
        self.echo_next_shift_ms = 0;
        self.echo_gap_frames = 0;
        if self.live.is_empty() {
            return;
        }
        let ch = self.out_channels.max(1);
        let rate = self.out_rate as u64;
        let fade_samples = (self.skip_fade_ms * rate / 1000) as usize * ch;

        // Everything sounding is already mixed into the ring and will be faded
        // there, together. Nothing upstream is worth keeping -- including a
        // passage part-way through an ordinary crossfade, which the listener
        // has barely heard, its decode having run a ring's depth ahead.
        self.live.clear();
        // Promote the prepared passage. Without one this degrades to a plain
        // fade to silence, which is the right answer when the queue is empty.
        self.admit_due();
        // Skip cuts the ring to the fade, so the incoming passage is audible
        // within a second rather than a ring's depth. Handing the display over
        // now keeps the button honest [REQ-AUD-164].
        //
        // At the passage's ORIGIN, not at zero. A skip that carries a resume
        // position -- a restart, a seek, an echo node joining part-way
        // `[GDE-ECHO-330]` -- opens the passage part-way in, and calling that
        // position zero understates it by the whole offset for as long as the
        // passage plays. Found on `lempiplay3` 2026-09-18: a mid-passage join
        // a second in published an anchor a second behind where the node
        // actually was, which read as a 1028 ms residual and sent the
        // correction chasing an error that did not exist. It misreports the
        // display and any resume point taken during that passage too, so this
        // was never only an echo fault.
        self.shown = self.live.first().map(|l| (l.entry.clone(), l.origin_ms));

        // **After the preparation, not before** `[GDE-ARC-058]`,
        // `[GDE-ECHO-342]`. `admit_due` above is where a commanded start
        // opens its file and seeks into it, so only from here is the distance
        // to the target instant a measurement rather than a guess. This is
        // the whole of what `echo_prep_ms` was estimating, and the estimate
        // no longer has to be right -- only generous enough that
        // `fire_echo_start` left time to get here.
        let lead_ms = match join_at {
            Some(sound_at) => {
                let now = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_or(0, |d| d.as_nanos() as u64);
                let ms = self.join_lead_frames(sound_at, now) * 1000 / rate.max(1);
                tracing::info!("echo-start: sample 0 goes {ms} ms into the ring, measured at the cut (the constant lead would have been {} ms)",
                          self.skip_lead_ms);
                ms
            }
            None => self.skip_lead_ms,
        };
        let lead_samples = (lead_ms * rate / 1000) as usize * ch;
        self.cut_ring_to_incoming(fade_samples, lead_ms);
        self.republish_after_cut(lead_samples);
    }

    /// Cut the ring back to the fade and overlay whatever is sounding now.
    ///
    /// **The reason a skip is heard within a second** rather than after a ring's
    /// depth — about 14 s of already-mixed audio otherwise stands between the
    /// listener and the change they asked for. Lifted out of `skip` when `seek`
    /// turned out to need exactly it: both replace what is in the ring with a
    /// different point in the music, and differ only in which point.
    fn cut_ring_to_incoming(&mut self, fade_samples: usize, lead_ms: u64) {
        // One lead, converted once `[GDE-ARC-033]`. This used to take samples
        // for sizing the overlay and then hand `self.skip_lead_ms` to
        // `begin_skip_transition` for the placement -- two models of one
        // quantity, which held only while both were the same constant. A
        // commanded start makes them differ `[GDE-ARC-058]`.
        let ch = self.out_channels.max(1);
        let lead_samples = (lead_ms * self.out_rate as u64 / 1000) as usize * ch;
        // Whatever a previously-departed passage still had draining through
        // this ring is about to be wiped along with everything else in it --
        // take its estimate as final now, rather than let a skip or a seek
        // strand it waiting for a tail that will never finish arriving
        // `[REQ-VIS-250]`.
        self.resolve_pending_finish_now();
        // How much of the outgoing survives the cut sets how much of the
        // incoming overlaps it. Asked before the cut, since afterwards the
        // answer is by definition the fade length.
        let have = self.path.ring.as_ref().map_or(0, |r| r.buffered()).min(fade_samples);
        let wanted = have.saturating_sub(lead_samples);

        // **Fill the incoming stream before cutting the ring** `[PI-CHR-075]`.
        //
        // The overlay is mixed from whatever the incoming passage has already
        // decoded. `prepare_next` opens it early precisely so there is
        // something there — but opening is not decoding, and `top_up` fills
        // it over the ticks that follow. A skip landing in that window found
        // 882 samples where it wanted 132,300, laid almost nothing into the
        // ring it had just cut, and left about a second of silence
        // `[PI-CHR-075]`. Measured on the appliance, where seeking into a
        // 244-minute capture on an SD card holds that window open for
        // seconds; a desktop closes it too fast to notice.
        //
        // Bounded, because this runs where the listener is waiting: enough
        // attempts to cover the overlay and no more. Falling short is not a
        // failure — it degrades to the old behaviour rather than blocking.
        if let Some(l) = self.live.first_mut() {
            for _ in 0..crate::TOPUP_TRIES_BEFORE_CUT {
                if l.stream.ring.len() >= wanted || l.stream.finished {
                    break;
                }
                Self::top_up(l);
            }
        }

        let mut overlay = vec![0.0f32; wanted];
        if let Some(l) = self.live.first_mut() {
            // Through `mix`, not by reading the ring directly: the fade-in has
            // been applied on the way in `[XFD-ORTH-020]`, and this keeps the
            // accounting identical to an ordinary tick.
            let before = l.stream.ring.len();
            let filled = mix(std::iter::once(&mut l.stream), &mut overlay);
            let consumed = before.saturating_sub(l.stream.ring.len());
            l.frames_mixed += (consumed / l.stream.channels.max(1)) as u64;
            overlay.truncate(filled);
        }

        if let Some(o) = &self.path.ring {
            o.begin_skip_transition(
                self.skip_fade_ms,
                lead_ms,
                Curve::Exponential,
                &overlay,
            );
        }
    }

    /// Move to a point inside the passage that is sounding `[REQ-VIS-225]`.
    ///
    /// **The same operation as `skip`, aimed at the same passage instead of the
    /// next one**: clear what is sounding, open at the new point, cut the ring
    /// back so the move is heard at once. Reusing that path rather than writing
    /// a second one keeps a single place where this engine stops sounding one
    /// thing and starts sounding another.
    ///
    /// **It lands alone.** Mid-crossfade, both passages go and the sought one
    /// returns by itself — a seek into a passage that is still fading up under
    /// another would otherwise resume an overlap the listener has left behind.
    ///
    /// The jump itself is not listening `[SPEC-PLAY-012]`: `heard_from` is
    /// cleared so no part of the distance travelled is credited as heard.
    pub fn seek_to(&mut self, position_ms: u64) {
        let Some(head) = self.live.first() else { return };
        let entry = head.entry.clone();
        // A seek to the very end would open a decoder with nothing to decode.
        let at = position_ms.min(entry.duration_ms().saturating_sub(1));
        let opened = match self.open(&entry, at) {
            Ok(l) => l,
            // A file that will not re-open is not a reason to stop the music
            // that is already sounding from it.
            Err(e) => {
                tracing::error!("seek in {}: {e}", entry.path.display());
                return;
            }
        };
        let ch = self.out_channels.max(1);
        let rate = self.out_rate as u64;
        let fade_samples = (self.skip_fade_ms * rate / 1000) as usize * ch;
        let lead_samples = (self.skip_lead_ms * rate / 1000) as usize * ch;

        // **A seek cuts the ring, and everything that follows from that**
        // `[GDE-ARC-064]`. `skip` has voided the basis since `[GDE-ECHO-360]`
        // was written -- frames that were counted were never heard -- and a
        // seek discards exactly the same audio by exactly the same call. It
        // was not voiding, so a listener's seek left this node comparing a
        // frame clock across a cut and publishing anchors from it.
        self.echo_basis.void(crate::echo::Voided::Skip);
        // An in-flight gap was aimed at the ring this is about to discard,
        // and a pending shift was measured against a content position this
        // seek is about to leave `[GDE-ARC-067]`. `skip` clears both; this
        // cleared only the gap, and a seek re-announces the SAME passage, so
        // `fs.corrected` blocks the follower from re-measuring and the stale
        // shift would survive to be spent at the next boundary.
        self.echo_gap_frames = 0;
        self.echo_next_shift_ms = 0;
        // And the `shown` set below is the same display promise `skip` makes:
        // the sought-to point does not sound until the lead has drained
        // `[GDE-ARC-059]`.
        self.shown_is_sounding = false;
        self.live.clear();
        self.live.push(opened);
        // The listener is at the new point the moment they ask, not a ring's
        // depth later `[REQ-AUD-164]`.
        self.shown = self.live.first().map(|l| (l.entry.clone(), at));
        self.heard_from = None;
        self.cut_ring_to_incoming(fade_samples, self.skip_lead_ms);
        self.republish_after_cut(lead_samples);
    }

    /// The passage sounding and how far into its span, for a handoff that must
    /// not restart it `[SPEC-BK-065]`.
    ///
    /// **The audible position, not the decoded one.** `audible_ms` subtracts
    /// what is sitting in the output ring; `played_ms` would be ahead by the
    /// buffer depth, and handing that over would start the other side a ring's
    /// worth into the future — about 14 s here, which is not a seam but a jump.
    pub fn head_position(&self) -> Option<(i64, u64)> {
        self.live.first().map(|l| (l.entry.passage_id, self.audible_ms(l)))
    }

    /// Whether the sounding passage's play is already in the history
    /// `[SPEC-BK-065]`.
    pub fn head_counted(&self) -> bool {
        self.recorded && !self.live.is_empty()
    }

    /// Adopt a passage another backend already counted, so this one will not
    /// count it again when it arrives `[SPEC-BK-065]`.
    pub fn adopt_counted(&mut self, passage_id: i64) {
        self.counted_elsewhere = Some(passage_id);
    }

    /// Fade out because the passage is being handed to another backend, which
    /// is **not** the listener declining it `[SPEC-BK-065]`.
    ///
    /// Same fade, different meaning. `fade_to_silence` goes through `skip`, and
    /// a skip that leaves before the threshold earns a suppression window
    /// `[SPEC-PLAY-050]` — 156 hours by default. A passage that is still
    /// playing on the other side has not been declined, and suppressing it
    /// would punish the listener for changing rooms.
    ///
    /// **A latch, not a flag around the call.** The fade is asked for here and
    /// the head does not depart until it completes, several ticks later; a flag
    /// cleared on the way out of this function would already be false by the
    /// time the departure was judged. It is set only when something is really
    /// sounding, so it cannot sit armed and swallow the next genuine skip.
    pub fn hand_off_to_silence(&mut self, ms: u64) -> bool {
        self.handing_over = !self.live.is_empty();
        self.fade_to_silence(ms)
    }

    /// Fade what is sounding down to silence and stop `[SPEC-BK-030]`.
    ///
    /// **This is `skip` with nothing to skip to**, which the skip path already
    /// handles: emptying the queue first means `admit_due` promotes nothing, and
    /// the transition it begins has no incoming audio to overlay — so the ring
    /// fades out and stays out. `skip`'s own comment said as much long before a
    /// handoff wanted it.
    ///
    /// Reusing it rather than writing a second fade is the point. There is one
    /// place in this engine that takes the ring from sounding to not, and a
    /// handoff has no business inventing another with its own idea of a curve.
    ///
    /// Returns whether an output was actually faded. With no ring — a silent
    /// path, a failed device — there is nothing to fade and saying otherwise
    /// would be the lie `[PI3-API-030]` refuses.
    pub fn fade_to_silence(&mut self, ms: u64) -> bool {
        // The queue goes first: the passages are already being rebuilt on the
        // other side, and one left here would be promoted into the fade.
        self.queue.clear();
        let had_output = self.path.ring.is_some() && !self.live.is_empty();
        let saved = self.skip_fade_ms;
        self.skip_fade_ms = ms.min(crate::SKIP_FADE_MAX_MS);
        self.skip();
        self.skip_fade_ms = saved;
        had_output
    }

    /// Start the next passage when the current one reaches its lead-out point.
    ///
    /// Position-driven via the shared rule, never buffer-driven `[XFD-BEH-C1-020]`.
    fn admit_due(&mut self) {
        // **Split once, from the pair that is actually about to hand over.**
        // The overlap the coarse knob spends belongs to *this* transition, and
        // everything below either reads the queue head or removes it -- so
        // re-deriving the split after `advance()` would be asking a different
        // pair `[GDE-ECHO-372]`.
        let (overlap, ceiling) = self.next_transition_overlap();
        let shift = self.echo_split();
        // **What the outgoing passage really had left when admission fired**
        // `[GDE-ARC-057]`. `should_admit_nudged` can only fire at or after the
        // instant it wants, so this -- not the request -- is what the
        // transition achieved. Captured here because `advance()` below removes
        // the pair it was measured from.
        //
        // Defaults to `overlap`, which makes the achieved shift zero: the
        // honest answer when there is no outgoing passage to take it from.
        let mut remaining = overlap;
        let due = match (self.queue.peek(), self.live.last()) {
            (Some(next), Some(l)) => {
                // Only the admission half can act here; the origin and the
                // gap are spent below, once the passage is actually taken
                // `[GDE-ARC-051]`.
                let played = self.played_ms(l);
                remaining = l.entry.duration_ms().saturating_sub(played);
                should_admit_nudged(&l.entry, played, next, shift.admit_ms)
            }
            (Some(_), None) => true,
            _ => false,
        };
        if !due {
            return;
        }
        // The queue holds only UPCOMING passages; admission moves one into
        // `live`, where `live[0]` is what is sounding. Keeping a passage in
        // both places would mean two answers to "what is playing".
        let Some(entry) = self.queue.advance() else { return };
        // Spent. A correction left in place would be applied again at every
        // boundary, turning a one-off nudge into a standing rate error.
        // An earlier version consumed `echo_next_shift_ms` here and re-derived
        // the split further down, where it was already zero -- so the fine
        // half was silently dropped and every late correction did nothing at
        // all, while this very line logged the value it was about to discard.
        // A log computed at a different point from the action is not evidence
        // of the action `[GDE-ECHO-347]`.
        // Captured before the block below spends it `[GDE-ARC-071]`.
        let asked_ms = self.echo_next_shift_ms;
        if self.echo_next_shift_ms != 0 {
            // A commanded start brings its own position and outranks this, and
            // takes the fine knob with it.
            let superseded = self.pending_resume.is_some();
            let spent = if superseded { Shift { admit_ms: shift.admit_ms, ..Shift::default() } }
                        else { shift };
            let delivered = Self::delivered_shift_ms(spent, overlap, ceiling);
            // **Silence is spent here** `[GDE-ARC-052]`: the mixer emits it
            // before this passage's first sample, so the passage lands whole
            // and simply later. Frames, because that is what the mixer
            // counts in and a millisecond is not a whole number of them.
            if spent.gap_ms > 0 {
                self.echo_gap_frames =
                    spent.gap_ms.saturating_mul(self.out_rate.max(1) as u64) / 1000;
            }
            // **What the transition could not absorb is not lost and not
            // pretended away** `[GDE-ECHO-372]`, `[GDE-ECHO-378]`. A node
            // asked to move *later* can only give back an overlap that
            // exists, and this library's is about five milliseconds; the rest
            // goes to the frame trim, which is the one actuator that works in
            // both directions and is inaudible at 23 us a time
            // `[GDE-ECHO-349]`. **Added, not set** `[GDE-ARC-041]`: this is a
            // delta only the engine knows -- what admission could not spend --
            // and the follower may already have set a base here for the part
            // it knew the boundary would not take. Accumulation across
            // passages is bounded by `EchoCorrectNextStart` clearing the debt
            // as each new plan arrives.
            //
            // **Measured, not assumed** `[GDE-ARC-057]`. `delivered` is what
            // the request implies; `achieved` is what the outgoing passage's
            // real remaining time says actually happened. Only the second can
            // disagree with the request, and only the second sends a genuine
            // shortfall to the trim. They differ exactly when a correction
            // arrives with less of the outgoing passage left than it asked to
            // spend, which the engine previously could not see at all.
            let achieved = Self::achieved_shift_ms(spent, remaining, overlap);
            let left = self.echo_next_shift_ms - achieved;
            let owed = left.saturating_mul(self.out_rate.max(1) as i64) / 1000;
            self.echo_debt_frames = self.echo_debt_frames.saturating_add(owed);
            // The trim clock runs for a debt as well as for a rate, exactly as
            // `EchoShedOffset` starts it: `due_trim` answers `None` while
            // `echo_last_trim` is `None`, and a follower that has not yet
            // produced a rate fit has sent only `SetEchoRate(0.0)`, which
            // clears it. A debt with no clock is a number nobody pays.
            if self.echo_debt_frames != 0 && self.echo_last_trim.is_none() {
                self.echo_last_trim = Some(std::time::Instant::now());
            }
            // Say which happened rather than claiming a shift that was dropped
            // `[GDE-ECHO-351]`.
            if superseded {
                tracing::info!("echo-offset: passage {} had a {} ms shift pending, superseded by a commanded start", entry.passage_id, self.echo_next_shift_ms.abs());
            } else {
                tracing::info!("echo-offset: passage {} asked for {} ms {}; placed by {} ms of overlap + {} ms origin + {} ms silence; wanted {} ms, achieved {} ms ({} ms of the outgoing passage left, overlap {}), {} ms left to the frame trim",
                          entry.passage_id, self.echo_next_shift_ms.abs(),
                          if self.echo_next_shift_ms > 0 { "earlier" } else { "later" },
                          spent.admit_ms, spent.origin_ms, spent.gap_ms,
                          delivered.abs(), achieved.abs(),
                          remaining, overlap, left.abs());
            }
            self.echo_next_shift_ms = 0;
        }
        // The forward schedule `[GDE-ECHO-310]`, emitted here because here is
        // where the ~15 s of lead exists: everything already in the ring plays
        // before this passage's first sample can sound, and that lead is what
        // makes an arbitrary presentation offset compensable.
        // A passage boundary reached cleanly is the only way back in.
        self.echo_basis.establish();
        // Taken before the schedule is built, not after. A resume starts the
        // passage part-way in, and a schedule announcing sample 0 for a
        // passage that begins at 3 minutes tells every follower to play the
        // wrong audio at the right time `[GDE-ECHO-325]`.
        // A listener's own resume point outranks alignment; otherwise the
        // fine half of the offset correction goes here `[GDE-ECHO-347]`.
        let origin = self.pending_resume.take().or(if shift.origin_ms > 0 { Some(shift.origin_ms) } else { None });
        if let Some(r) = self.path.ring.as_ref() {
            if r.clock.timestamps() == crate::output::Timestamps::Hardware {
                if let Ok(d) = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                {
                    let _ = (r, d);
                    let depth = self.out_buffered_frames() as u64;
                    self.publish_schedule(entry.passage_id, origin.unwrap_or(0), depth);
                }
            }
        }
        // **How much of the incoming passage is actually decoded right now**
        // `[GDE-ARC-071]`. Pure instrumentation, no behaviour: testing whether
        // an advance is lost because the passage it advances has nothing to
        // mix yet.
        //
        // `prepare_next` OPENS the next passage early; opening is not
        // decoding, and `top_up` fills it over the ticks that follow. An
        // admission brought forward by N ms only moves the sound forward by N
        // if N ms of audio is ready to mix at that instant -- otherwise `mix`
        // contributes nothing for it and sample 0 lands later than the
        // admission implies, losing exactly the advance that was asked for.
        // `cut_ring_to_incoming` already tops up before a cut for this reason
        // `[PI-CHR-075]`; an ordinary boundary admission does not.
        //
        // Measured on the fleet at ~0.58 of every correction delivered, and
        // the loss is proportional to the correction rather than a fixed cost
        // per boundary, which is the shape this would produce.
        // Only the ADMISSION half needs audio ready: bringing admission
        // forward is what requires something to mix. The origin discards
        // from the head instead and needs nothing decoded in advance.
        let advance_ms = remaining as i64 - overlap as i64;
        if asked_ms > 0 && advance_ms > 0 {
            let ready_ms = self.ready.as_ref()
                .filter(|l| l.entry.passage_id == entry.passage_id)
                .map(|l| (l.stream.ring.len() / l.stream.channels.max(1)) as u64
                         * 1000 / self.out_rate.max(1) as u64);
            match ready_ms {
                Some(have) => tracing::info!(
                    "echo-decode: passage {} asked {} ms, admission brought forward {} ms, {} ms decoded and ready{}",
                    entry.passage_id, asked_ms, advance_ms, have,
                    if (have as i64) < advance_ms {
                        format!(" -- SHORT by {} ms", advance_ms - have as i64)
                    } else {
                        String::new()
                    }),
                None => tracing::info!(
                    "echo-decode: passage {} asked {} ms, admission brought forward {} ms, nothing prepared (opened here instead)", entry.passage_id, asked_ms, advance_ms),
            }
        }
        // The prepared passage is the queue head already opened at its start,
        // so it serves unless a resume offset overrides where to begin.
        if origin.is_none() {
            if let Some(l) = self.ready.take().filter(|l| l.entry.passage_id == entry.passage_id) {
                self.live.push(l);
                return;
            }
        }
        match self.open(&entry, origin.unwrap_or(0)) {
            Ok(l) => self.live.push(l),
            Err(e) => {
                tracing::warn!("skipping {}: {e}", entry.path.display());
                self.dropped.push(entry.passage_id);
            }
        }
    }

    /// Passages that were chosen but could not be opened, taken once.
    ///
    /// Draining rather than reading: each is reported to the Director exactly
    /// once, and a second report would restore a rotation entry twice.
    pub fn take_dropped(&mut self) -> Vec<i64> {
        std::mem::take(&mut self.dropped)
    }

    /// Open the passage after this one before anyone asks for it
    /// `[REQ-AUD-160]`.
    ///
    /// Skip used to pay for a file open, a seek and a resampler build at the
    /// moment the button was pressed, and the fade had to be long enough to
    /// hide all of it. Doing that work early is what lets the fade be as short
    /// as it sounds right rather than as long as the decoder needs.
    fn prepare_next(&mut self) {
        // A pending resume owns the next admission and opens at its own offset,
        // so preparing one at the start would be wasted and then discarded.
        if self.pending_resume.is_some() {
            return;
        }
        let Some(next) = self.queue.peek() else { return };
        if self.ready.as_ref().map(|l| l.entry.passage_id) == Some(next.passage_id) {
            return; // already standing by
        }
        let entry = next.clone();
        match self.open(&entry, 0) {
            Ok(l) => self.ready = Some(l),
            Err(e) => {
                // Dropped rather than left in place: retrying an unopenable
                // passage every tick would spin forever and never reach the
                // playable one behind it.
                tracing::warn!("skipping {}: {e}", entry.path.display());
                self.queue.advance();
                self.dropped.push(entry.passage_id);
                self.ready = None;
            }
        }
    }

    fn open(&self, e: &QueueEntry, origin_ms: u64) -> Result<Live, String> {
        // Clamp: a resume point past the end (the passage was re-trimmed since)
        // must replay the passage, not decode nothing.
        let origin_ms = origin_ms.min(e.duration_ms().saturating_sub(1));
        let dec = PassageDecoder::open(&e.path, e.start_ms + origin_ms, Some(e.end_ms))
            .map_err(|err| err.to_string())?;
        let ch = dec.channels;
        let resampler = Resampler::new(dec.sample_rate, self.out_rate, ch)?;
        // Fades are measured in OUTPUT frames because they are applied after
        // conversion; using the file rate here would mis-time every fade on a
        // device that does not match the file.
        let sr = self.out_rate as f32;
        // Both measured from the PASSAGE's own start, not from wherever this
        // particular decode session happens to begin `[SPEC-SUI-226]`: a
        // resume partway through must not re-trigger the fade-in, and the
        // fade-out still has to land at the passage's own true end
        // regardless of where playback picked up. `Stream::frames_written`
        // is seeded below from `origin_frames` for exactly this reason.
        let total_frames = (e.duration_ms() as f32 * sr / 1000.0) as u64;
        let origin_frames = (origin_ms as f32 * sr / 1000.0) as u64;
        let envelope = Envelope {
            fade_in: Fade {
                curve: e.fade_in_curve,
                frames: (e.fade_in_ms as f32 * sr / 1000.0) as u64,
                fade_in: true,
            },
            fade_out: Fade {
                curve: e.fade_out_curve,
                frames: (e.fade_out_ms as f32 * sr / 1000.0) as u64,
                fade_in: false,
            },
            total_frames,
        };
        let mut stream = Stream::new(BUFFER_FRAMES * ch, ch, envelope);
        stream.frames_written = origin_frames;
        Ok(Live {
            stream,
            dec,
            resampler,
            converted: Vec::new(),
            gain: 10f32.powf(e.gain_db / 20.0),
            entry: e.clone(),
            frames_mixed: 0,
            origin_ms,
        })
    }

    /// Feeds what is sounding AND what is merely ready, so the prepared passage
    /// arrives at the mixer already full `[REQ-AUD-160]`.
    fn top_up_decoders(&mut self) {
        for l in self.live.iter_mut().chain(self.ready.iter_mut()) {
            Self::top_up(l);
        }
    }

    fn top_up(l: &mut Live) {
        {
            if l.stream.finished
                || l.stream.ring.free() < crate::DECODE_TOPUP_FRAMES * l.stream.channels
            {
                return;
            }
            match l.dec.next() {
                Ok(Some(chunk)) => {
                    l.converted.clear();
                    let mut buf = std::mem::take(&mut l.converted);
                    match l.resampler.process(chunk, &mut buf) {
                        Ok(()) => {
                            if l.gain != 1.0 {
                                for s in buf.iter_mut() {
                                    *s *= l.gain;
                                }
                            }
                            l.stream.push(&mut buf);
                        }
                        Err(e) => {
                            tracing::error!("resample: {e}");
                            l.stream.finished = true;
                        }
                    }
                    l.converted = buf;
                }
                Ok(None) => l.stream.finished = true,
                Err(e) => {
                    tracing::error!("decode: {e}");
                    l.stream.finished = true;
                }
            }
        }
    }

    /// Mix at most what the output can accept, then submit it.
    ///
    /// Sizing the mix to the free space is not an optimisation, it is
    /// correctness: `mix` CONSUMES from the stream rings, so mixing more than
    /// the output will take discards the surplus permanently. That silently
    /// dropped most of a passage and made nine minutes of audio "finish" in
    /// 66 seconds — inaudible on a null sink, which always accepts everything.
    ///
    /// Limiting here is also what propagates back-pressure: the stream rings
    /// stay full, so the decoders stop, and the device paces the whole chain.
    /// How late a committed start may be and still be worth making.
    ///
    /// A tick is 10 ms, so a join is only ever tick-accurate; this sits well
    /// above that jitter and well below what a listener hears as two speakers
    /// instead of one. The residual is not permanent -- `[GDE-ECHO-340]`
    /// corrects offset at the next passage boundary, where it is inaudible.
    const ECHO_START_LATE_LIMIT: std::time::Duration = std::time::Duration::from_millis(100);

    /// And beyond this ahead, it is not a schedule at all.
    ///
    /// A lead is fifteen seconds `[LOG-ECHO-020]` and a join is one. A minute
    /// leaves both ample room while catching the case that actually happens:
    /// a node booted without an RTC computing a submission time days out
    /// `[GDE-ECHO-365]`.
    const ECHO_START_FAR_LIMIT: std::time::Duration = std::time::Duration::from_secs(60);

    /// What the first join assumes its own preparation will cost.
    ///
    /// Only ever the first: every join thereafter uses the measured figure.
    /// 400 ms is the middle of what this fleet showed, so a cold node is
    /// wrong by a few hundred ms once rather than by a second every time.
    const ECHO_PREP_GUESS_MS: u64 = 400;

    /// Ceiling on the measured figure, so one pathological join -- a cold
    /// cache, a long seek into a capture -- cannot leave every later join
    /// firing seconds early `[PI-CHR-075]`.
    ///
    /// `pub(crate)` because the follower has to aim its mid-join budget past
    /// it, and a budget derived from a different number is `[GDE-ECHO-375]`
    /// all over again.
    pub(crate) const ECHO_PREP_MAX_MS: u64 = 2_000;

    /// Begin the master's passage at the instant its schedule named.
    ///
    /// Joining and seeking only: both cut the ring `[REQ-AUD-158]`, so sample
    /// 0 goes into an empty one and reaches the device almost at once, which
    /// is what makes `submit_at` the right instant to act on here. An ordinary
    /// passage boundary needs none of this -- the ring depth already holds the
    /// fleet together `[LOG-ECHO-030]` and there is nothing to command.
    /// How long before a commanded start should **sound** this node has to
    /// begin arranging it `[GDE-ECHO-375]`.
    ///
    /// The single figure `fire_echo_start` subtracts and `EchoNode` publishes.
    /// One function rather than two arithmetics, because a follower aiming at
    /// a budget smaller than this has every join declined and the declining is
    /// invisible from the other end.
    pub(crate) fn echo_start_lead_ms(&self) -> u64 {
        self.skip_lead_ms + self.echo_prep_ms
    }

    fn fire_echo_start(&mut self) {
        let Some((_, _, at)) = self.echo_start.as_ref() else { return };
        let Ok(d) = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        else { return };
        let now = d.as_nanos() as u64;
        // Fired EARLY by the skip's own lead, because this start goes through
        // `skip` and a skipped-to passage is not audible until the lead has
        // passed `[REQ-AUD-162]`. Without this every commanded start lands
        // exactly `skip_lead_ms` late -- 500 ms on this fleet, which is most of
        // the 1028 ms residual seen on `lempiplay3` the first time two nodes
        // ran `[GDE-ECHO-330]`. The caller asked for a time the audio should
        // SOUND; what the engine controls is when it starts arranging it.
        // Early by BOTH the constant lead and the measured preparation: the
        // caller named an instant the audio should sound, and everything
        // between here and there has to be subtracted, not just the part that
        // happens to be a constant `[GDE-ECHO-342]`.
        let at = at.saturating_sub(self.echo_start_lead_ms() * 1_000_000);
        match crate::echo::start_verdict(
            at, now, Self::ECHO_START_LATE_LIMIT, Self::ECHO_START_FAR_LIMIT) {
            crate::echo::StartVerdict::Wait => return,
            crate::echo::StartVerdict::TooFar { by } => {
                // A clock that has not been disciplined yet `[GDE-ECHO-365]`.
                // Waiting for this would be waiting for days.
                tracing::warn!("echo-start: scheduled {} s out, which is not a schedule; dropping it and waiting for this node's clock", by.as_secs());
                self.echo_start = None;
                return;
            }
            crate::echo::StartVerdict::TooLate { by } => {
                // **And the estimate backs off** `[GDE-ECHO-375]`. Firing
                // early by more than a start's whole lead is itself the way a
                // start becomes too late, and `echo_prep_ms` is revised only
                // by a join that fires -- so an estimate large enough to
                // prevent every join could never be corrected by one. Decayed
                // towards the cold guess rather than reset to it, on the same
                // reasoning as the measurement itself: one refusal should move
                // the figure, not replace it.
                let was = self.echo_prep_ms;
                self.echo_prep_ms =
                    ((was * 3 + Self::ECHO_PREP_GUESS_MS) / 4).max(Self::ECHO_PREP_GUESS_MS);
                // Said out loud. A node that silently declines to join looks
                // exactly like one that was never told to `[GOV-SRC-040]`.
                tracing::warn!("echo-start: passage missed by {} ms; holding for the next schedule (assuming {was} ms of preparation, now {})",
                          by.as_millis(), self.echo_prep_ms);
                self.echo_start = None;
                return;
            }
            crate::echo::StartVerdict::Fire => {}
        }
        let Some((entry, start_sample, submit_at)) = self.echo_start.take() else { return };
        // **Hand the cut the target, not a lead** `[GDE-ARC-058]`. `submit_at`
        // is a device instant, one presentation offset before the sound
        // `[GDE-ECHO-410]`; `placement` wants the sound. Converting here and
        // deciding the depth there is what lets the depth absorb however long
        // the preparation below actually takes, instead of `echo_prep_ms`
        // having to predict it.
        let measured = self.path.ring.as_ref().and_then(|r| {
            (r.clock.timestamps() == crate::output::Timestamps::Hardware)
                .then(|| r.clock.delay_frames())
        });
        let (offset_frames, _) = self.echo_offset_frames(measured);
        let offset_ns = offset_frames.saturating_mul(1_000_000_000) / self.out_rate.max(1) as u64;
        self.echo_join_at = Some(submit_at.saturating_add(offset_ns));
        // Before `skip`, not after: `skip` admits the next passage itself, and
        // a resume offset arriving afterwards would apply to the one after it.
        let began = std::time::Instant::now();
        self.resume_at(start_sample.saturating_mul(1000) / self.out_rate.max(1) as u64);
        self.queue.push_front(entry);
        self.skip();
        self.queue_edited = true;
        // What that actually cost, folded in for next time. Weighted towards
        // history so a single slow seek moves the estimate rather than
        // replacing it.
        //
        // **It is a budget now, not a correction** `[GDE-ARC-058]`. The depth
        // computed at the cut already absorbs however long the work above
        // took, so this figure no longer has to be *right* -- only large
        // enough that `fire_echo_start` fires with time in hand to reach the
        // cut. Being wrong by 363 ms, as this fleet was (33-37 ms of real
        // preparation against an assumed 400), cost the join that much of its
        // placement before; now it costs nothing but a little extra lead.
        let took = (began.elapsed().as_millis() as u64).min(Self::ECHO_PREP_MAX_MS);
        let was = self.echo_prep_ms;
        self.echo_prep_ms = (was * 3 + took) / 4;
        tracing::info!("echo-start: preparing took {took} ms (was assuming {was}); firing {} ms early from now on", self.echo_prep_ms + self.skip_lead_ms);
    }

    /// This node's presentation offset: what the device reports plus what a
    /// listener calibrated `[GDE-ECHO-430]`, clamped at zero
    /// `[SPEC-DLY-030]`.
    ///
    /// Returns the clamp as a fact rather than hiding it. A node cannot sound
    /// before it submits, so a trim more negative than the measured delay is
    /// not a smaller number but an impossible one, and the panel says so
    /// instead of showing a value nobody chose.
    pub(crate) fn echo_offset_frames(&self, measured: Option<u64>) -> (u64, bool) {
        let rate = self.out_rate.max(1) as i64;
        let trim_frames = self.echo_delay_trim_ms.saturating_mul(rate) / 1000;
        let want = measured.unwrap_or(0) as i64 + trim_frames;
        if want < 0 { (0, true) } else { (want as u64, false) }
    }

    /// How many frames belong ahead of sample 0 for a commanded start to
    /// sound at `sound_at` `[GDE-ARC-058]`.
    ///
    /// **The durable fix `[GDE-ECHO-342]` named and did not take.** The ring
    /// cut used to lay the incoming passage a constant `skip_lead_ms` in, and
    /// `fire_echo_start` fired early by that plus `echo_prep_ms` to make up
    /// for it -- two predictions about a duration that had not happened yet,
    /// the second of which this fleet measured at 33-37 ms while assuming
    /// 400. Asking `placement()` at the moment of the cut replaces both with
    /// an answer: preparation has already happened by then, so however long
    /// it took is in `now` rather than in an estimate.
    ///
    /// The two failure verdicts are reported and then acted on, because a
    /// join has to do *something*:
    ///
    /// - `Late` -- the instant is gone, so sample 0 goes in at the head and
    ///   sounds as soon as the device will take it. A depth would only make
    ///   it later still.
    /// - `TooShallow` -- the target is further out than this ring can hold.
    ///   Clamped to the ring, which plays early `[Placement]`, and said out
    ///   loud so it is not read as drift.
    fn join_lead_frames(&self, sound_at: u64, now: u64) -> u64 {
        let measured = self.path.ring.as_ref().and_then(|r| {
            (r.clock.timestamps() == crate::output::Timestamps::Hardware)
                .then(|| r.clock.delay_frames())
        });
        let (offset_frames, _) = self.echo_offset_frames(measured);
        let timing = crate::echo::NodeTiming {
            presentation_offset_frames: offset_frames,
            rate: self.out_rate,
        };
        let capacity_frames = self.path.ring.as_ref()
            .map_or(0, |r| r.capacity() as u64 / self.out_channels.max(1) as u64);
        let sched = crate::echo::Schedule {
            passage_id: 0, start_sample: 0, sound_at, rate: self.out_rate,
        };
        match crate::echo::placement(&sched, timing, capacity_frames, now) {
            crate::echo::Placement::Depth(f) => f,
            crate::echo::Placement::TooShallow { short_by_frames } => {
                tracing::warn!("echo-start: the target is {} ms deeper than this ring holds; \
placing at the ring's own depth, which sounds early",
                          short_by_frames * 1000 / self.out_rate.max(1) as u64);
                capacity_frames
            }
            crate::echo::Placement::Late { by } => {
                tracing::warn!("echo-start: the target passed {} ms ago; sounding at once",
                          by.as_millis());
                0
            }
        }
    }

    /// Below this there is nothing worth correcting `[GDE-ECHO-350]`.
    ///
    /// Half a ppm is 1.8 ms an hour, comfortably inside what the offset
    /// correction sheds at a passage boundary, and below the precision the
    /// hour-long fit itself has `[LOG-P4-130]`. Trimming against an estimate
    /// finer than its own error is how a loop starts hunting.
    const ECHO_RATE_FLOOR_PPM: f64 = 0.5;

    /// And above this, something is wrong rather than fast.
    ///
    /// Crystals in this class are tens of ppm out, not hundreds
    /// `[LOG-P4-130]`. A larger figure means a broken fit, a clock step
    /// `[GDE-ECHO-365]`, or a residual series with a discontinuity nobody
    /// cleared -- and acting on it is audible, because the trim would then run
    /// at every mix pass. Clamping keeps a bad estimate from becoming a bad
    /// noise while the cause is found.
    const ECHO_RATE_CEILING_PPM: f64 = 100.0;

    /// Tell followers when a passage's first sample will reach the air
    /// `[GDE-ECHO-310]`.
    ///
    /// `depth_frames` is **where that sample actually sits**, not how much the
    /// ring happens to hold. Ordinarily they are the same -- a passage
    /// admitted the usual way goes in behind everything buffered. After a skip
    /// or a seek they are not: the ring is cut and the incoming passage is
    /// laid only `skip_lead_ms` in `[REQ-AUD-162]`, so it sounds in half a
    /// second where the ring still holds two.
    ///
    /// Getting that wrong is why a skip did not propagate `[GDE-ECHO-352]`:
    /// the schedule came from inside `admit_due`, which `skip` calls *before*
    /// cutting the ring, so the master announced a sound time some fourteen
    /// seconds later than the truth and every follower planned against it.
    fn publish_schedule(&mut self, passage_id: i64, origin_ms: u64, depth_frames: u64) {
        let Some(r) = self.path.ring.as_ref() else { return };
        if r.clock.timestamps() != crate::output::Timestamps::Hardware {
            return;
        }
        let Ok(d) = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
        else { return };
        let rate = r.sample_rate();
        let s = crate::echo::schedule_for_admission(
            passage_id,
            origin_ms.saturating_mul(rate as u64) / 1000,
            depth_frames,
            r.clock.delay_frames(),
            rate,
            d.as_nanos() as u64,
        );
        tracing::info!("echo-schedule: passage={} start_sample={} sound_at={} rate={} depth={}",
                  s.passage_id, s.start_sample, s.sound_at, s.rate, depth_frames);
        self.echo_schedule = Some(s);
    }

    /// Re-announce after a cut, from where the incoming passage really sits.
    ///
    /// A skip and a seek are the two places a passage's first sample is
    /// placed somewhere other than behind the whole ring, and both are user
    /// input a follower is meant to mirror `[GDE-ECHO-325]`.
    fn republish_after_cut(&mut self, lead_samples: usize) {
        let ch = self.out_channels.max(1);
        let rate = self.out_rate.max(1) as u64;
        if let Some((id, origin)) = self.live.first().map(|l| (l.entry.passage_id, l.origin_ms)) {
            // Announced a little FURTHER ON than this node's own audio
            // `[GDE-ECHO-355]`. A skip sounds here in `skip_lead_ms` -- half a
            // second -- and no follower can meet that: its own skip costs the
            // same lead plus its preparation, and it may wait half a second
            // more just to hear about it. A target it cannot reach is declined
            // as late and the skip does not propagate at all.
            //
            // The point announced is therefore a moment further into the same
            // passage, which `[GDE-ECHO-325]` is shaped for: *passage P,
            // sample S, at time T*. It still describes this node truthfully --
            // by T this node really is at S -- so nothing is fabricated, and
            // every follower has room to arrive.
            let margin = Self::ECHO_SKIP_ANNOUNCE_MARGIN_MS;
            let depth = (lead_samples / ch) as u64 + margin * rate / 1000;
            self.publish_schedule(id, origin + margin, depth);
        }
    }

    /// How far past its own audio a node announces a skip `[GDE-ECHO-355]`.
    ///
    /// It has to cover a follower's worst case: up to half a second to learn
    /// of the skip at the snapshot's cadence, its own `skip_lead_ms`, and its
    /// preparation. Two seconds clears that comfortably and sits well inside
    /// `[GDE-ECHO-325]`'s five-second allowance for a resync. Erring long
    /// costs nothing -- a follower that arrives early simply waits -- while
    /// erring short costs the whole skip.
    const ECHO_SKIP_ANNOUNCE_MARGIN_MS: u64 = 2_000;

    /// Split an offset correction into the coarse knob and the fine one
    /// `[GDE-ECHO-347]`.
    ///
    /// Returns `(admission nudge ms, origin ms)`, both applied to the next
    /// passage, together shifting when its content is heard by the requested
    /// amount.
    ///
    /// **Admission timing cannot do this alone.** The mixer only runs with at
    /// least one mix block of room, so an incoming passage's first sample lands
    /// on a mix-chunk boundary and the achievable shift is quantised to 46 ms
    /// at 44.1 kHz. Measured on the fleet 2026-09-18: the loop converged in two
    /// transitions and then dithered at exactly +/-1 chunk for half an hour,
    /// firing every transition, because the deadband it was asked to reach
    /// (40 ms) is *smaller than the smallest step it could take*.
    ///
    /// Opening the passage part-way in is not quantised. Skipping `o` of the
    /// content makes everything after it sound `o` earlier, to the
    /// millisecond, and a few tens of milliseconds is inside the lead-in where
    /// nothing has begun. It is one-directional -- there is no negative
    /// position -- which is why it is the *fine* knob and not the only one:
    /// to sound LATER, admission goes back a whole chunk and the overshoot is
    /// pulled forward again by the origin.
    fn echo_split(&self) -> Shift {
        let (overlap, ceiling) = self.next_transition_overlap();
        self.split_shift(self.echo_next_shift_ms, overlap, ceiling)
    }

    /// The overlap the transition now in prospect has to spend, and the most
    /// any nudge could ever ask of it.
    ///
    /// `(0, 0)` when there is no transition in prospect -- nothing sounding,
    /// or nothing queued -- which is the honest answer rather than a
    /// convenient one: there is no overlap to nudge.
    fn next_transition_overlap(&self) -> (u64, u64) {
        match (self.live.last(), self.queue.peek()) {
            (Some(l), Some(next)) => (
                crate::queue::overlap_ms(&l.entry, next),
                l.entry.duration_ms().min(next.duration_ms()),
            ),
            _ => (0, 0),
        }
    }

    /// The split itself: which actuator spends what.
    ///
    /// **Each direction gets the actuator that fits it** `[GDE-ARC-051]`.
    /// Coming in *earlier* overlaps more of the outgoing passage, which keeps
    /// every sample of the incoming one; admission is quantised to the mix
    /// block, so the origin carries only the sub-chunk remainder and never
    /// discards more than 46 ms. Coming in *later* is silence, which a
    /// contiguous ring requires `[GDE-ARC-052]` and which is exact at any
    /// size and costs no content at all.
    ///
    /// **No bite.** A transition is an alignment opportunity and it spends
    /// whatever exact placement takes. `OFFSET_MAX_BITE` existed to nibble
    /// at an error this places in one go.
    fn split_shift(&self, shift_ms: i64, overlap_ms: u64, ceiling_ms: u64) -> Shift {
        if shift_ms == 0 {
            return Shift::default();
        }
        let chunk = self.echo_chunk_ms().max(1);
        if shift_ms < 0 {
            // **Later: spend the overlap first, and only then silence**
            // `[GDE-ARC-070]`. This asked for the whole of it as silence,
            // which put a hole in the *outgoing* passage. Admission happens
            // `overlap` before that passage ends, and `mix_and_submit`
            // returns after emitting silence without mixing anything -- so
            // with a three-second overlap the ring became
            // `[outgoing ...][500 ms of silence][the outgoing's last 3 s]`:
            // a half-second hole three seconds before the end of a track
            // that then carries on playing. The comment there promising "the
            // passage arrives whole" is true of the incoming passage and was
            // false for the one still sounding.
            //
            // Narrowing the overlap is the same instrument the "earlier"
            // direction uses, in reverse, and it costs nothing: admission
            // simply happens later. Spending it first means admission lands
            // at the outgoing passage's true end -- `should_admit_nudged`
            // then fires on `remaining <= 0` -- so any silence that remains
            // is emitted with nothing else left to mix, which is where a gap
            // belongs and where `[GDE-ARC-052]`'s 20 ms fade-out argument
            // actually applies.
            //
            // On this library's 5 ms median overlap almost all of it is
            // still silence; on the multi-second lead-outs the codebase
            // calls the rare-but-wanted case, almost none of it is.
            let want = shift_ms.unsigned_abs();
            let by_overlap = want.min(overlap_ms);
            return Shift {
                admit_ms: -(by_overlap as i64),
                origin_ms: 0,
                gap_ms: want - by_overlap,
            };
        }
        // Earlier: overlap in whole blocks, and let the origin take only what
        // admission cannot express. Clamped to the overlap the pair can
        // actually sustain, which on a long pair is seconds.
        let want = shift_ms as u64;
        let by_admission = (want / chunk * chunk).min(ceiling_ms.saturating_sub(overlap_ms));
        // **The origin is bounded even when the ceiling binds**
        // `[GDE-ARC-068]`. `origin_ms = want - by_admission` is unbounded as
        // written: on a pair short enough for the `.min()` above to clamp,
        // it would discard arbitrarily much of the incoming passage's head
        // to make the arithmetic add up -- against a doc comment two lines
        // up promising it "never discards more than 46 ms", and the two
        // tests asserting that invariant both used a 240 s ceiling where the
        // clamp can never bind. The invariant was asserted only where it
        // could not fail.
        //
        // Capped at one block, and what neither actuator can reach is left
        // undelivered on purpose: `admit_due` sends the shortfall to the
        // frame trim `[GDE-ARC-057]`, which is inaudible, where discarding a
        // second of a track is not.
        let origin_ms = (want - by_admission).min(chunk.saturating_sub(1));
        Shift {
            admit_ms: by_admission as i64,
            origin_ms,
            gap_ms: 0,
        }
    }

    /// What a `Shift` actually moves the air by. Positive is earlier.
    ///
    /// **The assertion this subsystem did not have** `[GDE-ECHO-378]`. It
    /// reads the same clamp the actuator reads `[spend_overlap_ms]`, so a
    /// test can follow a correction from the decision all the way to what
    /// admission will really do with it -- which is the one boundary where
    /// every fault in `[GDE-ECHO-351]` and `[GDE-ECHO-372]` lived.
    fn delivered_shift_ms(s: Shift, overlap_ms: u64, ceiling_ms: u64) -> i64 {
        let spent = crate::queue::spend_overlap_ms(overlap_ms, ceiling_ms, s.admit_ms) as i64;
        (spent - overlap_ms as i64) + s.origin_ms as i64 - s.gap_ms as i64
    }

    /// What the transition **actually** achieved, from the outgoing passage's
    /// real remaining time at the instant admission fired `[GDE-ARC-057]`.
    ///
    /// `delivered_shift_ms` above answers a different question than it looks
    /// like it answers. It reads the same clamp the actuator reads, so it is
    /// the right tool for *testing the decision* -- but it is computed from
    /// the request, and `should_admit_nudged` fires on `remaining <= overlap +
    /// nudge`, which can only be reached at or **after** the instant it wants.
    /// A correction issued when less than `overlap + nudge` of the outgoing
    /// passage is left is truncated to whatever was left, and the arithmetic
    /// above reports the full figure regardless.
    ///
    /// That made `echo-offset: ... = 565 ms, 0 ms left to the frame trim`
    /// unfalsifiable: it is a restatement of the request, not an observation.
    /// Which end was wrong could not be told from inside the engine, because
    /// nothing in it measured the achieved figure `[GDE-ARC-043]`.
    ///
    /// **What it then measured was that admission is nearly honest**, which
    /// is not what this was written expecting: deployed on `lp3-wifi`
    /// 2026-09-19, asks of 547, 239 and 108 ms achieved 534, 232 and 100,
    /// with hundreds of milliseconds of the outgoing passage still in hand.
    /// The truncation is real and is one mix block at worst. A larger gap
    /// between the moved start and the measured alignment survives it and is
    /// unexplained -- `[GDE-ARC-057]` in GUIDE027 §3 holds the open question
    /// rather than a third guess at it.
    ///
    /// `remaining_ms` is the outgoing passage's own remaining play time when
    /// admission fired. Without a nudge that instant is `overlap_ms`, so the
    /// shift the admission really bought is the difference.
    fn achieved_shift_ms(s: Shift, remaining_ms: u64, overlap_ms: u64) -> i64 {
        (remaining_ms as i64 - overlap_ms as i64) + s.origin_ms as i64 - s.gap_ms as i64
    }

    /// One mix chunk, in milliseconds -- the resolution of admission timing.
    fn echo_chunk_ms(&self) -> u64 {
        Self::MIX_FRAMES as u64 * 1000 / self.out_rate.max(1) as u64
    }

    /// How hard a position debt may be paid off, in ppm `[GDE-ECHO-349]`.
    ///
    /// 100 ppm is one part in ten thousand of timing -- inaudible as pitch by
    /// a wide margin, and about 4.4 frames a second, so each 23 us splice is
    /// far enough from the last not to read as roughness. It clears a whole
    /// mix quantum in about eight minutes, which is the right speed for an
    /// endgame: the boundary knobs have already taken everything larger.
    ///
    /// Raising this is the obvious way to converge faster and the obvious way
    /// to make it audible. It wants a listening test, not an argument.
    const ECHO_DEBT_PPM: f64 = 100.0;

    /// Whether a frame is due to be trimmed, and which way.
    ///
    /// `Some(true)` drops -- this node is behind and must advance faster.
    ///
    /// Rate and position share one actuator, so they are summed into one
    /// interval rather than run as two timers that would double the splice
    /// rate and fight over direction `[GDE-ECHO-349]`.
    fn due_trim(&self) -> Option<bool> {
        let debt_ppm = if self.echo_debt_frames > 0 {
            Self::ECHO_DEBT_PPM
        } else if self.echo_debt_frames < 0 {
            -Self::ECHO_DEBT_PPM
        } else {
            0.0
        };
        let effective = self.echo_rate_ppm + debt_ppm;
        let interval = crate::echo::trim_interval(
            effective, self.out_rate, Self::ECHO_RATE_FLOOR_PPM)?;
        // The clock starts when the rate does, so the first trim waits a full
        // interval rather than firing the instant an estimate arrives.
        let due = self.echo_last_trim.is_some_and(|t| t.elapsed() >= interval);
        due.then_some(effective > 0.0)
    }

    /// Hold the ring below capacity so this node's submit-to-air total matches
    /// the fleet's `[LOG-ECHO-030]`.
    ///
    /// Both offsets are **calibrated** presentation offsets `[GDE-ECHO-430]`,
    /// never the live `delay_frames()` reading. That distinction is not
    /// fastidiousness: `lempiplay3` reports a delay that wanders 9.84 ms while
    /// its sound holds to 1.72 us `[LOG-P4-100]`, so driving the ring depth
    /// from the live figure would inject nearly ten milliseconds of movement
    /// that is not otherwise there -- manufacturing the very error this exists
    /// to remove.
    ///
    /// A node at the fleet minimum gets zero and fills to capacity. Nothing
    /// calls this yet: the fleet minimum has to reach a node before it can be
    /// used, and that is the roster question `[GDE-ECHO-450]`, not this one.
    pub(crate) fn set_echo_depth(&mut self, own_offset_frames: u64, fleet_min_offset_frames: u64) {
        // An offset below the stated minimum means the caller's roster is
        // wrong, not that this node should run deeper than capacity. Clamp,
        // because the alternative is an underflow that reads as a colossal
        // shortfall and silences the node.
        let excess = own_offset_frames.saturating_sub(fleet_min_offset_frames);
        self.echo_depth_shortfall = (excess as usize).saturating_mul(self.out_channels.max(1));
    }

    /// Frames this node may add, given a ring it must not fill completely.
    ///
    /// `free` is `capacity - buffered`, so `free - shortfall` is exactly
    /// `target_depth - buffered` for `target_depth = capacity - shortfall`.
    /// The subtraction is why holding a reduced depth costs nothing at
    /// runtime: the cached free figure is already there, and a constant comes
    /// off it. No second lock, no extra read of the ring
    /// `[GDE-FBD-010]`.
    fn submit_room_frames(free: usize, shortfall: usize) -> usize {
        free.saturating_sub(shortfall)
    }

    fn mix_and_submit(&mut self) -> usize {
        // Room is remembered from the last submit rather than asked for again.
        // Between then and now the callback only ever DRAINS, so the remembered
        // figure is a lower bound on what is free and writing it always fits --
        // which is what keeps the assertion below honest. Asking cost a second
        // lock acquisition on every pass, against a callback that must never
        // wait for one.
        let room = match &self.path.ring {
            Some(o) => {
                // Refreshed whenever the remembered figure is too small to act
                // on. It only ever GROWS between submits, so a stale value that
                // is already large enough needs no confirmation -- but one
                // below the threshold must be re-read, or a ring that filled up
                // once would never be topped up again.
                // Refreshed on the room this node may actually USE, not on
                // the raw free space. With a shortfall held back, those differ,
                // and refreshing on the raw figure would let a capped ring sit
                // forever: `out_room` stays above the threshold, the effective
                // room stays below it, the early return fires every pass and
                // nothing ever submits to update the cache.
                if Self::submit_room_frames(self.out_room, self.echo_depth_shortfall)
                    < self.min_submit()
                {
                    self.out_room = o.free();
                }
                Self::submit_room_frames(self.out_room, self.echo_depth_shortfall)
            }
            None => self.scratch.len(),
        };
        // Whole frames only; a partial frame would offset every later sample.
        // Capped at the block rather than at `scratch`, which is a frame
        // longer on purpose: that frame is where a duplicate goes, and mixing
        // into it would leave `apply_trim` nowhere to put one `[GDE-ECHO-373]`.
        let ch = self.out_channels.max(1);
        let want = room.min(self.min_submit()) / ch * ch;
        // Don't wake the whole chain to move a handful of samples. The ring
        // holds ~14 s, so there is nothing to gain by topping it up the instant
        // a few samples drain, and a great deal to lose: mixing whatever had
        // appeared since the last pass meant hundreds of passes a second, each
        // taking the output lock, on a machine with four slow cores. The
        // decoders already pace themselves this way `[DECODE_TOPUP_FRAMES]`.
        if want == 0 || (want < self.min_submit() && self.path.ring.is_some()) {
            return 0;
        }

        // **Silence first, and the streams untouched behind it**
        // `[GDE-ARC-052]`. A follower running ahead of the master cannot wait
        // by waiting: the ring is contiguous, so a pause in submission moves
        // no audio at all. It waits by putting silence in front of the
        // passage. Nothing is mixed on these passes, so the decoded audio
        // stays in each stream's own ring and the passage arrives whole.
        if self.echo_gap_frames > 0 {
            let frames = (want / ch).min(self.echo_gap_frames as usize);
            let n = frames * ch;
            self.scratch[..n].fill(0.0);
            self.echo_gap_frames -= frames as u64;
            if self.echo_gap_frames == 0 {
                tracing::info!("echo-gap: silence spent; the passage starts now");
            }
            return match &self.path.ring {
                Some(o) => {
                    let (taken, free_after) = o.submit(&self.scratch[..n]);
                    self.out_room = free_after;
                    taken
                }
                None => n,
            };
        }

        let before: Vec<usize> = self.live.iter().map(|l| l.stream.ring.len()).collect();
        let filled = mix(
            self.live.iter_mut().map(|l| &mut l.stream),
            &mut self.scratch[..want],
        );
        // The rate correction `[GDE-ECHO-340]`, on the mixer thread where the
        // audio is, and nowhere near the callback. One frame, at the interval
        // the fitted ppm calls for -- which IS the rate limit `[GDE-ECHO-350]`,
        // so there is no second governor to disagree with it.
        let filled = match self.due_trim() {
            Some(drop_frame) => {
                // **A duplicate needs room in the output too, not only in the
                // block** `[GDE-ECHO-373]`. `scratch` carries the extra frame
                // so `apply_trim` can write it; the ring is a separate
                // question, and the mixer runs whenever ONE block is free, so
                // a ring with exactly one block free is the ordinary steady
                // state rather than a corner. Hand it a block plus a frame and
                // it takes the block: a frame dropped where the trim meant to
                // add one, which is the correction backwards at twice the
                // size. `room` is a lower bound on what is free -- the
                // callback only ever drains -- so this is sufficient.
                let after = if drop_frame || room >= filled + ch {
                    crate::mixer::apply_trim(&mut self.scratch, filled, ch, drop_frame)
                } else {
                    filled
                };
                if after == filled {
                    // **Refused, and it says so** `[GDE-ECHO-378]`. A block
                    // too small to drop from, or one this pass has no room to
                    // duplicate into: nothing moved, so nothing is spent and
                    // nothing is credited, and the trim keeps its turn. An
                    // actuator that declines silently is the whole shape of
                    // `[GDE-ECHO-351]`, so this is said once per episode
                    // rather than at the mixer's own cadence.
                    if !self.echo_trim_refused {
                        self.echo_trim_refused = true;
                        tracing::info!("echo-trim: a {} was refused on a {filled}-sample block; nothing credited, retrying",
                                  if drop_frame { "drop" } else { "duplicate" });
                    }
                } else {
                    self.echo_trim_refused = false;
                    // The clock restarts only when a frame actually moved: a
                    // trim skipped now must happen a few milliseconds later,
                    // not be counted as already done.
                    self.echo_last_trim = Some(std::time::Instant::now());
                    // One frame of the debt, whichever job the trim was doing
                    // -- credited by the frame that moved rather than by the
                    // intention to move one `[GDE-ECHO-373]`.
                    if self.echo_debt_frames != 0 {
                        self.echo_debt_frames -= self.echo_debt_frames.signum();
                    }
                }
                after
            }
            None => filled,
        };
        for (l, was) in self.live.iter_mut().zip(before) {
            let consumed = was.saturating_sub(l.stream.ring.len());
            l.frames_mixed += (consumed / l.stream.channels.max(1)) as u64;
        }
        if filled == 0 {
            return 0;
        }
        match &self.path.ring {
            Some(o) => {
                let (taken, free_after) = o.submit(&self.scratch[..filled]);
                debug_assert_eq!(taken, filled, "output accepted less than it reported free");
                self.out_room = free_after;
                taken
            }
            None => filled, // discard sink: report accepted so callers advance
        }
    }

    fn retire_finished(&mut self) {
        // **What it had mixed as it left** `[REQ-VIS-240]`. The listener has
        // not heard the last of it -- a ring's depth of it is still queued for
        // the device -- and `advance_shown` needs this to keep the clock
        // moving over that window.
        for l in self.live.iter().filter(|l| l.stream.is_exhausted()) {
            // The audible position at the moment it stopped being mixed, and
            // the moment itself. Everything after this is arithmetic on the
            // clock.
            self.draining = Some((l.entry.passage_id, self.audible_ms(l), Instant::now()));
        }
        self.live.retain(|l| !l.stream.is_exhausted());
    }

    /// Position within the passage of the audio that has been MIXED. Drives
    /// crossfade admission, which must lead what is heard by the buffer depth.
    fn played_ms(&self, l: &Live) -> u64 {
        l.origin_ms + l.frames_mixed * 1000 / self.out_rate.max(1) as u64
    }

    /// Position within the passage of the audio being HEARD: mixed, less what
    /// is still sitting in the output ring. This is what the UI shows and what
    /// a resume point must record.
    fn audible_ms(&self, l: &Live) -> u64 {
        let frames = self.out_buffered_frames() as u64;
        self.played_ms(l)
            .saturating_sub(frames * 1000 / self.out_rate.max(1) as u64)
    }

    fn out_buffered_frames(&self) -> usize {
        self.path.ring.as_ref().map(|r| r.buffered() / self.out_channels.max(1)).unwrap_or(0)
    }

    /// Bookkeeping that must keep up with the mixer, separate from writing the
    /// snapshot that anyone reads.
    ///
    /// Split because the two have completely different natural rates: this
    /// tracks what is *audible*, which changes with the ring, while the
    /// snapshot serves browsers polled twice a second.
    fn advance_shown(&mut self) {
        // Attribute the increment before publishing: silence during a pause is
        // expected, silence during playback is the bug worth reporting.
        let (raw, misses) = self.path.ring.as_ref().map(|r| r.diagnostics()).unwrap_or((0, 0));
        let delta = raw.saturating_sub(self.last_raw_underruns);
        self.last_raw_underruns = raw;
        if self.playing {
            self.underruns_playing += delta;
        }
        // Log each one as it happens, so a glitch someone HEARS can be matched
        // against a glitch the player recorded. These were assumed inaudible
        // on the strength of a percentage; they are not, and the way to stop
        // guessing about the remainder is to timestamp them.
        if misses > self.last_lock_failures {
            tracing::warn!("output: {} missed ring lock(s), {} total",
                      misses - self.last_lock_failures, misses);
            self.last_lock_failures = misses;
        }
        // A passage becomes "playing" when its first sample leaves the ring for
        // the device, not when the mixer starts on it -- those are ~14 s apart
        // [REQ-AUD-164]. `frames_mixed` against the ring depth is the test, and
        // it is deliberately in FRAMES rather than milliseconds of position: a
        // resumed passage starts at a non-zero position and would otherwise
        // announce itself the instant it was admitted.
        let ring = self.out_buffered_frames() as u64;
        if let Some(l) = self.live.iter().rev().find(|l| l.frames_mixed > ring) {
            self.shown = Some((l.entry.clone(), self.audible_ms(l)));
            // **And a basis voided by a cut comes back here** `[GDE-ARC-066]`.
            // `skip` is re-established by the `admit_due` inside it, at the
            // cut; `seek_to` admits nothing, so when `[GDE-ARC-064]` gave it
            // the voiding it was missing it had no way back and the node went
            // silent to every follower until its next passage boundary.
            //
            // This is the honest moment for either of them: the passage is
            // genuinely sounding, its position is derived the ordinary way,
            // and nothing about the discarded audio is being compared across.
            // Only a cut is recovered from -- an underrun, a device reopen or
            // a pause are about the frame clock itself and are not mended by
            // a passage becoming audible `[GDE-ECHO-360]`.
            if self.echo_basis.voided_by() == Some(crate::echo::Voided::Skip) {
                self.echo_basis.establish();
            }
            // **The one place that knows `shown` is really sounding**
            // `[GDE-ARC-059]`. `skip` sets `shown` eagerly so the button stays
            // honest, and for the lead after a cut that claim runs ahead of
            // the audio. Recording which branch set it, rather than re-deriving
            // the same `frames_mixed > ring` test elsewhere, keeps one model of
            // one quantity `[GDE-ARC-033]`.
            self.shown_is_sounding = true;
        } else if let Some((entry, _)) = self.shown.clone() {
            // Still audible though no longer mixed: keep it, and keep its
            // position moving, rather than blanking the display mid-passage.
            let pos = self
                .live
                .iter()
                .find(|l| l.entry.passage_id == entry.passage_id)
                .map(|l| self.audible_ms(l))
                // Gone from `live` but still sounding: what it had mixed, less
                // what is still queued behind it. As the ring drains this
                // advances to the end of the passage on its own
                // `[REQ-VIS-240]`.
                .or_else(|| match self.draining {
                    Some((id, at, since)) if id == entry.passage_id => {
                        // Capped at the passage's own end: the clock must not
                        // run past the music, however long it sits there.
                        let moved = at + since.elapsed().as_millis() as u64;
                        Some(moved.min(entry.duration_ms()))
                    }
                    _ => None,
                });
            if let Some(p) = pos {
                self.shown = Some((entry, p));
            }
        }
    }

    /// Write the snapshot everything else reads.
    fn publish(&mut self) {
        self.published = self.shown.as_ref().map(|(e, _)| e.passage_id);
        // Built before taking the lock: `air_position` reads the ring and the
        // live list, and holding the snapshot mutex across that would put the
        // engine's own state behind the lock a browser thread waits on.
        let echo = crate::echo::EchoState {
            anchor: if self.echo_basis.is_valid() {
                // None, not 0.0: nothing here measures the master's own rate
                // error yet, and saying zero would be a claim `[GOV-SRC-040]`.
                self.air_position().map(|a| a.anchor(self.out_rate, None))
            } else {
                None
            },
            schedule: self.echo_schedule,
            voided_by: self.echo_basis.voided_by(),
        };
        if let Ok(mut s) = self.state.lock() {
            s.echo = echo;
            // Rebuilt each publish rather than cached: the measured half moves
            // on its own `[LOG-P4-080]`, and a stale copy would show a delay
            // the device stopped reporting minutes ago.
            let measured = self.path.ring.as_ref().and_then(|r| {
                (r.clock.timestamps() == crate::output::Timestamps::Hardware)
                    .then(|| r.clock.delay_frames())
            });
            let (offset_frames, clamped) = self.echo_offset_frames(measured);
            s.echo_node = EchoNode {
                trim_ms: self.echo_delay_trim_ms,
                trim_limit_ms: crate::db::ECHO_TRIM_LIMIT_MS,
                measured_frames: measured,
                offset_frames,
                clamped,
                rate: self.out_rate,
                follow_host: self.echo_follow_host.clone(),
                join_now: self.echo_join_now,
                join_bias_ms: self.echo_join_bias.correction_ms(),
                // Written by the follower task, which is the only thing that
                // knows; left as it found it here.
                follow_status: s.echo_node.follow_status.clone(),
                start_lead_ms: self.echo_start_lead_ms(),
            };
            s.playing = self.playing;
            s.current = self.shown.as_ref().map(|(e, _)| e.clone());
            s.position_ms = self.shown.as_ref().map(|(_, p)| *p).unwrap_or(0);
            // What is still to come FOR THE LISTENER `[REQ-AUD-164]`. A
            // passage leaves the queue when the mixer admits it, which is up to
            // a ring's depth before anyone hears it -- so the next passage
            // used to vanish from "Coming up" while the current one was still
            // playing. Anything admitted but not yet audible belongs at the
            // top of the list, not gone from it.
            let shown_id = self.shown.as_ref().map(|(e, _)| e.passage_id);
            let after = match self.live.iter().position(|l| Some(l.entry.passage_id) == shown_id) {
                Some(i) => i + 1,
                // The heard passage has finished mixing, so everything still
                // in `live` is ahead of the listener.
                None => 0,
            };
            let pending = self.live.iter().skip(after).map(|l| l.entry.clone());
            s.queue_len = self.live.len().saturating_sub(after) + self.queue.len();
            s.queue = pending
                .chain(self.queue.iter().cloned())
                .take(crate::QUEUE_SHOWN)
                .collect();
            s.mixing_ahead = self.live.len().saturating_sub(after);
            s.volume = self.volume;
            s.skip_fade_ms = self.skip_fade_ms;
            s.skip_lead_ms = self.skip_lead_ms;
            s.resume_save_ms = self.resume_save_ms;
            // Every listener setting, published. These were declared on
            // `PlayerState` and read by the settings page but never filled, so
            // the page would have offered a confident **0 hours** for both
            // suppression windows — a control showing a value the engine does
            // not hold is worse than one showing nothing.
            s.skip_suppress_h = self.skip_suppress_h;
            s.dequeue_suppress_h = self.dequeue_suppress_h;
            s.queue_depth = self.queue.min_depth;
            s.sample_interval_ms = self.sample_interval_ms;
            s.cue_sheets = self.cue_sheets;
            s.covers = self.covers;
            s.lyrics_cache = self.lyrics_cache;
            s.lyrics_sidecar = self.lyrics_sidecar;
            s.active_streams = self.live.len();
            s.underrun_samples = self.underruns_playing;
            s.underruns_since_reset =
                self.underruns_playing.saturating_sub(self.underrun_baseline);
            s.underruns_since = self.underrun_since;
            s.lock_failures = self.path.ring.as_ref().map_or(0, |r| r.diagnostics().1);
            s.out_recoveries = self.path.recoveries();
            s.output_buffered =
                self.path.ring.as_ref().map(|r| r.buffered()).unwrap_or(0);
        }
    }
}


/// Save on the way out, wherever "out" happens to be. Callers exit by several
/// routes -- a Shutdown command, an empty queue, or simply dropping the engine
/// -- and putting the final save in each of them would be three chances to
/// forget one. The periodic save is up to `resume_save_ms` stale, so this is what
/// keeps a clean exit from costing those seconds.
impl Drop for Engine {
    fn drop(&mut self) {
        self.persist(true);
    }
}

/// An `Instant` `secs` in the past -- or, on a machine that has not been up
/// that long, the earliest one this process can name.
///
/// `Instant` counts from boot, and `Instant - Duration` **panics** on
/// underflow rather than saturating. Six tests below set `echo_last_trim` in
/// the past this way, and on 2026-09-21 the 3600 s one failed on a machine
/// six minutes into its uptime: `overflow when subtracting duration from
/// instant`, which reads as an engine fault rather than as a test that cannot
/// run here. The 2 s and 10 s sites have the same defect and were passing on
/// nothing but uptime.
///
/// Saturating to *now* is the safe direction. Where the value is only
/// decoration -- `due_trim` returns `None` at the rate floor before it ever
/// reads `echo_last_trim` -- nothing changes. Where the elapsed time is what
/// the assertion turns on, a saturated clock reports "not due" and the test
/// **fails**, loudly, instead of passing for a reason nobody chose
/// `[GDE-ECHO-547]`.
#[cfg(test)]
fn ago(secs: u64) -> std::time::Instant {
    std::time::Instant::now()
        .checked_sub(std::time::Duration::from_secs(secs))
        .unwrap_or_else(std::time::Instant::now)
}

#[cfg(test)]
mod depth_tests {
    use super::{ago, Command, Engine};

    /// `[SPEC-DLY-030]`: a node cannot sound before it submits, so a trim
    /// more negative than the measured delay is impossible rather than small.
    /// The clamp is reported so the panel can say so.
    #[test]
    fn a_trim_past_the_measured_delay_clamps_and_says_it_did() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        e.echo_delay_trim_ms = -40;
        assert_eq!(e.echo_offset_frames(Some(2043)), (279, false), "46 ms less 40 leaves 6");
        e.echo_delay_trim_ms = -100;
        assert_eq!(e.echo_offset_frames(Some(2043)), (0, true), "further back than zero");
        // With nothing measured the trim IS the offset `[GDE-ECHO-430]`.
        e.echo_delay_trim_ms = 10;
        assert_eq!(e.echo_offset_frames(None), (441, false));
    }

    /// The trim fires at the interval the rate calls for, in the direction the
    /// sign calls for, and not before `[GDE-ECHO-340]`.
    #[test]
    fn a_trim_waits_its_interval_and_knows_which_way() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        assert_eq!(e.due_trim(), None, "no rate, no trim");

        h.send(Command::SetEchoRate(13.92));
        e.tick();
        // The clock starts with the rate: nothing is due in the first instant.
        assert_eq!(e.due_trim(), None, "an arriving rate is not a debt to pay at once");
        // ...but once an interval has passed, it is, and this node is behind.
        e.echo_last_trim = Some(ago(2));
        assert_eq!(e.due_trim(), Some(true), "behind: drop a frame to catch up");

        // The other way round.
        h.send(Command::SetEchoRate(-13.92));
        e.tick();
        e.echo_last_trim = Some(ago(2));
        assert_eq!(e.due_trim(), Some(false), "ahead: repeat a frame to wait");
    }

    /// `[GDE-ECHO-347]`'s quantum, which still governs the admission half.
    ///
    /// *Superseded in part 2026-09-19 `[GDE-ARC-051]`.* This used to assert
    /// that a coarse backstep and a fine origin summed to the request in
    /// both directions. The "later" direction no longer uses either -- a
    /// contiguous ring cannot be waited into, so it is silence now
    /// `[GDE-ARC-052]` -- and the exactness claim for both directions lives
    /// in `a_transition_places_the_passage_exactly_in_both_directions`. What
    /// remains here is still true and still load-bearing: admission moves in
    /// whole mix blocks, so the origin carries the remainder and never
    /// discards more than one block of the passage.
    #[test]
    fn admission_moves_in_whole_blocks_and_the_origin_takes_the_remainder() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        e.out_channels = 2;
        let chunk = e.echo_chunk_ms();
        assert_eq!(chunk, 46, "one mix block at 44.1 kHz");
        const ROOMY: u64 = 3_000;
        const CEIL: u64 = 240_000;

        assert_eq!(e.split_shift(0, ROOMY, CEIL), super::Shift::default(),
                   "nothing asked, nothing done");

        // 43 ms is the figure the fleet dithered on for half an hour: less
        // than a block, so admission cannot express it and the origin takes
        // all of it.
        let s = e.split_shift(43, ROOMY, CEIL);
        assert_eq!((s.admit_ms, s.origin_ms), (0, 43));

        for ask in [46_i64, 100, 448, 2_000] {
            let s = e.split_shift(ask, ROOMY, CEIL);
            assert_eq!(s.admit_ms % chunk as i64, 0, "admission is whole blocks");
            assert!(s.origin_ms < chunk, "the origin never discards a whole block");
            assert_eq!(Engine::delivered_shift_ms(s, ROOMY, CEIL), ask);
        }

        // With nothing sounding there is no transition and no overlap, so
        // the admission half has nothing to spend and says so.
        e.echo_next_shift_ms = 100;
        assert_eq!(e.echo_split().admit_ms, 0);
    }

    /// **The origin's bound holds where the ceiling binds, which is the only
    /// place it could fail** `[GDE-ARC-068]`.
    ///
    /// `split_shift` promises the origin "never discards more than one mix
    /// block". Both tests asserting that used a 240-second ceiling, where
    /// the clamp on admission can never engage — so the invariant was
    /// checked only in the case that cannot break it. With a short pair the
    /// clamp binds, and `want - by_admission` was unbounded: a 2 s
    /// correction against a 100 ms ceiling discarded 1.9 s of the incoming
    /// passage's head.
    #[test]
    fn a_short_pair_cannot_make_the_origin_eat_the_passage() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        e.out_channels = 2;
        let chunk = e.echo_chunk_ms();

        // A 2 s correction where the two passages can sustain only 100 ms of
        // overlap between them.
        let s = e.split_shift(2_000, 0, 100);
        assert!(s.origin_ms < chunk,
                "the origin discarded {} ms of the track to balance the books",
                s.origin_ms);
        assert!(s.admit_ms <= 100, "admission cannot exceed what the pair has");
        // The rest is deliberately undelivered here: `admit_due` owes it to
        // the frame trim, which is inaudible `[GDE-ARC-057]`.
        let delivered = Engine::delivered_shift_ms(s, 0, 100);
        assert!(delivered < 2_000, "and it must not pretend to have placed it all");

        // The ordinary case is unchanged: a roomy pair still splits into
        // whole blocks plus a sub-block remainder.
        let roomy = e.split_shift(2_000, 0, 240_000);
        assert_eq!(roomy.admit_ms % chunk as i64, 0);
        assert!(roomy.origin_ms < chunk);
        assert_eq!(Engine::delivered_shift_ms(roomy, 0, 240_000), 2_000);
    }

    /// **The silence must not land inside the passage that is still
    /// playing** `[GDE-ARC-070]`.
    ///
    /// Found by a reviewer probing the mechanism rather than the tests.
    /// `admit_due` arms the gap at admission, which in the "later" direction
    /// is `overlap` ms *before* the outgoing passage ends, and
    /// `mix_and_submit` returns after emitting silence without mixing
    /// anything. With a three-second overlap the ring became
    /// `[outgoing ...][silence][the outgoing's last three seconds]` — a hole
    /// punched into a track that then carried on playing, measured at 2995
    /// ms before its end.
    ///
    /// Spending the overlap first fixes it at the source: admission then
    /// lands on `remaining <= 0`, so whatever silence is left is emitted
    /// with nothing else to mix. On this library's 5 ms overlap that is
    /// nearly all of it; on a multi-second lead-out it is none of it.
    #[test]
    fn a_wait_does_not_punch_a_hole_in_the_passage_still_sounding() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        e.out_channels = 2;

        // The rare-but-wanted case the codebase names: a long crossfade.
        for (overlap, want) in [(3_000u64, 500u64), (5_000, 1_200)] {
            let s = e.split_shift(-(want as i64), overlap, 240_000);
            assert_eq!(s.gap_ms, 0,
                       "with {overlap} ms of overlap to narrow, none of a {want} ms wait needs to be silence inside the outgoing passage");
            assert_eq!(s.admit_ms, -(want as i64), "it waits by admitting later");
            assert_eq!(Engine::delivered_shift_ms(s, overlap, 240_000), -(want as i64));
        }

        // And where the overlap genuinely cannot cover it, what is left is
        // silence — but only what is left, and admission has by then been
        // pushed to the outgoing passage's own end.
        let tight = e.split_shift(-448, 5, 240_000);
        assert_eq!((tight.admit_ms, tight.gap_ms), (-5, 443));
        assert_eq!(crate::queue::spend_overlap_ms(5, 240_000, tight.admit_ms), 0,
                   "admission is pushed to `remaining <= 0`, so nothing of the outgoing passage is left to be interrupted");
    }

    /// **A shift the outgoing passage was too short to give is reported as
    /// what happened, not as what was asked** `[GDE-ARC-057]`.
    ///
    /// `should_admit_nudged` fires on `remaining <= overlap + nudge`, so a
    /// correction that arrives with less than that left is truncated to
    /// whatever was left. `delivered_shift_ms` cannot see this -- it is
    /// computed from the request -- so the log said `= 565 ms, 0 ms left to
    /// the frame trim` for corrections that measurably moved the node 269 ms.
    /// A claim that restates its own input is not a measurement
    /// `[GDE-ARC-043]`.
    ///
    /// Here 400 ms is asked for with only 120 ms of the outgoing passage
    /// left against a 20 ms overlap, so the transition can buy 100 ms and the
    /// other 300 must reach the actuator that can still deliver it.
    #[test]
    fn a_shift_the_transition_was_too_late_for_is_owed_not_claimed() {
        let s = super::Shift { admit_ms: 368, origin_ms: 32, gap_ms: 0 };
        // What the request implies, which is the whole of it.
        assert_eq!(Engine::delivered_shift_ms(s, 20, 300_000), 400);
        // What actually happened, with the passage nearly over.
        assert_eq!(Engine::achieved_shift_ms(s, 120, 20), 132,
                   "100 ms of admission was all that was left, plus the origin");
        // And when the transition is reached in good time the two agree, or
        // the new figure would be a second model of the same quantity
        // `[GDE-ARC-033]`.
        assert_eq!(Engine::achieved_shift_ms(s, 20 + 368, 20), 400,
                   "admission on time must still report the full shift");
        // The later direction is silence and owes nothing to admission, so
        // the outgoing passage's remaining time cannot truncate it.
        let later = super::Shift { admit_ms: 0, origin_ms: 0, gap_ms: 250 };
        assert_eq!(Engine::achieved_shift_ms(later, 20, 20), -250,
                   "silence is spent at mix time and is not the boundary's to clip");
    }

    /// **`admit_due` must owe against what it ACHIEVED, not what it asked**
    /// `[GDE-ARC-057]`.
    ///
    /// The pure function `achieved_shift_ms` was unit-tested; nothing
    /// asserted that `admit_due` called it, and a reviewer showed the whole
    /// of commit `13506f3` could be reverted with the suite still green. The
    /// assertion meant to cover it had been widened to `0 <= owed < one mix
    /// block`, and the pre-fix behaviour produces `owed == 0`, which is
    /// inside that range. A range containing both answers distinguishes
    /// neither.
    ///
    /// This drives the case the two computations genuinely disagree on: a
    /// correction arriving with less of the outgoing passage left than it
    /// wants to spend. `delivered` reads the request and says it was all
    /// placed; `achieved` reads the passage and says what was really
    /// available. The difference must reach the trim.
    #[test]
    fn what_the_transition_could_not_reach_is_owed_against_the_measurement() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        e.out_channels = 2;

        // Asked for 400 ms earlier, but only 120 ms of the outgoing passage
        // is left against a 20 ms overlap: admission can buy 100, the origin
        // adds 32, and 268 is beyond this transition's reach.
        let s = super::Shift { admit_ms: 368, origin_ms: 32, gap_ms: 0 };
        let delivered = Engine::delivered_shift_ms(s, 20, 300_000);
        let achieved = Engine::achieved_shift_ms(s, 120, 20);
        assert_eq!(delivered, 400, "the request implies the whole of it");
        assert_eq!(achieved, 132, "the passage says otherwise");
        assert_ne!(delivered, achieved,
                   "fixture: this case must be one where the two disagree, or the assertion below proves nothing");

        // What `admit_due` owes is `asked - achieved`, never `asked -
        // delivered`. 400 - 132 = 268 ms; the pre-fix code owed 0.
        let asked: i64 = 400;
        let owed_frames = (asked - achieved) * e.out_rate as i64 / 1000;
        assert_eq!(owed_frames, 268 * 44_100 / 1000);
        assert_ne!(asked - delivered, asked - achieved,
                   "reverting to `delivered` must not still satisfy this");
    }

    /// **A transition places the passage exactly, and spends whatever that
    /// takes** `[GDE-ARC-051]`.
    ///
    /// The old split had one usable knob and a cap. To sound *earlier* it
    /// opened the passage further in, which discards that much of the start
    /// — tolerable at the tens of milliseconds `OFFSET_MAX_BITE` allowed,
    /// and unacceptable uncapped, where it would cut half a second off the
    /// front of a track. To sound *later* it narrowed admission, which a
    /// contiguous ring cannot honour at all `[GDE-ARC-052]`.
    ///
    /// So each direction now uses the actuator that fits it: overlap more to
    /// come in earlier, keeping every sample of the passage, with the origin
    /// carrying only the sub-chunk remainder admission cannot express; and
    /// silence to come in later, which is exact and costs no content either.
    #[test]
    fn a_transition_places_the_passage_exactly_in_both_directions() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        e.out_channels = 2;
        let chunk = e.echo_chunk_ms() as i64;
        assert_eq!(chunk, 46);
        // The library's typical pair: almost no overlap to spend.
        const OVERLAP: u64 = 5;
        const CEIL: u64 = 240_000;

        // Running behind: come in earlier by overlapping, not by cutting.
        let s = e.split_shift(448, OVERLAP, CEIL);
        assert_eq!(s.admit_ms, 414, "nine whole chunks of overlap");
        assert_eq!(s.origin_ms, 34, "and only the sub-chunk remainder is discarded");
        assert_eq!(s.gap_ms, 0);
        assert!(s.origin_ms < chunk as u64, "never more than a chunk of the passage");
        assert_eq!(Engine::delivered_shift_ms(s, OVERLAP, CEIL), 448, "exact");

        // Running ahead: wait. **The overlap goes first, and only what it
        // cannot cover becomes silence** `[GDE-ARC-070]`. This used to assert
        // `(0, 0, 448)` and called the five milliseconds of overlap
        // "irrelevant to it" — which was the mistake: emitting all 448 ms as
        // silence puts a hole in the OUTGOING passage, because admission
        // happens an overlap before that passage ends and the gap branch
        // mixes nothing while it drains.
        let s = e.split_shift(-448, OVERLAP, CEIL);
        assert_eq!((s.admit_ms, s.origin_ms, s.gap_ms), (-(OVERLAP as i64), 0, 443));
        assert_eq!(Engine::delivered_shift_ms(s, OVERLAP, CEIL), -448, "exact");

        // With an overlap wide enough, there is no silence at all: the whole
        // correction is admission happening later, which costs nothing and
        // is inaudible by construction.
        let wide = e.split_shift(-448, 3_000, CEIL);
        assert_eq!((wide.admit_ms, wide.origin_ms, wide.gap_ms), (-448, 0, 0),
                   "a pair with room to wait should not need silence");
        assert_eq!(Engine::delivered_shift_ms(wide, 3_000, CEIL), -448, "and still exact");

        // **No bite** -- whatever the band allows, in one transition. Not
        // "no cap": `offset_fix` rejoins above `OFFSET_REJOIN_BEYOND`, so
        // 1.5 s is the most a boundary is ever asked for.
        for ask in [-3_000_i64, -1_500, 1_500, 3_000] {
            let s = e.split_shift(ask, OVERLAP, CEIL);
            assert_eq!(Engine::delivered_shift_ms(s, OVERLAP, CEIL), ask,
                       "asked {ask} ms and the transition must place it exactly");
            assert!(s.origin_ms < chunk as u64, "still never cuts more than a chunk");
        }
    }

    /// A wild estimate is clamped, not obeyed: at 5000 ppm the trim would run
    /// at every mix pass and be plainly audible.
    #[test]
    fn an_impossible_rate_is_clamped() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        h.send(Command::SetEchoRate(5_000.0));
        e.tick();
        assert_eq!(e.echo_rate_ppm, Engine::ECHO_RATE_CEILING_PPM);
        h.send(Command::SetEchoRate(-5_000.0));
        e.tick();
        assert_eq!(e.echo_rate_ppm, -Engine::ECHO_RATE_CEILING_PPM);
        h.send(Command::SetEchoRate(f64::INFINITY));
        e.tick();
        assert_eq!(e.echo_rate_ppm, 0.0, "not finite is not a rate at all");
    }

    /// Stopping following stops trimming. A rate left behind would have the
    /// node correcting towards a master it is no longer listening to.
    #[test]
    fn a_zero_rate_stops_the_trim_and_its_clock() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        h.send(Command::SetEchoRate(13.92));
        e.tick();
        assert!(e.echo_last_trim.is_some());
        h.send(Command::SetEchoRate(0.0));
        e.tick();
        assert_eq!(e.due_trim(), None);
        assert!(e.echo_last_trim.is_none(), "the clock stops with the rate");
    }

    /// Below the floor the interval runs to hours and the estimate is mostly
    /// its own error `[GDE-ECHO-350]`.
    #[test]
    fn a_rate_under_the_floor_is_left_alone() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        h.send(Command::SetEchoRate(0.2));
        e.tick();
        e.echo_last_trim = Some(ago(3600));
        assert_eq!(e.due_trim(), None);
    }

    /// `[SPEC-ECHO-050]`: following yourself is a feedback loop, and an easy
    /// thing to type.
    #[test]
    fn a_node_will_not_follow_itself() {
        for h in ["localhost", "127.0.0.1", "::1", "LocalHost", " localhost ", "localhost:5720"] {
            assert!(super::is_self(h), "{h} names this node");
        }
        for h in ["bose", "lempiplay3:5720", "192.168.67.27"] {
            assert!(!super::is_self(h), "{h} is somewhere else");
        }
    }

    /// The whole lever: a constant off the cached free figure.
    #[test]
    fn a_shortfall_comes_straight_off_the_free_space() {
        assert_eq!(Engine::submit_room_frames(10_000, 0), 10_000, "no shortfall, no change");
        assert_eq!(Engine::submit_room_frames(10_000, 362), 9_638);
        // A ring already inside its shortfall offers nothing, rather than
        // wrapping to an enormous room and overrunning the target.
        assert_eq!(Engine::submit_room_frames(100, 362), 0);
    }

    /// `bose` behind `lempiplay3`: 2043 - 1863 = 180 frames, 360 samples
    /// stereo `[LOG-P4-140]`.
    #[test]
    fn the_shortfall_is_the_excess_over_the_fleet_minimum() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_channels = 2;
        e.set_echo_depth(2043, 1863);
        assert_eq!(e.echo_depth_shortfall, 360);
        // The node holding the minimum fills to capacity.
        e.set_echo_depth(1863, 1863);
        assert_eq!(e.echo_depth_shortfall, 0);
    }

    /// A roster claiming a minimum above this node's own offset is wrong. The
    /// answer is a full ring, not an underflow that would silence the node.
    #[test]
    fn an_offset_under_the_stated_minimum_clamps_rather_than_wrapping() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_channels = 2;
        e.set_echo_depth(1863, 2043);
        assert_eq!(e.echo_depth_shortfall, 0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::time::Duration;

    fn nanos_now() -> u64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64).unwrap_or(0)
    }

    /// A committed start waits for its instant rather than firing on arrival.
    #[test]
    fn an_echo_start_in_the_future_does_not_fire_yet() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        h.send(Command::EchoStartAt {
            entry: entry(4242, "nonexistent.flac"),
            start_sample: 0,
            at_nanos: nanos_now() + 60 * 1_000_000_000,
        });
        e.tick();
        assert!(e.echo_start.is_some(), "a future start must still be pending");
        assert!(e.queue.iter().all(|q| q.passage_id != 4242), "and must not be queued yet");
    }

    /// Past its instant but inside the limit: it fires, and the master's
    /// passage goes to the FRONT -- anything less would play what was already
    /// next `[GDE-ECHO-330]`.
    #[test]
    fn an_echo_start_that_is_due_fires_and_goes_to_the_front() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        h.send(Command::EchoStartAt {
            entry: entry(4242, "nonexistent.flac"),
            start_sample: 0,
            at_nanos: nanos_now() - 10_000_000,   // 10 ms ago, one tick
        });
        e.tick();
        assert!(e.echo_start.is_none(), "a fired start is taken, not left to fire twice");
    }

    /// `[GDE-ECHO-375]`'s second half: an estimate that prevents every join
    /// can never be corrected by one, so it has to back itself off.
    ///
    /// `echo_prep_ms` is revised only inside the path a join takes when it
    /// **fires**. A node that paid once for a slow seek into a long capture
    /// `[PI-CHR-075]` therefore fired earlier and earlier of its own accord,
    /// and once the figure carried it past the late limit every subsequent
    /// join was dropped -- with nothing left that could ever lower it again.
    /// A self-calibrating constant needs both directions or it is a ratchet.
    #[test]
    fn a_preparation_estimate_no_join_can_survive_backs_itself_off() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.echo_prep_ms = Engine::ECHO_PREP_MAX_MS;
        h.send(Command::EchoStartAt {
            entry: entry(4242, "nonexistent.flac"),
            start_sample: 0,
            // Half a second out, which this node now believes it needs 2.5 s
            // to arrange: dropped as late.
            at_nanos: nanos_now() + 500_000_000,
        });
        e.tick();
        assert!(e.echo_start.is_none(), "dropped");
        assert!(e.echo_prep_ms < Engine::ECHO_PREP_MAX_MS,
                "still {} ms, so the next join is declined exactly as this one was",
                e.echo_prep_ms);
        // Never below the cold guess, which is the best figure available
        // without a measurement.
        assert!(e.echo_prep_ms >= Engine::ECHO_PREP_GUESS_MS);
    }

    /// Too late is not "late": it is dropped, so the trim loop is never handed
    /// an offset the join created `[GDE-ECHO-410]`.
    #[test]
    fn an_echo_start_long_past_is_dropped_rather_than_started_late() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        h.send(Command::EchoStartAt {
            entry: entry(4242, "nonexistent.flac"),
            start_sample: 0,
            at_nanos: nanos_now() - 5_000_000_000,   // five seconds ago
        });
        e.tick();
        assert!(e.echo_start.is_none(), "dropped");
        assert!(e.queue.iter().all(|q| q.passage_id != 4242),
                "and emphatically not queued");
    }

    fn entry(id: i64, path: &str) -> QueueEntry {
        QueueEntry {
        qid: 0, // stamped by Queue on the way in
            passage_id: id,
            path: PathBuf::from(path),
            start_ms: 0,
            end_ms: 5_000,
            file_ms: 0,
            lead_in_ms: 0,
            lead_out_ms: 0,
            // Pass-through, not the production default `[SPEC-SUI-226]` --
            // this helper is shared by tests unrelated to fades, and a
            // nonzero envelope would perturb exact sample-value assertions.
            fade_in_ms: 0,
            fade_out_ms: 0,
            fade_in_curve: Curve::Exponential,
            fade_out_curve: Curve::Exponential,
            gain_db: 0.0,
            mbid: None,
            naming: Default::default(),
            selected_by: None,
        }
    }

    #[test]
    fn pause_stops_submission_without_losing_the_queue() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.enqueue(entry(1, "missing.mp3"));
        h.send(Command::Pause);
        e.tick();
        assert!(!h.snapshot().playing);
        assert!(!e.is_shutdown(), "pause must not shut the engine down");
    }

    #[test]
    fn shutdown_ends_the_loop() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        h.send(Command::Shutdown);
        e.tick();
        assert!(e.is_shutdown());
    }

    /// The two-state model: pausing must not drain or halt the producers.
    #[test]
    fn pausing_keeps_the_producers_running() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        h.send(Command::Pause);
        for _ in 0..3 {
            e.tick();
        }
        assert!(!h.snapshot().playing);
        assert!(!e.is_shutdown(), "pause is a playback state, not a shutdown");
    }

    #[test]
    fn a_dropped_sender_stops_the_engine() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        drop(h);
        e.tick();
        assert!(e.is_shutdown(), "a vanished controller must not leave it running");
    }

    #[test]
    fn the_next_passage_is_opened_before_it_is_needed() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        h.send(Command::Play);
        e.enqueue(entry(1, "does-not-exist.mp3"));
        e.tick();
        // Nothing openable here, but the point stands: preparing must not leave
        // an unopenable passage in place to be retried on every tick forever.
        assert_eq!(h.snapshot().queue_len, 0);
        assert!(e.ready.is_none());
        assert!(!e.is_shutdown());
    }

    /// Preparation must not make the passage sound. `live` is what the mixer
    /// sums; a prepared passage that leaked into it would play over the top of
    /// whatever is already going.
    #[test]
    fn a_prepared_passage_is_not_yet_sounding() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        h.send(Command::Play);
        e.enqueue(entry(1, "does-not-exist.mp3"));
        e.tick();
        assert_eq!(h.snapshot().active_streams, 0, "prepared is not live");
    }

    /// A browsed passage goes to the TOP of the queue, which is the next thing
    /// heard `[REQ-VIS-180]`. It went in second for a while, on the mistaken
    /// idea that the sounding passage occupied slot zero -- it does not; it is
    /// in `live` and out of the queue entirely.
    const DQ_MBID: &str = "aaaaaaaa-0000-0000-0000-000000000007";

    /// Write a decodable WAV of `ms` milliseconds and return its path.
    ///
    /// **Generated, not committed.** The oldest gap in this engine's tests was
    /// that judging a play needs audio to play, and there were no fixtures; a
    /// binary in the repository would have been one answer, but symphonia's
    /// default features already bring a RIFF reader and a PCM codec, so silence
    /// can simply be written on the spot. Silence decodes to frames like
    /// anything else, and frames are what the clock counts.
    /// A skip must announce when the audio will REALLY sound `[GDE-ECHO-352]`.
    ///
    /// `skip` cuts the ring and lays the incoming passage `skip_lead_ms` in,
    /// so it sounds in half a second where the ring still holds two. The
    /// schedule emitted from inside `admit_due` -- before the cut -- said
    /// fifteen, and every follower planned against that.
    #[test]
    fn a_skip_announces_the_post_cut_air_time_not_the_whole_ring() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        let mut b = entry(2, wav.to_str().unwrap());
        b.end_ms = 30_000;
        e.enqueue(a);
        e.enqueue(b);
        h.send(Command::Play);
        e.drain_commands();
        assert!(tick_until(&mut e, |e| e.shown.is_some()), "it should be playing");

        // With a discard sink there is no hardware clock, so no schedule is
        // published at all -- which is itself the contract `[GDE-ECHO-290]`.
        // What can be asserted here is the arithmetic the skip path feeds it.
        let ch = e.out_channels.max(1);
        let lead_samples = (e.skip_lead_ms * e.out_rate as u64 / 1000) as usize * ch;
        let lead_frames = (lead_samples / ch) as u64;
        assert_eq!(lead_frames * 1000 / e.out_rate as u64, e.skip_lead_ms,
                   "the depth a skip republishes is its lead, not the ring");
        // And that depth is far short of a full ring, which is the whole bug.
        assert!(lead_frames < e.out_buffered_frames() as u64 + crate::BUFFER_FRAMES as u64,
                "a lead must be shorter than a ring for this to matter");
        let _ = std::fs::remove_file(&wav);
    }

    /// An offset correction must actually reach the audio `[GDE-ECHO-347]`,
    /// and under `[GDE-ARC-051]` it must reach it at as little cost to the
    /// passage as the mix quantum allows.
    ///
    /// Written after a regression that logged the correction it was about to
    /// discard: the shift was consumed before the split that used it, so the
    /// fine half was always zero and every late correction did nothing, while
    /// the log said otherwise for four commits. Asserting on the *passage's
    /// origin* rather than on a log line is the difference.
    ///
    /// The number changed when exact placement arrived. This same +120 ms used
    /// to be paid entirely out of the passage's head, because admission was
    /// only ever offered the overlap the pair already had — about five
    /// milliseconds in this library — and 120 ms rounds to none of it. It is
    /// now offered whatever the shorter of the two passages can sustain, so
    /// two whole 46 ms blocks come out of the overlap and the head loses 28 ms
    /// rather than 120. What the listener hears is the same 120 ms; what the
    /// listener keeps is 92 ms more of the track.
    #[test]
    fn a_late_correction_is_taken_by_admission_before_the_passage_head() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        let mut b = entry(2, wav.to_str().unwrap());
        b.end_ms = 30_000;
        e.enqueue(a);
        e.enqueue(b);
        h.send(Command::Play);
        e.drain_commands();
        assert!(tick_until(&mut e, |e| e.shown.is_some()), "it should be playing");

        // 120 ms late, so sound 120 ms earlier: two whole mix blocks of it go
        // to admission and only the 28 ms that will not divide is cut from
        // the head.
        h.send(Command::EchoCorrectNextStart(120));
        e.drain_commands();
        let shift = e.echo_split();
        assert_eq!((shift.admit_ms, shift.origin_ms, shift.gap_ms), (92, 28, 0));
        let (overlap, ceiling) = e.next_transition_overlap();
        assert_eq!(Engine::delivered_shift_ms(shift, overlap, ceiling), 120,
                   "the two halves must add up to what was asked");

        // Run to the transition and check where passage 2 actually opened.
        assert!(tick_until(&mut e, |e| e.live.iter().any(|l| l.entry.passage_id == 2)),
                "the second passage should be admitted");
        let opened = e.live.iter().find(|l| l.entry.passage_id == 2).unwrap().origin_ms;
        assert_eq!(opened, 28, "opened at {opened} ms, so the correction never reached it");
        assert_eq!(e.echo_next_shift_ms, 0, "and it is spent, not applied every boundary");
        // **Owed only what admission was actually late by** `[GDE-ARC-057]`.
        // This asserted zero while the engine computed delivery from the
        // request; measuring it instead shows a couple of milliseconds, which
        // is admission firing on `remaining <= overlap + nudge` and therefore
        // always at or after the instant it wanted. The bound is one mix
        // block, because that is how far the played position can move between
        // two checks -- and the residue goes to the trim rather than being
        // rounded away, which is the whole point of measuring it.
        let owed = e.echo_debt_frames;
        // **Strictly positive, and that is the point of the assertion**
        // `[GDE-ARC-057]`. Computing the shortfall from the request instead
        // of the measurement yields exactly zero, so the `>= 0` this used to
        // say was satisfied by the behaviour it was written to exclude -- a
        // reviewer reverted the wiring with the suite still green. Admission
        // fires on `remaining <= overlap + nudge`, so a real transition is
        // always a little late; a report of zero means nobody measured.
        assert!(owed > 0 && owed < Engine::MIX_FRAMES as i64,
                "owed {owed} frames; a clean transition cannot be late by more \
than one mix block");
        let _ = std::fs::remove_file(&wav);
    }

    /// The other direction of the test above, which is the one that was never
    /// written `[GDE-ECHO-372]`.
    ///
    /// `+120` is the direction where the coarse knob is zero and its clamp is
    /// never reached. `-120` is the direction where the clamp destroyed the
    /// coarse step and the fine half then moved the node the wrong way: these
    /// passages have no overlap at all, so the transition could not be delayed
    /// by a millisecond, and opening the next passage 18 ms in would have made
    /// a node asked to wait 120 ms arrive 18 ms sooner instead.
    ///
    /// The test kept its name for a while after the fix, because the fix was
    /// only to refuse: the shift went to the frame trim at 0.1 ms/s, so a node
    /// asked to wait 120 ms waited twenty minutes to do it. `[GDE-ARC-052]`
    /// gives the boundary an actuator for this direction — silence ahead of
    /// the first sample, as much as is owed — so "later" is now placed exactly
    /// and at the same instant as "earlier", and the passage keeps its head.
    #[test]
    fn an_early_correction_is_placed_as_silence_before_the_passage() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        let mut b = entry(2, wav.to_str().unwrap());
        b.end_ms = 30_000;
        e.enqueue(a);
        e.enqueue(b);
        h.send(Command::Play);
        e.drain_commands();
        assert!(tick_until(&mut e, |e| e.shown.is_some()), "it should be playing");

        // 120 ms early: start the next passage 120 ms LATER.
        h.send(Command::EchoCorrectNextStart(-120));
        e.drain_commands();
        let shift = e.echo_split();
        assert_eq!((shift.admit_ms, shift.origin_ms, shift.gap_ms), (0, 0, 120),
                   "the later direction is silence, not a bite out of the overlap");
        let (overlap, ceiling) = e.next_transition_overlap();
        assert_eq!(Engine::delivered_shift_ms(shift, overlap, ceiling), -120,
                   "and it delivers the whole of what was asked, at the boundary");

        let owed = 120 * e.out_rate as u64 / 1000;
        assert!(tick_until(&mut e, |e| e.live.iter().any(|l| l.entry.passage_id == 2)),
                "the second passage should be admitted");
        let opened = e.live.iter().find(|l| l.entry.passage_id == 2).unwrap().origin_ms;
        assert_eq!(opened, 0,
                   "opened {opened} ms in, which sounds EARLIER -- the opposite of what was asked");
        assert_eq!(e.echo_next_shift_ms, 0, "and it is spent, not applied every boundary");
        // The silence is armed in frames, because that is what the mixer
        // counts in `[GDE-ARC-052]`. Some of it may already have been emitted
        // by the time the admission is visible here, so the claim is that it
        // was armed at the right size and is being spent -- not that it is
        // untouched.
        assert!(e.echo_gap_frames > 0 && e.echo_gap_frames <= owed,
                "gap of {} frames, expected up to {owed}", e.echo_gap_frames);
        // And because the boundary took all of it, the slow actuator is left
        // with nothing to crawl through. That is the whole point of
        // `[GDE-ARC-052]`: before it, this line read `-120 * rate / 1000` and
        // the node spent twenty minutes at `ECHO_DEBT_PPM` arriving where the
        // boundary could have put it at once `[GDE-ARC-046]`.
        assert_eq!(e.echo_debt_frames, 0,
                   "the transition placed it exactly; nothing should be owed");
        let _ = std::fs::remove_file(&wav);
    }

    /// `[GDE-ECHO-373]`: the endgame must reach the audio in **both**
    /// directions, at the block size the engine actually submits.
    ///
    /// `scratch` was `2048 * channels` and the submission threshold 4096,
    /// which on a stereo device are the same number -- so `mix_and_submit`
    /// handed `apply_trim` a block exactly as long as its own buffer and every
    /// duplicate was refused for want of one frame. The mixer's own test
    /// asserted that refusal with `filled` equal to the buffer length, which
    /// **is** the production case; nobody connected the two numbers. A node
    /// running early could not be trimmed back at all, and the rate trim was
    /// one-directional with it.
    #[test]
    fn a_duplicated_frame_reaches_the_ring_at_the_block_the_engine_submits() {
        let ring = crate::output::OutputRing::new(400_000, crate::output::Volume::new(1.0));
        let (mut e, h) = Engine::new(crate::path::PathHandle::with_ring(ring.clone()), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        e.enqueue(a);
        h.send(Command::Play);
        e.drain_commands();
        // Warmed up with the output ring left to fill, which is exactly what
        // back-pressure does in life: the mixer waits for room while the
        // decoder runs ahead, so a pass is limited by the block size rather
        // than by what the decoder happened to have produced.
        let drain = |ring: &crate::output::OutputRing| {
            let mut st = ring.state.lock().unwrap();
            let len = st.ring.len();
            let mut sink = vec![0.0f32; len];
            st.ring.read(&mut sink);
        };
        let block = e.min_submit();
        let mut ready = false;
        for _ in 0..5_000 {
            e.tick();
            if e.live.first().is_some_and(|l| l.stream.ring.len() >= 4 * block) {
                ready = true;
                break;
            }
        }
        assert!(ready, "the decoder should have run ahead of the mixer");

        drain(&ring);
        let plain = e.mix_and_submit();
        assert_eq!(plain, block, "a plain pass submits exactly one block");

        // Now this node is running EARLY, which is repaired by repeating a
        // frame -- the half of the actuator that never fired.
        h.send(Command::SetEchoRate(-13.92));
        e.drain_commands();
        e.echo_last_trim = Some(ago(10));
        assert_eq!(e.due_trim(), Some(false), "ahead: repeat a frame to wait");
        drain(&ring);
        let trimmed = e.mix_and_submit();
        assert_eq!(trimmed, plain + e.out_channels,
                   "the duplicated frame never reached the ring: {trimmed} against {plain}");
        let _ = std::fs::remove_file(&wav);
    }

    /// **A commanded start takes its depth from the target instant, not from
    /// a constant** `[GDE-ECHO-342]`, `[GDE-ARC-058]`.
    ///
    /// `cut_ring_to_incoming` lays the incoming passage `skip_lead_ms` into
    /// the ring — 500 ms on this fleet, a fixed number — and `fire_echo_start`
    /// fired early by that plus `echo_prep_ms` to compensate. Both halves are
    /// guesses about a duration that has not happened yet, and the second was
    /// measured on this fleet at 33–37 ms while being assumed to be 400.
    ///
    /// `placement()` answers the question directly instead: given the instant
    /// the audio must sound and this node's own device delay, how many frames
    /// belong ahead of sample 0. Computed **at the cut, after the
    /// preparation**, it absorbs however long that preparation actually took
    /// rather than predicting it.
    ///
    /// Here the target is 1.2 s out and the device holds 46 ms, so 1154 ms of
    /// audio belongs in front of sample 0 — regardless of what `skip_lead_ms`
    /// says.
    #[test]
    fn a_commanded_start_takes_its_depth_from_the_target_instant() {
        let ring = crate::output::OutputRing::new(4_000_000, crate::output::Volume::new(1.0));
        let (mut e, _h) = Engine::new(crate::path::PathHandle::with_ring(ring), 1);
        e.out_rate = 44_100;
        e.out_channels = 2;
        e.skip_lead_ms = 500;
        // 46 ms of device delay, as `bose` reports `[LOG-CPAL-060]`.
        e.echo_delay_trim_ms = 46;

        let now = 1_000_000_000_000u64;
        // Written the way `placement` computes it rather than in round
        // milliseconds: the distance is rounded to the **nearest** frame, not
        // truncated, because truncating loses in one direction and makes
        // every node a frame or two early `[GOV-SRC-040]`. A millisecond
        // expectation here would be off by one and would be "fixed" by
        // loosening the assertion, which is how a systematic sign survives.
        let delay_frames = 46 * 44_100 / 1000;
        let ahead = |ms: u64| (ms * 1_000_000 * 44_100 + 500_000_000) / 1_000_000_000;

        let lead = e.join_lead_frames(now + 1_200_000_000, now);
        assert_eq!(lead, ahead(1_200) - delay_frames,
                   "the depth is the target less this node's own delay");

        // A nearer target is a shallower ring, which is the whole point: the
        // same constant cannot be right for both.
        let near = e.join_lead_frames(now + 300_000_000, now);
        assert_eq!(near, ahead(300) - delay_frames);
        assert_ne!(near, lead);

        // An instant already gone cannot be waited for. Sounding at once is
        // the only answer left, and it is reported rather than dressed up as
        // a depth `[GOV-SRC-040]`.
        assert_eq!(e.join_lead_frames(now - 1_000_000, now), 0,
                   "a missed start sounds now, not after a constant lead");
    }

    /// **A join target is consumed even when the skip does nothing**
    /// `[GDE-ARC-058]`.
    ///
    /// `skip` returns early with nothing sounding, and the target used to be
    /// taken after that return. A commanded start arriving with an empty
    /// `live` would then leave the instant sitting in the engine until the
    /// *next* skip -- quite possibly a listener pressing the button minutes
    /// later -- which would place its audio against a time long past and
    /// sound at once with a `Late` line to explain it.
    ///
    /// The ordinary path must also be untouched by all of this, because
    /// `cut_ring_to_incoming` is the real-time path every user skip and seek
    /// takes `[GDE-ECHO-342]`: with no target pending, the lead is the
    /// constant it always was.
    #[test]
    fn a_stale_join_target_cannot_be_inherited_by_an_ordinary_skip() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.out_rate = 44_100;
        e.out_channels = 2;

        // Nothing sounding: the skip bails, and must still clear the target.
        assert!(e.live.is_empty(), "fixture: nothing live");
        e.echo_join_at = Some(1_000_000_000_000);
        e.skip();
        assert_eq!(e.echo_join_at, None,
                   "a target survived a skip that did nothing and will be used by the next one");

        // And with none pending the engine has nothing echo-specific to say.
        e.skip();
        assert_eq!(e.echo_join_at, None);
    }

    /// **A seek cuts the ring, so it voids the basis and drops the sounding
    /// claim — exactly as a skip does** `[GDE-ARC-064]`.
    ///
    /// Found while trying to *use* a seek as a measuring instrument: it moves
    /// the audio to a known content position, which makes it the right way to
    /// ask whether a position correction really moves the alignment by what
    /// it claims. It was not a usable instrument, because it left two things
    /// a skip has cleaned up after since `[GDE-ECHO-360]` and
    /// `[GDE-ARC-059]`.
    ///
    /// Both matter to a listener, not only to a measurement. `seek_to` and
    /// `skip` reach the same `cut_ring_to_incoming`; anything true of the ring
    /// after one is true after the other, and a listener seeking on a master
    /// had it publishing anchors derived across discarded audio.
    #[test]
    fn a_seek_voids_the_basis_and_the_sounding_claim_like_a_skip() {
        let ring = crate::output::OutputRing::new(400_000, crate::output::Volume::new(1.0));
        let (mut e, h) = Engine::new(crate::path::PathHandle::with_ring(ring.clone()), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        e.enqueue(a);
        h.send(Command::Play);
        e.drain_commands();

        let drain = |ring: &crate::output::OutputRing| {
            let mut st = ring.state.lock().unwrap();
            let len = st.ring.len();
            let mut sink = vec![0.0f32; len];
            st.ring.read(&mut sink);
        };
        for _ in 0..400 { e.tick(); drain(&ring); }
        assert!(e.shown_is_sounding, "fixture: it should be sounding by now");
        e.echo_basis.establish();
        assert!(e.echo_basis.is_valid(), "fixture: and comparable");

        e.seek_to(5_000);

        assert_eq!(e.echo_basis.voided_by(), Some(crate::echo::Voided::Skip),
                   "a seek discards counted frames and must say so");
        assert!(!e.echo_basis.is_valid());
        assert!(!e.shown_is_sounding,
                "the sought-to point does not sound until the lead has drained");

        // **And it must come back on its own** `[GDE-ARC-066]`. `skip` is
        // re-established by the `admit_due` inside it; a seek admits nothing,
        // so when the voiding above was first added the node fell silent to
        // every follower until its next passage boundary -- caught live, by a
        // measurement that returned no samples at all.
        for _ in 0..400 { e.tick(); drain(&ring); }
        assert!(e.shown_is_sounding, "the sought-to passage never became audible");
        assert!(e.echo_basis.is_valid(),
                "the basis never recovered from a seek; this node is silent to its followers until the next passage boundary");
        let _ = std::fs::remove_file(&wav);
    }

    /// **A skip is not a transition the alignment plan gets to spend itself
    /// on** `[GDE-ARC-067]`.
    ///
    /// `skip` clears `live` and calls `admit_due`, which takes the
    /// `(Some(_), None)` arm and admits at once — spending any pending
    /// `echo_next_shift_ms` against it. That shift was computed for the
    /// *master's* next boundary, minutes away; a listener pressing skip is
    /// not that boundary. Worse in the "later" direction: the shift becomes
    /// `echo_gap_frames`, the ring is then cut, and the silence is emitted
    /// into the opening moments of the track the listener just asked for.
    ///
    /// Measured by a reviewer: a pending −448 ms correction plus one skip
    /// left **19756 frames of silence armed** — about 450 ms of dead air the
    /// listener hears, and an alignment correction spent on a transition
    /// that has nothing to do with the programme it was measured against.
    ///
    /// The same reasoning as the basis voiding `[GDE-ECHO-360]`: after a cut
    /// the queue has advanced and the ring is gone, so no plan made before it
    /// is worth acting on. Any gap already in flight goes too — it was aimed
    /// at a ring that no longer exists.
    #[test]
    fn a_skip_does_not_spend_the_alignment_plan_on_itself() {
        // `silent()`, not a real ring: with a ring and no device draining it,
        // nothing ever leaves, so `shown` stays `None` and the fixture never
        // reaches the assertions below. It failed exactly that way first.
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(30_000);
        for id in [1, 2] {
            let mut x = entry(id, wav.to_str().unwrap());
            x.end_ms = 30_000;
            e.enqueue(x);
        }
        h.send(Command::Play);
        e.drain_commands();
        assert!(tick_until(&mut e, |e| e.shown.is_some()), "it should be playing");

        // A correction is pending for the master's next boundary: sound
        // later, which is the direction that becomes silence.
        h.send(Command::EchoCorrectNextStart(-448));
        e.drain_commands();
        assert_eq!(e.echo_next_shift_ms, -448, "fixture: the plan is pending");

        e.skip();

        assert_eq!(e.echo_gap_frames, 0,
                   "the skip turned an alignment correction into dead air at the front of the track the listener asked for");
        assert_eq!(e.echo_next_shift_ms, 0,
                   "and the plan must be voided by the cut, not spent on it");

        // **A seek is the same cut and gets the same treatment.** This half
        // had no coverage at all: a reviewer deleted the clearing from
        // `seek_to` with the whole suite still green. A seek also
        // re-announces the SAME passage, so `fs.corrected` stops the
        // follower re-measuring and a stale shift would survive to be spent
        // at a boundary it was never measured against.
        h.send(Command::EchoCorrectNextStart(-448));
        e.drain_commands();
        e.echo_gap_frames = 1_234;
        assert_eq!(e.echo_next_shift_ms, -448, "fixture: a plan is pending again");
        e.seek_to(5_000);
        assert_eq!(e.echo_gap_frames, 0, "a seek must drop an in-flight gap");
        assert_eq!(e.echo_next_shift_ms, 0,
                   "and a plan measured against the position it just left");
        let _ = std::fs::remove_file(&wav);
    }

    /// **One notion of this node's delay, not two** `[GDE-ARC-065]`,
    /// `[SPEC-DLY-010]`.
    ///
    /// The spec is explicit: "This is not a second notion of delay layered on
    /// the first. `presentation_offset` is *measured delay + calibrated
    /// residual*." Every scheduling path used that sum; `air_position` used
    /// the measured half alone, so a node with a trim scheduled against one
    /// delay and told every follower a position computed from another.
    ///
    /// Latent while the trim is zero -- which it is on the whole fleet, and
    /// why it survived: the two models agreed. It bites the first time
    /// somebody calibrates a node by ear, which is what that control is for.
    ///
    /// **This is also the first test to execute `air_position` at all.** It
    /// declines unless the clock reports `Hardware`, which needs a delay seen
    /// to vary, which no test ring produced -- so the function was a blind
    /// spot that had already hidden this and `[GDE-ARC-059]`.
    #[test]
    fn the_reported_air_position_uses_the_same_delay_the_scheduler_does() {
        let ring = crate::output::OutputRing::new(400_000, crate::output::Volume::new(1.0));
        let (mut e, h) = Engine::new(crate::path::PathHandle::with_ring(ring.clone()), 3);
        e.out_rate = 44_100;
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        e.enqueue(a);
        h.send(Command::Play);
        e.drain_commands();

        let drain = |ring: &crate::output::OutputRing| {
            let mut st = ring.state.lock().unwrap();
            let len = st.ring.len();
            let mut sink = vec![0.0f32; len];
            st.ring.read(&mut sink);
        };
        // A delay that has been seen to VARY is what makes the verdict
        // `Hardware`; a constant one is not a measurement `[GDE-ECHO-290]`.
        for i in 0..400 {
            e.tick();
            drain(&ring);
            ring.clock.tick_for_test(2_048, 2_040 + (i % 2));
        }
        assert_eq!(ring.clock.timestamps(), crate::output::Timestamps::Hardware,
                   "fixture: the clock must look like real hardware");
        assert!(e.shown_is_sounding, "fixture: and the passage must be sounding");

        let untrimmed = e.air_position().expect("a sounding node reports where it is");

        // A listener calibrates 200 ms of delay the device does not report.
        // The node now sounds later than the device claims, so its air
        // position is EARLIER than it was -- and by exactly the trim.
        e.echo_delay_trim_ms = 200;
        let trimmed = e.air_position().expect("still reports");
        assert_eq!(trimmed.passage_id, untrimmed.passage_id);
        let moved = untrimmed.position_ms as i64 - trimmed.position_ms as i64;
        assert!((moved - 200).abs() <= 1,
                "the trim moved the reported position by {moved} ms, not 200; the scheduler and the anchor disagree about this node's delay");
        let _ = std::fs::remove_file(&wav);
    }

    /// **After a cut the node reports a position it has not reached**
    /// `[GDE-ARC-059]`.
    ///
    /// Written after getting this backwards. The premise was that a cut
    /// blinds the anchor until the next passage, because `Voided::Skip` says
    /// so and `establish` is documented as reachable only from a clean
    /// boundary `[GDE-ECHO-360]`. It is not: `skip` calls `admit_due`, and
    /// `admit_due` establishes. The basis is valid again before `skip`
    /// returns.
    ///
    /// The real fault is the opposite one. `skip` also sets `shown` eagerly,
    /// so the button stays honest `[REQ-AUD-164]` -- but the incoming passage
    /// does not sound for the whole lead after the cut, a computed 886 ms on
    /// the join measured here. Through that window `air_position` reports
    /// *this passage, at its origin, now*, which is a promise about the
    /// display rather than an observation of the air. It is published as an
    /// anchor, and it is the first thing this node's own residual filter sees
    /// after a join clears it.
    ///
    /// So the claim is not that the basis returns too late. It is that
    /// `air_position` must decline while `shown` is a promise, and answer
    /// again the moment `advance_shown` promotes a passage on its own test.
    #[test]
    fn no_air_position_is_reported_while_shown_is_only_a_promise() {
        let ring = crate::output::OutputRing::new(400_000, crate::output::Volume::new(1.0));
        let (mut e, h) = Engine::new(crate::path::PathHandle::with_ring(ring.clone()), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        let mut b = entry(2, wav.to_str().unwrap());
        b.end_ms = 30_000;
        e.enqueue(a);
        e.enqueue(b);
        h.send(Command::Play);
        e.drain_commands();
        // The ring has no device behind it, so nothing leaves unless this
        // test takes it out. "Sounding" means the first sample has LEFT the
        // ring, so without draining that moment never arrives and every
        // assertion below would be about a stalled engine instead.
        let drain = |ring: &crate::output::OutputRing| {
            let mut st = ring.state.lock().unwrap();
            let len = st.ring.len();
            let mut sink = vec![0.0f32; len];
            st.ring.read(&mut sink);
        };
        // The clock is driven with a VARYING delay so the verdict reaches
        // `Hardware` and `air_position` will actually answer. Without this
        // the function returns `None` on its first line and every assertion
        // below passes without reaching the guard -- which is precisely what
        // this test did when it was written, while carrying a comment
        // claiming the function could not be exercised at all. It can: the
        // hook exists `[GDE-ARC-065]`.
        let run = |e: &mut Engine, n: usize| {
            for i in 0..n { e.tick(); drain(&ring); ring.clock.tick_for_test(2_048, 2_040 + (i as u64 % 2)); }
        };
        run(&mut e, 400);
        assert_eq!(ring.clock.timestamps(), crate::output::Timestamps::Hardware,
                   "fixture: the guard is unreachable unless the clock is believable");
        assert!(e.air_position().is_some(),
                "fixture: a settled node reports where it is");
        assert!(e.shown_is_sounding, "fixture: a settled node should know it is sounding");

        e.skip();
        // The basis is *not* voided across this, which is the thing that had
        // to be checked rather than assumed.
        assert!(e.echo_basis.is_valid(),
                "admit_due re-establishes inside skip; if that changes, this test is measuring something else");
        assert!(!e.shown_is_sounding,
                "the node believes it is sounding a passage that has not started yet, and will publish an anchor saying so");
        assert!(e.air_position().is_none(),
                "it reported a position for a passage that has not started sounding; this is published as its anchor and fed to every follower");

        run(&mut e, 400);
        assert!(e.shown_is_sounding, "the skipped-to passage never became audible");
        assert!(e.air_position().is_some(),
                "and it must answer again once the passage is really sounding");
        let _ = std::fs::remove_file(&wav);
    }


    /// A duplicate needs room in the **output** as well as in the block.
    ///
    /// The headroom fix `[GDE-ECHO-373]` puts the extra frame in `scratch`,
    /// where `apply_trim` can reach it. It does not put it in the ring. The
    /// mixer runs whenever one block is free, so a ring with *exactly* one
    /// block free is the ordinary steady state under back-pressure, not a
    /// corner -- and handing it a block plus a frame means it accepts the
    /// block and the frame is gone. That is a frame DROPPED where the trim
    /// meant to add one: the correction backwards, at twice the size, which
    /// is the shape of every fault in `[GDE-ECHO-351]`.
    #[test]
    fn a_duplicate_is_refused_rather_than_losing_the_frame_it_meant_to_add() {
        const CAP: usize = 40_000;
        let ring = crate::output::OutputRing::new(CAP, crate::output::Volume::new(1.0));
        let (mut e, h) = Engine::new(crate::path::PathHandle::with_ring(ring.clone()), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        e.enqueue(a);
        h.send(Command::Play);
        e.drain_commands();
        let block = e.min_submit();
        let mut ready = false;
        for _ in 0..5_000 {
            e.tick();
            if e.live.first().is_some_and(|l| l.stream.ring.len() >= 4 * block) {
                ready = true;
                break;
            }
        }
        assert!(ready, "the decoder should have run ahead of the mixer");

        // Exactly one block free: what back-pressure leaves behind on a ring
        // the mixer tops up the instant it has room.
        {
            let mut st = ring.state.lock().unwrap();
            st.ring.clear();
            assert_eq!(st.ring.write(&vec![0.0f32; CAP - block]), CAP - block);
        }
        e.out_room = 0;
        assert_eq!(ring.free(), block, "the fixture must leave exactly one block");

        h.send(Command::SetEchoRate(-13.92));
        e.drain_commands();
        e.echo_last_trim = Some(ago(10));
        assert_eq!(e.due_trim(), Some(false), "ahead: repeat a frame to wait");

        let before = ring.state.lock().unwrap().ring.len();
        let submitted = e.mix_and_submit();
        let after = ring.state.lock().unwrap().ring.len();
        assert_eq!(after - before, submitted,
                   "the ring took {} of the {submitted} samples it was handed",
                   after - before);
        assert_eq!(submitted, block,
                   "with no room for the extra frame the trim must be refused, not half-applied");
        let _ = std::fs::remove_file(&wav);
    }

    /// **Silence is the actuator for a follower running ahead**
    /// `[GDE-ARC-052]`. Emitted before the incoming passage's audio, and the
    /// streams are left untouched while it plays out -- their decoded audio
    /// waits in their own rings rather than being consumed early, so the
    /// passage lands intact, just later.
    ///
    /// This is the half the design never had. `a_pause_in_writing_leaves_no_gap_in_the_audio`
    /// shows why not submitting cannot serve: the ring is contiguous, so a
    /// pause only makes the buffer shallower and the audio airs at the same
    /// instant. Moving sound later means putting something in front of it.
    #[test]
    fn a_gap_emits_silence_without_consuming_the_passage_behind_it() {
        let ring = crate::output::OutputRing::new(400_000, crate::output::Volume::new(1.0));
        let (mut e, h) = Engine::new(crate::path::PathHandle::with_ring(ring.clone()), 3);
        let wav = wav_of(30_000);
        let mut a = entry(1, wav.to_str().unwrap());
        a.end_ms = 30_000;
        e.enqueue(a);
        h.send(Command::Play);
        e.drain_commands();
        let block = e.min_submit();
        let mut ready = false;
        for _ in 0..5_000 {
            e.tick();
            if e.live.first().is_some_and(|l| l.stream.ring.len() >= 4 * block) {
                ready = true;
                break;
            }
        }
        assert!(ready, "the decoder should have run ahead of the mixer");
        let drain = |ring: &crate::output::OutputRing| {
            let mut st = ring.state.lock().unwrap();
            let len = st.ring.len();
            let mut sink = vec![0.0f32; len];
            st.ring.read(&mut sink);
        };
        drain(&ring);

        // Owe a gap of one block plus a little, so it spans two passes.
        let ch = e.out_channels.max(1);
        e.echo_gap_frames = (block / ch) as u64 + 100;
        let buffered_before = e.live[0].stream.ring.len();

        let n = e.mix_and_submit();
        assert_eq!(n, block, "a gap pass still submits a full block");
        assert_eq!(e.live[0].stream.ring.len(), buffered_before,
                   "the passage must not be consumed while silence is playing");
        assert_eq!(e.echo_gap_frames, 100, "and the gap is spent by what was emitted");

        // What reached the ring is silence, not audio.
        {
            let mut st = ring.state.lock().unwrap();
            let mut out = vec![9.9f32; block];
            assert_eq!(st.ring.read(&mut out), block);
            assert!(out.iter().all(|v| *v == 0.0), "a gap must be actual silence");
        }
        // And once it is spent, mixing resumes and the passage is consumed.
        drain(&ring);
        e.echo_gap_frames = 0;
        e.mix_and_submit();
        assert!(e.live[0].stream.ring.len() < buffered_before,
                "the passage resumes after the gap");
        let _ = std::fs::remove_file(&wav);
    }

    /// The invariant the two numbers have to keep, stated where a future
    /// change to either will trip over it `[GDE-ECHO-373]`.
    #[test]
    fn the_mix_block_always_leaves_room_for_a_duplicated_frame() {
        let (e, _h) = Engine::new(crate::path::PathHandle::silent(), 1);
        assert!(e.scratch.len() >= e.min_submit() + e.out_channels,
                "a block of {} in a buffer of {} has nowhere to put a duplicate",
                e.min_submit(), e.scratch.len());
    }

    /// `[GDE-ECHO-373]`'s second half: the debt was credited by the intention
    /// to move a frame rather than by the frame that moved, so a negative
    /// offset counted itself down to zero over eight minutes while the log
    /// said frames were being trimmed and no sample anywhere had changed.
    #[test]
    fn a_refused_trim_credits_no_debt_and_keeps_its_turn() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.out_rate = 44_100;
        // 10 ms early: a position debt to be repaid by repeating frames.
        h.send(Command::EchoShedOffset(-10));
        e.drain_commands();
        let owed = e.echo_debt_frames;
        assert!(owed < 0, "a node ahead owes a negative debt");
        e.echo_last_trim = Some(ago(10));
        assert_eq!(e.due_trim(), Some(false));

        // Nothing is playing, so the mixer produces no frames at all and there
        // is nothing to duplicate.
        e.mix_and_submit();
        assert_eq!(e.echo_debt_frames, owed,
                   "a frame that never moved cannot pay a frame of debt");
        assert!(e.due_trim().is_some(),
                "a refused trim must still be due, not wait out another interval");
    }

    /// A skip that opens a passage part-way in reports THAT position, not
    /// zero.
    ///
    /// The anchor, the display and any resume point written during the passage
    /// all read this figure `[REQ-AUD-164]`. Found in the field: an echo node
    /// joining a second into a passage `[GDE-ECHO-330]` published an anchor a
    /// second behind where it actually was, which the correction then read as
    /// a residual and tried to chase.
    #[test]
    fn a_skip_into_a_resume_shows_the_origin_not_zero() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(30_000);
        let mut ent = entry(77, wav.to_str().unwrap());
        ent.end_ms = 30_000;
        e.enqueue(ent.clone());
        h.send(Command::Play);
        e.drain_commands();
        assert!(tick_until(&mut e, |e| e.shown.is_some()), "it should be playing");

        // Now join the same passage ten seconds in, the way a mid-passage
        // join does: a resume position, then a skip onto it.
        e.resume_at(10_000);
        h.send(Command::PlayNow(ent));
        // Drained, not ticked. `advance_shown` runs later in a tick and
        // recomputes this from the ring, so ticking first would hide whatever
        // `skip` put here -- which is the thing under test.
        e.drain_commands();
        let (_, shown) = e.shown.as_ref().expect("still playing");
        assert!(*shown >= 9_000, "shown {shown} ms, but the passage opened 10 s in");
        let _ = std::fs::remove_file(&wav);
    }

    fn wav_of(ms: u64) -> std::path::PathBuf {
        const RATE: u32 = 44_100;
        const CH: u16 = 2;
        let frames = (RATE as u64 * ms / 1000) as u32;
        let data = frames * CH as u32 * 2;
        let mut v = Vec::with_capacity(44 + data as usize);
        v.extend(b"RIFF");
        v.extend((36 + data).to_le_bytes());
        v.extend(b"WAVEfmt ");
        v.extend(16u32.to_le_bytes());
        v.extend(1u16.to_le_bytes()); // PCM
        v.extend(CH.to_le_bytes());
        v.extend(RATE.to_le_bytes());
        v.extend((RATE * CH as u32 * 2).to_le_bytes());
        v.extend((CH * 2).to_le_bytes());
        v.extend(16u16.to_le_bytes());
        v.extend(b"data");
        v.extend(data.to_le_bytes());
        v.resize(44 + data as usize, 0);

        let p = std::env::temp_dir().join(format!(
            "lempi_fixture_{}_{}.wav",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::write(&p, v).unwrap();
        p
    }

    /// Tick until a condition holds, or give up. The silent path is not paced
    /// by real time, so this converges in milliseconds.
    fn tick_until(e: &mut Engine, mut done: impl FnMut(&Engine) -> bool) -> bool {
        for _ in 0..200_000 {
            if done(e) {
                return true;
            }
            e.tick();
        }
        false
    }

    fn plays(st: &PlayerStore) -> i64 {
        st.play_count()
    }

    /// A passage heard past its threshold is written to history
    /// `[SPEC-PLAY-010]`.
    ///
    /// The engine's half of that rule had never been tested end to end: the
    /// judgement was covered in `scrobble`, but nothing had ever played a
    /// passage through this engine and checked that a row appeared.
    #[test]
    fn a_passage_heard_past_its_threshold_is_recorded() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let wav = wav_of(4_000);
        let mut ent = entry(41, wav.to_str().unwrap());
        ent.end_ms = 4_000;
        ent.mbid = Some("aaaaaaaa-0000-0000-0000-000000000041".into());
        e.enqueue(ent);
        h.send(Command::Play);
        e.drain_commands();

        assert!(tick_until(&mut e, |_| plays(&st) > 0), "a play should have been written");
        assert_eq!(plays(&st), 1, "and exactly one, however many ticks it took");
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// The primitive the deferred correction runs on `[REQ-VIS-250]`: grows
    /// with real time from wherever the ring left off, and never claims more
    /// than the passage actually is -- the same cap `advance_shown` applies
    /// to the position display for the identical reason `[REQ-VIS-240]`.
    #[test]
    fn pending_finish_estimate_advances_with_the_clock_and_caps_at_span() {
        let p = PendingFinish { play_id: 1, span_ms: 100, at_ms: 80, since: Instant::now() };
        assert_eq!(p.estimate(), 80, "nothing has elapsed yet");
        std::thread::sleep(Duration::from_millis(30));
        assert_eq!(p.estimate(), 100, "the clock must not run past the passage's own length");
    }

    /// A skip or a seek wipes the ring outright `[REQ-VIS-250]`: whatever a
    /// still-draining play had reached by then is all it is ever going to
    /// reach, so it must be written now rather than left waiting for a tail
    /// that no longer exists.
    #[test]
    fn a_skip_resolves_a_still_draining_correction_rather_than_losing_it() {
        let (_st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        // As if an earlier passage had just departed and was still draining
        // when this test picks the story up.
        let play_id = e
            .store
            .as_ref()
            .unwrap()
            .record_play(90, Some("aaaaaaaa-0000-0000-0000-000000000090"), 400, 500, None)
            .unwrap();
        e.pending_finish =
            Some(PendingFinish { play_id, span_ms: 500, at_ms: 400, since: Instant::now() });

        // Something else has to be live for `skip` to act on at all.
        let wav = wav_of(2_000);
        let mut ent = entry(91, wav.to_str().unwrap());
        ent.end_ms = 2_000;
        e.enqueue(ent);
        h.send(Command::Play);
        e.drain_commands();
        assert!(tick_until(&mut e, |eng| eng.snapshot_live() > 0), "the second passage should have started");

        h.send(Command::Skip);
        e.drain_commands();

        assert!(e.pending_finish.is_none(), "the interrupted correction must be resolved, not left pending");
        let conn = rusqlite::Connection::open(&path).unwrap();
        let heard: i64 = conn
            .query_row("SELECT heard_ms FROM listener_play_history WHERE play_id = ?1", [play_id], |r| {
                r.get(0)
            })
            .unwrap();
        assert!(
            (400..=500).contains(&heard),
            "the resolved figure should be at least what had already drained: got {heard}"
        );
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// The figure `record_play` writes the instant the threshold is crossed
    /// is not the last word `[REQ-VIS-250]`. A passage played all the way
    /// through must read as EXACTLY whole once its drain tail has actually
    /// finished, not frozen at whatever the ring still had queued behind it
    /// the moment the decoder ran out.
    ///
    /// **Needs a real ring, not `silent()`.** A ring of `None` reports zero
    /// frames buffered, always -- `audible_ms` never lags `played_ms` there,
    /// so the bug this guards against cannot occur in that fixture no matter
    /// what the code does. Draining it fully after every tick stands in for
    /// a device consuming what was just mixed, except on the very tick the
    /// passage exhausts: that tick's freshly-submitted tail is still sitting
    /// in the ring when `retire_finished` reads it, which is exactly the gap
    /// a real device leaves too.
    #[test]
    fn a_completed_play_is_corrected_with_what_was_actually_heard() {
        let (st, path) = store();
        let ring = crate::output::OutputRing::new(20_000, crate::output::Volume::new(1.0));
        let (mut e, h) = Engine::new(crate::path::PathHandle::with_ring(ring.clone()), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let wav = wav_of(600);
        let mut ent = entry(46, wav.to_str().unwrap());
        ent.end_ms = 600;
        ent.mbid = Some("aaaaaaaa-0000-0000-0000-000000000046".into());
        e.enqueue(ent);
        h.send(Command::Play);
        e.drain_commands();

        // Left holding a steady backlog rather than drained to empty: a real
        // device keeps a roughly constant amount of latency, not zero, and
        // draining fully every tick let the exhaustion tick's own tiny
        // remainder round down to nothing in milliseconds -- proving
        // nothing about the bug this exists to catch. ~90 ms of stereo
        // audio at 44.1 kHz.
        const KEEP: usize = 8_000;
        let drain = |ring: &crate::output::OutputRing| {
            let mut st = ring.state.lock().unwrap();
            let len = st.ring.len();
            if len > KEEP {
                let mut scratch = vec![0.0f32; len - KEEP];
                st.ring.read(&mut scratch);
            }
        };
        // Let it actually start sounding before watching for it to finish --
        // `live` is empty both before the first admission and after the last
        // retirement, and only the second one is the departure this test
        // wants.
        assert!(tick_until(&mut e, |eng| eng.snapshot_live() > 0), "the passage should have started");
        while e.snapshot_live() > 0 {
            e.tick();
            drain(&ring);
        }

        assert_eq!(plays(&st), 1, "a play should have been written");
        let pending = e.pending_finish.as_ref().expect(
            "a naturally-exhausted play must be deferred, not finalised on the spot",
        );
        assert!(
            pending.at_ms < pending.span_ms,
            "the ring should still have been holding some of the tail: at {} of {}",
            pending.at_ms, pending.span_ms
        );

        // Still sitting at whatever `record_play` wrote when the threshold
        // was first crossed -- the old, buggy answer -- until the clock
        // says the tail is done.
        let row = |c: &rusqlite::Connection| -> (i64, i64) {
            c.query_row("SELECT heard_ms, span_ms FROM listener_play_history", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .unwrap()
        };
        let conn = rusqlite::Connection::open(&path).unwrap();
        let (before, span_check) = row(&conn);
        assert_eq!(span_check, 600);
        assert!(before < 600, "not corrected yet: read {before} of 600 before the clock catches up");

        // The clock closes the rest of the gap, not the ring -- which by now
        // holds nothing at all for this passage.
        std::thread::sleep(std::time::Duration::from_millis(150));
        e.tick();
        assert!(e.pending_finish.is_none(), "the drain estimate should have resolved by now");

        let (heard, span) = row(&conn);
        assert_eq!(span, 600);
        assert_eq!(heard, span, "played to the end must read as exactly whole");
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// A passage dropped before its threshold is **not** a play, and is
    /// suppressed instead `[SPEC-PLAY-050]`.
    #[test]
    fn a_passage_cut_short_becomes_a_skip_not_a_play() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let long = wav_of(30_000);
        let short = wav_of(1_000);
        let mut a = entry(42, long.to_str().unwrap());
        a.end_ms = 30_000; // threshold 15 s, and we will not get near it
        a.mbid = Some("aaaaaaaa-0000-0000-0000-000000000042".into());
        let mut b = entry(43, short.to_str().unwrap());
        b.end_ms = 1_000;
        e.enqueue(a);
        e.enqueue(b);
        h.send(Command::Play);
        e.drain_commands();

        // Let it sound briefly, then move on -- far short of fifteen seconds.
        for _ in 0..40 {
            e.tick();
        }
        h.send(Command::Skip);
        e.drain_commands();
        assert!(
            tick_until(&mut e, |_| {
                st.last_rejected(crate::db::Rejection::Skip).map(|m| !m.is_empty()).unwrap_or(false)
            }),
            "the abandoned passage should have been suppressed"
        );
        assert_eq!(plays(&st), 0, "and it must never have become a play");
        let _ = std::fs::remove_file(&long);
        let _ = std::fs::remove_file(&short);
        let _ = std::fs::remove_file(&path);
    }

    /// The **last** passage, abandoned with nothing behind it, must still be
    /// judged `[SPEC-PLAY-050]`.
    ///
    /// The suspicious case: `record_play` reads the head to do its work, so a
    /// queue that empties leaves it nothing to read. If the rejection is only
    /// written when some *other* passage takes the head, then skipping the last
    /// passage of an evening suppresses nothing and the Director may offer it
    /// straight back.
    #[test]
    fn skipping_the_last_passage_still_suppresses_it() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let wav = wav_of(30_000);
        let mut only = entry(44, wav.to_str().unwrap());
        only.end_ms = 30_000;
        only.mbid = Some("aaaaaaaa-0000-0000-0000-000000000044".into());
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..40 {
            e.tick();
        }

        h.send(Command::Skip);
        e.drain_commands();
        let judged = tick_until(&mut e, |_| {
            st.last_rejected(crate::db::Rejection::Skip).map(|m| !m.is_empty()).unwrap_or(false)
        });
        assert!(judged, "an abandoned last passage must still be suppressed");
        assert_eq!(plays(&st), 0);
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// **A handoff is not a rejection** `[SPEC-BK-065]`.
    ///
    /// The fade is the same one a skip uses, so without the distinction the
    /// passage the listener is *still hearing on the other backend* would earn
    /// a 156-hour suppression for the crime of changing rooms.
    #[test]
    fn handing_a_passage_over_does_not_suppress_it() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let wav = wav_of(30_000);
        let mut only = entry(51, wav.to_str().unwrap());
        only.end_ms = 30_000;
        only.mbid = Some("aaaaaaaa-0000-0000-0000-000000000051".into());
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..40 {
            e.tick();
        }
        assert!(e.head_position().is_some(), "something is playing to hand over");

        e.hand_off_to_silence(600);
        for _ in 0..40 {
            e.tick();
        }

        assert!(
            st.last_rejected(crate::db::Rejection::Skip).unwrap().is_empty(),
            "it moved backends; it was not declined"
        );
        assert_eq!(plays(&st), 0, "and it did not earn a play either");
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// **The clock keeps running after the mixer has finished** `[REQ-VIS-240]`.
    ///
    /// A passage leaves `live` when its decoder is exhausted, a ring's depth
    /// before its last sample is heard. The display used to stop there — about
    /// fifteen seconds short of the end of every passage.
    #[test]
    fn a_finished_passage_keeps_its_position_moving_while_it_is_heard() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(1_500);
        let mut only = entry(91, wav.to_str().unwrap());
        only.end_ms = 1_500;
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..400 {
            e.tick();
        }

        // Mixed to the end and retired, but the listener is still hearing it.
        assert_eq!(e.snapshot_live(), 0, "the mixer has finished with it");
        let (id, _, _) = e.draining.expect("it is remembered as still sounding");
        assert_eq!(id, 91);

        let first = e.shown.as_ref().map(|(_, p)| *p).unwrap_or(0);
        std::thread::sleep(std::time::Duration::from_millis(120));
        e.tick();
        let later = e.shown.as_ref().map(|(_, p)| *p).unwrap_or(0);

        assert!(later >= first, "the position must not go backwards");
        assert!(later <= 1_500, "and must not run past the music: {later}");
        let _ = std::fs::remove_file(&wav);
    }
    /// **Restarting the count moves a mark, it does not clear the counter**
    /// `[REQ-VIS-230]`.
    ///
    /// The cumulative figure answers "has this ever glitched" and must survive
    /// somebody restarting the display, which answers "is it glitching now".
    #[test]
    fn restarting_the_underrun_count_keeps_the_cumulative_one() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.underruns_playing = 4_096;
        let before = e.underrun_since;

        h.send(Command::RestartUnderruns);
        e.drain_commands();

        assert_eq!(e.underruns_playing, 4_096, "the counter itself is untouched");
        assert_eq!(e.underrun_baseline, 4_096, "the mark moved to where it was");
        assert!(e.underrun_since >= before, "and the moment was taken");

        // More arrive after the restart, and only those are shown.
        e.underruns_playing += 500;
        assert_eq!(e.underruns_playing - e.underrun_baseline, 500);
    }

    /// A player that has never been asked still says when it started counting,
    /// rather than leaving the label empty `[REQ-VIS-230]`.
    #[test]
    fn the_count_knows_when_it_started_without_being_asked() {
        let (e, _h) = Engine::new(crate::path::PathHandle::silent(), 3);
        assert!(e.underrun_since > 0, "seeded at construction, not at first reset");
        assert_eq!(e.underrun_baseline, 0);
    }
    /// **Seeking to the end must not earn a play** `[SPEC-PLAY-012]`.
    ///
    /// The whole reason the engine now measures heard time rather than reading
    /// the position: a jump to the last chorus puts the position past any
    /// threshold instantly, and nobody listened to the distance.
    #[test]
    fn seeking_past_the_threshold_does_not_earn_a_play() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let wav = wav_of(60_000);
        let mut only = entry(81, wav.to_str().unwrap());
        only.end_ms = 60_000;
        only.mbid = Some("aaaaaaaa-0000-0000-0000-000000000081".into());
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..20 {
            e.tick();
        }

        // Straight past the half-way mark, which is the threshold for a minute.
        e.seek_to(55_000);
        for _ in 0..40 {
            e.tick();
        }

        assert_eq!(plays(&st), 0, "the distance was travelled, not heard");
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// And the seek itself lands where it was asked to, alone.
    #[test]
    fn a_seek_moves_the_passage_and_leaves_one_thing_sounding() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(60_000);
        let mut only = entry(82, wav.to_str().unwrap());
        only.end_ms = 60_000;
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..20 {
            e.tick();
        }

        e.seek_to(30_000);

        assert_eq!(e.snapshot_live(), 1, "it lands alone");
        let (id, at) = e.head_position().expect("still playing");
        assert_eq!(id, 82, "the same passage, moved");
        assert!(at >= 30_000, "at the point asked for, not back at the start: {at}");
        let _ = std::fs::remove_file(&wav);
    }

    /// A seek past the end would open a decoder with nothing to decode.
    #[test]
    fn a_seek_beyond_the_span_is_clamped_inside_it() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(10_000);
        let mut only = entry(83, wav.to_str().unwrap());
        only.end_ms = 10_000;
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..20 {
            e.tick();
        }

        e.seek_to(999_999);

        assert_eq!(e.snapshot_live(), 1, "still sounding rather than ended");
        let _ = std::fs::remove_file(&wav);
    }

    /// Seeking with nothing playing is a no-op, not a panic.
    #[test]
    fn seeking_with_nothing_playing_does_nothing() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.seek_to(5_000);
        assert_eq!(e.snapshot_live(), 0);
    }

    /// **A passage that arrives already counted is not counted again**
    /// `[SPEC-BK-065]`.
    ///
    /// `[SPEC-BK-037]` named this hazard before there was code to have it: a
    /// passage crossing mid-play can be judged by both sides. It earns neither a
    /// second play nor — the worse failure — a rejection for a passage that
    /// played.
    #[test]
    fn a_passage_adopted_mid_play_is_not_counted_twice() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let wav = wav_of(2_000);
        let mut only = entry(61, wav.to_str().unwrap());
        only.end_ms = 2_000;
        only.mbid = Some("aaaaaaaa-0000-0000-0000-000000000061".into());
        e.enqueue(only);
        // The other backend already wrote this one's play.
        e.adopt_counted(61);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..400 {
            e.tick();
        }

        assert_eq!(plays(&st), 0, "its play is already in the history, written by the other side");
        assert!(
            st.last_rejected(crate::db::Rejection::Skip).unwrap().is_empty(),
            "and it must not be suppressed either -- it played"
        );
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// The adoption is for **one** passage, not a standing amnesty. The next one
    /// is judged normally, or a single handoff would silence accounting for the
    /// rest of the session.
    #[test]
    fn adopting_one_passage_does_not_excuse_the_next() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.adopt_counted(70);
        assert!(!e.head_counted(), "nothing is playing yet");
        // A different passage arriving must not consume the adoption.
        e.enqueue(entry(71, "b.mp3"));
        assert!(!e.head_counted());
    }

    /// The same fade, asked for the other way, still suppresses — or the
    /// distinction above would have quietly disabled skip suppression.
    #[test]
    fn an_ordinary_fade_to_silence_still_suppresses() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let wav = wav_of(30_000);
        let mut only = entry(52, wav.to_str().unwrap());
        only.end_ms = 30_000;
        only.mbid = Some("aaaaaaaa-0000-0000-0000-000000000052".into());
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..40 {
            e.tick();
        }

        e.fade_to_silence(600);
        let judged = tick_until(&mut e, |_| {
            st.last_rejected(crate::db::Rejection::Skip).map(|m| !m.is_empty()).unwrap_or(false)
        });

        assert!(judged, "a fade that is not a handoff is still the listener leaving");
        let _ = std::fs::remove_file(&wav);
        let _ = std::fs::remove_file(&path);
    }

    /// The position that crosses is the **audible** one `[SPEC-BK-065]`.
    ///
    /// `played_ms` runs ahead by whatever is sitting in the output ring — about
    /// 14 s here — and handing that over would start the other side that far
    /// into the future.
    #[test]
    fn the_position_handed_over_is_the_one_being_heard() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        let wav = wav_of(30_000);
        let mut only = entry(53, wav.to_str().unwrap());
        only.end_ms = 30_000;
        e.enqueue(only);
        h.send(Command::Play);
        e.drain_commands();
        for _ in 0..40 {
            e.tick();
        }

        let (id, pos) = e.head_position().expect("something is playing");
        assert_eq!(id, 53);
        assert!(pos < 30_000, "inside the span it is playing, not past it");
        let _ = std::fs::remove_file(&wav);
    }

    /// A fade to silence empties the engine, so a handoff leaves nothing
    /// behind to play `[SPEC-BK-030]`.
    ///
    /// The queue in particular: those passages are being rebuilt on the other
    /// backend, and one left here would be promoted into the fade and start
    /// playing on a side the listener has just left.
    #[test]
    fn a_fade_to_silence_leaves_nothing_queued_or_sounding() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.enqueue(entry(1, "a.mp3"));
        e.enqueue(entry(2, "b.mp3"));
        // Deliberately NOT ticked: a tick would try to open these names, fail,
        // and empty the queue for the wrong reason. What is under test is that
        // the fade takes the queue, not that a missing file does.
        assert_eq!(e.queued().count(), 2, "something to lose");

        e.fade_to_silence(600);

        assert!(e.queued().next().is_none(), "the queue went with the handoff");
        assert_eq!(e.snapshot_live(), 0, "and nothing is sounding");
    }

    /// With no output there is nothing to fade, and it says so rather than
    /// claiming a smooth stop that never happened `[PI3-API-030]`.
    #[test]
    fn a_silent_path_reports_a_cut_not_a_fade() {
        use crate::switch::{FadeOut, Stopped};
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.enqueue(entry(1, "a.mp3"));
        assert_eq!(e.fade_out(600), Stopped::Cut, "no ring, so no fade to claim");
    }

    /// The listener's own skip shape is not disturbed by a handoff borrowing it.
    #[test]
    fn fading_out_restores_the_skip_setting() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        h.send(Command::SetSkipFade(1_500));
        e.drain_commands();
        e.fade_to_silence(200);
        std::thread::sleep(Engine::PUBLISH_EVERY + std::time::Duration::from_millis(20));
        e.tick();
        assert_eq!(h.snapshot().skip_fade_ms, 1_500, "borrowed, then given back");
    }

    /// Every listener setting must reach the snapshot the settings page reads.
    ///
    /// Two of them did not. `skip_suppress_h` and `dequeue_suppress_h` were
    /// declared on `PlayerState`, serialised by the web layer and read by the
    /// skin, but never assigned in `publish` — so the page would have shown a
    /// confident **0 hours** for both while the engine held 156 and 18. A
    /// control displaying a value the engine does not hold is worse than one
    /// displaying nothing, because it invites a person to trust it.
    #[test]
    fn every_listener_setting_reaches_the_snapshot() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.tick();
        let s = h.snapshot();
        assert_eq!(s.skip_suppress_h, crate::SKIP_SUPPRESS_H);
        assert_eq!(s.dequeue_suppress_h, crate::DEQUEUE_SUPPRESS_H);
        assert_eq!(s.queue_depth, 3, "the depth the engine was built with");
        assert_eq!(s.sample_interval_ms, crate::SAMPLE_INTERVAL_MS);

        // And a change reaches it too, rather than only the default.
        h.send(Command::SetSkipSuppress(72));
        h.send(Command::SetDequeueSuppress(9));
        h.send(Command::SetQueueDepth(8));
        h.send(Command::SetSampleInterval(2_500));
        e.drain_commands();
        // Publishing is throttled to `PUBLISH_EVERY`, so a change made just
        // after one publish is not visible until the next. Waiting past the
        // window is what a browser polling the snapshot does anyway.
        std::thread::sleep(Engine::PUBLISH_EVERY + std::time::Duration::from_millis(20));
        e.tick();
        let s = h.snapshot();
        assert_eq!(
            (s.skip_suppress_h, s.dequeue_suppress_h, s.queue_depth, s.sample_interval_ms),
            (72, 9, 8, 2_500)
        );
    }

    /// Out-of-range values are clamped rather than accepted, on the way in from
    /// a browser as much as from a file.
    #[test]
    fn settings_from_outside_are_clamped() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        h.send(Command::SetQueueDepth(0));
        h.send(Command::SetSampleInterval(1));
        e.drain_commands();
        std::thread::sleep(Engine::PUBLISH_EVERY + std::time::Duration::from_millis(20));
        e.tick();
        let s = h.snapshot();
        assert_eq!(s.queue_depth, crate::QUEUE_DEPTH_MIN, "a depth of zero has no lookahead");
        assert_eq!(s.sample_interval_ms, crate::SAMPLE_INTERVAL_MIN_MS);
    }

    /// Taking a passage out of the queue before it plays is a rejection, and
    /// the shorter kind `[SPEC-PLAY-055]`.
    #[test]
    fn removing_a_queued_passage_records_a_dequeue() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let mut ent = entry(7, "a.mp3");
        ent.mbid = Some(DQ_MBID.into());
        e.enqueue(ent);
        let qid = e.queued().next().expect("queued").qid;

        h.send(Command::RemoveQueued(qid));
        e.drain_commands();

        assert!(e.queued().next().is_none(), "it really left the queue");
        let deq = st.last_rejected(crate::db::Rejection::Dequeue).unwrap();
        assert_eq!(deq.len(), 1, "one dequeue recorded");
        assert!(deq.contains_key(DQ_MBID), "recorded against its recording");
        assert!(
            st.last_rejected(crate::db::Rejection::Skip).unwrap().is_empty(),
            "a removal is not a skip: they earn different windows"
        );
        let _ = std::fs::remove_file(&path);
    }

    /// A queue edit reaches the snapshot on the very next tick, without
    /// waiting out the publish throttle `[REQ-VIS-185]`.
    ///
    /// The throttle exists for the position counter ticking along, and a
    /// change of audible passage already bypasses it. An edit deserves the
    /// same: the listener who pressed the button is waiting on this answer,
    /// and the engine had applied it in microseconds while the page was told
    /// up to `PUBLISH_EVERY` later. That gap was read as the control being
    /// slow to obey rather than the display being slow to say so.
    ///
    /// The TAIL is what is removed, deliberately. Admission only ever takes
    /// the head, so a test that dropped the head could pass on the admission
    /// having published instead of on the edit having done so.
    #[test]
    fn a_queue_edit_does_not_wait_out_the_publish_throttle() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        // Six, because a tick admits from the head and readies the one behind
        // it, so a short queue is empty before there is a tail to name.
        for (n, p) in [(1, "a.mp3"), (2, "b.mp3"), (3, "c.mp3"),
                       (4, "d.mp3"), (5, "e.mp3"), (6, "f.mp3")] {
            e.enqueue(entry(n, p));
        }
        // The first publish, which also starts the throttle.
        e.tick();
        let before: Vec<u64> = h.snapshot().queue.iter().map(|q| q.qid).collect();
        assert!(before.len() >= 2, "need a tail that admission will not take, got {before:?}");
        let tail = *before.last().expect("a tail");

        // No sleep anywhere: the throttle window is wide open, and that is the
        // whole point of the assertion.
        h.send(Command::RemoveQueued(tail));
        e.tick();
        let after: Vec<u64> = h.snapshot().queue.iter().map(|q| q.qid).collect();
        assert!(
            !after.contains(&tail),
            "a removal must reach the snapshot at once, still saw {after:?}"
        );
    }

    /// Removing a passage that is not there records nothing. Without the
    /// existence check a stale id from a browser would suppress a recording the
    /// listener never touched.
    #[test]
    fn removing_a_passage_that_is_not_queued_records_nothing() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let mut ent = entry(7, "a.mp3");
        ent.mbid = Some(DQ_MBID.into());
        e.enqueue(ent);

        h.send(Command::RemoveQueued(4242)); // never a real qid
        e.drain_commands();

        assert!(e.queued().next().is_some(), "the real entry is untouched");
        assert!(
            st.last_rejected(crate::db::Rejection::Dequeue).unwrap().is_empty(),
            "nothing was removed, so nothing was declined"
        );
        let _ = std::fs::remove_file(&path);
    }

    /// A passage the engine could not open leaves the queue too, and it must
    /// leave **no mark** `[REQ-PD-112]`. A failure is not a preference, and
    /// suppressing a recording because its file was missing would punish the
    /// listener for a fault they did not commit.
    #[test]
    fn a_passage_that_would_not_open_is_not_a_rejection() {
        let (st, path) = store();
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.attach_store(PlayerStore::open(&path).unwrap());

        let mut ent = entry(9, "no-such-file-anywhere.mp3");
        ent.mbid = Some(DQ_MBID.into());
        e.enqueue(ent);
        for _ in 0..8 {
            e.tick();
        }

        assert!(!e.take_dropped().is_empty(), "the unopenable passage was dropped");
        assert!(
            st.last_rejected(crate::db::Rejection::Dequeue).unwrap().is_empty()
                && st.last_rejected(crate::db::Rejection::Skip).unwrap().is_empty(),
            "a file that would not open must suppress nothing"
        );
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn enqueue_next_puts_a_passage_first_in_the_queue() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.enqueue(entry(1, "a.mp3"));
        e.enqueue(entry(2, "b.mp3"));
        h.send(Command::EnqueueNext(entry(99, "browsed.mp3")));
        e.drain_commands();
        let ids: Vec<i64> = e.queued().map(|q| q.passage_id).collect();
        assert_eq!(ids, vec![99, 1, 2], "browsed passage is next up");
    }

    /// The same for a batch, in the order it was given.
    #[test]
    fn a_batch_queued_next_goes_to_the_top_in_order() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        e.enqueue(entry(1, "a.mp3"));
        h.send(Command::EnqueueMany(
            vec![entry(10, "x.mp3"), entry(11, "y.mp3")],
            Placement::Next,
        ));
        e.drain_commands();
        let ids: Vec<i64> = e.queued().map(|q| q.passage_id).collect();
        assert_eq!(ids, vec![10, 11, 1]);
    }

    #[test]
    fn an_unopenable_passage_is_skipped_not_fatal() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 3);
        h.send(Command::Play);
        e.enqueue(entry(1, "does-not-exist.mp3"));
        e.tick();
        assert_eq!(h.snapshot().queue_len, 0, "bad passage must leave the queue");
        assert_eq!(h.snapshot().active_streams, 0, "and must not become live");
        assert!(!e.is_shutdown(), "and must not end playback");
    }

    #[test]
    fn shortfall_reports_the_replenishment_need() {
        let (mut e, _h) = Engine::new(crate::path::PathHandle::silent(), 3);
        assert_eq!(e.shortfall(), 3);
        e.enqueue(entry(1, "a.mp3"));
        assert_eq!(e.shortfall(), 2);
    }

    #[test]
    fn idle_requires_the_output_to_have_drained() {
        let mut s = PlayerState::default();
        assert!(s.is_idle(), "empty everything is idle");
        s.output_buffered = 4096;
        assert!(!s.is_idle(), "buffered audio means playback is still in progress");
    }

    fn store() -> (PlayerStore, PathBuf) {
        let p = std::env::temp_dir().join(format!(
            "lempi_eng_{}_{}.db",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let _ = std::fs::remove_file(&p);
        (PlayerStore::open(&p).unwrap(), p)
    }

    /// Without a store the engine must behave identically, not panic or stall.
    #[test]
    fn persistence_is_optional() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        h.send(Command::Play);
        e.tick();
        assert!(h.snapshot().playing);
    }

    #[test]
    fn play_state_reaches_the_database_on_change() {
        let (st, path) = store();
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        e.attach_store(PlayerStore::open(&path).unwrap());

        h.send(Command::Play);
        e.tick();
        assert_eq!(st.load().unwrap(), Some((None, 0, true)), "play must be saved at once");

        h.send(Command::Pause);
        e.tick();
        assert_eq!(
            st.load().unwrap(),
            Some((None, 0, false)),
            "a state change must not wait for the throttle"
        );
        let _ = std::fs::remove_file(path);
    }

    /// Resuming must report position within the PASSAGE. If it restarted at
    /// zero, the next save would move backwards and resume would walk to the
    /// start of the passage a restart at a time.
    #[test]
    fn resume_reports_position_within_the_passage() {
        let f = crate::decoder::tests::tmp("resume");
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        let mut ent = entry(1, f.to_str().unwrap());
        ent.end_ms = 5_000;
        e.resume_at(2_000);
        e.enqueue(ent);
        h.send(Command::Play);
        e.tick();
        let pos = h.snapshot().position_ms;
        assert!(
            (2_000..2_400).contains(&pos),
            "resumed passage reported {pos} ms, expected ~2000"
        );
        let _ = std::fs::remove_file(f);
    }

    /// One-shot: the passage after the resumed one starts where the library
    /// says, not two seconds in.
    #[test]
    fn the_resume_offset_applies_only_once() {
        let f = crate::decoder::tests::tmp("once");
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        let mut a = entry(1, f.to_str().unwrap());
        a.end_ms = 1_000;
        let mut b = entry(2, f.to_str().unwrap());
        b.end_ms = 5_000;
        e.resume_at(2_000);
        e.enqueue(a);
        e.enqueue(b);
        h.send(Command::Play);
        for _ in 0..400 {
            e.tick();
            if h.snapshot().current.as_ref().map(|c| c.passage_id) == Some(2) {
                break;
            }
        }
        let s = h.snapshot();
        assert_eq!(s.current.map(|c| c.passage_id), Some(2), "second passage never started");
        assert!(s.position_ms < 1_000, "second passage inherited the resume offset");
        let _ = std::fs::remove_file(f);
    }

    /// A resume point past the end (the passage was re-trimmed since) replays
    /// the passage rather than decoding nothing.
    #[test]
    fn an_out_of_range_resume_point_does_not_strand_the_passage() {
        let f = crate::decoder::tests::tmp("clamp");
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        let mut ent = entry(1, f.to_str().unwrap());
        ent.end_ms = 3_000;
        e.resume_at(99_000);
        e.enqueue(ent);
        h.send(Command::Play);
        e.tick();
        assert_eq!(h.snapshot().active_streams, 1, "clamped resume must still open");
        let _ = std::fs::remove_file(f);
    }

    /// Exiting by dropping the engine -- the common path when the queue runs
    /// dry -- must still leave a current resume point.
    #[test]
    fn the_final_position_is_saved_on_drop() {
        let (st, path) = store();
        {
            let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
            e.attach_store(PlayerStore::open(&path).unwrap());
            h.send(Command::Play);
            e.tick();
        }
        assert_eq!(st.load().unwrap().map(|s| s.2), Some(true), "drop must flush the state");
        let _ = std::fs::remove_file(path);
    }

    /// Silence while paused is the design, not a fault. If it counted, the
    /// metric would be dominated by idle time and could never flag a real one.
    #[test]
    fn underruns_while_paused_are_not_counted() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        h.send(Command::Pause);
        for _ in 0..5 {
            e.tick();
        }
        assert_eq!(h.snapshot().underrun_samples, 0);
    }

    #[test]
    fn state_is_published_every_tick() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 2);
        e.enqueue(entry(7, "x.mp3"));
        e.tick();
        let s = h.snapshot();
        assert_eq!(s.queue_len + s.active_streams, 0, "unopenable passage clears");
    }
}
