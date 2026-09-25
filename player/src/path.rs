//! The audio path supervisor `[SPEC-APS-060]`.
//!
//! One component owns the device's whole life: opening it, noticing it has
//! stopped being audible, releasing it, waiting, and opening it again. The
//! engine holds only a [`PathHandle`] -- a ring to write into and a channel to
//! ask things of -- and can therefore neither open a device, nor wait for one,
//! nor shell out to ask about one.
//!
//! **That inability is the point `[GDE-FBD-090]`.** All of this logic used to
//! live inside `Engine::tick`, and three separate times something blocking got
//! into it: a `wpctl` fork/exec, then a 700 ms settle, then nearly again. Each
//! starved the ring and presented as underruns and dropouts that looked like
//! hardware failure. The rule "nothing blocking on the tick" was written down
//! and broken anyway, because nothing enforced it. Here the enforcement is that
//! the engine has no such capability to misuse.
//!
//! On this thread, by contrast, blocking is free: it may sleep through the
//! settle, poll `wpctl`, and take as long as it likes, because nothing is
//! waiting on it to fill a buffer.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, RecvTimeoutError, Sender};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::output::{Output, OutputRing};

/// Base spacing between recovery attempts, doubling to `RETRY_MAX`.
///
/// Retries are spaced rather than continuous because the usual reason a sink is
/// gone is that someone carried the speaker out of range, and reopening an
/// absent ALSA device several thousand times a second costs a core for nothing.
const RETRY: Duration = Duration::from_secs(2);
const RETRY_MAX: Duration = Duration::from_secs(30);
/// How long to leave the device closed between releasing and reopening.
///
/// A stream rebuilt in the same breath as the old one is dropped loses a
/// Bluetooth speaker about twenty-two seconds later, every time, where one
/// opened fresh holds indefinitely: PipeWire needs a moment to finish tearing
/// the old one down `[PI3-OPEN-010]`.
const SETTLE: Duration = Duration::from_millis(700);
/// How often to confirm the audio is still reaching something real.
const WATCH: Duration = Duration::from_secs(20);
/// How long the loop waits for a request before going round again.
const IDLE: Duration = Duration::from_millis(200);

/// Things only the supervisor can do, asked for from anywhere.
pub enum PathRequest {
    /// Start or stop the consumer. Pausing feeds silence rather than stopping
    /// the device, so the Bluetooth link survives it `[PI3-OPEN-020]`.
    SetPlaying(bool),
    /// Reopen because the sink changed under us, not because it failed
    /// `[PI3-API-010]`.
    Reopen,
    Shutdown,
}

/// What the engine holds: somewhere to put audio, and a way to ask for things.
///
/// Deliberately without any method that can block. `ring` is `None` for the
/// discard sink -- the full pipeline running into nothing, which is what the
/// tests and a headless host use.
#[derive(Clone)]
pub struct PathHandle {
    pub ring: Option<OutputRing>,
    tx: Option<Sender<PathRequest>>,
    recoveries: Arc<AtomicU64>,
}

impl PathHandle {
    /// A handle with no device at all.
    pub fn silent() -> Self {
        Self { ring: None, tx: None, recoveries: Arc::new(AtomicU64::new(0)) }
    }

    /// A handle over a real ring with no supervisor behind it `[REQ-VIS-250]`.
    ///
    /// For the engine's own tests, which otherwise run entirely against
    /// `silent()` -- a ring of `None` reports zero frames buffered, so
    /// nothing exercised there can tell "mixed" from "heard" apart. This
    /// gives a test a ring it can fill and simply never drain, which is
    /// indistinguishable from a real device that is buffering audio slower
    /// than the mixer is producing it.
    #[cfg(test)]
    pub(crate) fn with_ring(ring: crate::output::OutputRing) -> Self {
        Self { ring: Some(ring), tx: None, recoveries: Arc::new(AtomicU64::new(0)) }
    }

    fn ask(&self, r: PathRequest) {
        if let Some(t) = &self.tx {
            let _ = t.send(r);
        }
    }

    pub fn set_playing(&self, on: bool) {
        self.ask(PathRequest::SetPlaying(on));
    }
    pub fn reopen(&self) {
        self.ask(PathRequest::Reopen);
    }
    pub fn shutdown(&self) {
        self.ask(PathRequest::Shutdown);
    }

