//! Echo playback: the anchor arithmetic, and the things that invalidate it.
//!
//! Phase 3 of [GUIDE009], the parts `[GDE-ECHO-370]` says to unit-test rather
//! than listen to: "the anchor arithmetic, the hysteresis and every row of
//! `[GDE-ECHO-360]` are unit-testable against a synthetic clock that can be
//! told to run fast -- and should be, because they are the parts where a sign
//! error is invisible in listening and obvious in a test."
//!
//! **No transport here, deliberately.** `[GDE-ECHO-320]` makes every message
//! absolute, idempotent and independently sufficient, which is what renders
//! the transport choice uninteresting: a node that misses one uses the next,
//! and a node that receives a stale one discards it by its own timestamp.
//! Nothing in this module needs to know how the bytes arrived.
//!
//! The two messages are [`Schedule`] (forward, emitted on mixer admission,
//! ~15 s before anyone hears it) and [`DriftAnchor`] (backward, repeated).
//! One kind is not enough `[GDE-ECHO-310]`: a backward-looking statement
//! cannot serve a node whose presentation offset exceeds the master's, because
//! such a node needed to submit *before the master did* and would learn of it
//! too late to act.

use std::time::Duration;

/// Nanoseconds on the chrony-disciplined wall clock, since the epoch.
///
/// The same basis `FrameClock::sample` pairs its frame count with. Deliberately
/// **not** cpal's `StreamInstant`, which counts from each stream's own trigger
/// and so shares no epoch between machines `[GDE-ECHO-160]`.
pub type WallNanos = u64;

/// What a node has to know about itself to place a schedule.
///
/// `[GDE-ECHO-410]`'s model, both halves. Measured per node, not assumed:
/// `bose` 2043 frames and `lempi02w` 15676 as of `[LOG-CPAL-060]`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct NodeTiming {
    /// Submit-to-air delay, in frames. ALSA's reported delay plus whatever
    /// calibrated residual the node has `[GDE-ECHO-430]`.
    pub presentation_offset_frames: u64,
    /// The device's nominal rate. Frames are converted at this; the *actual*
    /// rate error is what the trim loop corrects, and does not belong here.
    pub rate: u32,
}

impl NodeTiming {
    pub fn offset(&self) -> Duration {
        frames_to_duration(self.presentation_offset_frames, self.rate)
    }
}

/// Forward: *passage P, sample 0, will be heard at wall time T.*
///
/// Emitted when the master admits P to the mixer, which by `[REQ-AUD-160]` is
/// roughly 15 s before anyone hears it. That lead is the whole reason an
/// arbitrary presentation offset is compensable, and it exists only because an
/// echo node holds the file locally and knows the queue `[GDE-ECHO-420]`.
#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct Schedule {
    pub passage_id: i64,
    /// The sample within the passage that reaches the air at `sound_at`.
    ///
    /// Zero for an ordinary passage-to-passage transition, which every node
    /// sees coming and holds sync straight through `[GDE-ECHO-325]`. Non-zero
    /// is a **seek**: the master announces *this passage, this far in, at this
    /// instant* and each node fills toward that target independently. Carrying
    /// the sample here rather than inventing a second message is what keeps a
    /// seek from being a special case -- it is the same schedule with a
    /// different offset and a nearer `sound_at`.
    ///
    /// Defaulted on the wire, and this is the one place where absent really
    /// does mean zero `[GOV-SRC-040]`: a master too old to publish the field
    /// is a master that could only ever announce passage starts, so every
    /// schedule it ever sent began at sample 0. Reading one as such is not a
    /// guess, it is what the sender meant.
    #[serde(default)]
    pub start_sample: u64,
    /// When `start_sample` reaches the **air**, not the device.
    pub sound_at: WallNanos,
    pub rate: u32,
}

/// Backward: *sample N of P was heard at T, and my rate error is this.*
///
/// Computed from `audible_ms`, never `played_ms`: those differ by the ring's
/// depth and only one of them describes sound `[REQ-AUD-164]`. Note that the
/// engine's own `audible_ms` subtracts the ring but **not** the device delay,
/// which is imperceptible for a display and is not for `lempi02w`'s 355 ms --
/// so an anchor must subtract [`NodeTiming::offset`] as well.
#[derive(Clone, Copy, Debug, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct DriftAnchor {
    pub passage_id: i64,
    pub sample: u64,
    pub heard_at: WallNanos,
    pub rate: u32,
    /// The master's own measured rate error, parts per million, signed.
    ///
    /// `None` when nothing has measured it, which is **not** the same claim as
    /// `Some(0.0)` `[GOV-SRC-040]`. A follower reading a bare `+0.00` would
    /// reasonably take it for "the master checked and found no error"; this
    /// makes "nobody has computed this" a thing the wire can say. Caught by
    /// the first client that ever read one.
    pub ppm: Option<f64>,
}

fn frames_to_duration(frames: u64, rate: u32) -> Duration {
    Duration::from_nanos(frames.saturating_mul(1_000_000_000) / rate.max(1) as u64)
}

/// When this node must **submit** sample 0 for it to be heard on time.
///
/// `[GDE-ECHO-410]`: `submit_time(node, N) = sound_time(N) - offset(node)`.
/// Symmetric by construction -- no node is the reference, and the master
/// applies the identical arithmetic to itself. A design that instead treated
/// the master's offset as zero would have worked in one direction only.
///
/// `None` when the schedule has already passed for this node: with an offset
/// of 355 ms and an anchor 100 ms in the future, submission was due 255 ms
/// ago, and there is no arithmetic that recovers it. Saying so beats returning
/// a time in the past that reads like an instruction `[GOV-SRC-040]`.
pub fn submit_at(sched: &Schedule, node: NodeTiming, now: WallNanos) -> Option<WallNanos> {
    let offset_ns = node.offset().as_nanos() as u64;
    let submit = sched.sound_at.checked_sub(offset_ns)?;
    if submit < now {
        return None;
    }
    Some(submit)
}

/// What a node should do about a start instant that has been committed to.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum StartVerdict {
    /// Not yet. Check again next tick.
    Wait,
    /// Now, or near enough.
    Fire,
    /// Too far ahead to be a real schedule `[GDE-ECHO-365]`.
    ///
    /// A lead is fifteen seconds and a join is one. A start days out is a
    /// clock that has not been disciplined yet, not a patient node, and
    /// waiting for it means waiting for ever.
    TooFar { by: Duration },
    /// Too late to be worth doing `[GDE-ECHO-410]`.
    ///
    /// A join that misses its instant does not become a join that starts late:
    /// it puts this node audibly behind the fleet and leaves the trim loop
    /// hauling at an offset it never created. Holding for the next schedule
    /// costs one passage of silence on this node and nothing else.
    TooLate { by: Duration },
}

/// Decide, once, whether a committed start instant is due.
///
/// Separated from the engine because the engine's version cannot be tested:
/// it needs a real file, a real device and a real clock. The decision is the
/// part with a sign error in it, so it is the part that gets a test
/// `[GDE-ECHO-370]`.
///
/// `late_limit` exists because a tick is 10 ms and a join is therefore only
/// ever tick-accurate. That error does not persist -- `[GDE-ECHO-340]` corrects
/// offset at the next passage boundary, where it is inaudible -- so the limit
/// is set well above tick jitter and well below anything a listener would hear
/// as two speakers rather than one.
pub fn start_verdict(
    at: WallNanos,
    now: WallNanos,
    late_limit: Duration,
    far_limit: Duration,
) -> StartVerdict {
    if now < at {
        let ahead = Duration::from_nanos(at - now);
        return if ahead > far_limit {
            StartVerdict::TooFar { by: ahead }
        } else {
            StartVerdict::Wait
        };
    }
    let late = Duration::from_nanos(now - at);
    if late > late_limit {
        StartVerdict::TooLate { by: late }
    } else {
        StartVerdict::Fire
    }
}

/// The master's clock, as this node can best estimate it `[GDE-ECHO-366]`.
///
/// **A follower does not need its own clock to be right; it needs to agree
/// with the node it follows.** Those are different problems and only one of
/// them requires privileges. Carrying the difference as an offset means the
/// follower's own wall clock drops out of every scheduling decision, so a node
/// booted two days behind -- which is every node here after a power cut, none
/// having an RTC -- computes exactly the same submission *interval* as a
/// perfectly disciplined one.
///
/// The alternative, stepping the clock to match, needs root the player does
/// not have and fights the NTP daemon that is actively disciplining it. This
/// needs neither and works from the first snapshot.
///
/// **Estimated by maximum, not by mean.** `heard_at` is stamped as the master
/// builds the snapshot, so it reaches this node one transport delay later and
/// every sample reads low by however long the network took. Delay is
/// one-sided -- it can lengthen but never go below the wire -- so the largest
/// observed difference is the least contaminated one, where an average would
/// bake in the typical delay instead. What remains is a few milliseconds on a
/// LAN, inside the anchor's own jitter `[LOG-P4-010]` and therefore not worth
/// a round trip to remove.
#[derive(Debug)]
pub struct MasterClock {
    /// `(own clock at receipt, master - own)`, oldest first.
    samples: std::collections::VecDeque<(WallNanos, i64)>,
    window: Duration,
    step: Duration,
    /// Consecutive readings that sit a step BELOW the established offset.
    ///
    /// A late snapshot and a backward clock step are the same observation
    /// `[GDE-ARC-049]`; only persistence tells them apart, so a low reading
    /// is counted rather than acted on.
    low_run: u8,
    /// The offset that stood when the current low run opened.
    low_ref: i64,
}

/// How many consecutive low readings before one is believed.
///
/// Snapshots arrive twice a second, so three is about a second and a half --
/// quick enough that a genuine step costs the rate window almost nothing,
/// and long enough that a single late packet, which is what this exists to
/// ignore, cannot reach it. A delay that persists for three snapshots is a
/// network fault the estimate should be reset for anyway.
const STEP_CONFIRMATIONS: u8 = 3;

impl MasterClock {
    pub fn new(window: Duration, step: Duration) -> Self {
        Self { samples: std::collections::VecDeque::new(), window, step, low_run: 0, low_ref: 0 }
    }

    pub fn clear(&mut self) {
        self.samples.clear();
        self.low_run = 0;
    }

    /// Take a reading. **True when either clock has stepped**, which the
    /// caller must treat as it treats any other discontinuity: a rate fitted
    /// across a step is not a rate `[RateEstimate::clear]`.
    pub fn observe(&mut self, master_heard_at: WallNanos, own_now: WallNanos) -> bool {
        let offset = master_heard_at as i64 - own_now as i64;
        // **A reading below the estimate is what a late snapshot looks like,
        // so one of them is not evidence** `[GDE-ARC-049]`. `heard_at` is
        // stamped as the master builds the snapshot, so transport delay
        // subtracts from every reading -- which is exactly why `offset()`
        // takes the maximum. Comparing a single new reading against that
        // maximum therefore read any snapshot over a step late as a backward
        // clock step: four of them in thirty minutes on a wifi node whose
        // chrony had stepped nothing at all, each one clearing the rate
        // window and zeroing the trim.
        //
        // Above the estimate is different and needs no confirming: no wire
        // delivers a snapshot early, so that can only be a real step.
        let limit = self.step.as_nanos() as i64;
        let stepped = if self.low_run > 0 {
            // **Judged against the offset that stood when the run opened**,
            // not against the current estimate. A large step ages every
            // older sample out of the window on its first reading, so the
            // estimate becomes the shifted level immediately and comparing
            // against it would say "settled" and reset the run -- confirming
            // nothing, ever. Found by the test for an hour-long step.
            if (offset - self.low_ref).abs() > limit {
                self.low_run = self.low_run.saturating_add(1);
                self.low_run >= STEP_CONFIRMATIONS
            } else {
                self.low_run = 0;
                false
            }
        } else {
            match self.offset() {
                None => false,
                Some(had) if offset - had > limit => true,
                Some(had) if offset - had < -limit => {
                    self.low_ref = had;
                    self.low_run = 1;
                    false
                }
                Some(_) => false,
            }
        };
        if stepped {
            self.low_run = 0;
            self.samples.clear();
        }
        self.samples.push_back((own_now, offset));
        let cutoff = own_now.saturating_sub(self.window.as_nanos() as u64);
        while self.samples.front().is_some_and(|(t, _)| *t < cutoff) {
            self.samples.pop_front();
        }
        stepped
    }

    /// `master - own`, or `None` before anything has been observed.
    pub fn offset(&self) -> Option<i64> {
        self.samples.iter().map(|(_, o)| *o).max()
    }

    /// An instant on the master's clock, as this node's own clock reads it.
    pub fn to_local(&self, master: WallNanos) -> Option<WallNanos> {
        let o = self.offset()?;
        (master as i64).checked_sub(o).map(|v| v.max(0) as u64)
    }

    /// What the master's clock reads now.
    pub fn now(&self, own_now: WallNanos) -> Option<WallNanos> {
        let o = self.offset()?;
        (own_now as i64).checked_add(o).map(|v| v.max(0) as u64)
    }
}

