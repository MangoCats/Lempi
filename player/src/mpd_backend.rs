//! MPD as a backend the session can drive `[SPEC-BK-020]`.
//!
//! `mpd_direct` proved this as a program with its own loop. This is the same
//! behaviour turned inside out: the loop belongs to the session now, and what
//! remains is an implementation of [`Playback`] that happens to reach a server
//! instead of a sound card.
//!
//! **The rate limit lives here, not in the caller.** `tick` is called as often
//! as the engine thread spins, and MPD must be sampled every few seconds
//! `[SPEC-MPD-105]` rather than every few microseconds. A backend that made the
//! session responsible for its polling rate would have leaked its own protocol
//! upward, which is the thing the trait exists to prevent.
//!
//! **Not playing means nothing to add.** `shortfall` reports zero unless MPD is
//! playing, so `[SPEC-MPD-120]`'s activation rule needs no special case in the
//! session: the Director simply finds nothing wanted.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use crate::db::{PlayerStore, Rejection};
use crate::mpd::{quote, Mpd};
use crate::playback::{Capabilities, Playback};
use crate::queue::QueueEntry;
use crate::scrobble::{counts_as_play, FOUR_MINUTES_MS};

/// How far short of the span's end a seek is allowed to land `[SPEC-BK-055]`.
///
/// MPD stops a song seeked past its span outright, and "past" includes the
/// last instant of it. A second short is inaudible and cannot trip that.
const SEEK_MARGIN_MS: u64 = 1_000;

/// The floor `effective_interval` will not poll tighter than `[SPEC-MPD-110]`.
///
/// A boundary a few milliseconds away would otherwise be chased with a
/// near-zero interval, turning "sample to meet it" into a busy loop against a
/// server that still has to answer each one. A quarter of a second is well
/// inside what a listener can hear as delay and nowhere near tight enough to
/// trouble MPD.
const BOUNDARY_POLL_FLOOR_MS: u64 = 250;

/// The URI MPD would use for a path, or `None` if it is outside MPD's tree.
///
/// A free function because naming needs a root and a path and **not a socket**:
/// testing it through a connected backend would have meant faking a TCP stream
/// to check a string operation.
fn uri_under(root: &str, path: &std::path::Path) -> Option<String> {
    let norm = path.to_string_lossy().replace('\\', "/");
    norm.strip_prefix(root).map(|r| r.trim_start_matches('/').to_string())
}

/// What a URI names, when it names exactly one thing.
///
/// `None` means **ambiguous**, which is not the same as unknown and must not be
/// treated as it: the file is in the library, it simply carries more than one
/// radio passage, and a whole-file entry could be any of them `[SPEC-MPD-060]`.
pub type Nameable = Option<(i64, Option<String>, u64)>;

/// Build the URI → passage map a backend needs to adopt a person's own
/// additions `[SPEC-MPD-115]`.
///
/// A file carrying more than one radio passage is recorded as ambiguous rather
/// than resolved to one of them. Added whole, a DAO capture is forty songs, and
/// attributing a play to whichever passage happened to be first would credit
/// one nobody heard.
pub fn nameable_uris(
    conn: &rusqlite::Connection,
    root: &str,
) -> Result<HashMap<String, Nameable>, String> {
    let root = root.replace('\\', "/").trim_end_matches('/').to_string();
    let mut q = conn
        .prepare(
            "SELECT f.path, f.duration_ms, p.passage_id,                 (SELECT pr.mbid FROM passage_recordings pr                  WHERE pr.passage_id = p.passage_id LIMIT 1)              FROM passages p JOIN files f USING(file_id) WHERE p.kind = 'radio'",
        )
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<i64>>(1)?.unwrap_or(0) as u64,
                r.get::<_, i64>(2)?,
                r.get::<_, Option<String>>(3)?,
            ))
        })
        .map_err(|e| e.to_string())?;
    let mut out: HashMap<String, Nameable> = HashMap::new();
    for (path, dur, pid, mbid) in rows.flatten() {
        let norm = path.replace('\\', "/");
        let Some(rel) = norm.strip_prefix(&root) else { continue };
        let uri = rel.trim_start_matches('/').to_string();
        match out.entry(uri) {
            std::collections::hash_map::Entry::Occupied(mut e) => {
                e.insert(None); // a second passage: ambiguous
            }
            std::collections::hash_map::Entry::Vacant(e) => {
                e.insert(Some((pid, mbid, dur)));
            }
        }
    }
    Ok(out)
}

/// passage id → the cue track that names it, e.g.
/// `Rush/TestForEcho.cue/track0002` `[SPEC-MPD-056]`.
///
/// Only for captures whose sheet is actually on disk. The track number is the
/// passage's position among that file's radio passages ordered by start, which
/// is exactly how [`crate::cue`] numbered them — the two must agree, and this
/// is the place they do.
pub fn cue_uris(
    conn: &rusqlite::Connection,
    root: &str,
) -> Result<HashMap<i64, String>, String> {
    let root_norm = root.replace('\\', "/").trim_end_matches('/').to_string();
    let mut q = conn
        .prepare(
            "SELECT f.path, p.passage_id FROM passages p JOIN files f USING(file_id)              WHERE p.kind = 'radio' ORDER BY f.path, p.start_ms",
        )
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
        .map_err(|e| e.to_string())?;
    let mut per_file: HashMap<String, Vec<i64>> = HashMap::new();
    let mut order: Vec<String> = Vec::new();
    for (path, pid) in rows.flatten() {
        if !per_file.contains_key(&path) {
            order.push(path.clone());
        }
        per_file.entry(path).or_default().push(pid);
    }
    let mut out = HashMap::new();
    for path in order {
        let ids = &per_file[&path];
        // One passage needs no sheet, and `cue::generate` wrote none.
        if ids.len() < 2 {
            continue;
        }
        let cue_path = std::path::Path::new(&path).with_extension("cue");
        if !cue_path.exists() {
            continue;
        }
        let norm = cue_path.to_string_lossy().replace('\\', "/");
        let Some(rel) = norm.strip_prefix(&root_norm) else { continue };
        let base = rel.trim_start_matches('/');
        for (i, pid) in ids.iter().enumerate() {
            out.insert(*pid, format!("{base}/track{:04}", i + 1));
        }
    }
    Ok(out)
}

/// The passages this backend is holding, **in the listener's order**.
///
/// A free function because it is a question about two data structures and not
/// about a socket: testing it through a connected backend would have meant
/// faking a TCP stream to check an ordering.
///
/// Order matters because this is what crosses a handoff `[SPEC-BK-030]`, and
/// `carry_queue` re-enqueues in the order it is given. Reading it out of a
/// `HashMap` shuffled the queue every time a listener moved back to Lempi.
fn in_queue_order(
    order: &[(String, String)],
    ours: &HashMap<String, Offered>,
    names: &HashMap<String, Nameable>,
) -> Vec<i64> {
    order
        .iter()
        .filter_map(|(id, uri)| match ours.get(id) {
            Some(o) => Some(o.passage_id),
            // Not offered by Lempi, so it is the listener's own. Named where
            // the library can name it, and passed over where it cannot -- which
            // is `[SPEC-BK-045]`'s rule, applied where the names actually live.
            None => match names.get(uri) {
                Some(Some((pid, ..))) => Some(*pid),
                _ => None,
            },
        })
        .collect()
}

/// `playlistinfo` as `(songid, uri)` pairs, in MPD's order.
///
/// One song's fields arrive as a run of lines beginning with `file:`, so a
/// `file:` opens an entry and the `Id:` that follows belongs to it.
fn queue_of(lines: &[String]) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for line in lines {
        if let Some(uri) = line.strip_prefix("file: ") {
            out.push((String::new(), uri.to_string()));
        } else if let Some(id) = line.strip_prefix("Id: ") {
            if let Some(last) = out.last_mut() {
                last.0 = id.to_string();
            }
        }
    }
    out
}

