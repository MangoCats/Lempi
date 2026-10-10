//! What the engine is told, and what it does about each: the `Command`s a
//! handle sends from any thread, drained at the top of every tick.
//!
//! Its own module because the handler is long -- a branch per command, each
//! carrying why it does what it does -- and is otherwise read past on the way
//! to the mixer. The state each command changes is still `Engine`'s
//! (`mod.rs`), and the work it calls on is there too.

use super::*;

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

/// What the engine can be told, through its handle, from any thread.
///
/// Playback has exactly **two** states, playing and paused. There is no
/// "stopped": pausing halts only the *consumer*, while decoders keep filling
/// their buffers, so resuming is instant and the pipeline stays primed after
/// the initial power-on fill.
#[derive(Debug)]
pub enum Command {
    Play,
    Pause,
    /// Toggle between playing and paused, flipping state and publishing immediately.
    TogglePlayPause,
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
    /// Enqueue a shutdown event at the tail of the queue `[IMPL-QSD-020]`.
    EnqueueShutdown,
    /// Sound a short tone at the device now, over the music or the pause
    /// `[SPEC-WFO-085]`. Nothing without a device; never in the history.
    Cue(crate::output::Cue),
    /// Terminate the process. Deliberately NOT a playback state -- it ends the
    /// engine rather than putting playback into a third mode.
    Shutdown,
}

