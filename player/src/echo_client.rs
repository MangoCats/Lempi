//! Following a master, from inside the player `[GDE-ECHO-330]`.
//!
//! `echoprobe` proved the wire can be read; this is the same reading wired to
//! an engine that can act on it. The division of labour is deliberate and
//! narrow: everything about *when* and *how far in* is decided by
//! [`crate::echo`], everything about *what plays* is decided by the engine,
//! and this module only carries messages between them and resolves a passage
//! id against the local library.
//!
//! It resolves ids because an echo node holds the same music on its own disk
//! `[GDE-ECHO-420]` -- that is the capability the whole design rests on. What
//! crosses the network is an intention, never audio.
//!
//! **A master is not trusted with this node's timing.** The schedule says when
//! a sample should sound; this node's own presentation offset decides when to
//! submit for that to happen `[GDE-ECHO-410]`, and a schedule it cannot meet
//! is declined rather than approximated.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use futures_util::StreamExt;

use crate::echo::{EchoState, Follow, Follower, NodeTiming};
use crate::engine::{Command, EngineHandle};

/// Only the field an echo node cares about. Serde ignores the rest of the
/// snapshot, which is how this rides a socket built for a browser.
#[derive(serde::Deserialize)]
struct Snapshot {
    #[serde(default)]
    echo: EchoState,
    /// What the node being followed will play next, in order `[GDE-ECHO-500]`.
    ///
    /// Already on this socket for the browser's sake, and ignored until now,
    /// which is why a follower's own "coming up" disagreed with the master's:
    /// it kept choosing for itself and was overridden one passage at a time.
    #[serde(default)]
    queue: Vec<AnnouncedEntry>,
}

#[derive(serde::Deserialize)]
struct AnnouncedEntry {
    passage_id: i64,
}