/// A passage handed to MPD, and what has become of it.
struct Offered {
    passage_id: i64,
    /// The URI it was added under. A sticker is addressed by URI, and the
    /// reasoning arrives after the add.
    uri: String,
    mbid: Option<String>,
    /// The span from Lempi, never MPD's estimate of it `[SPEC-MPD-092]`.
    span_ms: u64,
    /// The furthest point reached, which is a **position** and is what says
    /// whether the span has run out `[SPEC-MPD-096]`.
    furthest_ms: u64,
    /// How much of it was actually **heard** `[SPEC-PLAY-012]`, credited from
    /// the gap between samples. A seek moves the position without anyone
    /// listening to the distance, so the two part company the moment the
    /// listener touches the bar.
    heard_ms: u64,
    /// The position at the previous sample, for measuring that gap.
    seen_at_ms: Option<u64>,
    /// False when MPD dropped the range end `[SPEC-MPD-096]`, so this backend
    /// must end the passage itself rather than let it run to end of file.
    span_honoured: bool,
    /// Whether it ever reached the front. The whole difference between a
    /// listener hearing it and a listener removing it, and MPD reports the
    /// departure identically either way.
    was_current: bool,
}

pub struct MpdBackend {
    mpd: Mpd,
    /// Kept so the connection can be rebuilt. MPD is a separate process with
    /// its own lifetime — it is updated, it is restarted, it crashes — and the
    /// socket dying is a thing to recover from rather than a thing to end on
    /// `[SPEC-MPD-130]`.
    addr: String,
    /// MPD's `music_directory`, normalised, for turning a path into a URI.
    root: String,
    depth: usize,
    interval: Duration,
    last_poll: Option<Instant>,
    /// songid → what we offered under it.
    ours: HashMap<String, Offered>,
    /// MPD's queue as of the last poll: `(songid, uri)`, in MPD's order.
    ///
    /// **`ours` is a map and a map has no order**, so the listener's order has
    /// to be kept somewhere. `playlistinfo` returns both for one round trip —
    /// the same trip `playlistid` was making to find departures, and it was
    /// throwing the order away into a set on the way past.
    order: Vec<(String, String)>,
    /// MPD's whole queue length at the last poll, ours and the listener's both.
    queue_len: usize,
    playing: bool,
    dropped: Vec<i64>,
    store: Option<PlayerStore>,
    /// URI → passage, for adopting what the listener queued `[SPEC-MPD-115]`.
    names: HashMap<String, Nameable>,
    /// Cue tracks that name a passage a guest could not `[SPEC-MPD-056]`.
    cues: HashMap<i64, String>,
    /// Reported once each, so a log is not a stream of the same complaint.
    unnameable: HashSet<String>,
    /// The passage sounding at the last poll and how far into its span
    /// `[SPEC-BK-065]`. Remembered rather than asked for on demand: a
    /// handoff wants it at a moment when a round trip is the wrong thing to
    /// be doing.
    head: Option<(i64, u64)>,
    /// Passages that arrived mid-play with their play already in the
    /// history. Neither counted again nor written down as a rejection when
    /// they end `[SPEC-BK-065]`.
    counted_elsewhere: HashSet<i64>,
    /// The connection is dead. Recoverable: see `reconnect` `[SPEC-MPD-130]`.
    lost: bool,
    /// When the connection was first found dead, for deciding that MPD is not
    /// coming back rather than retrying for ever.
    lost_since: Option<Instant>,
    /// The position at the last poll and when it was last seen to move, for
    /// noticing an output that has stopped carrying samples `[SPEC-MPD-135]`.
    stall: Option<(u64, Instant)>,
    /// The next moment worth trying again, so a machine with no MPD on it does
    /// not spend the session opening sockets.
    retry_at: Option<Instant>,
}

/// How often a lost connection is retried.
const RECONNECT_EVERY: Duration = Duration::from_secs(2);

/// How long MPD may report playing without moving before its output is
/// restarted `[SPEC-MPD-135]`.
///
/// Longer than a sample interval, so an unlucky pair of identical readings is
/// not mistaken for a stall; short enough that a listener hears a gap rather
/// than wondering whether the music stopped for good.
const STALL_AFTER: Duration = Duration::from_secs(3);

/// How long MPD may be away before this backend calls itself finished.
///
/// **Long enough to cover a package update**, which is the ordinary reason an
/// appliance's MPD disappears for a moment: `apt` stops it, replaces it and
/// starts it again, and a listener should hear a gap rather than lose the
/// guest until someone restarts Lempi `[SPEC-MPD-130]`.
const GIVE_UP_AFTER: Duration = Duration::from_secs(120);

impl MpdBackend {
    pub fn connect(
        addr: &str,
        music_root: &str,
        depth: usize,
        interval_ms: u64,
    ) -> Result<Self, String> {
        let mut mpd = Mpd::connect(addr)?;
        // `consume 1` is what makes MPD's queue the same object as Lempi's
        // `[SPEC-MPD-035]`: a played passage leaves, so "top up to depth" is a
        // stable statement rather than a growing playlist.
        mpd.cmd("consume 1")?;
        Ok(Self {
            mpd,
            addr: addr.to_string(),
            root: music_root.replace('\\', "/").trim_end_matches('/').to_string(),
            depth,
            interval: Duration::from_millis(interval_ms),
            last_poll: None,
            ours: HashMap::new(),
            order: Vec::new(),
            queue_len: 0,
            playing: false,
            dropped: Vec::new(),
            store: None,
            names: HashMap::new(),
            cues: HashMap::new(),
            unnameable: HashSet::new(),
            head: None,
            counted_elsewhere: HashSet::new(),
            lost: false,
            lost_since: None,
            stall: None,
            retry_at: None,
        })
    }

    /// Report a failed command, and note the connection as lost if that is what
    /// it was.
    ///
    /// **A refusal is not a death.** MPD answers a command it dislikes with
    /// `ACK`, and treating that as a lost connection would throw away a working
    /// socket — and, through `is_shutdown`, eventually the player with it. Only
    /// the transport failing counts, which is what `Mpd::broken` records.
    fn mark_lost(&mut self, what: &str, e: &str) {
        if !self.mpd.broken {
            tracing::error!("mpd {what}: {e}");
            return;
        }
        if !self.lost {
            tracing::warn!("mpd {what}: {e} -- connection lost, will retry");
            self.lost_since = Some(Instant::now());
        }
        self.lost = true;
    }

    /// Try to become usable again `[SPEC-MPD-130]`.
    ///
    /// **What MPD remembers across its own restart is not ours to assume.** It
    /// restores its queue from `state_file`, but the song *ids* in it are freshly
    /// assigned, and `ours` is keyed by song id — so every entry in it now points
    /// at nothing, or worse, at somebody else's song. They are released as
    /// dropped rather than retired: a dropped passage is un-counted by the
    /// Director `[REQ-PD-112]`, which is the honest reading of "this was queued
    /// and its fate is now unknown". Calling `retire` instead would have written
    /// a play or a skip for something nobody observed `[PI3-API-030]`.
    ///
    /// Returns whether the connection is usable, so a caller that needs it right
    /// now — a handoff — can find out without waiting for the next tick.
    fn reconnect(&mut self) -> bool {
        if !self.lost {
            return true;
        }
        if self.retry_at.map(|t| Instant::now() < t).unwrap_or(false) {
            return false;
        }
        self.retry_at = Some(Instant::now() + RECONNECT_EVERY);
        let mut mpd = match Mpd::connect(&self.addr) {
            Ok(m) => m,
            Err(_) => return false,
        };
        if let Err(e) = mpd.cmd("consume 1") {
            tracing::error!("mpd reconnect: consume 1: {e}");
            return false;
        }
        tracing::info!("mpd reconnected at {} (protocol {})", self.addr, mpd.version);
        self.mpd = mpd;
        for (_, o) in std::mem::take(&mut self.ours) {
            self.dropped.push(o.passage_id);
        }
        self.order.clear();
        self.head = None;
        self.playing = false;
        self.stall = None;
        self.queue_len = 0;
        self.lost = false;
        self.lost_since = None;
        true
    }

