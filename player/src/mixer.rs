//! Ring buffer and mixer.
//!
//! Deliberately small. Per `[XFD-ORTH-020]` the mixer performs **no** gain
//! calculation: fades are applied by [`crate::fade`] before audio is buffered,
//! so mixing a crossfade is addition. Resisting the urge to put "just a little"
//! gain logic here is what keeps the fade math in one place.

use crate::fade::Envelope;

/// Drop or duplicate one frame in a freshly mixed block `[GDE-ECHO-340]`.
///
/// Returns the number of samples to submit. Dropping hands back one frame
/// fewer, discarding audio the mixer already consumed, so position advances
/// faster than time; duplicating repeats the last frame, so it advances
/// slower. One frame is 23 microseconds at 44.1 kHz -- below anything a
/// listener can hear as an event, which is the whole reason the *rate*
/// correction is allowed to happen mid-passage where the offset correction is
/// not.
///
/// Refuses rather than forces in two cases, both of which would otherwise
/// corrupt a block: a drop that would empty an already-tiny block, and a
/// duplicate with no room to put the extra frame. A trim skipped now simply
/// happens on the next pass, a few milliseconds later, and nothing accumulates
/// -- the schedule is a rate, not a queue of debts.
pub fn apply_trim(buf: &mut [f32], filled: usize, channels: usize, drop_frame: bool) -> usize {
    let ch = channels.max(1);
    if filled < 2 * ch || !filled.is_multiple_of(ch) {
        return filled;
    }
    if drop_frame {
        return filled - ch;
    }
    if filled + ch > buf.len() {
        return filled;
    }
    let (last, next) = (filled - ch, filled);
    for i in 0..ch {
        buf[next + i] = buf[last + i];
    }
    filled + ch
}

/// Fixed-capacity FIFO of interleaved f32 samples.
///
/// Capacity is set once and never grows -- that is the whole point
/// `[GDE-FBD-010]`. Writes that would exceed capacity are truncated and
/// reported, so a producer outrunning the consumer is visible rather than
/// silently ballooning memory.
pub struct RingBuffer {
    buf: Box<[f32]>,
    head: usize,
    len: usize,
}

impl RingBuffer {
    pub fn new(capacity_samples: usize) -> Self {
        Self { buf: vec![0.0; capacity_samples].into_boxed_slice(), head: 0, len: 0 }
    }

    /// Discard everything buffered, keeping the allocation.
    ///
    /// For recovery after an output failure: what the ring holds then is audio
    /// mixed for a moment already several seconds gone, and playing it out on
    /// reconnection would replay that moment `[IMPL-AUD-020]`. The samples are
    /// left in place because nothing reads past `len`.
    pub fn clear(&mut self) {
        self.head = 0;
        self.len = 0;
    }

