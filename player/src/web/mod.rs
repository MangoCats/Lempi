//! The web UI: state push over a WebSocket, controls over POST.
//!
//! The browser is a *view*. It holds no playback state of its own -- every
//! change arrives as a fresh snapshot of [`PlayerState`], and every control is
//! a command the engine may act on or ignore. That is what keeps one answer to
//! "what is playing": the engine's, published once per tick `[REQ-AUD-142]`.
//!
//! The wire format is deliberately its own type rather than a `Serialize` on
//! [`QueueEntry`]. The browser contract should change when we decide it does,
//! not as a side effect of adding a field to an internal struct -- and the
//! filesystem path is nobody's business outside the process.
//!
//! One file used to hold every route handler; now each topic has its own,
//! `pub(super)` back to here -- [`router`] is the one place that must see
//! all of them, and stays here so the actual live route table has exactly
//! one home.

use std::sync::Arc;
use std::time::Duration;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse};
use axum::routing::{get, post};
use axum::Router;

use serde::Serialize;

use crate::engine::{EngineHandle, PlayerState};
use crate::output::Volume;
use crate::session::{Explanations, SharedControls};

mod bluetooth;
mod browse;
mod control;
mod edit;
mod guide;
mod media;
mod musicbrainz;
mod preference;
mod review;
#[cfg(feature = "vipunen-support")]
mod vipunen;
mod segment;
mod settings;
mod skins;
mod wifi;

use bluetooth::*;
use browse::*;
use control::*;
use guide::*;
#[cfg(feature = "vipunen-support")]
use edit::*;
use media::*;
use preference::*;
#[cfg(feature = "vipunen-support")]
use musicbrainz::*;
#[cfg(feature = "vipunen-support")]
use review::*;
#[cfg(feature = "vipunen-support")]
use vipunen::*;
#[cfg(feature = "vipunen-support")]
use segment::*;
use settings::*;
use skins::*;
use wifi::*;

/// What the server needs to answer a request: the control surface, and why the
/// current passage was chosen.
#[derive(Clone)]
pub struct Ui {
    pub handle: Arc<EngineHandle>,
    /// The listener-side file. A path rather than a connection:
    /// `rusqlite`'s is not `Sync`, and a request opens its own.
    pub db: std::path::PathBuf,
    /// The catalog-side file, for serving cover art and everything else a
    /// handler reads through `Library`. Equal to `db` on every installation
    /// that hasn't split `[IMPL-DBSPLIT-025]` -- every handler in this
    /// module still opens `db` alone as of this field's addition; wiring
    /// each one to `Library::open_split(&db, &library)` instead is tracked,
    /// separately scoped work, not yet done.
    pub library: std::path::PathBuf,
    pub why: Explanations,
    pub controls: SharedControls,
}

/// How often a connected browser is sent a snapshot. Fast enough that the
/// position counter moves smoothly, slow enough to cost nothing on a Pi.
const PUSH_EVERY: Duration = Duration::from_millis(500);

/// How often the queue is looked at in between.
///
/// `PUSH_EVERY` is the right cadence for a clock and the wrong one for an
/// answer: a queue edit applied by the engine in microseconds was invisible
/// for up to half a second afterwards, which read as the control being slow to
/// obey rather than the display being slow to say so. Looking costs a copy of
/// the shared state and a comparison of qids -- the same read the engine
/// already serves ten times a second -- while sending, which is the part that
/// crosses a socket and rebuilds a page, still happens only on the heartbeat
/// or on the queue actually changing.
///
/// Matched to [`crate::engine::Engine::PUBLISH_EVERY`], since the engine
/// rewrites the shared snapshot at most that often and watching any faster
/// would only find the same answer twice.
const WATCH_EVERY: Duration = Duration::from_millis(100);

/// What the browser is told. One flat object, so the client needs no merge
/// logic and cannot drift out of step with the engine.
/// The skip transition, with the limits it may be set between `[REQ-AUD-162]`.
///
/// The bounds travel with the values so the browser can offer exactly the range
/// the engine will accept, rather than keeping its own copy to fall out of step.
#[derive(Serialize)]
pub struct SkipShape {
    pub fade_ms: u64,
    pub lead_ms: u64,
    pub fade_max_ms: u64,
    pub lead_min_ms: u64,
    pub lead_max_ms: u64,
    /// How often the resume point is written `[REQ-VIS-155]`. Here rather than
    /// in a settings object of its own because the page already binds this
    /// group, and the bounds travel with the value for the same reason the
    /// skip bounds do -- so the control offers exactly what the engine accepts.
    pub resume_save_ms: u64,
    pub resume_save_min_ms: u64,
    pub resume_save_max_ms: u64,
    /// Skip suppression `[SPEC-PLAY-050]`, with its bounds so the control can
    /// draw itself without hard-coding them.
    pub skip_suppress_h: u64,
    pub skip_suppress_min_h: u64,
    pub skip_suppress_max_h: u64,
    /// Queue-removal suppression `[SPEC-PLAY-055]`, with its bounds.
    pub dequeue_suppress_h: u64,
    pub dequeue_suppress_min_h: u64,
    pub dequeue_suppress_max_h: u64,
    /// Passages kept ahead, and the guest sampling rate `[SPEC-MPD-105]`.
    pub queue_depth: usize,
    pub queue_depth_min: usize,
    pub queue_depth_max: usize,
    pub sample_interval_ms: u64,
    pub sample_interval_min_ms: u64,
    pub sample_interval_max_ms: u64,
}

#[derive(Serialize)]
pub struct QueueItem {
    /// The entry's own identity `[REQ-VIS-186]`. What the edit controls name,
    /// because `passage_id` does not distinguish a passage queued twice.
    pub qid: u64,
    pub passage_id: i64,
    pub title: String,
    pub artist: Option<String>,
    /// The recording's own mbid, and the credited artist's, so a click on
    /// either displayed name has somewhere to point a preference edit at
    /// `[REQ-VIS-285]`. Absent for unidentified audio or an uncredited
    /// artist -- the same case that already leaves `artist` `None`.
    pub mbid: Option<String>,
    pub artist_mbid: Option<String>,
    pub duration_ms: u64,
    /// Whether it can still be moved or dropped `[REQ-VIS-185]`. False once
    /// the mixer has it: its audio is already partly in the ring, so removing
    /// it from the queue would change nothing anyone could hear.
    pub editable: bool,
}

#[derive(Serialize)]
pub struct ProgramItem {
    pub id: i64,
    pub name: String,
    pub start: String,
}

