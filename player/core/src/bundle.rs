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

use rusqlite::{params, Connection, OptionalExtension};
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
);
-- The words a bundle carries `[SPEC-LYR-025]`. The same shape
-- `tools/import_lyrics.py` creates on Vipunen's side, and created here for
-- the same reason: a receiver that has never been sent any has no table.
CREATE TABLE IF NOT EXISTS lyrics (
    mbid       TEXT PRIMARY KEY,
    text       TEXT NOT NULL,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL
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
    /// Audio is here and nothing on this host can check it: the payload
    /// carries no byte hash and the host passed no hasher to recompute
    /// `audio_md5`.
    /// Not written -- unverified is not trusted `[REQ-AND-230]` -- and not
    /// called corrupt, since nothing was found to disagree.
    Unverifiable { why: String },
}

/// Recomputes `audio_md5` from a file: the host's -- every host's, a phone's
/// included, since 2026-09-27 `[REQ-AND-270]` -- or absent where a caller
/// passes none.
type Md5Hasher = crate::relink::Hasher;

/// The SHA-256 of a file's bytes, lower-case hex `[SPEC-PL-087]`.
///
/// Of the bytes, not the audio: unlike `audio_md5` it changes when a tag is
/// rewritten, which is right for its one job -- proving the file that arrived
/// is the file that was sent -- and wrong for identity, which stays
/// `audio_md5`'s.
pub fn sha256_file(path: &Path) -> Result<String, String> {
    use sha2::Digest;
    use std::io::Read;
    let mut f = std::fs::File::open(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let mut hasher = sha2::Sha256::new();
    let mut buf = vec![0u8; 1 << 16];
    loop {
        let n = f.read(&mut buf).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().iter().map(|b| format!("{b:02x}")).collect())
}

fn is_sha256_hex(s: &str) -> bool {
    s.len() == 64 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
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
    /// Files held under a retired key, re-keyed to its successor
    /// `[SPEC-PL-097]`.
    pub rekeyed: usize,
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
        // Optional -- a sender older than `[SPEC-PL-087]` omits it -- but a
        // value that is there and unusable would pass every file as corrupt
        // at the far end of the transfer, so it is refused here instead.
        if req(e, "sha256") && !e["sha256"].as_str().is_some_and(is_sha256_hex) {
            out.push(format!("{md5}: encoding.sha256 is not 64 lower-case hex digits"));
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
        // Optional as a whole; present means a whole row `[SPEC-LYR-025]`.
        if let Some(w) = r.get("lyrics").filter(|w| !w.is_null()) {
            for k in ["text", "source", "fetched_at"] {
                if !req(w, k) {
                    out.push(format!("{mbid}: missing lyrics.{k}"));
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

/// `[SPEC-PL-097]`, `[SPEC-RLK-155]`: the retired keys a payload carries,
/// applied here before anything else looks a key up. A file this library
/// holds under an old key is the same file under the new one, so the value is
/// rewritten in every table keyed by it, in one transaction, and the pair
/// recorded in this library's own `audio_md5_aliases`. A key is rewritten only
/// where the old one is held and the new one is not: both held would be two
/// rows for one file, which is a person's to look at, and is left alone and
/// logged. Idempotent -- a second payload carrying the same pairs finds nothing
/// left to do. Returns how many files were re-keyed.
fn apply_aliases(db: &mut Connection, doc: &Value) -> Result<usize, String> {
    let Some(list) = doc.get("aliases").and_then(|v| v.as_array()) else {
        return Ok(0);
    };
    let pairs: Vec<(String, String, String)> = list
        .iter()
        .filter_map(|a| {
            let (old, new) = (a.get("old")?.as_str()?, a.get("new")?.as_str()?);
            let g = a.get("generator").and_then(|v| v.as_str()).unwrap_or("");
            (!old.is_empty() && !new.is_empty() && old != new).then(|| (old.into(), new.into(), g.into()))
        })
        .collect();
    if pairs.is_empty() {
        return Ok(0);
    }
    let tx = db.transaction().map_err(|e| e.to_string())?;
    tx.execute_batch(
        "CREATE TABLE IF NOT EXISTS audio_md5_aliases (old_md5 TEXT PRIMARY KEY, new_md5 TEXT NOT NULL, \
         generator TEXT NOT NULL, rekeyed_at TEXT NOT NULL)",
    )
    .map_err(|e| e.to_string())?;
    let tables: Vec<String> = {
        let mut q = tx
            .prepare("SELECT name FROM sqlite_master WHERE type = 'table' AND name <> 'audio_md5_aliases'")
            .map_err(|e| e.to_string())?;
        let names: Vec<String> =
            q.query_map([], |r| r.get(0)).map_err(|e| e.to_string())?.filter_map(Result::ok).collect();
        names
            .into_iter()
            .filter(|t| {
                tx.prepare(&format!("SELECT audio_md5 FROM \"{t}\" LIMIT 0")).is_ok()
            })
            .collect()
    };
    let held = |k: &str| -> Result<bool, String> {
        tx.query_row("SELECT 1 FROM files WHERE audio_md5 = ?1", params![k], |_| Ok(()))
            .optional()
            .map(|r| r.is_some())
            .map_err(|e| e.to_string())
    };
    let now = now_iso();
    let mut n = 0;
    for (old, new, generator) in &pairs {
        if !held(old)? {
            continue;
        }
        if held(new)? {
            tracing::warn!("aliases: both {old} and its successor {new} are held; left as they are");
            continue;
        }
        for t in &tables {
            // `files` is checked above and cannot collide. A cache keyed by
            // (audio_md5, ...) could already hold a row under the new key;
            // that row wins, and the old one goes.
            tx.execute(&format!("UPDATE OR IGNORE \"{t}\" SET audio_md5 = ?1 WHERE audio_md5 = ?2"), params![new, old])
                .map_err(|e| format!("re-keying {t}: {e}"))?;
            tx.execute(&format!("DELETE FROM \"{t}\" WHERE audio_md5 = ?1"), params![old])
                .map_err(|e| format!("re-keying {t}: {e}"))?;
        }
        tx.execute(
            "INSERT OR REPLACE INTO audio_md5_aliases VALUES (?1, ?2, ?3, ?4)",
            params![old, new, generator, now],
        )
        .map_err(|e| e.to_string())?;
        n += 1;
    }
    tx.commit().map_err(|e| e.to_string())?;
    if n > 0 {
        tracing::info!("aliases: {n} file(s) re-keyed to their successors");
    }
    Ok(n)
}

/// Import a bundle. `audio_root` is where arriving audio lives — the bundle's
/// own `audio/` directory, or the library root it has already been placed in.
///
/// **The import binds what it creates** `[SPEC-PL-085]`. Relink never creates a
/// row `[SPEC-RLK-090]`, so it cannot bind an arriving one; this hashes each
/// file it is given, verifies it against the payload, stats it for the
/// machine-scope columns `[SPEC-DF-030]` and writes the path itself. No
/// separate relink pass is needed for a bundle.
///
/// Verified two ways where it can be `[SPEC-PL-087]`: the bytes against the
/// payload's `sha256` when it carries one, and `audio_md5` recomputed by
/// `md5_hasher` when the host passes one `[REQ-AND-270]`. Without a hasher
/// the byte hash is the check `[REQ-AND-230]`, and a file with neither is
/// reported, never written.
pub fn import(
    db: &mut Connection,
    doc: &Value,
    body: &str,
    audio_root: &Path,
    apply: bool,
    md5_hasher: Option<Md5Hasher>,
) -> Result<Report, String> {
    import_bound(db, doc, body, audio_root, apply, md5_hasher, &HashMap::new(), &Default::default())
}

/// [`import_with`], with some encodings bound where they already are rather
/// than under `audio_root`: `bind` maps an `audio_md5` to its file. A phone
/// uses it for a file it found in its own storage, which a bundle then names
/// `[REQ-AND-260]`.
// Eight: each is one thing the caller decides, and a struct would only
// rename them.
#[allow(clippy::too_many_arguments)]
fn import_bound(
    db: &mut Connection,
    doc: &Value,
    body: &str,
    audio_root: &Path,
    apply: bool,
    md5_hasher: Option<Md5Hasher>,
    bind: &HashMap<String, PathBuf>,
    trusted: &HashMap<String, String>,
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
        // Separate from `DDL` rather than folded into it: `execute_batch` is
        // all-or-nothing, and `ADD COLUMN` fails on every run after the first,
        // which would take the whole batch down with it `[SPEC-RLK-150]`.
        crate::db::ensure_md5_generator_column(db);
        crate::db::ensure_sha256_column(db);
        rep.rekeyed = apply_aliases(db, doc)?;
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
    // Every row this run writes was verified by the same hasher a moment
    // earlier, so its name is the true one for all of them `[SPEC-RLK-150]`.
    // Where nothing here recomputed `audio_md5`, the value is the sender's and
    // its hasher unknown: `NULL`, not a guess.
    let generator = md5_hasher.map(|h| h.generator);

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

        let path: PathBuf = bind
            .get(&md5)
            .cloned()
            .unwrap_or_else(|| audio_root.join(rel.replace('/', std::path::MAIN_SEPARATOR_STR)));
        if !path.is_file() {
            rep.outcomes.push((md5, Landed::AwaitingAudio { at: path.display().to_string() }));
            continue;
        }
        // Verify before trusting `[SPEC-DF-070]`. The bytes first, where the
        // payload carries their hash: cheap, and the only check a phone has
        // `[SPEC-PL-087]`.
        let sha256 = e.get("sha256").and_then(|v| v.as_str());
        // `trusted`: a file a phone's own scan already identified, unchanged
        // since `[REQ-AND-260]` -- by these very bytes, or by this audio in
        // bytes of its own (a re-tagged copy). Its value is the file's own
        // byte hash, which is what is recorded for it.
        let own = trusted.get(&md5).map(String::as_str);
        if let Some(want) = sha256.filter(|_| own.is_none()) {
            let found = match sha256_file(&path) {
                Ok(h) if h == want => None,
                Ok(h) => Some(format!("sha256 {h}")),
                Err(err) => Some(err),
            };
            if let Some(found) = found {
                let expected = format!("sha256 {want}");
                rep.outcomes.push((md5, Landed::Corrupt { expected, found }));
                continue;
            }
        }
        // Then `audio_md5`, with the hasher relink already uses -- one
        // implementation `[GDE-FBD-040]`, and the one the library is keyed
        // by `[SPEC-RLK-150]`. Still asked where the bytes
        // matched: it is the identity the row is keyed on, and a sender whose
        // `audio_md5` disagrees with its own file is worth catching here.
        // Not asked of a trusted file: its identity is already established,
        // and on a phone asking would be a read of the whole SD card.
        match md5_hasher.filter(|_| own.is_none()) {
            Some(hasher) => {
                let found = match (hasher.hash)(&path) {
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
            }
            None if own.is_some() => {}
            // The bytes are the ones the sender hashed, so its `audio_md5`
            // is carried as sent.
            None if sha256.is_some() => {}
            None => {
                let why = "no sha256 in the payload, and no hasher here to recompute audio_md5".into();
                rep.outcomes.push((md5, Landed::Unverifiable { why }));
                continue;
            }
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
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,md5_generator,sha256)\
             VALUES (?1,?2,?3,?4,?5,?6,?7,?7,?8,?9)",
            params![md5, path.to_string_lossy(), meta.len() as i64, mtime,
                    str_of(e, "format"),
                    num(e, "duration_ms")
                        .filter(|d| *d > 0)
                        .ok_or_else(|| format!("{md5}: encoding.duration_ms is not a positive integer"))?,
                    now,
                    // `NULL` where the host passed no hasher. The import got
                    // this far, so the hash was computed -- but by what is
                    // then genuinely unknown, and a guess in a provenance
                    // column is worse than a gap.
                    generator,
                    // The byte hash just verified against this file, kept so
                    // a phone can recognise the file again by it
                    // `[REQ-AND-260]` -- the file's own where it was trusted,
                    // since a re-tagged copy's bytes are not the sender's.
                    // NULL where the payload carried none.
                    own.or(sha256)],
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
    if let Some(w) = r.get("lyrics").filter(|w| !w.is_null()) {
        upsert_lyrics(tx, &mbid, w, rep)?;
    }
    Ok(())
}

/// A recording's words `[SPEC-LYR-025]`, into this database -- which on a
/// phone is private storage `[REQ-AND-202]`.
///
/// The flavor rule, ranked the same way `[SPEC-DF-070]`: a `manual` text is
/// never replaced, and otherwise the more recently fetched text wins -- so a
/// resend of the same bundle changes nothing, and a corrected source reaches
/// the phone on the next one.
fn upsert_lyrics(tx: &rusqlite::Transaction, mbid: &str, w: &Value, rep: &mut Report) -> Result<(), String> {
    let (text, source, fetched) = (str_of(w, "text"), str_of(w, "source"), str_of(w, "fetched_at"));
    let local: Option<(String, String)> = tx
        .query_row("SELECT source, fetched_at FROM lyrics WHERE mbid = ?1", params![mbid], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .ok();
    match local {
        Some((src, _)) if src == "manual" => {
            rep.kept_local += 1;
            return Ok(());
        }
        // ISO-8601 in one zone compares as text; an older or equal arrival is
        // not news.
        Some((_, at)) if at >= fetched => return Ok(()),
        _ => {}
    }
    tx.execute(
        "INSERT INTO lyrics (mbid, text, source, fetched_at) VALUES (?1, ?2, ?3, ?4) \
         ON CONFLICT(mbid) DO UPDATE SET text = excluded.text, \
         source = excluded.source, fetched_at = excluded.fetched_at",
        params![mbid, text, source, fetched],
    )
    .map_err(|e| e.to_string())?;
    rep.rows_written += 1;
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

// --------------------------------------------------------- staged import ---

/// What became of a bundle brought to a phone `[REQ-AND-230]`.
#[derive(Debug, Default, PartialEq)]
pub struct StagedReport {
    /// Non-empty means the payload was refused whole, and nothing was placed
    /// or written.
    pub refused: Vec<String>,
    /// Placed under the music folder, verified and written to the library.
    pub imported: usize,
    /// Held already, by `audio_md5`, with the same bytes: not copied again.
    pub already: usize,
    /// Held already, and Vipunen has rewritten the file since, so the phone's
    /// copy was replaced where it lies `[REQ-AND-250]`.
    pub replaced: usize,
    /// A rewritten file whose replacement the system refused, with why.
    pub not_replaced: Vec<String>,
    /// The same refusals, as what a host needs to ask the system's leave and
    /// write the replacement itself `[SPEC-PL-098]`.
    pub refused_replacements: Vec<RefusedReplacement>,
    /// Files the phone had found for itself and held as tags-only, now made
    /// whole where they lie with what Vipunen sent `[REQ-AND-260]`.
    pub upgraded: usize,
    /// Found files the bundle named by bytes they no longer hold: changed
    /// since the scan, and left as they are.
    pub changed_since_scan: Vec<String>,
    /// A byte-identical file was already where it belongs, and was bound
    /// rather than copied beside itself `[REQ-AND-240]`.
    pub reused: usize,
    /// Arrived, and its bytes are not the ones Vipunen sent. Never placed.
    pub corrupt: Vec<String>,
    /// Described by the payload, but not in the bundle.
    pub missing: Vec<String>,
    /// No byte hash in the payload, and nothing here can check it otherwise.
    pub unverifiable: Vec<String>,
    /// A different file already has this name in the music folder. It is left
    /// alone, and the arriving one is not placed.
    pub conflicts: Vec<String>,
    /// A `bundle_path` that would land outside the music folder.
    pub unsafe_paths: Vec<String>,
    pub rows_written: usize,
    /// Files held under a retired key, re-keyed to its successor
    /// `[SPEC-PL-097]`.
    pub rekeyed: usize,
    /// Releases, and their cover images, stored `[SPEC-PL-105]`.
    pub releases: usize,
    pub covers: usize,
    /// Cover files absent or not matching their byte hash: not stored.
    pub bad_covers: Vec<String>,
}

/// A replacement the system refused: the phone's copy, the staged file that
/// should replace it, and that file's byte hash `[SPEC-PL-098]`.
#[derive(Debug, Clone, PartialEq)]
pub struct RefusedReplacement {
    pub held: String,
    pub staged: String,
    pub sha256: String,
}

/// What [`import_releases`] did.
#[derive(Debug, Default, PartialEq)]
pub struct ReleasesReport {
    pub releases: usize,
    pub tracks: usize,
    /// Cover images stored, front and back counted apart.
    pub covers: usize,
    /// Cover files named by the payload that were absent or did not match
    /// their byte hash: not stored.
    pub bad_covers: Vec<String>,
}

/// Columns a catalogue made before they existed lacks: added, as
/// `ensure_sha256_column` adds its own. A phone's catalogue has `releases`
/// with four of these ten columns and `release_recordings` without `chosen`,
/// which the player's cover lookup orders by.
fn ensure_release_columns(db: &Connection) -> Result<(), String> {
    db.execute_batch(
        "CREATE TABLE IF NOT EXISTS releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT, \
             source TEXT NOT NULL);
         CREATE TABLE IF NOT EXISTS release_recordings (release_mbid TEXT NOT NULL, mbid TEXT NOT NULL, \
             position INTEGER, source TEXT NOT NULL, PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;
         CREATE TABLE IF NOT EXISTS cover_art (release_mbid TEXT PRIMARY KEY, front BLOB, back BLOB, \
             source TEXT NOT NULL, fetched_at TEXT NOT NULL);",
    )
    .map_err(|e| e.to_string())?;
    for (table, cols) in [
        ("releases", &["release_group TEXT", "status TEXT", "primary_type TEXT", "secondary_types TEXT",
                       "country TEXT", "track_count INTEGER"][..]),
        ("release_recordings", &["track_length_ms INTEGER", "chosen INTEGER DEFAULT 0", "disc INTEGER"][..]),
    ] {
        let have: std::collections::HashSet<String> = db
            .prepare(&format!("PRAGMA table_info({table})"))
            .map_err(|e| e.to_string())?
            .query_map([], |r| r.get::<_, String>(1))
            .map_err(|e| e.to_string())?
            .filter_map(Result::ok)
            .collect();
        for col in cols {
            let name = col.split(' ').next().unwrap_or_default();
            if !have.contains(name) {
                db.execute(&format!("ALTER TABLE {table} ADD COLUMN {col}"), []).map_err(|e| e.to_string())?;
            }
        }
    }
    Ok(())
}

/// `[SPEC-PL-105]`: the releases a payload carries, their tracks, and their
/// covers, into the catalogue, so the player's third cover source -- the
/// archive, keyed by release -- works on a receiver too. On a phone it is the
/// only one a bundle can add: Android keeps pictures out of `Music/`, so a
/// cover cannot travel as a file beside the audio.
///
/// A cover file is read from `bundle_root` (the staged bundle), and stored
/// only if its bytes match the hash the payload gives. A track is linked only
/// to a recording the catalogue holds. Idempotent: a second import of the same
/// payload changes nothing.
pub fn import_releases(db: &mut Connection, doc: &Value, bundle_root: &Path) -> Result<ReleasesReport, String> {
    let mut rep = ReleasesReport::default();
    let Some(list) = doc.get("releases").and_then(|v| v.as_array()).filter(|l| !l.is_empty()) else {
        return Ok(rep);
    };
    ensure_release_columns(db)?;
    let tx = db.transaction().map_err(|e| e.to_string())?;
    let opt = |v: &Value, k: &str| v.get(k).and_then(|x| x.as_str()).map(str::to_string);
    let int = |v: &Value, k: &str| v.get(k).and_then(|x| x.as_i64());
    for r in list {
        let mbid = str_of(r, "mbid");
        tx.execute(
            "INSERT INTO releases (mbid,title,release_date,source,release_group,status,primary_type,secondary_types,\
             country,track_count) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10) ON CONFLICT(mbid) DO UPDATE SET \
             title=excluded.title, release_date=excluded.release_date, source=excluded.source, \
             release_group=excluded.release_group, status=excluded.status, primary_type=excluded.primary_type, \
             secondary_types=excluded.secondary_types, country=excluded.country, track_count=excluded.track_count",
            params![mbid, str_of(r, "title"), opt(r, "release_date"), str_of(r, "source"), opt(r, "release_group"),
                    opt(r, "status"), opt(r, "primary_type"), opt(r, "secondary_types"), opt(r, "country"),
                    int(r, "track_count")],
        )
        .map_err(|e| format!("release {mbid}: {e}"))?;
        rep.releases += 1;
        for t in r.get("tracks").and_then(|v| v.as_array()).map(Vec::as_slice).unwrap_or(&[]) {
            let rec = str_of(t, "recording");
            let held = tx
                .query_row("SELECT 1 FROM recordings WHERE mbid = ?1", params![rec], |_| Ok(()))
                .optional()
                .map_err(|e| e.to_string())?
                .is_some();
            if !held {
                continue;
            }
            tx.execute(
                "INSERT INTO release_recordings (release_mbid,mbid,position,source,track_length_ms,chosen,disc) \
                 VALUES (?1,?2,?3,?4,?5,?6,?7) ON CONFLICT(release_mbid,mbid) DO UPDATE SET \
                 position=excluded.position, source=excluded.source, track_length_ms=excluded.track_length_ms, \
                 chosen=excluded.chosen, disc=excluded.disc",
                params![mbid, rec, int(t, "position"), str_of(t, "source"), int(t, "track_length_ms"),
                        int(t, "chosen").unwrap_or(0), int(t, "disc")],
            )
            .map_err(|e| format!("release {mbid} track {rec}: {e}"))?;
            rep.tracks += 1;
        }
        let Some(cover) = r.get("cover").filter(|c| c.is_object()) else { continue };
        let mut sides: [Option<Vec<u8>>; 2] = [None, None];
        for (i, side) in ["front", "back"].iter().enumerate() {
            let Some(c) = cover.get(*side) else { continue };
            let (file, want) = (str_of(c, "file"), str_of(c, "sha256"));
            let bytes = safe_rel(&file)
                .filter(|rel| rel.starts_with("covers"))
                .and_then(|rel| std::fs::read(bundle_root.join(rel)).ok());
            match bytes {
                Some(b) if sha256_bytes(&b) == want => {
                    sides[i] = Some(b);
                    rep.covers += 1;
                }
                _ => rep.bad_covers.push(file),
            }
        }
        if sides.iter().any(Option::is_some) {
            let [front, back] = sides;
            tx.execute(
                "INSERT INTO cover_art (release_mbid,front,back,source,fetched_at) VALUES (?1,?2,?3,?4,?5) \
                 ON CONFLICT(release_mbid) DO UPDATE SET front=COALESCE(excluded.front, cover_art.front), \
                 back=COALESCE(excluded.back, cover_art.back), source=excluded.source, fetched_at=excluded.fetched_at",
                params![mbid, front, back, str_of(cover, "source"), str_of(cover, "fetched_at")],
            )
            .map_err(|e| format!("cover of {mbid}: {e}"))?;
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(rep)
}

/// The SHA-256 of some bytes, lower-case hex, as [`sha256_file`] gives a file's.
fn sha256_bytes(b: &[u8]) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(b).iter().map(|x| format!("{x:02x}")).collect()
}

/// Whether the `files` row aliased `f` is tags-only: a file a phone found for
/// itself, with one whole-file passage named from its own tags
/// `[REQ-AND-260]`. Marked by that passage, not by the key: found files were
/// keyed `sha256:<bytes>` until the phone could compute a signature, and by
/// their signature since `[SPEC-SC-041]`.
pub const TAGS_ONLY_SQL: &str =
    "EXISTS (SELECT 1 FROM passages p WHERE p.file_id = f.file_id AND p.boundary_src = 'found:tags')";

/// Whether the file at `path` is the size and age a scan recorded: then what
/// the scan established about its bytes still holds, and reading them again
/// proves nothing new -- on the Moto G, 5,639 files and fourteen minutes of SD.
fn unchanged_since(path: &str, size: i64, mtime: f64) -> bool {
    let Ok(meta) = std::fs::metadata(path) else { return false };
    let now = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(-1.0);
    meta.len() as i64 == size && (now - mtime).abs() < 0.001
}

/// A bundle path as components under a root, or `None` if it could leave it
/// -- absolute, a drive, or any `..`. A bundle comes from outside the app.
fn safe_rel(rel: &str) -> Option<PathBuf> {
    use std::path::Component;
    let p = Path::new(rel);
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            Component::Normal(s) => out.push(s),
            Component::CurDir => {}
            _ => return None,
        }
    }
    (!out.as_os_str().is_empty()).then_some(out)
}

/// The payload files a staged bundle carries, in order: `payload.json`, or
/// `payload-001.json`, `payload-002.json`, ... when it was written in parts
/// `[SPEC-PL-095]`.
fn payload_parts(staging: &Path) -> Result<Vec<PathBuf>, String> {
    let single = staging.join("payload.json");
    if single.is_file() {
        return Ok(vec![single]);
    }
    let mut parts: Vec<PathBuf> = std::fs::read_dir(staging)
        .map_err(|e| format!("cannot read {}: {e}", staging.display()))?
        .filter_map(|d| d.ok().map(|d| d.path()))
        .filter(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(is_part_name))
        .collect();
    parts.sort();
    if parts.is_empty() {
        return Err(format!("no payload.json in the bundle ({})", staging.display()));
    }
    Ok(parts)
}

/// `payload-<digits>.json`.
pub fn is_part_name(n: &str) -> bool {
    n.strip_prefix("payload-")
        .and_then(|r| r.strip_suffix(".json"))
        .is_some_and(|d| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit()))
}

fn read_payload(path: &Path) -> Result<(String, Value), String> {
    let body = std::fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let doc = serde_json::from_str(&body).map_err(|e| format!("{} is not JSON: {e}", path.display()))?;
    Ok((body, doc))
}

/// Import a bundle unpacked into `staging` -- its `payload.json`, and its audio
/// under `audio/` -- into the library, placing its audio under `music_root`
/// `[REQ-AND-210]`.
///
/// **Verified before it is placed**, not after. Every staged file is checked
/// against the byte hash Vipunen carried `[REQ-AND-230]` while it is still in
/// private staging, so a file that arrived damaged never reaches shared
/// storage, where other apps would find it. Only then is it copied into the
/// music folder, and [`import`] binds it there, checking it again.
///
/// **Never duplicates** `[REQ-AND-240]`. An encoding the library holds already
/// is not copied, and a byte-identical file already at its destination is
/// bound rather than copied beside itself. A *different* file at the
/// destination is left alone and reported: this does not overwrite a file it
/// did not write.
///
/// The caller removes `staging` afterwards; this reads it and writes nothing
/// there.
///
/// `md5_hasher` is the host's identity hasher `[REQ-AND-270]`, or `None`:
/// then every file needs the byte hash.
pub fn import_staged(
    db: &mut Connection,
    staging: &Path,
    music_root: &Path,
    md5_hasher: Option<Md5Hasher>,
) -> Result<StagedReport, String> {
    let parts = payload_parts(staging)?;
    // Acceptance is per bundle and all-or-nothing `[SPEC-PL-070]`: every part
    // is checked before any is written -- one at a time, so a large bundle
    // is never all in memory at once.
    let mut total = StagedReport::default();
    let many = parts.len() > 1;
    for part in &parts {
        let (_, doc) = read_payload(part)?;
        let name = part.file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_default();
        for r in unacceptable(&doc) {
            total.refused.push(if many { format!("{name}: {r}") } else { r });
        }
    }
    if !total.refused.is_empty() {
        return Ok(total);
    }
    for part in &parts {
        let (body, doc) = read_payload(part)?;
        let r = import_one_payload(db, staging, music_root, md5_hasher, &doc, &body)?;
        total.imported += r.imported;
        total.already += r.already;
        total.replaced += r.replaced;
        total.upgraded += r.upgraded;
        total.rekeyed += r.rekeyed;
        total.releases += r.releases;
        total.covers += r.covers;
        total.bad_covers.extend(r.bad_covers);
        total.reused += r.reused;
        total.rows_written += r.rows_written;
        total.not_replaced.extend(r.not_replaced);
        total.refused_replacements.extend(r.refused_replacements);
        total.corrupt.extend(r.corrupt);
        total.missing.extend(r.missing);
        total.unverifiable.extend(r.unverifiable);
        total.conflicts.extend(r.conflicts);
        total.unsafe_paths.extend(r.unsafe_paths);
        total.changed_since_scan.extend(r.changed_since_scan);
    }
    Ok(total)
}

fn import_one_payload(
    db: &mut Connection,
    staging: &Path,
    music_root: &Path,
    md5_hasher: Option<Md5Hasher>,
    doc: &Value,
    body: &str,
) -> Result<StagedReport, String> {
    let mut rep = StagedReport::default();
    let empty = Vec::new();
    crate::db::ensure_sha256_column(db);
    // Before any key is looked up: a file held under a retired key must be
    // found under its new one `[SPEC-PL-097]`.
    rep.rekeyed = apply_aliases(db, doc)?;
    let mut upgrades: Vec<(i64, String, PathBuf)> = Vec::new();
    let mut trusted: HashMap<String, String> = HashMap::new();
    for e in doc.get("encodings").and_then(|v| v.as_array()).unwrap_or(&empty) {
        let md5 = str_of(e, "audio_md5");
        let rel = str_of(e, "bundle_path");
        let held: Option<(i64, String, i64, f64, Option<String>, bool)> = db
            .query_row(
                &format!(
                    "SELECT file_id, path, size_bytes, mtime, sha256, {TAGS_ONLY_SQL} FROM files f WHERE audio_md5 = ?1"
                ),
                params![md5],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)),
            )
            .ok();
        if let Some((file_id, held_path, size, mtime, own_sha, tags_only)) = held {
            if !tags_only {
                replace_if_rewritten(db, e, &md5, &rel, staging, Path::new(&held_path), &mut rep)?;
                continue;
            }
            // `[REQ-AND-260]`, by audio: the phone found this audio in its own
            // storage -- perhaps in bytes of its own, re-tagged -- and holds it
            // tags-only under its signature. The bundle makes that file whole
            // where it lies. Trusted if unchanged since the scan identified
            // it; otherwise identified again, and left alone if it is no
            // longer this audio.
            let own = match own_sha {
                Some(h) if unchanged_since(&held_path, size, mtime) => Some(h),
                _ => match md5_hasher.map(|h| (h.hash)(Path::new(&held_path))) {
                    Some(Ok(found)) if found == md5 => sha256_file(Path::new(&held_path)).ok(),
                    _ => None,
                },
            };
            match own {
                Some(h) => {
                    trusted.insert(md5.clone(), h);
                    upgrades.push((file_id, md5.clone(), PathBuf::from(held_path)));
                }
                None => rep.changed_since_scan.push(held_path),
            }
            continue;
        }
        // `[REQ-AND-260]`: the phone found these very bytes in its own storage
        // and holds them as tags-only. The bundle makes that file whole where
        // it lies -- its tags-only rows give way to Vipunen's, bound to the
        // same path -- rather than copying it beside itself `[REQ-AND-240]`.
        if let Some(want) = e.get("sha256").and_then(|v| v.as_str()) {
            let found: Option<(i64, String, i64, f64)> = db
                .query_row(
                    &format!(
                        "SELECT file_id, path, size_bytes, mtime FROM files f \
                         WHERE sha256 = ?1 AND {TAGS_ONLY_SQL}"
                    ),
                    params![want],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
                )
                .ok();
            if let Some((file_id, found_path, size, mtime)) = found {
                if let Ok(meta) = std::fs::metadata(&found_path) {
                    // The scan hashed these bytes. If the file is the same
                    // size and age as then, hashing it again proves nothing
                    // new -- on the Moto G that is 5,639 files and fourteen
                    // minutes of SD reads. Otherwise it is checked.
                    let now = meta
                        .modified()
                        .ok()
                        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                        .map(|d| d.as_secs_f64())
                        .unwrap_or(-1.0);
                    if meta.len() as i64 == size && (now - mtime).abs() < 0.001 {
                        trusted.insert(md5.clone(), want.to_string());
                    } else if sha256_file(Path::new(&found_path)).ok().as_deref() != Some(want) {
                        // Changed since the scan, and no longer these bytes:
                        // left exactly as it is, tags-only rows and all. It
                        // is checked here, before anything is removed.
                        rep.changed_since_scan.push(found_path);
                        continue;
                    }
                    upgrades.push((file_id, md5.clone(), PathBuf::from(found_path)));
                    continue;
                }
            }
        }
        let Some(rel_path) = safe_rel(&rel) else {
            rep.unsafe_paths.push(rel);
            continue;
        };
        let staged = staging.join("audio").join(&rel_path);
        if !staged.is_file() {
            rep.missing.push(rel);
            continue;
        }
        let Some(want) = e.get("sha256").and_then(|v| v.as_str()) else {
            if md5_hasher.is_none() {
                rep.unverifiable.push(rel);
                continue;
            }
            // A host with a hasher: `import` checks audio_md5 once placed.
            place(&staged, &music_root.join(&rel_path), None, &rel, &mut rep)?;
            continue;
        };
        match sha256_file(&staged) {
            Ok(h) if h == want => {}
            _ => {
                rep.corrupt.push(rel);
                continue;
            }
        }
        place(&staged, &music_root.join(&rel_path), Some(want), &rel, &mut rep)?;
    }

    // Everything placed is now where `import` looks for it. It verifies each
    // again and writes the rows; an encoding that was not placed is simply
    // not there, and lands as awaiting its audio, which writes nothing.
    // The tags-only rows of each found file go first, in one transaction, so
    // its full rows can take the same path. Its plays keep their times but
    // no longer name a passage that exists; a tags-only passage has no
    // recording, so they were never part of any recording's history.
    let mut bind = HashMap::new();
    if !upgrades.is_empty() {
        let tx = db.transaction().map_err(|e| e.to_string())?;
        for (file_id, md5, path) in &upgrades {
            for sql in [
                "DELETE FROM passage_recordings WHERE passage_id IN (SELECT passage_id FROM passages WHERE file_id = ?1)",
                "DELETE FROM passages WHERE file_id = ?1",
                "DELETE FROM file_tags WHERE file_id = ?1",
                "DELETE FROM files WHERE file_id = ?1",
            ] {
                // `file_tags` may be absent from an older catalogue.
                let _ = tx.execute(sql, params![file_id]);
            }
            bind.insert(md5.clone(), path.clone());
        }
        tx.commit().map_err(|e| e.to_string())?;
    }
    let r = import_bound(db, doc, body, music_root, true, md5_hasher, &bind, &trusted)?;
    // After the encodings, so each track finds the recording it links to.
    let rel = import_releases(db, doc, staging)?;
    rep.releases = rel.releases;
    rep.covers = rel.covers;
    rep.bad_covers = rel.bad_covers;
    rep.upgraded = upgrades.len().min(r.count(|o| *o == Landed::Imported));
    rep.rows_written = r.rows_written;
    rep.imported = r.count(|o| *o == Landed::Imported);
    rep.reused = rep.reused.min(rep.imported);
    rep.imported = rep.imported.saturating_sub(rep.reused + rep.upgraded);
    Ok(rep)
}

/// `[REQ-AND-250]`: the phone holds this encoding -- the same audio -- and the
/// bundle brings it again. If its bytes differ, Vipunen has rewritten the
/// file (its tags, most often), and the phone's copy is replaced where it
/// lies rather than kept beside the new one. Only with a staged file that
/// verifies against the payload's hash; otherwise it is simply held already.
/// A replacement Android refuses -- a file the app did not write -- is
/// reported for that file, and fails nothing else.
fn replace_if_rewritten(
    db: &Connection,
    e: &Value,
    md5: &str,
    rel: &str,
    staging: &Path,
    held: &Path,
    rep: &mut StagedReport,
) -> Result<(), String> {
    let staged = safe_rel(rel).map(|r| staging.join("audio").join(r));
    let (Some(want), Some(staged)) = (e.get("sha256").and_then(|v| v.as_str()), staged) else {
        rep.already += 1;
        return Ok(());
    };
    // A held file that is not where the catalogue says is relink's to find
    // `[SPEC-RLK-090]`, not this import's to write in its absence.
    if !staged.is_file() || !held.is_file() {
        rep.already += 1;
        return Ok(());
    }
    if sha256_file(held).ok().as_deref() == Some(want) {
        // Already these bytes -- perhaps written since by the host, with the
        // system's leave `[SPEC-PL-098]`. The catalogue follows the file.
        record_bytes(db, md5, want, held)?;
        rep.already += 1;
        return Ok(());
    }
    if sha256_file(&staged).ok().as_deref() != Some(want) {
        rep.corrupt.push(rel.to_string());
        return Ok(());
    }
    let part = part_name(held);
    let replaced = std::fs::copy(&staged, &part)
        .and_then(|_| std::fs::rename(&part, held))
        .map_err(|err| err.to_string());
    if let Err(why) = replaced {
        let _ = std::fs::remove_file(&part);
        rep.not_replaced.push(format!("{rel}: {why}"));
        rep.refused_replacements.push(RefusedReplacement {
            held: held.to_string_lossy().into_owned(),
            staged: staged.to_string_lossy().into_owned(),
            sha256: want.to_string(),
        });
        return Ok(());
    }
    let meta = std::fs::metadata(held).map_err(|e| format!("cannot stat {}: {e}", held.display()))?;
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    db.execute(
        "UPDATE files SET sha256 = ?1, size_bytes = ?2, mtime = ?3, last_seen = ?4 WHERE audio_md5 = ?5",
        params![want, meta.len() as i64, mtime, now_iso(), md5],
    )
    .map_err(|e| e.to_string())?;
    rep.replaced += 1;
    Ok(())
}

/// The catalogue's record of a file's bytes, made to agree with the file at
/// `held`, whose bytes are `sha256`. Nothing is written when it agrees already.
fn record_bytes(db: &Connection, md5: &str, sha256: &str, held: &Path) -> Result<(), String> {
    let recorded: Option<String> = db
        .query_row("SELECT sha256 FROM files WHERE audio_md5 = ?1", params![md5], |r| r.get(0))
        .optional()
        .map_err(|e| e.to_string())?
        .flatten();
    if recorded.as_deref() == Some(sha256) {
        return Ok(());
    }
    let meta = std::fs::metadata(held).map_err(|e| format!("cannot stat {}: {e}", held.display()))?;
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    db.execute(
        "UPDATE files SET sha256 = ?1, size_bytes = ?2, mtime = ?3, last_seen = ?4 WHERE audio_md5 = ?5",
        params![sha256, meta.len() as i64, mtime, now_iso(), md5],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// Where a file is written before it is renamed into place. It keeps the
/// audio extension: Android's shared storage lets an app create only files
/// whose names say what they are in `Music/`, and refused `x.lempi-part`
/// with EPERM on the Moto G, 2026-09-26.
fn part_name(dest: &Path) -> PathBuf {
    match dest.extension() {
        Some(ext) => dest.with_extension(format!("lempi-part.{}", ext.to_string_lossy())),
        None => dest.with_extension("lempi-part"),
    }
}

/// Copy one verified file into the music folder, unless the same bytes are
/// there already `[REQ-AND-240]` or different ones are (a conflict).
fn place(staged: &Path, dest: &Path, sha256: Option<&str>, rel: &str, rep: &mut StagedReport) -> Result<(), String> {
    if dest.exists() {
        match (sha256, sha256_file(dest)) {
            (Some(want), Ok(have)) if have == want => rep.reused += 1,
            _ => rep.conflicts.push(rel.to_string()),
        }
        return Ok(());
    }
    if let Some(dir) = dest.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    }
    // Written beside itself and renamed, so a copy cut short is never found
    // under the real name.
    let part = part_name(dest);
    std::fs::copy(staged, &part).map_err(|e| format!("cannot write {}: {e}", part.display()))?;
    std::fs::rename(&part, dest).map_err(|e| format!("cannot place {}: {e}", dest.display()))?;
    Ok(())
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

    /// The identity hasher these tests stand in for the host's. This crate
    /// cannot reach a demuxer `[GDE-AND-045]`, and what is under test here is
    /// that an import verifies with, and records, whatever it is given -- so
    /// the bytes' own hash, cut to `audio_md5`'s length, is enough.
    const TEST_HASHER: Md5Hasher = crate::relink::Hasher {
        hash: |p| sha256_file(p).map(|h| h[..32].to_string()),
        generator: "test@1",
    };

    /// A small audio file for the tests above to import.
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
        // real file `[SPEC-DF-070]` -- hash it rather than asserting against a
        // value that would only ever agree with itself.
        let md5 = (TEST_HASHER.hash)(&audio_path).unwrap();

        let first = one_encoding_bundle(&md5, "m1", 0.5, "computed:x@1");
        let rep1 = import(&mut c, &first, "body1", &audio_dir, true, Some(TEST_HASHER)).unwrap();
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
        let rep2 = import(&mut c, &second, "body2", &audio_dir, true, Some(TEST_HASHER)).unwrap();
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

    /// `[SPEC-RLK-150]` precondition 3: an imported row records *what hashed
    /// it*, and a row written before the column existed stays `NULL`.
    ///
    /// `empty_library()` builds `files` without `md5_generator`, which is
    /// exactly the library this migration exists for -- so this also proves
    /// the `ALTER TABLE` runs and that the pre-existing row beside it is not
    /// back-annotated.
    #[test]
    fn an_imported_file_records_which_hasher_produced_its_audio_md5() {
        let mut c = empty_library();
        let audio_dir =
            std::env::temp_dir().join(format!("lempi-bundle-gen-{}", std::process::id()));
        std::fs::create_dir_all(&audio_dir).unwrap();
        let audio_path = audio_dir.join("a.wav");
        write_tiny_wav(&audio_path);

        // A row that predates the column, written directly rather than through
        // `import` -- the 5,705 incumbent values, in miniature.
        c.execute(
            "INSERT INTO files (file_id,audio_md5,path,size_bytes,mtime,format,duration_ms,\
             first_seen,last_seen) VALUES (99,'older','/gone.mp3',1,1.0,'mp3',1000,'t','t')",
            [],
        )
        .unwrap();

        let md5 = (TEST_HASHER.hash)(&audio_path).unwrap();
        let doc = one_encoding_bundle(&md5, "m1", 0.5, "computed:x@1");
        let rep = import(&mut c, &doc, "body", &audio_dir, true, Some(TEST_HASHER)).unwrap();
        assert!(rep.refused.is_empty(), "{:?}", rep.refused);

        let stored: Option<String> = c
            .query_row("SELECT md5_generator FROM files WHERE audio_md5 = ?1", params![md5], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(stored.as_deref(), Some(TEST_HASHER.generator),
            "the importer must record the hasher it actually used");

        let older: Option<String> = c
            .query_row("SELECT md5_generator FROM files WHERE audio_md5 = 'older'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(older, None,
            "a row written before the column must stay NULL -- no back-annotation");

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
        let md5 = (TEST_HASHER.hash)(&audio_path).unwrap();

        let first = one_encoding_bundle(&md5, "m2", 0.5, "computed:x@1");
        import(&mut c, &first, "body1", &audio_dir, true, Some(TEST_HASHER)).unwrap();
        // A listener corrects it locally, same as `upsert_recording`'s own
        // fresh-import test would exercise on the first pass.
        c.execute(
            "UPDATE flavor SET value=1.0, source='manual' \
             WHERE subject_id='m2' AND characteristic='mood_happy'",
            [],
        )
        .unwrap();

        let second = one_encoding_bundle(&md5, "m2", 0.1, "computed:x@2");
        import(&mut c, &second, "body2", &audio_dir, true, Some(TEST_HASHER)).unwrap();

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
        // Three levels, not two: this file is `player/core/src/`, and the
        // fixture Vipunen checks the same way is at the repository root.
        let text = include_str!("../../../fixtures/payload/09-fade-fields.json");
        let d: Value = serde_json::from_str(text).unwrap();
        assert_eq!(unacceptable(&d), Vec::<String>::new());
        let p = &d["encodings"][0]["passages"][0];
        assert_eq!(fade_ms(p, "fade_in_ms"), 15);
        assert_eq!(fade_ms(p, "fade_out_ms"), 1200);
        assert_eq!(fade_curve(p, "fade_in_curve"), "linear");
        assert_eq!(fade_curve(p, "fade_out_curve"), "cosine");
    }

    /// `one_encoding_bundle` with a byte hash on its encoding `[SPEC-PL-087]`.
    fn with_sha256(mut d: Value, sha256: &str) -> Value {
        d["encodings"][0]["sha256"] = Value::String(sha256.into());
        d
    }

    /// A fresh directory holding the tiny WAV, and the hash of its bytes.
    fn one_file(tag: &str) -> (PathBuf, String) {
        let dir = std::env::temp_dir().join(format!("lempi-bundle-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        write_tiny_wav(&dir.join("a.wav"));
        let sha = sha256_file(&dir.join("a.wav")).unwrap();
        (dir, sha)
    }

    /// FIPS 180-2's own example, so the hex form is proven and not merely
    /// self-consistent: a hasher agreeing with itself would pass every other
    /// test here.
    #[test]
    fn the_byte_hash_is_standard_sha256() {
        let dir = std::env::temp_dir().join(format!("lempi-sha-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("abc"), b"abc").unwrap();
        assert_eq!(sha256_file(&dir.join("abc")).unwrap(),
                   "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
        std::fs::remove_dir_all(&dir).ok();
    }

    /// The phone's case `[REQ-AND-230]`: no hasher, so the byte hash is the
    /// check, and a matching file lands with `audio_md5` as sent and no
    /// hasher claimed for it.
    #[test]
    fn without_a_hasher_a_matching_byte_hash_is_enough() {
        let mut c = empty_library();
        let (dir, sha) = one_file("phone");
        let doc = with_sha256(one_encoding_bundle("sent-md5", "m1", 0.5, "computed:x@1"), &sha);
        let rep = import(&mut c, &doc, "body", &dir, true, None).unwrap();
        assert_eq!(rep.outcomes, vec![("sent-md5".to_string(), Landed::Imported)]);
        let gen: Option<String> = c
            .query_row("SELECT md5_generator FROM files WHERE audio_md5='sent-md5'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(gen, None, "nothing here hashed audio_md5, so no hasher may be recorded");
        // Kept, so a phone can recognise the file by its bytes again
        // `[REQ-AND-260]` -- in a column the import added to a table that
        // predated it.
        let stored: Option<String> = c
            .query_row("SELECT sha256 FROM files WHERE audio_md5='sent-md5'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(stored.as_deref(), Some(sha.as_str()));
        std::fs::remove_dir_all(&dir).ok();
    }

    /// Bytes that disagree are corrupt on every host, hasher or not -- and
    /// nothing is written for them.
    #[test]
    fn a_byte_hash_that_disagrees_is_corrupt() {
        let mut c = empty_library();
        let (dir, sha) = one_file("wrong");
        let wrong = format!("{}0", &sha[..63]);
        let wrong = if wrong == sha { format!("{}1", &sha[..63]) } else { wrong };
        let doc = with_sha256(one_encoding_bundle("sent-md5", "m1", 0.5, "computed:x@1"), &wrong);
        let rep = import(&mut c, &doc, "body", &dir, true, None).unwrap();
        assert_eq!(rep.outcomes, vec![("sent-md5".to_string(), Landed::Corrupt {
            expected: format!("sha256 {wrong}"),
            found: format!("sha256 {sha}"),
        })]);
        let n: i64 = c.query_row("SELECT count(*) FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 0);
        std::fs::remove_dir_all(&dir).ok();
    }

    /// Neither hash available: reported, not written, and not called corrupt
    /// -- nothing disagreed; nothing could be asked.
    #[test]
    fn a_file_nothing_here_can_check_is_not_trusted() {
        let mut c = empty_library();
        let (dir, _) = one_file("neither");
        let doc = one_encoding_bundle("sent-md5", "m1", 0.5, "computed:x@1");
        let rep = import(&mut c, &doc, "body", &dir, true, None).unwrap();
        assert!(matches!(rep.outcomes.as_slice(), [(_, Landed::Unverifiable { .. })]),
                "{:?}", rep.outcomes);
        let n: i64 = c.query_row("SELECT count(*) FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 0);
        std::fs::remove_dir_all(&dir).ok();
    }

    /// Where there is a hasher, both are asked: matching bytes do not excuse an
    /// `audio_md5` the file does not have.
    #[test]
    fn with_a_hasher_audio_md5_is_still_checked_after_the_bytes() {
        let mut c = empty_library();
        let (dir, sha) = one_file("both");
        let doc = with_sha256(one_encoding_bundle("not-its-md5", "m1", 0.5, "computed:x@1"), &sha);
        let rep = import(&mut c, &doc, "body", &dir, true, Some(TEST_HASHER)).unwrap();
        assert!(matches!(rep.outcomes.as_slice(), [(_, Landed::Corrupt { .. })]),
                "{:?}", rep.outcomes);
        std::fs::remove_dir_all(&dir).ok();
    }

    /// `[SPEC-PL-097]`, `[SPEC-RLK-155]`: a file held under a retired key is
    /// re-keyed wherever that key keys a row, its passages untouched; a key
    /// held in both forms is left alone; a key not held is ignored; the pair
    /// is recorded; and a second import finds nothing left to do. A dry run
    /// writes nothing.
    #[test]
    fn a_retired_key_is_rewritten_wherever_it_keys_a_row() {
        let library = || {
            let c = empty_library();
            c.execute_batch(
                "CREATE TABLE lowlevel_cache (audio_md5 TEXT NOT NULL, start_ms INTEGER NOT NULL,
                     end_ms INTEGER NOT NULL, data TEXT, PRIMARY KEY (audio_md5, start_ms, end_ms));
                 INSERT INTO files (file_id,audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen)
                     VALUES (7,'old-key','/m/a.mp3',1,1.0,'mp3',1000,'t','t'),
                            (8,'both-old','/m/b.mp3',1,1.0,'mp3',1000,'t','t'),
                            (9,'both-new','/m/c.mp3',1,1.0,'mp3',1000,'t','t');
                 INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (7,'radio',0,1000,'x');
                 INSERT INTO lowlevel_cache VALUES ('old-key',0,1000,'d');",
            )
            .unwrap();
            c
        };
        let mut doc = one_encoding_bundle("other-md5", "m1", 0.5, "computed:x@1");
        doc["aliases"] = serde_json::json!([
            {"old": "old-key", "new": "new-key", "generator": "symphonia@0.5.5"},
            {"old": "both-old", "new": "both-new", "generator": "symphonia@0.5.5"},
            {"old": "not-held", "new": "whatever", "generator": "symphonia@0.5.5"},
        ]);
        let dir = std::env::temp_dir().join(format!("lempi-aliases-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();

        let mut dry = library();
        assert_eq!(import(&mut dry, &doc, "body", &dir, false, None).unwrap().rekeyed, 0);
        let k: String = dry.query_row("SELECT audio_md5 FROM files WHERE file_id = 7", [], |r| r.get(0)).unwrap();
        assert_eq!(k, "old-key", "a dry run writes nothing");

        let mut c = library();
        assert_eq!(import(&mut c, &doc, "body", &dir, true, None).unwrap().rekeyed, 1);
        let keys: Vec<(i64, String)> = c
            .prepare("SELECT file_id, audio_md5 FROM files ORDER BY file_id")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(keys, vec![(7, "new-key".into()), (8, "both-old".into()), (9, "both-new".into())]);
        let cache: Vec<String> = c
            .prepare("SELECT audio_md5 FROM lowlevel_cache")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(cache, vec!["new-key".to_string()], "the cache follows its file");
        let p: i64 = c.query_row("SELECT count(*) FROM passages WHERE file_id = 7", [], |r| r.get(0)).unwrap();
        assert_eq!(p, 1, "rows keyed by file id are not touched");
        let recorded: Vec<(String, String, String)> = c
            .prepare("SELECT old_md5, new_md5, generator FROM audio_md5_aliases")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(recorded, vec![("old-key".into(), "new-key".into(), "symphonia@0.5.5".into())]);
        assert_eq!(import(&mut c, &doc, "body", &dir, true, None).unwrap().rekeyed, 0, "idempotent");
        std::fs::remove_dir_all(&dir).ok();
    }

    /// `one_encoding_bundle` with words on its recording `[SPEC-LYR-025]`.
    fn with_lyrics(mut d: Value, text: &str, source: &str, at: &str) -> Value {
        d["recordings"][0]["lyrics"] =
            serde_json::json!({"text": text, "source": source, "fetched_at": at});
        d
    }

    fn words(c: &Connection, mbid: &str) -> Option<(String, String)> {
        c.query_row("SELECT text, source FROM lyrics WHERE mbid = ?1", params![mbid], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .ok()
    }

    /// Lyrics reach the receiver's own database `[REQ-AND-202]`; a newer
    /// fetch replaces them, an older or equal one does not, and a `manual`
    /// text is never replaced `[SPEC-DF-070]`.
    #[test]
    fn lyrics_arrive_with_their_recording_and_rank_by_provenance_then_recency() {
        let mut c = empty_library();
        let (dir, sha) = one_file("lyrics");
        let base = || with_sha256(one_encoding_bundle("md5-l", "m1", 0.5, "computed:x@1"), &sha);

        let first = with_lyrics(base(), "old words", "mulibplay", "2026-09-01T00:00:00+00:00");
        import(&mut c, &first, "b", &dir, true, None).unwrap();
        assert_eq!(words(&c, "m1"), Some(("old words".into(), "mulibplay".into())));

        let stale = with_lyrics(base(), "stale", "mulibplay", "2026-08-01T00:00:00+00:00");
        import(&mut c, &stale, "b", &dir, true, None).unwrap();
        assert_eq!(words(&c, "m1").unwrap().0, "old words", "an older fetch is not news");

        let newer = with_lyrics(base(), "new words", "mulibplay", "2026-09-20T00:00:00+00:00");
        import(&mut c, &newer, "b", &dir, true, None).unwrap();
        assert_eq!(words(&c, "m1").unwrap().0, "new words", "a corrected source must arrive");

        c.execute("UPDATE lyrics SET source = 'manual' WHERE mbid = 'm1'", []).unwrap();
        let later = with_lyrics(base(), "overwrite?", "mulibplay", "2026-09-24T00:00:00+00:00");
        let rep = import(&mut c, &later, "b", &dir, true, None).unwrap();
        assert_eq!(words(&c, "m1"), Some(("new words".into(), "manual".into())));
        assert_eq!(rep.kept_local, 1);
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn partial_lyrics_are_refused() {
        let mut d = with_lyrics(one_encoding_bundle("a", "m1", 0.5, "s"), "w", "s", "t");
        d["recordings"][0]["lyrics"].as_object_mut().unwrap().remove("fetched_at");
        assert_eq!(unacceptable(&d), vec!["m1: missing lyrics.fetched_at"]);
    }

    /// Present and unusable is refused whole, like a bad duration.
    #[test]
    fn a_malformed_byte_hash_is_refused() {
        for bad in ["\"abc\"", "12", &format!("\"{}\"", "A".repeat(64))] {
            let d = doc(&format!(
                r#"{{"encodings":[
                {{"audio_md5":"a","bundle_path":"a.mp3","format":"mp3","duration_ms":10,"sha256":{bad},
                 "passages":[{{"kind":"radio","start_ms":0,"end_ms":10,"boundary_src":"x"}}]}}]}}"#
            ));
            assert_eq!(unacceptable(&d), vec!["a: encoding.sha256 is not 64 lower-case hex digits"],
                       "{bad}");
        }
    }

    // ---------------------------------------------------- staged import ---

    /// A bundle staged as the phone unpacks one: `payload.json`, and each
    /// file's bytes under `audio/`. Each entry is (audio_md5, bundle_path,
    /// the bytes staged, the sha256 the payload claims -- None for none).
    struct Staged {
        root: PathBuf,
        staging: PathBuf,
        music: PathBuf,
    }

    /// One staged entry: (audio_md5, bundle_path, the bytes staged, the
    /// sha256 the payload claims).
    type Entry<'a> = (&'a str, &'a str, Option<&'a [u8]>, Option<String>);

    fn stage(name: &str, entries: &[Entry]) -> Staged {
        let root = std::env::temp_dir().join(format!("lempi-staged-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let (staging, music) = (root.join("staging"), root.join("Music"));
        std::fs::create_dir_all(staging.join("audio")).unwrap();
        std::fs::create_dir_all(&music).unwrap();
        let mut encs = Vec::new();
        for (i, (md5, rel, bytes, sha)) in entries.iter().enumerate() {
            if let Some(b) = bytes {
                let p = staging.join("audio").join(rel);
                if let Some(d) = p.parent() {
                    let _ = std::fs::create_dir_all(d);
                }
                std::fs::write(&p, b).unwrap();
            }
            let sha = sha.as_ref().map(|h| format!(r#","sha256":"{h}""#)).unwrap_or_default();
            encs.push(format!(
                r#"{{"audio_md5":"{md5}","bundle_path":"{rel}","format":"wav","duration_ms":1000{sha},
                 "passages":[{{"kind":"radio","start_ms":0,"end_ms":1000,"boundary_src":"x",
                              "recordings":[{{"mbid":"m{i}","weight":1.0,"source":"s"}}]}}]}}"#
            ));
        }
        let recs: Vec<String> =
            (0..entries.len()).map(|i| format!(r#"{{"mbid":"m{i}","title":"t{i}","source":"s"}}"#)).collect();
        std::fs::write(
            staging.join("payload.json"),
            format!(r#"{{"payload_version":1,"encodings":[{}],"recordings":[{}]}}"#, encs.join(","), recs.join(",")),
        )
        .unwrap();
        Staged { root, staging, music }
    }

    fn sha_of(b: &[u8]) -> String {
        use sha2::Digest;
        sha2::Sha256::digest(b).iter().map(|x| format!("{x:02x}")).collect()
    }

    /// `[REQ-AND-230]`: a verified file is placed in the music folder and
    /// bound there; a damaged one never reaches it.
    #[test]
    fn staged_import_places_what_verifies_and_nothing_else() {
        let good: &[u8] = b"the bytes as sent";
        let s = stage("verify", &[
            ("md5good", "A/good.wav", Some(good), Some(sha_of(good))),
            ("md5bad", "A/bad.wav", Some(b"damaged in transit"), Some(sha_of(b"the bytes as sent, originally"))),
            ("md5gone", "A/gone.wav", None, Some(sha_of(b"never arrived"))),
            ("md5nohash", "A/nohash.wav", Some(b"unhashed"), None),
        ]);
        let mut c = empty_library();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert!(r.refused.is_empty(), "{:?}", r.refused);
        assert_eq!(r.imported, 1, "{r:?}");
        assert_eq!(r.corrupt, vec!["A/bad.wav"]);
        assert_eq!(r.missing, vec!["A/gone.wav"]);
        assert_eq!(r.unverifiable, vec!["A/nohash.wav"]);
        assert!(s.music.join("A/good.wav").is_file(), "the verified file is placed");
        assert!(!s.music.join("A/bad.wav").exists(), "a damaged file never reaches shared storage");
        assert!(!s.music.join("A/nohash.wav").exists(), "nor does one that cannot be checked");
        let path: String = c.query_row("SELECT path FROM files WHERE audio_md5='md5good'", [], |r| r.get(0)).unwrap();
        assert_eq!(PathBuf::from(path), s.music.join("A/good.wav"), "and it is bound where it was placed");
        let rows: i64 = c.query_row("SELECT count(*) FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(rows, 1, "nothing else was written");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// `[REQ-AND-240]`: what the library holds is not copied; a byte-identical
    /// file already in place is bound; a different file there is left alone.
    #[test]
    fn staged_import_never_duplicates_or_overwrites() {
        let a: &[u8] = b"already catalogued";
        let b: &[u8] = b"already on the phone";
        let d: &[u8] = b"arriving";
        let s = stage("dup", &[
            ("md5held", "held.wav", Some(a), Some(sha_of(a))),
            ("md5same", "same.wav", Some(b), Some(sha_of(b))),
            ("md5clash", "clash.wav", Some(d), Some(sha_of(d))),
        ]);
        let mut c = empty_library();
        c.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen) \
             VALUES ('md5held','/elsewhere/held.wav',1,1.0,'wav',1000,'t','t')",
            [],
        )
        .unwrap();
        std::fs::write(s.music.join("same.wav"), b).unwrap();
        std::fs::write(s.music.join("clash.wav"), b"a file the app did not write").unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!(r.already, 1, "{r:?}");
        assert!(!s.music.join("held.wav").exists(), "a held encoding is not copied again");
        assert_eq!(r.reused, 1, "{r:?}");
        assert_eq!(r.imported, 0, "{r:?}");
        let bound: i64 =
            c.query_row("SELECT count(*) FROM files WHERE audio_md5='md5same'", [], |r| r.get(0)).unwrap();
        assert_eq!(bound, 1, "the identical file already there is bound, not copied beside itself");
        assert_eq!(r.conflicts, vec!["clash.wav"]);
        assert_eq!(
            std::fs::read(s.music.join("clash.wav")).unwrap(),
            b"a file the app did not write",
            "a different file is never overwritten"
        );
        let clash: i64 =
            c.query_row("SELECT count(*) FROM files WHERE audio_md5='md5clash'", [], |r| r.get(0)).unwrap();
        assert_eq!(clash, 0);
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// A bundle comes from outside the app: a path may not leave the music
    /// folder, and a refused payload places nothing.
    #[test]
    fn staged_import_keeps_to_the_music_folder_and_refuses_whole() {
        let x: &[u8] = b"escape";
        let s = stage("unsafe", &[("md5esc", "../outside.wav", Some(x), Some(sha_of(x)))]);
        let mut c = empty_library();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!(r.unsafe_paths, vec!["../outside.wav"]);
        assert!(!s.root.join("outside.wav").exists(), "nothing written outside the music folder");

        std::fs::write(
            s.staging.join("payload.json"),
            r#"{"payload_version":1,"encodings":[
            {"audio_md5":"a","bundle_path":"a.wav","format":"wav","duration_ms":1000,
             "passages":[{"kind":"radio","start_ms":0,"end_ms":1000,
                          "recordings":[{"mbid":"m","weight":1.0,"source":"s"}]}]}],
            "recordings":[{"mbid":"m","title":"t","source":"s"}]}"#,
        )
        .unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert!(r.refused.iter().any(|x| x.contains("boundary_src")), "{r:?}");
        assert_eq!(std::fs::read_dir(&s.music).unwrap().count(), 0, "a refused payload places nothing");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// `[REQ-AND-250]`: the same audio with rewritten bytes replaces the
    /// phone's copy where it lies; the same bytes are only held already; a
    /// damaged rewrite leaves the phone's copy alone.
    #[test]
    fn staged_import_replaces_a_rewritten_file_in_place() {
        let old: &[u8] = b"audio, with the tags as first sent";
        let new: &[u8] = b"audio, with the tags Vipunen wrote back";
        let s = stage("rewrite", &[
            ("md5re", "re.wav", Some(new), Some(sha_of(new))),
            ("md5same", "same.wav", Some(old), Some(sha_of(old))),
            ("md5dmg", "dmg.wav", Some(b"damaged rewrite"), Some(sha_of(new))),
        ]);
        let mut c = empty_library();
        for (md5, name) in [("md5re", "re.wav"), ("md5same", "same.wav"), ("md5dmg", "dmg.wav")] {
            let held = s.music.join("held").join(name);
            std::fs::create_dir_all(held.parent().unwrap()).unwrap();
            std::fs::write(&held, old).unwrap();
            c.execute(
                "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen) \
                 VALUES (?1,?2,1,1.0,'wav',1000,'t','t')",
                params![md5, held.to_string_lossy()],
            )
            .unwrap();
        }
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!(r.replaced, 1, "{r:?}");
        assert_eq!(r.already, 1, "{r:?}");
        assert_eq!(r.corrupt, vec!["dmg.wav"], "{r:?}");
        assert!(r.not_replaced.is_empty(), "{r:?}");
        assert_eq!(std::fs::read(s.music.join("held/re.wav")).unwrap(), new, "replaced where it lies");
        assert!(!s.music.join("re.wav").exists(), "and not kept beside the new copy");
        assert_eq!(std::fs::read(s.music.join("held/same.wav")).unwrap(), old, "the same bytes are left alone");
        assert_eq!(std::fs::read(s.music.join("held/dmg.wav")).unwrap(), old, "a damaged rewrite replaces nothing");
        let (sha, size): (String, i64) = c
            .query_row("SELECT sha256, size_bytes FROM files WHERE audio_md5='md5re'", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .unwrap();
        assert_eq!((sha, size), (sha_of(new), new.len() as i64), "the catalogue records the new bytes");
        let leftovers: Vec<_> = std::fs::read_dir(s.music.join("held"))
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().contains("lempi-part"))
            .collect();
        assert!(leftovers.is_empty(), "no temporary file is left behind");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// `[REQ-AND-260]`: a file the phone found for itself, held as tags-only,
    /// is made whole where it lies when a bundle names its bytes -- not
    /// copied into the music folder beside itself.
    #[test]
    fn staged_import_makes_a_found_file_whole_where_it_lies() {
        let bytes: &[u8] = b"a song the phone found in Downloads";
        let s = stage("upgrade", &[("realmd5", "Artist/song.wav", Some(bytes), Some(sha_of(bytes)))]);
        let found = s.root.join("Download").join("song.wav");
        std::fs::create_dir_all(found.parent().unwrap()).unwrap();
        std::fs::write(&found, bytes).unwrap();
        let mut c = empty_library();
        crate::db::ensure_sha256_column(&c);
        c.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
             VALUES (?1,?2,1,1.0,'wav',1000,'t','t',?3)",
            params![format!("sha256:{}", sha_of(bytes)), found.to_string_lossy(), sha_of(bytes)],
        )
        .unwrap();
        c.execute(
            "INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (1,'radio',0,1000,'found:tags')",
            [],
        )
        .unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!(r.upgraded, 1, "{r:?}");
        assert_eq!(r.imported, 0, "{r:?}");
        assert!(!s.music.join("Artist/song.wav").exists(), "not copied beside itself");
        let (md5, path): (String, String) =
            c.query_row("SELECT audio_md5, path FROM files", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!(md5, "realmd5", "it takes Vipunen's identity");
        assert_eq!(PathBuf::from(path), found, "bound where it was found");
        let srcs: Vec<String> = c
            .prepare("SELECT boundary_src FROM passages")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(srcs, vec!["x"], "the tags-only passage gives way to Vipunen's");
        let credited: i64 = c.query_row("SELECT count(*) FROM passage_recordings", [], |r| r.get(0)).unwrap();
        assert_eq!(credited, 1, "and it now has a recording");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// `[SPEC-PL-095]`: a payload in parts is imported part by part; one
    /// unacceptable part refuses the whole bundle, before anything is written.
    #[test]
    fn staged_import_takes_a_payload_in_parts_and_refuses_it_whole() {
        let a: &[u8] = b"first part's song";
        let b: &[u8] = b"second part's song";
        let s = stage("parts", &[("md5a", "a.wav", Some(a), Some(sha_of(a)))]);
        let one = std::fs::read_to_string(s.staging.join("payload.json")).unwrap();
        std::fs::remove_file(s.staging.join("payload.json")).unwrap();
        std::fs::write(s.staging.join("payload-001.json"), &one).unwrap();
        std::fs::write(s.staging.join("audio/b.wav"), b).unwrap();
        let two = one.replace("md5a", "md5b").replace("a.wav", "b.wav").replace(&sha_of(a), &sha_of(b));
        std::fs::write(s.staging.join("payload-002.json"), &two).unwrap();

        // A broken second part: nothing from the first may land either.
        let broken = two.replace(r#""boundary_src":"x","#, "");
        std::fs::write(s.staging.join("payload-002.json"), &broken).unwrap();
        let mut c = empty_library();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert!(r.refused.iter().any(|x| x.starts_with("payload-002.json: ")), "{r:?}");
        let n: i64 = c.query_row("SELECT count(*) FROM files", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 0, "refused whole: the good first part wrote nothing");
        assert!(!s.music.join("a.wav").exists());

        std::fs::write(s.staging.join("payload-002.json"), &two).unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert!(r.refused.is_empty(), "{r:?}");
        assert_eq!(r.imported, 2, "both parts land: {r:?}");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// A phone file found by its signature -- the same audio in bytes of its
    /// own, a re-tagged copy -- sets up its tags-only row as the scan would.
    fn found_by_audio(c: &Connection, path: &Path, md5: &str, bytes: &[u8]) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, bytes).unwrap();
        let meta = std::fs::metadata(path).unwrap();
        let mtime = meta.modified().unwrap().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
        crate::db::ensure_sha256_column(c);
        c.execute(
            "INSERT INTO files (file_id,audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256)              VALUES (1,?1,?2,?3,?4,'mp3',1000,'t','t',?5)",
            params![md5, path.to_string_lossy(), meta.len() as i64, mtime, sha_of(bytes)],
        )
        .unwrap();
        c.execute("INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (1,'radio',0,1000,'found:tags')", [])
            .unwrap();
    }

    /// `[REQ-AND-260]` by audio: the bundle names the phone's tags-only file
    /// by its signature although the bytes differ. It is made whole where it
    /// lies, and keeps the byte hash of the bytes it has, not the sender's.
    #[test]
    fn a_re_tagged_copy_found_by_signature_is_made_whole_where_it_lies() {
        let vipunen: &[u8] = b"vipunen's copy, its own tags";
        let phone: &[u8] = b"the phone's copy, re-tagged, same audio";
        let s = stage("by-audio", &[("the-signature", "song.mp3", None, Some(sha_of(vipunen)))]);
        let found = s.root.join("Found").join("song.mp3");
        let mut c = empty_library();
        found_by_audio(&c, &found, "the-signature", phone);
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!(r.upgraded, 1, "{r:?}");
        let (path, sha): (String, String) = c
            .query_row("SELECT path, sha256 FROM files WHERE audio_md5 = 'the-signature'", [], |r| {
                Ok((r.get(0)?, r.get(1)?))
            })
            .unwrap();
        assert_eq!(path, found.to_string_lossy(), "bound where it lies, not copied");
        assert_eq!(sha, sha_of(phone), "the byte hash of the bytes it has");
        let src: Vec<String> = c
            .prepare("SELECT boundary_src FROM passages")
            .unwrap()
            .query_map([], |r| r.get(0))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(src, vec!["x".to_string()], "Vipunen's passage, and no tags-only one");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// The same, where the file changed after the scan identified it: asked
    /// again, and left alone when it is no longer that audio.
    #[test]
    fn a_found_file_that_is_no_longer_its_audio_is_left_alone() {
        let s = stage("no-longer", &[("the-signature", "song.mp3", None, Some(sha_of(b"vipunen")))]);
        let found = s.root.join("Found").join("song.mp3");
        let mut c = empty_library();
        found_by_audio(&c, &found, "the-signature", b"what the scan saw");
        std::fs::write(&found, b"edited since, into something else entirely").unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, Some(TEST_HASHER)).unwrap();
        assert_eq!((r.upgraded, r.changed_since_scan.len()), (0, 1), "{r:?}");
        let tags_only: i64 = c
            .query_row("SELECT count(*) FROM passages WHERE boundary_src = 'found:tags'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(tags_only, 1, "its tags-only entry is left as it was");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// `[SPEC-PL-098]`: a phone copy the host has since written with the
    /// system's leave holds Vipunen's bytes while its row still records the
    /// old ones. The import finds it held, and the row follows the file.
    #[test]
    fn a_copy_already_rewritten_brings_its_row_up_to_date() {
        let new: &[u8] = b"vipunen's rewritten file";
        let s = stage("follows", &[("md5-1", "a.mp3", Some(new), Some(sha_of(new)))]);
        let held = s.music.join("a.mp3");
        std::fs::write(&held, new).unwrap();
        let mut c = empty_library();
        crate::db::ensure_sha256_column(&c);
        c.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
             VALUES ('md5-1',?1,3,1.0,'mp3',1000,'t','t',?2)",
            params![held.to_string_lossy(), sha_of(b"old")],
        )
        .unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!((r.already, r.replaced, r.refused_replacements.len()), (1, 0, 0), "{r:?}");
        let (sha, size): (String, i64) = c
            .query_row("SELECT sha256, size_bytes FROM files WHERE audio_md5='md5-1'", [], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap();
        assert_eq!((sha, size), (sha_of(new), new.len() as i64));
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// `[SPEC-PL-105]`: releases and their covers land in the catalogue from
    /// a staged bundle -- into a phone's older release tables, whose missing
    /// columns are added. A cover whose bytes do not match is refused; a track
    /// links only a recording the catalogue holds; a second import changes
    /// nothing.
    #[test]
    fn releases_and_verified_covers_land_in_the_catalogue() {
        let audio: &[u8] = b"some audio";
        let s = stage("covers", &[("md5-1", "a.mp3", Some(audio), Some(sha_of(audio)))]);
        let front: Vec<u8> = [&[0xFF, 0xD8, 0xFF][..], &[b'j'; 600][..]].concat();
        std::fs::create_dir_all(s.staging.join("covers")).unwrap();
        std::fs::write(s.staging.join("covers").join("r1-front.jpg"), &front).unwrap();
        std::fs::write(s.staging.join("covers").join("r2-front.jpg"), b"tampered in transit").unwrap();
        let path = s.staging.join("payload.json");
        let mut doc: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
        doc["releases"] = serde_json::json!([
            {"mbid": "r1", "title": "One", "source": "mb", "status": "Official",
             "tracks": [{"recording": "m0", "position": 4, "disc": 1, "chosen": 1, "source": "mb"},
                        {"recording": "not-held", "position": 5, "disc": 1, "chosen": 1, "source": "mb"}],
             "cover": {"source": "caa", "fetched_at": "t",
                       "front": {"file": "covers/r1-front.jpg", "sha256": sha_of(&front)}}},
            {"mbid": "r2", "title": "Two", "source": "mb", "tracks": [],
             "cover": {"source": "caa", "fetched_at": "t",
                       "front": {"file": "covers/r2-front.jpg", "sha256": sha_of(b"the real picture")}}},
        ]);
        std::fs::write(&path, doc.to_string()).unwrap();
        let mut c = empty_library();
        // The phone's shape: four columns, and no `chosen`.
        c.execute_batch(
            "CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT NOT NULL, release_date TEXT, source TEXT NOT NULL);
             CREATE TABLE release_recordings (release_mbid TEXT NOT NULL, mbid TEXT NOT NULL, position INTEGER,
                 source TEXT NOT NULL, PRIMARY KEY (release_mbid, mbid)) WITHOUT ROWID;",
        )
        .unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!((r.imported, r.releases, r.covers), (1, 2, 1), "{r:?}");
        assert_eq!(r.bad_covers, vec!["covers/r2-front.jpg".to_string()]);
        let status: String = c.query_row("SELECT status FROM releases WHERE mbid='r1'", [], |r| r.get(0)).unwrap();
        assert_eq!(status, "Official", "a column the old table lacked, added and filled");
        let tracks: Vec<(String, i64)> = c
            .prepare("SELECT mbid, chosen FROM release_recordings")
            .unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
            .unwrap()
            .map(|x| x.unwrap())
            .collect();
        assert_eq!(tracks, vec![("m0".to_string(), 1)], "linked only to a recording held");
        let stored: Vec<u8> = c.query_row("SELECT front FROM cover_art WHERE release_mbid='r1'", [], |r| r.get(0)).unwrap();
        assert_eq!(stored, front);
        let r2: i64 = c.query_row("SELECT count(*) FROM cover_art WHERE release_mbid='r2'", [], |r| r.get(0)).unwrap();
        assert_eq!(r2, 0, "a cover that does not match is not stored");
        let again = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!(again.imported, 0, "{again:?}");
        let n: i64 = c.query_row("SELECT count(*) FROM release_recordings", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 1, "idempotent");
        std::fs::remove_dir_all(&s.root).ok();
    }

    /// A found file changed since its scan is checked again, and a found
    /// file that no longer holds the bytes the payload names is not made
    /// whole with them.
    #[test]
    fn a_found_file_changed_since_its_scan_is_checked_again() {
        let claimed: &[u8] = b"the bytes the scan saw";
        let s = stage("changed", &[("realmd5", "song.wav", None, Some(sha_of(claimed)))]);
        let found = s.root.join("Found").join("song.wav");
        std::fs::create_dir_all(found.parent().unwrap()).unwrap();
        std::fs::write(&found, b"edited since, and a different length").unwrap();
        let mut c = empty_library();
        crate::db::ensure_sha256_column(&c);
        c.execute(
            "INSERT INTO files (audio_md5,path,size_bytes,mtime,format,duration_ms,first_seen,last_seen,sha256) \
             VALUES (?1,?2,?3,1.0,'wav',1000,'t','t',?4)",
            params![format!("sha256:{}", sha_of(claimed)), found.to_string_lossy(), claimed.len() as i64, sha_of(claimed)],
        )
        .unwrap();
        // As the scan makes it: tags-only is marked by this passage.
        c.execute("INSERT INTO passages (file_id,kind,start_ms,end_ms,boundary_src) VALUES (1,'radio',0,1000,'found:tags')", [])
            .unwrap();
        let r = import_staged(&mut c, &s.staging, &s.music, None).unwrap();
        assert_eq!(r.upgraded, 0, "{r:?}");
        let real: i64 = c.query_row("SELECT count(*) FROM files WHERE audio_md5='realmd5'", [], |r| r.get(0)).unwrap();
        assert_eq!(real, 0, "a file whose bytes changed is not given Vipunen's identity");
        assert_eq!(r.changed_since_scan.len(), 1, "{r:?}");
        let kept: i64 = c
            .query_row("SELECT count(*) FROM files WHERE audio_md5 LIKE 'sha256:%'", [], |r| r.get(0))
            .unwrap();
        assert_eq!(kept, 1, "and its tags-only entry is left as it was");
        std::fs::remove_dir_all(&s.root).ok();
    }
}