    /// Teach it to name what the listener queues `[SPEC-MPD-115]`. Without
    /// this, a song added by hand plays and counts for nothing.
    pub fn attach_names(&mut self, names: HashMap<String, Nameable>) {
        self.names = names;
    }

    /// Teach it which passages have a cue track `[SPEC-MPD-056]`.
    pub fn attach_cues(&mut self, cues: HashMap<i64, String>) {
        self.cues = cues;
    }

    /// Where plays and rejections are written `[SPEC-PLAY-030]`. Optional, so a
    /// dry run is a run without a store rather than a flag threaded through.
    pub fn attach_store(&mut self, store: PlayerStore) {
        self.store = Some(store);
    }

    fn uri_for(&self, e: &QueueEntry) -> Option<String> {
        uri_under(&self.root, &e.path)
    }

    /// Fade out and stop, returning whether the fade was real.
    ///
    /// **MPD cannot be asked to fade.** The protocol has a global `crossfade`
    /// between songs and it has `stop`; there is no "fade out over N ms". So a
    /// fade has to be built from `setvol` steps — and `setvol` is refused with
    /// **`No mixer`** unless the output plugin has one `[SPEC-MPD-099]`.
    ///
    /// Measured: MPD's `null` output has no mixer at all, so on a test rig this
    /// returns `false` and cuts. A PipeWire or ALSA output — what an appliance
    /// actually runs — does have one. Reporting which happened is the point:
    /// silently cutting where a fade was promised is the class of lie
    /// `[PI3-API-030]` refuses.
    ///
    /// The listener's volume is **restored** afterwards. It is theirs, their
    /// other clients display it, and borrowing it for a second is only
    /// acceptable if it is given back.
    fn fade_out_inner(&mut self, ms: u64) -> bool {
        let start = self
            .mpd
            .status()
            .ok()
            .and_then(|s| s.get("volume").and_then(|v| v.parse::<i64>().ok()))
            .filter(|v| *v >= 0);
        let Some(start) = start else {
            self.stop_sounding();
            return false;
        };
        const STEPS: u64 = 8;
        let step = Duration::from_millis((ms / STEPS).max(1));
        for i in 1..=STEPS {
            let level = start - (start * i as i64 / STEPS as i64);
            if self.mpd.cmd(&format!("setvol {}", level.max(0))).is_err() {
                // The mixer went away mid-fade. Stop rather than leave the
                // listener's volume somewhere they did not put it.
                break;
            }
            std::thread::sleep(step);
        }
        self.stop_sounding();
        let _ = self.mpd.cmd(&format!("setvol {start}"));
        true
    }

    /// Make MPD's output start carrying samples again `[SPEC-MPD-135]`.
    ///
    /// **A workaround for something that is not Lempi's bug, kept here because
    /// the listener's silence is Lempi's problem.** On the appliance, any MPD
    /// command that cancels the output mid-playback — `seekid`, `next` — leaves
    /// MPD reporting `state: play` with `elapsed` frozen and nothing at the
    /// speaker. Measured across seven output configurations: every ALSA route
    /// sounds and then dies this way, and every `pulse` or native `pipewire`
    /// route never sounds at all `[PI-CHR-100]`. There is no configuration that
    /// does both, so the choice is between a player that cannot seek and one
    /// that carries this.
    ///
    /// `pause 1` immediately followed by `pause 0` brings it back — measured
    /// with **no delay between them**, so this costs two round trips and no
    /// sleep. The position moves by up to the output buffer; a couple of
    /// seconds of the passage, against losing the rest of it.
    fn restart_output(&mut self) {
        let _ = self.mpd.cmd("pause 1");
        let _ = self.mpd.cmd("pause 0");
    }

    /// Notice an output that has stopped carrying samples and restart it.
    ///
    /// **For the flushes Lempi did not cause.** A guest client is the whole
    /// point of the MPD backend `[SPEC-MPD-050]`, and somebody seeking in
    /// Cantata wedges the output exactly as Lempi's own seek does — with Lempi
    /// nowhere in that conversation. What Lempi does have is the poll, so the
    /// stall is caught there instead.
    ///
    /// Position, not heard time: a stalled output is precisely the case where
    /// MPD says it is playing and the clock disagrees.
    fn watch_for_stall(&mut self, position_ms: u64) {
        if !self.playing {
            self.stall = None;
            return;
        }
        match self.stall {
            Some((at, since)) if at == position_ms => {
                if since.elapsed() >= STALL_AFTER {
                    tracing::warn!(
                        "mpd says playing at {position_ms} ms and has not moved for {:.1}s; \
                         restarting its output",
                        since.elapsed().as_secs_f32()
                    );
                    self.restart_output();
                    // Restarted or not, the clock is measured afresh from here
                    // rather than firing again on the next poll.
                    self.stall = Some((position_ms, Instant::now()));
                }
            }
            _ => self.stall = Some((position_ms, Instant::now())),
        }
    }

    /// Stop sounding, without losing the connection.
    ///
    /// A handoff away from MPD leaves MPD *running* — it is a guest that may be
    /// wanted again in a moment, and its clients are still someone's remote
    /// control `[SPEC-BK-025]`.
    pub fn stop_sounding(&mut self) {
        let _ = self.mpd.cmd("stop");
        self.playing = false;
    }

    /// Judge a passage that has left MPD's queue, and record what it was.
    fn retire(&mut self, o: Offered) {
        if !o.was_current {
            // Never reached the front, so a person took it out. It did not play
            // and it is not forgotten: the removal earns the shorter window
            // `[SPEC-PLAY-055]`, and the Director is told to un-count the
            // queueing mark by way of `take_dropped` `[REQ-PD-112]`.
            self.dropped.push(o.passage_id);
            // Never sounded, so there is no percentage to report `[REQ-VIS-250]`.
            self.write(Rejection::Dequeue, o.passage_id, o.mbid.as_deref(), None, None);
            return;
        }
        // A passage handed over mid-play brought its play with it. Asking the
        // question again could only answer it a second time, and answering
        // `no` would be worse still: a rejection for a passage that played
        // `[SPEC-BK-065]`.
        if self.counted_elsewhere.remove(&o.passage_id) {
            return;
        }
        // It sounded. Whether it *played* is `[SPEC-PLAY-010]`'s question, and
        // it is asked against Lempi's span rather than MPD's idea of one.
        //
        // Unlike the local engine, `o.heard_ms` is already final here --
        // `retire` runs once, at departure, not on every tick -- so there is
        // no threshold-crossing figure to correct afterwards `[REQ-VIS-250]`.
        if counts_as_play(o.heard_ms, o.span_ms) {
            if let Some(s) = &self.store {
                // This backend does not yet track who queued a passage
                // `[REQ-VIS-300]` -- such a play still counts toward the
                // play-frequency panel's "All" row, just not "User" or a
                // program row.
                if let Err(e) = s.record_play(o.passage_id, o.mbid.as_deref(), o.heard_ms, o.span_ms, None) {
                    tracing::error!("record play: {e}");
                }
            }
        } else {
            self.write(Rejection::Skip, o.passage_id, o.mbid.as_deref(), Some(o.heard_ms), Some(o.span_ms));
        }
    }