pub struct Following {
    pub timing: NodeTiming,
    pub db: PathBuf,
    pub library: PathBuf,
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

/// Follow `cfg.url` until the process ends, reconnecting as needed.
///
/// Reconnection is a plain retry with no backoff state and no resynchronising
/// handshake, because there is nothing to resynchronise: every message is
/// absolute and the next one to arrive is sufficient on its own
/// `[GDE-ECHO-320]`. A node that misses an hour of them rejoins on the first
/// one it sees.
/// Read the host the listener has set, as a URL, or `None` for independent.
///
/// A bare name gets no port, which is port 80 -- what `bose` serves. A node on
/// another port is named with one, `lempiplay3:5720`, because the fleet is not
/// uniform and pretending otherwise would make the control work on some nodes
/// and silently not on others `[SPEC-ECHO-010]`.
fn wanted_url(handle: &EngineHandle) -> Option<String> {
    let host = handle.state.lock().ok()?.echo_node.follow_host.clone();
    let host = host.trim();
    (!host.is_empty()).then(|| format!("ws://{host}/ws"))
}

/// The least a mid-passage join ever aims ahead of itself `[SPEC-ECHO-030]`.
///
/// A floor, not the budget: what the budget actually has to cover is whatever
/// this node's own engine needs before a commanded start can sound, and only
/// the engine knows that `[GDE-ECHO-375]`. This is what a node that has not
/// published a figure yet gets -- enough for a 10 ms tick, a snapshot's
/// cadence, and the engine's own 100 ms late limit.
const MID_JOIN_MARGIN: Duration = Duration::from_secs(1);

/// On top of whatever the engine says it needs.
///
/// The engine's figure covers the work; this covers everything around it --
/// up to half a second to learn of the join at the snapshot's cadence, a
/// 10 ms tick, and `ECHO_START_LATE_LIMIT`. Erring long costs a follower
/// nothing but waiting; erring short costs the whole join.
const MID_JOIN_SLACK: Duration = Duration::from_secs(1);

/// And no further out than this, however wild the engine's estimate gets.
///
/// `[GDE-ECHO-325]` allows five seconds for a resync; a join aimed past that
/// is not a join any more. Deliberately **above** the engine's own worst case
/// -- `SKIP_LEAD_MAX_MS` plus `ECHO_PREP_MAX_MS`, 4 s -- so the clamp can
/// only ever catch a figure that is already nonsense, never trim the budget
/// back below what the engine really needs. That is the trap this whole
/// finding is: a ceiling chosen without reference to the thing it bounds.
const MID_JOIN_MARGIN_MAX: Duration = Duration::from_secs(5);

/// How far ahead of itself a mid-passage join must aim, on this node.
///
/// **Read from the engine, not assumed** `[GDE-ECHO-375]`. The offset term
/// cancels inside `join_mid_passage`, so this margin *is* the entire budget a
/// join has -- and the engine fires a commanded start early by `skip_lead_ms`
/// plus the preparation it has measured on itself, which begins at 400 ms and
/// is explicitly permitted to reach 2000 `[GDE-ECHO-342]`. Against a fixed
/// one-second budget, a node that once paid for a slow seek had every later
/// join declined as `TooLate` for ever after, because the estimate is only
/// revised by a join that actually fires. Two models of one quantity; this is
/// the one that reads the other.
fn mid_join_margin(handle: &EngineHandle) -> Duration {
    let needs = handle
        .state
        .lock()
        .ok()
        .map(|s| Duration::from_millis(s.echo_node.start_lead_ms))
        .unwrap_or_default();
    (needs + MID_JOIN_SLACK).clamp(MID_JOIN_MARGIN, MID_JOIN_MARGIN_MAX)
}

/// How far out this node may be before the offset correction acts.
///
/// **Set by the measurement, not by the ear.** A listener can place two
/// speakers to a millisecond `[SPEC-DLY-020]`, but the anchor this residual
/// comes from carries the output ring's own depth jitter -- tens of
/// milliseconds `[LOG-P4-010]` -- so a deadband below that would have the node
/// chasing noise, which is the exact failure `[GDE-ECHO-350]` exists to
/// prevent. Alignment finer than this needs a more precise anchor, not a
/// smaller number here.
const OFFSET_DEADBAND: Duration = Duration::from_millis(8);

/// Below this the boundary knobs cannot help and the frame trim takes over
/// `[GDE-ECHO-349]`.
///
/// One mix quantum, 46 ms at 44.1 kHz stereo, rounded up. Above it a passage
/// boundary can shift the whole error at once; below it the coarse knob's
/// step is larger than the error itself and only the 23 us actuator will do.
const OFFSET_ENDGAME: Duration = Duration::from_millis(50);

/// How far this node's own transition may sit from the announced one and
/// still be called the same transition `[GDE-ECHO-353]`.
///
/// Generous, because it is separating two cases that are far apart rather
/// than measuring either: an ordinary boundary both nodes are heading to
/// within a second or two of each other, against a skip that moved the
/// master's by minutes. The offset correction handles everything inside it
/// `[GDE-ECHO-340]`, and a scheduled start would be the wrong tool there
/// anyway -- it cuts the ring.
const FLOW_TOLERANCE: Duration = Duration::from_secs(5);

/// How long a committed placement suppresses the other way of placing
/// `[GDE-ECHO-354]`.
///
/// Long enough to cover a scheduled start's lead and the engine acting on it;
/// short enough that a start the engine declined is retried rather than
/// leaving the node stranded on the wrong passage `[GDE-ECHO-355]`.
const COMMITMENT_HOLDS: Duration = Duration::from_secs(6);

/// How the filtered residual is taken `[GDE-ECHO-348]`.
const RESIDUAL_WINDOW: Duration = Duration::from_secs(120);
const RESIDUAL_MIN_SAMPLES: usize = 60;

// There was an `OFFSET_MAX_BITE` here, half a second `[GDE-ECHO-341]`. It is
// gone `[GDE-ARC-051]`: the transition is asked for the whole residual, so an
// offset is one correction rather than a walk across several passages. Its
// reasoning -- half a second of crossfade is imperceptible -- was about the
// overlap, and the overlap stopped being the only actuator `[GDE-ARC-052]`.

/// Beyond this, waiting for the next transition is worse than a biased jump,
/// and the node places its first sample afresh instead `[GDE-ECHO-344]`.
///
/// **Above the join bias, and deliberately.** A join lands a few hundred
/// milliseconds to a second late `[GDE-ECHO-342]`; a threshold at or below
/// that would have every join trigger the next one, for ever. 1.5 s clears the
/// worst bias seen with room to spare, so a join always lands *inside* the
/// band and the nudges take it from there.
///
/// **The arithmetic for the upper side changed, and the number did not.** It
/// used to be "at 500 ms a transition, five seconds takes forty minutes"; with
/// the bite gone, any offset the boundary can express is one transition. What
/// is left is a wait: a correction lands at the master's next passage, four to
/// six minutes on this library. So the question is whether sitting `x` out for
/// up to six minutes beats jumping now and landing within the join bias.
/// Below about twice that bias it does not -- a rejoin from 700 ms to 500 ms
/// buys nothing and cuts the ring `[GDE-ARC-042]` -- and twice the worst bias
/// is where 1.5 s already sat. **Fixing `placement()` `[GDE-ARC-044]` is what
/// would move this number**, because a cheap join is worth reaching for sooner.
const OFFSET_REJOIN_BEYOND: Duration = Duration::from_millis(1_500);

/// The rate fit's window, and what it takes before it means anything.
///
/// An hour, because 30 ms of anchor scatter sampled twice a second resolves a
/// slope to about 0.35 ppm over that span and only 5 ppm over ten minutes
/// `[RateEstimate]`. Fifteen minutes is the earliest anything is published,
/// and it is worth a couple of ppm then -- enough to start correcting the bulk
/// of a 14 ppm drift while the estimate sharpens.
const RATE_WINDOW: Duration = Duration::from_secs(3600);
const RATE_MIN_SPAN: Duration = Duration::from_secs(900);
const RATE_MIN_SAMPLES: usize = 200;

/// This node's presentation offset as it stands **now**.
///
/// Read every pass rather than taken at startup, because the listener can move
/// it from the settings panel at any moment `[SPEC-DLY-010]` and a follower
/// holding a figure from boot would schedule against a delay nobody has any
/// more. It is the measured half plus the calibrated one, already clamped by
/// the engine `[GDE-ECHO-430]`.
fn live_timing(handle: &EngineHandle, fallback: NodeTiming) -> NodeTiming {
    match handle.state.lock() {
        Ok(s) => NodeTiming {
            presentation_offset_frames: s.echo_node.offset_frames,
            rate: if s.echo_node.rate > 0 { s.echo_node.rate } else { fallback.rate },
        },
        Err(_) => fallback,
    }
}

/// This node's own anchor, as it publishes it to anyone following *it*.
fn own_anchor(handle: &EngineHandle) -> Option<crate::echo::DriftAnchor> {
    handle.state.lock().ok()?.echo.anchor
}

/// What one connection to a master accumulates.
///
/// Held together because every field is reset by the same event -- losing the
/// connection -- and because passing five of them separately is how a
/// parameter list stops being readable.
struct FollowState {
    /// The last line printed, so a steady state is not restated twice a second.
    note: String,
    /// The master passage a placement was last committed to, and when.
    ///
    /// **Expiring, not permanent.** It exists so the scheduled start and the
    /// mid-passage join stop racing each other `[GDE-ECHO-354]`, but a
    /// commitment the engine then declined -- too late to meet, say -- must
    /// not lock the passage out for ever. It did once: a skip the follower
    /// could not reach was declined, the marker blocked the join that would
    /// have rescued it, and the node played something else entirely
    /// `[GDE-ECHO-355]`.
    mid_joined: Option<(i64, std::time::Instant)>,
    /// The master passage an offset correction was last sent for.
    corrected: Option<i64>,
    /// The relative rate fit that drives the trim `[GDE-ECHO-340]`.
    rate: crate::echo::RateEstimate,
    /// The announced queue last adopted, so an unchanged one costs no
    /// database work. The snapshot arrives twice a second.
    queue: Vec<i64>,
    /// An offset too large for a transition to absorb, waiting for a
    /// scheduled start to place the first sample afresh `[GDE-ECHO-340]`.
    want_rejoin: bool,
    /// The followed node's clock, as this one estimates it `[GDE-ECHO-366]`.
    clock: crate::echo::MasterClock,
    /// The residual, seen through two minutes rather than one reading
    /// `[GDE-ECHO-348]`.
    filtered: crate::echo::ResidualFilter,
    /// Watching where a join just landed, while one is being watched
    /// `[GDE-ARC-061]`.
    landing: Option<Landing>,
    /// The rate correction currently being applied, ppm `[GDE-ECHO-346]`.
    ///
    /// Carried because the fit measures what is LEFT after this correction,
    /// not the drift itself. Treating a fitted slope as the whole answer sets
    /// the trim to `R - A` when it already holds `A`, which settles at half
    /// the drift and oscillates about it.
    applied_ppm: f64,
}

impl FollowState {
    fn new() -> Self {
        Self {
            note: String::new(),
            landing: None,
            mid_joined: None,
            corrected: None,
            rate: crate::echo::RateEstimate::new(
                RATE_WINDOW, RATE_MIN_SPAN, RATE_MIN_SAMPLES),
            queue: Vec::new(),
            want_rejoin: false,
            clock: crate::echo::MasterClock::new(
                CLOCK_WINDOW, Duration::from_secs(1)),
            applied_ppm: 0.0,
            filtered: crate::echo::ResidualFilter::new(
                RESIDUAL_WINDOW, RESIDUAL_MIN_SAMPLES),
        }
    }
}

/// How far two nodes' clocks may differ before nothing here means anything.
///
/// Generous, because it is not measuring quality: a healthy pair agrees to
/// milliseconds and the failure it catches is *days*, a node booted without an
/// RTC before NTP has stepped it `[GDE-ECHO-365]`. Thirty seconds is far past
/// any transport delay and far short of the fault.
const MAX_CLOCK_SKEW: Duration = Duration::from_secs(30);

/// How long a run of clock readings the offset is taken from.
///
/// Long enough that one quiet moment on the network supplies an
/// uncontaminated sample `[MasterClock]`, short enough to follow a master
/// whose own clock is being disciplined underneath it.
const CLOCK_WINDOW: Duration = Duration::from_secs(60);

/// The passage this node will play next of its own accord.
///
/// `None` when nothing is queued, which is the case a scheduled start exists
/// for.
fn next_up(handle: &EngineHandle) -> Option<i64> {
    handle.state.lock().ok()?.queue.first().map(|e| e.passage_id)
}

/// How long until this node reaches its own next passage, in ms.
///
/// `None` when nothing is playing, or when the passage has no length to
/// measure against -- a live capture, say -- in which case a follower has no
/// business guessing and should act on what it was told.
fn own_transition_in_ms(handle: &EngineHandle) -> Option<u64> {
    let s = handle.state.lock().ok()?;
    let cur = s.current.as_ref()?;
    let dur = cur.duration_ms();
    (dur > 0).then(|| dur.saturating_sub(s.position_ms))
}

/// Whether this node is playing that passage or is going to.
///
/// **Playing is not enough to ask.** `current` is the *audible* passage, and
/// the output ring is some fifteen seconds deep `[LOG-ECHO-020]`, so for a
/// long window after a follower admits a passage it still names the previous
/// one. The master's anchor switches as soon as its own ring drains, which is
/// sooner -- so a guard that only asks what is playing sees a mismatch at
/// every ordinary transition and joins into a passage the node was already
/// flowing into. Each of those joins cuts the ring and re-imposes the join
/// bias `[GDE-ECHO-342]`, which is how `lempiplay3` held a steady 0.9 s of lag
/// through a correction loop that was working perfectly `[GDE-ECHO-343]`.
///
/// The queue is the rest of the answer: a passage already coming needs no
/// join, only patience.
fn coming_here(handle: &EngineHandle, passage_id: i64) -> bool {
    let Ok(s) = handle.state.lock() else { return false };
    s.current.as_ref().is_some_and(|e| e.passage_id == passage_id)
        || s.queue.iter().any(|e| e.passage_id == passage_id)
}

/// Would flowing into this node's own next transition land near the announced
/// one `[GDE-ECHO-353]`?
///
/// **Both sides are air times, and both are this node's own** `[GDE-ECHO-376]`.
/// The comparison first written put this node's passage *ending* against the
/// master's *submit* instant, which differ by the transition's overlap plus
/// this node's presentation offset. It passed because both terms are small
/// against a five-second tolerance -- and a passage with the three-to-five
/// second lead-out the library calls the rare-but-wanted case makes it fail at
/// every boundary, reinstating the ring cut and the join bias `[GDE-ECHO-343]`
/// at each one and starving a rate fit that needs fifteen unbroken minutes.
///
/// So: when does this node's *next* passage begin to sound, and when did the
/// master say its own would? `at` is a submit instant, one presentation offset
/// before the sound it was computed from `[GDE-ECHO-410]`, and this node's own
/// transition begins one overlap before its current passage runs out
/// `[overlap_ms]`.
/// **How far out this node's own next transition would land**, in
/// milliseconds, positive when it would sound too **late** -- the same sign as
/// `EchoCorrectNextStart`, which takes "start this much earlier"
/// `[GDE-ARC-054]`.
///
/// `None` when either figure is missing, which is the only honest answer:
/// without both there is nothing to compare `[GOV-SRC-040]`.
fn flow_error_ms(
    handle: &EngineHandle,
    timing: NodeTiming,
    at: u64,
    now_master: u64,
) -> Option<i64> {
    let sound_at = at.saturating_add(timing.offset().as_nanos() as u64);
    let mine = own_next_starts_in_ms(handle)?;
    let theirs = sound_at.checked_sub(now_master)? / 1_000_000;
    Some(mine as i64 - theirs as i64)
}

/// Whether that error is small enough to call it the same transition.
fn flowing_would_do(
    handle: &EngineHandle,
    timing: NodeTiming,
    at: u64,
    now_master: u64,
) -> bool {
    // Without both figures, believe what the master said rather than a guess
    // about this node's own future `[GOV-SRC-040]`.
    flow_error_ms(handle, timing, at, now_master)
        .is_some_and(|e| e.unsigned_abs() <= FLOW_TOLERANCE.as_millis() as u64)
}

/// How long until this node's **next** passage begins to sound, in ms.
///
/// Its current passage's remaining time, less the overlap the transition into
/// the next one will spend: the incoming passage starts sounding that much
/// before the outgoing one runs out, which for most of this library is five
/// milliseconds and for a real crossfade is seconds `[crate::queue]`.
///
/// `None` on the same terms as `own_transition_in_ms` -- nothing playing, or
/// a passage with no length to measure against.
fn own_next_starts_in_ms(handle: &EngineHandle) -> Option<u64> {
    let remaining = own_transition_in_ms(handle)?;
    let overlap = handle.state.lock().ok().and_then(|s| {
        let cur = s.current.as_ref()?;
        let next = s.queue.first()?;
        Some(crate::queue::overlap_ms(cur, next))
    })?;
    Some(remaining.saturating_sub(overlap))
}

/// What the engine says past joins have been landing at `[GDE-ARC-061]`.
///
/// Read from the snapshot rather than kept here: the engine owns it because
/// the engine persists it, and a second copy would drift `[GDE-ARC-033]`.
fn join_bias_ms(handle: &EngineHandle) -> i64 {
    handle.state.lock().map_or(0, |s| s.echo_node.join_bias_ms)
}

/// Watching where one join landed `[GDE-ARC-061]`.
///
/// **Its own small window, deliberately shorter than the correction loop's.**
/// `RESIDUAL_MIN_SAMPLES` is 60 over 120 s, sized to resolve a few
/// milliseconds for a loop that must not hunt. This is measuring a quantity
/// of several hundred, and the anchor's own scatter is about 25 ms, so eleven
/// samples resolve it to ~8 ms -- about six seconds at the snapshot cadence.
/// Waiting two minutes instead would mean waiting through the very
/// corrections that make the reading meaningless.
struct Landing {
    began: std::time::Instant,
    /// The correction that was in force when this join was aimed
    /// `[GDE-ARC-061]`, `[GDE-ECHO-346]`.
    ///
    /// **What is measured afterwards is what is LEFT of the bias, not the
    /// bias.** A join aimed `applied` ms on that still lands `L` ms behind is
    /// evidence that the bias is `applied + L`; recording `L` alone tells the
    /// next join the error shrank when all that happened is that it was
    /// partly corrected. The window then mixes corrected and uncorrected
    /// samples, the median falls back towards the uncorrected ones, and the
    /// loop oscillates instead of converging.
    ///
    /// Observed doing exactly that while seeding: landings of 611, 687, 893
    /// gave a median of 687, which cut the next two to 133 and 174 -- and the
    /// median then moved *down* to 611 on the strength of them.
    applied: i64,
    /// How long after the decision the start was aimed at `[GDE-ARC-069]`.
    not_before: Duration,
    samples: Vec<i64>,
}

impl Landing {
    /// Enough to beat the anchor's scatter, few enough to finish before the
    /// first boundary correction can act. Odd, so the median is a sample.
    const WANT: usize = 11;
    /// Give up after this. A join whose landing could not be measured in a
    /// quarter of a minute has had something else happen to it, and a guess
    /// is worse than no sample `[GOV-SRC-040]`.
    const PATIENCE: Duration = Duration::from_secs(25);

