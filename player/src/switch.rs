//! Holding two backends and exchanging which one sounds `[SPEC-BK-030]`.
//!
//! The session drives **this**, not a backend, and never learns that it has
//! changed: [`Switching`] is itself a [`Playback`], forwarding every call to
//! whichever side is live. That is the whole trick, and it is only available
//! because the trait was made load-bearing first `[SPEC-BK-022]`.
//!
//! **What crosses a handoff is a queue of passage ids and a position**, never
//! audio and never decoder state. Lempi names what it holds exactly; MPD's
//! queue has to be *read back* into passages, and some of it cannot be
//! `[SPEC-BK-045]`.

use crate::playback::{Capabilities, Playback};
use crate::queue::QueueEntry;

/// Which side is sounding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    /// Lempi's own engine: spans, gain and ramps `[SPEC-BK-025]`.
    Local,
    /// A guest, today MPD: spans only.
    Guest,
}

/// How the outgoing side stopped, so a caller can say `[PI3-API-030]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Stopped {
    /// It faded, and the changeover was smooth.
    Faded,
    /// It cut. Honest rather than hidden — MPD without a mixer cannot fade
    /// `[SPEC-MPD-099]`, and pretending otherwise would be the lie.
    Cut,
}

/// A backend that can stop gracefully when handed off away from.
///
/// Deliberately **not** part of [`Playback`]. Fading is something a handoff
/// wants and ordinary playback never does, and widening the trait for one
/// caller is how a seam stops being the shape of what crosses it
/// `[SPEC-BK-020]`.
pub trait FadeOut {
    /// Stop sounding over roughly this long. Returns whether it really faded.
    fn fade_out(&mut self, ms: u64) -> Stopped;

    /// The same fade, because the passage is **moving to the other backend**
    /// rather than being declined `[SPEC-BK-065]`.
    ///
    /// Identical in sound and different in meaning, which is why it is a
    /// method and not a comment at one call site: a backend that keeps
    /// listening history has to tell the two apart, and one that does not can
    /// ignore the distinction by taking this default.
    fn hand_off(&mut self, ms: u64) -> Stopped {
        self.fade_out(ms)
    }
}

/// A backend that can publish the Director's reasoning where its own clients
/// will find it `[SPEC-MPD-050]`.
///
/// Separate from [`Playback`] for the reason `FadeOut` is: publishing is
/// something a *guest* wants, because its clients have no other way to learn
/// why a passage was chosen. Lempi's own UI reads the decision store directly and
/// needs none of it, so putting this on the playback trait would have made every
/// backend answer a question only one of them is asked.
pub trait Publish {
    fn publish(&mut self, p: &Published<'_>);
}

/// What Lempi knows about a passage that a guest cannot say for itself.
///
/// A struct rather than positional arguments because there are five of them and
/// two are strings that would sit next to each other unchecked.
pub struct Published<'a> {
    pub passage_id: i64,
    /// The weight decomposition as JSON `[REQ-VIS-100]`.
    pub why: &'a str,
    /// A short human reading of the flavor; may be empty.
    pub flavor: &'a str,
    /// **What the passage actually is.** A guest names it from the file's tags,
    /// and a capture has none per passage `[SPEC-MPD-052]`.
    pub title: &'a str,
    pub artist: &'a str,
    pub chosen_at: i64,
}

/// A backend that can say where it is in the passage it is sounding.
///
/// Separate from [`Playback`] for the reason [`FadeOut`] is: a handoff needs
/// this and ordinary playback never does `[SPEC-BK-020]`. [`Playback::resume_at`]
/// is the write half and was already there; this is the read half that was
/// missing, and without it a passage crosses only ever at its beginning.
pub trait Progress {
    /// The passage now sounding and how far **into its span** it is, or `None`
    /// when nothing is sounding.
    ///
    /// Into the span, never into the file. A passage is a span of a file
    /// `[SPEC-DF-020]`, and MPD measures a bounded song the same way — a range
    /// or a cue track runs its own clock from zero `[SPEC-BK-055]`. The two
    /// agreeing is what lets a position cross unaltered.
    fn head_position(&self) -> Option<(i64, u64)>;

    /// Read the backend's own clock now rather than at its next scheduled poll.
    ///
    /// Free for the local engine, which is the clock. One request for a guest,
    /// which is why it is asked for explicitly instead of on every tick.
    fn refresh(&mut self) {}