    /// Could anyone hear us? Observed, never assumed `[GDE-FBD-100]`.
    ///
    /// A discard sink answers `true`: it is not a fault, and treating it as one
    /// would stop the queue advancing on a host with no audio at all.
    pub fn audible(&self) -> bool {
        self.ring.as_ref().is_none_or(|r| !r.failed())
    }

    pub fn recoveries(&self) -> u64 {
        self.recoveries.load(Ordering::Relaxed)
    }

    pub fn sample_rate(&self) -> u32 {
        self.ring.as_ref().map_or(44_100, |r| r.sample_rate())
    }
    pub fn channels(&self) -> usize {
        self.ring.as_ref().map_or(2, |r| r.channels())
    }
}

/// Open the device on a thread of the supervisor's own and keep it alive.
///
/// `cpal::Stream` is `!Send`, so the device cannot be opened here and moved
/// there; the supervisor opens it itself and reports back what happened. The
/// returned string is for the startup line -- it says which device, or why not.
pub fn start(device: Option<String>, ring_capacity: usize) -> (PathHandle, String) {
    let (ready_tx, ready_rx) = sync_channel::<(Option<OutputRing>, String)>(1);
    let (tx, rx) = std::sync::mpsc::channel::<PathRequest>();
    let recoveries = Arc::new(AtomicU64::new(0));
    let counter = Arc::clone(&recoveries);

    std::thread::Builder::new()
        .name("lempi-path".into())
        .spawn(move || supervise(device, ring_capacity, ready_tx, rx, counter))
        .expect("spawn path supervisor");

    // Wait for the first open attempt, so the caller can print an honest line
    // about it. Only this one moment blocks, and it is before any audio flows.
    let (ring, why) = ready_rx.recv().unwrap_or((None, "path supervisor failed to start".into()));
    (PathHandle { ring, tx: Some(tx), recoveries }, why)
}

fn supervise(
    device: Option<String>,
    ring_capacity: usize,
    ready: std::sync::mpsc::SyncSender<(Option<OutputRing>, String)>,
    rx: Receiver<PathRequest>,
    recoveries: Arc<AtomicU64>,
) {
    let mut out = match Output::open_device(device.as_deref(), ring_capacity) {
        Ok(o) => {
            let why = format!("output: {} @ {} Hz, {} ch",
                              o.device_name, o.ring.sample_rate(), o.ring.channels());
            let _ = ready.send((Some(o.ring()), why));
            Some(o)
        }
        Err(e) => {
            // A missing device must not stop the process: the UI still needs to
            // come up and say so, which is more use than exiting silently.
            let _ = ready.send((None, format!("no audio device ({e}); running without output")));
            None
        }
    };
    let Some(out) = out.as_mut() else { return };

    let mut playing = false;
    let mut backoff = RETRY;
    let mut retry_at: Option<Instant> = None;
    let mut watch_at = Instant::now() + WATCH;

    loop {
        match rx.recv_timeout(IDLE) {
            Ok(PathRequest::SetPlaying(on)) => {
                playing = on;
                out.set_playing(on);
                // Check for a dummy AT ONCE when playback starts, rather than
                // up to WATCH later. A speaker that is not there is never more
                // likely than at the moment someone presses play -- and never
                // more so than when the machine has just booted and resumed
                // `[PI5-PWR-030]`. Until it is noticed, the ring drains into
                // the discard sink and the clock advances through music nobody
                // can hear: exactly the lie `[PI3-API-030]` exists to stop.
                if on {
                    watch_at = Instant::now();
                }
            }
            Ok(PathRequest::Reopen) => {
                // Same path as a recovery: a reopen landing on a device that is
                // not ready yet -- a speaker still completing its connection is
                // the normal case -- should keep trying rather than fail once
                // and leave the listener with a selection that did nothing.
                out.mark_failed();
                retry_at = None;
                backoff = RETRY;
            }
            Ok(PathRequest::Shutdown) | Err(RecvTimeoutError::Disconnected) => return,
            Err(RecvTimeoutError::Timeout) => {}
        }

        watch(out, playing, &mut watch_at);
        recover(out, playing, &mut retry_at, &mut backoff, &recoveries);
    }
}