    fn write(
        &self,
        kind: Rejection,
        passage_id: i64,
        mbid: Option<&str>,
        heard_ms: Option<u64>,
        span_ms: Option<u64>,
    ) {
        if let Some(s) = &self.store {
            if let Err(e) = s.record_rejection(kind, passage_id, mbid, heard_ms, span_ms) {
                tracing::error!("record {}: {e}", kind.as_str());
            }
        }
    }

    /// The rate `SPEC-MPD-105`'s setting configures, tightened near a
    /// boundary this backend already has enough to know about `[SPEC-MPD-110]`.
    ///
    /// Between boundaries a fixed interval is the right answer -- measurement
    /// found a uniformly faster clock is not worth its own cost. But two
    /// moments matter more than the rest: the **span end**, where a late
    /// sample is an *absolute* overrun of unwanted audio bounded only by this
    /// interval `[SPEC-MPD-096]`, and the **play/skip threshold**
    /// `[SPEC-PLAY-010]` of an unusually short passage, where a late sample
    /// only risks calling a play a skip. Estimated from the last poll's own
    /// position plus wall-clock time elapsed since -- MPD is not asked again
    /// just to answer this -- so it degrades to the configured interval
    /// whenever nothing is playing or nothing about the current passage is
    /// known yet.
    fn effective_interval(&self) -> Duration {
        if !self.playing {
            return self.interval;
        }
        let Some((passage_id, pos_at_poll)) = self.head else {
            return self.interval;
        };
        let Some(o) = self.ours.values().find(|o| o.passage_id == passage_id) else {
            return self.interval;
        };
        let since_poll =
            self.last_poll.map(|t| t.elapsed().as_millis() as u64).unwrap_or(0);
        let estimated_pos = pos_at_poll.saturating_add(since_poll);
        let to_span_end = o.span_ms.saturating_sub(estimated_pos);
        let threshold = (o.span_ms / 2).min(FOUR_MINUTES_MS);
        let to_threshold =
            if estimated_pos < threshold { threshold - estimated_pos } else { u64::MAX };
        let nearest = to_span_end.min(to_threshold);
        if nearest < self.interval.as_millis() as u64 {
            Duration::from_millis(nearest.max(BOUNDARY_POLL_FLOOR_MS))
        } else {
            self.interval
        }
    }

    /// One sample: where playback is, what has left the queue, and whether a
    /// span MPD would not honour has run past its end.
    fn poll(&mut self) {
        let status = match self.mpd.status() {
            Ok(s) => s,
            Err(e) => {
                self.mark_lost("status", &e);
                return;
            }
        };
        self.playing = status.get("state").map(|s| s == "play").unwrap_or(false);
        self.queue_len =
            status.get("playlistlength").and_then(|v| v.parse().ok()).unwrap_or(0);
        let current = status.get("songid").cloned();
        let elapsed_ms = status
            .get("elapsed")
            .and_then(|v| v.parse::<f64>().ok())
            .map(|s| (s * 1000.0).round() as u64)
            .unwrap_or(0);
        // **MPD's clock on a bounded song runs from the start of the span**,
        // measured rather than assumed `[SPEC-BK-055]`: a `rangeid` range
        // reports `duration` as the range's and `elapsed` from zero, and a cue
        // track does the same. So this is already a position within the
        // passage and needs no adjusting.
        self.head = match (&current, self.playing) {
            (Some(id), true) => self.ours.get(id).map(|o| (o.passage_id, elapsed_ms)),
            _ => None,
        };
        // Before anything else is decided from this sample: an output that has
        // stopped carrying samples reports a position that never changes
        // `[SPEC-MPD-135]`.
        self.watch_for_stall(elapsed_ms);

        // **A song this backend did not queue is still one the listener heard**
        // `[SPEC-MPD-115]`. Adopt it once, so its play is attributed and its
        // artist blocks like any other. Without this a person's own additions
        // sound and count for nothing.
        if let Some(id) = current.clone() {
            if !self.ours.contains_key(&id) {
                let uri = self
                    .mpd
                    .cmd("currentsong")
                    .map(|l| crate::mpd::parse(&l).get("file").cloned().unwrap_or_default())
                    .unwrap_or_default();
                match self.names.get(&uri) {
                    Some(Some((pid, mbid, file_ms))) => {
                        self.ours.insert(
                            id.clone(),
                            Offered {
                                passage_id: *pid,
                                uri: uri.clone(),
                                mbid: mbid.clone(),
                                // Queued whole, so the file's length is the
                                // span; there is no range to be relative to.
                                span_ms: *file_ms,
                                furthest_ms: 0,
                                heard_ms: 0,
                                seen_at_ms: None,
                                span_honoured: true,
                                was_current: true,
                            },
                        );
                    }
                    // Ambiguous, or not in the library at all. Reported once
                    // rather than guessed at `[SPEC-MPD-060]`, and once rather
                    // than on every sample.
                    _ if !uri.is_empty() && self.unnameable.insert(uri.clone()) => {
                        tracing::warn!(
                            "a queued song could not be named, so its play is not attributed: {}",
                            uri.rsplit('/').next().unwrap_or(&uri)
                        );
                    }
                    _ => {}
                }
            }
        }

        let mut overrun = false;
        if let Some(id) = &current {
            if let Some(o) = self.ours.get_mut(id) {
                o.was_current = true;
                if self.playing {
                    // Only forward movement, and only as much as one sample
                    // could have covered: a jump longer than the interval is
                    // a seek, and nobody heard the distance `[SPEC-PLAY-012]`.
                    let step = self.interval.as_millis() as u64 * 2;
                    if let Some(previous) = o.seen_at_ms {
                        o.heard_ms += elapsed_ms.saturating_sub(previous).min(step);
                    }
                    o.seen_at_ms = Some(elapsed_ms);
                    o.furthest_ms = o.furthest_ms.max(elapsed_ms);
                }
                // MPD will play to end of file where it dropped the range end,
                // so the passage is ended here instead `[SPEC-MPD-096]`. The
                // overrun is then bounded by the sample interval rather than by
                // the length of the file.
                overrun = !o.span_honoured && self.playing && o.furthest_ms >= o.span_ms;
            }
        }
        if overrun {
            let _ = self.mpd.cmd("next");
            // `next` cancels the output exactly as a seek does, and leaves it
            // silent `[SPEC-MPD-135]`. Measured: the clock froze and the
            // speaker went quiet on the passage after an unhonoured span.
            self.restart_output();
        }

        let order = match self.mpd.cmd("playlistinfo") {
            Ok(lines) => queue_of(&lines),
            Err(e) => {
                self.mark_lost("playlistinfo", &e);
                return;
            }
        };
        let live: HashSet<&str> = order.iter().map(|(id, _)| id.as_str()).collect();
        let gone: Vec<String> =
            self.ours.keys().filter(|id| !live.contains(id.as_str())).cloned().collect();
        self.order = order;
        for id in gone {
            if let Some(o) = self.ours.remove(&id) {
                self.retire(o);
            }
        }
    }
}

/// **The whole point of the sticker approach** `[SPEC-MPD-050]`: a client that
/// has never heard of Lempi is unaffected, and one that shows stickers gains a
/// "why this passage" panel without a line of code changing. Nothing is added to
/// MPD to make it work.
/// Where MPD says it is, from the last poll — and able to ask again now,
/// because a handoff cannot wait for the next scheduled one `[SPEC-BK-065]`.
impl crate::switch::Progress for MpdBackend {
    fn head_position(&self) -> Option<(i64, u64)> {
        self.head
    }

    fn adopt_counted(&mut self, passage_id: i64) {
        self.counted_elsewhere.insert(passage_id);
    }

    fn refresh(&mut self) {
        if !self.lost {
            self.last_poll = Some(Instant::now());
            self.poll();
        }
    }
}

