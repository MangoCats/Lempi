//! What a session needs of whatever is playing `[GDE-EXT-055]`.
//!
//! **Began as a spike, on `investigate/external-players`, to measure the cost
//! of driving something other than the built-in engine.** The measurement it
//! made was the width of this file: if the trait stayed small, the Director
//! was portable. It did, and MPD is now a shipped backend (`SPEC015`,
//! `SPEC018`) built on this trait — OpenSubsonic was not pursued.
//!
//! The seam was already here and had not been named. Measured on this tree:
//!
//! * `Session` calls **seven** methods on `Engine`, and a binary's loop three
//!   more.
//! * The **Director touches `path` nowhere** — the only occurrences under
//!   `director/` are confined to a handful of comments and a test fixture's
//!   DDL, not code.
//! * Exactly **one line** turns a queue entry into audio:
//!   `PassageDecoder::open(&e.path, …)` in `engine.rs`. Every other use of
//!   `entry.path` is an error message, a filename for display, or `relink`,
//!   which is a different subsystem entirely.
//!
//! So selection is already independent of playback. What follows only writes
//! that down.

use crate::engine::Engine;
use crate::queue::QueueEntry;

/// What a backend can actually do, declared rather than assumed.
///
/// The temptation is a trait that pretends every backend is equivalent, and it
/// must be refused. Lempi selects a **span** — `start_ms` to `end_ms`, with
/// lead-in, lead-out and gain `[SPEC-SC-040]` — and MPD and OpenSubsonic
/// address whole files. A backend that cannot honour a span will play the whole
/// file instead, which for a DAO capture means forty songs where one was
/// chosen `[GDE-EXT-025]`.
///
/// Reporting that is the whole point. `[PI3-API-030]` is the standing precedent:
/// an output that accepts audio perfectly and is inaudible is a player lying
/// about what it is doing, and this project treats that as the fault to remove
/// rather than the cost of doing business. A backend that silently plays the
/// wrong forty minutes is the same fault wearing a network protocol.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Capabilities {
    /// Can it start and stop inside a file? False for MPD and OpenSubsonic as
    /// commonly deployed, and the reason `kind='radio'` trim points do not
    /// survive the trip.
    pub spans: bool,
    /// Can it apply per-passage gain `[SPEC-SC-040]`?
    pub gain: bool,
    /// Can the listener move to a point inside the passage `[REQ-VIS-225]`?
    ///
    /// Declared rather than assumed, for the reason the rest of this type
    /// exists `[SPEC-BK-040]`: a control that silently does nothing is worse
    /// than one that is not offered.
    pub seek: bool,
    /// Can it ramp in and out, or does a passage begin and end abruptly?
    pub ramps: bool,
    /// Can it say *what the passage is*?
    ///
    /// **Missed in the first draft of this type, and found by a listener.** A
    /// guest names a passage from the file's tags, and a DAO capture carries
    /// one set of album-level tags for the whole recording — no per-track
    /// title at all. **2,840 of 8,330 radio passages here (34.1%) live in such
    /// a file**, so a third of everything the Director can choose arrives at a
    /// client with the album's name or none.
    ///
    /// Lempi knows the title; it comes from MusicBrainz `[SPEC-DF-030]` and is
    /// on the queue entry. There is simply no place in MPD's protocol to put
    /// it: a queue entry's tags are the file's. So this is declared false and
    /// published out-of-band instead `[SPEC-MPD-050]`.
    pub naming: bool,
}

impl Capabilities {
    /// Everything, which is what the built-in engine does.
    pub const FULL: Self =
        Self { spans: true, gain: true, ramps: true, naming: true, seek: true };
    /// A whole-file backend: it plays what you name, start to end.
    ///
    /// OpenSubsonic as deployed. `stream` returns a song; HTTP range requests
    /// seek within the bytes, which is not the same as being told to stop.
    pub const WHOLE_FILE: Self =
        Self { spans: false, gain: false, ramps: false, naming: false, seek: false };