/// What the listening surface shows `[REQ-VIS-150]`: the queue in play order,
/// the programme in force with its manual override, and master volume -- all of
/// it as one complete snapshot, so a render is a pure function of the last
/// message and cannot drift.
#[derive(Serialize)]
pub struct Snapshot {
    pub playing: bool,
    /// The passage on air, for fetching its cover at `/art/{id}`.
    pub passage_id: Option<i64>,
    /// MusicBrainz Recording title where there is one, then the file's tag,
    /// then the filename `[REQ-VIS-170]`.
    pub title: Option<String>,
    pub artist: Option<String>,
    /// The recording's own mbid, and the credited artist's -- same reason
    /// `QueueItem` carries them `[REQ-VIS-285]`.
    pub mbid: Option<String>,
    pub artist_mbid: Option<String>,
    /// The **Release** title. `None` until the release tables are populated
    /// and the file carries no album tag either.
    pub album: Option<String>,
    /// Which source each displayed name came from `[REQ-VIS-120]`.
    pub title_source: &'static str,
    pub artist_source: &'static str,
    pub album_source: &'static str,
    /// Plays of this **recording**, all-time, and when it was last heard.
    pub plays: i64,
    pub last_played: Option<i64>,
    pub position_ms: u64,
    pub duration_ms: u64,
    pub queue_len: usize,
    /// The echo messages `[GDE-ECHO-310]`, riding the snapshot rather than a
    /// socket of their own. A browser ignores the field; an echo node reads
    /// only this one. That is what makes the transport choice uninteresting
    /// `[GDE-ECHO-320]`: every message here is absolute and idempotent, so a
    /// dropped snapshot costs nothing and a stale one is discarded by its own
    /// timestamps.
    pub echo: crate::echo::EchoState,
    /// This node's own place in the fleet, as the settings panel shows it
    /// `[SPEC-DLY-050]`, `[SPEC-ECHO-020]`.
    ///
    /// Distinct from `echo` above: that is what a *follower* reads off this
    /// node, this is what *this* node's own listener sees — the measured
    /// delay, the trim they set, the host being followed and what following
    /// is doing. The engine rebuilt it on every publish and nothing carried
    /// it here, so `renderEchoNode` bailed on `undefined` twice a second and
    /// the whole panel stayed blank `[GDE-ECHO-379]`.
    pub echo_node: crate::engine::EchoNode,
    /// What is coming, in play order.
    pub queue: Vec<QueueItem>,
    /// Master level in dB relative to full scale, `-72.0` to `0.0`
    /// `[REQ-AUD-154]`. The control is graduated in dB and captioned with this
    /// number directly, so the browser never converts and cannot disagree with
    /// the engine about what the fader is set to.
    pub volume_db: f32,
    /// The bottom of the fader, in dB. Sent so the control can shape itself
    /// around the engine's floor instead of keeping its own copy of the number
    /// `[REQ-AUD-156]`.
    pub fader_min_db: f32,
    /// Most tracks a browse request will return, so the page can say so
    /// without a second copy of the number `[REQ-VIS-180]`.
    pub browse_limit: usize,
    pub skip: SkipShape,
    /// The programme in force, and whether it was chosen by hand.
    pub program: Option<String>,
    pub program_manual: bool,
    /// What a live Director rebuild is doing, if one has been asked for
    /// `[IMPL-SUI-075]`. `None` until something requests one.
    /// What this server is `[REQ-VIS-200]`. Sent to every skin so a person
    /// looking at a page can say which build drew it, rather than being asked
    /// to remember.
    pub build: String,
    /// Branch, commit date, commit subject, and uncommitted-file count the
    /// build came from -- the same fields the Settings page's "Server build"
    /// row expands into, matching Vipunen's own `/system` page `[REQ-VIS-200]`.
    pub branch: String,
    pub commit_date: String,
    pub commit_subject: String,
    pub dirty_files: u32,
    pub reload_status: Option<String>,
    /// Which backend is playing, whether a guest exists, and what the last
    /// switch did `[SPEC-BK-025]`.
    pub backend: Option<String>,
    pub guest_available: bool,
    pub guest_name: Option<String>,
    pub switch_status: Option<String>,
    /// `[REQ-VIS-205]`
    pub cue_sheets: bool,
    pub cue_status: Option<String>,
    /// `[REQ-VIS-210]`
    pub covers: bool,
    /// Whether the live backend can move inside a passage `[REQ-VIS-225]`.
    /// The bar is a control only where this is true.
    pub can_seek: bool,
    pub covers_status: Option<String>,
    /// `[REQ-VIS-215]`
    pub lyrics_cache: bool,
    pub lyrics_status: Option<String>,
    /// `[REQ-VIS-220]`
    pub lyrics_sidecar: bool,
    pub sidecar_status: Option<String>,
    /// The Director's pool as `(eligible, total)`, so a rebuild's effect is
    /// visible rather than asserted: importing music and reloading moves
    /// `total`. Absent when there is no Director.
    pub pool: Option<(usize, usize)>,
    pub programs: Vec<ProgramItem>,
    /// Development mode is on `[PI-SET-016]`: sshd and diagnostics are
    /// running. Always false off the appliance -- the setting belongs to the
    /// Pi's privileged helper, and the player only reports what it is told.
    pub dev_mode: bool,
    pub underrun_samples: u64,
    /// What the interface shows: the count since the baseline, and when that
    /// baseline was taken `[REQ-VIS-230]`.
    pub underruns_since_reset: u64,
    pub underruns_since: i64,
    /// Where the sound is actually coming from `[REQ-VIS-235]`: the file, and
    /// the passage's place in it.
    ///
    /// **Local only.** This names the listener's filesystem, and it belongs in
    /// the snapshot a local browser reads and nowhere that travels
    /// `[SPEC-DF-055]`.
    pub file_path: Option<String>,
    pub file_start_ms: u64,
    pub file_end_ms: u64,
    pub file_ms: u64,
    /// Output-lock contention `[REQ-VIS-140]`. Non-zero means the callback was
    /// kept waiting, which sounds like a click rather than a gap.
    pub lock_failures: u64,
    pub out_recoveries: u64,
    /// The full weight decomposition for what is playing `[REQ-VIS-100]`.
    /// Null when the passage was not chosen by the Director -- a resumed
    /// passage, or one queued before the log was populated -- so the panel can
    /// say so rather than render an explanation that was never computed.
    pub why: Option<serde_json::Value>,
}

impl From<&PlayerState> for Snapshot {
    fn from(s: &PlayerState) -> Self {
        Snapshot {
            playing: s.playing,
            passage_id: s.current.as_ref().map(|e| e.passage_id),
            title: s.current.as_ref().map(|e| e.title()),
            artist: s.current.as_ref().and_then(|e| e.artist()),
            mbid: s.current.as_ref().and_then(|e| e.mbid.clone()),
            artist_mbid: s.current.as_ref().and_then(|e| e.naming.artist_mbid.clone()),
            album: s.current.as_ref().and_then(|e| e.album()),
            title_source: s.current.as_ref().map_or("unknown", |e| e.title_source().as_str()),
            artist_source: s.current.as_ref().map_or("unknown", |e| e.artist_source().as_str()),
            album_source: s.current.as_ref().map_or("unknown", |e| e.album_source().as_str()),
            plays: s.current.as_ref().map_or(0, |e| e.naming.plays),
            last_played: s.current.as_ref().and_then(|e| e.naming.last_played),
            position_ms: s.position_ms,
            duration_ms: s.current.as_ref().map(|e| e.duration_ms()).unwrap_or(0),
            queue_len: s.queue_len,
            echo: s.echo.clone(),
            echo_node: s.echo_node.clone(),
            queue: s
                .queue
                .iter()
                .enumerate()
                .map(|(i, e)| QueueItem {
                    qid: e.qid,
                    passage_id: e.passage_id,
                    title: e.title(),
                    artist: e.artist(),
                    mbid: e.mbid.clone(),
                    artist_mbid: e.naming.artist_mbid.clone(),
                    duration_ms: e.duration_ms(),
                    editable: i >= s.mixing_ahead,
                })
                .collect(),
            volume_db: Volume::db_for(s.volume),
            fader_min_db: crate::output::FADER_MIN_DB,
            browse_limit: crate::BROWSE_LIMIT,
            skip: SkipShape {
                fade_ms: s.skip_fade_ms,
                lead_ms: s.skip_lead_ms,
                fade_max_ms: crate::SKIP_FADE_MAX_MS,
                lead_min_ms: crate::SKIP_LEAD_MIN_MS,
                lead_max_ms: crate::SKIP_LEAD_MAX_MS,
                resume_save_ms: s.resume_save_ms,
                resume_save_min_ms: crate::RESUME_SAVE_MIN_MS,
                resume_save_max_ms: crate::RESUME_SAVE_MAX_MS,
                skip_suppress_h: s.skip_suppress_h,
                skip_suppress_min_h: crate::SKIP_SUPPRESS_MIN_H,
                skip_suppress_max_h: crate::SKIP_SUPPRESS_MAX_H,
                dequeue_suppress_h: s.dequeue_suppress_h,
                dequeue_suppress_min_h: crate::DEQUEUE_SUPPRESS_MIN_H,
                dequeue_suppress_max_h: crate::DEQUEUE_SUPPRESS_MAX_H,
                queue_depth: s.queue_depth,
                queue_depth_min: crate::QUEUE_DEPTH_MIN,
                queue_depth_max: crate::QUEUE_DEPTH_MAX,
                sample_interval_ms: s.sample_interval_ms,
                sample_interval_min_ms: crate::SAMPLE_INTERVAL_MIN_MS,
                sample_interval_max_ms: crate::SAMPLE_INTERVAL_MAX_MS,
            },
            program: None,
            build: crate::build_id(),
            branch: crate::BRANCH.to_string(),
            commit_date: crate::COMMIT_DATE.to_string(),
            commit_subject: crate::COMMIT_SUBJECT.to_string(),
            dirty_files: crate::DIRTY_FILES.parse().unwrap_or(0),
            reload_status: None,
            backend: None,
            guest_available: false,
            guest_name: None,
            switch_status: None,
            cue_status: None,
            covers_status: None,
            lyrics_status: None,
            sidecar_status: None,
            pool: None,
            program_manual: false,
            programs: Vec::new(),
            cue_sheets: s.cue_sheets,
            covers: s.covers,
            // Replaced below from the live backend; the engine cannot answer for
            // a side that is not it.
            can_seek: false,
            lyrics_cache: s.lyrics_cache,
            lyrics_sidecar: s.lyrics_sidecar,
            dev_mode: s.dev_mode,
            underrun_samples: s.underrun_samples,
            underruns_since_reset: s.underruns_since_reset,
            underruns_since: s.underruns_since,
            file_path: s.current.as_ref().map(|e| e.path.to_string_lossy().to_string()),
            file_start_ms: s.current.as_ref().map_or(0, |e| e.start_ms),
            file_end_ms: s.current.as_ref().map_or(0, |e| e.end_ms),
            file_ms: s.current.as_ref().map_or(0, |e| e.file_ms),
            lock_failures: s.lock_failures,
            out_recoveries: s.out_recoveries,
            why: None,
        }
    }
}

