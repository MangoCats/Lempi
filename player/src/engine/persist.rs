//! Persistence and play-history bookkeeping for [`super::Engine`]: settings,
//! the resume point, and the play/rejection history a passage earns as it
//! departs. Split out of the tick/mixing methods in `engine/mod.rs` because
//! none of this runs on every sample -- it is triggered by a setting change,
//! a passage departing, or the periodic (and shutdown) save -- unlike the
//! tick itself, which `[GDE-FBD-090]` forbids ever blocking.
//!
//! A child module of `engine`, not a sibling: every method below reads or
//! writes `Engine`'s private fields directly, the same access the methods in
//! `mod.rs` have -- Rust privacy reaches from a module down into its
//! descendants. The reverse direction does not hold, which is why every
//! method here callable from `mod.rs`'s own tick/mixing methods is
//! `pub(super)` rather than left private: a child can see its parent's
//! private items, but not the other way around.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::time::{Duration, Instant};

use super::Engine;

/// The head entry's own identity and provenance, snapshotted once at the
/// top of `record_play` before any bookkeeping needs `&mut self` -- id,
/// mbid, audible position, span, and who selected it `[REQ-VIS-300]`.
/// Named so clippy's own `type_complexity` lint has nothing to flag and a
/// reader has a label for what would otherwise be five bare fields in a row.
type HeadNow = (i64, Option<String>, u64, u64, Option<String>);

impl Engine {
    /// Write the settings down, now rather than on a timer.
    ///
    /// They change when a hand moves a control, which is rare and deliberate,
    /// and a setting that survives everything except the crash that happens
    /// before the next tick is not really saved. Best-effort: failing to
    /// record a volume must never interrupt the music.
    pub(super) fn remember_settings(&self) {
        if let Some(store) = &self.store {
            if let Err(e) = store.save_settings(&self.settings()) {
                tracing::error!("save settings: {e}");
            }
        }
    }

    /// The listener's settings as the engine currently holds them.
    pub fn settings(&self) -> crate::db::Settings {
        crate::db::Settings {
            volume: self.volume,
            skip_fade_ms: self.skip_fade_ms,
            skip_lead_ms: self.skip_lead_ms,
            resume_save_ms: self.prefs.resume_save_ms,
            skip_suppress_h: self.prefs.skip_suppress_h,
            dequeue_suppress_h: self.prefs.dequeue_suppress_h,
            queue_depth: self.queue.min_depth,
            sample_interval_ms: self.prefs.sample_interval_ms,
            cue_sheets: self.prefs.cue_sheets,
            covers: self.prefs.covers,
            lyrics_cache: self.prefs.lyrics_cache,
            lyrics_sidecar: self.prefs.lyrics_sidecar,
            echo_delay_trim_ms: self.echo.delay_trim_ms,
            echo_follow_host: self.echo.follow_host.clone(),
            echo_join_now: self.echo.join_now,
            echo_join_bias: self.echo.join_bias.encode(),
        }
    }

    /// Put back what was last chosen. Clamped on the way in, because a value
    /// from disk deserves no more trust than one from the network.
    pub fn apply_settings(&mut self, s: &crate::db::Settings) {
        self.prefs.resume_save_ms =
            s.resume_save_ms.clamp(crate::RESUME_SAVE_MIN_MS, crate::RESUME_SAVE_MAX_MS);
        self.prefs.skip_suppress_h =
            s.skip_suppress_h.clamp(crate::SKIP_SUPPRESS_MIN_H, crate::SKIP_SUPPRESS_MAX_H);
        self.prefs.dequeue_suppress_h = s
            .dequeue_suppress_h
            .clamp(crate::DEQUEUE_SUPPRESS_MIN_H, crate::DEQUEUE_SUPPRESS_MAX_H);
        // The queue depth is a listener setting now, not a launch flag
        // `[SPEC-MPD-105]`, and it governs this engine as much as the MPD one.
        self.queue.min_depth =
            s.queue_depth.clamp(crate::QUEUE_DEPTH_MIN, crate::QUEUE_DEPTH_MAX);
        self.prefs.sample_interval_ms = s
            .sample_interval_ms
            .clamp(crate::SAMPLE_INTERVAL_MIN_MS, crate::SAMPLE_INTERVAL_MAX_MS);
        self.prefs.cue_sheets = s.cue_sheets;
        self.prefs.covers = s.covers;
        self.prefs.lyrics_cache = s.lyrics_cache;
        self.prefs.lyrics_sidecar = s.lyrics_sidecar;
        self.echo.delay_trim_ms = s.echo_delay_trim_ms;
        self.echo.follow_host = s.echo_follow_host.clone();
        self.echo.join_now = s.echo_join_now;
        self.echo.join_bias = crate::echo::JoinBias::decode(&s.echo_join_bias);
        self.volume = s.volume.clamp(0.0, 1.0);
        if let Some(r) = &self.path.ring {
            r.volume.set(self.volume);
        }
        self.skip_fade_ms = s.skip_fade_ms.min(crate::SKIP_FADE_MAX_MS);
        self.skip_lead_ms =
            s.skip_lead_ms.clamp(crate::SKIP_LEAD_MIN_MS, crate::SKIP_LEAD_MAX_MS);
    }