    /// `not_before` is the lead the commanded start was aimed at
    /// `[GDE-ARC-069]`.
    ///
    /// **The join has not happened yet when this is created.** `act` decides
    /// to join, schedules the start for `offset + margin` into the future,
    /// and then falls through to `correct_offset` on the same pass -- which
    /// begins feeding residuals immediately. Those first readings are of the
    /// node's *old* position, the one the join is about to replace, and
    /// `[GDE-ARC-059]` does not exclude them: it guards the window after the
    /// cut, not the window between the decision and the cut. At ~1.8
    /// samples/s and a lead of half a second to a second, one or two of
    /// eleven samples are pre-join outliers, all on the same side, which
    /// moves an eleven-sample median by that many ranks.
    fn new(applied: i64, not_before: Duration) -> Self {
        Self { began: std::time::Instant::now(), applied, not_before, samples: Vec::new() }
    }

    /// Feed one residual. `Some(bias_ms)` once there are enough to speak --
    /// the **whole** bias this join is evidence for, not the part left over.
    fn push(&mut self, residual_ns: i64) -> Option<i64> {
        // Before the start has fired this is the old position `[GDE-ARC-069]`.
        if self.began.elapsed() < self.not_before {
            return None;
        }
        self.samples.push(residual_ns / 1_000_000);
        if self.samples.len() < Self::WANT {
            return None;
        }
        let mut v = self.samples.clone();
        v.sort_unstable();
        Some(self.applied + v[v.len() / 2])
    }

    /// What was left after the correction, for saying so in the log.
    fn residual_ms(&self) -> i64 {
        let mut v = self.samples.clone();
        v.sort_unstable();
        v.get(v.len() / 2).copied().unwrap_or(0)
    }