/// Whether two nodes are using the same clock at all `[GDE-ECHO-365]`.
///
/// Nothing in echo works across a wall-clock disagreement: every instant on
/// the wire is absolute, so a node whose clock is out by days computes a
/// submission time days away and waits for it. No node in this fleet has an
/// RTC, so each boots on a restored time and is stepped by NTP minutes later
/// -- a window in which a follower can connect, adopt a queue, and silently do
/// nothing at all. Observed on `lempiplay3` 2026-09-18 after a power cycle.
///
/// Measured against the master rather than asked of the operating system: the
/// question is not "is this node disciplined" but "do these two agree", and
/// the anchor already carries the other side's answer.
pub fn clocks_agree(master_heard_at: WallNanos, now: WallNanos, tolerance: Duration) -> bool {
    let skew = (now as i64 - master_heard_at as i64).unsigned_abs();
    skew <= tolerance.as_nanos() as u64
}

/// Where sample 0 must sit in this node's output ring at admission.
///
/// **`submit_at` says *when* sample 0 must reach the device; it does not say
/// how a node makes that happen, and the obvious reading is wrong.** Admitting
/// the passage to the mixer at `submit_at` puts sample 0 at the *back* of a
/// ring that is 15.0 s deep `[LOG-ECHO-020]`, so it would sound a full ring
/// late. The knob is not when to admit -- it is how much audio sits ahead of
/// sample 0 when it does.
///
/// In steady state admission is not a free choice anyway: the ring is full of
/// the previous passage, and sample 0 goes in where that passage ends. Two
/// nodes admitting at the same point in the same programme therefore differ in
/// air time by exactly their device delays, permanently, and waiting cannot
/// correct it because waiting only makes a node later. Running the ring at
/// different depths can, which is feasible only because a ring fills at decode
/// speed rather than in real time -- it drains in real time, it does not fill
/// that way.
///
/// So each node runs at `depth = Total - device_delay`, for one `Total` common
/// to the fleet, and `depth <= capacity` caps `Total` at
/// `capacity + min(device delay)` `[LOG-ECHO-030]`. That is a property of the
/// **fleet**, not a constraint on which node may be master: the node with the
/// smallest delay simply runs the fullest ring, and any node may announce
/// `[GDE-ECHO-315]`.
///
/// `TooShallow` therefore reports a `Total` this node cannot reach, which in
/// practice means the announcer computed it from its own full ring instead of
/// from the fleet's cap -- exactly what `schedule_for_admission` does today.
/// With `bose` at 46 ms and `lempi02w` at 355 ms `[LOG-CPAL-060]`, a `lempi02w`
/// announcing off a full ring asks for 15.355 s and `bose` can reach 15.046 s;
/// the fix is the announced total, not the choice of master.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Placement {
    /// Admit with exactly this many frames ahead of sample 0 in the ring.
    Depth(u64),
    /// This node cannot be late enough: the master's submit-to-air distance
    /// exceeds this node's ring plus its device delay. Reported rather than
    /// clamped, because a clamped depth plays *early* on every passage and
    /// looks like a working fleet with a drift problem.
    TooShallow { short_by_frames: u64 },
    /// The instant has passed -- the schedule arrived later than this node's
    /// own device delay leaves room for.
    Late { by: Duration },
}

/// **Status: taken, 2026-09-19** `[GDE-ARC-058]`. Called by `Engine::skip`
/// through `join_lead_frames`, from the moment the ring is cut -- after the
/// preparation, which is the whole point `[GDE-ECHO-342]`, `[GDE-ECHO-377]`.
///
/// The standing decision not to take it held for a good reason and was
/// retired by a measurement rather than by a change of taste:
/// `cut_ring_to_incoming` is the real-time path every ordinary user skip
/// also uses, and a measured constant was reckoned to reach most of the
/// benefit. On this fleet it did not -- `echo_prep_ms` was assuming 400 ms
/// against a real 33-37, converging by quarters over joins that happen a few
/// times a day, and resetting to the cold guess at every restart. The
/// ordinary skip path is untouched: with no commanded start pending, the
/// lead is the same constant it always was.
pub fn placement(
    sched: &Schedule,
    node: NodeTiming,
    ring_capacity_frames: u64,
    now: WallNanos,
) -> Placement {
    let rate = node.rate.max(1) as u64;
    let Some(ahead_ns) = sched.sound_at.checked_sub(now) else {
        return Placement::Late { by: Duration::from_nanos(now - sched.sound_at) };
    };
    // Round to nearest, not down. `sound_at` was itself built from a frame
    // count divided into nanoseconds, so truncating here loses whatever that
    // division dropped -- and it loses it in one direction, making every node
    // a frame or two shallow and therefore early. 23 us is inaudible; a
    // systematic sign is still worth not having `[GOV-SRC-040]`.
    let ahead_frames = (ahead_ns.saturating_mul(rate) + 500_000_000) / 1_000_000_000;
    let Some(depth) = ahead_frames.checked_sub(node.presentation_offset_frames) else {
        // The sound is nearer than this node's device delay: even a depth of
        // zero is too late.
        let short = node.presentation_offset_frames - ahead_frames;
        return Placement::Late { by: frames_to_duration(short, node.rate) };
    };
    if depth > ring_capacity_frames {
        return Placement::TooShallow { short_by_frames: depth - ring_capacity_frames };
    }
    Placement::Depth(depth)
}

/// When this node reached the master's anchored sample.
///
/// **The two anchors are never about the same sample.** The master's crossed a
/// network and describes some sample it played a moment ago; this node's
/// describes wherever it is now. Subtracting their timestamps therefore
/// measures the gap between two *readings*, which on a quiet network is mostly
/// transport delay and has nothing to do with alignment.
///
/// So the local reading is carried along its own playback to the sample the
/// master named, and only then compared. Positions advance at one millisecond
/// per millisecond, so this is a subtraction rather than a model.
///
/// **The only residual arithmetic in this module, deliberately.** There used
/// to be a `residual_ns` beside it that subtracted the two *timestamps* --
/// the defect `[GDE-ECHO-345]` records as measuring the network rather than
/// the alignment -- kept alive by nothing but its own test, under a name that
/// sounded like the right one to reach for. It is gone `[GDE-ECHO-377]`.
/// Subtract `anchor.heard_at` from what this returns and the sign convention
/// is the usual one: **positive means this node is late**, its audio reached
/// the air after the master's, so it must speed up or start earlier.
pub fn local_at_sample(local: &AirPosition, anchor: &DriftAnchor) -> i64 {
    let anchor_ms = anchor.sample.saturating_mul(1000) / anchor.rate.max(1) as u64;
    local.at as i64 + (anchor_ms as i64 - local.position_ms as i64) * 1_000_000
}

/// A residual worth acting on at sample resolution.
///
/// **The actuator is finer than the measurement, and that is the trap.** One
/// frame is 23 us, but one anchor reading carries tens of milliseconds of the
/// output ring's own depth jitter `[LOG-P4-010]`. Correcting a position from a
/// single reading below that noise is chasing it `[GDE-ECHO-350]`.
///
/// A median over a couple of minutes is what makes the endgame measurable --
/// the **median** and not the mean, because the ring's shortfall is bounded on
/// one side and unbounded on the other, so the noise has a tail rather than a
/// shape.
///
/// The window is derived rather than chosen `[GDE-ECHO-348]`: filtered noise
/// falls as `1.25 s / sqrt(2T)` while the drift accruing *during* the window
/// grows as `r * T`. At 30 ms of scatter and the 13.92 ppm measured for this
/// pair `[LOG-P4-130]` the two cross near 130 s, about 2 ms each. Shorter is
/// all noise, longer is all lag, and the sum is flat enough either side that
/// 120 s is right to within a factor of two.
///
/// Cleared by everything that clears the rate window, for the same reason: a
/// correction, a rejoin or a clock step moves the quantity, and a median
/// across that move describes neither side of it.
#[derive(Debug)]
pub struct ResidualFilter {
    samples: std::collections::VecDeque<(WallNanos, i64)>,
    window: Duration,
    min_samples: usize,
}

impl ResidualFilter {
    pub fn new(window: Duration, min_samples: usize) -> Self {
        Self { samples: std::collections::VecDeque::new(), window, min_samples }
    }

    pub fn clear(&mut self) {
        self.samples.clear();
    }

    pub fn push(&mut self, at: WallNanos, residual: i64) {
        self.samples.push_back((at, residual));
        let cutoff = at.saturating_sub(self.window.as_nanos() as u64);
        while self.samples.front().is_some_and(|(t, _)| *t < cutoff) {
            self.samples.pop_front();
        }
    }

    /// The filtered residual, or `None` until the window is full enough to
    /// mean something. Refusing beats answering early `[GOV-SRC-040]`.
    pub fn median(&self) -> Option<i64> {
        if self.samples.len() < self.min_samples {
            return None;
        }
        let mut v: Vec<i64> = self.samples.iter().map(|(_, r)| *r).collect();
        v.sort_unstable();
        Some(v[v.len() / 2])
    }
}

/// The relative rate error, fitted from how the residual moves.
///
/// **A slope, not a position.** One residual reading carries the output ring's
/// depth jitter -- tens of milliseconds `[LOG-P4-010]` -- so a loop driven by
/// the latest one would chase noise `[GDE-ECHO-350]`. A line fitted across
/// minutes of them does not: the jitter is zero-mean and divides out, leaving
/// the drift that actually accumulates. Thirty milliseconds of scatter over a
/// ten-minute span is 0.05 ppm of slope error.
///
/// **The window has to be long, and the arithmetic says how long.** For
/// scatter `s` sampled `n` times across a span `T`, the slope's standard error
/// is `s / (sd(t) * sqrt(n))` with `sd(t) = T/sqrt(12)`. At 30 ms of scatter
/// and two samples a second that is **5 ppm over ten minutes** and about
/// **0.35 ppm over an hour** -- so ten minutes measures nothing useful about a
/// 14 ppm drift, and an hour measures it comfortably. An earlier version of
/// this comment claimed 0.05 ppm at ten minutes, which was wrong by two orders
/// of magnitude and would have justified acting on noise.
///
/// An hour is also about the timescale the underlying rate itself wanders on
/// -- `bose`'s hourly windows scatter 2.5-3.2 ppm `[LOG-P4-130]` -- so there
/// is nothing to gain by averaging further: past that the quantity has moved.
///
/// This is why the rate half of `[GDE-ECHO-340]` can be built on the anchor
/// that exists, while the offset half cannot do better than the anchor's own
/// precision.
#[derive(Debug)]
pub struct RateEstimate {
    /// `(nanoseconds since the first sample, residual)`, oldest first.
    ///
    /// Relative to the first rather than absolute: wall-clock nanoseconds are
    /// near 1.8e18, and squaring those in a least-squares fit spends most of
    /// an `f64`'s precision on a constant that cancels anyway.
    samples: std::collections::VecDeque<(i64, i64)>,
    origin: WallNanos,
    window: Duration,
    min_span: Duration,
    min_samples: usize,
}

impl RateEstimate {
    pub fn new(window: Duration, min_span: Duration, min_samples: usize) -> Self {
        Self {
            samples: std::collections::VecDeque::new(),
            origin: 0,
            window,
            min_span,
            min_samples,
        }
    }

    /// Forget everything measured so far.
    ///
    /// **Called whenever the residual is moved by something other than drift**
    /// -- an offset correction, a rejoin, a voided basis. A step in the middle
    /// of the window is read by a straight-line fit as an enormous slope, and
    /// the loop would then trim hard against a rate error that never existed.
    pub fn clear(&mut self) {
        self.samples.clear();
        self.origin = 0;
    }

    pub fn push(&mut self, at: WallNanos, residual: i64) {
        if self.samples.is_empty() {
            self.origin = at;
        }
        let t = at as i64 - self.origin as i64;
        self.samples.push_back((t, residual));
        let cutoff = t - self.window.as_nanos() as i64;
        while self.samples.front().is_some_and(|(ts, _)| *ts < cutoff) {
            self.samples.pop_front();
        }
    }

    /// The fitted rate error in ppm, positive when this node is falling behind.
    ///
    /// `None` until the window is both long enough and full enough to mean
    /// something. An estimate from thirty seconds is not a small estimate, it
    /// is a different quantity `[GDE-ECHO-350]`.
    pub fn ppm(&self) -> Option<f64> {
        if self.samples.len() < self.min_samples {
            return None;
        }
        let (first, last) = (self.samples.front()?.0, self.samples.back()?.0);
        if (last - first) < self.min_span.as_nanos() as i64 {
            return None;
        }
        let n = self.samples.len() as f64;
        let mean_t = self.samples.iter().map(|(t, _)| *t as f64).sum::<f64>() / n;
        let mean_r = self.samples.iter().map(|(_, r)| *r as f64).sum::<f64>() / n;
        let mut num = 0.0;
        let mut den = 0.0;
        for (t, r) in &self.samples {
            let dt = *t as f64 - mean_t;
            num += dt * (*r as f64 - mean_r);
            den += dt * dt;
        }
        (den > 0.0).then(|| num / den * 1e6)
    }
}

