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
        Some((entry, draft))
    })
    .await
    .ok()
    .flatten();
    match found {
        Some((entry, draft)) => {
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
        decode_window_wav(&entry.path, from, to)
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
pub(super) fn decode_window_wav(path: &std::path::Path, from_ms: u64, to_ms: u64) -> Option<Vec<u8>> {
    // A cap independent of what is asked for: a malformed or malicious
    // request cannot reintroduce the whole-file problem this exists to fix,
    // no matter what `from_ms`/`to_ms` claim.
    let to_ms = to_ms.min(from_ms + EDIT_AUDIO_MAX_MS);
    let mut dec = crate::decoder::PassageDecoder::open(path, from_ms, Some(to_ms)).ok()?;
    let (sr, ch) = (dec.sample_rate, dec.channels as u16);
    let mut samples: Vec<f32> = Vec::new();
    while let Ok(Some(chunk)) = dec.next() {
        samples.extend_from_slice(chunk);
    }
    Some(write_wav_pcm16(sr, ch, &samples))
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