    fn expired(&self) -> bool {
        self.began.elapsed() > Self::PATIENCE
    }
}

/// Join at once, and what this node is playing, as the panel has them.
fn join_intent(handle: &EngineHandle) -> (bool, Option<i64>) {
    match handle.state.lock() {
        Ok(s) => (s.echo_node.join_now, s.current.as_ref().map(|e| e.passage_id)),
        Err(_) => (false, None),
    }
}

/// Say what following is actually doing, where the panel can see it.
///
/// The engine rewrites `echo_node` twice a second and deliberately carries
/// this field across untouched, because the engine does not know it: only the
/// follower does `[SPEC-ECHO-020]`.
fn set_status(handle: &EngineHandle, status: &str) {
    if let Ok(mut s) = handle.state.lock() {
        s.echo_node.follow_status = status.to_string();
    }
}

/// Follow whatever node the settings name, for as long as they name one.
///
/// Reconnection is a plain retry with no backoff state and no resynchronising
/// handshake, because there is nothing to resynchronise: every message is
/// absolute and the next to arrive is sufficient on its own `[GDE-ECHO-320]`.
/// A node that misses an hour of them rejoins on the first one it sees.
pub async fn run(cfg: Following, handle: Arc<EngineHandle>) {
    let mut last_note = String::new();
    loop {
        let Some(url) = wanted_url(&handle) else {
            // Independent is not an error and not a wait for anything; it is
            // what every node does by default `[GDE-ECHO-500]`.
            set_status(&handle, "");
            // And an independent node trims nothing. Leaving a rate behind
            // would have it quietly correcting towards a master it is no
            // longer listening to.
            // The integrator resets with it: `FollowState` is rebuilt per
            // connection, so a reconnection never resumes from a stale trim.
            handle.send(Command::SetEchoRate(0.0));
            tokio::time::sleep(Duration::from_secs(1)).await;
            continue;
        };
        let mut follower =
            Follower::new(cfg.timing, Duration::from_micros(500), Duration::from_secs(1));
        // Per connection: a node that reconnects should catch up again, since
        // whatever it was playing while disconnected is by then its own.
        let mut fs = FollowState::new();
        // A follower's basis would normally be established and voided by its
        // own audio path. Nothing here trims, so nothing consults it; it is
        // established once and left alone, and that is said out loud because a
        // node that DOES trim must not do this `[GDE-ECHO-360]`.
        follower.basis.establish();

        let ws = match tokio_tungstenite::connect_async(&url).await {
            Ok((ws, _)) => ws,
            Err(e) => {
                set_status(&handle, &format!("Cannot reach {url}: {e}"));
                note_warn(&mut last_note, format!("echo-follow: connect to {url} failed: {e}"));
                tokio::time::sleep(Duration::from_secs(3)).await;
                continue;
            }
        };
        set_status(&handle, &format!("Connected to {url}, waiting for a passage to start."));
        note(&mut last_note, format!("echo-follow: connected to {url}"));
        let (_, mut rx) = ws.split();

        loop {
            // Bounded, so a setting changed while the master is quiet is
            // noticed. An unbounded await here would hold a node to a master
            // it was told to stop following until that master next spoke.
            let msg = match tokio::time::timeout(Duration::from_secs(2), rx.next()).await {
                Err(_) => {
                    if wanted_url(&handle).as_deref() != Some(url.as_str()) { break }
                    continue;
                }
                Ok(None) => break,
                Ok(Some(m)) => m,
            };
            if wanted_url(&handle).as_deref() != Some(url.as_str()) {
                note(&mut last_note, "echo-follow: the node to follow changed".to_string());
                break;
            }
            let text = match msg {
                Ok(tokio_tungstenite::tungstenite::Message::Text(t)) => t,
                Ok(_) => continue,
                Err(e) => {
                    note_warn(&mut last_note, format!("echo-follow: socket error: {e}"));
                    break;
                }
            };
            let Ok(snap) = serde_json::from_str::<Snapshot>(&text) else { continue };
            adopt_queue(&snap, &cfg, &handle, &mut fs).await;
            act(&mut follower, &snap.echo, &cfg, &handle, &mut fs).await;
        }
        set_status(&handle, "Not connected.");
        // Same reasoning as going independent: no master, no trim.
        handle.send(Command::SetEchoRate(0.0));
        tokio::time::sleep(Duration::from_secs(1)).await;
    }
}

async fn act(
    f: &mut Follower,
    st: &EchoState,
    cfg: &Following,
    handle: &Arc<EngineHandle>,
    fs: &mut FollowState,
) {
    // The offset the listener has set, this instant. Assigned rather than
    // passed, because `submit_at` and the trim both read it off the follower.
    f.timing = live_timing(handle, cfg.timing);
    // Everything below works in the MASTER's frame of time `[GDE-ECHO-366]`.
    // This node's own wall clock is used only to measure intervals against,
    // never compared with the wire, so a node booted days behind -- which is
    // every node here after a power cut, none having an RTC -- schedules
    // exactly as a disciplined one would. A step on either side is a
    // discontinuity like any other and clears the rate window.
    if let Some(m) = st.anchor.as_ref() {
        if fs.clock.observe(m.heard_at, now_nanos()) {
            note(&mut fs.note, "echo-follow: a clock stepped; re-measuring".to_string());
            fs.rate.clear();
            // A step moves every residual by the same amount, so a landing
            // measured across one is measuring the clock `[GDE-ARC-061]`.
            fs.landing = None;
            handle.send(Command::SetEchoRate(0.0));
        }
        // Still worth saying, but as a diagnosis rather than a refusal: this
        // node now follows correctly with a wrong clock, and a listener
        // should still be told the clock is wrong `[GDE-ECHO-365]`.
        if !crate::echo::clocks_agree(m.heard_at, now_nanos(), MAX_CLOCK_SKEW) {
            let skew = (now_nanos() as i64 - m.heard_at as i64) / 1_000_000_000;
            note_warn(&mut fs.note, format!(
                "echo-follow: this node's clock is {skew} s from that one's; following anyway, on its clock"));
        }
    }
    // Without a reading there is no shared frame and nothing can be scheduled.
    let Some(now_master) = fs.clock.now(now_nanos()) else { return };

    // Catching up to what the master is ALREADY playing, which the schedule
    // cannot do: a schedule describes a passage about to start, and the moment
    // somebody switches a speaker into follower mode is almost never one
    // `[SPEC-ECHO-030]`.
    //
    // Attempted once per master passage. A node whose library lacks it must
    // not retry twice a second forever, and when the master moves on the
    // ordinary schedule path takes over anyway.
    let (join_now, _) = join_intent(handle);
    if join_now {
        if let Some(a) = st.anchor.as_ref() {
            // Coming to it is enough to skip the join only while the node is
            // roughly in the right place. Grossly out -- a node that has just
            // restarted and resumed its own programme, say -- it is playing
            // the right passage at the wrong moment, and only placing the
            // first sample afresh fixes that `[GDE-ECHO-344]`.
            let near = !fs.want_rejoin;
            // **Any commitment, not one to this passage** `[GDE-ARC-042]`.
            // After a commanded start this node's ring is cut, its queue has
            // advanced and its basis is voided; no join decision taken from
            // that state can be trusted, whichever passage it is about. Keyed
            // on the passage id, a commitment to the passage being started
            // left a join into the one just *left* entirely unsuppressed,
            // which is half of how three ring cuts happened in three seconds.
            let committed =
                fs.mid_joined.is_some_and(|(_, when)| when.elapsed() < COMMITMENT_HOLDS);
            // **The master is inside its own transition, and the schedule
            // path owns it** `[GDE-ARC-042]`. Its anchor is what it can hear
            // and lags admission by a ring; its schedule is what it just
            // admitted and leads by the same ring `[LOG-ECHO-020]`. So for
            // some fifteen seconds after every boundary the master truthfully
            // reports two different passages, and `coming_here` -- which only
            // ever asks about the anchor -- answers no to both in turn: the
            // follower has just skipped, so the old passage is in neither
            // `current` nor its queue.
            //
            // Nothing needs joining in that window. The schedule for the new
            // passage is already in hand and `Follower::on_state` acts on it;
            // a mid-join can only cut the ring a second time and re-impose
            // the join bias `[GDE-ECHO-342]`. Measured on `lp3-wifi`
            // 2026-09-19: a converged 14 ms became 802 ms, and the boundary
            // path then needed ten minutes to take it back.
            let mid_transition = st.schedule
                .is_some_and(|s| s.passage_id != a.passage_id);
            let already =
                (coming_here(handle, a.passage_id) && near) || committed || mid_transition;
            if !already {
                fs.mid_joined = Some((a.passage_id, std::time::Instant::now()));
                let air = crate::echo::AirPosition {
                    passage_id: a.passage_id,
                    position_ms: a.sample.saturating_mul(1000) / a.rate.max(1) as u64,
                    at: a.heard_at,
                };
                let lead = f.timing.offset() + mid_join_margin(handle);
                fs.want_rejoin = false;
                let bias = join_bias_ms(handle);
                if let Some(j) = crate::echo::join_mid_passage(
                    &air, f.timing, now_master, lead, bias) {
                    // Back into this node's own clock before anyone waits on it.
                    let at = fs.clock.to_local(j.submit_at).unwrap_or(j.submit_at);
                    if bias != 0 {
                        note(&mut fs.note, format!(
                            "echo-join: aiming {bias:+} ms on from where that node says it is, from what past joins landed at"));
                    }
                    start(j.passage_id, j.start_sample, at, cfg, handle, &mut fs.note,
                          "joining part-way into").await;
                    // **Everything measured before this is about a position
                    // the node no longer holds.** The same reset the
                    // scheduled-start path makes, for the same reason: a join
                    // moves the node bodily, so a filtered residual and a rate
                    // fit taken across it are describing somewhere it has
                    // been. `corrected` goes too -- it gates the residual
                    // block on "already corrected this master passage", and
                    // left set it would stop this node measuring anything at
                    // all until the master moved on `[GDE-ARC-061]`.
                    fs.rate.clear();
                    fs.filtered.clear();
                    fs.corrected = None;
                    // **Watch where this one lands** `[GDE-ARC-061]`. Nothing
                    // else in the loop measures a join, and every later
                    // reading is about the correction loop instead.
                    fs.landing = Some(Landing::new(bias, lead));
                }
            }
        }
    }

    // Measured and acted on, then **fall through to the schedule whatever
    // happened** `[GDE-ECHO-374]`. A function rather than a block because it
    // used to be a block, and two of its arms returned out of `act` itself.
    correct_offset(st, handle, fs, now_master);

    match f.on_state(st, now_master) {
        Follow::Idle => {}
        Follow::Hold(why) => {
            set_status(handle, &format!(
                "The node being followed cannot place itself in time ({why:?}); holding."));
            note(&mut fs.note, format!("echo-follow: holding -- master reports {why:?}"));
        }
        Follow::Missed { passage_id } => {
            // Not a failure of the master's: a node with a large presentation
            // offset misses schedules a short-offset node makes comfortably
            // `[GDE-ECHO-410]`. Saying which node and which passage is what
            // makes that diagnosable instead of mysterious.
            set_status(handle, &format!(
                "Schedules are arriving too late for this speaker's {} ms delay; waiting for the next passage.", f.timing.offset().as_millis()));
            note(&mut fs.note, format!(
                "echo-follow: passage {passage_id} was already due for this node's \
{} ms offset; waiting for the next",
                f.timing.offset().as_millis()));
        }
        Follow::StartAt { passage_id, start_sample, at } => {
            // Already heading there, and heading there *at about the right
            // time*. Let it flow: a scheduled start goes through `skip`, which
            // cuts the ring `[REQ-AUD-158]`, and a follower that skips into
            // every passage loses its buffer at every boundary -- heard as a
            // stutter at the start of each track `[GDE-ECHO-336]`.
            //
            // **Being next is not enough on its own.** When the master skips,
            // its next passage is the one this node also has queued -- and
            // this node will not reach it for minutes. Suppressing on
            // queue membership alone is why a skip on the master left the
            // follower playing calmly on `[GDE-ECHO-353]`. The test is
            // whether flowing would land anywhere near the announced time.
            if next_up(handle) == Some(passage_id) && start_sample == 0
                && !fs.want_rejoin && flowing_would_do(handle, f.timing, at, now_master)
            {
                // **SUPERSEDED -- read `[GDE-ARC-056]` below before this.**
                // The three paragraphs that follow describe a mechanism that
                // was built, measured, and taken out again the same day. They
                // are kept because the reasoning is still correct about where
                // the number comes from, and only wrong about whether it can
                // be used. Nothing here is sent.
                //
                // **Flow, and place the boundary exactly while doing it**
                // `[GDE-ARC-054]`. The tolerance above separates two cases; it
                // does not measure either, and the error it just computed is
                // the placement error outright. Both terms are schedule
                // quantities -- this node's own transition, and the instant
                // the master announced -- so neither passes through an anchor
                // and neither carries the ring's depth jitter `[GDE-ARC-047]`.
                //
                // Sent **after** `correct_offset`, deliberately, so that where
                // both have an answer this one wins: ranked by measurement
                // `[GOV-SRC-040]`, a schedule read against a schedule beats a
                // residual read through two output rings. The engine takes the
                // last shift as the plan and clears the old debt with it.
                //
                // The transition is minutes away when this first fires and the
                // pass repeats twice a second, so what actually lands is the
                // final estimate before admission -- which is the look-ahead
                // this design is named for, arrived at by simply not stopping.
                // **A placement was sent from here and it has been taken out
                // again** `[GDE-ARC-056]`. The error is real and well
                // measured; there is nowhere to put it. `EchoCorrectNextStart`
                // is spent at the next **admission**, and by the time the
                // master's schedule for this passage arrives, this node has
                // already admitted it -- so the correction landed on the
                // passage *after* the one it was computed for.
                //
                // Measured on `lp3-wifi` 2026-09-19, and the chain is plain in
                // one log: 404 ms computed for passage 11572, applied to 5766;
                // 431 ms computed for 11056, applied to 2030. The node had
                // converged to +10 ms and that last misapplied 431 ms put it
                // 264 ms out -- a new injector of exactly the kind
                // `[GDE-ARC-043]` says outruns every remover in the system.
                //
                // Not a guard, because there is no window to guard: both nodes
                // run a ~15 s ring, the master publishes at its admission and
                // this node admits at the same instant, so the look-ahead is
                // **negative**. A correction aimed at an already-admitted
                // passage needs an actuator that acts after admission, which
                // the silence budget could be `[GDE-ARC-052]` -- it is spent
                // at mix time, a whole ring later. That is a design step, not
                // a condition to add here.
                note(&mut fs.note, format!(
                    "echo-follow: passage {passage_id} is already next here; flowing into it"));
                return;
            }
            fs.want_rejoin = false;
            fs.rate.clear();
            fs.filtered.clear();
            // **Committed.** A start is queued for a future instant, so the
            // passage is in neither `current` nor the queue until it fires --
            // which means `coming_here` says no and the mid-passage join
            // races it `[GDE-ECHO-354]`. Observed on a skip: the follower
            // took the master's corrected schedule at sample 0, then a second
            // later joined the same passage part-way in and threw it away.
            // Recording the commitment here is what makes the two paths see
            // each other.
            fs.mid_joined = Some((passage_id, std::time::Instant::now()));
            let at = fs.clock.to_local(at).unwrap_or(at);
            start(passage_id, start_sample, at, cfg, handle, &mut fs.note, "starting").await;
        }
    }
}

/// Measure this node's residual against the master's anchor and act on it.
///
/// `[GDE-ECHO-340]`'s offset correction. Measured now, applied at the next
/// admission -- which is sound only because an offset is a position and does
/// not grow while nobody is looking, unlike the rate error trimming handles.
///
/// Once per master passage: the correction is for the passage after this one,
/// and sending it twice a second would simply overwrite itself.
///
/// **Its own function, and that is the fix for `[GDE-ECHO-374]`.** Two of the
/// arms below return, and while this was a block inside `act` those returns
/// left `act` -- so a pass that was still filling the median, or that had just
/// handed an endgame offset to the trim, never read the master's forward
/// schedule. Ordinary passage boundaries survive that, because the two nodes
/// then name different passages and none of this runs. A **seek** does not: it
/// re-announces the passage both nodes are already on `[GDE-ECHO-325]`, which
/// is precisely the condition that reaches here, and the filter is cleared by
/// every start, shift and rejoin and needs thirty seconds to answer again. A
/// seek landing in that window was dropped silently, and the next snapshot's
/// schedule is identical, so `Follower::on_state`'s dedup would not re-offer
/// it.
fn correct_offset(
    st: &EchoState,
    handle: &Arc<EngineHandle>,
    fs: &mut FollowState,
    now_master: u64,
) {
    if let (Some(m), Some(mine)) = (st.anchor.as_ref(), own_anchor(handle)) {
        if m.passage_id == mine.passage_id && fs.corrected != Some(m.passage_id) {
            let local = crate::echo::AirPosition {
                passage_id: mine.passage_id,
                position_ms: mine.sample.saturating_mul(1000) / mine.rate.max(1) as u64,
                at: mine.heard_at,
            };
            // `local.at` is on THIS node's clock and `m.heard_at` on the
            // master's, so one is carried into the other's frame before they
            // are subtracted -- otherwise the offset between the clocks reads
            // as an alignment error `[GDE-ECHO-366]`.
            let local = crate::echo::AirPosition {
                at: fs.clock.now(local.at).unwrap_or(local.at),
                ..local
            };
            let residual = crate::echo::local_at_sample(&local, m) - m.heard_at as i64;
            // **Where a join landed, before anything else acts on it**
            // `[GDE-ARC-061]`. Taken from the same residual the loop uses,
            // and taken here because this is the only point where a reading
            // exists that is still attributable to the join: the correction
            // below is what makes every later one a measurement of the loop
            // instead.
            if let Some(l) = fs.landing.as_mut() {
                if l.expired() {
                    note(&mut fs.note,
                         "echo-join: the landing could not be measured in time; not learned from".into());
                    fs.landing = None;
                } else if let Some(bias) = l.push(residual) {
                    note(&mut fs.note, format!(
                        "echo-join: aimed {:+} ms on and still landed {:+} ms out, so this join says the bias is {bias:+} ms", l.applied, l.residual_ms()));
                    handle.send(Command::RecordJoinLanding(bias));
                    fs.landing = None;
                }
            }
            // Every reading feeds the fit, not just the ones that trigger a
            // correction: the slope is what the rate trim runs on, and it
            // needs the whole series `[GDE-ECHO-340]`.
            fs.rate.push(now_master, residual);
            // **Added to what is already applied, not substituted for it**
            // `[GDE-ECHO-346]`. The residual being fitted is what remains
            // AFTER the current trim, so the slope is the error in the
            // correction rather than the drift. Sending it as an absolute
            // sets the trim to `R - A` when it already holds `A`: the fixed
            // point is half the drift and the map oscillates about it, which
            // leaves ~7 ppm of the measured 13.92 uncorrected for ever
            // `[LOG-P4-130]`.
            //
            // Applied once per window and then cleared, because the plant's
            // slope has just stepped and a line fitted across that step is not
            // a slope `[RateEstimate::clear]`.
            if let Some(ppm) = fs.rate.ppm() {
                fs.applied_ppm = crate::echo::next_trim_ppm(fs.applied_ppm, ppm);
                note(&mut fs.note, format!(
                    "echo-rate: {ppm:+.2} ppm still out; trimming at {:+.2} ppm",
                    fs.applied_ppm));
                handle.send(Command::SetEchoRate(fs.applied_ppm));
                fs.rate.clear();
            }
            // Every reading feeds the filter; the corrections read the
            // filter, never the reading `[GDE-ECHO-348]`.
            fs.filtered.push(now_master, residual);
            let Some(filtered) = fs.filtered.median() else {
                set_status(handle, &format!(
                    "Following, {:+.0} ms from that node (still measuring).",
                    residual as f64 / 1e6));
                return;
            };
            // Logged, not merely observed `[GDE-ECHO-260]`: a regression months
            // from now needs a baseline to fail against.
            set_status(handle, &format!("Following, {:+.0} ms from that node.",
                                        filtered as f64 / 1e6));

            // Below a mix quantum the boundary knobs cannot help: the coarse
            // step is bigger than the error `[GDE-ECHO-347]`. The frame trim
            // can, at 23 us a time `[GDE-ECHO-349]`.
            if filtered.unsigned_abs() <= OFFSET_ENDGAME.as_nanos() as u64
                && filtered.unsigned_abs() > OFFSET_DEADBAND.as_nanos() as u64
            {
                fs.filtered.clear();
                note(&mut fs.note, format!(
                    "echo-offset: {:+.0} ms out; shedding it by trimming frames",
                    filtered as f64 / 1e6));
                handle.send(Command::EchoShedOffset(filtered / 1_000_000));
                if fs.landing.take().is_some() {
                    note(&mut fs.note,
                         "echo-join: the trim acted first; that join is not learned from".into());
                }
                return;
            }

            match crate::echo::offset_fix(
                filtered, OFFSET_DEADBAND, OFFSET_REJOIN_BEYOND) {
                crate::echo::OffsetFix::Hold => {}
                crate::echo::OffsetFix::ShiftStart(ms) => {
                    fs.corrected = Some(m.passage_id);
                    // The correction is about to step the residual, and a step
                    // inside the window reads as an enormous slope. Clearing
                    // costs an hour of rate estimate and saves the loop from
                    // trimming hard against a drift that never happened.
                    fs.rate.clear();
                    fs.filtered.clear();
                    note(&mut fs.note, format!(
                        "echo-offset: {:+.0} ms out; starting the next passage {} ms {}",
                        filtered as f64 / 1e6, ms.abs(),
                        if ms > 0 { "earlier" } else { "later" }));
                    handle.send(Command::EchoCorrectNextStart(ms));
                    // A landing still being watched is now a measurement of
                    // this correction rather than of the join `[GDE-ARC-061]`.
                    if fs.landing.take().is_some() {
                        note(&mut fs.note,
                             "echo-join: a correction landed first; that join is not learned from".into());
                    }
                    // **There was a second send here and it is gone**
                    // `[GDE-ARC-051]`. When the bite was capped at 500 ms this
                    // arm computed what the boundary would not take and handed
                    // that remainder to the frame trim at once, so that a
                    // listener who had asked to be in step straight away had
                    // *something* acting between boundaries `[GDE-ARC-041]`.
                    // With the cap gone, `ms` **is** `filtered / 1_000_000`,
                    // the remainder is identically zero, and the branch could
                    // never fire.
                    //
                    // It is worth being clear that nothing was lost with it.
                    // The trim pays at `ECHO_DEBT_PPM`, 0.1 ms/s: the 100 ms
                    // it used to be handed took seventeen minutes, which is
                    // longer than waiting for the boundary it was meant to
                    // beat. It was never the fast path, and reading it as one
                    // is `[GDE-ARC-043]` again -- an actuator sized for drift
                    // being asked to answer a step.
                    //
                    // So "straight away" now means the transition takes the
                    // whole of it, exactly `[SPEC-ECHO-030]`; what still acts
                    // sooner than a boundary is the mid-join, and that is
                    // decided above. A genuine shortfall -- admission clamped
                    // by a pair of passages too short to spend it -- is known
                    // only to the engine, which adds it to the debt itself.
                }
                // Too far for one transition to absorb. Saying so beats a
                // silent hold -- this is the case a listener would otherwise
                // hear and not be able to explain `[GOV-SRC-040]`.
                crate::echo::OffsetFix::Rejoin => {
                    fs.corrected = Some(m.passage_id);
                    // The escape hatch for the suppression below: without this
                    // a node too far out would flow into every transition,
                    // never take a scheduled start, and stay out indefinitely.
                    fs.want_rejoin = true;
                    fs.filtered.clear();
                    // A rejoin places the first sample afresh, which steps the
                    // residual just as a nudge does.
                    fs.rate.clear();
                    note(&mut fs.note, format!(
                        "echo-offset: {:+.0} ms out, which is not an offset any more; waiting for a scheduled start", filtered as f64 / 1e6));
                }
            }
        }
    }
}

/// A passage, with the names a listener reads.
///
/// `Library::passage` returns the audio facts -- path, bounds, fades -- and
/// nothing a person would recognise; the title and artist come from a second
/// lookup against the recording's MBID. Every other queue path in the player
/// calls `describe`, and this one did not, so a follower's *Coming Up* listed
/// filenames where the node it followed listed songs.
fn named(lib: &crate::db::Library, passage_id: i64) -> Option<crate::queue::QueueEntry> {
    let mut e = lib.passage(passage_id).ok()?;
    lib.describe(&mut e);
    Some(e)
}

/// Take the followed node's queue as this node's own `[GDE-ECHO-500]`.
///
/// Only when it changes: the snapshot arrives twice a second and resolving a
/// queue means a database read per passage.
///
/// Passages this node does not have are dropped rather than refused wholesale.
/// Two libraries that have drifted apart should cost the passages they differ
/// on, not the whole programme `[GDE-ECHO-420]`.
async fn adopt_queue(
    snap: &Snapshot,
    cfg: &Following,
    handle: &Arc<EngineHandle>,
    fs: &mut FollowState,
) {
    let announced: Vec<i64> = snap.queue.iter().map(|e| e.passage_id).collect();
    if announced.is_empty() || announced == fs.queue {
        return;
    }
    fs.queue = announced.clone();
    let db = cfg.db.clone();
    let library = cfg.library.clone();
    let wanted = announced.len();
    let found = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        Some(announced.iter().filter_map(|id| named(&lib, *id)).collect::<Vec<_>>())
    })
    .await;
    if let Ok(Some(entries)) = found {
        if entries.len() < wanted {
            note(&mut fs.note, format!(
                "echo-queue: {} of {wanted} upcoming passages are in this node's library",
                entries.len()));
        }
        handle.send(Command::EchoSetQueue(entries));
    }
}

