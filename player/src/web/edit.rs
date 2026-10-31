//! The boundary/waveform editor `[SPEC021]`: a passage's own facts, the
//! decision it records, and the raw-audio window the waveform draws from.

#[cfg(feature = "vipunen-support")]
use axum::extract::State;
#[cfg(feature = "vipunen-support")]
use axum::http::StatusCode;
#[cfg(feature = "vipunen-support")]
use axum::response::IntoResponse;

#[cfg(feature = "vipunen-support")]
use super::Ui;

/// The editor's read side: what a passage's boundaries currently are
/// `[SPEC-SUI-201]`. A small JSON sibling of the editor page rather than data
/// baked into the page response -- every page Lempi serves is a static shell
/// compiled in with `include_str!`, and this one fetches its own state the
/// same way `/review` does `[SPEC021 §3]`.
#[cfg(feature = "vipunen-support")]
pub(super) async fn edit_info(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let found = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        let entry = lib.passage(passage_id).ok()?;
        // A recorded-but-not-yet-applied draft wins over the passage's own
        // values -- reopening the editor after a commit must show the edit
        // that was made, not the automatic values it drafted over
        // `[SPEC021 §2]`.
        let draft = lib.boundary_review(passage_id);
        let context = lib.passage_context(passage_id);
        Some((entry, draft, context))
    })
    .await
    .ok()
    .flatten();
    match found {
        Some((entry, draft, context)) => {
            let edited = draft.is_some();
            let (start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db,
                 fade_in_ms, fade_out_ms, fade_in_curve, fade_out_curve) = match draft {
                Some(d) => (
                    d.start_ms,
                    d.end_ms,
                    d.lead_in_ms.unwrap_or(entry.lead_in_ms),
                    d.lead_out_ms.unwrap_or(entry.lead_out_ms),
                    d.gain_db.unwrap_or(entry.gain_db as f64),
                    d.fade_in_ms.unwrap_or(entry.fade_in_ms),
                    d.fade_out_ms.unwrap_or(entry.fade_out_ms),
                    d.fade_in_curve.unwrap_or_else(|| entry.fade_in_curve.as_str().to_string()),
                    d.fade_out_curve.unwrap_or_else(|| entry.fade_out_curve.as_str().to_string()),
                ),
                None => (
                    entry.start_ms,
                    entry.end_ms,
                    entry.lead_in_ms,
                    entry.lead_out_ms,
                    entry.gain_db as f64,
                    entry.fade_in_ms,
                    entry.fade_out_ms,
                    entry.fade_in_curve.as_str().to_string(),
                    entry.fade_out_curve.as_str().to_string(),
                ),
            };
            axum::Json(serde_json::json!({
                "passage_id": entry.passage_id,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "file_ms": entry.file_ms,
                "lead_in_ms": lead_in_ms,
                "lead_out_ms": lead_out_ms,
                "gain_db": gain_db,
                "fade_in_ms": fade_in_ms,
                "fade_out_ms": fade_out_ms,
                "fade_in_curve": fade_in_curve,
                "fade_out_curve": fade_out_curve,
                "edited": edited,
                // What the detection panel needs `[SPEC-SUI-231]`: which
                // parameter set applies, where the stored span came from, and
                // how far a start or end may be searched before it reaches a
                // neighbouring passage. `null` throughout when it could not be
                // read -- the panel says so rather than guessing a kind.
                "kind": context.as_ref().map(|c| c.kind.clone()),
                "boundary_src": context.as_ref().map(|c| c.boundary_src.clone()),
                "prev_end_ms": context.as_ref().and_then(|c| c.prev_end_ms),
                "next_start_ms": context.as_ref().and_then(|c| c.next_start_ms),
            }))
            .into_response()
        }
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

/// The nine values a boundary edit posts `[SPEC021 §3]`, `[SPEC-SUI-226]`,
/// and nothing else -- no `passage_id`, which comes from the path and cannot
/// be spoofed by the body disagreeing with the URL. `fade_in_curve`/
/// `fade_out_curve` travel as plain strings and are validated against
/// `Curve::parse` inside `record_boundary_review`, not here -- the same
/// place `start_ms >= end_ms` is already checked, so every draft-level
/// refusal comes from one spot.
#[cfg(feature = "vipunen-support")]
#[derive(serde::Deserialize)]
pub(super) struct BoundaryDraft {
    pub(super) start_ms: u64,
    pub(super) end_ms: u64,
    pub(super) lead_in_ms: u64,
    pub(super) lead_out_ms: u64,
    pub(super) gain_db: f64,
    pub(super) fade_in_ms: u64,
    pub(super) fade_out_ms: u64,
    pub(super) fade_in_curve: String,
    pub(super) fade_out_curve: String,
}

/// Commit a boundary edit `[SPEC021 §2]`. Recorded, not applied -- the same
/// posture `id_reviews` takes and for the same reason: this changes what a
/// passage *is*, and the library is Vipunen's to write.
#[cfg(feature = "vipunen-support")]
pub(super) async fn edit_review(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
    axum::extract::Json(draft): axum::extract::Json<BoundaryDraft>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let done = tokio::task::spawn_blocking(move || {
        crate::db::PlayerStore::open_split(&db, &library)
            .map_err(|e| e.message().to_string())?
            .record_boundary_review(
                passage_id,
                draft.start_ms,
                draft.end_ms,
                draft.lead_in_ms,
                draft.lead_out_ms,
                draft.gain_db,
                draft.fade_in_ms,
                draft.fade_out_ms,
                &draft.fade_in_curve,
                &draft.fade_out_curve,
            )
            .map_err(|e| e.message().to_string())
    })
    .await;
    match done {
        Ok(Ok(())) => StatusCode::NO_CONTENT.into_response(),
        // The reason travels back as text, same as `record_review`'s own
        // refusals -- "already applied" is exactly what the operator needs
        // to read, not a bare status code.
        Ok(Err(msg)) => (StatusCode::CONFLICT, msg).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// A decoded WAV window around a passage, never the raw file
/// `[SPEC021 §3]`, `[SPEC-SUI-224]`.
///
/// **Was** the raw file's bytes, `Range`-aware in principle. `edit.js` never
/// actually sent a `Range` header, so every load fetched the *whole* file and
/// asked the browser to `decodeAudioData` it -- fine for a single-track
/// library, catastrophic for `GoodbyeYellowBrickRoad.mp3`: a 324.7 MB, 4h05m
/// single-file DAO capture, meaning ~10 GB of interleaved f32 PCM asked of
/// the browser to produce one waveform. Exactly the risk this section's own
/// prior note left unmeasured -- confirmed live against passage 16379, no
/// waveform, no response from Play, the browser simply never finishing.
///
/// Decoding through `PassageDecoder` (`crate::decoder`) instead -- the same
/// seek-accurate, bounded-memory decoder the real player already uses
/// `[REQ-AUD-110]`, `[GDE-FBD-010]` -- costs nothing new to trust and closes
/// two problems at once: the window is bounded regardless of the file's own
/// size, and there is no byte-range-from-time-range arithmetic to get wrong
/// for a VBR file, which this session's own duration audit already showed
/// cannot be done reliably by estimate (`[REQ-LIB-145]`).
#[cfg(feature = "vipunen-support")]
pub(super) async fn edit_audio(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let want_from: Option<u64> = q.get("from_ms").and_then(|s| s.parse().ok());
    let want_to: Option<u64> = q.get("to_ms").and_then(|s| s.parse().ok());

    let wav = tokio::task::spawn_blocking(move || -> Option<Vec<u8>> {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        let entry = lib.passage(passage_id).ok()?;
        // The client is expected to send both, padded around the passage
        // `[SPEC-SUI-224]`; the passage's own span is the fallback for a
        // request that omits them, not the primary path.
        let from = want_from.unwrap_or(entry.start_ms);
        let to = want_to.unwrap_or(entry.end_ms).max(from + 1);
        decode_window_wav(&entry.path, entry.gapless, from, to)
    })
    .await
    .ok()
    .flatten();

    match wav {
        Some(bytes) => (
            StatusCode::OK,
            [(axum::http::header::CONTENT_TYPE, "audio/wav".to_string())],
            bytes,
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

/// 30 minutes -- generous for any real edit (a passage's own span plus
/// `edit.js`'s own padding is ordinarily a few minutes at most) and small
/// next to what a 4-hour capture was costing before this existed.
#[cfg(feature = "vipunen-support")]
pub(super) const EDIT_AUDIO_MAX_MS: u64 = 30 * 60 * 1000;

/// Decode `[from_ms, to_ms)` of `path` through `PassageDecoder` and return it
/// as a WAV, or `None` if the file cannot be opened. Split out from the
/// handler above so it can be tested against a real fixture file without an
/// HTTP round trip -- the same shape `read_audio_range` (its predecessor)
/// used to keep the filesystem-touching logic reachable from a plain test.
#[cfg(feature = "vipunen-support")]
pub(super) fn decode_window_wav(path: &std::path::Path, gapless: bool, from_ms: u64, to_ms: u64) -> Option<Vec<u8>> {
    // A cap independent of what is asked for: a malformed or malicious
    // request cannot reintroduce the whole-file problem this exists to fix,
    // no matter what `from_ms`/`to_ms` claim.
    let to_ms = to_ms.min(from_ms + EDIT_AUDIO_MAX_MS);
    // Decoded as the passage plays `[SPEC-AGN-062]`, or a boundary set by
    // ear here would sit 25 ms from where it is heard.
    let mut dec = crate::decoder::PassageDecoder::open_at(path, from_ms, Some(to_ms), 0, gapless).ok()?;
    let (sr, ch) = (dec.sample_rate, dec.channels as u16);
    let mut samples: Vec<f32> = Vec::new();
    while let Ok(Some(chunk)) = dec.next() {
        samples.extend_from_slice(chunk);
    }
    Some(write_wav_pcm16(sr, ch, &samples))
}

/// The whole file at a glance `[SPEC-SUI-232]`: one min/max pair of the
/// channel-averaged signal per `OVERVIEW_BUCKET_MS`, as signed bytes, so the
/// editor can draw and navigate a file far longer than any window it decodes
/// in full. Asked for 2026-10-18 against passage 18069 -- a 5.5-minute track
/// inside a 68.5-minute single-file rip, whose ±60 s window left no way to
/// reach a boundary that really lay further out.
///
/// The bytes are `min, max, min, max, ...`; `x-bucket-ms` says how much of the
/// file each pair covers, and `x-decoded-ms` how much audio was really found
/// -- not the stored duration, which `[SPEC-SUI-224]` already showed can be
/// wrong. Measured on this desktop the same day: 10 minutes of that MP3
/// decoded in 0.78 s, so the whole file costs about 5 s once, and a 4-hour
/// capture about 20 s; the result is 82 KB and 290 KB, which is why it is
/// kept (`OVERVIEW_CACHE`) rather than recomputed for the next passage of the
/// same file.
#[cfg(feature = "vipunen-support")]
pub(super) async fn edit_overview(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) -> axum::response::Response {
    let db = ui.db.clone();
    let library = ui.library.clone();
    let found = tokio::task::spawn_blocking(move || {
        let lib = crate::db::Library::open_split(&db, &library).ok()?;
        let entry = lib.passage(passage_id).ok()?;
        cached_overview(&entry.path, entry.gapless)
    })
    .await
    .ok()
    .flatten();

    match found {
        Some(ov) => (
            StatusCode::OK,
            [
                (axum::http::header::CONTENT_TYPE, "application/octet-stream".to_string()),
                (axum::http::HeaderName::from_static("x-bucket-ms"), ov.bucket_ms.to_string()),
                (axum::http::HeaderName::from_static("x-decoded-ms"), ov.decoded_ms.to_string()),
            ],
            ov.peaks.iter().map(|&v| v as u8).collect::<Vec<u8>>(),
        )
            .into_response(),
        None => StatusCode::NOT_FOUND.into_response(),
    }
}

/// Fine enough that a view just wider than the editor's own detail window
/// (five minutes on a 2400-pixel canvas is 125 ms a pixel) still reads as a
/// waveform rather than a block.
#[cfg(feature = "vipunen-support")]
pub(super) const OVERVIEW_BUCKET_MS: u64 = 100;

#[cfg(feature = "vipunen-support")]
pub(super) struct Overview {
    pub bucket_ms: u64,
    pub decoded_ms: u64,
    pub peaks: Vec<i8>,
}

/// The last few files' overviews, keyed by path, modification time and size,
/// so a changed file is read again rather than served stale. Several passages
/// share one file -- the reason it is worth keeping at all.
#[cfg(feature = "vipunen-support")]
type OverviewKey = (std::path::PathBuf, Option<std::time::SystemTime>, u64, bool);
#[cfg(feature = "vipunen-support")]
static OVERVIEW_CACHE: std::sync::Mutex<Vec<(OverviewKey, std::sync::Arc<Overview>)>> =
    std::sync::Mutex::new(Vec::new());
#[cfg(feature = "vipunen-support")]
const OVERVIEW_CACHE_FILES: usize = 4;

#[cfg(feature = "vipunen-support")]
pub(super) fn cached_overview(path: &std::path::Path, gapless: bool) -> Option<std::sync::Arc<Overview>> {
    let meta = std::fs::metadata(path).ok()?;
    let key: OverviewKey = (path.to_path_buf(), meta.modified().ok(), meta.len(), gapless);
    if let Ok(cache) = OVERVIEW_CACHE.lock() {
        if let Some((_, ov)) = cache.iter().find(|(k, _)| *k == key) {
            return Some(ov.clone());
        }
    }
    // Decoded outside the lock: two first requests for one file both pay for
    // it, which is cheaper than every other file's request waiting on it.
    let ov = std::sync::Arc::new(overview_peaks(path, gapless, OVERVIEW_BUCKET_MS)?);
    if let Ok(mut cache) = OVERVIEW_CACHE.lock() {
        cache.retain(|(k, _)| *k != key);
        cache.push((key, ov.clone()));
        let excess = cache.len().saturating_sub(OVERVIEW_CACHE_FILES);
        cache.drain(..excess);
    }
    Some(ov)
}

/// Decode all of `path` and reduce it to min/max pairs per `bucket_ms`, the
/// channels averaged first -- the same mono `edit.js` draws its detail from,
/// so the two resolutions agree about what the signal is.
#[cfg(feature = "vipunen-support")]
pub(super) fn overview_peaks(path: &std::path::Path, gapless: bool, bucket_ms: u64) -> Option<Overview> {
    let mut dec = crate::decoder::PassageDecoder::open_at(path, 0, None, 0, gapless).ok()?;
    let (sr, ch) = (dec.sample_rate as u64, dec.channels.max(1));
    let per_bucket = (sr * bucket_ms / 1000).max(1);
    let q = |v: f32| (v.clamp(-1.0, 1.0) * 127.0).round() as i8;
    let mut peaks = Vec::new();
    let (mut lo, mut hi, mut n, mut frames) = (f32::MAX, f32::MIN, 0u64, 0u64);
    while let Ok(Some(chunk)) = dec.next() {
        for frame in chunk.chunks_exact(ch) {
            let v = frame.iter().sum::<f32>() / ch as f32;
            lo = lo.min(v);
            hi = hi.max(v);
            n += 1;
            if n == per_bucket {
                peaks.push(q(lo));
                peaks.push(q(hi));
                (lo, hi, n) = (f32::MAX, f32::MIN, 0);
            }
        }
        frames += (chunk.len() / ch) as u64;
    }
    if n > 0 {
        peaks.push(q(lo));
        peaks.push(q(hi));
    }
    Some(Overview { bucket_ms, decoded_ms: frames * 1000 / sr, peaks })
}

/// A minimal 16-bit PCM WAV -- the same header shape
/// `decoder::tests::ramp_wav` already writes as a *test* fixture, written
/// here as the real response body a browser's `decodeAudioData` reads back.
/// `samples` is already interleaved, already `f32` in `[-1, 1]` -- exactly
/// what `PassageDecoder::next` yields -- so the only work left is the
/// int16 conversion and the 44-byte header.
#[cfg(feature = "vipunen-support")]
pub(super) fn write_wav_pcm16(sample_rate: u32, channels: u16, samples: &[f32]) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut b = Vec::with_capacity(44 + data_len as usize);
    b.extend(b"RIFF");
    b.extend(&(36 + data_len).to_le_bytes());
    b.extend(b"WAVEfmt ");
    b.extend(&16u32.to_le_bytes());
    b.extend(&1u16.to_le_bytes()); // PCM
    b.extend(&channels.to_le_bytes());
    b.extend(&sample_rate.to_le_bytes());
    b.extend(&(sample_rate * channels as u32 * 2).to_le_bytes());
    b.extend(&(channels * 2).to_le_bytes());
    b.extend(&16u16.to_le_bytes());
    b.extend(b"data");
    b.extend(&data_len.to_le_bytes());
    for s in samples {
        let v = (s.clamp(-1.0, 1.0) * 32767.0).round() as i16;
        b.extend(&v.to_le_bytes());
    }
    b
}