impl crate::switch::Publish for MpdBackend {
    fn publish(&mut self, p: &crate::switch::Published<'_>) {
        let Some(uri) = self
            .ours
            .values()
            .find(|o| o.passage_id == p.passage_id)
            .map(|o| o.uri.clone())
        else {
            return; // never offered here, so there is nothing to hang it on
        };
        let mut set = |name: &str, value: &str| {
            if value.is_empty() {
                return;
            }
            let cmd =
                format!("sticker set song {} {} {}", quote(&uri), quote(name), quote(value));
            if let Err(e) = self.mpd.cmd(&cmd) {
                tracing::warn!("sticker {name}: {e}");
            }
        };
        set("lempi.why", p.why);
        set("lempi.flavor", p.flavor);
        // **Identity, because MPD cannot carry it** `[SPEC-MPD-052]`. A
        // capture's file tags name the album and no track, so a client reading
        // tags alone shows the album for every passage inside it. This is the
        // only place the real title exists on this side.
        set("lempi.title", p.title);
        set("lempi.artist", p.artist);
        set("lempi.chosen_at", &p.chosen_at.to_string());
    }
}

impl crate::switch::FadeOut for MpdBackend {
    fn fade_out(&mut self, ms: u64) -> crate::switch::Stopped {
        if self.fade_out_inner(ms) {
            crate::switch::Stopped::Faded
        } else {
            crate::switch::Stopped::Cut
        }
    }
}

impl Playback for MpdBackend {
    /// Spans yes, gain and ramps no `[SPEC-MPD-055]`. Declared rather than
    /// discovered when a passage plays at the wrong level.
    fn capabilities(&self) -> Capabilities {
        Capabilities::MPD
    }

    fn enqueue(&mut self, entry: QueueEntry) {
        // A handoff enqueues into a backend that may have been idle for hours,
        // and the next tick is too late to find out the socket died in the
        // meantime `[SPEC-MPD-130]`. If it cannot be rebuilt, fall through: the
        // failure is then reported as a drop rather than as a passage carried.
        self.reconnect();
        let span_ms = entry.end_ms.saturating_sub(entry.start_ms);

        // **A cue track names what a bare file cannot** `[SPEC-MPD-056]`. MPD
        // applies the cue's own boundaries as a range and reports the real
        // title, so `rangeid` is not called here — it would overwrite that
        // range with offsets into the *file* and lose both.
        //
        // The cue's end is the next track's start, which is a median 4.8 s
        // late against a radio span, so the passage is marked as one MPD will
        // not end correctly and the sampler ends it instead `[SPEC-MPD-096]`.
        // That is the same machinery, bounding the overrun by one interval.
        if let Some(uri) = self.cues.get(&entry.passage_id).cloned() {
            match self.mpd.cmd(&format!("addid {}", quote(&uri))) {
                Ok(lines) => {
                    if let Some(id) = crate::mpd::parse(&lines).get("Id").cloned() {
                        self.ours.insert(
                            id,
                            Offered {
                                passage_id: entry.passage_id,
                                uri,
                                mbid: entry.mbid.clone(),
                                span_ms,
                                furthest_ms: 0,
                                heard_ms: 0,
                                seen_at_ms: None,
                                span_honoured: false,
                                was_current: false,
                            },
                        );
                        self.queue_len += 1;
                        return;
                    }
                    tracing::warn!("addid returned no Id for cue track {uri}");
                }
                Err(e) => tracing::warn!("mpd addid {uri}: {e}"),
            }
            // A cue track that would not add is not a reason to give up on the
            // passage: the file underneath it still plays, just unnamed.
        }

        let Some(uri) = self.uri_for(&entry) else {
            // Outside MPD's music directory, so MPD cannot name it. It never
            // played, and the Director must un-count it `[REQ-PD-112]`.
            tracing::warn!("passage {} is not under MPD's music directory", entry.passage_id);
            self.dropped.push(entry.passage_id);
            return;
        };
        match self.mpd.add_ranged(&uri, entry.start_ms as i64, entry.end_ms as i64) {
            Ok(added) => {
                let _ = self.mpd.cmd(&format!(
                    "sticker set song {} {} {}",
                    quote(&uri),
                    quote("lempi.passage"),
                    quote(&entry.passage_id.to_string())
                ));
                self.ours.insert(
                    added.id,
                    Offered {
                        passage_id: entry.passage_id,
                        uri: uri.clone(),
                        mbid: entry.mbid.clone(),
                        span_ms,
                        furthest_ms: 0,
            heard_ms: 0,
            seen_at_ms: None,
                        span_honoured: added.span_honoured,
                        was_current: false,
                    },
                );
                self.queue_len += 1;
            }
            Err(e) => {
                // Refused for a reason to do with this passage, or refused
                // because there is no longer anyone to refuse it. The second
                // kind must be noticed here, or every later enqueue writes into
                // a closed socket and reports nothing wrong.
                self.mark_lost(&format!("enqueue {}", entry.passage_id), &e);
                self.dropped.push(entry.passage_id);
            }
        }
    }

    fn queued_ids(&self) -> Vec<i64> {
        in_queue_order(&self.order, &self.ours, &self.names)
    }

    fn queued_ms(&self) -> u64 {
        self.ours.values().map(|o| o.span_ms.saturating_sub(o.furthest_ms)).sum()
    }

    /// Zero unless MPD is playing `[SPEC-MPD-120]`, so the activation rule
    /// needs no special case anywhere else. Measured against MPD's **whole**
    /// queue, not only what this backend put there: a person who queued twenty
    /// tracks by hand has left nothing wanted `[SPEC-MPD-095]`.
    fn shortfall(&self) -> usize {
        if !self.playing {
            return 0;
        }
        self.depth.saturating_sub(self.queue_len)
    }

    fn take_dropped(&mut self) -> Vec<i64> {
        std::mem::take(&mut self.dropped)
    }

    /// Begin the **first** queued song at this offset.
    ///
    /// First in MPD's order, not first out of a hash map. `ours.keys().next()`
    /// picked an arbitrary one of them, so a handoff could seek — and so start
    /// — a passage from the middle of the queue rather than the one being
    /// handed over.
    ///
    /// `order` is appended to by `enqueue` rather than waiting for the next
    /// poll, because a handoff enqueues and seeks in the same breath: read
    /// from a poll-stale order this found nothing, sent no seek, and left the
    /// guest holding a full queue and not playing `[SPEC-BK-065]`.
    fn resume_at(&mut self, position_ms: u64) {
        let head_of_ours = |b: &Self| {
            b.order.iter().find(|(id, _)| b.ours.contains_key(id)).map(|(id, _)| id.clone())
        };
        // A handoff enqueues and resumes in the same breath, so the order read
        // at the last poll predates everything just added and matches nothing.
        // Read it again rather than seek blindly: sending no seek left the guest
        // holding a full queue and silent, which is what this looked like on the
        // appliance `[SPEC-BK-065]`.
        let first = head_of_ours(self).or_else(|| {
            self.poll();
            head_of_ours(self)
        });
        if let Some(id) = first {
            let _ = self.mpd.cmd(&format!("seekid {id} {:.3}", position_ms as f64 / 1000.0));
            // **`seekid` moves a paused player without starting it.** From
            // `stop` it begins playing, which is why this was not noticed until
            // MPD was restarted mid-session: `restore_paused "yes"` brings it
            // back *paused*, the seek landed at the right offset, and the
            // handoff completed into silence with `elapsed` advancing not at
            // all `[SPEC-MPD-130]`. `pause 0` is "not paused" rather than a
            // toggle, so it is a no-op on a player already playing.
            let _ = self.mpd.cmd("pause 0");
        }
    }