/// Resolve a passage against this node's own library and commit to the instant.
///
/// Both ways in land here -- a schedule for a passage about to start, and a
/// mid-passage catch-up -- because they differ only in which sample they name.
#[allow(clippy::too_many_arguments)]
async fn start(
    passage_id: i64,
    start_sample: u64,
    at: u64,
    cfg: &Following,
    handle: &Arc<EngineHandle>,
    last: &mut String,
    what: &str,
) {
    let db = cfg.db.clone();
    let library = cfg.library.clone();
    // The library is SQLite and blocking; the socket must not wait on a disk
    // read. Once per passage, so the spawn costs nothing.
    let found = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library)?;
        named(&lib, passage_id)
            .ok_or_else(|| crate::db::DbError::Query(format!("passage {passage_id} not here")))
    })
    .await;
    match found {
        Ok(Ok(entry)) => {
            set_status(handle, "Following.");
            tracing::info!("echo-follow: {what} passage {passage_id} at sample {start_sample} in {:.3}s", (at as i64 - now_nanos() as i64) as f64 / 1e9);
            handle.send(Command::EchoStartAt { entry, start_sample, at_nanos: at });
        }
        // A master playing something this node does not have is the expected
        // cost of independent libraries, not an error to retry
        // `[GDE-ECHO-420]`. Reported once; the node carries on with its own
        // programme.
        Ok(Err(e)) => {
            set_status(handle, &format!(
                "That node is playing something this one does not have (passage {passage_id}); playing its own queue instead."));
            note(last, format!(
                "echo-follow: passage {passage_id} is not in this node's library ({e}); staying with its own queue"));
        }
        Err(e) => note_warn(last, format!("echo-follow: library lookup failed: {e}")),
    }
}

/// Print only when the message changes.
///
/// The snapshot arrives twice a second forever, so an unconditional line for a
/// steady state would bury every line that matters. A log nobody can read is
/// the same as no log.
fn note(last: &mut String, line: String) {
    if line != *last {
        tracing::info!("{line}");
        *last = line;
    }
}

