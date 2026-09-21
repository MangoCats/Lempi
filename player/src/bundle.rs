//! Receive a derived-data bundle from Vipunen `[SPEC014]`, `[SPEC-SUI-130]`.
//!
//! Lempi's half of one feature whose other half is Vipunen's exporter. They are
//! in different languages under different licences — this is MIT and stays MIT,
//! since MIT may be incorporated into an AGPL work and not the reverse
//! `[GDE-ARC-018]` — and they meet only at the payload schema, which is
//! therefore the only thing keeping them in agreement.
//!
//! **This is what makes `[REQ-PORT-100]` true rather than intended:** a Lempi
//! with no Vipunen, receiving flavor and segmentation it could never compute,
//! because the extractor is x86-only `[SPEC-SA-018]`.
//!
//! Two independent axes, and conflating them would be the mistake
//! `[SPEC-PL-075]` names:
//!
//! * **Acceptance** is per bundle and all-or-nothing. An incompatible payload
//!   leaves the library byte-identical.
//! * **Arrival** is per encoding and partial by nature. Audio that has not
//!   turned up is a transfer gap, not a schema disagreement, and audio that
//!   hashes wrong is `corrupt` rather than `unknown` `[SPEC-RLK-055]`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use rusqlite::{params, Connection};
use serde_json::Value;

/// Retains the payload as it arrived `[SPEC-SUI-165]`.
///
/// Fields this version cannot interpret are **not discarded**: the bundle
/// crossed a link measured in hours, and a later Lempi that understands them
/// must not have to ask for them again. Measured at 1.27 KB per track
/// compressed `[SPEC-PL-090]`, so retaining the whole library's payload costs
/// about 10 MB against a gigabyte-scale database.
const DDL: &str = "
CREATE TABLE IF NOT EXISTS imported_payloads (
    import_id       INTEGER PRIMARY KEY,
    payload_version INTEGER NOT NULL,
    generator       TEXT,
    encodings       INTEGER NOT NULL,
    body            TEXT NOT NULL,
    imported_at     TEXT NOT NULL
)";

/// What became of one encoding `[SPEC-PL-075]`.
#[derive(Debug, PartialEq)]
pub enum Landed {
    /// Rows created, path bound, audio verified against the payload's hash.
    Imported,
    /// Already held, by `audio_md5`. A resend is ordinary `[SPEC-SUI-180]`.
    Already,
    /// The payload describes it; the audio is not here yet.
    AwaitingAudio { at: String },
    /// Audio is here and hashes to something else. A failed transfer, never a
    /// discovery `[SPEC-RLK-055]`.
    Corrupt { expected: String, found: String },
}

#[derive(Debug, Default)]
pub struct Report {
    /// Non-empty means the whole bundle was refused and nothing was written.
    pub refused: Vec<String>,
    pub outcomes: Vec<(String, Landed)>,
    /// Flavor values left alone because the receiver's own outrank the
    /// payload's `[SPEC-DF-070]`.
    pub kept_local: usize,
    pub rows_written: usize,
}

impl Report {
    pub fn count(&self, f: impl Fn(&Landed) -> bool) -> usize {
        self.outcomes.iter().filter(|(_, o)| f(o)).count()
    }
}

// ---------------------------------------------------------------- accept ---

fn req(o: &Value, k: &str) -> bool {
    !matches!(o.get(k), None | Some(Value::Null))
}