    pub fn capacity(&self) -> usize {
        self.buf.len()
    }
    pub fn len(&self) -> usize {
        self.len
    }
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }
    pub fn free(&self) -> usize {
        self.buf.len() - self.len
    }

    /// Append what fits. Returns the number of samples written, which is less
    /// than `src.len()` when the buffer is full.
    pub fn write(&mut self, src: &[f32]) -> usize {
        let n = src.len().min(self.free());
        let cap = self.buf.len();
        let tail = (self.head + self.len) % cap;
        let first = n.min(cap - tail);
        self.buf[tail..tail + first].copy_from_slice(&src[..first]);
        if n > first {
            self.buf[..n - first].copy_from_slice(&src[first..n]);
        }
        self.len += n;
        n
    }

    /// Keep at most `n` samples and discard everything queued behind them.
    ///
    /// The ring is a head and a length, so dropping the tail is one assignment
    /// -- no copying, no reallocation, safe to call from under the output lock.
    /// Returns what is left.
    pub fn truncate(&mut self, n: usize) -> usize {
        self.len = self.len.min(n);
        self.len
    }

    /// Remove up to `dst.len()` samples into `dst`, returning how many.
    pub fn read(&mut self, dst: &mut [f32]) -> usize {
        let n = dst.len().min(self.len);
        let cap = self.buf.len();
        let first = n.min(cap - self.head);
        dst[..first].copy_from_slice(&self.buf[self.head..self.head + first]);
        if n > first {
            dst[first..n].copy_from_slice(&self.buf[..n - first]);
        }
        self.head = (self.head + n) % cap;
        self.len -= n;
        n
    }

    /// The buffered samples, as the two contiguous runs a ring stores them in.
    ///
    /// For editing audio that has already been submitted -- which is the only
    /// way to reach it, since the callback owns everything downstream. The
    /// second run is the wrapped part and is empty unless the data straddles
    /// the end of the backing store.
    pub fn as_mut_slices(&mut self) -> (&mut [f32], &mut [f32]) {
        let cap = self.buf.len();
        let first = self.len.min(cap - self.head);
        let (start, from_head) = self.buf.split_at_mut(self.head);
        (&mut from_head[..first], &mut start[..self.len - first])
    }

    /// Add `src` into the buffer starting `offset` samples from the read point,
    /// summing where audio is already queued and appending past the end.
    ///
    /// This is how a passage is laid OVER audio that has already been
    /// submitted `[REQ-AUD-162]`. `write` cannot do it: that appends, and the
    /// whole point is to overlap. Any gap between the current end and `offset`
    /// is filled with silence, so the offset means what it says even when the
    /// buffer holds less than that.
    ///
    /// Returns the samples placed, which is short of `src.len()` only when the
    /// buffer runs out of capacity.
    pub fn mix_at(&mut self, offset: usize, src: &[f32]) -> usize {
        let cap = self.buf.len();
        while self.len < offset && self.len < cap {
            self.buf[(self.head + self.len) % cap] = 0.0;
            self.len += 1;
        }
        let mut placed = 0;
        for (i, s) in src.iter().enumerate() {
            let pos = offset + i;
            if pos < self.len {
                let p = (self.head + pos) % cap;
                self.buf[p] += *s;
            } else if self.len < cap {
                let p = (self.head + self.len) % cap;
                self.buf[p] = *s;
                self.len += 1;
            } else {
                break;
            }
            placed += 1;
        }
        placed
    }

    /// Add up to `dst.len()` samples into `dst` rather than overwriting.
    ///
    /// This is the crossfade primitive: two passages summed into one output
    /// `[XFD-BEH-C1-020]`. Samples are consumed either way.
    pub fn mix_into(&mut self, dst: &mut [f32]) -> usize {
        let n = dst.len().min(self.len);
        let cap = self.buf.len();
        for (i, out) in dst.iter_mut().enumerate().take(n) {
            *out += self.buf[(self.head + i) % cap];
        }
        self.head = (self.head + n) % cap;
        self.len -= n;
        n
    }
}

/// One passage's buffered, already-faded audio.
pub struct Stream {
    pub ring: RingBuffer,
    /// Frames written so far, which is the envelope's position. Tracked
    /// here because it is applied on the way *in*.
    pub frames_written: u64,
    /// The passage's own fade-in/fade-out, independent of `Fade` above --
    /// that one stays reserved for Skip/Handoff's own retroactive cut
    /// `[SPEC-SUI-226]`.
    pub envelope: Envelope,
    pub channels: usize,
    /// No more audio will be produced; the stream ends when the ring drains.
    pub finished: bool,
}

impl Stream {
    pub fn new(capacity_samples: usize, channels: usize, envelope: Envelope) -> Self {
        Self {
            ring: RingBuffer::new(capacity_samples),
            frames_written: 0,
            envelope,
            channels,
            finished: false,
        }
    }

    /// Apply this stream's envelope to `samples` and buffer the result.
    ///
    /// The only path by which audio enters a stream, so audio in the ring is
    /// always already faded -- the invariant the mixer relies on.
    pub fn push(&mut self, samples: &mut [f32]) -> usize {
        self.envelope.apply(samples, self.channels, self.frames_written);
        let written = self.ring.write(samples);
        self.frames_written += (written / self.channels.max(1)) as u64;
        written
    }

    pub fn is_exhausted(&self) -> bool {
        self.finished && self.ring.is_empty()
    }
}

/// Sum active streams into `out`, returning how many samples had a contributor.
///
/// A free function, not a struct: the mixer needs no state, and making it own
/// its streams forced callers to hand ownership away just to mix -- which made
/// feeding a decoder into an already-mixing stream impossible. Streams stay
/// with whoever is filling them; mixing borrows them for a block.
///
/// Holds no gain, no curves and no timing policy: those belong to the fader and
/// the queue. Keeping it this dumb is what makes crossfade testable in one place.
/// Takes any iterator of `&mut Stream` rather than a slice, so callers that
/// keep a stream beside its decoder can pass `.iter_mut().map(|(_, s)| s)`
/// instead of shuffling ownership to satisfy the signature.
pub fn mix<'a, I>(streams: I, out: &mut [f32]) -> usize
where
    I: IntoIterator<Item = &'a mut Stream>,
{
    out.iter_mut().for_each(|s| *s = 0.0);
    let mut filled = 0;
    for s in streams {
        filled = filled.max(s.ring.mix_into(out));
    }
    filled
}