/// [`note`], for the lines that report a fault rather than narrate the
/// follow loop: a connection or socket that failed, a clock that disagrees,
/// a lookup that errored. Everything here was info until 2026-09-25, so
/// `journalctl -p warning` could not see a follower that had lost its master
/// `[GDE-HST-110]`. Same de-duplication, so a fault that persists is one line.
/// A boot that tries before DNS is ready shows one of these, then "connected".
fn note_warn(last: &mut String, line: String) {
    if line != *last {
        tracing::warn!("{line}");
        *last = line;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::{Command, Engine};

    /// A follower's side of the snapshot contract `[GDE-HST-060]`: the fields
    /// it reads, taken from its own structs, are the ones the fixture records
    /// for it -- so the server's test is checking what a follower really needs.
    #[test]
    fn what_a_follower_reads_is_what_the_fixture_records() {
        use crate::web::contract;
        let mine = contract::paths(
            contract::fields_of::<Snapshot>(),
            &[("queue[]", contract::fields_of::<AnnouncedEntry>())],
        );
        assert_eq!(mine, contract::required("echo-follower"));
    }

    fn node() -> Arc<EngineHandle> {
        let (_e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        // The engine is dropped; the handle and its shared state are what the
        // follower actually reads, and they outlive it.
        Arc::new(h)
    }

    fn entry(id: i64) -> crate::queue::QueueEntry {
        crate::queue::QueueEntry {
            qid: 0,
            passage_id: id,
            path: PathBuf::from("nonexistent.flac"),
            start_ms: 0,
            end_ms: 5_000,
            file_ms: 0,
            lead_in_ms: 0,
            lead_out_ms: 0,
            fade_in_ms: 0,
            fade_out_ms: 0,
            fade_in_curve: crate::fade::Curve::Exponential,
            fade_out_curve: crate::fade::Curve::Exponential,
            gain_db: 0.0,
            mbid: None,
            naming: Default::default(),
            selected_by: None,
        }
    }

    /// One snapshot's worth of `act`, on a node with no library behind it.
    ///
    /// Every verdict reached here is one that needs no database: the schedule
    /// below is deliberately unreachable for this node's offset, so `act`
    /// takes the `Missed` arm, which is decided entirely in `[crate::echo]`
    /// and reported through the status line.
    async fn one_pass(h: &Arc<EngineHandle>, st: &EchoState, fs: &mut FollowState) {
        let cfg = Following {
            timing: NodeTiming { presentation_offset_frames: 0, rate: 44_100 },
            db: PathBuf::from("no.db"),
            library: PathBuf::from("nowhere"),
        };
        let mut f = Follower::new(cfg.timing, Duration::from_micros(500),
                                  Duration::from_secs(1));
        act(&mut f, st, &cfg, h, fs).await;
    }

    /// A node, a master anchor and a schedule it cannot possibly meet.
    fn unreachable_schedule(h: &Arc<EngineHandle>, now: u64) -> EchoState {
        let anchor = crate::echo::DriftAnchor {
            passage_id: 7, sample: 0, heard_at: now, rate: 44_100, ppm: None,
        };
        if let Ok(mut s) = h.state.lock() {
            // A second of presentation offset, so a schedule sounding in
            // 100 ms needed submitting 900 ms ago `[GDE-ECHO-410]`.
            s.echo_node.offset_frames = 44_100;
            s.echo_node.rate = 44_100;
            // On the master's own passage, which is what a seek looks like
            // and what puts `act` into the residual block at all.
            s.echo.anchor = Some(anchor);
        }
        EchoState {
            anchor: Some(anchor),
            schedule: Some(crate::echo::Schedule {
                passage_id: 7, start_sample: 0,
                sound_at: now + 100_000_000, rate: 44_100,
            }),
            voided_by: None,
        }
    }

    fn status_of(h: &Arc<EngineHandle>) -> String {
        h.state.lock().map(|s| s.echo_node.follow_status.clone()).unwrap_or_default()
    }

    /// `[GDE-ECHO-374]`: while the filter is measuring, the follower was not
    /// following.
    ///
    /// The residual block returned early when the median was not yet
    /// available, and that return sat *above* the schedule handling -- so for
    /// that pass the master's forward schedule was not read at all. Ordinary
    /// boundaries survive it, because the two nodes then name different
    /// passages and the block is skipped entirely. A **seek** does not: it
    /// re-announces the passage both nodes are already on `[GDE-ECHO-325]`,
    /// which is exactly the condition that reaches the early return, and the
    /// next snapshot's schedule is identical so the dedup will not re-offer
    /// it. The filter is cleared by every start, shift and rejoin and needs
    /// thirty seconds to answer again, so the window is wide.
    #[tokio::test]
    async fn a_schedule_is_read_even_while_the_residual_filter_is_measuring() {
        let h = node();
        let st = unreachable_schedule(&h, now_nanos());
        let mut fs = FollowState::new();
        one_pass(&h, &st, &mut fs).await;
        assert!(status_of(&h).contains("arriving too late"),
                "the schedule was never read; the node reported {:?} instead",
                status_of(&h));
    }

    /// The same early return, one branch further down: handing an endgame
    /// offset to the frame trim also returned before the schedule
    /// `[GDE-ECHO-374]`.
    #[tokio::test]
    async fn a_schedule_is_read_on_the_pass_that_sheds_an_endgame_offset() {
        let h = node();
        let now = now_nanos();
        let st = unreachable_schedule(&h, now);
        let mut fs = FollowState::new();
        // A full window sitting 20 ms out: inside the endgame band, above the
        // deadband, so this pass hands the trim a debt.
        for i in 0..RESIDUAL_MIN_SAMPLES as u64 {
            fs.filtered.push(now - (RESIDUAL_MIN_SAMPLES as u64 - i) * 500_000_000,
                             20_000_000);
        }
        one_pass(&h, &st, &mut fs).await;
        assert!(status_of(&h).contains("arriving too late"),
                "the schedule was never read; the node reported {:?} instead",
                status_of(&h));
    }

    fn shaped(id: i64, dur_ms: u64, lead_in: u64, lead_out: u64) -> crate::queue::QueueEntry {
        let mut e = entry(id);
        e.end_ms = dur_ms;
        e.lead_in_ms = lead_in;
        e.lead_out_ms = lead_out;
        e
    }

    /// `[GDE-ECHO-376]`: "would flowing do?" compared this node's passage
    /// **ending** against another node's **submission**.
    ///
    /// Those differ by the transition's own overlap plus this node's
    /// presentation offset. It passed only because both terms are small
    /// against the five-second tolerance -- and a passage with the three-to-
    /// five-second lead-out the library calls the rare-but-wanted case makes
    /// the comparison fail at *every* boundary. That reinstates
    /// `[GDE-ECHO-343]`: a ring cut and the join bias at every transition,
    /// and it starves the rate fit besides, since a commanded start clears a
    /// window that needs fifteen unbroken minutes to produce anything.
    ///
    /// Here the two nodes agree exactly: this node's next passage begins to
    /// sound in three seconds, and that is the instant the master announced.
    #[tokio::test]
    async fn a_long_crossfade_is_still_recognised_as_the_same_transition() {
        let h = node();
        let now = now_nanos();
        let anchor = crate::echo::DriftAnchor {
            passage_id: 4, sample: 0, heard_at: now, rate: 44_100, ppm: None,
        };
        {
            let mut s = h.state.lock().unwrap();
            // lempi02w's 355 ms `[LOG-CPAL-060]`.
            s.echo_node.offset_frames = 15_676;
            s.echo_node.rate = 44_100;
            // No anchor of its own, so the residual block is not in the way.
            s.echo.anchor = None;
            // Five minutes long, 292 s played: its own transition is eight
            // seconds off. A five-second lead-out met by a five-second
            // lead-in, so the incoming passage begins to sound three seconds
            // from now.
            s.current = Some(shaped(3, 300_000, 0, 5_000));
            s.position_ms = 292_000;
            s.queue = vec![shaped(7, 300_000, 5_000, 0)];
        }
        let st = EchoState {
            anchor: Some(anchor),
            schedule: Some(crate::echo::Schedule {
                passage_id: 7, start_sample: 0,
                sound_at: now + 3_000_000_000, rate: 44_100,
            }),
            voided_by: None,
        };
        let mut fs = FollowState::new();
        one_pass(&h, &st, &mut fs).await;
        assert!(fs.mid_joined.is_none(),
                "a boundary this node was already flowing into was cut and re-placed");
    }

    /// **The flow path must not issue a boundary correction at all**
    /// `[GDE-ARC-056]`.
    ///
    /// It did, for one afternoon, and this test asserted the opposite of what
    /// it asserts now. The reasoning was sound as far as it went: the two
    /// quantities compared here are both *schedule* quantities -- when this
    /// node's next passage will begin to sound, and when the master said its
    /// own would -- so their difference is the placement error without the
    /// anchor's ~25 ms of ring jitter `[GDE-ARC-047]`. Better measured than
    /// anything the residual loop has.
    ///
    /// **It is the wrong target, not the wrong number.**
    /// `EchoCorrectNextStart` is spent at the next *admission*, and the
    /// master publishes its schedule for a passage at its own admission --
    /// which, with both nodes on a ~15 s ring, is about when this node has
    /// already admitted the same passage. The correction therefore landed on
    /// the passage *after* the one it was computed for.
    ///
    /// Measured on `lp3-wifi` 2026-09-19, visible as a chain in one log: 404
    /// ms computed for passage 11572 and applied to 5766; 431 ms computed for
    /// 11056 and applied to 2030. The node had converged to **+10 ms**; that
    /// last misapplied correction put it **264 ms** out.
    ///
    /// So the assertion is that a node deciding to flow leaves the boundary
    /// alone. Restoring the placement needs an actuator spent *after*
    /// admission -- the silence budget is one `[GDE-ARC-052]` -- not a
    /// condition on this branch, because the look-ahead window here is
    /// negative rather than merely small.
    #[tokio::test]
    async fn flowing_into_a_boundary_does_not_correct_the_following_one() {
        let (mut e, raw) = Engine::new(crate::path::PathHandle::silent(), 1);
        let h = Arc::new(raw);
        let now = now_nanos();
        let anchor = crate::echo::DriftAnchor {
            passage_id: 4, sample: 0, heard_at: now, rate: 44_100, ppm: None,
        };
        {
            let mut s = h.state.lock().unwrap();
            s.echo_node.offset_frames = 15_676;
            s.echo_node.rate = 44_100;
            // No anchor of its own: the residual loop must not be the thing
            // under test, or the assertion cannot say which path set the knob.
            s.echo.anchor = None;
            s.current = Some(shaped(3, 300_000, 0, 5_000));
            s.position_ms = 292_000;
            s.queue = vec![shaped(7, 300_000, 5_000, 0)];
        }
        // Flowing lands at now+3.0 s; the master announced now+3.4 s.
        let st = EchoState {
            anchor: Some(anchor),
            schedule: Some(crate::echo::Schedule {
                passage_id: 7, start_sample: 0,
                sound_at: now + 3_400_000_000, rate: 44_100,
            }),
            voided_by: None,
        };
        let mut fs = FollowState::new();
        one_pass(&h, &st, &mut fs).await;
        e.tick();

        assert!(fs.mid_joined.is_none(),
                "400 ms is not a reason to cut the ring");
        assert_eq!(e.echo_next_shift_ms, 0,
                   "the flow path corrected a boundary it cannot aim at; this \
lands on the passage after the one it was measured for");
    }

    /// **The endgame band has one controller, and placement is not it**
    /// `[GDE-ARC-055]`.
    ///
    /// Caught by running the deployed build rather than by reading it. The
    /// placement above fires on every pass -- twice a second -- and
    /// `EchoCorrectNextStart` also **clears the outstanding debt**, because a
    /// new boundary plan supersedes the remainder of the old one
    /// `[GDE-ARC-041]`. Inside the endgame band `correct_offset` has already
    /// handed the error to the frame trim with `EchoShedOffset` and returned;
    /// placing on the same pass then wiped that debt before a single 23 us
    /// splice could be paid against it, and did so again half a second later,
    /// for ever. The band a node spends almost all its time in would never
    /// have converged.
    ///
    /// The partition already existed and is simply respected: above
    /// `OFFSET_ENDGAME` the boundary acts, below it the trim does
    /// `[GDE-ECHO-349]`. Two actuators sharing one band is not a tuning
    /// problem, it is a design fault.
    ///
    /// *The placement itself was withdrawn hours later `[GDE-ARC-056]`, so
    /// this now guards a path nothing takes.* It is kept deliberately: the
    /// placement will come back once it has an actuator it can aim at, and
    /// this is the trap it fell into the first time. The band belongs to the
    /// trim whatever else changes.
    #[tokio::test]
    async fn a_placement_does_not_wipe_the_endgame_trim() {
        let (mut e, raw) = Engine::new(crate::path::PathHandle::silent(), 1);
        let h = Arc::new(raw);
        let now = now_nanos();
        let anchor = crate::echo::DriftAnchor {
            passage_id: 4, sample: 0, heard_at: now, rate: 44_100, ppm: None,
        };
        // The trim is already working off a debt, exactly as the endgame
        // branch of `correct_offset` leaves it. Established FIRST, because a
        // tick republishes the engine's own snapshot over the shared state and
        // would wipe the fixture below -- which is how this test first passed
        // while the fault it names was live on three appliances.
        h.send(Command::EchoShedOffset(30));
        e.tick();
        let owed = e.echo_debt_frames;
        assert_ne!(owed, 0, "fixture: the trim should have something to pay");
        {
            let mut s = h.state.lock().unwrap();
            s.echo_node.offset_frames = 15_676;
            s.echo_node.rate = 44_100;
            s.echo.anchor = None;
            s.current = Some(shaped(3, 300_000, 0, 5_000));
            s.position_ms = 292_000;
            s.queue = vec![shaped(7, 300_000, 5_000, 0)];
        }

        // Flowing would land 30 ms early -- inside the endgame band, so the
        // boundary must keep its hands off it.
        let st = EchoState {
            anchor: Some(anchor),
            schedule: Some(crate::echo::Schedule {
                passage_id: 7, start_sample: 0,
                sound_at: now + 3_030_000_000, rate: 44_100,
            }),
            voided_by: None,
        };
        let mut fs = FollowState::new();
        one_pass(&h, &st, &mut fs).await;
        e.tick();

        assert_eq!(e.echo_next_shift_ms, 0,
                   "an endgame error must not be placed at the boundary as well");
        assert_eq!(e.echo_debt_frames, owed,
                   "the placement cleared a debt the frame trim was paying off");
    }

    /// And the case the test exists to separate must still be separated: a
    /// skip on the master moves its transition by minutes, and that is not a
    /// boundary this node is about to reach `[GDE-ECHO-353]`.
    #[tokio::test]
    async fn a_skip_on_the_master_is_still_not_mistaken_for_flowing() {
        let h = node();
        let now = now_nanos();
        let anchor = crate::echo::DriftAnchor {
            passage_id: 4, sample: 0, heard_at: now, rate: 44_100, ppm: None,
        };
        {
            let mut s = h.state.lock().unwrap();
            s.echo_node.offset_frames = 15_676;
            s.echo_node.rate = 44_100;
            s.echo.anchor = None;
            s.current = Some(shaped(3, 300_000, 0, 5_000));
            s.position_ms = 10_000;   // four and a half minutes still to run
            s.queue = vec![shaped(7, 300_000, 5_000, 0)];
        }
        let st = EchoState {
            anchor: Some(anchor),
            schedule: Some(crate::echo::Schedule {
                passage_id: 7, start_sample: 0,
                sound_at: now + 3_000_000_000, rate: 44_100,
            }),
            voided_by: None,
        };
        let mut fs = FollowState::new();
        one_pass(&h, &st, &mut fs).await;
        assert_eq!(fs.mid_joined.map(|(id, _)| id), Some(7),
                   "the master skipped and this node flowed calmly on");
    }

    /// **The master's anchor and its schedule name different passages for a
    /// whole ring around every boundary, and a join must not fire in that
    /// window** `[GDE-ARC-042]`.
    ///
    /// The anchor is what the master can *hear* and lags its admission by the
    /// ring -- some fifteen seconds `[LOG-ECHO-020]`. The schedule is what it
    /// just admitted and leads by the same ring. So for fifteen seconds after
    /// every transition the master truthfully reports two different passages,
    /// and `coming_here` -- which asks only about the anchor -- says no to
    /// both in turn: the follower has just skipped, so the *old* passage is in
    /// neither `current` nor its queue, and `mid_joined` is keyed on one
    /// passage id so a commitment to the new one cannot suppress a join into
    /// the old one.
    ///
    /// Observed on `lp3-wifi` 2026-09-19: a scheduled start for 13796, then a
    /// join into 5164 -- the passage it had just left -- then a join into
    /// 13796, three ring cuts in three seconds. Each re-imposes the join bias
    /// `[GDE-ECHO-342]`, and the node went from a converged 14 ms to 802 ms
    /// out, then spent ten minutes crawling back at the boundary rate. That
    /// sawtooth is the whole of what a listener hears.
    #[tokio::test]
    async fn no_join_while_the_master_is_inside_its_own_transition() {
        let h = node();
        let now = now_nanos();
        if let Ok(mut s) = h.state.lock() {
            s.echo_node.join_now = true;
            s.echo_node.rate = 44_100;
            // A second of presentation offset, so the schedule below is
            // `Missed` and commits to nothing. Only the mid-join can leave a
            // mark here, which is what this test is about -- the schedule
            // path acting on a transition is correct and is not the fault.
            s.echo_node.offset_frames = 44_100;
        }
        let st = EchoState {
            // Still audibly on the old passage...
            anchor: Some(crate::echo::DriftAnchor {
                passage_id: 5164, sample: 0, heard_at: now, rate: 44_100, ppm: None,
            }),
            // ...while having already admitted the new one.
            schedule: Some(crate::echo::Schedule {
                passage_id: 13796, start_sample: 0,
                sound_at: now + 100_000_000, rate: 44_100,
            }),
            voided_by: None,
        };
        let mut fs = FollowState::new();
        one_pass(&h, &st, &mut fs).await;
        assert!(fs.mid_joined.is_none(),
                "joined into {:?} while the master was mid-transition; each such \
join cuts the ring and re-imposes the join bias",
                fs.mid_joined.map(|(id, _)| id));
    }

    /// And the ordinary case still joins, which is the half a suppression
    /// this broad has to be held to `[GDE-ECHO-547]`.
    ///
    /// The master is mid-passage: its anchor and its schedule name the same
    /// passage, this node is on neither, and the listener asked to get in
    /// step straight away. That must still cut in.
    #[tokio::test]
    async fn a_node_on_the_wrong_passage_still_joins_at_once() {
        let h = node();
        let now = now_nanos();
        if let Ok(mut s) = h.state.lock() {
            s.echo_node.join_now = true;
            s.echo_node.rate = 44_100;
            s.echo_node.offset_frames = 44_100;
        }
        let st = EchoState {
            anchor: Some(crate::echo::DriftAnchor {
                passage_id: 5164, sample: 44_100 * 30, heard_at: now,
                rate: 44_100, ppm: None,
            }),
            // The master's last admission was this same passage: it is not in
            // a transition, it is simply playing.
            schedule: Some(crate::echo::Schedule {
                passage_id: 5164, start_sample: 0,
                sound_at: now + 100_000_000, rate: 44_100,
            }),
            voided_by: None,
        };
        let mut fs = FollowState::new();
        one_pass(&h, &st, &mut fs).await;
        assert_eq!(fs.mid_joined.map(|(id, _)| id), Some(5164),
                   "a node on the wrong passage must still join at once");
    }

    /// And once this node has committed to a start, nothing joins anywhere
    /// until that has had time to land `[GDE-ARC-042]`.
    ///
    /// The commitment used to be keyed on the passage it was for, so a start
    /// committed to 13796 left a join into 5164 entirely unsuppressed. After
    /// a commanded start this node's own state is in flux -- the ring is cut,
    /// the queue has advanced, its basis is voided -- and no join decision
    /// taken from it can be trusted.
    #[tokio::test]
    async fn a_commanded_start_suppresses_a_join_to_any_passage() {
        let h = node();
        let now = now_nanos();
        if let Ok(mut s) = h.state.lock() {
            s.echo_node.join_now = true;
            s.echo_node.rate = 44_100;
        }
        let st = EchoState {
            anchor: Some(crate::echo::DriftAnchor {
                passage_id: 5164, sample: 0, heard_at: now, rate: 44_100, ppm: None,
            }),
            schedule: None,
            voided_by: None,
        };
        let mut fs = FollowState::new();
        // A start committed one second ago, for a different passage.
        fs.mid_joined = Some((13796, std::time::Instant::now()));
        one_pass(&h, &st, &mut fs).await;
        assert_eq!(fs.mid_joined.map(|(id, _)| id), Some(13796),
                   "the commitment was replaced by a join to another passage");
    }

    /// A node `ms` out of step with the master, on the same passage, with a
    /// full enough filter to act on.
    fn out_by(h: &Arc<EngineHandle>, fs: &mut FollowState, ms: i64, align_now: bool)
        -> EchoState
    {
        let now = now_nanos();
        let anchor = crate::echo::DriftAnchor {
            passage_id: 7, sample: 0, heard_at: now, rate: 44_100, ppm: None,
        };
        if let Ok(mut s) = h.state.lock() {
            s.echo_node.rate = 44_100;
            s.echo_node.join_now = align_now;
            // This node reached the same sample `ms` later: it is behind.
            s.echo.anchor = Some(crate::echo::DriftAnchor {
                heard_at: now.saturating_add((ms * 1_000_000) as u64),
                ..anchor
            });
        }
        for i in 0..RESIDUAL_MIN_SAMPLES as u64 {
            fs.filtered.push(now - (RESIDUAL_MIN_SAMPLES as u64 - i) * 500_000_000,
                             ms * 1_000_000);
        }
        EchoState { anchor: Some(anchor), schedule: None, voided_by: None }
    }

    /// **A landing must not sample the position the join is replacing**
    /// `[GDE-ARC-069]`.
    ///
    /// `act` decides to join, schedules the start for a lead into the
    /// future, and falls through to `correct_offset` on the same pass. Those
    /// residuals are of the node's old position. `[GDE-ARC-059]` guards the
    /// window *after* the cut; this is the window before it.
    #[test]
    fn a_landing_ignores_readings_taken_before_the_start_fires() {
        let lead = Duration::from_millis(120);
        let mut l = Landing::new(0, lead);

        // Everything offered before the start could have fired is the old
        // position and must not be counted.
        for _ in 0..Landing::WANT * 2 {
            assert_eq!(l.push(9_999 * 1_000_000), None,
                       "a pre-join reading was accepted into the landing");
        }
        assert_eq!(l.residual_ms(), 0, "and none of them reached the samples");

        std::thread::sleep(lead + Duration::from_millis(20));
        let mut last = None;
        for _ in 0..Landing::WANT { last = l.push(250 * 1_000_000); }
        assert_eq!(last, Some(250),
                   "the post-join readings alone decide it; a 9999 ms outlier in the median means the guard let the old position through");
    }

    /// **A landing measures what is LEFT of the bias, not the bias**
    /// `[GDE-ARC-061]`, `[GDE-ECHO-346]`.
    ///
    /// Caught by seeding the learner on the fleet and reading the numbers it
    /// produced. Three uncorrected joins landed 611, 687 and 893 ms behind,
    /// giving a median of 687; applying that cut the next two to 133 and
    /// 174, which is the mechanism working. Then the median moved **down**
    /// to 611, because a residual of 133 was being filed as though the bias
    /// had shrunk to 133 -- when what it actually says is that the bias is
    /// 687 + 133.
    ///
    /// The window then holds corrected and uncorrected samples together, the
    /// median falls back towards whichever there are more of, and the loop
    /// hunts. This is `[GDE-ECHO-346]` exactly, which the rate trim in this
    /// same file already carries a paragraph about: a correction measured
    /// against a plant that is already being corrected has to be added to
    /// what is applied, never substituted for it.
    #[test]
    fn a_landing_reports_the_whole_bias_not_what_is_left_of_it() {
        // Uncorrected: what lands is the whole of it.
        let mut cold = Landing::new(0, Duration::ZERO);
        let mut last = None;
        for _ in 0..Landing::WANT { last = cold.push(611 * 1_000_000); }
        assert_eq!(last, Some(611));

        // Corrected by 687 and still 133 out: the bias is 820, and filing
        // 133 is what made the median walk backwards.
        let mut warm = Landing::new(687, Duration::ZERO);
        let mut last = None;
        for _ in 0..Landing::WANT { last = warm.push(133 * 1_000_000); }
        assert_eq!(last, Some(820), "a corrected join must report applied + residual");
        assert_eq!(warm.residual_ms(), 133, "and still be able to say what was left");

        // The property that matters: fed its own output, the estimate must
        // settle rather than walk. A node whose true bias is 800 and which is
        // already aiming 800 should record 800, not 0.
        let mut settled = Landing::new(800, Duration::ZERO);
        let mut last = None;
        for _ in 0..Landing::WANT { last = settled.push(0); }
        assert_eq!(last, Some(800),
                   "a join that landed perfectly must confirm the bias, not erase it");
    }

    /// **The whole residual reaches the boundary, whichever setting is on**
    /// `[GDE-ARC-051]`, `[SPEC-ECHO-030]`.
    ///
    /// This pair used to assert the difference the cap forced: with a 500 ms
    /// bite, 600 ms out left 100 ms the transition would not take, and
    /// "straight away" handed that remainder to the frame trim at once while
    /// "at the next track" did not `[GDE-ARC-041]`. The cap is gone, so there
    /// is no remainder to divide and the two settings no longer differ *here*
    /// -- they differ at the mid-join, which is decided before this point.
    ///
    /// Asserting the shift the engine is left holding, rather than the absence
    /// of a second command, is deliberate: the fault this replaces was a
    /// correction that was logged and then discarded `[GDE-ECHO-347]`, and a
    /// test that reads the state the engine will act on cannot be fooled that
    /// way.
    #[test]
    fn the_whole_offset_reaches_the_boundary_under_either_setting() {
        for straight_away in [true, false] {
            let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
            let handle = Arc::new(h);
            let mut fs = FollowState::new();
            let st = out_by(&handle, &mut fs, 600, straight_away);

            correct_offset(&st, &handle, &mut fs, now_nanos());
            e.tick();

            assert_eq!(e.echo_next_shift_ms, 600,
                       "straight_away={straight_away}: the boundary must be asked for all of it");
            // And nothing is trimmed mid-passage in either case. Under the old
            // cap this was the line that told the two settings apart; it is now
            // the same claim for both, because 0.1 ms/s was never going to
            // answer a step anyway `[GDE-ARC-043]`.
            assert_eq!(e.echo_debt_frames, 0,
                       "straight_away={straight_away}: nothing is owed to the trim");
        }
    }

    /// `[GDE-ECHO-375]`: the mid-join budget and the engine's own measured
    /// preparation are two models of one quantity, and they disagreed by a
    /// factor of two.
    ///
    /// The offset term cancels inside `join_mid_passage`, so the margin **is**
    /// the whole budget. The engine then fires early by `skip_lead_ms` plus a
    /// measured preparation that starts at 400 ms and is explicitly permitted
    /// to reach 2000 `[GDE-ECHO-342]`, so above about 600 ms of measured
    /// preparation every mid-join landed `TooLate` and was dropped -- which
    /// disables the one mechanism that aligns a node initially
    /// `[GDE-ECHO-344]`, and does it quietly.
    #[test]
    fn a_mid_join_aims_past_the_preparation_this_node_actually_needs() {
        let h = node();
        for lead_ms in [900_u64, 2_000, 2_500] {
            if let Ok(mut s) = h.state.lock() {
                s.echo_node.start_lead_ms = lead_ms;
            }
            let margin = mid_join_margin(&h).as_millis() as u64;
            assert!(margin > lead_ms,
                    "a join aimed {margin} ms out is declined by an engine that needs {lead_ms}");
        }
    }

    /// And the budget stays inside `[GDE-ECHO-325]`'s five-second allowance
    /// for a resync however wild the engine's estimate gets: a join aimed
    /// further out than that is no longer a join.
    #[test]
    fn the_mid_join_budget_stays_inside_the_resync_allowance() {
        let h = node();
        if let Ok(mut s) = h.state.lock() {
            s.echo_node.start_lead_ms = 60_000;
        }
        assert!(mid_join_margin(&h) <= MID_JOIN_MARGIN_MAX);
        // And a node that has published nothing yet still gets a usable one.
        let fresh = node();
        assert!(mid_join_margin(&fresh) >= MID_JOIN_MARGIN);
        // The ceiling must sit above the largest lead the engine can ever
        // ask for, or it becomes the same fault at a different number.
        let worst = Duration::from_millis(crate::SKIP_LEAD_MAX_MS)
            + Duration::from_millis(Engine::ECHO_PREP_MAX_MS);
        assert!(MID_JOIN_MARGIN_MAX > worst,
                "a {MID_JOIN_MARGIN_MAX:?} ceiling cannot cover a {worst:?} lead");
    }

    /// The other half of the same quantity: what the node publishes has to be
    /// what it actually fires early by, or the follower is aiming at a figure
    /// nothing honours `[GDE-ECHO-375]`.
    #[test]
    fn the_lead_a_node_publishes_is_the_lead_it_fires_early_by() {
        let (mut e, h) = Engine::new(crate::path::PathHandle::silent(), 1);
        // One slow seek into a long capture `[PI-CHR-075]`.
        e.echo_prep_ms = 1_500;
        e.tick();
        let published = h.snapshot().echo_node.start_lead_ms;
        assert_eq!(published, e.echo_start_lead_ms());
        assert_eq!(published, 2_000, "half a second of skip lead plus 1500 measured");

        // A start commanded exactly that far ahead must be reachable. Only a
        // join that actually FIRES re-measures its own cost, so the estimate
        // moving is the proof it was not dropped.
        let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64).unwrap_or(0);
        h.send(Command::EchoStartAt {
            entry: entry(4242),
            start_sample: 0,
            at_nanos: now + published * 1_000_000,
        });
        e.tick();
        assert_ne!(e.echo_prep_ms, 1_500,
                   "the join was declined rather than fired");
    }
}
