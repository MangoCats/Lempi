//! What the listener is hearing, by output sample `[REQ-VIS-240]`,
//! `[REQ-AUD-164]`.
//!
//! The mixer runs a ring's depth -- about 15 s -- ahead of the device, so "the
//! passage being mixed" and "the passage being heard" part company at every
//! transition. This records, as each block is submitted, which passage
//! contributed to which output samples and at what position in it. The device's
//! read position then answers what is audible directly: through a crossfade,
//! the drain after a passage leaves the mixer, a pause (the read position does
//! not move), an echo gap (no passage covers it) and a cut (the samples it
//! discards are cut here too).
//!
//! Sample indices count from the ring's creation and never go back: a cut
//! moves the *write* position back, never the read one.

use std::collections::VecDeque;

/// One passage's contribution to a run of output samples.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Segment {
    pub passage: i64,
    /// When this passage was admitted, in order: in an overlap, the later
    /// admission is the one shown, as it always was `[REQ-AUD-164]`.
    pub admitted: u64,
    /// Output samples `[from, to)`.
    pub from: u64,
    pub to: u64,
    /// The passage's own position at `from`, in milliseconds.
    pub at_ms: u64,
    pub channels: u32,
    pub rate: u32,
}

impl Segment {
    fn ms_at(&self, sample: u64) -> u64 {
        let frames = sample.saturating_sub(self.from) / self.channels.max(1) as u64;
        self.at_ms + frames * 1000 / self.rate.max(1) as u64
    }
}

/// What is audible at a read position.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Audible {
    pub passage: i64,
    pub admitted: u64,
    pub position_ms: u64,
    /// No segment covers the read position: this passage was the last heard,
    /// and `position_ms` is where it stopped -- an echo gap, or a queue that
    /// ran out.
    pub ended: bool,
}

#[derive(Debug, Default)]
pub(crate) struct Timeline {
    segments: VecDeque<Segment>,
}

impl Timeline {
    /// A passage's samples, laid at `[from, to)` from position `at_ms`. Joined
    /// to the passage's previous segment when it continues it exactly, so a
    /// passage mixed block by block is usually one segment.
    pub fn record(&mut self, s: Segment) {
        if s.to <= s.from {
            return;
        }
        if let Some(last) = self
            .segments
            .iter_mut()
            .rev()
            .find(|x| x.passage == s.passage && x.admitted == s.admitted)
        {
            if last.to == s.from
                && last.channels == s.channels
                && last.rate == s.rate
                && last.ms_at(s.from) == s.at_ms
            {
                last.to = s.to;
                return;
            }
        }
        self.segments.push_back(s);
    }

    /// Everything at or after `at` was discarded from the ring.
    pub fn cut(&mut self, at: u64) {
        self.segments.retain(|s| s.from < at);
        for s in self.segments.iter_mut() {
            s.to = s.to.min(at);
        }
    }

    /// How far into its passage an admission has been heard at `read`: where
    /// a segment of it covers `read`, or, if all of it is behind `read`, the
    /// end of the last of it. `None` if nothing of it has been heard.
    pub fn heard_to(&self, admitted: u64, read: u64) -> Option<u64> {
        let mine = || self.segments.iter().filter(move |s| s.admitted == admitted);
        if let Some(s) = mine().find(|s| s.from <= read && read < s.to) {
            return Some(s.ms_at(read));
        }
        mine().filter(|s| s.to <= read).max_by_key(|s| s.to).map(|s| s.ms_at(s.to))
    }

    /// Whether this admission still has a segment: still to be heard, or the
    /// last heard.
    pub fn holds(&self, admitted: u64) -> bool {
        self.segments.iter().any(|s| s.admitted == admitted)
    }

    /// Forget everything: the ring was replaced, and its sample count with it.
    pub fn reset(&mut self) {
        self.segments.clear();
    }

    /// The last sample any segment reaches, or 0.
    #[cfg(test)]
    pub fn end(&self) -> u64 {
        self.segments.iter().map(|s| s.to).max().unwrap_or(0)
    }