/// Everything that makes this payload unacceptable. Empty means import it.
///
/// **Not a version comparison** `[SPEC-PL-065]`. A *newer* payload that dropped
/// a required field is incompatible and an older one may be perfectly usable,
/// so `payload_version` is recorded and never consulted for acceptance. The
/// required set is read off SPEC008's `NOT NULL` constraints `[SPEC-PL-060]`,
/// which is what makes it normative rather than a description.
pub fn unacceptable(doc: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let empty = Vec::new();
    let encodings = doc.get("encodings").and_then(|v| v.as_array()).unwrap_or(&empty);
    let recordings = doc.get("recordings").and_then(|v| v.as_array()).unwrap_or(&empty);
    if encodings.is_empty() {
        out.push("payload carries no encodings".into());
    }

    let mut seen_md5: HashMap<&str, &Value> = HashMap::new();
    for e in encodings {
        let md5 = e.get("audio_md5").and_then(|v| v.as_str()).unwrap_or("<none>");
        for k in ["audio_md5", "bundle_path", "format", "duration_ms"] {
            if !req(e, k) {
                out.push(format!("{md5}: missing encoding.{k}"));
            }
        }
        // Present is not the same as usable. `req` only asks whether the key
        // exists, so a string or a float passed acceptance and then landed as
        // **zero** — and a zero duration is not a small error, it is a length
        // against which every play/skip judgement is meaningless
        // `[SPEC-MPD-092]`. Reject it here rather than default it later.
        if req(e, "duration_ms") && num(e, "duration_ms").is_none_or(|d| d <= 0) {
            out.push(format!("{md5}: encoding.duration_ms is not a positive integer"));
        }
        // A payload disagreeing with ITSELF. `[SPEC-DF-070]` ranks a payload
        // against the receiver, by provenance then recency; nothing ranks it
        // against itself, so two entries for one key have equal claim and
        // choosing either would be a guess recorded as a fact.
        if let Some(prev) = seen_md5.insert(md5, e) {
            if prev != e {
                out.push(format!("encoding {md5}: two entries, and they differ"));
            }
        }
        let passages = e.get("passages").and_then(|v| v.as_array()).unwrap_or(&empty);
        if passages.is_empty() {
            out.push(format!("{md5}: no passages"));
        }
        for p in passages {
            for k in ["kind", "start_ms", "end_ms", "boundary_src"] {
                if !req(p, k) {
                    out.push(format!("{md5}: missing passage.{k}"));
                }
            }
            // A row SQLite would refuse is not a value to reconcile, and
            // finding that out at INSERT time means finding it out half way.
            let (s, x) = (num(p, "start_ms"), num(p, "end_ms"));
            if let (Some(s), Some(x)) = (s, x) {
                if x <= s {
                    out.push(format!("{md5}: passage end_ms {x} <= start_ms {s}"));
                }
            }
            for c in p.get("recordings").and_then(|v| v.as_array()).unwrap_or(&empty) {
                for k in ["mbid", "weight", "source"] {
                    if !req(c, k) {
                        out.push(format!("{md5}: missing credit.{k}"));
                    }
                }
            }
        }
    }

    let mut seen_mbid: HashMap<&str, &Value> = HashMap::new();
    for r in recordings {
        let mbid = r.get("mbid").and_then(|v| v.as_str()).unwrap_or("<none>");
        for k in ["mbid", "title", "source"] {
            if !req(r, k) {
                out.push(format!("{mbid}: missing recording.{k}"));
            }
        }
        if let Some(prev) = seen_mbid.insert(mbid, r) {
            if prev != r {
                out.push(format!("recording {mbid}: two entries, and they differ"));
            }
        }
        let mut seen_flavor: Vec<(String, String)> = Vec::new();
        for f in r.get("flavor").and_then(|v| v.as_array()).unwrap_or(&empty) {
            for k in ["characteristic", "class", "value", "source"] {
                if !req(f, k) {
                    out.push(format!("{mbid}: missing flavor.{k}"));
                }
            }
            let key = (str_of(f, "characteristic"), str_of(f, "class"));
            if seen_flavor.contains(&key) {
                out.push(format!("{mbid}: flavor {}/{} appears twice", key.0, key.1));
            }
            seen_flavor.push(key);
            if let Some(v) = f.get("value").and_then(|v| v.as_f64()) {
                if !(0.0..=1.0).contains(&v) {
                    out.push(format!("{mbid}: flavor value {v} outside 0..1"));
                }
            }
        }
    }
    out
}

fn num(o: &Value, k: &str) -> Option<i64> {
    o.get(k).and_then(|v| v.as_i64())
}
fn str_of(o: &Value, k: &str) -> String {
    o.get(k).and_then(|v| v.as_str()).unwrap_or_default().to_string()
}

/// A fade field with the schema's own fallback `[SPEC-SC-046]`, for a sender
/// too old to have carried `fade_in_ms`/`fade_out_ms` at all. Unlike `num`,
/// this never yields `None`: `passages.fade_in_ms`/`fade_out_ms` are `NOT
/// NULL DEFAULT 20`, so there is no absent state to preserve, only a value a
/// bare INSERT omitting the column would have produced anyway.
fn fade_ms(o: &Value, k: &str) -> i64 {
    num(o, k).unwrap_or(20)
}