    /// Move inside the song MPD is playing now `[REQ-VIS-225]`.
    ///
    /// **Clamped, because MPD stops a song seeked past its span** — measured:
    /// `seekid 70` into a 60-second range returned `state=stop` rather than
    /// landing at the end `[SPEC-BK-055]`. The offset is already span-relative
    /// on both sides, so it crosses unaltered.
    ///
    /// Addressed to the song that is *current*, not to the first this backend
    /// offered: the listener may be on something they queued themselves
    /// `[SPEC-MPD-115]`, and seeking the wrong song is worse than not seeking.
    fn seek_to(&mut self, position_ms: u64) {
        let Some((id, span)) = self
            .head
            .and_then(|(passage, _)| {
                self.ours.iter().find(|(_, o)| o.passage_id == passage).map(|(id, o)| (id.clone(), o.span_ms))
            })
        else {
            return;
        };
        let at = position_ms.min(span.saturating_sub(SEEK_MARGIN_MS));
        if self.mpd.cmd(&format!("seekid {id} {:.3}", at as f64 / 1000.0)).is_err() {
            return;
        }
        // Immediately, rather than leaving it to the watchdog three seconds
        // later: this is an interactive action and the listener is waiting on
        // it `[SPEC-MPD-135]`.
        self.restart_output();
        // The clock has moved without anyone listening to the distance. Reading
        // it back on the next poll would otherwise credit the jump as heard
        // `[SPEC-PLAY-012]`.
        if let Some(o) = self.ours.get_mut(&id) {
            o.seen_at_ms = None;
        }
        self.head = self.head.map(|(passage, _)| (passage, at));
    }

    fn tick(&mut self) -> usize {
        // The session spins far faster than MPD should be asked anything.
        let interval = self.effective_interval();
        let due = self.last_poll.map(|t| t.elapsed() >= interval).unwrap_or(true);
        if due {
            self.last_poll = Some(Instant::now());
            if self.lost {
                self.reconnect();
            }
            if !self.lost {
                self.poll();
            }
        }
        0
    }

    /// Update the two listener-facing tunables live, without a reconnect
    /// `[SPEC-MPD-105]`. Until this existed, `depth`/`interval` were set once
    /// at `connect()` from the CLI flag and the compiled-in default, and never
    /// touched again -- changing either on the settings page while a guest was
    /// live had no effect here at all.
    fn apply_queue_settings(&mut self, depth: usize, sample_interval_ms: u64) {
        self.depth = depth;
        self.interval = Duration::from_millis(sample_interval_ms);
    }