impl Engine {
    /// Act on every command waiting, in the order sent, then return: the tick
    /// must not wait for one.
    pub(super) fn drain_commands(&mut self) {
        loop {
            match self.rx.try_recv() {
                Ok(Command::Play) => self.set_playing(true),
                Ok(Command::Pause) => self.set_playing(false),
                Ok(Command::TogglePlayPause) => {
                    let next = !self.playing;
                    self.set_playing(next);
                    self.publish();
                }
                Ok(Command::ReopenOutput) => self.path.reopen(),
                Ok(Command::Cue(c)) => {
                    if let Some(r) = &self.path.ring {
                        r.play_cue(c);
                    }
                }
                Ok(Command::Skip) => self.skip(),
                Ok(Command::SetEchoDelayTrim(ms)) => {
                    self.echo.delay_trim_ms =
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
                        if host != self.echo.follow_host && !self.echo.join_bias.is_empty() {
                            tracing::info!("echo-join: now following {host}; forgetting {} landing(s) learned against {}",
                                      self.echo.join_bias.len(),
                                      if self.echo.follow_host.is_empty() { "nobody" } else { &self.echo.follow_host });
                            self.echo.join_bias = crate::echo::JoinBias::default();
                        }
                        self.echo.follow_host = host;
                        self.remember_settings();
                    }
                }
                Ok(Command::SetEchoRate(ppm)) => {
                    let want = if ppm.is_finite() { ppm } else { 0.0 };
                    if want.abs() > Self::ECHO_RATE_CEILING_PPM {
                        tracing::warn!("echo-rate: {want:+.1} ppm is not a crystal; clamping to {:+.0} and carrying on", Self::ECHO_RATE_CEILING_PPM.copysign(want));
                    }
                    self.echo.rate_ppm =
                        want.clamp(-Self::ECHO_RATE_CEILING_PPM, Self::ECHO_RATE_CEILING_PPM);
                    // Starting or stopping the clock, never resetting it
                    // mid-run: a rate that is merely refined should not push
                    // the next trim back by a whole interval each time.
                    if self.echo.rate_ppm == 0.0 && self.echo.debt_frames == 0 {
                        self.echo.last_trim = None;
                    } else if self.echo.last_trim.is_none() {
                        self.echo.last_trim = Some(std::time::Instant::now());
                    }
                }
                Ok(Command::EchoSetQueue(entries)) => {
                    self.queue.replace_upcoming(entries);
                    self.queue_edited = true;
                }
                Ok(Command::EchoShedOffset(ms)) => {
                    let rate = self.out_rate.max(1) as i64;
                    self.echo.debt_frames = ms.saturating_mul(rate) / 1000;
                    // The trim clock runs for a debt as well as for a rate.
                    if self.echo.debt_frames != 0 && self.echo.last_trim.is_none() {
                        self.echo.last_trim = Some(std::time::Instant::now());
                    }
                    tracing::info!("echo-offset: shedding {ms} ms by trimming ({} frames)",
                              self.echo.debt_frames);
                }
                Ok(Command::EchoCorrectNextStart(ms)) => {
                    self.echo.next_shift_ms = ms;
                    // **A new correction is a new plan, and it supersedes the
                    // outstanding debt** `[GDE-ARC-041]`. The follower
                    // measures once per master passage and sends the pair --
                    // this shift for the boundary, then whatever the boundary
                    // will not take for the trim. Without clearing here, the
                    // remainder `admit_due` adds when a boundary under-delivers
                    // would accumulate across passages into a debt nobody
                    // measured. The follower's own `EchoShedOffset` follows
                    // this command and sets the new base.
                    self.echo.debt_frames = 0;
                }
                Ok(Command::RecordJoinLanding(ms)) => {
                    // **Persisted immediately, not at the next settings
                    // write** `[GDE-ARC-061]`. Joins are rare on a quiet
                    // follower -- three in seven hours was typical -- so a
                    // sample lost to a restart can be a day's learning, and
                    // that is exactly how `echo.prep_ms` never converged
                    // `[GDE-ARC-058]`.
                    let was = self.echo.join_bias.correction_ms();
                    if self.echo.join_bias.record(ms) {
                        let now = self.echo.join_bias.correction_ms();
                        tracing::info!("echo-join: landed {ms:+} ms; {} sample(s), aiming {now:+} ms further on from now (was {was:+})",
                                  self.echo.join_bias.len());
                        self.remember_settings();
                    } else {
                        tracing::warn!("echo-join: landed {ms:+} ms, which is past the credible range; not learned from");
                    }
                }
                Ok(Command::SetEchoJoinNow(now)) => {
                    self.echo.join_now = now;
                    self.remember_settings();
                }
                Ok(Command::EchoStartAt { entry, start_sample, at_nanos }) => {
                    self.echo.start = Some((entry, start_sample, at_nanos));
                }
                Ok(Command::SetEchoDepth { own_offset_frames, fleet_min_offset_frames }) => {
                    self.set_echo_depth(own_offset_frames, fleet_min_offset_frames);
                }
                Ok(Command::SetSkipFade(ms)) => {
                    self.skip_fade_ms = ms.min(crate::SKIP_FADE_MAX_MS);
                    self.remember_settings();
                }
                Ok(Command::SetResumeSave(ms)) => {
                    self.prefs.resume_save_ms =
                        ms.clamp(crate::RESUME_SAVE_MIN_MS, crate::RESUME_SAVE_MAX_MS);
                    self.remember_settings();
                }
                Ok(Command::SetSkipSuppress(h)) => {
                    self.prefs.skip_suppress_h =
                        h.clamp(crate::SKIP_SUPPRESS_MIN_H, crate::SKIP_SUPPRESS_MAX_H);
                    self.remember_settings();
                }
                Ok(Command::SetDequeueSuppress(h)) => {
                    self.prefs.dequeue_suppress_h =
                        h.clamp(crate::DEQUEUE_SUPPRESS_MIN_H, crate::DEQUEUE_SUPPRESS_MAX_H);
                    self.remember_settings();
                }
                Ok(Command::SetQueueDepth(n)) => {
                    self.queue.min_depth =
                        n.clamp(crate::QUEUE_DEPTH_MIN, crate::QUEUE_DEPTH_MAX);
                    self.remember_settings();
                }
                Ok(Command::SetCueSheets(on)) => {
                    self.prefs.cue_sheets = on;
                    self.remember_settings();
                }
                Ok(Command::SetCovers(on)) => {
                    self.prefs.covers = on;
                    self.remember_settings();
                }
                Ok(Command::SetLyricsCache(on)) => {
                    self.prefs.lyrics_cache = on;
                    self.remember_settings();
                }
                Ok(Command::SetLyricsSidecar(on)) => {
                    self.prefs.lyrics_sidecar = on;
                    self.remember_settings();
                }
                Ok(Command::RestartUnderruns) => {
                    // The real counter is untouched; only the mark moves.
                    self.underrun_baseline = self.underruns_playing;
                    self.underrun_since = unix_now();
                }
                Ok(Command::SetSampleInterval(ms)) => {
                    self.prefs.sample_interval_ms =
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
                    self.note_people_picks([&e]);
                    self.queue.push(e);
                    self.queue_edited = true;
                }
                Ok(Command::EnqueueNext(e)) => {
                    self.note_people_picks([&e]);
                    self.queue.push_front(e);
                    self.queue_edited = true;
                }
                Ok(Command::EnqueueMany(entries, place)) => {
                    self.queue_edited = true;
                    self.note_people_picks(&entries);
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
                    self.note_people_picks([&e]);
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
                    //
                    // A person's own pick taken back out is not a rejection
                    // either: they changed their mind about their choice, not
                    // about the music. It leaves the queue as a dropped passage
                    // does, un-marked and with no window (the maintainer,
                    // 2026-10-08).
                    let declined = self
                        .queue
                        .iter()
                        .find(|e| e.qid == id && !e.is_shutdown && e.passage_id > 0)
                        .map(|e| (e.passage_id, e.mbid.clone(), is_persons_pick(e)));
                    self.queue_edited = true;
                    if self.queue.remove(id) {
                        match declined {
                            Some((passage_id, _, true)) => self.dropped.push(passage_id),
                            Some((passage_id, mbid, false)) => self.note_rejection(
                                crate::db::Rejection::Dequeue,
                                passage_id,
                                mbid.as_deref(),
                                None,
                                None,
                            ),
                            None => {}
                        }
                    }
                }
                Ok(Command::ShiftQueued(id, delta)) => {
                    self.queue.shift(id, delta);
                    self.queue_edited = true;
                }
                Ok(Command::EnqueueShutdown) => {
                    if self.queue.push_shutdown().is_some() {
                        self.queue_edited = true;
                    }
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
}