/// The trim to apply next, given what is already applied and what is left.
///
/// **A fitted slope is the error in the correction, not the drift.** The
/// residual being fitted is what remains *after* the current trim, so applying
/// the fit as an absolute sets the trim to `R - A` when it already holds `A`.
/// That map has a fixed point at half the drift and an eigenvalue of -1: it
/// settles at half-correction or oscillates about it, and either way leaves
/// about 7 ppm of the 13.92 measured for this fleet uncorrected for ever
/// `[LOG-P4-130]`, which is 25 ms an hour `[GDE-ECHO-346]`.
///
/// Adding instead is deadbeat: one window, and what is left is the fit's own
/// error rather than half the drift.
pub fn next_trim_ppm(applied: f64, fitted: f64) -> f64 {
    applied + fitted
}

/// How long between single-frame trims, to cancel a rate error of `ppm`.
///
/// A frame is `1/rate` of a second of position, and the error accrues
/// `ppm * 1e-6` seconds every second, so they balance at
/// `1 / (ppm * 1e-6 * rate)`. At the +13.92 ppm measured for
/// `bose`-`lempiplay3` `[LOG-P4-130]` that is one frame every 1.63 s, which is
/// the arithmetic `[GDE-ECHO-340]` does by hand.
///
/// `None` below a rate error too small to be worth correcting, where the
/// interval would run to hours and the estimate is mostly noise anyway.
pub fn trim_interval(ppm: f64, rate: u32, floor_ppm: f64) -> Option<Duration> {
    if !ppm.is_finite() || ppm.abs() < floor_ppm {
        return None;
    }
    let per_second = ppm.abs() * 1e-6 * rate as f64;
    (per_second > 0.0).then(|| Duration::from_secs_f64(1.0 / per_second))
}

/// What to do about an offset that trimming will not remove.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum OffsetFix {
    /// Inside the deadband. Leave it alone `[GDE-ECHO-350]`.
    Hold,
    /// Start the next passage this many milliseconds **earlier**; negative is
    /// later.
    ///
    /// The whole of `[GDE-ECHO-340]`'s offset correction, and symmetric:
    /// where the incoming passage sits inside the transition is free in both
    /// directions. Earlier overlaps a little more and catches up, later
    /// overlaps a little less and waits. No content is skipped or repeated
    /// either way `[should_admit_nudged]`.
    ShiftStart(i64),
    /// Start the next passage from the master's schedule instead.
    ///
    /// **Only for an offset so large the node is probably not playing what it
    /// thinks it is.** A rejoin is not a smaller correction than a shift, it
    /// is a *different* one: it throws the first sample down afresh and
    /// inherits whatever error that placement carries, where a shift always
    /// reduces the error it was given. Reaching for it to fix an ordinary
    /// offset is how a follower thrashes -- observed on `lempiplay3`
    /// 2026-09-18, rejoining every few seconds and holding a steady 0.9 s of
    /// lag it never once reduced `[GDE-ECHO-341]`.
    Rejoin,
}

/// Decide the offset correction for the passage about to be opened.
///
/// Called at admission, which is a ring's depth before anyone hears the
/// result -- the correction is aimed at audio fifteen seconds out, using a
/// residual measured from audio fifteen seconds old. That is sound only
/// because an offset is a position rather than a slope: it does not grow
/// while nobody is looking, which is exactly the property `[GDE-ECHO-340]`
/// separates it from rate for.
pub fn offset_fix(
    residual: i64,
    deadband: Duration,
    rejoin_beyond: Duration,
) -> OffsetFix {
    if residual.unsigned_abs() <= deadband.as_nanos() as u64 {
        return OffsetFix::Hold;
    }
    if residual.unsigned_abs() > rejoin_beyond.as_nanos() as u64 {
        return OffsetFix::Rejoin;
    }
    // **The whole of it, at the next transition** `[GDE-ARC-051]`. There was a
    // `max_bite` here, half a second, and an offset larger than one bite was
    // taken across several transitions -- four to six minutes apart, so 886 ms
    // was ten minutes of walking in. The cap was reasoned about the *overlap*:
    // half a second of a crossfade made longer or shorter is not something a
    // listener can point at. True, and no longer the constraint, because the
    // overlap is not the only thing the boundary can spend `[GDE-ARC-052]`.
    //
    // **Bitten off, never escalated** `[GDE-ECHO-341]` survives and is now
    // trivially satisfied: one correction takes the error to zero rather than
    // reducing it. Escalating to a rejoin re-places the first sample and
    // inherits that placement's own error, so a node with a systematic join
    // bias corrects forever and converges never.
    //
    // Late is positive, and a late node starts the next passage EARLIER -- the
    // sign survives unchanged, which is worth saying because it reads either
    // way at a glance.
    OffsetFix::ShiftStart(residual / 1_000_000)
}

/// Where a passage actually is **in the air**, and when that was true.
///
/// Distinct from the engine's `audible_ms`, which subtracts the output ring
/// but not the device: those differ by the presentation offset, which is 46 ms
/// on `bose` and 355 on `lempi02w` `[LOG-CPAL-060]`. Imperceptible for a
/// display, and a third of a second for echo.
///
/// `audible_ms` is deliberately **not** changed to match. It drives the UI and
/// the resume point, and moving those by 355 ms to serve echo would be the
/// tail wagging the dog `[REQ-AUD-164]`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct AirPosition {
    pub passage_id: i64,
    pub position_ms: u64,
    pub at: WallNanos,
}

/// What is being heard, from what the mixer has produced and what is queued
/// ahead of the air.
///
/// Saturates at zero rather than wrapping: early in a passage the ring plus
/// the device delay exceed what has been mixed, and the honest answer is that
/// none of this passage is audible yet.
pub fn air_position(
    passage_id: i64,
    played_ms: u64,
    ring_frames: u64,
    device_delay_frames: u64,
    rate: u32,
    at: WallNanos,
) -> AirPosition {
    let ahead_ms = (ring_frames + device_delay_frames) * 1000 / rate.max(1) as u64;
    AirPosition { passage_id, position_ms: played_ms.saturating_sub(ahead_ms), at }
}

impl AirPosition {
    /// The backward anchor this position supports.
    pub fn anchor(&self, rate: u32, ppm: Option<f64>) -> DriftAnchor {
        DriftAnchor {
            passage_id: self.passage_id,
            sample: self.position_ms * rate as u64 / 1000,
            heard_at: self.at,
            rate,
            ppm,
        }
    }
}

/// When a passage admitted to the mixer **now** will start to sound.
///
/// Everything already in the ring plays first, then the device's own delay.
/// That sum is the ~15 s of lead `[REQ-AUD-160]` gives, and it is the entire
/// reason an arbitrary presentation offset is compensable `[GDE-ECHO-310]`:
/// the announcement goes out long before anybody could hear it.
pub fn schedule_for_admission(
    passage_id: i64,
    start_sample: u64,
    ring_frames: u64,
    device_delay_frames: u64,
    rate: u32,
    now: WallNanos,
) -> Schedule {
    let ahead_ns = (ring_frames + device_delay_frames) * 1_000_000_000 / rate.max(1) as u64;
    Schedule { passage_id, start_sample, sound_at: now + ahead_ns, rate }
}

/// What a master publishes for echo nodes, on the snapshot's own cadence.
///
/// `[GDE-ECHO-310]` puts both messages on the WebSocket the browser snapshot
/// already uses, which is why the transport is uninteresting: it exists.
///
/// `voided_by` is carried rather than implied by a missing anchor. A node that
/// receives nothing cannot tell "the master is quiet" from "the master cannot
/// currently place itself", and those call for different behaviour -- the
/// second means hold position and wait for the next passage boundary
/// `[GDE-ECHO-360]`, not go independent `[GDE-ECHO-500]`.
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct EchoState {
    pub anchor: Option<DriftAnchor>,
    pub schedule: Option<Schedule>,
    pub voided_by: Option<Voided>,
}

/// Everything that voids the frame clock as a basis for an anchor.
///
/// `[GDE-ECHO-360]`. Each of these must force a rejoin at the next passage
/// boundary rather than a silent continuation on stale state.
#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum Voided {
    /// Frame counter and stream epoch both reset `[SPEC-APS-010]`.
    DeviceReopen,
    /// Frames that were counted were never heard `[REQ-AUD-142]`.
    Underrun,
    /// The device stopped and the count stopped with it.
    Pause,
    /// The ring was cut `[REQ-AUD-158]`, so counted frames were discarded.
    Skip,
}

/// Whether the local frame clock can still be compared against an anchor.
///
/// **The derived default is the invalid state, and that is the point.** A node
/// that has just opened a stream has not yet earned the right to be compared
/// against anything, so `established` starts false and only a clean passage
/// boundary sets it. `Undetermined` is not `Valid` `[GOV-SRC-040]`.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Basis {
    voided_by: Option<Voided>,
    established: bool,
}

impl Basis {
    /// A passage boundary reached cleanly: this is the only way in.
    pub fn establish(&mut self) {
        self.voided_by = None;
        self.established = true;
    }
    pub fn void(&mut self, why: Voided) {
        self.voided_by = Some(why);
        self.established = false;
    }
    pub fn is_valid(&self) -> bool {
        self.established && self.voided_by.is_none()
    }
    /// What broke it, for saying so rather than silently not correcting.
    pub fn voided_by(&self) -> Option<Voided> {
        self.voided_by
    }
}

/// One correction step for the rate error `[GDE-ECHO-340]`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Trim {
    /// Inside the deadband, or too soon, or on no basis at all.
    None,
    /// This node is **late**: skip a frame so its playback position advances
    /// without time passing, and it catches up.
    DropFrame,
    /// This node is **early**: repeat a frame so time passes without its
    /// position advancing, and it falls back.
    DuplicateFrame,
}

/// Whether to trim, given the residual and how long since the last trim.
///
/// `[GDE-ECHO-350]`: "Trim only while the estimated offset exceeds a deadband
/// comfortably larger than the measurement noise, never more than one frame
/// per correction interval, and never on an estimate younger than the
/// regression window. A correction loop that reacts to its own measurement
/// noise produces exactly the slow periodic wobble it was built to remove."
///
/// All three guards are parameters rather than constants because the numbers
/// are still being measured: `lempi02w` has no single rate at all
/// `[LOG-CAL-080]`, so a constant chosen today would be wrong for it tomorrow.
pub fn trim_decision(
    basis: &Basis,
    residual: i64,
    deadband: Duration,
    since_last_trim: Duration,
    min_interval: Duration,
) -> Trim {
    if !basis.is_valid() {
        return Trim::None;
    }
    if since_last_trim < min_interval {
        return Trim::None;
    }
    let band = deadband.as_nanos() as i64;
    if residual.abs() <= band {
        return Trim::None;
    }
    // Positive residual means this node is LATE, so it must catch up: drop a
    // frame, and the position advances by one more than the time spent.
    // Getting this backwards doubles the error instead of removing it, and
    // sounds like nothing in particular until hours have passed -- which is
    // why `trim_directions_are_not_reversed` exists and why the mutation that
    // swaps these two arms fails three tests.
    if residual > 0 {
        Trim::DropFrame
    } else {
        Trim::DuplicateFrame
    }
}

/// What an echo node decides to do about the master's forward schedule.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Follow {
    /// Nothing new to act on -- no schedule, or one already acted on.
    Idle,
    /// Begin this passage by submitting sample 0 at this instant.
    StartAt { passage_id: i64, start_sample: u64, at: WallNanos },
    /// The schedule cannot be honoured: submission was already due.
    ///
    /// Distinct from `Idle` because it is not nothing happening. A node with a
    /// large presentation offset misses schedules a low-offset node makes
    /// comfortably `[GDE-ECHO-410]`, and it must rejoin at the next boundary
    /// rather than start late and be trimmed towards a master it never caught.
    Missed { passage_id: i64 },
    /// The master says it cannot currently place itself in time.
    ///
    /// **Hold, do not go independent.** `[GDE-ECHO-500]`'s handover is for a
    /// master that has gone *away*; this one is present and honest, and will
    /// re-establish at its own next passage boundary `[GDE-ECHO-360]`.
    Hold(Voided),
}

/// An echo node's side of the wire.
///
/// Holds no transport and no queue: it decides, and something else acts. That
/// keeps every branch here reachable from a test, which `[GDE-ECHO-370]`
/// asks for precisely because a sign error in this arithmetic is inaudible
/// until hours have passed.
#[derive(Clone, Debug)]
pub struct Follower {
    pub timing: NodeTiming,
    /// This node's own basis -- its own underruns and reopens, not the
    /// master's. Both must be sound before a residual means anything.
    pub basis: Basis,
    pub deadband: Duration,
    pub min_trim_interval: Duration,
    /// The last schedule acted on, whole.
    ///
    /// Keyed on the entire message rather than its `passage_id`, because a
    /// seek re-announces the passage already playing `[GDE-ECHO-325]` -- and a
    /// dedup on the id alone would discard exactly the message the seek exists
    /// to deliver. Comparing the whole thing is also the natural reading of
    /// `[GDE-ECHO-320]`: every message is absolute, so two identical ones are
    /// the same instruction and any difference is a new one.
    acted: Option<Schedule>,
    last_trim: Option<WallNanos>,
}

impl Follower {
    pub fn new(timing: NodeTiming, deadband: Duration, min_trim_interval: Duration) -> Self {
        Self { timing, basis: Basis::default(), deadband, min_trim_interval,
               acted: None, last_trim: None }
    }