    /// **MPD, and the surprise of this investigation.**
    ///
    /// `rangeid {ID} {START:END}` has specified the portion of a song to play
    /// since MPD 0.19, in fractional seconds, with either end optional. That is
    /// Lempi's passage model almost exactly, so the Album/Radio duality
    /// `[GDE-BMK-030]` survives the trip to MPD — where it does not survive the
    /// trip to OpenSubsonic. `crossfade` exists but is global rather than
    /// per-passage, and ReplayGain is per-file rather than per-span, so gain
    /// and ramps are still lost.
    pub const MPD: Self =
        Self { spans: true, gain: false, ramps: false, naming: false, seek: true };

    /// Would sending this passage to this backend play something other than
    /// what was chosen?
    ///
    /// True only when the passage is a genuine slice. A passage covering its
    /// whole file — which is every `ingest:whole-file` row, and most of a
    /// single-track library — is unharmed by a backend that cannot clip.
    pub fn would_misplay(&self, entry: &QueueEntry, file_duration_ms: u64) -> bool {
        if self.spans {
            return false;
        }
        let covers_whole = entry.start_ms == 0
            && entry.end_ms + WHOLE_FILE_SLACK_MS >= file_duration_ms;
        !covers_whole
    }
}

/// Within this much of the end, a passage is the whole file. The same slack
/// `extract_library.py` uses when deciding whether to slice before extracting.
pub const WHOLE_FILE_SLACK_MS: u64 = 5_000;

/// The whole of what a session asks of a player.
///
/// **Eleven methods, and the shape now comes from the callers.** *(Narrowed
/// 2026-08-21, when a session was first driven through it; `apply_queue_settings`
/// added 2026-09-03 `[SPEC-MPD-105]`.)*
///
/// The spike's own finding stands: every method forwards to one that already
/// existed, and nothing in `engine.rs` was touched to make it compile. What did
/// not stand was the width. `queued` returned an owned `Vec<QueueEntry>` — a
/// deep clone of every queued passage, per tick — where the concrete code had
/// been careful to hand out borrows.
///
/// Looking at what the four call sites want settled it: three want **passage
/// ids**, one wants the **total queued duration**, none wants a `QueueEntry`.
/// So the trait asks for those two instead, and the abstraction costs a
/// `Vec<i64>` rather than a clone of every path, title and tag `[SPEC-BK-020]`.
pub trait Playback {
    /// What this backend can honour, declared rather than assumed.
    fn capabilities(&self) -> Capabilities;

    /// Add a passage to the back of the queue.
    fn enqueue(&mut self, entry: QueueEntry);

    /// The passages coming, in play order. The Director reads this to avoid
    /// choosing something already queued `[SPEC-DIR-160]`, and that is all it
    /// wants — hence ids rather than entries.
    fn queued_ids(&self) -> Vec<i64>;

    /// How much play time is queued ahead, in milliseconds. Used to decide
    /// whether the lookahead is deep enough in *time* rather than in count.
    fn queued_ms(&self) -> u64;

    /// How many more passages are wanted to reach the configured depth.
    fn shortfall(&self) -> usize;

    /// Passages the backend could not open, so the Director can un-count them
    /// `[REQ-PD-112]`. A remote backend reports the same thing for a song the
    /// server has forgotten.
    fn take_dropped(&mut self) -> Vec<i64>;

    /// Begin the first queued passage at this offset, for a resumed session.
    fn resume_at(&mut self, position_ms: u64);

    /// Move to a point inside the passage already sounding `[REQ-VIS-225]`.
    ///
    /// Distinct from `resume_at`, which places a passage that has not started
    /// yet. A backend whose `capabilities().seek` is false may leave this
    /// unimplemented; one that advertises it must not.
    fn seek_to(&mut self, _position_ms: u64) {}

    /// Do a slice of work. For the local engine this mixes; for a remote
    /// backend it polls the server and reconciles.
    fn tick(&mut self) -> usize;

    fn is_shutdown(&self) -> bool;

    /// Apply the listener's queue-depth and sample-interval settings live,
    /// without a reconnect `[SPEC-MPD-105]`.
    ///
    /// The local engine already receives these through its own `Command`
    /// channel (`Engine::drain_commands`), which reaches it whether or not it
    /// is the side sounding — `Switching::tick` ticks both sides every pass,
    /// so `Engine` keeps draining its channel regardless. A remote guest has
    /// no such channel: until this method existed, changing either setting
    /// while a guest was live had no visible effect there at all, only on the
    /// (possibly idle) local engine. Defaulted to nothing for a backend with
    /// no such state of its own to update.
    fn apply_queue_settings(&mut self, _depth: usize, _sample_interval_ms: u64) {}
}