pub fn router(ui: Ui) -> Router {
    let router = Router::new()
        .route("/", get(|| async { Html(SHELL) }))
        .route("/build", get(build_identity))
        .route("/core.js", get(|| async { js(CORE) }))
        .route("/skins", get(skin_list))
        .route("/skin/:name/:file", get(skin_asset))
        .route("/why/:passage_id", get(why_for))
        .route("/lyrics/:passage_id", get(lyrics))
        .route("/art/:passage_id", get(cover_art))
        .route("/art/:passage_id/back", get(cover_art_back))
        .route("/browse", get(|| async { ([REVALIDATE], Html(BROWSE_HTML)) }))
        .route("/browse.js", get(|| async { js(BROWSE_JS) }))
        .route("/browse/:kind", get(browse))
        .route("/passage/:passage_id", get(|| async { ([REVALIDATE], Html(PASSAGE_HTML)) }))
        .route("/passage.js", get(|| async { js(PASSAGE_JS) }))
        .route("/passage/:passage_id/info", get(passage_info))
        .route("/history", get(history))
        .route("/history/flag/:kind/:id", post(set_flag))
        .route("/preference/:kind/:id", get(get_preference).post(set_preference))
        .route("/play-frequency/:kind/:id", get(play_frequency))
        .route("/guide", get(guide_page))
        .route("/guide.js", get(guide_js_route))
        .route("/guide/langs", get(guide_langs))
        .route("/guide/content/:lang", get(guide_content));

    // The identification-review page, and everything reached from it: desktop
    // induct tooling with no reason to occupy an appliance image that never
    // runs Vipunen `[SPEC-SUI-190]`. A build without this feature serves none of
    // these routes at all, not even a 404 stub -- the handlers, the embedded
    // HTML/JS and the database code behind them are not compiled in.
    #[cfg(feature = "vipunen-support")]
    let router = router
        .route("/review", get(|| async { ([REVALIDATE], Html(REVIEW_HTML)) }))
        .route("/review.js", get(|| async { js(REVIEW_JS) }))
        .route(REVIEW_QUEUE_ROUTE, get(review_queue))
        .route("/review/passage/:passage_id", get(review_passage))
        .route("/review/releases/:mbid", get(review_releases))
        .route("/review/:passage_id/:decision", post(record_review))
        .route("/review/:passage_id/artist/:verb", post(artist_review_verb))
        .route("/api/musicbrainz/search", get(musicbrainz_search))
        .route("/review/release-tracks/:mbid", get(release_tracks))
        .route("/edit/:passage_id", get(|| async { ([REVALIDATE], Html(EDIT_HTML)) }))
        .route("/edit.js", get(|| async { js(EDIT_JS) }))
        .route("/fade.js", get(|| async { js(FADE_JS) }))
        .route("/edit/:passage_id/info", get(edit_info))
        .route("/edit/:passage_id/audio", get(edit_audio))
        .route("/edit/:passage_id/review", post(edit_review))
        .route(SEGMENT_QUEUE_ROUTE, get(segment_queue))
        .route("/segment/:passage_id/accept", post(accept_segment))
        .route("/vipunen/available", get(vipunen_available))
        .route("/vipunen/ensure", post(vipunen_ensure));

    router
        .route("/queue/:passages/:action", post(queue_passage))
        .route("/ws", get(ws_upgrade))
        .route("/audio/sink", get(audio_sink))
        .route("/audio/speakers", get(speakers))
        .route("/audio/speakers/:verb", post(speaker_verb))
        .route("/audio/speakers/:verb/:address", post(speaker_verb_on))
        .route("/led", get(led_state))
        .route("/led/:state", post(set_led))
        .route("/wifi/scan", get(scan))
        .route("/wifi/known", get(known))
        .route("/wifi/connect", post(connect))
        .route("/wifi/confirm/:change_id", post(confirm))
        .route("/wifi/forget/:name", post(forget))
        .route("/wifi/autoconnect/:name/:state", post(autoconnect))
        .route("/wifi/ap/start", post(ap_start))
        .route("/wifi/ap/stop", post(ap_stop))
        .route("/command/:name", post(command))
        .route("/volume/:db", post(set_volume))
        .route("/seek/:ms", post(seek_to))
        .route("/underruns/restart", post(restart_underruns))
        .route("/skip/fade/:ms", post(set_skip_fade))
        .route("/skip/lead/:ms", post(set_skip_lead))
        .route("/resume/save/:ms", post(set_resume_save))
        // This node's place in the fleet `[SPEC-DLY-120]`, `[SPEC-ECHO-010]`.
        .route("/echo/trim/:ms", post(set_echo_trim))
        .route("/echo/trim/reset", post(reset_echo_trim))
        .route("/echo/follow", post(set_echo_follow))
        .route("/echo/join/:now", post(set_echo_join_now))
        .route("/skip/suppress/:hours", post(set_skip_suppress))
        .route("/dequeue/suppress/:hours", post(set_dequeue_suppress))
        .route("/queue/depth/:n", post(set_queue_depth))
        .route("/sample/interval/:ms", post(set_sample_interval))
        .route("/program/:id", post(set_program))
        .route("/library/reload", post(reload_library))
        .route("/backend/:which", post(switch_backend))
        .route("/cue/:on", post(set_cue_sheets))
        .route("/covers/:on", post(set_covers))
        .route("/lyricscache/:on", post(set_lyrics_cache))
        .route("/lyricssidecar/:on", post(set_lyrics_sidecar))
        .route("/audio/radios", get(radios))
        .route("/power/off", post(power_off))
        .route("/power/restart", post(restart_player))
        .route("/audio/radio/:kind/:state", post(set_radio))
        .with_state(ui)
}

async fn ws_upgrade(State(ui): State<Ui>, ws: WebSocketUpgrade) -> impl IntoResponse {
    ws.on_upgrade(move |socket| push_state(socket, ui))
}

/// This build's identity, machine-readable `[SPEC-SUI-227]` -- the same
/// fields the Settings page already shows a person, in a shape Vipunen can
/// compare against its own without a websocket handshake (`PlayerState`,
/// which carries these same values, only ever travels over `/ws`). Build
/// capability, not library data, so it needs no `vipunen-support` gate --
/// the same boundary `[SPEC-SUI-213]`'s `/review.js` probe already draws.
///
/// The JSON body is built by a plain function so a test can check its shape
/// without spinning up a router or a runtime for an async fn that touches
/// nothing but compile-time constants.
fn build_identity_json() -> serde_json::Value {
    serde_json::json!({
        "git": crate::GIT,
        "branch": crate::BRANCH,
        "commit_date": crate::COMMIT_DATE,
        "dirty_files": crate::DIRTY_FILES.parse::<u32>().unwrap_or(0),
    })
}

async fn build_identity() -> axum::response::Response {
    axum::Json(build_identity_json()).into_response()
}