/// `fade_in_curve`/`fade_out_curve` with the same fallback, `'exponential'`.
fn fade_curve<'a>(o: &'a Value, k: &str) -> &'a str {
    o.get(k).and_then(|v| v.as_str()).unwrap_or("exponential")
}

// ---------------------------------------------------------------- import ---

/// Import a bundle. `audio_root` is where arriving audio lives — the bundle's
/// own `audio/` directory, or the library root it has already been placed in.
///
/// **The import binds what it creates** `[SPEC-PL-085]`. Relink never creates a
/// row `[SPEC-RLK-090]`, so it cannot bind an arriving one; this hashes each
/// file it is given, verifies it against the payload, stats it for the
/// machine-scope columns `[SPEC-DF-030]` and writes the path itself. No
/// separate relink pass is needed for a bundle.
pub fn import(
    db: &mut Connection,
    doc: &Value,
    body: &str,
    audio_root: &Path,
    apply: bool,
) -> Result<Report, String> {
    let mut rep = Report { refused: unacceptable(doc), ..Default::default() };
    if !rep.refused.is_empty() {
        // Whole, and it names the unmet requirement `[SPEC-PL-070]`. A partial
        // import leaves a library that is neither the old one nor the new one,
        // with nothing recording which parts are which.
        return Ok(rep);
    }
    // Only when writing. An earlier draft ran this before the `apply` check, so
    // a run that reported "nothing was written" had created a table -- report-
    // by-default meaning "almost nothing", which is the kind of quiet exception
    // that makes a dry run untrustworthy `[SPEC-RLK-100]`.
    if apply {
        db.execute_batch(DDL).map_err(|e| e.to_string())?;
    }

    let empty = Vec::new();
    let recordings: HashMap<String, &Value> = doc
        .get("recordings")
        .and_then(|v| v.as_array())
        .unwrap_or(&empty)
        .iter()
        .map(|r| (str_of(r, "mbid"), r))
        .collect();

    let tx = db.transaction().map_err(|e| e.to_string())?;
    let now = now_iso();

    for e in doc.get("encodings").and_then(|v| v.as_array()).unwrap_or(&empty) {
        let md5 = str_of(e, "audio_md5");
        let rel = str_of(e, "bundle_path");

        // Idempotent by identity, not by flag `[SPEC-PL-080]`. Checked
        // explicitly: `INSERT OR IGNORE` turns a NOT NULL violation into
        // nothing happening, which is how `apply_reviews` came to be unable to
        // apply anything `[REQ-LIB-165]`.
        let held: Option<i64> = tx
            .query_row("SELECT file_id FROM files WHERE audio_md5 = ?1", params![md5], |r| r.get(0))
            .ok();
        if held.is_some() {
            // `[SPEC-MESH-080]` The file needs no write, but a later bundle's
            // recording-scope data (flavor, most often) may still be an
            // improvement over what this library already has for the same
            // mbid -- provenance-checked by `upsert_recording` exactly as it
            // is on a fresh import, never a blind overwrite. Passages and
            // `passage_recordings` are untouched here: the file already has
            // them, and re-deriving which existing passage a credit belongs
            // to is a real question `[SPEC-SC-045]` leaves to a future pass,
            // not one this fix answers by guessing.
            if apply {
                for p in e.get("passages").and_then(|v| v.as_array()).unwrap_or(&empty) {
                    for c in p.get("recordings").and_then(|v| v.as_array()).unwrap_or(&empty) {
                        let mbid = str_of(c, "mbid");
                        if let Some(r) = recordings.get(&mbid) {
                            upsert_recording(&tx, r, &mut rep)?;
                        }
                    }
                }
            }
            rep.outcomes.push((md5, Landed::Already));
            continue;
        }

        let path: PathBuf = audio_root.join(rel.replace('/', std::path::MAIN_SEPARATOR_STR));
        if !path.is_file() {
            rep.outcomes.push((md5, Landed::AwaitingAudio { at: path.display().to_string() }));
            continue;
        }
        // Verify before trusting `[SPEC-DF-070]`, with the hasher relink
        // already uses -- one implementation `[GDE-FBD-040]`, and the one that
        // produced the incumbent values `[SPEC-RLK-086]`.
        let found = match crate::relink::hash_encoded(&path) {
            Ok(h) => h,
            Err(e) => {
                rep.outcomes.push((md5.clone(), Landed::Corrupt { expected: md5, found: e }));
                continue;
            }
        };
        if found != md5 {
            rep.outcomes.push((md5.clone(), Landed::Corrupt { expected: md5, found }));
            continue;
        }

        if !apply {
            rep.outcomes.push((md5, Landed::Imported));
            continue;
        }

        let meta = std::fs::metadata(&path).map_err(|e| e.to_string())?;
        let mtime = meta
            .modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        tx.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen)\
             VALUES (?1,?2,?3,?4,?5,?6,?7,?7)",
            params![md5, path.to_string_lossy(), meta.len() as i64, mtime,
                    str_of(e, "format"),
                    num(e, "duration_ms")
                        .filter(|d| *d > 0)
                        .ok_or_else(|| format!("{md5}: encoding.duration_ms is not a positive integer"))?,
                    now],
        )
        .map_err(|e| e.to_string())?;
        let file_id = tx.last_insert_rowid();
        rep.rows_written += 1;

        if let Some(t) = e.get("tags").filter(|v| !v.is_null()) {
            // Tags travel although they are re-derivable: for audio with no
            // MusicBrainz entry the file's own tag is the only place the
            // artist name exists `[SPEC-PL-050]`.
            tx.execute(
                "INSERT INTO file_tags (file_id,title,artist,album,track_no,disc_no,has_art,scanned_at)\
                 VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                params![file_id, t.get("title").and_then(|v| v.as_str()),
                        t.get("artist").and_then(|v| v.as_str()),
                        t.get("album").and_then(|v| v.as_str()),
                        num(t, "track_no"), num(t, "disc_no"),
                        num(t, "has_art").unwrap_or(0), now],
            )
            .map_err(|e| e.to_string())?;
            rep.rows_written += 1;
        }

        for p in e.get("passages").and_then(|v| v.as_array()).unwrap_or(&empty) {
            tx.execute(
                "INSERT INTO passages \
                     (file_id,kind,start_ms,end_ms,lead_in_ms,lead_out_ms,gain_db,boundary_src,\
                      fade_in_ms,fade_out_ms,fade_in_curve,fade_out_curve)\
                 VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12)",
                // NULL stays NULL for lead/gain: it means "not analysed",
                // which is not zero, and the player acts on lead-out timing
                // `[SPEC-PL-030]`. Fade `[SPEC-SC-046]` has no such state --
                // `passages.fade_in_ms` etc. are `NOT NULL DEFAULT`, so a
                // sender too old to have carried them `[SPEC-PL-060]` gets
                // exactly the value a bare INSERT omitting the columns would
                // have produced, not a constraint violation.
                params![file_id, str_of(p, "kind"), num(p, "start_ms"), num(p, "end_ms"),
                        num(p, "lead_in_ms"), num(p, "lead_out_ms"),
                        p.get("gain_db").and_then(|v| v.as_f64()),
                        str_of(p, "boundary_src"),
                        fade_ms(p, "fade_in_ms"), fade_ms(p, "fade_out_ms"),
                        fade_curve(p, "fade_in_curve"), fade_curve(p, "fade_out_curve")],
            )
            .map_err(|e| e.to_string())?;
            let passage_id = tx.last_insert_rowid();
            rep.rows_written += 1;

            for c in p.get("recordings").and_then(|v| v.as_array()).unwrap_or(&empty) {
                let mbid = str_of(c, "mbid");
                if let Some(r) = recordings.get(&mbid) {
                    upsert_recording(&tx, r, &mut rep)?;
                }
                tx.execute(
                    "INSERT INTO passage_recordings (passage_id,mbid,weight,source) VALUES (?1,?2,?3,?4)",
                    params![passage_id, mbid,
                            c.get("weight").and_then(|v| v.as_f64()).unwrap_or(1.0),
                            str_of(c, "source")],
                )
                .map_err(|e| e.to_string())?;
                rep.rows_written += 1;
            }
        }
        rep.outcomes.push((md5, Landed::Imported));
    }

    if apply {
        // Only when something actually landed `[SPEC-MESH-080]`: a full
        // resend where every encoding comes back `Already` and no recording
        // needed updating would otherwise still append an identical audit
        // row every time, growing `imported_payloads` from a record of what
        // arrived into a record of how often it was resent.
        if rep.rows_written > 0 {
            tx.execute(
                "INSERT INTO imported_payloads (payload_version,generator,encodings,body,imported_at)\
                 VALUES (?1,?2,?3,?4,?5)",
                params![num(doc, "payload_version").unwrap_or(0), str_of(doc, "generator"),
                        doc.get("encodings").and_then(|v| v.as_array()).map(|a| a.len()).unwrap_or(0) as i64,
                        body, now],
            )
            .map_err(|e| e.to_string())?;
        }
        tx.commit().map_err(|e| e.to_string())?;
    }
    Ok(rep)
}