    /// What to do about the master's schedule, if anything.
    ///
    /// Idempotent by passage: the same schedule arriving twice a second for
    /// fifteen seconds produces one `StartAt` and then `Idle`, which is what
    /// lets `[GDE-ECHO-320]` repeat every message without a sequence number.
    pub fn on_state(&mut self, st: &EchoState, now: WallNanos) -> Follow {
        if let Some(why) = st.voided_by {
            return Follow::Hold(why);
        }
        let Some(sched) = st.schedule else { return Follow::Idle };
        if self.acted == Some(sched) {
            return Follow::Idle;
        }
        self.acted = Some(sched);
        match submit_at(&sched, self.timing, now) {
            Some(at) => Follow::StartAt {
                passage_id: sched.passage_id, start_sample: sched.start_sample, at },
            None => Follow::Missed { passage_id: sched.passage_id },
        }
    }

    /// Whether to trim, given the master's anchor and this node's own air.
    ///
    /// Returns `None` when no comparison is possible at all -- a missing
    /// anchor, a voided basis on either side, or the two describing *different
    /// passages*, which is the case a naive implementation would silently
    /// treat as an enormous error and trim hard against `[GOV-SRC-040]`.
    ///
    /// **Status: reachable, and not the live path** `[GDE-ECHO-377]`. The
    /// running follower does not trim from here -- it fits a slope across an
    /// hour `[GDE-ECHO-356]` and commands ppm, because one anchor reading
    /// carries tens of milliseconds of ring jitter `[LOG-P4-010]` and a
    /// position servo on it hunts. `echoprobe` is the only caller, and it
    /// passes `local: None`, so in the tree as it stands nothing reaches the
    /// arithmetic below outside this module's own tests -- `basis`,
    /// `deadband`, `min_trim_interval` and `last_trim` with it.
    ///
    /// It is kept rather than deleted because it is the decision an observer
    /// needs the moment one has an air position to offer. **Anything adopting
    /// it must add the clock translation first**: this subtracts two
    /// instants that are on two different nodes' wall clocks, where the live
    /// path carries the local reading into the master's frame before
    /// comparing `[GDE-ECHO-366]`. On a pair whose clocks agree that costs
    /// nothing; on a node booted without an RTC it measures the clocks.
    pub fn trim_for(
        &mut self,
        st: &EchoState,
        local: Option<&AirPosition>,
        now: WallNanos,
    ) -> Option<Trim> {
        if st.voided_by.is_some() {
            return None;
        }
        let anchor = st.anchor?;
        let local = local?;
        if local.passage_id != anchor.passage_id {
            return None;
        }
        // Where the master says this node's own sample should have been heard,
        // and where it actually was. Both are on the disciplined wall clock,
        // so the difference is a real offset rather than a clock comparison.
        // Carried to the master's own sample first `[local_at_sample]`;
        // comparing the two readings directly would measure transport delay.
        let residual = local_at_sample(local, &anchor) - anchor.heard_at as i64;
        let since = self.last_trim.map_or(self.min_trim_interval, |t| {
            Duration::from_nanos(now.saturating_sub(t))
        });
        let t = trim_decision(&self.basis, residual, self.deadband, since,
                              self.min_trim_interval);
        if t != Trim::None {
            self.last_trim = Some(now);
        }
        Some(t)
    }
}

/// Where a queue entry came from, which is the whole of what a rejoin needs
/// to know about it `[GDE-ECHO-510]`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Origin {
    /// Announced by the master.
    Announced,
    /// Chosen locally to keep the queue full while no announcement had
    /// arrived. Discarded the moment the master is heard from again -- it was
    /// only ever filling a gap.
    Local,
}

/// What the queue should become, and what the local Director still owes it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct QueuePlan {
    /// The queue in play order, after reconciliation.
    pub queue: Vec<(i64, Origin)>,
    /// Entries the warm Director must supply to reach the configured depth.
    ///
    /// `[GDE-ECHO-500]`'s hysteresis: the queue is **topped up** rather than
    /// allowed to drain, so there is no moment of decision and no threshold to
    /// tune. Announced entries leave from the front while local ones fill in
    /// behind, and the changeover is a blend rather than an event.
    pub want_local: usize,
    /// Locally-chosen entries dropped because the master was heard from.
    pub discarded_local: usize,
}

/// Reconcile the local queue against what the master has announced.
///
/// **Announcements win outright.** `[GDE-ECHO-510]` is explicit that when
/// contact returns the master's queue takes precedence immediately and
/// locally-chosen entries still waiting are discarded. Discarding entries that
/// are about to be re-chosen looks wasteful and is the specified behaviour:
/// they were gap-fillers, and a node rejoining the fleet should be playing the
/// fleet's programme rather than a blend of two.
///
/// With no announcement at all this keeps what is there and reports the
/// shortfall, which is the going-independent path `[GDE-ECHO-500]` -- and it
/// is the same code, not a mode.
///
/// **Status: specified, tested, and not yet wired** `[GDE-ECHO-377]`. The
/// follower as built adopts the master's queue wholesale in
/// `echo_client::adopt_queue` and never tops it up locally, so the hysteresis
/// half of `[GDE-ECHO-500]` -- the blend that keeps a node out of contact
/// from running dry -- exists here and nowhere else. Deferred, not abandoned:
/// wiring it needs the local Director in the follower's loop, which is a
/// larger change than anything the correction work wanted.
pub fn reconcile_queue(local: &[(i64, Origin)], announced: &[i64], depth: usize) -> QueuePlan {
    if announced.is_empty() {
        let kept: Vec<_> = local.to_vec();
        let want = depth.saturating_sub(kept.len());
        return QueuePlan { queue: kept, want_local: want, discarded_local: 0 };
    }
    let discarded = local.iter().filter(|(_, o)| *o == Origin::Local).count();
    let queue: Vec<_> = announced.iter().map(|id| (*id, Origin::Announced)).collect();
    let want = depth.saturating_sub(queue.len());
    QueuePlan { queue, want_local: want, discarded_local: discarded }
}

/// What to do with what is sounding when the master's programme returns.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rejoin {
    /// Let the current passage finish. The master's programme is taken up at
    /// its natural end, or at a Skip `[REQ-AUD-162]`.
    ///
    /// Cutting a passage short to rejoin would make reconnection audible for
    /// no benefit: the node was never playing anything *wrong*, only something
    /// different `[GDE-ECHO-510]`.
    PlayOut,
    /// Nothing is sounding, so join the master's passage **mid-passage**, at
    /// the offset it has already reached.
    ///
    /// This is the capability `[GDE-ECHO-330]` deferred; the rejoin case
    /// promotes it from optional to required, because by the time a node
    /// rejoins the master is always part-way through something.
    JoinMidPassage { passage_id: i64, at_ms: u64 },
}

/// Decide between playing out and joining, given what is sounding here and
/// where the master is.
/// A mid-passage join: what to open, where, and when to submit it.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MidJoin {
    pub passage_id: i64,
    /// The sample the master will have reached when this node's audio lands.
    pub start_sample: u64,
    /// When to hand sample `start_sample` to the device `[GDE-ECHO-410]`.
    pub submit_at: WallNanos,
}

/// Join a master part-way through what it is already playing.
///
/// `[GDE-ECHO-330]` deferred this and `[GDE-ECHO-510]` made it required; a
/// listener switching a speaker into follower mode makes it required again,
/// because the master is almost never at a passage boundary at the moment
/// somebody presses the button.
///
/// The master moves while this node prepares, so the target is not where it is
/// but where it **will be**: pick an instant `lead` ahead, extrapolate the
/// master's position to it, and submit so that sample lands then.
///
/// Extrapolation ignores the master's own rate error, which is a deliberate
/// omission rather than an oversight -- at the ~14 ppm measured for this fleet
/// `[LOG-P4-130]` a one-second lead accrues 14 microseconds, four orders below
/// the tens of milliseconds the anchor itself carries `[LOG-P4-010]`. A
/// correction here would be arithmetic theatre.
///
/// `None` when the anchor is from the future, which means clocks disagree and
/// no arithmetic here can fix it `[GDE-ECHO-365]`.
pub fn join_mid_passage(
    m: &AirPosition,
    node: NodeTiming,
    now: WallNanos,
    lead: Duration,
    bias_ms: i64,
) -> Option<MidJoin> {
    let target = now.checked_add(lead.as_nanos() as u64)?;
    let ahead_ns = target.checked_sub(m.at)?;
    // **Plus what this node's joins have actually been landing at**
    // `[GDE-ARC-061]`. Everything above is where the master says it will be;
    // this is the standing difference between that and where this node ends
    // up, measured rather than modelled -- 530 and 712 ms on the two joins
    // that were measured, against a 1 ms steady state. Positive means past
    // joins landed BEHIND, so aim that much further on. Saturating at zero
    // because a passage has no negative position.
    let position_ms = (m.position_ms + ahead_ns / 1_000_000)
        .saturating_add_signed(bias_ms);
    // Submitting is earlier than sounding by this node's own offset, and a
    // lead shorter than that offset cannot be met at all.
    let submit_at = target.checked_sub(node.offset().as_nanos() as u64)?;
    if submit_at < now {
        return None;
    }
    Some(MidJoin {
        passage_id: m.passage_id,
        start_sample: position_ms.saturating_mul(node.rate.max(1) as u64) / 1000,
        submit_at,
    })
}

/// What this node's joins have actually been worth, so the next one starts
/// from experience rather than from zero `[GDE-ARC-061]`.
///
/// **A join is placed from the master's reported position, and the report is
/// not the truth.** `placement()` `[GDE-ARC-058]` removed the part of the join
/// error that was calculable -- the preparation the old constant had to
/// predict -- and measurement then put what survives at **530 and 712 ms** on
/// two joins, against a steady state of 1 ms. Something systematic is left,
/// and it is not yet identified `[GDE-ARC-059]`.
///
/// This absorbs it without claiming to explain it. Each join's landing error
/// is recorded, and the next join is aimed that much further on.
///
/// **The middle, not the mean, and not the last one.** Three joins in three
/// seconds once drove `echo_prep_ms` 295 -> 162 -> 195 -> 158 -> 124, the
/// compensation thrashed by the storm it was meant to absorb
/// `[GDE-ARC-044]`. A median over a window cannot be dragged that way by one
/// bad landing; an exponential average is defined by its willingness to be.
///
/// **It is per node and it is forgotten when the master changes.** The bias
/// mixes this node's own pipeline with whatever its master's reported
/// position is worth, and only the first half travels. Followers change
/// master rarely, so relearning then is cheaper than carrying a number that
/// is quietly about somebody else.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct JoinBias {
    samples: Vec<i64>,
}

impl JoinBias {
    /// How many landings are kept.
    ///
    /// Odd, so the median is a sample rather than a mean of two, which keeps
    /// the figure one this node actually measured. Nine is about a week of a
    /// quiet follower's joins and a few minutes of a busy one's.
    pub const WINDOW: usize = 9;

    /// A landing that is not a landing. Beyond this the node was not placed
    /// badly, it was placed somewhere else entirely -- a stale schedule, a
    /// clock that had not settled -- and averaging it in would teach the next
    /// join a lie `[GOV-SRC-040]`.
    pub const CREDIBLE_MS: i64 = 5_000;

    /// Record one landing error: positive when the join left this node
    /// **behind**, the same sign as the residual it is measured from.
    ///
    /// Returns whether it was taken, so a caller can say why not.
    pub fn record(&mut self, landing_ms: i64) -> bool {
        if landing_ms.unsigned_abs() > Self::CREDIBLE_MS as u64 {
            return false;
        }
        self.samples.push(landing_ms);
        if self.samples.len() > Self::WINDOW {
            self.samples.remove(0);
        }
        true
    }

    /// What to add to the next join's target position, in ms.
    ///
    /// `0` until there is something to say. **One sample is not a
    /// distribution**, and acting on a single landing is how the old
    /// compensation came to be thrashed -- but refusing to act until the
    /// window is full would leave a fresh node uncorrected for nine joins,
    /// which on a quiet follower is weeks. Three is the smallest count with a
    /// middle that is not simply the last reading.
    pub fn correction_ms(&self) -> i64 {
        if self.samples.len() < 3 {
            return 0;
        }
        let mut v = self.samples.clone();
        v.sort_unstable();
        v[v.len() / 2]
    }

    pub fn len(&self) -> usize { self.samples.len() }
    pub fn is_empty(&self) -> bool { self.samples.is_empty() }

    /// Stored as text, because this is a handful of small integers and a
    /// settings row is text `[Settings::KEYS]`. Anything unparseable is
    /// dropped rather than defaulted to zero: a corrupt row should cost the
    /// history, not silently assert that every past join was perfect.
    pub fn encode(&self) -> String {
        self.samples.iter().map(|n| n.to_string()).collect::<Vec<_>>().join(",")
    }

    pub fn decode(s: &str) -> Self {
        let mut b = Self::default();
        for part in s.split(',').filter(|p| !p.trim().is_empty()) {
            if let Ok(n) = part.trim().parse::<i64>() {
                b.record(n);
            }
        }
        b
    }
}