    /// Write the resume point. Throttled, because a tick is sub-millisecond and
    /// an SQLite write per tick would dominate the loop; `force` bypasses it for
    /// shutdown. Writes immediately when the passage or play state changes, so
    /// the interesting transitions are never the ones lost to a power cut.
    pub(super) fn persist(&mut self, force: bool) {
        if self.shutdown_saved {
            return;
        }
        let Some(store) = &self.store else { return };
        let key = (self.live.first().map(|l| l.entry.passage_id).unwrap_or(-1), self.playing);
        let changed = self.saved != Some(key);
        let every = Duration::from_millis(self.prefs.resume_save_ms);
        if !force && !changed && self.last_save.elapsed() < every {
            return;
        }
        let (id, pos) = match self.live.first() {
            Some(l) => (Some(l.entry.passage_id), self.audible_ms(l)),
            None => (None, 0),
        };
        if let Err(e) = store.save(id, pos, self.playing) {
            tracing::error!("save player state: {e}");
        }
        self.last_save = Instant::now();
        self.saved = Some(key);
    }

    /// Write a play to history once enough of the passage has been heard
    /// `[SPEC-PLAY-010]`, `[SPEC-PLAY-030]`.
    ///
    /// **Not at the start of playback.** *(Changed 2026-08-21.)* This used to
    /// write the moment a passage began sounding, following MuLibPlay, whose
    /// note says history updates "as each new track finishes playing (or is put
    /// in the play queue)" — and it argued that a passage skipped after ten
    /// seconds had been *encountered*, so suppressing it was wanted.
    ///
    /// That is now a **measured divergence from MuLibPlay** `[GDE-PHS-030]`.
    /// The threshold is half the passage or four minutes, the same rule the MPD
    /// path judges by and the same one Last.fm and ListenBrainz use, because
    /// both paths write this one table and it cannot mean two things
    /// `[SPEC-PLAY-030]`.
    ///
    /// Measured against **audible** position, net of output buffering: what the
    /// listener heard, not what the decoder reached.
    ///
    /// A failure here must never interrupt playback: history is what the next
    /// selection reads, not what this one depends on.
    pub(super) fn record_play(&mut self) {
        if !self.playing {
            return;
        }
        // **The passage heard, not the one mixed** `[REQ-AUD-164]`: read off
        // the device through the timeline. The mixer runs a ring's depth ahead,
        // so a passage leaves it fifteen seconds before its last sample is
        // played, and judging it there froze `heard_ms` at what was decoded --
        // which a deferred correction then estimated by a clock.
        //
        // **Read even when nothing is heard.** No head is not "nothing to do":
        // it is the strongest evidence a passage has just departed. An earlier
        // version returned here, so skipping the *last* queued passage judged
        // nothing at all -- the passage was abandoned and suppressed nothing,
        // and the Director could offer it straight back `[SPEC-PLAY-050]`.
        let heard = self.heard_now();
        let admitted_now = heard.as_ref().map(|(a, _)| a.admitted);
        let head_now: Option<HeadNow> = heard.map(|(a, e)| {
            (
                e.passage_id,
                e.mbid.clone(),
                a.position_ms.min(e.duration_ms()),
                e.duration_ms(),
                // Rides along for the eventual `record_play` below
                // `[REQ-VIS-300]` -- the entry's own provenance, set
                // when it was queued, never computed here.
                e.selected_by.clone(),
            )
        });
        let id_now = head_now.as_ref().map(|(id, ..)| *id);

        // The guard has to follow the head, not just remember the last write.
        // While every started passage was recorded these were the same thing;
        // now that a passage can finish unrecorded, a stale id would suppress
        // the next honest play of the same passage.
        if self.head != id_now {
            // A handoff is a departure without a rejection: the passage did
            // not stop, it moved to the other backend `[SPEC-BK-065]`. Taken
            // rather than read, so it covers exactly one departure.
            let handoff = std::mem::take(&mut self.handing_over);
            // What was heard of it after the last sample this saw: its tail,
            // read by the device since, up to where it ended or was overtaken.
            if let (Some(admitted), Some(from), Some(read)) =
                (self.head_admitted, self.heard_from, self.read_position())
            {
                if let Some(to) = self.timeline.heard_to(admitted, read) {
                    self.heard_ms += to.min(self.head_span_ms).saturating_sub(from);
                }
            }
            if let Some(prev) = self.head {
                if !handoff {
                    if self.recorded {
                        // Already earned a play. Its last sample has been
                        // heard, or it was cut, so what was heard is final
                        // `[REQ-VIS-250]`. A passage adopted mid-play from
                        // another backend, or one whose write itself failed,
                        // leaves no local row to correct.
                        if let Some(play_id) = self.pending_play_id.take() {
                            self.write_finish(play_id, self.heard_ms.min(self.head_span_ms));
                        }
                    } else {
                        // The outgoing passage left without reaching the
                        // threshold: it did not play, and it is not
                        // forgotten either `[SPEC-PLAY-050]`.
                        let prev_mbid = self.head_mbid.take();
                        self.note_rejection(
                            crate::db::Rejection::Skip,
                            prev,
                            prev_mbid.as_deref(),
                            Some(self.heard_ms),
                            Some(self.head_span_ms),
                        );
                    }
                }
            }
            self.head = id_now;
            self.head_admitted = admitted_now;
            self.head_mbid = head_now.as_ref().and_then(|(_, m, ..)| m.clone());
            self.head_span_ms = head_now.as_ref().map(|(_, _, _, span, _)| *span).unwrap_or(0);
            self.pending_play_id = None;
            // A passage that arrives already counted starts its life here as
            // recorded, which is what stops it being counted twice.
            self.recorded = match (id_now, self.counted_elsewhere) {
                (Some(now), Some(already)) if now == already => {
                    self.counted_elsewhere = None;
                    true
                }
                _ => false,
            };
            // A new passage has been heard for none of itself, and there is
            // no earlier position of it to measure the first gap from.
            self.heard_ms = 0;
            self.heard_from = None;
        } else if self.head_admitted != admitted_now {
            // The same passage opened again -- a seek -- now sounding. It has
            // not departed, but its position has jumped, and the distance is
            // not listening `[SPEC-PLAY-012]`.
            self.head_admitted = admitted_now;
            self.heard_from = None;
        }

        let Some((id, mbid, position_ms, span_ms, selected_by)) = head_now else { return };

        // **Credited from the gap between samples, never from the position.**
        // Only forward movement counts, and only movement this sample saw:
        // a seek clears `heard_from`, so the jump across contributes nothing
        // and the next sample simply starts measuring again `[SPEC-PLAY-012]`.
        if let Some(previous) = self.heard_from {
            self.heard_ms += position_ms.saturating_sub(previous);
        }
        self.heard_from = Some(position_ms);

        if self.recorded {
            return;
        }
        if !crate::scrobble::counts_as_play(self.heard_ms, span_ms) {
            return;
        }
        self.recorded = true;
        self.outcomes.push(crate::playback::Outcome::Played { passage: id });
        if let Some(store) = &self.store {
            // `heard_ms` at this instant is only the threshold just crossed --
            // half the passage, or four minutes -- not what will finally have
            // been heard. `finish_play` corrects it once the passage actually
            // departs `[REQ-VIS-250]`.
            match store.record_play(id, mbid.as_deref(), self.heard_ms, span_ms, selected_by.as_deref()) {
                Ok(play_id) => self.pending_play_id = Some(play_id),
                Err(e) => tracing::error!("record play: {e}"),
            }
        }
    }