/// The local engine is a backend like any other, and the one that can do
/// everything `[SPEC-BK-025]`.
impl Playback for crate::engine::Engine {
    fn capabilities(&self) -> Capabilities {
        Capabilities::FULL
    }
    fn enqueue(&mut self, entry: QueueEntry) {
        Engine::enqueue(self, entry)
    }
    fn queued_ids(&self) -> Vec<i64> {
        Engine::queued(self).map(|e| e.passage_id).collect()
    }
    fn queued_ms(&self) -> u64 {
        Engine::queued(self).map(|e| e.end_ms.saturating_sub(e.start_ms)).sum()
    }
    fn shortfall(&self) -> usize {
        Engine::shortfall(self)
    }
    fn take_dropped(&mut self) -> Vec<i64> {
        Engine::take_dropped(self)
    }
    fn resume_at(&mut self, position_ms: u64) {
        Engine::resume_at(self, position_ms)
    }
    fn seek_to(&mut self, position_ms: u64) {
        Engine::seek_to(self, position_ms)
    }
    fn tick(&mut self) -> usize {
        Engine::tick(self)
    }
    fn is_shutdown(&self) -> bool {
        Engine::is_shutdown(self)
    }
}

/// **The local engine fades, through the path a skip already takes.**
///
/// `[REQ-AUD-158]`'s curve, `[XFD-ORTH-020]`'s accounting, and the one place
/// that takes the ring from sounding to not — reused rather than reimplemented,
/// because a handoff has no business owning a second idea of a fade.
///
/// `Cut` where there is no output to fade: a silent path or a failed device has
/// nothing to ramp down, and reporting a fade there would be the lie
/// `[PI3-API-030]` refuses.
/// **The local engine publishes nothing, and needs to.**
///
/// Lempi's own UI reads the decision store and the explanation log directly
/// `[REQ-VIS-100]`, so a sticker would be a second copy of something it already
/// has, kept somewhere it never looks. Publishing exists for *guests*, whose
/// clients have no other way to learn why a passage was chosen `[SPEC-MPD-050]`.
/// The engine is the clock, so there is nothing to refresh.
impl crate::switch::Progress for Engine {
    fn head_position(&self) -> Option<(i64, u64)> {
        Engine::head_position(self)
    }
    fn head_counted(&self) -> bool {
        Engine::head_counted(self)
    }
    fn adopt_counted(&mut self, passage_id: i64) {
        Engine::adopt_counted(self, passage_id)
    }
}

impl crate::switch::Publish for Engine {
    fn publish(&mut self, _p: &crate::switch::Published<'_>) {}
}

impl crate::switch::FadeOut for Engine {
    fn fade_out(&mut self, ms: u64) -> crate::switch::Stopped {
        if self.fade_to_silence(ms) {
            crate::switch::Stopped::Faded
        } else {
            crate::switch::Stopped::Cut
        }
    }