    /// **A dead socket is not a shutdown until MPD has really gone.**
    ///
    /// This answer ends the process: `Switching` forwards `is_shutdown` to the
    /// live side, and `lempi`'s main loop runs until the live side says it is
    /// finished. Returning `true` the moment a write failed meant that
    /// restarting MPD — a package update, a config change — took the player
    /// down with it `[SPEC-MPD-130]`. Measured on the appliance: after an
    /// `mpd` restart the backend went silently inert, `enqueue` wrote to a
    /// closed socket, and a handoff reported six passages carried into an
    /// empty queue `[PI-CHR-095]`.
    fn is_shutdown(&self) -> bool {
        self.lost && self.lost_since.map(|t| t.elapsed() >= GIVE_UP_AFTER).unwrap_or(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The smallest thing that answers like MPD, and can be made to stop.
    ///
    /// **A connection that dies has to be a real one.** The failure being
    /// covered here — MPD restarting under a running player — is entirely
    /// about socket lifetime, and a mock that returns `Err` on demand would
    /// have agreed with the broken code as readily as with the fixed one.
    struct FakeMpd {
        addr: String,
        /// Bumped to hang up on whatever is currently connected.
        generation: std::sync::Arc<std::sync::atomic::AtomicU64>,
        /// False while MPD is "not running": connections are accepted by the
        /// operating system and then closed without a greeting, which is what
        /// a listener with nothing behind it does.
        up: std::sync::Arc<std::sync::atomic::AtomicBool>,
        /// Every command received, in order, so a test can assert what was
        /// *sent* rather than only what came back.
        seen: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
        /// What `status` should report, for standing in as a paused MPD or as
        /// one whose clock has stopped moving.
        state: std::sync::Arc<std::sync::Mutex<String>>,
        /// The position `status` reports. Fixed, so a stalled output is a
        /// position that does not change between polls — which is exactly how
        /// the real one presents `[SPEC-MPD-135]`.
        elapsed: std::sync::Arc<std::sync::Mutex<f64>>,
    }

    impl FakeMpd {
        fn start() -> Self {
            use std::io::{BufRead, BufReader, Write};
            use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
            use std::sync::Arc;

            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let addr = listener.local_addr().unwrap().to_string();
            let generation = Arc::new(AtomicU64::new(0));
            let up = Arc::new(AtomicBool::new(true));
            let seen = Arc::new(std::sync::Mutex::new(Vec::new()));
            let state = Arc::new(std::sync::Mutex::new("stop".to_string()));
            let elapsed = Arc::new(std::sync::Mutex::new(0.0f64));
            let (g, u) = (generation.clone(), up.clone());
            let (log, st, el) = (seen.clone(), state.clone(), elapsed.clone());
            std::thread::spawn(move || {
                for stream in listener.incoming() {
                    let Ok(mut sock) = stream else { return };
                    if !u.load(Ordering::SeqCst) {
                        continue; // dropped unanswered: nothing is listening
                    }
                    let mine = g.load(Ordering::SeqCst);
                    let (g, u) = (g.clone(), u.clone());
                    let (log, st, el) = (log.clone(), st.clone(), el.clone());
                    std::thread::spawn(move || {
                        if sock.write_all(b"OK MPD 0.23.5\n").is_err() {
                            return;
                        }
                        let read = BufReader::new(sock.try_clone().unwrap());
                        // MPD's queue, so `playlistinfo` can answer with what
                        // was actually added: `resume_at` reads that order to
                        // decide which song to seek, and against an always-empty
                        // queue it would find nothing and send nothing.
                        let mut queue: Vec<(u64, String)> = Vec::new();
                        let mut next_id = 100;
                        for line in read.lines() {
                            let Ok(line) = line else { return };
                            // Hung up on, the way a restarting MPD hangs up.
                            if g.load(Ordering::SeqCst) != mine || !u.load(Ordering::SeqCst) {
                                return;
                            }
                            log.lock().unwrap().push(line.clone());
                            let reply = if let Some(rest) = line.strip_prefix("addid ") {
                                next_id += 1;
                                queue.push((next_id, rest.trim_matches('"').to_string()));
                                format!("Id: {next_id}\nOK\n")
                            } else if line.starts_with("playlistid") {
                                "Time: 30\nOK\n".to_string()
                            } else if line == "playlistinfo" {
                                let mut out = String::new();
                                for (id, uri) in &queue {
                                    out.push_str(&format!("file: {uri}\nId: {id}\n"));
                                }
                                out.push_str("OK\n");
                                out
                            } else if line == "status" {
                                let song = queue
                                    .first()
                                    .map(|(id, _)| format!("songid: {id}\n"))
                                    .unwrap_or_default();
                                format!(
                                    "state: {}\nelapsed: {:.3}\n{}playlistlength: {}\nOK\n",
                                    st.lock().unwrap(),
                                    el.lock().unwrap(),
                                    song,
                                    queue.len()
                                )
                            } else {
                                "OK\n".to_string()
                            };
                            if sock.write_all(reply.as_bytes()).is_err() {
                                return;
                            }
                        }
                    });
                }
            });
            FakeMpd { addr, generation, up, seen, state, elapsed }
        }

        /// Come back paused, the way `restore_paused "yes"` does.
        fn paused(&self) {
            *self.state.lock().unwrap() = "pause".to_string();
        }

        /// Report playing, at a position that never moves — a wedged output.
        fn stuck_at(&self, seconds: f64) {
            *self.state.lock().unwrap() = "play".to_string();
            *self.elapsed.lock().unwrap() = seconds;
        }

        fn commands(&self) -> Vec<String> {
            self.seen.lock().unwrap().clone()
        }

        /// Hang up, and refuse to answer until `restart`.
        fn stop(&self) {
            use std::sync::atomic::Ordering;
            self.up.store(false, Ordering::SeqCst);
            self.generation.fetch_add(1, Ordering::SeqCst);
        }

        /// Hang up, but be there again immediately — a service restart.
        fn bounce(&self) {
            self.generation.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        }

        fn restart(&self) {
            self.up.store(true, std::sync::atomic::Ordering::SeqCst);
        }

        fn backend(&self) -> MpdBackend {
            // Zero interval: every tick polls, so a test is not a wait.
            MpdBackend::connect(&self.addr, "/music", 5, 0).unwrap()
        }
    }

    fn queued(passage_id: i64) -> QueueEntry {
        QueueEntry {
            qid: 0,
            passage_id,
            path: std::path::PathBuf::from("/music/a/b.mp3"),
            start_ms: 0,
            end_ms: 30_000,
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

    /// **The appliance failure, in a test** `[SPEC-MPD-130]` `[PI-CHR-095]`.
    ///
    /// MPD was restarted under a running Lempi. Every later command wrote into
    /// a closed socket, `enqueue` did nothing and said nothing, and a handoff
    /// announced six passages carried into an empty queue.
    #[test]
    fn a_dead_connection_refuses_work_out_loud() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        mpd.stop();
        b.tick();
        assert!(b.lost, "a failed poll is noticed");

        b.enqueue(queued(42));
        assert_eq!(b.take_dropped(), vec![42], "what it could not enqueue, it drops");
        assert!(b.queued_ids().is_empty(), "and it holds nothing");
    }

    /// Losing the socket must not take the player down with it: `Switching`
    /// forwards `is_shutdown` to the live side, and `lempi` runs until the live
    /// side says it is finished `[SPEC-MPD-130]`.
    #[test]
    fn a_restart_of_mpd_is_not_a_shutdown_of_lempi() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        mpd.stop();
        b.tick();
        assert!(b.lost);
        assert!(!b.is_shutdown(), "MPD being away is not Lempi being over");

        // Only after it has been away far longer than any restart takes.
        b.lost_since = Some(Instant::now() - GIVE_UP_AFTER - Duration::from_secs(1));
        assert!(b.is_shutdown(), "but an MPD that never comes back is");
    }

    /// And when it comes back, it is used again without anyone restarting
    /// anything `[SPEC-MPD-130]`.
    #[test]
    fn a_returning_mpd_is_picked_up_again() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        b.enqueue(queued(7));
        assert!(b.take_dropped().is_empty(), "accepted while the socket was good");

        mpd.bounce();
        b.tick();
        assert!(b.lost, "the hang-up is noticed");

        mpd.restart();
        b.tick();
        assert!(!b.lost, "and the next tick has it back");
        // Passage 7 was released by the reconnect, which is its own test below.
        assert_eq!(b.take_dropped(), vec![7]);

        b.enqueue(queued(8));
        assert!(b.take_dropped().is_empty(), "working again, with no restart of Lempi");
    }

    /// **A seek is not a start** `[SPEC-MPD-130]`.
    ///
    /// `seekid` begins playback from `stop`, so a handoff appeared to work for
    /// as long as MPD was only ever stopped. Restart MPD mid-session and
    /// `restore_paused "yes"` brings it back *paused*: the seek then landed at
    /// exactly the right offset and nothing was heard, while `status` reported
    /// the position the switch had asked for `[PI-CHR-095]`.
    #[test]
    fn resuming_a_paused_mpd_also_starts_it() {
        let mpd = FakeMpd::start();
        mpd.paused();
        let mut b = mpd.backend();
        b.enqueue(queued(7));
        b.resume_at(20_000);

        let sent = mpd.commands();
        let seek = sent.iter().position(|c| c.starts_with("seekid")).expect("it seeks");
        assert!(
            sent[seek..].iter().any(|c| c == "pause 0"),
            "and then says play, or the handoff lands silently: {sent:?}"
        );
    }

    /// **A seek wedges MPD's output on the appliance, so the seek restarts it**
    /// `[SPEC-MPD-135]`.
    ///
    /// Not a hypothetical: measured across seven output configurations, every
    /// one that carries sound at all stops carrying it after a `seekid`, while
    /// reporting `state: play` `[PI-CHR-100]`.
    #[test]
    fn a_seek_restarts_the_output_it_just_wedged() {
        let mpd = FakeMpd::start();
        mpd.stuck_at(5.0);
        let mut b = mpd.backend();
        b.enqueue(queued(7));
        b.tick(); // a poll, so the backend knows what is current
        b.seek_to(10_000);

        let sent = mpd.commands();
        let seek = sent.iter().rposition(|c| c.starts_with("seekid")).expect("it seeks");
        let after = &sent[seek..];
        assert!(after.iter().any(|c| c == "pause 1") && after.iter().any(|c| c == "pause 0"),
                "the output is restarted after the seek: {after:?}");
    }

    /// **And a seek somebody made in their own client wedges it just the same**
    /// `[SPEC-MPD-135]`.
    ///
    /// Lempi is not in that conversation — a guest client is the point of this
    /// backend `[SPEC-MPD-050]` — so the stall is caught at the poll instead.
    #[test]
    fn an_output_that_stops_moving_is_restarted_without_being_asked() {
        let mpd = FakeMpd::start();
        mpd.stuck_at(30.0);
        let mut b = mpd.backend();
        b.enqueue(queued(7));

        b.tick();
        assert!(b.stall.is_some(), "the position is being watched");
        let before = mpd.commands().len();
        b.tick();
        assert_eq!(mpd.commands()[before..].iter().filter(|c| *c == "pause 0").count(), 0,
                   "not on a second identical reading -- that is only two samples");

        // Now with the clock genuinely stopped for longer than a listener
        // should be asked to sit through.
        b.stall = b.stall.map(|(at, _)| (at, Instant::now() - STALL_AFTER - Duration::from_secs(1)));
        let before = mpd.commands().len();
        b.tick();
        let after = &mpd.commands()[before..];
        assert!(after.iter().any(|c| c == "pause 1") && after.iter().any(|c| c == "pause 0"),
                "a stalled output is restarted: {after:?}");
    }

    /// A player that is merely **paused** is not a stalled one, and must be
    /// left alone — restarting its output would resume playback nobody asked
    /// to resume.
    #[test]
    fn a_paused_mpd_is_not_mistaken_for_a_stalled_one() {
        let mpd = FakeMpd::start();
        mpd.paused();
        let mut b = mpd.backend();
        b.enqueue(queued(7));
        b.tick();
        assert!(b.stall.is_none(), "nothing to watch: it is not playing");

        let before = mpd.commands().len();
        b.tick();
        b.tick();
        assert!(!mpd.commands()[before..].iter().any(|c| c == "pause 0"),
                "a paused player is left paused");
    }

    /// **What MPD remembers across its own restart is not ours to assume.**
    /// Song ids are reassigned, so entries keyed by the old ones are released
    /// as dropped — un-counted by the Director — rather than retired as though
    /// somebody had watched them play or skip `[PI3-API-030]`.
    #[test]
    fn passages_held_under_old_song_ids_are_released_not_judged() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        b.enqueue(queued(7));
        b.enqueue(queued(8));
        assert!(b.take_dropped().is_empty());

        mpd.bounce();
        b.tick();
        mpd.restart();
        b.tick();

        let mut released = b.take_dropped();
        released.sort_unstable();
        assert_eq!(released, vec![7, 8], "named, so the Director can un-count them");
        assert!(b.ours.is_empty(), "and nothing is still keyed by a stale song id");
    }