    /// What is audible at read position `read`: the latest-admitted passage
    /// covering it, or, where none does, the one that ended last.
    pub fn audible_at(&self, read: u64) -> Option<Audible> {
        let covering = self
            .segments
            .iter()
            .filter(|s| s.from <= read && read < s.to)
            .max_by_key(|s| s.admitted);
        if let Some(s) = covering {
            return Some(Audible {
                passage: s.passage,
                admitted: s.admitted,
                position_ms: s.ms_at(read),
                ended: false,
            });
        }
        self.segments
            .iter()
            .filter(|s| s.to <= read)
            .max_by_key(|s| (s.to, s.admitted))
            .map(|s| Audible {
                passage: s.passage,
                admitted: s.admitted,
                position_ms: s.ms_at(s.to),
                ended: true,
            })
    }

    /// Drop what has been heard, keeping the last-ended segment so a gap can
    /// still say what came before it.
    pub fn prune(&mut self, read: u64) {
        let keep = self
            .segments
            .iter()
            .filter(|s| s.to <= read)
            .map(|s| s.to)
            .max();
        self.segments.retain(|s| s.to > read || Some(s.to) == keep);
    }

    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.segments.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Stereo at 1 kHz, so one frame is one millisecond and two samples.
    fn seg(passage: i64, admitted: u64, from: u64, to: u64, at_ms: u64) -> Segment {
        Segment { passage, admitted, from, to, at_ms, channels: 2, rate: 1000 }
    }

    #[test]
    fn blocks_of_one_passage_join_and_positions_follow_the_read() {
        let mut t = Timeline::default();
        t.record(seg(7, 1, 0, 200, 0));
        t.record(seg(7, 1, 200, 400, 100));
        assert_eq!(t.len(), 1, "a continuous passage is one segment");
        let a = t.audible_at(250).unwrap();
        assert_eq!((a.passage, a.position_ms, a.ended), (7, 125, false));
    }

    #[test]
    fn in_a_crossfade_the_later_admission_is_shown_once_it_sounds() {
        let mut t = Timeline::default();
        t.record(seg(1, 1, 0, 1000, 60_000));
        t.record(seg(2, 2, 600, 1400, 0));
        assert_eq!(t.audible_at(500).unwrap().passage, 1, "before the newcomer sounds");
        let a = t.audible_at(800).unwrap();
        assert_eq!((a.passage, a.position_ms), (2, 100), "once it sounds, it is shown");
    }

    #[test]
    fn a_gap_shows_what_came_before_it_stopped_at_its_end() {
        let mut t = Timeline::default();
        t.record(seg(1, 1, 0, 200, 0));
        t.record(seg(2, 2, 300, 500, 0));
        let a = t.audible_at(250).unwrap();
        assert_eq!((a.passage, a.position_ms, a.ended), (1, 100, true));
        assert_eq!(t.audible_at(300).unwrap().passage, 2);
    }

    #[test]
    fn a_cut_discards_what_was_never_heard() {
        let mut t = Timeline::default();
        t.record(seg(1, 1, 0, 1000, 0));
        t.record(seg(2, 2, 800, 1200, 0));
        t.cut(500);
        assert_eq!(t.end(), 500);
        assert_eq!(t.audible_at(499).unwrap().passage, 1, "the newcomer never reached the ring's head");
        assert!(t.audible_at(700).unwrap().ended);
    }

    #[test]
    fn a_trimmed_block_starts_a_new_segment_rather_than_skewing() {
        let mut t = Timeline::default();
        t.record(seg(1, 1, 0, 200, 0));
        // A dropped frame: the next block's audio is one millisecond further on.
        t.record(seg(1, 1, 200, 400, 101));
        assert_eq!(t.len(), 2);
        assert_eq!(t.audible_at(200).unwrap().position_ms, 101);
    }

    #[test]
    fn pruning_keeps_the_last_heard_for_a_gap() {
        let mut t = Timeline::default();
        t.record(seg(1, 1, 0, 200, 0));
        t.record(seg(2, 2, 200, 400, 0));
        t.record(seg(3, 3, 500, 600, 0));
        t.prune(450);
        assert_eq!(t.len(), 2, "1 is gone; 2 stays to name the gap; 3 is to come");
        assert_eq!(t.audible_at(450).unwrap().passage, 2);
    }
}