/// Drop streams that have finished and drained.
pub fn retain_active(streams: &mut Vec<Stream>) {
    streams.retain(|s| !s.is_exhausted());
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The overlay sums where audio is already queued and appends past the end
    /// -- one call spanning both, because the incoming passage straddles the
    /// end of what the outgoing one left behind.
    /// One frame either way, and the sample values say which.
    #[test]
    fn a_trim_removes_or_repeats_exactly_one_frame() {
        // Stereo: [L0 R0 L1 R1 L2 R2] with room for one more frame.
        let mut buf = [1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 0.0, 0.0];
        assert_eq!(apply_trim(&mut buf, 6, 2, true), 4, "a dropped frame is simply not sent");
        let mut buf = [1.0, -1.0, 2.0, -2.0, 3.0, -3.0, 0.0, 0.0];
        assert_eq!(apply_trim(&mut buf, 6, 2, false), 8);
        assert_eq!(&buf[6..8], &[3.0, -3.0], "the repeated frame is the last one");
    }

    /// Both refusals `[GDE-ECHO-340]`: a trim skipped now happens on the next
    /// pass, so refusing costs nothing and forcing would corrupt the block.
    ///
    /// **The first case below used to be the production one, and nobody
    /// connected the two numbers** `[GDE-ECHO-373]`. `filled` equal to
    /// `buf.len()` is what the engine handed in on every stereo pass, so this
    /// test asserted -- correctly, and uselessly -- that the duplicate half of
    /// the endgame never fired. The engine now sizes its scratch one frame
    /// longer than the block it mixes (`Engine::MIX_FRAMES`), so the refusal
    /// stays a real guard against a caller that gets it wrong rather than a
    /// description of the caller that exists.
    #[test]
    fn a_trim_refuses_rather_than_corrupting_a_block() {
        let mut buf = [1.0, -1.0, 2.0, -2.0];
        assert_eq!(apply_trim(&mut buf, 4, 2, false), 4, "nowhere to put it");
        let mut buf = [1.0, -1.0, 0.0, 0.0];
        assert_eq!(apply_trim(&mut buf, 2, 2, true), 2, "one frame is not droppable");
        // A partial frame is never touched; that would offset every sample after.
        let mut buf = [1.0, -1.0, 2.0, 0.0];
        assert_eq!(apply_trim(&mut buf, 3, 2, true), 3);
    }

    #[test]
    fn mix_at_sums_over_existing_audio_and_appends_past_it() {
        let mut r = RingBuffer::new(64);
        r.write(&[1.0; 20]);
        let placed = r.mix_at(15, &[0.5; 10]);
        assert_eq!(placed, 10);
        assert_eq!(r.len(), 25, "five summed, five appended");
        let mut out = [0.0f32; 25];
        r.read(&mut out);
        assert_eq!(out[14], 1.0, "before the overlay, untouched");
        assert_eq!(out[15], 1.5, "summed, not overwritten");
        assert_eq!(out[19], 1.5);
        assert_eq!(out[20], 0.5, "past the end, appended");
    }

    /// The offset must mean the same thing after the buffer has wrapped, which
    /// it will have done: the output ring runs for the life of the process.
    #[test]
    fn mix_at_is_correct_across_the_wrap() {
        let mut r = RingBuffer::new(16);
        r.write(&[9.0; 12]);
        let mut sink = [0.0f32; 10];
        r.read(&mut sink); // head now at 10, two samples left
        r.write(&[1.0; 8]); // straddles the end of the backing store
        assert_eq!(r.len(), 10);
        let placed = r.mix_at(4, &[0.5; 4]);
        assert_eq!(placed, 4);
        let mut out = [0.0f32; 10];
        r.read(&mut out);
        assert_eq!(out[3], 1.0, "before the overlay");
        assert_eq!(out[4], 1.5, "summed across the wrap");
        assert_eq!(out[7], 1.5);
        assert_eq!(out[8], 1.0, "after it");
    }

    /// A gap between the end of the audio and the offset is silence, so the
    /// offset means what it says even when the ring is nearly empty.
    #[test]
    fn mix_at_pads_when_the_offset_is_beyond_the_end() {
        let mut r = RingBuffer::new(32);
        r.write(&[1.0; 2]);
        r.mix_at(5, &[0.5; 3]);
        assert_eq!(r.len(), 8);
        let mut out = [0.0f32; 8];
        r.read(&mut out);
        assert_eq!(&out[2..5], &[0.0, 0.0, 0.0], "silence, not stale audio");
        assert_eq!(out[5], 0.5);
    }

    /// The shape of a skip: the outgoing passage falls away, the incoming one
    /// arrives part-way down, and for the difference they are summed. This is
    /// the property that distinguishes it from a stop followed by a start.
    #[test]
    fn a_skip_transition_overlaps_the_two_passages() {
        let mut r = RingBuffer::new(64);
        r.write(&[1.0; 40]);
        assert_eq!(r.truncate(20), 20, "the backlog is cut to the fade");
        let fade = Fade { curve: Curve::Linear, frames: 20, fade_in: false };
        {
            let (front, back) = r.as_mut_slices();
            fade.apply(front, 1, 0);
            let wrapped_at = front.len() as u64;
            fade.apply(back, 1, wrapped_at);
        }
        r.mix_at(5, &[0.5; 25]);

        let mut out = [0.0f32; 30];
        assert_eq!(r.read(&mut out), 30);
        assert!((out[0] - 1.0).abs() < 1e-6, "outgoing starts where it was");
        assert!((out[4] - 0.8).abs() < 1e-6, "and is alone until the lead");
        assert!((out[5] - (0.75 + 0.5)).abs() < 1e-6, "then the two are summed");
        assert!((out[19] - (0.05 + 0.5)).abs() < 1e-6, "still summed at the end");
        assert!((out[20] - 0.5).abs() < 1e-6, "outgoing gone, incoming alone");
        // The outgoing part must fall monotonically the whole way.
        let outgoing: Vec<f32> = (0..5).map(|i| out[i]).collect();
        assert!(outgoing.windows(2).all(|w| w[1] < w[0]), "{outgoing:?}");
    }
    use crate::fade::{Curve, Fade};

    #[test]
    fn ring_clear_discards_stale_audio_and_still_wraps() {
        let mut r = RingBuffer::new(8);
        r.write(&[1.0, 2.0, 3.0, 4.0, 5.0]);
        let mut out = [0.0; 3];
        r.read(&mut out);                 // leave head mid-buffer, as in life
        r.clear();
        assert_eq!(r.len(), 0);
        let mut nothing = [9.9; 4];
        assert_eq!(r.read(&mut nothing), 0, "cleared ring must yield nothing");
        assert_eq!(nothing, [9.9; 4], "and must not touch the caller's buffer");

        // The reason this matters: after an output failure the ring holds audio
        // from a moment now several seconds gone, and playing it on
        // reconnection is a stutter rather than a gap `[IMPL-AUD-020]`. A clear
        // that only reset the length would replay it on the next wrap.
        assert_eq!(r.write(&[1.0; 8]), 8, "full capacity available after clear");
        let mut all = [0.0; 8];
        assert_eq!(r.read(&mut all), 8);
        assert_eq!(all, [1.0; 8], "no stale samples resurface");
    }

    /// **Waiting does not delay the sound, and this is why silence has to be
    /// an actuator** `[GDE-ARC-052]`.
    ///
    /// The ring is contiguous, so audio written later lands immediately after
    /// audio written earlier however long the producer paused in between. If
    /// the outgoing passage's last sample is submitted at `T0` sitting `D0`
    /// deep, it airs at `T0 + D0`; submit the incoming at `T1` and the ring
    /// has drained by `T1 - T0`, so sample 0 airs at
    /// `T1 + (D0 - (T1 - T0))` = `T0 + D0` — the same instant, whatever `T1`
    /// is. Delaying admission only makes the buffer shallower.
    ///
    /// So a follower running *ahead* cannot wait by waiting. The only way to
    /// move audio later in a contiguous ring is to put something in front of
    /// it, and the only thing that can go there without inventing content is
    /// silence.
    #[test]
    fn a_pause_in_writing_leaves_no_gap_in_the_audio() {
        let mut r = RingBuffer::new(64);
        r.write(&[1.0; 20]);            // the outgoing passage
        let mut drained = [0.0f32; 8];
        r.read(&mut drained);           // the device plays some of it
        // The producer now pauses -- writes nothing at all for a while --
        // and only then submits the incoming passage.
        r.write(&[2.0; 10]);
        let mut out = [0.0f32; 22];
        assert_eq!(r.read(&mut out), 22);
        // Twelve samples of the outgoing remained, and the incoming follows
        // them immediately. Nothing separates the two.
        assert!(out[..12].iter().all(|v| *v == 1.0), "the outgoing's remainder");
        assert!(out[12..].iter().all(|v| *v == 2.0), "and the incoming, butted \
straight against it -- the pause bought no delay at all");
    }

    #[test]
    fn ring_wraps_without_loss() {
        let mut r = RingBuffer::new(8);
        assert_eq!(r.write(&[1.0, 2.0, 3.0, 4.0, 5.0]), 5);
        let mut out = [0.0; 3];
        assert_eq!(r.read(&mut out), 3);
        assert_eq!(out, [1.0, 2.0, 3.0]);
        // wraps past the physical end
        assert_eq!(r.write(&[6.0, 7.0, 8.0, 9.0, 10.0]), 5);
        let mut all = [0.0; 7];
        assert_eq!(r.read(&mut all), 7);
        assert_eq!(all, [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]);
    }

    #[test]
    fn ring_never_exceeds_capacity() {
        let mut r = RingBuffer::new(4);
        assert_eq!(r.write(&[1.0; 10]), 4, "must truncate, not grow");
        assert_eq!(r.len(), 4);
        assert_eq!(r.capacity(), 4);
    }

    #[test]
    fn mix_sums_two_streams() {
        let mut streams: Vec<Stream> = [0.25f32, 0.5]
            .iter()
            .map(|v| {
                let mut s = Stream::new(16, 2, Envelope::none());
                s.push(&mut [*v; 8]);
                s.finished = true;
                s
            })
            .collect();
        let mut out = [0.0; 8];
        assert_eq!(mix(streams.iter_mut(), &mut out), 8);
        assert!(out.iter().all(|x| (*x - 0.75).abs() < 1e-6), "got {out:?}");
    }

    #[test]
    fn exhausted_streams_are_dropped() {
        let mut s = Stream::new(8, 2, Envelope::none());
        s.push(&mut [1.0; 4]);
        s.finished = true;
        let mut streams = vec![s];
        let mut out = [0.0; 8];
        mix(streams.iter_mut(), &mut out);
        retain_active(&mut streams);
        assert!(streams.is_empty(), "drained stream must not linger");
    }

    #[test]
    fn audio_is_faded_on_the_way_in_not_at_mix_time() {
        let envelope = Envelope {
            fade_in: Fade { curve: Curve::Linear, frames: 8, fade_in: true },
            fade_out: Fade::none(),
            total_frames: 8,
        };
        let mut s = Stream::new(64, 1, envelope);
        let mut block = vec![1.0f32; 8];
        s.push(&mut block);
        let mut out = [0.0; 8];
        s.ring.read(&mut out);
        assert!(out[0] < out[7], "ring must already hold faded audio");
        assert!(out[0].abs() < 1e-6, "fade-in starts silent");
    }

    /// The other half of the same claim: a stream configured with only a
    /// fade-*out* plays full volume until its own fade-out region, then
    /// falls -- ordinary end-of-passage playback now genuinely ramps the
    /// outgoing gain down, which nothing in the engine did before this
    /// `[SPEC-SUI-226]`.
    #[test]
    fn a_fade_out_only_envelope_plays_full_volume_then_falls() {
        let envelope = Envelope {
            fade_in: Fade::none(),
            fade_out: Fade { curve: Curve::Linear, frames: 4, fade_in: false },
            total_frames: 8,
        };
        let mut s = Stream::new(64, 1, envelope);
        let mut block = vec![1.0f32; 8]; // the whole passage, pushed in one call
        s.push(&mut block);
        let mut out = [0.0; 8];
        s.ring.read(&mut out);
        // fade_out_start = 8 - 4 = 4: frames 0-3 are before the fade-out
        // region and stay full volume; frame 4 is t=0 of a linear fade-out
        // (still exactly full volume, the fade's own starting point); frames
        // 5-7 fall linearly from there.
        assert!((out[0] - 1.0).abs() < 1e-6, "full volume before the fade-out region begins");
        assert!((out[4] - 1.0).abs() < 1e-6, "still full volume at the fade-out's own t=0");
        assert!(out[5] < out[4], "falling from here on -- into the fade-out region itself");
        assert!(out[7] < out[5], "still falling toward the passage's own last frame");
    }

    #[test]
    fn silence_where_no_stream_contributes() {
        let mut out = [9.9; 4];
        assert_eq!(mix(std::iter::empty(), &mut out), 0);
        assert!(out.iter().all(|s| *s == 0.0), "must clear, not leave stale audio");
    }
}