    fn offered(passage_id: i64) -> Offered {
        Offered {
            passage_id,
            uri: String::new(),
            mbid: None,
            span_ms: 1_000,
            furthest_ms: 0,
            heard_ms: 0,
            seen_at_ms: None,
            span_honoured: true,
            was_current: false,
        }
    }

    /// **The listener's order, not a hash map's** `[SPEC-BK-030]`.
    ///
    /// What this returns is what crosses a handoff, and `carry_queue` re-enqueues
    /// in the order it is handed. Read straight out of `ours` it came back
    /// shuffled, so moving back to Lempi rearranged what was coming up.
    #[test]
    fn the_queue_crosses_in_the_order_mpd_holds_it() {
        let mut ours = HashMap::new();
        // Deliberately inserted in a different order from the queue's, which is
        // what a map would report and what the bug did report.
        ours.insert("30".to_string(), offered(300));
        ours.insert("10".to_string(), offered(100));
        ours.insert("20".to_string(), offered(200));

        let order = [
            ("10".to_string(), "a.mp3".to_string()),
            ("20".to_string(), "b.mp3".to_string()),
            ("30".to_string(), "c.mp3".to_string()),
        ];

        assert_eq!(in_queue_order(&order, &ours, &HashMap::new()), vec![100, 200, 300]);
    }

    /// **The settled rule `[SPEC-BK-045]`: what cannot be named is dropped, and
    /// the rest goes through in the listener's order.**
    ///
    /// A song Lempi never offered is the listener's own addition. Named where
    /// the library can name it, passed over where it cannot — an entry is
    /// unnameable when its file carries more than one radio passage, and a
    /// whole-file entry could be any of up to forty of them.
    #[test]
    fn what_the_listener_queued_crosses_too_and_what_cannot_be_named_does_not() {
        let mut ours = HashMap::new();
        ours.insert("10".to_string(), offered(100));

        let mut names: HashMap<String, Nameable> = HashMap::new();
        names.insert("mine.mp3".into(), Some((200, None, 1_000)));
        // In the library, but a capture: it could be any of its passages.
        names.insert("capture.mp3".into(), None);

        let order = [
            ("10".to_string(), "ours.mp3".to_string()),
            ("20".to_string(), "mine.mp3".to_string()),
            ("30".to_string(), "capture.mp3".to_string()),
            ("40".to_string(), "unknown.mp3".to_string()),
        ];

        assert_eq!(
            in_queue_order(&order, &ours, &names),
            vec![100, 200],
            "ours, then theirs; the ambiguous and the unknown are left behind"
        );
    }

    /// `playlistinfo` interleaves each song's fields, so the `Id:` after a
    /// `file:` is the one that belongs to it.
    #[test]
    fn the_queue_is_read_as_pairs_in_order() {
        let lines: Vec<String> = [
            "file: a.mp3", "Last-Modified: x", "Time: 100", "Pos: 0", "Id: 7",
            "file: b.mp3", "Time: 200", "Pos: 1", "Id: 9",
        ]
        .iter()
        .map(|s| s.to_string())
        .collect();

        assert_eq!(
            queue_of(&lines),
            vec![
                ("7".to_string(), "a.mp3".to_string()),
                ("9".to_string(), "b.mp3".to_string())
            ]
        );
    }

    fn offered_at(passage_id: i64, span_ms: u64, at_ms: u64) -> Offered {
        Offered {
            passage_id,
            uri: "a.mp3".into(),
            mbid: None,
            span_ms,
            furthest_ms: at_ms,
            heard_ms: 0,
            seen_at_ms: None,
            span_honoured: true,
            was_current: true,
        }
    }

    /// `effective_interval` degrades to the configured interval whenever
    /// nothing is known to be playing, or the passage is far from either
    /// boundary — the common case, and the one `[SPEC-MPD-110]` decided was
    /// not worth a uniformly faster clock on its own.
    #[test]
    fn effective_interval_is_unchanged_far_from_any_boundary() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        b.interval = Duration::from_secs(5);
        assert_eq!(b.effective_interval(), Duration::from_secs(5), "nothing is playing yet");

        b.playing = true;
        b.head = Some((1, 30_000));
        b.ours.insert("9".to_string(), offered_at(1, 240_000, 30_000));
        assert_eq!(
            b.effective_interval(),
            Duration::from_secs(5),
            "far from both the span end and the play threshold"
        );
    }

    /// A close span end earns a tighter interval, the case `[SPEC-MPD-096]`
    /// measured as an absolute overrun bounded only by the old fixed interval.
    #[test]
    fn effective_interval_tightens_near_the_span_end() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        b.interval = Duration::from_secs(5);
        b.playing = true;
        b.head = Some((1, 239_000)); // one second from a 240s span
        b.ours.insert("9".to_string(), offered_at(1, 240_000, 239_000));

        let got = b.effective_interval();
        assert!(got < Duration::from_secs(5), "tightened rather than the fixed 5s: {got:?}");
        assert!(
            got >= Duration::from_millis(BOUNDARY_POLL_FLOOR_MS),
            "never tighter than the floor: {got:?}"
        );
    }

    /// A short passage's play/skip threshold `[SPEC-PLAY-010]` earns the same
    /// treatment as the span end — both are boundaries `[SPEC-MPD-110]` names.
    #[test]
    fn effective_interval_tightens_near_the_play_threshold() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        b.interval = Duration::from_secs(5);
        b.playing = true;
        // A 20s passage: threshold is min(10s, 4min) = 10s. At 9.8s in, the
        // threshold is 200ms away -- much closer than the 10.2s-away span end.
        b.head = Some((1, 9_800));
        b.ours.insert("9".to_string(), offered_at(1, 20_000, 9_800));

        let got = b.effective_interval();
        assert!(got <= Duration::from_millis(500), "the threshold is 200ms away: {got:?}");
    }

    /// The floor stops a boundary a few milliseconds away from turning
    /// "sample to meet it" into a busy loop against a server that still has
    /// to answer each poll `[SPEC-MPD-110]`.
    #[test]
    fn effective_interval_never_polls_tighter_than_the_floor() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        b.interval = Duration::from_secs(5);
        b.playing = true;
        b.head = Some((1, 239_999)); // one millisecond from the span end
        b.ours.insert("9".to_string(), offered_at(1, 240_000, 239_999));

        assert_eq!(b.effective_interval(), Duration::from_millis(BOUNDARY_POLL_FLOOR_MS));
    }

    /// A live setting change reaches this backend without a reconnect
    /// `[SPEC-MPD-105]` — until this method existed, a change made while a
    /// guest was live was only ever visible to the (possibly idle) local
    /// engine.
    #[test]
    fn queue_settings_apply_live() {
        let mpd = FakeMpd::start();
        let mut b = mpd.backend();
        b.apply_queue_settings(8, 2_500);
        assert_eq!(b.depth, 8);
        assert_eq!(b.interval, Duration::from_millis(2_500));
    }

    /// A URI is the path with the music directory taken off the front — rung 1
    /// of the ladder `[SPEC-MPD-060]`, and the only rung this backend uses when
    /// it is the one doing the naming.
    #[test]
    fn a_path_under_the_root_becomes_a_uri() {
        let root = "C:/Users/x/Music";
        let p = std::path::Path::new;
        assert_eq!(uri_under(root, p("C:/Users/x/Music/A/b.mp3")).as_deref(), Some("A/b.mp3"));
        // Backslashes are what the library actually stores on this platform.
        let windows_style = "C:\\Users\\x\\Music\\A\\b.mp3";
        assert_eq!(uri_under(root, p(windows_style)).as_deref(), Some("A/b.mp3"));
        assert_eq!(
            uri_under(root, p("D:/Elsewhere/c.mp3")),
            None,
            "outside the root is not nameable, and must not be guessed at"
        );
    }
}