fn upsert_recording(tx: &rusqlite::Transaction, r: &Value, rep: &mut Report) -> Result<(), String> {
    let mbid = str_of(r, "mbid");
    let exists: bool = tx
        .query_row("SELECT 1 FROM recordings WHERE mbid = ?1", params![mbid], |_| Ok(()))
        .is_ok();
    if !exists {
        tx.execute(
            "INSERT INTO recordings (mbid,title,length_ms,source) VALUES (?1,?2,?3,?4)",
            params![mbid, str_of(r, "title"), num(r, "length_ms"), str_of(r, "source")],
        )
        .map_err(|e| e.to_string())?;
        rep.rows_written += 1;
    }
    let empty = Vec::new();
    for f in r.get("flavor").and_then(|v| v.as_array()).unwrap_or(&empty) {
        let (ch, cl) = (str_of(f, "characteristic"), str_of(f, "class"));
        // Provenance outranks recency, and `manual` outranks everything
        // `[SPEC-DF-070]`. A user's correction is never silently overwritten,
        // so an arriving value that would replace one is simply not applied.
        let local: Option<String> = tx
            .query_row(
                "SELECT source FROM flavor WHERE subject_kind='recording' AND subject_id=?1 \
                 AND characteristic=?2 AND class=?3",
                params![mbid, ch, cl],
                |r| r.get(0),
            )
            .ok();
        if let Some(src) = local {
            if src == "manual" {
                rep.kept_local += 1;
                continue;
            }
        }
        tx.execute(
            "INSERT OR REPLACE INTO flavor (subject_kind,subject_id,characteristic,class,value,source,accuracy)\
             VALUES ('recording',?1,?2,?3,?4,?5,?6)",
            params![mbid, ch, cl, f.get("value").and_then(|v| v.as_f64()).unwrap_or(0.0),
                    str_of(f, "source"), f.get("accuracy").and_then(|v| v.as_f64())],
        )
        .map_err(|e| e.to_string())?;
        rep.rows_written += 1;
    }
    Ok(())
}