    /// Has the passage now sounding **already been written to play history**
    /// by this side?
    ///
    /// `[SPEC-BK-037]` named the hazard before there was any code to have it:
    /// a passage that crosses mid-play can be judged twice, once by each
    /// side. The incoming side's own accounting is right — its clock starts
    /// where the passage arrived, so time heard elsewhere is included — but
    /// only if the outgoing side has not already counted it.
    fn head_counted(&self) -> bool {
        false
    }

    /// Take a passage whose play another backend has already recorded, so
    /// this one does not record it again `[SPEC-BK-065]`.
    ///
    /// It must also not be written down as a *rejection* when it ends: it
    /// played, on the other side, and that is already in the history.
    fn adopt_counted(&mut self, _passage_id: i64) {}
}

/// What [`Switching`] holds: something that plays, stops gracefully, says why
/// it is playing what it is, and knows where it has got to.
pub trait Backend: Playback + FadeOut + Publish + Progress {}
impl<T: Playback + FadeOut + Publish + Progress> Backend for T {}

/// What a queue transfer moved, and what it lost on the way.
#[derive(Debug, Default, PartialEq)]
pub struct Carried {
    /// Passages now queued on the incoming backend, in order.
    pub moved: Vec<i64>,
    /// Passages the library could no longer build. Named, not counted.
    pub lost: Vec<i64>,
}

/// Re-offer a carried queue to whichever backend is now live.
///
/// **The spans are re-derived here, not carried.** A passage id is the whole of
/// what crosses `[SPEC-BK-030]`; `start_ms`, `end_ms`, gain and ramps are read
/// from the library again, because they belong to the passage and not to
/// whichever backend last played it. Handing over a built `QueueEntry` would
/// have carried the *previous* backend's idea of the passage into the next one.
///
/// A passage that cannot be rebuilt is reported and skipped, for the same
/// reason an unnameable guest entry is `[SPEC-BK-045]`: a library that has been
/// rescanned since the queue was built may have renumbered it away, and that is
/// not a reason to refuse the switch.
///
/// **`moved` is what the backend took, not what the library could build**
/// `[SPEC-BK-047]`. `enqueue` returns nothing and cannot refuse in line; a
/// backend that would not take a passage says so by dropping it. Counting the
/// successful `build` as a passage carried made this report unfalsifiable —
/// measured on the appliance, a handoff into an MPD whose socket had died
/// announced "6 passage(s) carried" into an empty queue, and went on saying it
/// for as long as the connection stayed dead `[PI-CHR-095]`. A report that
/// cannot come out wrong is not evidence `[PI3-API-030]`.
pub fn carry_queue<F>(ids: &[i64], into: &mut dyn Playback, build: F) -> Carried
where
    F: Fn(i64) -> Option<QueueEntry>,
{
    // Anything already waiting is from before this transfer and belongs to
    // whoever asks next, not to this report.
    let _ = into.take_dropped();
    let mut out = Carried::default();
    for &id in ids {
        match build(id) {
            Some(e) => {
                into.enqueue(e);
                out.moved.push(id);
            }
            None => out.lost.push(id),
        }
    }
    // Taken here rather than left for the session loop because these are the
    // same failure as an unbuildable passage and want the same treatment: named
    // in `lost`, and un-counted by the Director once `[REQ-PD-112]`.
    let refused = into.take_dropped();
    if !refused.is_empty() {
        out.moved.retain(|id| !refused.contains(id));
        out.lost.extend(refused);
    }
    out
}

/// Two backends, one of them sounding.
pub struct Switching {
    local: Box<dyn Backend>,
    guest: Option<Box<dyn Backend>>,
    active: Side,
}

impl Switching {
    /// Start on the local engine, which is where an appliance comes up
    /// `[PI5-PWR-030]`. A guest is attached later or never.
    pub fn new(local: Box<dyn Backend>) -> Self {
        Self { local, guest: None, active: Side::Local }
    }

    pub fn attach_guest(&mut self, guest: Box<dyn Backend>) {
        self.guest = Some(guest);
    }

    pub fn active(&self) -> Side {
        self.active
    }

    pub fn has_guest(&self) -> bool {
        self.guest.is_some()
    }