/// **Status: superseded in place, kept for the decision it records**
/// `[GDE-ECHO-377]`. The live follower asks a different and better question --
/// `coming_here`, "is it playing this *or coming to it*" `[GDE-ECHO-343]` --
/// because `current` is the audible passage and lags admission by a ring, so
/// the `Some(id) if id == m.passage_id` test here sees a mismatch at every
/// ordinary transition. What survives is the `PlayOut` rule: a node that is
/// playing something else is never cut short. Nothing calls this outside its
/// own tests; a caller wanting the rejoin decision should ask `coming_here`
/// and reach for `join_mid_passage` from there.
pub fn rejoin_action(sounding: Option<i64>, master: Option<&AirPosition>) -> Option<Rejoin> {
    let m = master?;
    match sounding {
        // Already on the master's passage: nothing to rejoin to.
        Some(id) if id == m.passage_id => None,
        Some(_) => Some(Rejoin::PlayOut),
        None => Some(Rejoin::JoinMidPassage { passage_id: m.passage_id, at_ms: m.position_ms }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BOSE: NodeTiming = NodeTiming { presentation_offset_frames: 2043, rate: 44100 };
    const LEMPIPI: NodeTiming = NodeTiming { presentation_offset_frames: 15676, rate: 44100 };

    const SEC: u64 = 1_000_000_000;

    #[test]
    fn offsets_match_the_measured_nodes() {
        // `[LOG-CPAL-060]`: 46.3 ms and 355 ms.
        assert_eq!(BOSE.offset().as_millis(), 46);
        assert_eq!(LEMPIPI.offset().as_millis(), 355);
    }

    #[test]
    fn the_node_with_the_larger_offset_submits_earlier() {
        // The whole point of `[GDE-ECHO-410]`. A backward-only design would
        // have had lempi02w submitting after bose and never catching up.
        let sched = Schedule { passage_id: 7, start_sample: 0, sound_at: 100 * SEC, rate: 44100 };
        let now = 80 * SEC;
        let b = submit_at(&sched, BOSE, now).unwrap();
        let v = submit_at(&sched, LEMPIPI, now).unwrap();
        assert!(v < b, "lempi02w must submit before bose");
        assert_eq!(b - v, (LEMPIPI.offset() - BOSE.offset()).as_nanos() as u64);
        // 355 - 46 = 309 ms, the figure the design turns on.
        assert_eq!((b - v) / 1_000_000, 309);
    }

    #[test]
    fn the_master_applies_the_same_arithmetic_to_itself() {
        // Symmetry: no node is the reference `[GDE-ECHO-410]`.
        let sched = Schedule { passage_id: 1, start_sample: 0, sound_at: 50 * SEC, rate: 44100 };
        let submit = submit_at(&sched, BOSE, 0).unwrap();
        assert_eq!(submit, 50 * SEC - BOSE.offset().as_nanos() as u64);
    }

    #[test]
    fn a_schedule_already_past_is_refused_not_rounded_forward() {
        // 100 ms of lead against a 355 ms offset: submission was due 255 ms
        // ago. Returning a past instant would read like an instruction.
        let sched = Schedule { passage_id: 2, start_sample: 0, sound_at: 10 * SEC + 100_000_000, rate: 44100 };
        assert!(submit_at(&sched, LEMPIPI, 10 * SEC).is_none());
        // The same schedule is comfortably reachable by the low-offset node.
        assert!(submit_at(&sched, BOSE, 10 * SEC).is_some());
    }

    #[test]
    fn fifteen_seconds_of_lead_clears_every_measured_offset() {
        // `[GDE-ECHO-310]`'s claim, checked rather than asserted.
        let now = 1000 * SEC;
        let sched = Schedule { passage_id: 3, start_sample: 0, sound_at: now + 15 * SEC, rate: 44100 };
        for node in [BOSE, LEMPIPI] {
            assert!(submit_at(&sched, node, now).is_some());
        }
    }

    /// The sign a test catches and listening does not, on the arithmetic the
    /// loop actually runs `[GDE-ECHO-377]`.
    #[test]
    fn residual_sign_says_late_is_positive() {
        // The master heard its own sample 44100 -- one second in -- at t=5 s.
        let a = DriftAnchor {
            passage_id: 4, sample: 44100, heard_at: 5 * SEC, rate: 44100, ppm: None,
        };
        let at_the_same_place = |at: WallNanos| AirPosition {
            passage_id: 4, position_ms: 1_000, at,
        };
        let residual = |at| local_at_sample(&at_the_same_place(at), &a) - a.heard_at as i64;
        assert!(residual(5 * SEC + 1_000_000) > 0, "later than master is positive");
        assert!(residual(5 * SEC - 1_000_000) < 0, "earlier than master is negative");
        assert_eq!(residual(5 * SEC), 0);
    }

    // `[REQ-AUD-160]`'s ring is ~15 s at 44100.
    const RING: u64 = 44100 * 15;

    #[test]
    fn a_start_instant_is_due_once_and_stale_soon_after() {
        let at = 100 * SEC;
        let lim = Duration::from_millis(100);
        let far = Duration::from_secs(60);
        assert_eq!(start_verdict(at, at - 1, lim, far), StartVerdict::Wait);
        assert_eq!(start_verdict(at, at, lim, far), StartVerdict::Fire, "exactly due fires");
        assert_eq!(start_verdict(at, at + 99_000_000, lim, far), StartVerdict::Fire);
        // A start days out is an undisciplined clock, not a patient node.
        // The node is two days BEHIND, so the schedule reads two days out.
        assert!(matches!(start_verdict(at + 2 * 86_400 * SEC, at, lim, far),
                         StartVerdict::TooFar { .. }));
        // One tick's jitter is fine; a quarter second is not a join any more.
        match start_verdict(at, at + 250_000_000, lim, far) {
            StartVerdict::TooLate { by } => assert_eq!(by, Duration::from_millis(250)),
            other => panic!("expected TooLate, got {other:?}"),
        }
    }

    /// The trap the type exists to stop: admitting at `submit_at` would put
    /// sample 0 a full ring late. Depth, not admission time, is the knob.
    #[test]
    fn placement_is_a_depth_not_an_admission_time() {
        let now = 100 * SEC;
        // A master with `bose`'s pipeline: 15.0 s of ring plus 46 ms of device.
        let s = schedule_for_admission(7, 0, RING, BOSE.presentation_offset_frames, 44100, now);
        // The follower is the same node shape, so it runs a full ring.
        assert_eq!(placement(&s, BOSE, RING, now), Placement::Depth(RING));
        // And `submit_at` is a *device* instant, one device delay before the
        // sound -- not the moment to admit. The two differ by the whole ring.
        let submit = submit_at(&s, BOSE, now).unwrap();
        assert_eq!(submit - now, frames_to_duration(RING, 44100).as_nanos() as u64);
    }

    /// `[LOG-ECHO-030]`: a larger device delay is absorbed by running shallower.
    #[test]
    fn a_slower_device_runs_a_shallower_ring() {
        let now = 100 * SEC;
        let s = schedule_for_admission(7, 0, RING, BOSE.presentation_offset_frames, 44100, now);
        // lempi02w's 355 ms of A2DP comes out of its ring, exactly.
        let want = RING + BOSE.presentation_offset_frames - LEMPIPI.presentation_offset_frames;
        assert_eq!(placement(&s, LEMPIPI, RING, now), Placement::Depth(want));
        assert_eq!(RING - want, 13633, "lempi02w runs 309 ms shallower than bose");
    }

    /// A total computed off a full ring by the *slower* node is unreachable for
    /// the faster one -- and must say so rather than clamp and play early
    /// forever. This is a defect in the announced total, not in who announces
    /// `[GDE-ECHO-315]`; the same pair works at a total the fleet can meet, as
    /// the next test shows.
    #[test]
    fn a_total_announced_off_a_full_ring_can_be_unreachable() {
        let now = 100 * SEC;
        let s = schedule_for_admission(7, 0, RING, LEMPIPI.presentation_offset_frames, 44100, now);
        match placement(&s, BOSE, RING, now) {
            Placement::TooShallow { short_by_frames } => {
                assert_eq!(short_by_frames,
                    LEMPIPI.presentation_offset_frames - BOSE.presentation_offset_frames);
            }
            other => panic!("expected TooShallow, got {other:?}"),
        }
    }

    fn estimator() -> RateEstimate {
        RateEstimate::new(Duration::from_secs(3600), Duration::from_secs(900), 200)
    }

    /// Two samples a second for `mins` minutes, drifting at `ppm`, with
    /// `jitter_ms` of scatter drawn from a fixed pseudo-random sequence.
    ///
    /// Pseudo-random rather than patterned, and that is the point: *any*
    /// regular pattern correlates with evenly spaced time and tilts the fit.
    /// A square wave alternating every sample biases this by exactly 1 ppm,
    /// and a sawtooth of period seven by another, both of which are facts
    /// about the noise rather than about the estimator.
    fn feed(e: &mut RateEstimate, mins: u64, ppm: f64, jitter_ms: i64) {
        let mut seed = 0x2545_F491_4F6C_DD1Du64;
        for i in 0..(mins * 120) {
            let t = 10 * SEC + i * 500_000_000;
            let drift = (i as f64 * 0.5 * ppm * 1e-6 * 1e9) as i64;
            seed ^= seed << 13;
            seed ^= seed >> 7;
            seed ^= seed << 17;
            let jitter = if jitter_ms == 0 {
                0
            } else {
                (seed % (2 * jitter_ms as u64 + 1)) as i64 - jitter_ms
            } * 1_000_000;
            e.push(t, drift + jitter);
        }
    }

    /// A clean ramp is read back as the rate that made it.
    #[test]
    fn a_steady_drift_is_measured_as_its_own_ppm() {
        let mut e = estimator();
        // +13.92 ppm, the figure measured for this fleet `[LOG-P4-130]`, with
        // no scatter at all: the fit should return it almost exactly.
        feed(&mut e, 20, 13.92, 0);
        let ppm = e.ppm().expect("twenty minutes is plenty");
        assert!((ppm - 13.92).abs() < 0.01, "got {ppm}");
    }

    /// The property the whole design leans on: ring jitter is zero-mean, so a
    /// fit sees through it where a single reading cannot `[LOG-P4-010]`.
    #[test]
    fn scatter_far_larger_than_the_drift_still_yields_the_drift() {
        let mut e = estimator();
        // +14 ppm buried under +/-30 ms of scatter -- the residual swings a
        // thousand times further than it drifts. An hour resolves it to a few
        // tenths of a ppm; ten minutes would not resolve it at all.
        feed(&mut e, 60, 14.0, 30);
        let ppm = e.ppm().unwrap();
        assert!((ppm - 14.0).abs() < 1.0, "got {ppm} from +/-30 ms of scatter");
    }

    /// An estimate from too little data is a different quantity, not a rough
    /// one `[GDE-ECHO-350]`.
    #[test]
    fn a_short_or_sparse_window_yields_nothing() {
        let mut e = estimator();
        feed(&mut e, 1, 14.0, 0);
        assert_eq!(e.ppm(), None, "one minute is not an estimate");
        let mut e = estimator();
        // Enough samples, but crammed into ninety seconds.
        for i in 0..400u64 {
            e.push(10 * SEC + i * 225_000_000, 0);
        }
        assert_eq!(e.ppm(), None, "ninety seconds is not an estimate either");
    }

    /// A step in the middle reads as a colossal slope, which is why anything
    /// that moves the residual must clear the window.
    #[test]
    fn clearing_is_what_keeps_a_correction_from_reading_as_drift() {
        let mut e = estimator();
        for i in 0..3600u64 {
            let t = 10 * SEC + i * 500_000_000;
            // A 40 ms offset correction lands half way through.
            e.push(t, if i < 1800 { 0 } else { 40_000_000 });
        }
        // The true rate here is zero. A 40 ms step across a half-hour window
        // fits to about 33 ppm -- larger than the real drift of this fleet,
        // and in whichever direction the correction went.
        let invented = e.ppm().unwrap();
        assert!(invented > 20.0, "an uncleared step invents a rate: got {invented}");
        e.clear();
        assert_eq!(e.ppm(), None, "and clearing leaves nothing to act on");
    }

    /// The filter must be far steadier than any single reading, which is what
    /// makes a sample-resolution endgame measurable at all.
    #[test]
    fn a_median_is_steadier_than_any_one_reading() {
        let mut f = ResidualFilter::new(Duration::from_secs(120), 60);
        assert_eq!(f.median(), None, "an empty window answers nothing");
        let mut seed = 0x9E37_79B9_7F4A_7C15u64;
        let mut raw_min = i64::MAX;
        let mut raw_max = i64::MIN;
        for i in 0..240u64 {
            seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
            // One-sided: the ring can fall short by a lot and run over by
            // little `[LOG-ECHO-020]`.
            let r = 12_000_000 + (seed % 30_000_000) as i64;
            raw_min = raw_min.min(r);
            raw_max = raw_max.max(r);
            f.push(10 * SEC + i * 500_000_000, r);
        }
        let m = f.median().unwrap();
        assert!(raw_max - raw_min > 25_000_000, "the raw series really does scatter");
        // Steady is the property, not accurate: a one-sided noise floor biases
        // any estimator, which is why `[GDE-ECHO-345]` wants a better anchor
        // rather than a cleverer filter.
        let mut g = ResidualFilter::new(Duration::from_secs(120), 60);
        for i in 240..480u64 {
            seed ^= seed << 13; seed ^= seed >> 7; seed ^= seed << 17;
            g.push(10 * SEC + i * 500_000_000, 12_000_000 + (seed % 30_000_000) as i64);
        }
        let m2 = g.median().unwrap();
        assert!((m - m2).abs() < 4_000_000,
                "two independent windows must agree far closer than one reading scatters");
    }

    /// A step inside the window describes neither side of it.
    #[test]
    fn the_filter_is_cleared_by_anything_that_moves_the_quantity() {
        let mut f = ResidualFilter::new(Duration::from_secs(120), 60);
        for i in 0..240u64 {
            f.push(10 * SEC + i * 500_000_000, if i < 120 { 0 } else { 40_000_000 });
        }
        assert!(f.median().is_some(), "a straddled step still answers, wrongly");
        f.clear();
        assert_eq!(f.median(), None, "which is why anything that steps it clears it");
    }

    /// `[GDE-ECHO-346]`: the loop must converge on the drift, not on half of
    /// it. Written to fail against treating a fitted slope as an absolute.
    #[test]
    fn the_rate_loop_converges_on_the_drift_not_half_of_it() {
        const R: f64 = 13.92;                 // the measured pair `[LOG-P4-130]`
        let mut applied = 0.0;
        for _ in 0..6 {
            // What a fit sees is whatever the trim has not already removed.
            let fitted = R - applied;
            applied = next_trim_ppm(applied, fitted);
        }
        assert!((applied - R).abs() < 1e-9, "settled at {applied}, not {R}");

        // The same loop with the fit applied as an absolute, which is what
        // shipped: it reaches half and stays there.
        let mut wrong = 0.0;
        for _ in 0..6 {
            wrong = R - wrong;
        }
        assert!((wrong - R / 2.0).abs() < 1e-9 || (wrong - R).abs() > 1.0,
                "an absolute command cannot reach the drift; got {wrong}");
    }

    /// `[GDE-ECHO-340]`'s own arithmetic, checked.
    #[test]
    fn the_trim_interval_matches_the_rate_it_cancels() {
        let d = trim_interval(13.92, 44100, 0.5).unwrap();
        assert!((d.as_secs_f64() - 1.629).abs() < 0.01, "got {d:?}");
        // Direction does not change how often, only which way.
        assert_eq!(trim_interval(-13.92, 44100, 0.5), Some(d));
        // Below the floor there is nothing worth correcting.
        assert_eq!(trim_interval(0.2, 44100, 0.5), None);
        assert_eq!(trim_interval(f64::NAN, 44100, 0.5), None);
    }

    /// The defect this replaced: two anchors are never about the same sample,
    /// and subtracting their timestamps measures transport delay, not
    /// alignment.
    #[test]
    fn a_residual_compares_the_same_sample_not_the_same_moment() {
        // Master: heard sample 0 (0 ms) at t=10 s. Its snapshot then crossed a
        // network, and this node reads its own position 250 ms later -- by
        // which time it has itself played 250 ms. Perfectly in sync.
        let m = DriftAnchor { passage_id: 3, sample: 0, heard_at: 10 * SEC,
                              rate: 44100, ppm: None };
        let local = AirPosition { passage_id: 3, position_ms: 250, at: 10 * SEC + 250_000_000 };
        assert_eq!(local_at_sample(&local, &m) - m.heard_at as i64, 0,
                   "a quarter second of transport is not a quarter second of error");
        // The naive subtraction -- two timestamps, written out here rather
        // than kept as a function somebody could reach for `[GDE-ECHO-377]` --
        // would have called that 250 ms late.
        assert_eq!(local.at as i64 - m.heard_at as i64, 250_000_000);

        // Genuinely 5 ms late: same position, reached 5 ms later.
        let late = AirPosition { passage_id: 3, position_ms: 250,
                                 at: 10 * SEC + 255_000_000 };
        assert_eq!(local_at_sample(&late, &m) - m.heard_at as i64, 5_000_000);
    }

    fn mclock() -> MasterClock {
        MasterClock::new(Duration::from_secs(60), Duration::from_secs(1))
    }

    /// The whole point: a node days out schedules correctly anyway.
    #[test]
    fn a_follower_two_days_behind_still_computes_the_right_interval() {
        let mut c = mclock();
        // A real epoch, because two days must fit underneath it.
        const NOW: u64 = 1_789_000_000 * SEC;
        let two_days = 2 * 86_400 * SEC;
        let own = NOW - two_days;
        c.observe(NOW, own);
        assert_eq!(c.offset(), Some(two_days as i64));
        // A master instant one second out lands one second out locally, which
        // is the only thing scheduling actually needs.
        assert_eq!(c.to_local(NOW + SEC).unwrap() - own, SEC);
        assert_eq!(c.now(own), Some(NOW));
    }

    /// Transport delay only ever makes a reading look EARLY, so the largest
    /// difference is the least contaminated one.
    #[test]
    fn the_offset_takes_the_least_delayed_reading() {
        let mut c = mclock();
        // Same true offset of zero, seen through 5, 40 and 12 ms of transport.
        c.observe(10 * SEC, 10 * SEC + 5_000_000);
        c.observe(11 * SEC, 11 * SEC + 40_000_000);
        c.observe(12 * SEC, 12 * SEC + 12_000_000);
        assert_eq!(c.offset(), Some(-5_000_000), "the 5 ms reading, not the average");
    }

    /// A step is reported once, and the window starts again from it.
    ///
    /// **It has to be confirmed first** `[GDE-ARC-049]`. A reading below the
    /// established offset is exactly what a delayed snapshot looks like, so
    /// one of them is not evidence; a shift that persists is.
    #[test]
    fn a_step_is_reported_and_clears_what_came_before() {
        let mut c = mclock();
        assert!(!c.observe(10 * SEC, 10 * SEC), "the first reading is not a step");
        assert!(!c.observe(11 * SEC, 11 * SEC), "nor is an agreeing one");
        // NTP steps this node forward by an hour. Every reading from here on
        // carries the shift, which is what tells it apart from a late packet.
        let late = 3600 * SEC;
        assert!(!c.observe(12 * SEC, 12 * SEC + late), "one reading is not yet evidence");
        assert!(!c.observe(13 * SEC, 13 * SEC + late), "nor two");
        assert!(c.observe(14 * SEC, 14 * SEC + late), "three in a row is a step");
        assert_eq!(c.offset(), Some(-(late as i64)), "and only the new reading survives");
        assert!(!c.observe(15 * SEC, 15 * SEC + late), "settled again");
    }

    /// **One late snapshot is transport, not a clock** `[GDE-ARC-049]`.
    ///
    /// `heard_at` is stamped as the master builds the snapshot, so a reading
    /// delayed on the wire looks exactly like the master's clock jumping
    /// backwards by the delay. Delay is one-sided -- which is why `offset()`
    /// takes the maximum -- but the step test compared a single new reading
    /// against that maximum, so any snapshot over a second late was read as a
    /// step. Measured on `lp3-wifi` 2026-09-19: four "a clock stepped" lines
    /// in thirty minutes while chrony's own `Last offset` was 465 us and it
    /// had stepped nothing. Each false positive cleared the rate window and
    /// zeroed the trim, which is one of the reasons the rate loop had never
    /// once run `[GDE-ARC-045]`.
    #[test]
    fn one_late_snapshot_is_delay_and_not_a_clock_step() {
        let mut c = mclock();
        c.observe(10 * SEC, 10 * SEC);
        c.observe(11 * SEC, 11 * SEC);
        // A snapshot that took two seconds to reach this node.
        assert!(!c.observe(12 * SEC, 12 * SEC + 2 * SEC),
                "a late snapshot must not read as a clock step");
        // And it does not disturb the estimate either: the maximum is the
        // least-delayed reading, and a low outlier is not it.
        assert_eq!(c.offset(), Some(0), "the estimate survives a late packet");
        assert!(!c.observe(13 * SEC, 13 * SEC), "nor does recovering from one");
    }

    /// A reading ABOVE the established offset cannot be delay -- the wire
    /// cannot deliver a snapshot early -- so that needs no confirming
    /// `[GDE-ARC-049]`.
    #[test]
    fn a_jump_the_wire_cannot_explain_is_a_step_at_once() {
        let mut c = mclock();
        c.observe(10 * SEC, 10 * SEC);
        c.observe(11 * SEC, 11 * SEC);
        assert!(c.observe(12 * SEC + 3600 * SEC, 12 * SEC),
                "no transport delay makes a reading arrive an hour early");
    }

    /// `[GDE-ECHO-365]`: a node two days behind is not following anything.
    #[test]
    fn clocks_days_apart_do_not_agree() {
        let tol = Duration::from_secs(30);
        assert!(clocks_agree(100 * SEC, 100 * SEC, tol), "identical agree");
        assert!(clocks_agree(100 * SEC, 110 * SEC, tol), "ten seconds is transport");
        assert!(!clocks_agree(100 * SEC, 100 * SEC + 2 * 86_400 * SEC, tol),
                "two days is a clock that has not been stepped yet");
        assert!(!clocks_agree(100 * SEC + 2 * 86_400 * SEC, 100 * SEC, tol),
                "and it is symmetric");
    }

    /// The loop must shrink the error it is given, whatever it is given
    /// `[GDE-ECHO-341]`. A node with a systematic join bias converges only if
    /// every correction reduces the offset; escalating never does.
    ///
    /// It used to take two transitions -- ten minutes on this library -- to
    /// walk 886 ms in at half a second a bite. `[GDE-ARC-051]` removed the
    /// bite, so the same offset is one transition and the loop is where it
    /// was always trying to get to: **one correction, then hold.**
    #[test]
    fn a_large_offset_is_taken_in_one_transition() {
        let dead = Duration::from_millis(40);
        let far = Duration::from_secs(5);
        // The 886 ms actually observed.
        let mut r: i64 = 886_000_000;
        let mut steps = 0;
        while let OffsetFix::ShiftStart(ms) = offset_fix(r, dead, far) {
            assert!(ms > 0, "a late node always starts earlier");
            r -= ms * 1_000_000;
            steps += 1;
            assert!(steps < 10, "it must converge, not circle");
        }
        assert_eq!(offset_fix(r, dead, far), OffsetFix::Hold);
        assert_eq!(steps, 1, "the transition places the whole of it");
        assert_eq!(r, 0, "and places it exactly");
    }

    /// `[GDE-ARC-051]`: **no cap.** Whatever the residual is, the transition
    /// is asked for the whole of it.
    ///
    /// The cap was sized against a crossfade -- half a second of one made
    /// longer or shorter is not something a listener can point at. That
    /// reasoning was about the *overlap*, and the overlap is no longer the
    /// only actuator at the boundary: an earlier start spends whole mix
    /// blocks of admission and cuts the remainder from the head, a later one
    /// is silence, and neither has a natural ceiling at 500 ms
    /// `[GDE-ARC-052]`. What the cap bought was a slow loop.
    #[test]
    fn the_whole_offset_is_asked_of_one_transition() {
        let dead = Duration::from_millis(8);
        // **The threshold production actually uses.** This test used 30 s,
        // which let it assert that a twelve-second residual is "one
        // transition's work" -- true of no node that has ever run, because
        // `offset_fix` returns `Rejoin` above this and the boundary is never
        // asked for more. Substituting a bound the system does not have is
        // how a test comes to describe a different program.
        let far = Duration::from_millis(1_500);
        assert_eq!(offset_fix(886_000_000, dead, far), OffsetFix::ShiftStart(886));
        assert_eq!(offset_fix(-886_000_000, dead, far), OffsetFix::ShiftStart(-886),
                   "and symmetric, now that the later direction has an actuator");
        // What "no bite" really bought: the whole band in one transition,
        // where the 500 ms cap took three. The band itself is unchanged.
        assert_eq!(offset_fix(1_400_000_000, dead, far), OffsetFix::ShiftStart(1_400));
        assert_eq!(offset_fix(12_000_000_000, dead, far), OffsetFix::Rejoin,
                   "twelve seconds is not a boundary correction at all");
    }

    /// `[GDE-ARC-061]`: the middle of what this node has actually measured,
    /// and nothing at all until there is a middle to take.
    #[test]
    fn a_join_bias_is_the_middle_of_experience_not_the_last_surprise() {
        let mut b = JoinBias::default();
        assert_eq!(b.correction_ms(), 0, "a node with no history corrects nothing");
        b.record(700);
        assert_eq!(b.correction_ms(), 0, "one landing is not a distribution");
        b.record(690);
        assert_eq!(b.correction_ms(), 0, "nor two");
        b.record(710);
        assert_eq!(b.correction_ms(), 700, "three has a middle");

        // **The storm test** `[GDE-ARC-044]`. One wild landing among steady
        // ones must move the figure a little, not become it -- the failure
        // that made `echo_prep_ms` useless was a compensation defined by its
        // most recent surprise.
        b.record(-400);
        assert_eq!(b.correction_ms(), 700, "a single outlier must not take over");
        let mean = (700 + 690 + 710 - 400) / 4;
        assert_ne!(b.correction_ms(), mean, "and a mean would have let it");

        // Beyond credible is not a landing at all.
        assert!(!b.record(9_000), "a five-second miss is a different fault");
        assert_eq!(b.correction_ms(), 700, "and it must not reach the figure");

        // The window slides, so a node that genuinely moves is followed.
        let mut c = JoinBias::default();
        for _ in 0..JoinBias::WINDOW { c.record(700); }
        assert_eq!(c.len(), JoinBias::WINDOW);
        for _ in 0..JoinBias::WINDOW { c.record(100); }
        assert_eq!(c.len(), JoinBias::WINDOW, "it is bounded");
        assert_eq!(c.correction_ms(), 100, "and it followed the move");
    }

    /// It has to survive a restart or it is `echo_prep_ms` again: that reset
    /// to its cold guess on every boot and never converged `[GDE-ARC-058]`.
    #[test]
    fn a_join_bias_survives_being_written_down() {
        let mut b = JoinBias::default();
        for n in [700, 690, 710, -20] { b.record(n); }
        let back = JoinBias::decode(&b.encode());
        assert_eq!(back, b, "what was written is not what came back");
        assert_eq!(back.correction_ms(), b.correction_ms());

        // A corrupt row costs the history, not the correctness of what is
        // left, and never asserts that past joins were perfect.
        let partial = JoinBias::decode("700, rubbish ,690,,710");
        assert_eq!(partial.len(), 3, "the unreadable part is dropped, not zeroed");
        assert_eq!(partial.correction_ms(), 700);
        assert_eq!(JoinBias::decode("").correction_ms(), 0);
        assert!(JoinBias::decode("").is_empty());
    }

    /// `[GDE-ECHO-340]`: a transition absorbs an offset either way, and far is
    /// a rejoin.
    #[test]
    fn an_offset_is_hidden_when_it_can_be_and_rejoined_when_it_cannot() {
        let dead = Duration::from_millis(5);
        let far = Duration::from_secs(5);
        assert_eq!(offset_fix(4_000_000, dead, far), OffsetFix::Hold);
        assert_eq!(offset_fix(-4_000_000, dead, far), OffsetFix::Hold);
        // Late: start the next passage earlier, overlapping a little more.
        assert_eq!(offset_fix(20_000_000, dead, far), OffsetFix::ShiftStart(20));
        // Early: start it later, overlapping a little less. Symmetric.
        assert_eq!(offset_fix(-20_000_000, dead, far), OffsetFix::ShiftStart(-20));
        // Formerly three transitions' worth, now one.
        assert_eq!(offset_fix(400_000_000, dead, far), OffsetFix::ShiftStart(400));
        assert_eq!(offset_fix(-400_000_000, dead, far), OffsetFix::ShiftStart(-400));
        // Only an absurd one is a rejoin.
        assert_eq!(offset_fix(9_000_000_000, dead, far), OffsetFix::Rejoin);
    }

    /// `[GDE-ARC-061]`: a join aims where the master says it will be, **plus
    /// what this node's joins have actually been landing short by**.
    #[test]
    fn a_join_aims_past_the_master_by_what_past_joins_missed_by() {
        let now = 100 * SEC;
        let m = AirPosition { passage_id: 9, position_ms: 30_000, at: now };
        let lead = Duration::from_secs(1);
        let plain = join_mid_passage(&m, BOSE, now, lead, 0).unwrap();

        // Past joins landed 700 ms behind, so start 700 ms further on.
        let learned = join_mid_passage(&m, BOSE, now, lead, 700).unwrap();
        assert_eq!(learned.start_sample - plain.start_sample, 700 * 44_100 / 1000,
                   "the bias must move the content position, not the timing");
        assert_eq!(learned.submit_at, plain.submit_at,
                   "and only the content position: when to submit is unchanged");

        // It works the other way too -- a node that has been landing early
        // starts further back.
        let early = join_mid_passage(&m, BOSE, now, lead, -700).unwrap();
        assert_eq!(plain.start_sample - early.start_sample, 700 * 44_100 / 1000);

        // A bias deeper than the passage's own start cannot make the position
        // negative; it saturates rather than wrapping into an enormous sample.
        let near_start = AirPosition { passage_id: 9, position_ms: 10, at: now };
        let j = join_mid_passage(&near_start, BOSE, now, lead, -5_000).unwrap();
        assert_eq!(j.start_sample, 0, "saturated at the start of the passage");
    }

    /// The master moves while this node prepares, so a mid-passage join aims
    /// where it WILL be. One second of lead is one second further in.
    #[test]
    fn a_mid_passage_join_aims_ahead_of_where_the_master_is() {
        let now = 100 * SEC;
        // Master heard 30.000 s into passage 9, as of now.
        let m = AirPosition { passage_id: 9, position_ms: 30_000, at: now };
        let j = join_mid_passage(&m, BOSE, now, Duration::from_secs(1), 0).unwrap();
        assert_eq!(j.passage_id, 9);
        assert_eq!(j.start_sample, 31_000 * 44100 / 1000, "31.000 s, not 30.000");
        // Submitting is earlier than sounding by this node's own offset.
        assert_eq!(now + SEC - j.submit_at, BOSE.offset().as_nanos() as u64);
    }

    /// A node with a large offset needs a lead longer than that offset; there
    /// is no arithmetic that recovers a shorter one `[GDE-ECHO-410]`.
    #[test]
    fn a_lead_shorter_than_the_offset_is_refused() {
        let now = 100 * SEC;
        let m = AirPosition { passage_id: 9, position_ms: 30_000, at: now };
        // lempi02w's 355 ms against a 100 ms lead.
        assert_eq!(join_mid_passage(&m, LEMPIPI, now, Duration::from_millis(100), 0), None);
        // The same node with room to work in is fine.
        assert!(join_mid_passage(&m, LEMPIPI, now, Duration::from_millis(500), 0).is_some());
    }

    /// A stale anchor still extrapolates: that is the whole point of carrying
    /// `at` beside the position rather than a bare position.
    #[test]
    fn an_anchor_from_a_moment_ago_extrapolates_from_its_own_timestamp() {
        let now = 100 * SEC;
        let m = AirPosition { passage_id: 9, position_ms: 30_000, at: now - 2 * SEC };
        let j = join_mid_passage(&m, BOSE, now, Duration::from_secs(1), 0).unwrap();
        assert_eq!(j.start_sample, 33_000 * 44100 / 1000, "2 s stale plus 1 s lead");
    }

    /// `[GDE-ECHO-315]`: the slower node announcing is fine, and is in fact the
    /// preferred arrangement, so long as it announces the fleet's total rather
    /// than its own full ring. `lempi02w` then runs the shallow ring it would
    /// have run anyway, and `bose` runs full -- the same two depths as when
    /// `bose` announces. Who announces does not enter the arithmetic.
    #[test]
    fn the_slower_node_may_announce_at_the_fleets_total() {
        let now = 100 * SEC;
        // The fleet's cap: capacity + the SMALLEST device delay. lempi02w holds
        // the larger, so it announces off a ring short by the difference.
        let lempi02w_depth = RING + BOSE.presentation_offset_frames
            - LEMPIPI.presentation_offset_frames;
        let s = schedule_for_admission(
            7, 0, lempi02w_depth, LEMPIPI.presentation_offset_frames, 44100, now);
        assert_eq!(placement(&s, BOSE, RING, now), Placement::Depth(RING),
            "the smallest-delay node runs the fullest ring, whoever announced");
        assert_eq!(placement(&s, LEMPIPI, RING, now), Placement::Depth(lempi02w_depth));
    }

    /// Network lateness spends the ring, and there is ~15 s of it to spend --
    /// which is the margin `[GDE-ECHO-310]` claims, correctly, once it is
    /// measured against latency rather than against the offset.
    #[test]
    fn lateness_spends_depth_and_there_is_plenty() {
        let emitted = 100 * SEC;
        let s = schedule_for_admission(7, 0, RING, BOSE.presentation_offset_frames, 44100, emitted);
        // Five seconds late: still fine, just a shallower ring.
        assert_eq!(placement(&s, BOSE, RING, emitted + 5 * SEC), Placement::Depth(RING - 44100 * 5));
        // Past the sound itself: named, not clamped.
        assert!(matches!(placement(&s, BOSE, RING, emitted + 20 * SEC), Placement::Late { .. }));
    }

    #[test]
    fn the_air_lags_the_mixer_by_the_ring_and_the_device() {
        let a = air_position(9, 30_000, RING, BOSE.presentation_offset_frames, 44100, 7 * SEC);
        // 30 s mixed, less 15 s of ring and 46 ms of device.
        assert_eq!(a.position_ms, 30_000 - 15_000 - 46);
        assert_eq!(a.at, 7 * SEC);
    }

    #[test]
    fn the_device_delay_is_what_separates_air_from_audible_ms() {
        // The engine's audible_ms subtracts the ring only. On bose that is a
        // 46 ms difference and on lempi02w 355 -- one is a rounding error in a
        // display, the other is not `[LOG-CPAL-060]`.
        let b = air_position(1, 60_000, RING, BOSE.presentation_offset_frames, 44100, 0);
        let v = air_position(1, 60_000, RING, LEMPIPI.presentation_offset_frames, 44100, 0);
        assert_eq!(b.position_ms - v.position_ms, 355 - 46);
    }

    #[test]
    fn early_in_a_passage_nothing_is_audible_yet_rather_than_negative() {
        // 2 s mixed against 15 s of ring: saturates, does not wrap.
        let a = air_position(2, 2_000, RING, LEMPIPI.presentation_offset_frames, 44100, 0);
        assert_eq!(a.position_ms, 0);
    }

    #[test]
    fn an_admission_is_announced_about_a_ring_ahead_of_being_heard() {
        let now = 500 * SEC;
        let s = schedule_for_admission(5, 0, RING, BOSE.presentation_offset_frames, 44100, now);
        let lead_ms = (s.sound_at - now) / 1_000_000;
        assert!((15_000..=15_100).contains(&lead_ms), "lead was {lead_ms} ms");
        // And that lead is what makes every node's offset compensable.
        for node in [BOSE, LEMPIPI] {
            assert!(submit_at(&s, node, now).is_some());
        }
    }

    #[test]
    fn an_anchor_round_trips_through_the_sample_number() {
        let a = air_position(3, 10_000, 0, 0, 44100, 12 * SEC);
        let anchor = a.anchor(44100, Some(-2.09));
        assert_eq!(anchor.sample, 441_000);           // 10 s at 44100
        assert_eq!(anchor.heard_at, 12 * SEC);
        // And a node reading the same position at the same moment is exactly
        // level with it.
        assert_eq!(local_at_sample(&a, &anchor) - anchor.heard_at as i64, 0);
    }

    #[test]
    fn two_nodes_agreeing_on_the_air_have_no_residual() {
        // The property echo is trying to hold: different offsets, same sound.
        let sched = Schedule { passage_id: 8, start_sample: 0, sound_at: 900 * SEC, rate: 44100 };
        let now = 880 * SEC;
        let b = submit_at(&sched, BOSE, now).unwrap() + BOSE.offset().as_nanos() as u64;
        let v = submit_at(&sched, LEMPIPI, now).unwrap() + LEMPIPI.offset().as_nanos() as u64;
        assert_eq!(b, v, "submit + own offset must land on the same instant");
        assert_eq!(b, sched.sound_at);
    }

    fn follower(t: NodeTiming) -> Follower {
        Follower::new(t, Duration::from_micros(500), Duration::from_secs(1))
    }

    fn state_with(sched: Option<Schedule>, anchor: Option<DriftAnchor>) -> EchoState {
        EchoState { anchor, schedule: sched, voided_by: None }
    }

    /// The case the dedup key was changed for `[GDE-ECHO-325]`. A seek
    /// re-announces the passage already playing, so a follower keyed on
    /// `passage_id` alone would call it Idle and never move -- swallowing
    /// precisely the message the seek exists to deliver.
    #[test]
    fn a_seek_re_announces_the_playing_passage_and_is_acted_on() {
        let mut f = follower(BOSE);
        let playing = Schedule { passage_id: 4, start_sample: 0, sound_at: 100 * SEC, rate: 44100 };
        assert!(matches!(f.on_state(&state_with(Some(playing), None), 80 * SEC),
                         Follow::StartAt { passage_id: 4, start_sample: 0, .. }));
        assert_eq!(f.on_state(&state_with(Some(playing), None), 80 * SEC), Follow::Idle);

        // Same passage, three minutes in, sounding much sooner.
        let seek = Schedule { passage_id: 4, start_sample: 180 * 44100,
                              sound_at: 81 * SEC, rate: 44100 };
        match f.on_state(&state_with(Some(seek), None), 80 * SEC) {
            Follow::StartAt { passage_id: 4, start_sample, .. } => {
                assert_eq!(start_sample, 180 * 44100, "the offset must survive the wire");
            }
            other => panic!("a seek was not acted on: {other:?}"),
        }
        // And is itself idempotent afterwards.
        assert_eq!(f.on_state(&state_with(Some(seek), None), 80 * SEC), Follow::Idle);
    }

    /// Two seeks to the same point at different times are different
    /// instructions, and the second must not be mistaken for a repeat.
    #[test]
    fn the_same_offset_announced_again_later_is_a_new_instruction() {
        let mut f = follower(BOSE);
        let a = Schedule { passage_id: 9, start_sample: 44100, sound_at: 90 * SEC, rate: 44100 };
        let b = Schedule { passage_id: 9, start_sample: 44100, sound_at: 95 * SEC, rate: 44100 };
        assert!(matches!(f.on_state(&state_with(Some(a), None), 80 * SEC), Follow::StartAt { .. }));
        assert!(matches!(f.on_state(&state_with(Some(b), None), 80 * SEC), Follow::StartAt { .. }));
    }

    #[test]
    fn a_repeated_schedule_is_acted_on_once() {
        // `[GDE-ECHO-320]` repeats every message rather than sequencing them,
        // so the follower has to be the thing that makes it idempotent.
        let sched = Schedule { passage_id: 4, start_sample: 0, sound_at: 100 * SEC, rate: 44100 };
        let st = state_with(Some(sched), None);
        let mut f = follower(BOSE);
        assert!(matches!(f.on_state(&st, 80 * SEC), Follow::StartAt { passage_id: 4, .. }));
        for _ in 0..30 {
            assert_eq!(f.on_state(&st, 80 * SEC), Follow::Idle);
        }
    }

    #[test]
    fn a_high_offset_node_can_miss_what_a_low_offset_node_makes() {
        // 100 ms of lead: bose can still submit, lempi02w needed to 255 ms ago.
        let sched = Schedule { passage_id: 5, start_sample: 0, sound_at: 10 * SEC + 100_000_000, rate: 44100 };
        let st = state_with(Some(sched), None);
        assert!(matches!(follower(BOSE).on_state(&st, 10 * SEC),
                         Follow::StartAt { .. }));
        assert_eq!(follower(LEMPIPI).on_state(&st, 10 * SEC),
                   Follow::Missed { passage_id: 5 });
    }

    #[test]
    fn a_master_that_cannot_place_itself_is_held_not_abandoned() {
        // `[GDE-ECHO-500]`'s handover is for a master that has gone away. This
        // one is present and saying so, and will recover at its own next
        // boundary -- going independent here would desynchronise on purpose.
        let st = EchoState {
            anchor: None,
            schedule: Some(Schedule { passage_id: 6, start_sample: 0, sound_at: 100 * SEC, rate: 44100 }),
            voided_by: Some(Voided::Underrun),
        };
        let mut f = follower(BOSE);
        assert_eq!(f.on_state(&st, 80 * SEC), Follow::Hold(Voided::Underrun));
        assert_eq!(f.trim_for(&st, None, 80 * SEC), None, "and no trim while held");
    }

    #[test]
    fn anchors_for_a_different_passage_are_not_a_huge_error() {
        // The trap: the master is on passage 7 and this node still on 6, so
        // the positions differ by minutes. Trimming against that would drive
        // the node hard in the wrong direction.
        let anchor = DriftAnchor {
            passage_id: 7, sample: 0, heard_at: 500 * SEC, rate: 44100, ppm: None,
        };
        let local = AirPosition { passage_id: 6, position_ms: 0, at: 200 * SEC };
        let st = state_with(None, Some(anchor));
        let mut f = follower(BOSE);
        f.basis.establish();
        assert_eq!(f.trim_for(&st, Some(&local), 500 * SEC), None);
    }

    #[test]
    fn a_node_with_no_basis_of_its_own_does_not_trim() {
        let anchor = DriftAnchor {
            passage_id: 1, sample: 0, heard_at: 100 * SEC, rate: 44100, ppm: None,
        };
        let local = AirPosition { passage_id: 1, position_ms: 0, at: 100 * SEC + 5_000_000 };
        let st = state_with(None, Some(anchor));
        let mut f = follower(BOSE);           // basis never established
        assert_eq!(f.trim_for(&st, Some(&local), 100 * SEC), Some(Trim::None));
    }

    #[test]
    fn a_late_follower_drops_and_then_waits_out_the_interval() {
        let anchor = DriftAnchor {
            passage_id: 1, sample: 0, heard_at: 100 * SEC, rate: 44100, ppm: None,
        };
        // 5 ms late.
        let local = AirPosition { passage_id: 1, position_ms: 0, at: 100 * SEC + 5_000_000 };
        let st = state_with(None, Some(anchor));
        let mut f = follower(BOSE);
        f.basis.establish();
        assert_eq!(f.trim_for(&st, Some(&local), 100 * SEC), Some(Trim::DropFrame));
        // Immediately after, the rate limit holds even though still 5 ms out.
        assert_eq!(f.trim_for(&st, Some(&local), 100 * SEC), Some(Trim::None));
        // A second later it may act again.
        assert_eq!(f.trim_for(&st, Some(&local), 101 * SEC), Some(Trim::DropFrame));
    }

    #[test]
    fn an_early_follower_duplicates() {
        let anchor = DriftAnchor {
            passage_id: 1, sample: 0, heard_at: 100 * SEC, rate: 44100, ppm: None,
        };
        let local = AirPosition { passage_id: 1, position_ms: 0, at: 100 * SEC - 5_000_000 };
        let st = state_with(None, Some(anchor));
        let mut f = follower(LEMPIPI);
        f.basis.establish();
        assert_eq!(f.trim_for(&st, Some(&local), 100 * SEC), Some(Trim::DuplicateFrame));
    }

    use Origin::{Announced, Local};

    #[test]
    fn silence_tops_the_queue_up_rather_than_letting_it_drain() {
        // `[GDE-ECHO-500]`: no timeout, no threshold. The queue simply never
        // runs dry, so there is no moment of decision.
        let local = vec![(1, Announced), (2, Announced)];
        let p = reconcile_queue(&local, &[], 5);
        assert_eq!(p.queue, local, "nothing announced, nothing replaced");
        assert_eq!(p.want_local, 3, "the Director owes three to reach depth");
        assert_eq!(p.discarded_local, 0);
    }

    #[test]
    fn a_node_out_of_contact_ends_up_entirely_local_without_a_cliff() {
        // Five announced passages is roughly twenty minutes of runway. Drain
        // them one at a time and the queue refills locally behind -- the
        // changeover is a blend rather than an event.
        let mut q: Vec<(i64, Origin)> = (1..=5).map(|i| (i, Announced)).collect();
        for step in 0..5 {
            q.remove(0); // a passage completes
            let p = reconcile_queue(&q, &[], 5);
            assert_eq!(p.want_local, 1, "exactly one selection per completion");
            q = p.queue;
            q.push((100 + step, Local));
            assert_eq!(q.len(), 5, "never below depth");
        }
        assert!(q.iter().all(|(_, o)| *o == Local), "fully independent, gradually");
    }

    #[test]
    fn announcements_win_and_gap_fillers_are_discarded() {
        // `[GDE-ECHO-510]`: the master's queue takes precedence immediately.
        let local = vec![(90, Announced), (101, Local), (102, Local)];
        let p = reconcile_queue(&local, &[7, 8, 9, 10, 11], 5);
        assert_eq!(p.queue.len(), 5);
        assert!(p.queue.iter().all(|(_, o)| *o == Announced));
        assert_eq!(p.queue[0].0, 7, "in the master's order");
        assert_eq!(p.discarded_local, 2, "both gap-fillers dropped");
        assert_eq!(p.want_local, 0);
    }

    #[test]
    fn a_short_announcement_is_topped_up_behind_rather_than_padded_with_locals() {
        // Discarding entries that are about to be re-chosen looks wasteful and
        // is the specified behaviour: a rejoining node plays the fleet's
        // programme, not a blend of two.
        let local = vec![(101, Local), (102, Local)];
        let p = reconcile_queue(&local, &[7, 8], 5);
        assert_eq!(p.queue, vec![(7, Announced), (8, Announced)]);
        assert_eq!(p.discarded_local, 2);
        assert_eq!(p.want_local, 3, "fresh selections, behind the announced");
    }

    #[test]
    fn rejoining_never_cuts_the_passage_in_progress_short() {
        // The node was never playing anything wrong, only something different.
        let master = AirPosition { passage_id: 42, position_ms: 95_000, at: 0 };
        assert_eq!(rejoin_action(Some(7), Some(&master)), Some(Rejoin::PlayOut));
    }

    #[test]
    fn with_nothing_sounding_it_joins_mid_passage_not_at_the_start() {
        // `[GDE-ECHO-330]` deferred mid-passage joining; the rejoin case makes
        // it required, because the master is always part-way through by then.
        let master = AirPosition { passage_id: 42, position_ms: 95_000, at: 0 };
        assert_eq!(rejoin_action(None, Some(&master)),
                   Some(Rejoin::JoinMidPassage { passage_id: 42, at_ms: 95_000 }));
    }

    #[test]
    fn a_node_already_on_the_masters_passage_has_nothing_to_rejoin() {
        let master = AirPosition { passage_id: 42, position_ms: 95_000, at: 0 };
        assert_eq!(rejoin_action(Some(42), Some(&master)), None);
        assert_eq!(rejoin_action(Some(7), None), None, "and no master, no action");
    }

    #[test]
    fn a_fresh_basis_is_not_valid_until_a_clean_boundary() {
        let mut b = Basis::default();
        assert!(!b.is_valid(), "absent is not zero `[GOV-SRC-040]`");
        b.establish();
        assert!(b.is_valid());
    }

    #[test]
    fn every_row_of_the_invalidation_table_voids_the_basis() {
        for why in [Voided::DeviceReopen, Voided::Underrun, Voided::Pause, Voided::Skip] {
            let mut b = Basis::default();
            b.establish();
            assert!(b.is_valid());
            b.void(why);
            assert!(!b.is_valid(), "{why:?} must void the basis");
            assert_eq!(b.voided_by(), Some(why), "and must say which");
            // Only a clean boundary re-establishes it.
            b.establish();
            assert!(b.is_valid());
        }
    }

    #[test]
    fn no_trim_without_a_basis_however_large_the_error() {
        let b = Basis::default();
        let t = trim_decision(&b, 10 * SEC as i64, Duration::from_micros(500),
                              Duration::from_secs(60), Duration::from_secs(1));
        assert_eq!(t, Trim::None, "a huge residual on stale state is not a licence");
    }

    #[test]
    fn trim_directions_are_not_reversed() {
        let mut b = Basis::default();
        b.establish();
        let band = Duration::from_micros(500);
        let (elapsed, interval) = (Duration::from_secs(10), Duration::from_secs(1));
        // Late -> drop, so the node stops falling further behind.
        assert_eq!(trim_decision(&b, 2_000_000, band, elapsed, interval), Trim::DropFrame);
        // Early -> duplicate.
        assert_eq!(trim_decision(&b, -2_000_000, band, elapsed, interval), Trim::DuplicateFrame);
    }

    #[test]
    fn the_deadband_holds_and_is_inclusive_at_its_edge() {
        let mut b = Basis::default();
        b.establish();
        let band = Duration::from_micros(500);
        let (elapsed, interval) = (Duration::from_secs(10), Duration::from_secs(1));
        assert_eq!(trim_decision(&b, 500_000, band, elapsed, interval), Trim::None);
        assert_eq!(trim_decision(&b, -500_000, band, elapsed, interval), Trim::None);
        assert_eq!(trim_decision(&b, 500_001, band, elapsed, interval), Trim::DropFrame);
    }

    #[test]
    fn the_rate_limit_holds_even_far_outside_the_deadband() {
        let mut b = Basis::default();
        b.establish();
        let t = trim_decision(&b, 50_000_000, Duration::from_micros(500),
                              Duration::from_millis(100), Duration::from_secs(1));
        assert_eq!(t, Trim::None, "never more than one frame per interval");
    }

    #[test]
    fn a_loop_fed_pure_noise_inside_the_band_never_trims() {
        // `[GDE-ECHO-350]`'s failure mode: a correction loop reacting to its
        // own measurement noise produces the slow wobble it exists to remove.
        let mut b = Basis::default();
        b.establish();
        let band = Duration::from_micros(500);
        let mut state: i64 = 12345;
        for _ in 0..10_000 {
            state = (state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407)) >> 1;
            let noise = state % 500_000; // strictly inside the band
            assert_eq!(
                trim_decision(&b, noise, band, Duration::from_secs(5), Duration::from_secs(1)),
                Trim::None
            );
        }
    }

    #[test]
    fn a_synthetic_node_running_fast_is_driven_back_to_the_band() {
        // The closed loop, against a clock told to run fast `[GDE-ECHO-370]`.
        // 14 ppm is bose's measured error `[LOG-FIX-030]`.
        let mut b = Basis::default();
        b.establish();
        let band = Duration::from_micros(500);
        let interval = Duration::from_secs(1);
        let rate = 44100i64;
        let ppm = 14.0f64;
        let mut residual: i64 = 5_000_000; // 5 ms late to begin with
        let mut trims = 0;
        for _ in 0..20_000 {
            // One second passes: the node's own error accrues...
            residual += (ppm * 1000.0) as i64; // ppm -> ns per second
            // ...and at most one frame is trimmed against it.
            match trim_decision(&b, residual, band, interval, interval) {
                Trim::DropFrame => { residual -= 1_000_000_000 / rate; trims += 1; }
                Trim::DuplicateFrame => { residual += 1_000_000_000 / rate; trims += 1; }
                Trim::None => {}
            }
        }
        assert!(trims > 0, "the loop must actually act");
        assert!(
            residual.abs() <= band.as_nanos() as i64 + 1_000_000_000 / rate,
            "converged to within the deadband plus one frame, got {residual} ns"
        );
    }
}