fn now_iso() -> String {
    // The library stores ISO-8601 without a zone, as every other writer does.
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = secs / 86_400;
    let (y, m, d) = civil_from_days(days as i64);
    let t = secs % 86_400;
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}", t / 3600, (t % 3600) / 60, t % 60)
}

/// Howard Hinnant's civil-from-days. Here rather than as a dependency: one
/// timestamp does not justify a date crate on a 512 MB appliance `[REQ-HW-140]`.
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(s: &str) -> Value {
        serde_json::from_str(s).unwrap()
    }

    #[test]
    fn accepts_unknown_fields_and_a_newer_version() {
        // `[SPEC-PL-065]`: acceptance must not consult the version number.
        let d = doc(r#"{"payload_version":99,"whatever":{"x":1},"encodings":[
            {"audio_md5":"a","bundle_path":"a.mp3","format":"mp3","duration_ms":10,
             "loudness_lufs":-9.3,
             "passages":[{"kind":"radio","start_ms":0,"end_ms":10,"boundary_src":"x",
                          "segue_frames":[1,2],
                          "recordings":[{"mbid":"m","weight":1.0,"source":"s"}]}]}],
            "recordings":[{"mbid":"m","title":"t","source":"s"}]}"#);
        assert!(unacceptable(&d).is_empty());
    }

    #[test]
    fn rejects_a_missing_required_field() {
        let d = doc(r#"{"payload_version":1,"encodings":[
            {"audio_md5":"a","bundle_path":"a.mp3","format":"mp3","duration_ms":10,
             "passages":[{"kind":"radio","start_ms":0,"end_ms":10,
                          "recordings":[{"mbid":"m","weight":1.0,"source":"s"}]}]}],
            "recordings":[{"mbid":"m","title":"t","source":"s"}]}"#);
        assert!(unacceptable(&d).iter().any(|s| s.contains("boundary_src")));
    }

    #[test]
    fn rejects_a_duration_that_is_present_but_unusable() {
        // Presence passed the required-field check, then `unwrap_or(0)` wrote a
        // zero length into the column the play/skip judgement reads
        // `[SPEC-MPD-092]`. Each of these must be refused, not defaulted.
        for bad in [r#""284250""#, "12.5", "0", "-1"] {
            let d = doc(&format!(
                r#"{{"payload_version":1,"encodings":[
                {{"audio_md5":"a","bundle_path":"a.mp3","format":"mp3","duration_ms":{bad},
                 "passages":[{{"kind":"radio","start_ms":0,"end_ms":10,"boundary_src":"x",
                              "recordings":[{{"mbid":"m","weight":1.0,"source":"s"}}]}}]}}],
                "recordings":[{{"mbid":"m","title":"t","source":"s"}}]}}"#
            ));
            assert!(
                unacceptable(&d).iter().any(|s| s.contains("duration_ms")),
                "duration_ms {bad} should be rejected"
            );
        }
    }

    #[test]
    fn rejects_a_payload_disagreeing_with_itself() {
        let d = doc(r#"{"payload_version":1,"encodings":[
            {"audio_md5":"a","bundle_path":"a.mp3","format":"mp3","duration_ms":10,
             "passages":[{"kind":"radio","start_ms":0,"end_ms":10,"boundary_src":"x",
                          "recordings":[{"mbid":"m","weight":1.0,"source":"s"}]}]}],
            "recordings":[{"mbid":"m","title":"one","source":"s"},
                          {"mbid":"m","title":"two","source":"s"}]}"#);
        assert!(unacceptable(&d).iter().any(|s| s.contains("two entries")));
    }

    #[test]
    fn rejects_an_unconstructable_span() {
        let d = doc(r#"{"payload_version":1,"encodings":[
            {"audio_md5":"a","bundle_path":"a.mp3","format":"mp3","duration_ms":10,
             "passages":[{"kind":"radio","start_ms":10,"end_ms":10,"boundary_src":"x",
                          "recordings":[]}]}],"recordings":[]}"#);
        assert!(unacceptable(&d).iter().any(|s| s.contains("end_ms")));
    }

    // `[SPEC-SUI-226]` fade travelling through the bundle payload: present
    // values are carried, and a sender too old to have carried them at all
    // (fade predates this payload version) falls back to `passages`' own
    // schema default rather than a NOT NULL violation.
    #[test]
    fn fade_present_is_carried() {
        let p = doc(r#"{"fade_in_ms":5,"fade_out_ms":2000,
                         "fade_in_curve":"linear","fade_out_curve":"cosine"}"#);
        assert_eq!(fade_ms(&p, "fade_in_ms"), 5);
        assert_eq!(fade_ms(&p, "fade_out_ms"), 2000);
        assert_eq!(fade_curve(&p, "fade_in_curve"), "linear");
        assert_eq!(fade_curve(&p, "fade_out_curve"), "cosine");
    }

    #[test]
    fn fade_absent_falls_back_to_the_schema_default() {
        let p = doc(r#"{"kind":"radio","start_ms":0,"end_ms":10,"boundary_src":"x"}"#);
        assert_eq!(fade_ms(&p, "fade_in_ms"), 20);
        assert_eq!(fade_ms(&p, "fade_out_ms"), 20);
        assert_eq!(fade_curve(&p, "fade_in_curve"), "exponential");
        assert_eq!(fade_curve(&p, "fade_out_curve"), "exponential");
    }

    /// The minimum of SPEC008 `import()` actually touches -- files, passages,
    /// passage_recordings, recordings, flavor -- built fresh rather than
    /// reused from `db::test_support`, which is scoped to `library.rs`'s own
    /// review-queue fixtures and carries tables (`id_checks`, `artists`,
    /// `listener_play_history`) this file has no reason to depend on.
    fn empty_library() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE,
                 path TEXT NOT NULL, size_bytes INTEGER NOT NULL, mtime REAL NOT NULL,
                 format TEXT NOT NULL, duration_ms INTEGER NOT NULL,
                 first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
             CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY REFERENCES files(file_id),
                 title TEXT, artist TEXT, album TEXT, track_no INTEGER, disc_no INTEGER,
                 has_art INTEGER NOT NULL DEFAULT 0, scanned_at INTEGER NOT NULL);
             CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT NOT NULL,
                 length_ms INTEGER, source TEXT NOT NULL);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                 lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL,
                 boundary_src TEXT NOT NULL, fade_in_ms INTEGER NOT NULL DEFAULT 20,
                 fade_out_ms INTEGER NOT NULL DEFAULT 20,
                 fade_in_curve TEXT NOT NULL DEFAULT 'exponential',
                 fade_out_curve TEXT NOT NULL DEFAULT 'exponential');
             CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
             CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL,
                 weight REAL NOT NULL DEFAULT 1.0, source TEXT NOT NULL,
                 PRIMARY KEY (passage_id, mbid));
             CREATE TABLE flavor (subject_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
                 characteristic TEXT NOT NULL, class TEXT NOT NULL, value REAL NOT NULL,
                 source TEXT NOT NULL, accuracy REAL,
                 PRIMARY KEY (subject_kind, subject_id, characteristic, class));",
        )
        .unwrap();
        c
    }

    fn one_encoding_bundle(md5: &str, mbid: &str, flavor_value: f64, flavor_source: &str) -> Value {
        doc(&format!(
            r#"{{"payload_version":1,"encodings":[
                {{"audio_md5":"{md5}","bundle_path":"a.wav","format":"wav","duration_ms":1000,
                 "passages":[{{"kind":"radio","start_ms":0,"end_ms":1000,"boundary_src":"x",
                              "recordings":[{{"mbid":"{mbid}","weight":1.0,"source":"s"}}]}}]}}],
                "recordings":[{{"mbid":"{mbid}","title":"t","source":"s",
                    "flavor":[{{"characteristic":"mood_happy","class":"happy",
                                "value":{flavor_value},"source":"{flavor_source}"}}]}}]}}"#
        ))
    }

    /// A minimal, real, ffmpeg-decodable audio file -- `hash_encoded` shells
    /// out to ffmpeg `[SPEC-RLK-080]`, so nothing shorter than an actual
    /// container it can open will do. WAV rather than a hand-rolled MP3
    /// frame: no encoder needed, just a 44-byte header ffmpeg reads directly.
    fn write_tiny_wav(path: &std::path::Path) {
        let (sample_rate, num_samples, bits_per_sample, num_channels): (u32, u32, u16, u16) =
            (8000, 100, 16, 1);
        let byte_rate = sample_rate * num_channels as u32 * (bits_per_sample as u32 / 8);
        let block_align = num_channels * (bits_per_sample / 8);
        let data_size = num_samples * block_align as u32;
        let mut buf = Vec::new();
        buf.extend_from_slice(b"RIFF");
        buf.extend_from_slice(&(36 + data_size).to_le_bytes());
        buf.extend_from_slice(b"WAVE");
        buf.extend_from_slice(b"fmt ");
        buf.extend_from_slice(&16u32.to_le_bytes());
        buf.extend_from_slice(&1u16.to_le_bytes()); // PCM
        buf.extend_from_slice(&num_channels.to_le_bytes());
        buf.extend_from_slice(&sample_rate.to_le_bytes());
        buf.extend_from_slice(&byte_rate.to_le_bytes());
        buf.extend_from_slice(&block_align.to_le_bytes());
        buf.extend_from_slice(&bits_per_sample.to_le_bytes());
        buf.extend_from_slice(b"data");
        buf.extend_from_slice(&data_size.to_le_bytes());
        for i in 0..num_samples {
            buf.extend_from_slice(&(((i % 100) as i16) * 100).to_le_bytes());
        }
        std::fs::write(path, &buf).unwrap();
    }

    /// `[SPEC-MESH-080]` The gap `[SPEC-SUI-180]` named, made concrete: a
    /// second bundle arriving for an *already-held* encoding must still let
    /// improved recording-scope data (flavor, here) land -- today's `held`
    /// check `continue`s past the whole encoding, including its credits'
    /// flavor, the moment the file itself is no longer new. A resend of the
    /// *identical* bundle must change nothing (the `flavor_value_updates`
    /// test below covers that side); this is the other direction: a
    /// *different*, newer bundle for audio already on disk.
    #[test]
    fn a_later_bundle_still_updates_flavor_for_an_already_held_file() {
        let mut c = empty_library();
        let audio_dir = std::env::temp_dir().join(format!("lempi-bundle-test-{}", std::process::id()));
        std::fs::create_dir_all(&audio_dir).unwrap();
        let audio_path = audio_dir.join("a.wav");
        write_tiny_wav(&audio_path);
        // `import()` verifies the payload's claimed audio_md5 against the
        // real file `[SPEC-DF-070]` -- hash it for real rather than asserting
        // against a value that would only ever agree with itself.
        let md5 = crate::relink::hash_encoded(&audio_path).unwrap();

        let first = one_encoding_bundle(&md5, "m1", 0.5, "computed:x@1");
        let rep1 = import(&mut c, &first, "body1", &audio_dir, true).unwrap();
        assert!(rep1.refused.is_empty(), "{:?}", rep1.refused);
        assert_eq!(rep1.outcomes, vec![(md5.clone(), Landed::Imported)]);
        let v1: f64 = c
            .query_row(
                "SELECT value FROM flavor WHERE subject_id='m1' AND characteristic='mood_happy'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(v1, 0.5);

        // Same encoding, a later Vipunen run with an improved (still
        // non-manual) flavor value for the same recording.
        let second = one_encoding_bundle(&md5, "m1", 0.9, "computed:x@2");
        let rep2 = import(&mut c, &second, "body2", &audio_dir, true).unwrap();
        assert!(rep2.refused.is_empty(), "{:?}", rep2.refused);
        assert_eq!(rep2.outcomes, vec![(md5, Landed::Already)],
            "the file itself is unchanged -- Already is correct");

        let v2: f64 = c
            .query_row(
                "SELECT value FROM flavor WHERE subject_id='m1' AND characteristic='mood_happy'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(v2, 0.9, "an already-held encoding must not gate updates to its recording's data");

        std::fs::remove_dir_all(&audio_dir).ok();
    }

    /// The other side of the same gap: a `manual` flavor value must survive
    /// a later bundle's computed one, exactly as it already does on a fresh
    /// import (`upsert_recording`'s own check) -- proven here for the
    /// already-held-encoding path specifically, since that is the path the
    /// fix above adds a second call site to.
    #[test]
    fn a_manual_flavor_value_survives_a_later_computed_bundle_even_when_the_file_is_already_held() {
        let mut c = empty_library();
        let audio_dir = std::env::temp_dir().join(format!("lempi-bundle-test2-{}", std::process::id()));
        std::fs::create_dir_all(&audio_dir).unwrap();
        let audio_path = audio_dir.join("a.wav");
        write_tiny_wav(&audio_path);
        let md5 = crate::relink::hash_encoded(&audio_path).unwrap();

        let first = one_encoding_bundle(&md5, "m2", 0.5, "computed:x@1");
        import(&mut c, &first, "body1", &audio_dir, true).unwrap();
        // A listener corrects it locally, same as `upsert_recording`'s own
        // fresh-import test would exercise on the first pass.
        c.execute(
            "UPDATE flavor SET value=1.0, source='manual' \
             WHERE subject_id='m2' AND characteristic='mood_happy'",
            [],
        )
        .unwrap();

        let second = one_encoding_bundle(&md5, "m2", 0.1, "computed:x@2");
        import(&mut c, &second, "body2", &audio_dir, true).unwrap();

        let (v, src): (f64, String) = c
            .query_row(
                "SELECT value, source FROM flavor WHERE subject_id='m2' AND characteristic='mood_happy'",
                [],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .unwrap();
        assert_eq!((v, src.as_str()), (1.0, "manual"), "manual outranks a later computed value here too");

        std::fs::remove_dir_all(&audio_dir).ok();
    }

    /// `[SPEC-PL-032]` The cross-language conformance fixture: Vipunen's
    /// `tools/test_payload.py` checks the same file with `compatible()`, and
    /// this checks it with `unacceptable()` -- one file, both implementations,
    /// per `fixtures/payload/README.md`'s own stated purpose.
    #[test]
    fn the_fade_fields_fixture_is_accepted_by_the_rust_side_too() {
        let text = include_str!("../../fixtures/payload/09-fade-fields.json");
        let d: Value = serde_json::from_str(text).unwrap();
        assert_eq!(unacceptable(&d), Vec::<String>::new());
        let p = &d["encodings"][0]["passages"][0];
        assert_eq!(fade_ms(p, "fade_in_ms"), 15);
        assert_eq!(fade_ms(p, "fade_out_ms"), 1200);
        assert_eq!(fade_curve(p, "fade_in_curve"), "linear");
        assert_eq!(fade_curve(p, "fade_out_curve"), "cosine");
    }
}