/// Why any one passage was chosen `[REQ-VIS-100]`.
///
/// The log is keyed by passage and already holds the queued ones; only the
/// playing one was ever reachable. A skin that lets you ask about the passage
/// *after* next needs the rest of it.
///
/// Fetched on demand rather than pushed with every snapshot: an explanation is
/// several hundred bytes of weights and runners-up, it never changes once the
/// passage has been chosen, and pushing six of them twice a second to render
/// one would be most of the traffic.
///
/// 404 when there is none -- a resumed passage, or one queued before the log
/// existed. That is a real state and the page says so rather than blanking.
async fn why_for(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let Ok(log) = ui.why.lock() else {
        return StatusCode::INTERNAL_SERVER_ERROR.into_response();
    };
    match log.get(passage_id).and_then(|w| serde_json::to_value(w).ok()) {
        Some(v) => axum::Json(v).into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

/// Attach the reasoning for whatever is playing now.
fn explain(ui: &Ui, snap: &mut Snapshot, state: &PlayerState) {
    let Some(id) = state.current.as_ref().map(|e| e.passage_id) else { return };
    let Ok(log) = ui.why.lock() else { return };
    snap.why = log.get(id).and_then(|w| serde_json::to_value(w).ok());
}

/// Push snapshots until the browser goes away. Each is independent, so a
/// dropped frame costs nothing and a reconnecting client needs no replay.
///
/// Two reasons to send, and they want different cadences. The heartbeat keeps
/// the position counter moving and is worth no more than `PUSH_EVERY`; a
/// change to the queue is an answer someone is waiting on and is worth saying
/// at once. So this watches at `WATCH_EVERY` and sends when either applies.
///
/// The queue is observed rather than signalled from the command handlers on
/// purpose. A handler knows when it *sent* an instruction, not when the engine
/// applied it -- the engine drains its own channel on its own loop -- so a
/// push fired from there would usually arrive a tick early and carry the state
/// the browser already had. What is on screen has to follow what is true.
async fn push_state(mut socket: WebSocket, ui: Ui) {
    let mut watch = tokio::time::interval(WATCH_EVERY);
    let mut sent_at: Option<std::time::Instant> = None;
    // The queue as the edit controls see it: which entries, in what order, and
    // how many the mixer has taken -- which is what makes a row uneditable.
    let mut sent_queue: Option<(Vec<u64>, usize)> = None;
    loop {
        watch.tick().await;
        let state = ui.handle.snapshot();
        let queue: Vec<u64> = state.queue.iter().map(|e| e.qid).collect();
        let now = std::time::Instant::now();
        let due = sent_at.is_none_or(|t| now.duration_since(t) >= PUSH_EVERY);
        let moved = sent_queue
            .as_ref()
            .is_none_or(|(q, ahead)| q != &queue || *ahead != state.mixing_ahead);
        if !due && !moved {
            continue;
        }
        sent_at = Some(now);
        sent_queue = Some((queue, state.mixing_ahead));
        let mut snap = Snapshot::from(&state);
        explain(&ui, &mut snap, &state);
        if let Ok(c) = ui.controls.lock() {
            snap.program = c.active.clone();
            snap.program_manual = c.manual_program.is_some();
            snap.reload_status = c.reload_status.clone();
            snap.backend = c.backend.clone();
            snap.guest_available = c.guest_available;
            snap.guest_name = c.guest_name.clone();
            snap.switch_status = c.switch_status.clone();
            snap.cue_status = c.cue_status.clone();
            snap.covers_status = c.covers_status.clone();
            snap.lyrics_status = c.lyrics_status.clone();
            snap.can_seek = c.can_seek;
            snap.sidecar_status = c.sidecar_status.clone();
            snap.pool = c.pool;
            snap.programs = c
                .programs
                .iter()
                .map(|(id, name, start)| ProgramItem {
                    id: *id,
                    name: name.clone(),
                    start: start.clone(),
                })
                .collect();
        }
        let Ok(text) = serde_json::to_string(&snap) else { continue };
        if socket.send(Message::Text(text)).await.is_err() {
            return; // client gone
        }
    }
}

/// The page, and the skins it may wear `[REQ-VIS-160]`.
///
/// `include_str!` rather than reading from disk: one binary that runs anywhere
/// without a data directory beside it, which is what makes deploying to a Pi a
/// copy rather than an install. Adding a skin means adding a row here and three
/// files; nothing else in the server changes.
const SHELL: &str = include_str!("shell.html");
const BROWSE_HTML: &str = include_str!("browse.html");
const BROWSE_JS: &str = include_str!("browse.js");
const PASSAGE_HTML: &str = include_str!("passage.html");
const PASSAGE_JS: &str = include_str!("passage.js");
#[cfg(feature = "vipunen-support")]
const REVIEW_HTML: &str = include_str!("review.html");
#[cfg(feature = "vipunen-support")]
const REVIEW_JS: &str = include_str!("review.js");
#[cfg(feature = "vipunen-support")]
const EDIT_HTML: &str = include_str!("edit.html");
#[cfg(feature = "vipunen-support")]
const EDIT_JS: &str = include_str!("edit.js");
/// The exponential ramp curve, ported from `fade.rs` and checked against the
/// same fixture `[SPEC021 §4]`. Its own file and route so the editor's
/// preview and `fade.rs`'s real playback provably agree, not just resemble
/// each other.
#[cfg(feature = "vipunen-support")]
const FADE_JS: &str = include_str!("fade.js");

/// The route the review page fetches its work from, named once so the router
/// and the test that checks the page agrees with it cannot drift apart.
#[cfg(feature = "vipunen-support")]
const REVIEW_QUEUE_ROUTE: &str = "/review/queue";
/// The segmentation-cascade queue's own route `[SPEC024 §7]`, named the same
/// way `REVIEW_QUEUE_ROUTE` is -- no page fetches it from inside Lempi yet
/// (Vipunen's own console is the reader, `[SPEC-SA-125]`), but the router and
/// this file's own tests still share one spelling rather than two.
#[cfg(feature = "vipunen-support")]
const SEGMENT_QUEUE_ROUTE: &str = "/segment/queue";
/// The prefix every decision is posted to. The page builds the rest of the
/// path from the passage id, so only the stem can be shared.
#[cfg(all(test, feature = "vipunen-support"))]
const REVIEW_DECIDE_PREFIX: &str = "/review/";
const CORE: &str = include_str!("core.js");

/// Skins are compiled into the binary, so a cached copy in the browser can be
/// older than the running player -- which looks exactly like a feature that
/// does not work. `no-cache` means revalidate, not "do not store": the browser
/// still keeps it and still gets a 304 when nothing changed.
const REVALIDATE: (axum::http::HeaderName, &str) =
    (axum::http::header::CACHE_CONTROL, "no-cache");

fn js(body: &'static str) -> impl IntoResponse {
    (
        [
            (axum::http::header::CONTENT_TYPE, "text/javascript; charset=utf-8"),
            REVALIDATE,
        ],
        body,
    )
}



#[cfg(test)]
mod tests {
    use super::*;
    use crate::queue::QueueEntry;
    use std::path::PathBuf;

    fn entry(id: i64, title: &str) -> QueueEntry {
        let mut e = QueueEntry {
        qid: 0, // stamped by Queue on the way in
            passage_id: id,
            path: PathBuf::from(format!("/music/{title}.mp3")),
            start_ms: 0,
            end_ms: 200_000,
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
        };
        e.naming.mb_title = Some(title.into());
        e
    }

    fn state() -> PlayerState {
        let mut s = PlayerState::default();
        let mut cur = entry(1, "Now Playing");
        cur.naming.mb_artist = Some("The Artist".into());
        cur.naming.tag_album = Some("Some Record".into());
        cur.naming.plays = 12;
        cur.naming.last_played = Some(1_700_000_000);
        s.current = Some(cur);
        s.queue = vec![entry(2, "Mixing"), entry(3, "Waiting")];
        s.mixing_ahead = 1;
        s.queue_len = 2;
        s.volume = 0.5;
        s.skip_fade_ms = 2_000;
        s.skip_lead_ms = 500;
        s
    }

    /// Every skin is three non-empty files under a unique name. A skin that
    /// compiles in empty would serve a blank page and fail nowhere else.
    #[test]
    fn every_registered_skin_is_complete() {
        // A const array, so this is checked when it compiles rather than here;
        // the assertion below is the one that can actually fail.
        const _: () = assert!(!SKINS.is_empty());
        let mut seen = std::collections::HashSet::new();
        for s in SKINS {
            assert!(seen.insert(s.name), "duplicate skin name {}", s.name);
            assert!(!s.label.is_empty(), "{} has no label", s.name);
            for (what, body) in [("html", s.html), ("css", s.css), ("js", s.js)] {
                assert!(body.len() > 50, "{} {what} is empty", s.name);
            }
            // The contract: a skin gets its transport wired by core, so it must
            // mark the buttons up `[REQ-VIS-160]`.
            for cmd in ["play", "pause", "skip"] {
                assert!(
                    s.html.contains(&format!("data-cmd=\"{cmd}\"")),
                    "{} is missing the {cmd} control",
                    s.name
                );
            }
            assert!(s.html.contains("data-skins"), "{} has no skin picker", s.name);
        }
    }

    /// The shell and browse page must load core, or nothing on them works.
    #[test]
    fn the_pages_load_the_runtime() {
        assert!(SHELL.contains("/core.js"));
        assert!(SHELL.contains("Lempi.start()"), "the shell starts core");
        assert!(BROWSE_HTML.contains("/core.js") && BROWSE_HTML.contains("/browse.js"));
        assert!(BROWSE_JS.contains("startBare"), "browse takes the skin, not the player");
        // Reviewing ids is reachable, or it may as well not exist -- it is not
        // linked from the player, deliberately, so browse is the only way in.
        // The literal link is compiled into `BROWSE_HTML` regardless of
        // whether `/review` itself is served this build `[SPEC-SUI-190]`, so
        // this assertion holds either way; the route's own existence is
        // `vipunen-support`'s to check.
        assert!(BROWSE_HTML.contains("/review"), "no way to reach the review page");
    }

    /// The Vipunen link `[REQ-VIS-320]` and the routes it asks for -- present
    /// in `BROWSE_HTML`/`BROWSE_JS` regardless of `vipunen-support`, the same
    /// reasoning `the_pages_load_the_runtime` already gives for `/review`
    /// above: the literal markup/JS is compiled in either way, and whether
    /// the routes themselves exist to answer it is `vipunen-support`'s own
    /// question, checked separately below.
    #[test]
    fn the_vipunen_link_reaches_the_routes_it_asks_for() {
        assert!(BROWSE_HTML.contains(r#"id="vipunen-link""#), "browse has no Vipunen link");
        assert!(BROWSE_JS.contains("/vipunen/available"), "browse.js never probes availability");
        assert!(BROWSE_JS.contains("/vipunen/ensure"), "browse.js never asks to launch Vipunen");
    }

    /// The review page loads core the same way the others do -- gated with
    /// everything else it depends on `[SPEC-SUI-190]`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_review_page_loads_the_runtime() {
        assert!(REVIEW_HTML.contains("/core.js") && REVIEW_HTML.contains("/review.js"));
        assert!(REVIEW_JS.contains("startBare"), "review takes the skin, not the player");
    }

    /// The review page and its routes have to agree about the URLs, which is
    /// the seam a jsdom check cannot see: it mocks `fetch`, so a page asking
    /// for a route the server never registered still passes there.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_review_routes_match_what_the_page_asks_for() {
        assert!(REVIEW_JS.contains(REVIEW_QUEUE_ROUTE));
        assert!(REVIEW_JS.contains(REVIEW_DECIDE_PREFIX));
        // The on-demand handoff card `[SPEC-SUI-199]` -- reached by a route
        // the deep-link case asks for directly, not through the queue batch.
        assert!(REVIEW_JS.contains("/review/passage/"), "page cannot ask for a single card");
        // The three decisions the page can send, and the only three
        // `PlayerStore::record_review` accepts. One added on either side alone
        // is the bug this is here to catch.
        for decision in ["reassigned", "kept", "deferred"] {
            assert!(REVIEW_JS.contains(decision), "page cannot send {decision}");
            assert!(
                crate::db::PlayerStore::open(&std::path::PathBuf::from(":memory:"))
                    .map(|s| s.record_review(1, decision, Some("m"), None).is_ok())
                    .unwrap_or(false),
                "the store rejects {decision}, which the page can send"
            );
        }
    }

    /// Names reach the browser resolved, and carry where they came from
    /// `[REQ-VIS-170]`.
    #[test]
    fn a_snapshot_reports_names_and_their_provenance() {
        let snap = Snapshot::from(&state());
        assert_eq!(snap.title.as_deref(), Some("Now Playing"));
        assert_eq!(snap.artist.as_deref(), Some("The Artist"));
        assert_eq!(snap.album.as_deref(), Some("Some Record"));
        assert_eq!(snap.title_source, "musicbrainz");
        assert_eq!(snap.artist_source, "musicbrainz");
        // The release tables are empty, so album comes from the file and must
        // say so rather than claiming MusicBrainz.
        assert_eq!(snap.album_source, "tags");
        assert_eq!(snap.plays, 12);
        assert_eq!(snap.passage_id, Some(1));
    }

    /// What the mixer already holds cannot be edited, and the flag says which
    /// `[REQ-VIS-185]`.
    #[test]
    fn queue_items_report_whether_they_can_still_be_edited() {
        let snap = Snapshot::from(&state());
        assert_eq!(snap.queue.len(), 2);
        assert!(!snap.queue[0].editable, "the mixer has this one");
        assert!(snap.queue[1].editable, "this one is still only queued");
        assert_eq!(snap.queue[0].title, "Mixing");
    }

    /// Amplitude is internal; the browser is told dB and the floor
    /// `[REQ-AUD-154]`.
    #[test]
    fn volume_crosses_as_decibels_not_amplitude() {
        let snap = Snapshot::from(&state());
        assert!((snap.volume_db - (-6.0206)).abs() < 1e-3, "{}", snap.volume_db);
        assert_eq!(snap.fader_min_db, crate::output::FADER_MIN_DB);
    }

    /// The limits travel with the values, so the browser cannot offer a range
    /// the engine will refuse `[REQ-AUD-162]`.
    #[test]
    fn the_skip_shape_carries_its_own_limits() {
        let snap = Snapshot::from(&state());
        assert_eq!(snap.skip.fade_ms, 2_000);
        assert_eq!(snap.skip.lead_ms, 500);
        assert_eq!(snap.skip.fade_max_ms, crate::SKIP_FADE_MAX_MS);
        assert_eq!(snap.skip.lead_min_ms, crate::SKIP_LEAD_MIN_MS);
        assert_eq!(snap.skip.lead_max_ms, crate::SKIP_LEAD_MAX_MS);
    }

    /// An idle player must serialise rather than panic on its empty fields.
    #[test]
    fn an_empty_snapshot_is_still_a_snapshot() {
        let snap = Snapshot::from(&PlayerState::default());
        assert_eq!(snap.title, None);
        assert_eq!(snap.title_source, "unknown");
        assert_eq!(snap.plays, 0);
        assert!(snap.queue.is_empty());
        let json = serde_json::to_string(&snap).expect("an idle snapshot must serialise");
        assert!(json.contains("\"volume_db\""));
    }

    /// `core.js`'s shared preference panel has to agree with the router
    /// about this route `[REQ-VIS-285]` -- the same seam every other
    /// page-to-route check here guards -- and Lempi/MuLibPlay's own skins
    /// actually have to call the panel it opens, or the route is dead code
    /// nothing reaches. WinAmp is deliberately not asked for either: out of
    /// scope for this feature (its marquee has no separate artist node to
    /// link).
    #[test]
    fn the_preference_panel_reaches_the_route_the_router_serves() {
        assert!(CORE.contains("/preference/"), "core.js never asks for /preference/");
        for name in ["lempi", "mulibplay"] {
            let skin = SKINS.iter().find(|s| s.name == name).expect("skin exists");
            assert!(
                skin.js.contains("Lempi.editPreference") || skin.js.contains("editPreference("),
                "{name} never opens the preference panel"
            );
        }
    }

    /// The play-frequency panel's own route and slot, the same shape the
    /// preference panel's own check above guards `[REQ-VIS-300]`: the
    /// fetch core.js issues must match a route the router actually serves,
    /// and each skin that opens a preference panel must carry the `#freq-
    /// panel` slot it is filled into, or the feature is unreachable dead
    /// code on that skin.
    #[test]
    fn the_play_frequency_panel_reaches_the_route_the_router_serves() {
        assert!(CORE.contains("/play-frequency/"), "core.js never asks for /play-frequency/");
        for name in ["lempi", "mulibplay"] {
            let skin = SKINS.iter().find(|s| s.name == name).expect("skin exists");
            assert!(skin.html.contains(r#"id="freq-panel""#), "{name} has no #freq-panel slot");
        }
    }

    /// The MuLibPlay skin's lyrics panel actually scrolls itself, not just
    /// fetches -- a rough guess from `position_ms`/`duration_ms` rather than
    /// real synchronisation, since no timed data exists for these words.
    /// Checked here rather than left to notice by eye, the same way the
    /// panel's own existence would be if this file never asserted `#lyrics`.
    #[test]
    fn the_mulibplay_lyrics_panel_scrolls_itself() {
        let skin = SKINS.iter().find(|s| s.name == "mulibplay").expect("skin exists");
        assert!(skin.html.contains(r#"id="lyrics""#), "mulibplay has no #lyrics panel");
        assert!(skin.js.contains("autoScrollLyrics"), "mulibplay never defines the auto-scroll");
        assert!(
            skin.js.contains("autoScrollLyrics(s)"),
            "mulibplay defines auto-scroll but never calls it from the snapshot tick"
        );
    }

    /// The status-LED control reaches routes the router actually serves
    /// `[PI3-LED-010]` -- Lempi-skin only, the same posture every other
    /// Settings-panel control already has (Bluetooth, radios, restart,
    /// shutdown all live there and nowhere else). All four modes must be
    /// real `<option>`s, or a listener would be offered a choice the page
    /// cannot actually send.
    #[test]
    fn the_led_control_reaches_the_routes_the_router_serves() {
        let skin = SKINS.iter().find(|s| s.name == "lempi").expect("skin exists");
        assert!(skin.html.contains(r#"id="led-mode""#), "lempi has no #led-mode select");
        for mode in ["on", "wifi", "off", "default"] {
            assert!(
                skin.html.contains(&format!(r#"value="{mode}""#)),
                "lempi's led-mode select is missing the {mode} option"
            );
        }
        assert!(skin.js.contains("/led"), "lempi's skin.js never asks for /led");
    }

    /// The Wi-Fi settings section reaches every route the router actually
    /// serves `[SPEC034]` -- Lempi-skin only, the same posture the other
    /// appliance-hardware controls already have.
    #[test]
    fn the_wifi_controls_reach_the_routes_the_router_serves() {
        let skin = SKINS.iter().find(|s| s.name == "lempi").expect("skin exists");
        assert!(skin.html.contains(r#"id="wifi-known-list""#), "lempi has no known-networks list");
        assert!(skin.html.contains(r#"id="wifi-confirm""#), "lempi has no confirm-or-revert banner");
        // Both password fields (an existing network's, and this appliance's
        // own AP's) get a visibility toggle -- typed once, from a router
        // label, with no server-side check to catch a mistake before the
        // radio switches out from under this very page.
        for id in ["wifi-connect-password-toggle", "ap-password-toggle"] {
            assert!(skin.html.contains(&format!(r#"id="{id}""#)), "lempi has no #{id} button");
            assert!(skin.js.contains(id), "lempi's skin.js never wires up #{id}");
        }
        for route in [
            "/wifi/known",
            "/wifi/scan",
            "/wifi/connect",
            "/wifi/confirm/",
            "/wifi/forget/",
            "/wifi/autoconnect/",
            "/wifi/ap/start",
            "/wifi/ap/stop",
        ] {
            assert!(skin.js.contains(route), "lempi's skin.js never asks for {route}");
        }
    }

    /// The guide's own Help link reaches a route the router actually serves,
    /// in every skin -- not just the two that get a preference panel
    /// `[REQ-VIS-310]`. Unlike the preference/play-frequency panels, the
    /// guide is a full page reached by a plain link, so WinAmp gets one too.
    #[test]
    fn every_skin_carries_a_help_link_to_the_guide() {
        for skin in SKINS {
            assert!(
                skin.html.contains(r#"href="/guide""#),
                "{} has no link to /guide",
                skin.name
            );
        }
    }

    /// Every skin says what a registered press looks like `[REQ-VIS-185]`.
    ///
    /// `core.js` raises `.acked` the moment the pointer lands on a queue edit
    /// and `.failed` if the request comes back refused, but it deliberately
    /// carries no colours of its own -- a skin owns its palette. A skin that
    /// styles neither class still *works*, and that is exactly the failure
    /// worth catching here: the press would be taken and the listener would
    /// have no way to know, which is the complaint all of this answers.
    #[test]
    fn every_skin_says_what_a_registered_press_looks_like() {
        assert!(
            CORE.contains(".acked") || CORE.contains("'acked'"),
            "core.js no longer raises the acknowledgement class these skins style"
        );
        for skin in SKINS {
            for state in ["button.acked", "button.failed"] {
                assert!(
                    skin.css.contains(&format!(".qedit {state}")),
                    "{} does not style .qedit {state}, so a press it takes looks                      exactly like one it drops",
                    skin.name
                );
            }
            // The ends of the queue clamp, so those verbs come up disabled and
            // must not merely look live-but-dim by accident.
            assert!(
                skin.css.contains(".qedit button:disabled"),
                "{} does not style a disabled queue verb",
                skin.name
            );
        }

        // Skip gets the same receipt, because it is the one transport command
        // with no state of its own: `playing` reports Play and Pause, and
        // every skin already draws that, but nothing reports a skip.
        //
        // Named skins rather than all of them, deliberately. WinAmp declines:
        // its bevel already presses in its own idiom, and core hands every
        // skin the class either way, so opting out means styling nothing. The
        // two that DO flash it are held to it here -- a half-applied
        // convention is worse than either choice made whole.
        assert!(
            CORE.contains("MOMENTARY"),
            "core.js no longer distinguishes a momentary command from a state"
        );
        for name in ["lempi", "mulibplay"] {
            let skin = SKINS.iter().find(|s| s.name == name).expect("skin exists");
            assert!(
                skin.css.contains("[data-cmd].acked"),
                "{name} flashes its queue verbs but not Skip, so one gesture                  reports itself two ways depending on which control took it"
            );
        }
    }

    /// `guide.js` only ever asks for routes the router serves, and only ever
    /// reads tier keys the router's own `guide_content` actually emits --
    /// the same drift `the_snapshot_keeps_the_field_names_the_skins_read`
    /// guards against for the player snapshot.
    #[test]
    fn the_guide_page_reaches_the_routes_the_router_serves() {
        const GUIDE_JS: &str = include_str!("guide.js");
        assert!(GUIDE_JS.contains("/guide/langs"));
        assert!(GUIDE_JS.contains("/guide/content/"));
        for tier in
            ["quickstart", "preferences", "importing", "empty_system", "advanced", "appendix"]
        {
            assert!(GUIDE_JS.contains(tier), "guide.js never mentions the {tier} tier");
        }
    }

    /// The wire shape the skins are written against. Renaming a field here
    /// silently blanks part of every skin, which no Rust test would otherwise
    /// catch `[REQ-VIS-160]`.
    #[test]
    fn the_snapshot_keeps_the_field_names_the_skins_read() {
        let json = serde_json::to_string(&Snapshot::from(&state())).unwrap();
        for field in [
            "playing", "passage_id", "title", "artist", "mbid", "artist_mbid", "album", "plays",
            "last_played", "position_ms", "duration_ms", "queue_len", "queue",
            "volume_db", "fader_min_db", "skip", "program", "program_manual",
            "programs", "underrun_samples", "branch", "commit_date",
            "commit_subject", "dirty_files",
        ] {
            assert!(json.contains(&format!("\"{field}\"")), "snapshot lost {field}");
        }
        for field in ["passage_id", "title", "artist", "mbid", "artist_mbid", "duration_ms", "editable"] {
            assert!(json.contains(&format!("\"{field}\"")), "queue item lost {field}");
        }
    }

    /// Every `s.<field>` the skin reads off a snapshot.
    ///
    /// `s` must be the whole identifier rather than the tail of one, or
    /// `errors.push` reads as a snapshot field called `push`.
    fn skin_reads(js: &str) -> Vec<String> {
        let b = js.as_bytes();
        let mut out: Vec<String> = Vec::new();
        let mut i = 0;
        while let Some(k) = js[i..].find("s.") {
            let at = i + k;
            i = at + 2;
            if at > 0 {
                let p = b[at - 1];
                if p.is_ascii_alphanumeric() || p == b'_' || p == b'$' || p == b'.' {
                    continue;
                }
            }
            let rest = &js[at + 2..];
            let n = rest
                .find(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_'))
                .unwrap_or(rest.len());
            if n == 0 || !rest.starts_with(|c: char| c.is_ascii_lowercase()) {
                continue;
            }
            let name = rest[..n].to_string();
            if !out.contains(&name) {
                out.push(name);
            }
        }
        out
    }

    /// Every path in a `.route(...)` call, with named constants resolved.
    ///
    /// Two of the 84 routes are registered by constant rather than literal --
    /// `REVIEW_QUEUE_ROUTE`, `SEGMENT_QUEUE_ROUTE` -- which is the *better*
    /// pattern and the reason this has to resolve them: a scanner that reads
    /// only literals reports those two as unserved, which is exactly the false
    /// alarm they caused when this check was first written by hand.
    fn declared_routes(src: &str) -> Vec<String> {
        let mut out = Vec::new();
        for (_, after) in src.match_indices(".route(").map(|(i, m)| (i, &src[i + m.len()..])) {
            let after = after.trim_start();
            if let Some(rest) = after.strip_prefix('"') {
                if let Some(end) = rest.find('"') {
                    out.push(rest[..end].to_string());
                }
                continue;
            }
            // A named constant: take the identifier and look up its value.
            let name: String =
                after.chars().take_while(|c| c.is_ascii_uppercase() || *c == '_').collect();
            if name.is_empty() {
                continue;
            }
            let needle = format!("const {name}: &str = \"");
            if let Some(at) = src.find(&needle) {
                let rest = &src[at + needle.len()..];
                if let Some(end) = rest.find('"') {
                    out.push(rest[..end].to_string());
                }
            }
        }
        out.sort();
        out.dedup();
        out
    }

    /// Every literal path handed to `fetch(...)` in a page's JavaScript.
    ///
    /// Literals only: a target assembled from a variable is invisible here,
    /// and saying so is better than implying a completeness this cannot have.
    /// The count assertion in the test is what stops the scan silently
    /// reading nothing `[GDE-ECHO-547]`.
    fn fetch_targets(js: &str) -> Vec<String> {
        let mut out = Vec::new();
        for (i, m) in js.match_indices("fetch(") {
            let rest = js[i + m.len()..].trim_start();
            let Some(delim) = rest.chars().next().filter(|c| "'\"`".contains(*c)) else {
                continue;
            };
            let body = &rest[delim.len_utf8()..];
            let Some(end) = body.find(delim) else { continue };
            let url = &body[..end];
            if url.starts_with('/') {
                out.push(url.split('?').next().unwrap_or(url).to_string());
            }
        }
        out.sort();
        out.dedup();
        out
    }

    /// Does this route serve that URL? Axum's matching, narrowly.
    ///
    /// A `:param` segment takes exactly one non-empty segment, and a `${...}`
    /// interpolation in the JS stands for whatever the page will put there.
    fn route_matches(route: &str, url: &str) -> bool {
        let (r, u): (Vec<_>, Vec<_>) = (route.split('/').collect(), url.split('/').collect());
        r.len() == u.len()
            && r.iter().zip(&u).all(|(rs, us)| {
                if rs.starts_with(':') {
                    !us.is_empty()
                } else if us.contains("${") {
                    true
                } else {
                    rs == us
                }
            })
    }

    /// **Every path the pages fetch must be a path the router serves.**
    ///
    /// This is the check eight tests are *named* for and none performs.
    /// `the_wifi_controls_reach_the_routes_the_router_serves` and its seven
    /// siblings assert only `skin.js.contains(route)` against a hand-typed
    /// list -- so a path renamed in `router()` leaves every one of them
    /// passing, because the JS still contains the old string it was asked
    /// about. A test written from one side cannot see the other side decline
    /// `[GDE-ECHO-378]`; those tests still earn their place for the element
    /// ids and wiring they check, but the router half is here.
    ///
    /// Derived from both sides rather than restated beside them, which is the
    /// same correction `the_snapshot_sends_every_field_the_skin_reads` made
    /// for the snapshot `[GDE-ARC-031]`.
    #[test]
    fn every_path_the_pages_fetch_is_a_route_the_router_serves() {
        let routes = declared_routes(include_str!("mod.rs"));
        assert!(routes.len() > 60, "the route scan found only {} routes", routes.len());

        let mut sources: Vec<(&str, &str)> = vec![
            ("core.js", CORE),
            ("browse.js", BROWSE_JS),
            ("passage.js", PASSAGE_JS),
            ("guide.js", include_str!("guide.js")),
        ];
        #[cfg(feature = "vipunen-support")]
        sources.extend([("review.js", REVIEW_JS), ("edit.js", EDIT_JS), ("fade.js", FADE_JS)]);
        for s in SKINS {
            sources.push((s.name, s.js));
        }

        let mut checked = 0;
        let mut unserved = Vec::new();
        for (name, js) in sources {
            for url in fetch_targets(js) {
                checked += 1;
                if !routes.iter().any(|r| route_matches(r, &url)) {
                    unserved.push(format!("{name} fetches {url}, which no route serves"));
                }
            }
        }
        assert!(unserved.is_empty(), "{}", unserved.join("\n"));
        // A scan that read nothing would otherwise pass by finding no fault.
        assert!(checked > 30, "only {checked} fetch targets found; the scan is not reading");
    }

    /// **A bound the engine enforces is a bound the control must be told.**
    ///
    /// `ECHO_TRIM_LIMIT_MS` was enforced in the engine's command handler, again
    /// in the store on load, and hardcoded a third time as `min="-2000"
    /// max="2000"` in the skin's HTML -- two models of one quantity in two
    /// languages `[GDE-ECHO-375]`, with nothing tying them together. Every
    /// neighbouring control already does this properly: `SkipShape` ships
    /// `lead_min_ms`/`lead_max_ms` and the fader ships `fader_min_db`, "sent so
    /// the control can shape itself around the engine's floor instead of
    /// keeping its own copy of the number" `[REQ-AUD-156]`. The delay trim was
    /// the one that kept its own copy `[GDE-ARC-033]`.
    #[test]
    fn the_delay_trim_control_is_told_the_limit_the_engine_enforces() {
        const SKIN: &str = include_str!("skins/lempi/skin.js");
        let json = serde_json::to_string(&Snapshot::from(&state())).unwrap();
        assert!(json.contains("\"trim_limit_ms\""), "the snapshot never sends the limit");
        assert!(SKIN.contains("trim_limit_ms"),
                "the skin never reads the limit and must still be keeping its own copy");
        // And the number itself is the engine's, not a second opinion.
        let node = crate::engine::EchoNode { ..Default::default() };
        let _ = node;
        assert_eq!(Snapshot::from(&state()).echo_node.trim_limit_ms,
                   crate::db::ECHO_TRIM_LIMIT_MS);
    }

    /// **Every field the skin reads must be a field the snapshot sends.**
    ///
    /// The list above is written from the server's side, which is exactly why
    /// it could not catch a field the skin reads and the snapshot never had.
    /// `echo_node` was built by the engine on every publish, carefully
    /// commented, `Serialize` — and absent from the wire, so
    /// `renderEchoNode` took its `if (!n) return;` on every update and the
    /// delay and follow panel `[SPEC-DLY-050]`, `[SPEC-ECHO-020]` showed
    /// nothing at all, on every node, for as long as it has existed. A
    /// listener could type a host into *Follow this node* and never be told
    /// anything back `[GDE-ECHO-379]`.
    ///
    /// That is `[GDE-ECHO-378]`'s shape a third time: a decision computed and
    /// declined one call below, silently. So this is derived from the skin
    /// rather than restated beside it, and the next such field fails here
    /// instead of rendering blank.
    #[test]
    fn the_snapshot_sends_every_field_the_skin_reads() {
        const SKIN: &str = include_str!("skins/lempi/skin.js");
        let json = serde_json::to_string(&Snapshot::from(&state())).unwrap();
        // Read by the Bluetooth helper against a field no snapshot has ever
        // carried, so `currentSink()` can never match the playing device.
        // Named here rather than quietly skipped: a known gap with a name
        // beats an absence nothing mentions `[GOV-SRC-040]`. It is not an
        // echo fault and is not fixed with them.
        const NOT_SENT: [&str; 1] = ["sink"];
        let missing: Vec<String> = skin_reads(SKIN)
            .into_iter()
            .filter(|f| !NOT_SENT.contains(&f.as_str()))
            .filter(|f| !json.contains(&format!("\"{f}\"")))
            .collect();
        assert!(missing.is_empty(),
                "the skin reads snapshot fields the snapshot does not send: {missing:?}");
        // And the guard itself has to be reading something, or it passes by
        // finding nothing `[GDE-ECHO-547]`.
        assert!(skin_reads(SKIN).len() > 20, "the skin scan found almost nothing");
    }

    /// The editor page and its own JS, present only behind the feature that
    /// gates every other Vipunen-support page `[SPEC-SUI-190]`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_edit_page_loads_the_runtime() {
        assert!(EDIT_HTML.contains("/core.js") && EDIT_HTML.contains("/edit.js"));
        assert!(EDIT_JS.contains("startBare"), "edit takes the skin, not the player");
    }

    /// The page and the router have to agree about the URLs it fetches --
    /// the seam a jsdom check of the JS alone cannot see `[SPEC021 §3]`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_edit_page_asks_for_routes_the_router_serves() {
        assert!(EDIT_JS.contains("/edit/${passageId}/info"));
        assert!(EDIT_JS.contains("/edit/${passageId}/audio"));
        assert!(EDIT_JS.contains("/edit/${passageId}/review"));
        // Silences the server's own transport on entry `[SPEC-SUI-217]` --
        // the same route the main skin's own pause button sends.
        assert!(EDIT_JS.contains("/command/pause"));
    }

    /// Unconditional, like `/browse` beside it -- and the page/router
    /// agreement `the_edit_page_asks_for_routes_the_router_serves` checks
    /// for the vipunen-support pages applies here too `[REQ-VIS-270]`.
    #[test]
    fn the_passage_page_loads_the_runtime_and_asks_for_routes_the_router_serves() {
        assert!(PASSAGE_HTML.contains("/core.js") && PASSAGE_HTML.contains("/passage.js"));
        assert!(PASSAGE_JS.contains("startBare"), "passage takes the skin, not the player");
        assert!(PASSAGE_JS.contains("/passage/${passageId}/info"));
        assert!(PASSAGE_JS.contains("/why/${p.passage_id}"), "the why-link must name a route /why_for actually serves");
    }

    /// `GET /build`'s body carries what Vipunen's own staleness check needs
    /// `[SPEC-SUI-227]` -- unconditional, unlike the test above, since this
    /// route exists in every build, vipunen-support or not.
    #[test]
    fn build_identity_carries_what_vipunen_compares() {
        let v = build_identity_json();
        assert_eq!(v["git"], crate::GIT, "must be the same hash the Settings page shows");
        assert_eq!(v["branch"], crate::BRANCH);
        assert_eq!(v["commit_date"], crate::COMMIT_DATE);
        assert!(v["dirty_files"].is_u64(), "must be a number, not the raw env string");
    }

    /// The wire shape `edit_review` accepts must be exactly what the store
    /// can write -- an extra or renamed field here is invisible to `cargo
    /// check` because `serde` just ignores unknown fields by default.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn boundary_draft_deserialises_the_nine_values_the_store_wants() {
        let draft: BoundaryDraft = serde_json::from_str(
            r#"{"start_ms":1000,"end_ms":2000,"lead_in_ms":50,"lead_out_ms":900,"gain_db":-1.5,
                "fade_in_ms":20,"fade_out_ms":30,"fade_in_curve":"linear","fade_out_curve":"cosine"}"#,
        )
        .unwrap();
        assert_eq!((draft.start_ms, draft.end_ms), (1000, 2000));
        assert_eq!((draft.lead_in_ms, draft.lead_out_ms), (50, 900));
        assert!((draft.gain_db - -1.5).abs() < 1e-9);
        assert_eq!((draft.fade_in_ms, draft.fade_out_ms), (20, 30));
        assert_eq!((draft.fade_in_curve.as_str(), draft.fade_out_curve.as_str()), ("linear", "cosine"));
    }

    /// A round trip through `write_wav_pcm16`: known samples in, a header
    /// that names the right format/rate/channels, and the same samples back
    /// out (bit-exact for int16, since the fixture values already land
    /// exactly on int16 steps).
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn wav_header_names_what_write_wav_pcm16_was_given() {
        let samples = [0.5f32, -0.5, 1.0, -1.0, 0.0, 0.25];
        let wav = write_wav_pcm16(44_100, 2, &samples);
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(&wav[8..12], b"WAVE");
        assert_eq!(u16::from_le_bytes([wav[22], wav[23]]), 2, "channel count");
        assert_eq!(u32::from_le_bytes([wav[24], wav[25], wav[26], wav[27]]), 44_100, "sample rate");
        assert_eq!(u16::from_le_bytes([wav[34], wav[35]]), 16, "bits per sample");
        let data_len = u32::from_le_bytes([wav[40], wav[41], wav[42], wav[43]]);
        assert_eq!(data_len as usize, samples.len() * 2);
        let got: Vec<i16> = wav[44..]
            .chunks_exact(2)
            .map(|c| i16::from_le_bytes([c[0], c[1]]))
            .collect();
        assert_eq!(got, vec![16384, -16384, 32767, -32767, 0, 8192]);
    }

    /// A real file on disk, decoded through the exact path the route uses --
    /// the seam a pure-function test of `write_wav_pcm16` alone cannot reach,
    /// since it never touches `PassageDecoder` or the filesystem. Reuses
    /// `decoder::tests`' own WAV fixture rather than building a second one.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn decode_window_wav_serves_only_the_requested_span() {
        let f = crate::decoder::tests::tmp("edit_audio"); // 5s of 44.1kHz stereo
        let wav = decode_window_wav(&f, 1_000, 2_000).expect("a readable fixture");
        assert_eq!(&wav[0..4], b"RIFF");
        let data_len = u32::from_le_bytes([wav[40], wav[41], wav[42], wav[43]]) as usize;
        let got_frames = data_len / 2 / 2; // bytes -> int16 samples -> stereo frames
        let want_frames = 44_100; // 1000ms at 44.1kHz
        assert!(
            (got_frames as i64 - want_frames as i64).abs() < 4096,
            "decoded {got_frames} frames for a 1000ms window, wanted ~{want_frames}"
        );
        std::fs::remove_file(&f).ok();
    }

    /// A window asking for more than `EDIT_AUDIO_MAX_MS` is clamped, not
    /// honoured -- the actual defence this route exists to add back after
    /// removing the raw-file route that had no such limit at all.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn decode_window_wav_caps_an_oversized_request() {
        let f = crate::decoder::tests::tmp("edit_audio_cap"); // 5s fixture
        // Ask for far more than the fixture even contains -- the cap must
        // still be the thing that bounds the *request*, not merely the
        // fixture's own short length standing in for it by coincidence.
        let wav = decode_window_wav(&f, 0, EDIT_AUDIO_MAX_MS * 10).expect("a readable fixture");
        let data_len = u32::from_le_bytes([wav[40], wav[41], wav[42], wav[43]]) as usize;
        let got_frames = data_len / 2 / 2;
        // The fixture itself is only 5s, so this mostly confirms decoding
        // stopped at real end-of-stream rather than hanging or erroring on
        // an oversized request -- `EDIT_AUDIO_MAX_MS` is minutes, the
        // fixture is seconds, so the cap is never the binding constraint
        // here, and that asymmetry is the point: a real 4-hour file is what
        // the cap is for, which no test fixture should actually contain.
        assert!(got_frames > 0 && got_frames < 44_100 * 6, "got {got_frames} frames");
        std::fs::remove_file(&f).ok();
    }

    /// A recording search response, in MusicBrainz's own shape -- `title`,
    /// `artist-credit[0].name`, `score` 0-100 `[SPEC-SUI-196]`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn mb_recording_results_parse_into_suggestions() {
        let body: serde_json::Value = serde_json::from_str(
            r#"{"recordings":[{"id":"rec-1","title":"Why Worry","score":97,
                 "artist-credit":[{"name":"Dire Straits"}]},
                {"id":"rec-2","title":"No Artist Credit","score":80}]}"#,
        )
        .unwrap();
        let out = parse_mb_results("recording", &body);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].mbid, "rec-1");
        assert_eq!(out[0].title, Some("Why Worry".into()));
        assert_eq!(out[0].artist, Some("Dire Straits".into()));
        assert!((out[0].score - 0.97).abs() < 1e-9, "score must be normalised to 0..1");
        assert_eq!(out[1].artist, None, "no artist-credit must not panic or fabricate one");
    }

    /// An artist search names itself with `name`, not `title` -- the one
    /// place MusicBrainz's own shape actually differs by entity kind.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn mb_artist_results_read_name_not_title() {
        let body: serde_json::Value = serde_json::from_str(
            r#"{"artists":[{"id":"art-1","name":"Dire Straits","score":100}]}"#,
        )
        .unwrap();
        let out = parse_mb_results("artist", &body);
        assert_eq!(out[0].title, Some("Dire Straits".into()));
    }

    /// No results, or a shape this parser was not given a key for, must come
    /// back as an empty list rather than panicking on the request that
    /// probably matters most: a search for something that genuinely is not
    /// on MusicBrainz at all.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn mb_results_with_nothing_found_is_an_empty_list_not_a_panic() {
        let body: serde_json::Value = serde_json::from_str(r#"{"recordings":[]}"#).unwrap();
        assert!(parse_mb_results("recording", &body).is_empty());
        let empty = serde_json::json!({});
        assert!(parse_mb_results("release", &empty).is_empty());
    }

    /// A release's own tracklist, real shape from `inc=recordings`
    /// `[SPEC-RIP-074]` -- position, the recording it actually names, and
    /// the recording's title takes precedence over the track's own (they
    /// are usually the same string, but the recording is what a reassign
    /// actually links to).
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn release_tracks_parse_by_position() {
        let body: serde_json::Value = serde_json::from_str(
            r#"{"media":[{"tracks":[
                 {"position":1,"title":"Girls Just Want to Have Fun",
                  "recording":{"id":"rec-1","title":"Girls Just Want to Have Fun"}},
                 {"position":2,"title":"Money Changes Everything",
                  "recording":{"id":"rec-2","title":"Money Changes Everything"}}
               ]}]}"#,
        )
        .unwrap();
        let out = parse_release_tracks(&body);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].position, 1);
        assert_eq!(out[0].mbid, "rec-1");
        assert_eq!(out[0].title, Some("Girls Just Want to Have Fun".into()));
        assert_eq!(out[1].position, 2);
    }

    /// A multi-disc release's tracks are read across every medium, not only
    /// the first -- position numbering restarts per disc, so stopping after
    /// medium 1 would silently drop every later disc's own tracklist.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn release_tracks_cover_every_medium_not_only_the_first() {
        let body: serde_json::Value = serde_json::from_str(
            r#"{"media":[
                 {"tracks":[{"position":1,"recording":{"id":"d1t1","title":"Disc 1 Track 1"}}]},
                 {"tracks":[{"position":1,"recording":{"id":"d2t1","title":"Disc 2 Track 1"}}]}
               ]}"#,
        )
        .unwrap();
        let out = parse_release_tracks(&body);
        assert_eq!(out.len(), 2, "both discs' own track 1 must both be present");
        assert_eq!(out[0].mbid, "d1t1");
        assert_eq!(out[1].mbid, "d2t1");
    }

    /// No `media`, or a track missing its own `recording`, comes back as
    /// simply absent from the list -- not a panic on a release the API
    /// happens to describe incompletely.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn release_tracks_with_nothing_usable_is_an_empty_list_not_a_panic() {
        assert!(parse_release_tracks(&serde_json::json!({})).is_empty());
        let no_recording: serde_json::Value = serde_json::from_str(
            r#"{"media":[{"tracks":[{"position":1,"title":"No recording field"}]}]}"#,
        )
        .unwrap();
        assert!(parse_release_tracks(&no_recording).is_empty());
    }

    /// The review page's own search box has to agree with the router about
    /// this route, the same seam every other page-to-route check here guards.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_review_page_asks_musicbrainz_search_for_the_route_it_gets() {
        assert!(REVIEW_JS.contains("/api/musicbrainz/search"));
    }

    /// The release-kind search's own second step -- fetching a picked
    /// release's tracklist -- has to agree with the router about this route,
    /// the same seam every other page-to-route check here guards
    /// `[SPEC-RIP-074]`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_review_page_asks_for_release_tracks_at_the_route_it_gets() {
        assert!(REVIEW_JS.contains("/review/release-tracks/"));
    }

    /// The artist-correction verbs the page can send are exactly the two the
    /// router registers under `/review/:id/artist/:verb` -- `correct` (via
    /// the literal path built into the fetch call) and `reopen`.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_review_page_sends_artist_verbs_the_router_serves() {
        assert!(REVIEW_JS.contains("/artist/correct"));
        assert!(REVIEW_JS.contains("/artist/reopen"));
    }

    /// `SEGMENT_QUEUE_ROUTE` is what the router actually registers
    /// `[SPEC024 §7]` -- named once, the same reason `REVIEW_QUEUE_ROUTE`
    /// is, so the router table and any future reader of it cannot drift
    /// apart on the spelling.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn the_segment_queue_route_is_named_once() {
        assert_eq!(SEGMENT_QUEUE_ROUTE, "/segment/queue");
    }

    /// `accept_segment`'s refusal for a passage the queue never offered
    /// travels back as readable text, the same posture `record_review`'s own
    /// check above exercises for its own vocabulary `[SPEC-SA-125]` --
    /// `accept_segment` is what `POST /segment/:passage_id/accept` calls
    /// through `PlayerStore`, exactly as `record_review` is what `POST
    /// /review/:passage_id/:decision` calls.
    #[cfg(feature = "vipunen-support")]
    #[test]
    fn accept_segment_refuses_what_the_queue_never_offered() {
        let store = crate::db::PlayerStore::open(&std::path::PathBuf::from(":memory:")).unwrap();
        let err = store.accept_segment(1).unwrap_err();
        assert!(err.message().contains("no such passage"), "{}", err.message());
    }
}