    /// Correct a play already written with how much was truly heard.
    ///
    /// Best-effort, like the write it corrects: if this never runs -- process
    /// exit, a store error -- the row simply keeps whatever figure it was
    /// last written with, which under-reports rather than over-reports.
    pub(super) fn write_finish(&self, play_id: i64, heard_ms: u64) {
        let Some(store) = &self.store else { return };
        if let Err(e) = store.finish_play(play_id, heard_ms) {
            tracing::error!("finish play: {e}");
        }
    }

    /// How many passages are sounding. For tests that need to know the engine
    /// really went quiet.
    pub fn snapshot_live(&self) -> usize {
        self.live.len()
    }

    /// The suppression windows as the listener has them set, in hours:
    /// `(skip, dequeue)` `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`.
    pub fn snapshot_suppress_h(&self) -> (u64, u64) {
        (self.prefs.skip_suppress_h, self.prefs.dequeue_suppress_h)
    }

    /// A passage the listener declined `[SPEC-PLAY-050]`.
    ///
    /// Written to `listener_rejections`, never to `listener_play_history`: it
    /// must not gain a play, a ramp or an artist mark. The only thing it earns
    /// is a spell out of the running.
    ///
    /// Best-effort, like `record_play`. A history write must never interrupt
    /// the music.
    ///
    /// `heard_ms`/`span_ms` are `None` for a dequeue: the passage never
    /// sounded, so there is no percentage to report, not a percentage of
    /// zero `[REQ-VIS-250]`. A skip supplies both -- it always started.
    pub(super) fn note_rejection(
        &mut self,
        kind: crate::db::Rejection,
        passage_id: i64,
        mbid: Option<&str>,
        heard_ms: Option<u64>,
        span_ms: Option<u64>,
    ) {
        if let Some(store) = &self.store {
            if let Err(e) = store.record_rejection(kind, passage_id, mbid, heard_ms, span_ms) {
                tracing::error!("record {}: {e}", kind.as_str());
            }
        }
        // And to the running Director, written or not: the window is the
        // listener's wish, and a failed write is no reason to ignore it.
        self.outcomes.push(crate::playback::Outcome::Rejected {
            passage: passage_id,
            kind,
            mbid: mbid.map(str::to_string),
            at: super::unix_now(),
        });
    }

}