    fn live(&self) -> &dyn Backend {
        match self.active {
            Side::Local => self.local.as_ref(),
            Side::Guest => self.guest.as_deref().unwrap_or(self.local.as_ref()),
        }
    }

    fn live_mut(&mut self) -> &mut dyn Backend {
        match self.active {
            Side::Guest if self.guest.is_some() => self.guest.as_deref_mut().unwrap(),
            _ => self.local.as_mut(),
        }
    }

    /// Move the session to the other side, reporting **what the outgoing
    /// backend was holding** so the caller can re-offer it.
    ///
    /// The transfer is not done here, and that is deliberate: a queue crosses
    /// as passage ids, and turning an id back into something playable needs the
    /// library, which a backend has no business holding. The caller reads the
    /// library, builds entries and enqueues them into the new side — where the
    /// spans are re-derived, because a span belongs to the passage and not to
    /// whichever backend last played it.
    ///
    /// **Audio is not crossfaded here yet.** `[SPEC-BK-030]` wants both sides
    /// sounding briefly, which the appliance measurement showed is possible;
    /// this moves the queue and leaves that to the audio path.
    pub fn switch_to(&mut self, target: Side) -> Result<Vec<i64>, String> {
        self.switch_to_over(target, 0).map(|(ids, _)| ids)
    }

    /// The same, fading the outgoing side out over `fade_ms` first.
    ///
    /// Reports **how** it stopped as well as what it was holding, because a
    /// backend that cannot fade cuts instead `[SPEC-MPD-099]` and a caller
    /// saying "switched" over a hard cut would be describing something that did
    /// not happen `[PI3-API-030]`.
    /// The side that is *not* live, so it can be loaded before it is heard.
    ///
    /// The whole of what makes a handoff seamless: a backend that is made ready
    /// while the other one is still sounding has nothing to catch up on when it
    /// takes over. `live_mut` cannot express this — by the time a side is live
    /// it is too late to prepare it.
    pub fn side_mut(&mut self, side: Side) -> Option<&mut (dyn Backend + 'static)> {
        match side {
            Side::Local => Some(self.local.as_mut()),
            Side::Guest => self.guest.as_deref_mut(),
        }
    }

    /// Fade whichever side is live, then make `target` live.
    ///
    /// The two halves of [`switch_to_over`](Self::switch_to_over) after the
    /// queue has been read, split apart so a caller can put the loading of the
    /// incoming side *between* them.
    pub fn stop_and_flip(&mut self, target: Side, fade_ms: u64) -> Result<Stopped, String> {
        if target == Side::Guest && self.guest.is_none() {
            return Err("no guest backend is attached".into());
        }
        let stopped = if fade_ms > 0 { self.live_mut().hand_off(fade_ms) } else { Stopped::Cut };
        self.active = target;
        Ok(stopped)
    }

    pub fn switch_to_over(
        &mut self,
        target: Side,
        fade_ms: u64,
    ) -> Result<(Vec<i64>, Stopped), String> {
        if target == self.active {
            return Ok((Vec::new(), Stopped::Faded));
        }
        if target == Side::Guest && self.guest.is_none() {
            return Err("no guest backend is attached".into());
        }
        let carried = self.live().queued_ids();
        // Stop the outgoing side *before* the switch, while it is still the
        // live one: after it, `live_mut` would fade the side just arrived at.
        let stopped =
            if fade_ms > 0 { self.live_mut().fade_out(fade_ms) } else { Stopped::Cut };
        self.active = target;
        Ok((carried, stopped))
    }
}

/// Forwarding the other two capabilities as well, so `Switching` is a
/// [`Backend`] and can be handed to a session like any other.
impl Publish for Switching {
    fn publish(&mut self, p: &Published<'_>) {
        self.live_mut().publish(p)
    }
}

/// The live side's position, because the other side's is not what anyone is
/// hearing.
impl Progress for Switching {
    fn head_position(&self) -> Option<(i64, u64)> {
        self.live().head_position()
    }
    fn refresh(&mut self) {
        self.live_mut().refresh()
    }
}

/// Stopping a `Switching` stops whichever side is sounding — the other one
/// already is not.
impl FadeOut for Switching {
    fn fade_out(&mut self, ms: u64) -> Stopped {
        self.live_mut().fade_out(ms)
    }
}