/// Notice a sink that became a dummy without anyone reporting an error
/// `[PI3-API-030]`.
///
/// The failure path covers a stream that breaks. This covers the quieter one: a
/// speaker switched off during normal playback, whose stream PipeWire moves to
/// the `Dummy Output` with no error at all. Nothing is wrong at that moment --
/// the callback runs, the ring drains, the clock advances -- and nobody can
/// hear a thing.
///
/// It costs a subprocess, which is exactly why it belongs here and nowhere near
/// the mixer `[GDE-FBD-110]`.
fn watch(out: &Output, playing: bool, watch_at: &mut Instant) {
    if !playing || out.failed() || Instant::now() < *watch_at {
        return;
    }
    *watch_at = Instant::now() + WATCH;
    if crate::sink::current().dummy {
        tracing::warn!("audio is going nowhere audible; looking for a sink");
        out.mark_failed();
    }
}

/// Release, wait, reopen -- and only call it recovered if it is audible.
///
/// Gated on `playing` for the same reason `watch()` already is: nothing
/// here previously consulted it at all, so an already-failed device kept
/// releasing and reopening on its own backoff regardless of whether
/// anything was even asking to play -- endlessly re-hunting a device that
/// simply isn't there while a plain pause, or nothing chosen to play at
/// all, made the answer not matter either way. `watch()` stops *starting*
/// a new failure while `!playing`; this stops an *already-armed* one from
/// continuing to run. The next `SetPlaying(true)` re-arms `watch_at`
/// immediately and this resumes exactly as before.
fn recover(
    out: &mut Output,
    playing: bool,
    retry_at: &mut Option<Instant>,
    backoff: &mut Duration,
    recoveries: &Arc<AtomicU64>,
) {
    if !playing || !out.failed() {
        return;
    }
    let now = Instant::now();
    if retry_at.is_some_and(|t| now < t) {
        return;
    }
    *backoff = (*backoff * 2).min(RETRY_MAX).max(RETRY);
    *retry_at = Some(now + *backoff);
    recoveries.fetch_add(1, Ordering::Relaxed);

    if !out.released() {
        out.release();
        // Free to sleep: nothing is waiting on this thread to fill a buffer.
        // In the engine this same wait starved the ring and turned a dropout
        // into a stutter.
        std::thread::sleep(SETTLE);
    }
    match out.recover() {
        Ok(name) => {
            // Opening succeeded, which says nothing about whether anyone can
            // hear it: the dummy accepts audio perfectly forever. Treat that as
            // a failure so we keep looking for a real sink `[PI3-API-030]`.
            if crate::sink::current().dummy {
                tracing::warn!("output opened onto a dummy -- still silent, retrying");
                out.mark_failed();
            } else {
                tracing::info!("output recovered on {name}");
                *retry_at = None;
                *backoff = RETRY;
                // The stream comes back stopped; only resume it if the listener
                // had not paused in the meantime.
                out.set_playing(playing);
            }
        }
        Err(e) => tracing::warn!("output recovery failed, retrying: {e}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_silent_handle_asks_for_nothing_and_answers_honestly() {
        let h = PathHandle::silent();
        // No device is not a fault: a discard sink must let the queue advance,
        // or a host with no audio would sit forever on one passage.
        assert!(h.audible());
        assert_eq!(h.recoveries(), 0);
        // Requests are dropped rather than panicking on a missing supervisor.
        h.set_playing(true);
        h.reopen();
        h.shutdown();
    }

    #[test]
    fn the_handle_carries_no_way_to_block() {
        // A compile-time-ish guard expressed as intent: the handle exposes only
        // sends and atomic loads. If a method here ever starts opening devices
        // or sleeping, `[GDE-FBD-090]` has been broken and this test's name is
        // the place someone will look.
        let h = PathHandle::silent();
        let before = std::time::Instant::now();
        for _ in 0..10_000 {
            let _ = h.audible();
            let _ = h.channels();
        }
        assert!(before.elapsed() < Duration::from_millis(500),
                "reading path state must be a load, not a query");
    }
}