    /// The same fade, minus the suppression a skip would earn
    /// `[SPEC-BK-065]`.
    fn hand_off(&mut self, ms: u64) -> crate::switch::Stopped {
        if self.hand_off_to_silence(ms) {
            crate::switch::Stopped::Faded
        } else {
            crate::switch::Stopped::Cut
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Built out rather than defaulted: `QueueEntry` has no `Default`, and a
    /// spike is not a reason to add one to a production type.
    fn entry(start: u64, end: u64) -> QueueEntry {
        QueueEntry {
            qid: 0,
            passage_id: 1,
            path: std::path::PathBuf::from("/m/a.mp3"),
            start_ms: start,
            end_ms: end,
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

    /// A backend that is **not** the engine, to prove the seam is a seam.
    ///
    /// It holds ids and spans and nothing else — no decoder, no output, no
    /// path. If the Director can fill this, it can fill MPD `[SPEC-BK-020]`.
    struct StubBackend {
        depth: usize,
        queued: Vec<(i64, u64)>,
        dropped: Vec<i64>,
        caps: Capabilities,
    }

    impl Playback for StubBackend {
        fn capabilities(&self) -> Capabilities {
            self.caps
        }
        fn enqueue(&mut self, e: QueueEntry) {
            self.queued.push((e.passage_id, e.end_ms.saturating_sub(e.start_ms)));
        }
        fn queued_ids(&self) -> Vec<i64> {
            self.queued.iter().map(|(id, _)| *id).collect()
        }
        fn queued_ms(&self) -> u64 {
            self.queued.iter().map(|(_, ms)| *ms).sum()
        }
        fn shortfall(&self) -> usize {
            self.depth.saturating_sub(self.queued.len())
        }
        fn take_dropped(&mut self) -> Vec<i64> {
            std::mem::take(&mut self.dropped)
        }
        fn resume_at(&mut self, _position_ms: u64) {}
        fn tick(&mut self) -> usize {
            0
        }
        fn is_shutdown(&self) -> bool {
            false
        }
    }

    /// The refill pattern, run against something with no audio path at all.
    ///
    /// This is the whole claim of `[SPEC-BK-020]` in miniature: top up to
    /// depth, read back what is queued to exclude it from the next choice, and
    /// measure the lookahead in time. None of it touches `Engine`.
    #[test]
    fn a_session_can_fill_a_backend_that_is_not_the_engine() {
        let mut b = StubBackend {
            depth: 5,
            queued: Vec::new(),
            dropped: Vec::new(),
            caps: Capabilities::MPD,
        };
        let backend: &mut dyn Playback = &mut b;

        assert_eq!(backend.shortfall(), 5, "an empty queue wants the full depth");
        for i in 0..backend.shortfall() {
            let mut e = entry(0, 200_000);
            e.passage_id = 100 + i as i64;
            backend.enqueue(e);
        }
        assert_eq!(backend.shortfall(), 0, "filled to depth, and it stops");
        assert_eq!(backend.queued_ids(), vec![100, 101, 102, 103, 104]);
        assert_eq!(backend.queued_ms(), 5 * 200_000, "lookahead measured in time");
        assert!(!backend.capabilities().gain, "and it says what it cannot do");
    }

    /// A dropped passage reaches the Director the same way from any backend
    /// `[REQ-PD-112]`, and is reported once.
    #[test]
    fn a_backend_reports_what_it_could_not_play_exactly_once() {
        let mut b = StubBackend {
            depth: 2,
            queued: Vec::new(),
            dropped: vec![7, 9],
            caps: Capabilities::MPD,
        };
        let backend: &mut dyn Playback = &mut b;
        assert_eq!(backend.take_dropped(), vec![7, 9]);
        assert!(backend.take_dropped().is_empty(), "taking clears them");
    }

    #[test]
    fn a_full_backend_never_misplays() {
        assert!(!Capabilities::FULL.would_misplay(&entry(30_000, 240_000), 600_000));
    }

    #[test]
    fn a_whole_file_backend_is_fine_with_whole_file_passages() {
        // The `ingest:whole-file` case, and most of a single-track library.
        assert!(!Capabilities::WHOLE_FILE.would_misplay(&entry(0, 284_250), 284_250));
    }

    #[test]
    fn a_whole_file_backend_would_misplay_a_dao_slice() {
        // Track 7 of a 40-track capture: naming the file plays the whole hour.
        assert!(Capabilities::WHOLE_FILE.would_misplay(&entry(1_200_000, 1_440_000), 8_000_000));
    }

    #[test]
    fn mpd_keeps_the_dao_slice_that_subsonic_loses() {
        // The same passage, to two backends. Measured on the real library:
        // 98.6% of radio passages are trimmed slices, so this is the common
        // case and not an edge one.
        let dao = entry(1_200_000, 1_440_000);
        assert!(!Capabilities::MPD.would_misplay(&dao, 8_000_000));
        assert!(Capabilities::WHOLE_FILE.would_misplay(&dao, 8_000_000));
    }

    #[test]
    fn trailing_slack_still_counts_as_whole() {
        // A trim that stops a few seconds early is not a slice worth refusing.
        assert!(!Capabilities::WHOLE_FILE.would_misplay(&entry(0, 280_000), 284_250));
    }
}