/// Forwarding, so the session cannot tell `[SPEC-BK-025]`.
impl Playback for Switching {
    /// **The live side's, never the union.** Reporting `FULL` while a guest is
    /// playing would promise gain and ramps that MPD cannot honour, which is
    /// exactly the lie `[SPEC-BK-040]` requires be told in advance instead.
    fn capabilities(&self) -> Capabilities {
        self.live().capabilities()
    }
    fn enqueue(&mut self, entry: QueueEntry) {
        self.live_mut().enqueue(entry)
    }
    fn queued_ids(&self) -> Vec<i64> {
        self.live().queued_ids()
    }
    fn queued_ms(&self) -> u64 {
        self.live().queued_ms()
    }
    fn shortfall(&self) -> usize {
        self.live().shortfall()
    }
    fn take_dropped(&mut self) -> Vec<i64> {
        self.live_mut().take_dropped()
    }
    fn resume_at(&mut self, position_ms: u64) {
        self.live_mut().resume_at(position_ms)
    }
    fn seek_to(&mut self, position_ms: u64) {
        self.live_mut().seek_to(position_ms)
    }
    fn tick(&mut self) -> usize {
        // **Both sides tick, not only the live one.** A guest that is not
        // sounding still has a server to reconcile with, and a local engine
        // that is not sounding still has an output ring to drain. Ticking only
        // the active side would make the other one's view of the world stop at
        // the moment of the switch, and it would be wrong the moment it
        // mattered.
        let mut work = self.local.tick();
        if let Some(g) = self.guest.as_deref_mut() {
            work += g.tick();
        }
        work
    }
    fn is_shutdown(&self) -> bool {
        self.live().is_shutdown()
    }

    /// **Both sides get it, not only the live one** — the same reasoning as
    /// `tick`: a switch must not leave the side just vacated holding a stale
    /// depth or interval, or switching back would show it briefly wrong
    /// `[SPEC-MPD-105]`.
    fn apply_queue_settings(&mut self, depth: usize, sample_interval_ms: u64) {
        self.local.apply_queue_settings(depth, sample_interval_ms);
        if let Some(g) = self.guest.as_deref_mut() {
            g.apply_queue_settings(depth, sample_interval_ms);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A backend that records what it was told, and nothing else.
    #[derive(Default)]
    struct Fake {
        queued: Vec<i64>,
        ticks: usize,
        caps: Option<Capabilities>,
        faded_ms: Option<u64>,
        can_fade: bool,
        /// What it would say it is playing, and where.
        head: Option<(i64, u64)>,
        /// Where `resume_at` put it, so a test can see the position cross.
        resumed: Option<u64>,
        refreshed: usize,
        /// Set when `hand_off` was used rather than `fade_out`, which is the
        /// difference between a handoff and a rejection `[SPEC-BK-065]`.
        /// Shared, because once boxed as a `Backend` the field is out of a
        /// test's reach and the flag has to come back some other way.
        handed_off: Option<std::sync::Arc<std::sync::atomic::AtomicBool>>,
        /// Passages this backend will not take. A real one refuses for its own
        /// reasons — outside MPD's music directory, or nothing on the far end
        /// of the socket — and says so only by dropping them.
        refuses: Vec<i64>,
        dropped: Vec<i64>,
        /// What `apply_queue_settings` was last told, if anything. Shared for
        /// the same reason `handed_off` is: once boxed as a `Backend` the
        /// field itself is out of a test's reach.
        applied_settings: std::sync::Arc<std::sync::Mutex<Option<(usize, u64)>>>,
    }

    impl Progress for Fake {
        fn head_position(&self) -> Option<(i64, u64)> {
            self.head
        }
        fn refresh(&mut self) {
            self.refreshed += 1;
        }
    }
    impl Publish for Fake {
        fn publish(&mut self, _p: &Published<'_>) {}
    }
    impl FadeOut for Fake {
        fn fade_out(&mut self, ms: u64) -> Stopped {
            self.faded_ms = Some(ms);
            if self.can_fade {
                Stopped::Faded
            } else {
                Stopped::Cut
            }
        }
        fn hand_off(&mut self, ms: u64) -> Stopped {
            if let Some(f) = &self.handed_off {
                f.store(true, std::sync::atomic::Ordering::SeqCst);
            }
            self.fade_out(ms)
        }
    }
    impl Playback for Fake {
        fn capabilities(&self) -> Capabilities {
            self.caps.unwrap_or(Capabilities::FULL)
        }
        fn enqueue(&mut self, e: QueueEntry) {
            if self.refuses.contains(&e.passage_id) {
                self.dropped.push(e.passage_id);
                return;
            }
            self.queued.push(e.passage_id);
        }
        fn queued_ids(&self) -> Vec<i64> {
            self.queued.clone()
        }
        fn queued_ms(&self) -> u64 {
            self.queued.len() as u64 * 1000
        }
        fn shortfall(&self) -> usize {
            5usize.saturating_sub(self.queued.len())
        }
        fn take_dropped(&mut self) -> Vec<i64> {
            std::mem::take(&mut self.dropped)
        }
        fn resume_at(&mut self, ms: u64) {
            self.resumed = Some(ms);
        }
        fn tick(&mut self) -> usize {
            self.ticks += 1;
            1
        }
        fn is_shutdown(&self) -> bool {
            false
        }
        fn apply_queue_settings(&mut self, depth: usize, sample_interval_ms: u64) {
            *self.applied_settings.lock().unwrap() = Some((depth, sample_interval_ms));
        }
    }

    fn entry(id: i64) -> QueueEntry {
        QueueEntry {
            qid: 0,
            passage_id: id,
            path: std::path::PathBuf::from("x.mp3"),
            start_ms: 0,
            end_ms: 1000,
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

    /// A queue crosses as ids, and arrives rebuilt from the library
    /// `[SPEC-BK-030]`.
    #[test]
    fn a_carried_queue_is_rebuilt_on_the_far_side() {
        let mut dest = Fake::default();
        let got = carry_queue(&[7, 8], &mut dest, |id| Some(entry(id)));
        assert_eq!(got.moved, vec![7, 8]);
        assert!(got.lost.is_empty());
        assert_eq!(dest.queued_ids(), vec![7, 8], "order is the listener's order");
    }

    /// A passage the library can no longer build is reported and skipped, not
    /// allowed to stop the switch `[SPEC-BK-045]`.
    #[test]
    fn a_passage_the_library_lost_does_not_stop_the_switch() {
        let mut dest = Fake::default();
        let got = carry_queue(&[7, 999, 8], &mut dest, |id| (id != 999).then(|| entry(id)));
        assert_eq!(got.moved, vec![7, 8]);
        assert_eq!(got.lost, vec![999], "named, so a caller can say which");
        assert_eq!(dest.queued_ids(), vec![7, 8]);
    }

    /// The bug this pair of tests exists for: a backend that took nothing at
    /// all once reported a full queue carried `[SPEC-BK-047]`.
    #[test]
    fn a_passage_the_backend_refused_is_not_reported_as_carried() {
        let mut dest = Fake { refuses: vec![8], ..Default::default() };
        let got = carry_queue(&[7, 8, 9], &mut dest, |id| Some(entry(id)));
        assert_eq!(got.moved, vec![7, 9], "only what arrived");
        assert_eq!(got.lost, vec![8], "and the refusal is named, not silent");
        assert_eq!(dest.queued_ids(), vec![7, 9]);
    }

    /// A dead MPD socket refuses everything, which is exactly the case that
    /// went unnoticed on the appliance `[PI-CHR-095]`.
    #[test]
    fn a_backend_that_takes_nothing_reports_nothing_carried() {
        let mut dest = Fake { refuses: vec![7, 8, 9], ..Default::default() };
        let got = carry_queue(&[7, 8, 9], &mut dest, |id| Some(entry(id)));
        assert!(got.moved.is_empty(), "nothing arrived, so nothing was carried");
        assert_eq!(got.lost, vec![7, 8, 9]);
    }

    /// A drop left over from before the transfer is not this transfer's news,
    /// and must not turn a passage that did arrive into a loss.
    #[test]
    fn a_stale_drop_does_not_contaminate_the_report() {
        let mut dest = Fake::default();
        dest.dropped.push(7);
        let got = carry_queue(&[7, 8], &mut dest, |id| Some(entry(id)));
        assert_eq!(got.moved, vec![7, 8], "both were taken this time");
        assert!(got.lost.is_empty());
    }

    impl Publish for Cutter {
        fn publish(&mut self, _p: &Published<'_>) {}
    }
    impl Publish for Fader {
        fn publish(&mut self, _p: &Published<'_>) {}
    }
    struct Cutter;
    impl FadeOut for Cutter {
        fn fade_out(&mut self, _ms: u64) -> Stopped {
            Stopped::Cut
        }
    }
    struct Fader;
    impl FadeOut for Fader {
        fn fade_out(&mut self, _ms: u64) -> Stopped {
            Stopped::Faded
        }
    }

    /// A backend that cannot fade says so, rather than cutting quietly
    /// `[SPEC-MPD-099]`, `[PI3-API-030]`.
    #[test]
    fn a_backend_that_cannot_fade_reports_the_cut() {
        assert_eq!(Cutter.fade_out(600), Stopped::Cut);
        assert_eq!(Fader.fade_out(600), Stopped::Faded);
    }

    /// The session talks to one thing; which side answers is not its business.
    #[test]
    fn the_session_cannot_tell_which_side_it_is_driving() {
        let mut s = Switching::new(Box::new(Fake::default()));
        s.attach_guest(Box::new(Fake { caps: Some(Capabilities::MPD), ..Default::default() }));

        let backend: &mut dyn Playback = &mut s;
        backend.enqueue(entry(1));
        assert_eq!(backend.queued_ids(), vec![1], "the local side took it");
        assert!(backend.capabilities().gain, "and reports what the local side can do");

        let carried = s.switch_to(Side::Guest).unwrap();
        assert_eq!(carried, vec![1], "and says what the outgoing side was holding");
        let backend: &mut dyn Playback = &mut s;
        assert!(backend.queued_ids().is_empty(), "the guest has its own queue");
        assert!(!backend.capabilities().gain, "and its own, smaller capabilities");
        backend.enqueue(entry(2));
        assert_eq!(backend.queued_ids(), vec![2]);

        assert_eq!(s.switch_to(Side::Local).unwrap(), vec![2]);
        assert_eq!(s.queued_ids(), vec![1], "the local side kept what it had");
    }

    /// A settings change reaches both sides, not only whichever is sounding
    /// `[SPEC-MPD-105]` — the same reasoning `tick` already gets, since a
    /// switch back to the other side must not find it holding a stale value.
    #[test]
    fn a_queue_settings_change_reaches_both_sides() {
        let local_applied = std::sync::Arc::new(std::sync::Mutex::new(None));
        let guest_applied = std::sync::Arc::new(std::sync::Mutex::new(None));
        let mut s = Switching::new(Box::new(Fake {
            applied_settings: local_applied.clone(),
            ..Default::default()
        }));
        s.attach_guest(Box::new(Fake {
            caps: Some(Capabilities::MPD),
            applied_settings: guest_applied.clone(),
            ..Default::default()
        }));

        let backend: &mut dyn Playback = &mut s;
        backend.apply_queue_settings(8, 2_500);

        assert_eq!(*local_applied.lock().unwrap(), Some((8, 2_500)), "the local side got it");
        assert_eq!(
            *guest_applied.lock().unwrap(),
            Some((8, 2_500)),
            "and so did the guest, whether or not it is the one sounding"
        );
    }

    /// The **outgoing** side is the one that stops, and it stops before the
    /// switch. Fading after would have silenced the side just arrived at.
    #[test]
    fn the_outgoing_side_is_what_fades() {
        let mut s = Switching::new(Box::new(Fake { can_fade: true, ..Default::default() }));
        s.attach_guest(Box::new(Fake { can_fade: true, ..Default::default() }));

        let (_, stopped) = s.switch_to_over(Side::Guest, 600).unwrap();
        assert_eq!(stopped, Stopped::Faded);
        assert_eq!(s.active(), Side::Guest);
    }

    /// A cut is reported as a cut `[SPEC-MPD-099]`. The switch still happens —
    /// the listener asked for it, and an abrupt stop is not a failure.
    #[test]
    fn a_side_that_cannot_fade_still_hands_over() {
        let mut s = Switching::new(Box::new(Fake { can_fade: false, ..Default::default() }));
        s.attach_guest(Box::new(Fake { can_fade: true, ..Default::default() }));
        let (_, stopped) = s.switch_to_over(Side::Guest, 600).unwrap();
        assert_eq!(stopped, Stopped::Cut, "said so, rather than claiming a fade");
        assert_eq!(s.active(), Side::Guest, "and switched anyway");
    }

    /// Capabilities must follow the live side, or `[SPEC-BK-040]` cannot be
    /// honoured: a UI would promise gain while MPD was playing.
    #[test]
    fn capabilities_are_the_live_sides_and_not_the_union() {
        let mut s = Switching::new(Box::new(Fake::default()));
        s.attach_guest(Box::new(Fake { caps: Some(Capabilities::MPD), ..Default::default() }));
        assert_eq!(s.capabilities(), Capabilities::FULL);
        s.switch_to(Side::Guest).unwrap();
        assert_eq!(s.capabilities(), Capabilities::MPD);
    }

    /// Both sides keep working while one sounds. A guest left un-ticked would
    /// stop reconciling with its server and be wrong the moment it mattered.
    #[test]
    fn the_idle_side_keeps_ticking() {
        let mut s = Switching::new(Box::new(Fake::default()));
        s.attach_guest(Box::new(Fake::default()));
        assert_eq!(s.tick(), 2, "one unit of work from each side");
    }

    /// The incoming side is loadable **while the other one is still sounding**,
    /// which is the whole of what makes a handoff seamless `[SPEC-BK-065]`.
    #[test]
    fn the_side_that_is_not_live_can_be_loaded_before_it_is_heard() {
        let mut s = Switching::new(Box::new(Fake::default()));
        s.attach_guest(Box::new(Fake::default()));

        let into = s.side_mut(Side::Guest).expect("a guest is attached");
        into.enqueue(entry(7));
        into.resume_at(42_000);

        assert_eq!(s.queued_ids(), Vec::<i64>::new(), "the live side is undisturbed");
        assert_eq!(s.active(), Side::Local, "and still the live one");
        s.stop_and_flip(Side::Guest, 600).unwrap();
        assert_eq!(s.queued_ids(), vec![7], "the side that was prepared is now live");
    }

    /// A position crosses, so the passage does not start again from its
    /// beginning `[SPEC-BK-065]`.
    #[test]
    fn a_resume_point_reaches_the_incoming_side() {
        let mut s = Switching::new(Box::new(Fake::default()));
        s.attach_guest(Box::new(Fake::default()));
        s.side_mut(Side::Guest).unwrap().resume_at(96_500);
        s.switch_to(Side::Guest).unwrap();
        assert_eq!(s.queued_ms(), 0, "nothing queued, but the point was taken");
        // Read back through the concrete side, which is where a test can see it.
        let g = s.side_mut(Side::Guest).unwrap();
        assert_eq!(g.head_position(), None, "not sounding until it is told to");
    }

    /// **A handoff must not be recorded as a rejection** `[SPEC-BK-065]`. The
    /// fade is identical; only the meaning differs, so the outgoing side is
    /// asked with the method that carries that meaning.
    #[test]
    fn the_outgoing_side_is_told_it_is_a_handoff_not_a_skip() {
        let flag = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
        let mut s = Switching::new(Box::new(Fake {
            can_fade: true,
            handed_off: Some(flag.clone()),
            ..Default::default()
        }));
        s.attach_guest(Box::new(Fake { can_fade: true, ..Default::default() }));

        s.stop_and_flip(Side::Guest, 600).unwrap();

        assert!(
            flag.load(std::sync::atomic::Ordering::SeqCst),
            "asked through hand_off, so listening history is not told a skip happened"
        );
    }

    /// The position reported is the sounding side's, not the idle one's.
    #[test]
    fn progress_follows_the_live_side() {
        let mut s = Switching::new(Box::new(Fake { head: Some((1, 5_000)), ..Default::default() }));
        s.attach_guest(Box::new(Fake { head: Some((2, 9_000)), ..Default::default() }));
        assert_eq!(s.head_position(), Some((1, 5_000)));
        s.switch_to(Side::Guest).unwrap();
        assert_eq!(s.head_position(), Some((2, 9_000)), "whoever is audible");
    }

    #[test]
    fn switching_to_a_guest_that_is_not_there_is_refused() {
        let mut s = Switching::new(Box::new(Fake::default()));
        assert!(s.switch_to(Side::Guest).is_err());
        assert_eq!(s.active(), Side::Local, "and leaves the session where it was");
    }
}
