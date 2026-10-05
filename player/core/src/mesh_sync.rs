//! A member's share of the household's listening, up to the hub and back
//! `[SPEC-MTR-030]`, `[REQ-AND-330]`.
//!
//! A phone is not reached over ssh as an appliance is, so it takes part in the
//! star sync `[SPEC046]` by two operations of its own. [`snapshot`] writes the
//! shared tables -- and only those; its plays never leave it `[REQ-PD-113]` --
//! to a small file it uploads over the members' channel `[SPEC-MTR-200]`. The
//! hub merges that as it merges any node's listener. [`apply`] takes back what
//! the merge decided, as a patch of values, with star_patch's three-way rule:
//! a row changes only where the phone still holds what it uploaded.
//!
//! One difference from `star_patch.py`: a row this phone has changed since its
//! upload is *kept*, not a reason to refuse the whole patch. These tables are
//! last-write-wins and union, so the phone's newer value simply goes up with
//! its next upload and is decided then.
//!
//! The patch carries the old and new values themselves, not a digest of them.
//! star_patch's digest is SHA-256 over Python's JSON spelling of a row, which
//! Rust does not reproduce for every float (`1e-06` against `0.000001`).

use std::collections::HashMap;
use std::path::Path;

use rusqlite::types::Value;
use rusqlite::{params, params_from_iter, Connection, OpenFlags, OptionalExtension};
use serde_json::Value as Json;

/// The household's edits a member shares: preferences, occasion values and
/// flags. Plays, programmes and node state are the node's own.
pub const SHARED: [&str; 3] = ["listener_preferences", "listener_characteristics", "listener_flags"];

/// What [`snapshot`] wrote.
#[derive(Debug, Default, PartialEq)]
pub struct Snapshot {
    /// Rows written, all tables together.
    pub rows: usize,
    /// Rows about a *passage*, kept on this node: a passage id is local to
    /// the node that numbered it `[SPEC-STAR-049]`, and meaningless at the hub.
    pub held_back: usize,
}

/// Write the shared tables of `listener` to a new database at `out`, each
/// with the schema it has here. Read through a read-only attachment, so a
/// player writing meanwhile is not disturbed.
pub fn snapshot(listener: &Path, out: &Path) -> Result<Snapshot, String> {
    let _ = std::fs::remove_file(out);
    let c = Connection::open(out).map_err(|e| e.to_string())?;
    let uri = format!("file:{}?mode=ro", listener.to_string_lossy().replace('\\', "/"));
    c.execute("ATTACH DATABASE ?1 AS src", [&uri]).map_err(|e| format!("open {}: {e}", listener.display()))?;
    let mut rep = Snapshot::default();
    for t in SHARED {
        let sql: Option<String> = c
            .query_row("SELECT sql FROM src.sqlite_master WHERE type='table' AND name=?1", [t], |r| r.get(0))
            .ok();
        let Some(sql) = sql else { continue };
        c.execute_batch(&sql).map_err(|e| format!("{t}: {e}"))?;
        let passage = columns(&c, &format!("src.{t}"))?.iter().any(|x| x == "subject_kind");
        let keep = if passage { " WHERE subject_kind IS NOT 'passage'" } else { "" };
        rep.rows += c
            .execute(&format!("INSERT INTO main.{t} SELECT * FROM src.{t}{keep}"), [])
            .map_err(|e| format!("{t}: {e}"))?;
        if passage {
            rep.held_back += c
                .query_row(&format!("SELECT count(*) FROM src.{t} WHERE subject_kind = 'passage'"), [], |r| {
                    r.get::<_, i64>(0)
                })
                .map_err(|e| e.to_string())? as usize;
        }
    }
    c.execute("DETACH DATABASE src", []).map_err(|e| e.to_string())?;
    Ok(rep)
}

/// What [`apply`] did.
#[derive(Debug, Default, PartialEq)]
pub struct Applied {
    /// Rows changed to the merge's value.
    pub applied: usize,
    /// Rows already holding it.
    pub already: usize,
    /// Rows changed here since the upload, and kept as they are.
    pub kept: usize,
}

/// Apply a member patch to `listener` `[SPEC-MTR-030]`:
///
/// ```json
/// {"tables": [{"name": "listener_flags", "columns": [...], "key": [...],
///              "rows": [{"key": [...], "was": [...] | null, "now": [...] | null}]}]}
/// ```
///
/// `was` is the row as uploaded, `now` the merge's; `null` means absent.
/// Only the shared tables are accepted. One transaction: all of it, or none.
pub fn apply(listener: &Path, patch: &str) -> Result<Applied, String> {
    let p: Json = serde_json::from_str(patch).map_err(|e| format!("not a patch: {e}"))?;
    Ok(apply_with(listener, &p, Some(&SHARED), Rule::Member, true)?.counts)
}

// ---------------------------------------------------------------------
// An appliance's share, over the signed transport `[SPEC-NSH-050..080]`.
// ---------------------------------------------------------------------

/// The listener tables the hub's merge shares: its last-write-wins and union
/// tables, `TABLES` in `tools/star_merge.py`, whose test holds the two lists
/// equal. Never the ones it keeps per node or takes from the hub -- plays,
/// programmes, node state `[SPEC-NSH-050]`. A phone shares [`SHARED`], three of
/// these; an appliance shares all it holds, as the ssh path carried them.
pub const MERGED: [&str; 10] = [
    "artist_reviews", "boundary_reviews", "id_reviews", "listener_characteristics", "listener_flags",
    "listener_likes", "listener_occasion_points", "listener_occasions", "listener_preferences",
    "listener_settings",
];

/// Each node's own columns of the catalogue, never merged `[SPEC-DF-030]`:
/// `MACHINE_SCOPE` in `tools/star_merge.py`. `first_seen` is when *this*
/// installation first saw the file, and joined them with `[SPEC-NKP-050]`. They travel in neither direction
/// `[SPEC-NSH-070]`; the summary reports them so the hub keeps them as they are.
pub const MACHINE_SCOPE: [&str; 5] = ["path", "size_bytes", "mtime", "last_seen", "first_seen"];

/// What a row that no longer holds the patch's `was` means `[SPEC-NSH-070]`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Rule {
    /// Kept as it is, and decided at the next sync: the shared tables, which
    /// are last-write-wins and union.
    Member,
    /// The whole patch refused and nothing written: the catalogue, which is
    /// authored at the hub `[SPEC-STAR-080]`.
    Strict,
}

/// A patch applied or rehearsed by [`apply_with`].
#[derive(Debug, Default)]
pub struct Outcome {
    pub counts: Applied,
    /// The rows written, reversed. Given to [`undo`] against the same file it
    /// puts back exactly what the patch changed: under either rule a row is
    /// written only where it held `was`, so `was` *is* the backup
    /// `[SPEC-NSH-080]`. Empty tables are left out.
    pub inverse: Json,
}

/// Apply `patch` to `db` under `rule`, or with `commit` false rehearse it:
/// the same checks and counts, and nothing kept. One transaction either way.
///
/// The patch is [`apply`]'s shape, with three additions a catalogue patch
/// needs and a member patch never carries: `create` (the statements that make
/// a table this file lacks), `add_columns` (declarations of columns it lacks),
/// and per row `also` -- values for columns outside the patch's own, used
/// only when the row is new here. `allowed`, when given, is the only tables
/// the patch may name.
pub fn apply_with(db: &Path, patch: &Json, allowed: Option<&[&str]>, rule: Rule, commit: bool) -> Result<Outcome, String> {
    let mut c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_WRITE)
        .map_err(|e| format!("open {}: {e}", db.display()))?;
    c.busy_timeout(std::time::Duration::from_secs(10)).map_err(|e| e.to_string())?;
    let tx = c.transaction().map_err(|e| e.to_string())?;
    let out = apply_tx(&tx, patch, allowed, rule)?;
    if commit {
        tx.commit().map_err(|e| e.to_string())?;
    }
    Ok(out)
}

/// Put back what an [`Outcome::inverse`] records, in one transaction: its rows
/// under the strict rule -- each must still hold what the patch wrote, or
/// nothing is undone -- then the tables and columns the patch added, dropped.
pub fn undo(db: &Path, inverse: &Json) -> Result<Applied, String> {
    undo_parts(db, &mut std::iter::once(Ok(inverse.clone())))
}

/// `[SPEC-NKP-940]`: the inverses of a patch sent in parts, each put back in the
/// order given -- the last part's first -- all in one transaction, so the undo
/// is whole or not at all, as the commit it reverses was.
pub fn undo_parts(db: &Path, inverses: &mut dyn Iterator<Item = Result<Json, String>>) -> Result<Applied, String> {
    let mut c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_WRITE)
        .map_err(|e| format!("open {}: {e}", db.display()))?;
    c.busy_timeout(std::time::Duration::from_secs(10)).map_err(|e| e.to_string())?;
    let tx = c.transaction().map_err(|e| e.to_string())?;
    let mut total = Applied::default();
    for inverse in inverses {
        let counts = undo_tx(&tx, &inverse?)?;
        total.applied += counts.applied;
        total.already += counts.already;
        total.kept += counts.kept;
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(total)
}

fn undo_tx(tx: &Connection, inverse: &Json) -> Result<Applied, String> {
    let entries = inverse["tables"].as_array().ok_or("an inverse has tables")?;
    let rows: Vec<&Json> = entries.iter().filter(|t| t["drop_table"] != true).collect();
    let counts = apply_tx(tx, &serde_json::json!({ "tables": rows }), None, Rule::Strict)?.counts;
    for t in entries {
        let name = ident(t["name"].as_str().unwrap_or(""))?;
        if t["drop_table"] == true {
            tx.execute_batch(&format!("DROP TABLE {name}")).map_err(|e| format!("{name}: {e}"))?;
            continue;
        }
        for col in t["drop_columns"].as_array().into_iter().flatten() {
            let col = ident(col.as_str().unwrap_or(""))?;
            tx.execute_batch(&format!("ALTER TABLE {name} DROP COLUMN {col}")).map_err(|e| format!("{name}.{col}: {e}"))?;
        }
    }
    Ok(counts)
}

/// `[SPEC-NKP-920]`: a patch sent in parts, applied in order in **one**
/// transaction, so one row not as expected writes nothing from any part. Only
/// one part is held in memory at a time: `parts` yields them as they are wanted,
/// and `sink` is handed each part's inverse as it is made, to be put on disk and
/// dropped, never kept here. Each part is checked against the database as the
/// parts before it left it, which is why the natural entries are all in the first
/// `[SPEC-NKP-930]`. With `commit` false it is a rehearsal and nothing is kept.
pub fn apply_parts(
    db: &Path,
    parts: &mut dyn Iterator<Item = Result<Json, String>>,
    allowed: Option<&[&str]>,
    rule: Rule,
    commit: bool,
    sink: &mut dyn FnMut(usize, &Json) -> Result<(), String>,
) -> Result<Applied, String> {
    let mut c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_WRITE)
        .map_err(|e| format!("open {}: {e}", db.display()))?;
    c.busy_timeout(std::time::Duration::from_secs(10)).map_err(|e| e.to_string())?;
    let tx = c.transaction().map_err(|e| e.to_string())?;
    let mut total = Applied::default();
    for (i, part) in parts.enumerate() {
        let patch = part.map_err(|e| format!("part {}: {e}", i + 1))?;
        let out = apply_tx(&tx, &patch, allowed, rule).map_err(|e| format!("part {}: {e}", i + 1))?;
        sink(i, &out.inverse)?;
        total.applied += out.counts.applied;
        total.already += out.counts.already;
        total.kept += out.counts.kept;
    }
    if commit {
        tx.commit().map_err(|e| e.to_string())?;
    }
    Ok(total)
}

fn apply_tx(tx: &Connection, patch: &Json, allowed: Option<&[&str]>, rule: Rule) -> Result<Outcome, String> {
    let mut counts = Applied::default();
    let mut inverse = Vec::new();
    let entries = patch["tables"].as_array().ok_or("a patch has tables")?;
    // `[SPEC-NKP-060]`: every natural entry is checked, against the database as it
    // is before the patch writes anything, so that a parent whose key the patch
    // changes cannot make its children unfindable.
    let mut natural = plan_natural(tx, entries, allowed, rule)?;
    for (at, t) in entries.iter().enumerate() {
        if let Some(np) = natural.remove(&at) {
            if let Some(inv) = write_natural(tx, t, &np, &mut counts)? {
                inverse.push(inv);
            }
            continue;
        }
        let name = ident(t["name"].as_str().ok_or("a table has a name")?)?;
        if allowed.is_some_and(|a| !a.contains(&name)) {
            return Err(format!("{name} is not a shared table: refused, and nothing written"));
        }
        let strs = |k: &str| -> Result<Vec<String>, String> {
            t[k].as_array()
                .ok_or(format!("{name}: no {k}"))?
                .iter()
                .map(|v| v.as_str().ok_or(format!("{name}: {k} holds a non-name")).and_then(ident).map(str::to_string))
                .collect()
        };
        let (cols, key) = (strs("columns")?, strs("key")?);
        let mut inv = serde_json::json!({"name": name, "columns": cols, "key": key});
        // The schema first, and only for the catalogue: a member's shared
        // tables are made by its own player, never by a patch.
        let mut have = columns(tx, name)?;
        if have.is_empty() && rule == Rule::Strict && t["create"].is_array() {
            for s in t["create"].as_array().into_iter().flatten() {
                tx.execute_batch(creation(s.as_str().unwrap_or(""))?).map_err(|e| format!("{name}: {e}"))?;
            }
            inv["drop_table"] = Json::Bool(true);
            have = columns(tx, name)?;
        }
        if rule == Rule::Strict {
            let mut added = Vec::new();
            for d in t["add_columns"].as_array().into_iter().flatten() {
                let decl = d.as_str().unwrap_or("");
                let col = ident(decl.split_whitespace().next().unwrap_or(""))?;
                if decl.contains(';') {
                    return Err(format!("{name}: not a column declaration: {decl}"));
                }
                if !have.iter().any(|h| h == col) {
                    tx.execute_batch(&format!("ALTER TABLE {name} ADD COLUMN {decl}")).map_err(|e| format!("{name}: {e}"))?;
                    added.push(col.to_string());
                }
            }
            if !added.is_empty() {
                inv["drop_columns"] = serde_json::json!(added);
                have = columns(tx, name)?;
            }
        }
        match rule {
            Rule::Member => {
                let (mut h, mut w) = (have.clone(), cols.clone());
                h.sort();
                w.sort();
                if h != w {
                    return Err(format!("{name}: columns here {h:?} are not the patch's {w:?}; nothing written"));
                }
            }
            Rule::Strict => {
                if let Some(c) = cols.iter().find(|c| !have.contains(c)) {
                    return Err(format!("{name}: no column {c} here; nothing written"));
                }
            }
        }
        if let Some(k) = key.iter().find(|k| !cols.contains(k)) {
            return Err(format!("{name}: key column {k} is not among the patch's columns"));
        }
        let others: Vec<&String> = have.iter().filter(|h| !cols.contains(h)).collect();
        let wh = |from: usize| {
            key.iter().enumerate().map(|(i, k)| format!("{k} IS ?{}", from + i)).collect::<Vec<_>>().join(" AND ")
        };
        let select = format!("SELECT {} FROM {name} WHERE {} LIMIT 1", cols.join(", "), wh(1));
        let delete = format!("DELETE FROM {name} WHERE {}", wh(1));
        let update = format!(
            "UPDATE {name} SET {} WHERE {}",
            cols.iter().enumerate().map(|(i, c)| format!("{c} = ?{}", i + 1)).collect::<Vec<_>>().join(", "),
            wh(cols.len() + 1)
        );
        let mut rows_inv = Vec::new();
        for r in t["rows"].as_array().ok_or(format!("{name}: no rows"))? {
            let k: Vec<Value> = r["key"].as_array().ok_or("a row has a key")?.iter().map(to_sql).collect();
            let cur: Option<Vec<Value>> = tx
                .query_row(&select, params_from_iter(k.iter()), |row| (0..cols.len()).map(|i| row.get::<_, Value>(i)).collect())
                .ok();
            let (now, was) = (row_of(&r["now"]), row_of(&r["was"]));
            if same(&cur, &now) {
                counts.already += 1;
                continue;
            }
            if !same(&cur, &was) {
                if rule == Rule::Strict {
                    return Err(format!("{name}: the row keyed {} is not as the patch expects; nothing written", r["key"]));
                }
                counts.kept += 1;
                continue;
            }
            match (&was, &now) {
                (None, Some(v)) => {
                    let mut names: Vec<String> = cols.clone();
                    let mut vals: Vec<Value> = v.clone();
                    for (col, val) in r["also"].as_object().into_iter().flatten() {
                        let col = ident(col)?;
                        if !others.iter().any(|o| *o == col) {
                            return Err(format!("{name}: `also` names {col}, which is not a column outside the patch's"));
                        }
                        names.push(col.to_string());
                        vals.push(to_sql(val));
                    }
                    let sql = format!(
                        "INSERT INTO {name} ({}) VALUES ({})",
                        names.join(", "),
                        (1..=names.len()).map(|i| format!("?{i}")).collect::<Vec<_>>().join(", ")
                    );
                    tx.execute(&sql, params_from_iter(vals.iter())).map_err(|e| format!("{name}: {e}"))?;
                    rows_inv.push(serde_json::json!({"key": r["key"], "was": r["now"], "now": null}));
                }
                (Some(_), None) => {
                    // What the patch does not name is kept for the undo, so a
                    // deleted row comes back whole.
                    let mut also = serde_json::Map::new();
                    if !others.is_empty() {
                        let q = format!("SELECT {} FROM {name} WHERE {} LIMIT 1",
                                        others.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", "), wh(1));
                        let vals: Vec<Value> = tx
                            .query_row(&q, params_from_iter(k.iter()), |row| (0..others.len()).map(|i| row.get::<_, Value>(i)).collect())
                            .map_err(|e| format!("{name}: {e}"))?;
                        for (o, v) in others.iter().zip(vals) {
                            also.insert(o.to_string(), to_json(&v));
                        }
                    }
                    tx.execute(&delete, params_from_iter(k.iter())).map_err(|e| format!("{name}: {e}"))?;
                    rows_inv.push(serde_json::json!({"key": r["key"], "was": null, "now": r["was"], "also": also}));
                }
                (Some(_), Some(v)) => {
                    let params: Vec<&Value> = v.iter().chain(k.iter()).collect();
                    tx.execute(&update, params_from_iter(params)).map_err(|e| format!("{name}: {e}"))?;
                    rows_inv.push(serde_json::json!({"key": r["key"], "was": r["now"], "now": r["was"]}));
                }
                (None, None) => unreachable!("a row absent both before and after equals `now`"),
            }
            counts.applied += 1;
        }
        rows_inv.reverse();
        inv["rows"] = Json::Array(rows_inv);
        if inv["rows"].as_array().is_some_and(|r| !r.is_empty()) || inv.get("drop_table").is_some() || inv.get("drop_columns").is_some() {
            inverse.push(inv);
        }
    }
    inverse.reverse();
    Ok(Outcome { counts, inverse: serde_json::json!({ "tables": inverse }) })
}

// ---------------------------------------------------------------------
// The catalogue patch by natural key `[SPEC-NKP-010..075]`.
//
// A catalogue's numeric ids are its own `[SPEC-DF-035]`, so a patch for the
// tables that hold them names rows by what identifies them everywhere: a file
// by its `audio_md5`, a passage by `(audio_md5, kind, start_ms, end_ms)`. The
// node finds its own row by that, compares the row as named by that, and
// writes with its own ids. A patch entry says which columns are references:
//
//   {"name": "passages", "natural": {"surrogate": "passage_id",
//                                    "refs": [{"col": "file_id", "kind": "file"}]},
//    "columns": [...N-form, no surrogate...], "key": [...], "rows": [...]}
// ---------------------------------------------------------------------

/// What a column holding a local id names. The vocabulary is fixed
/// `[SPEC-NKP-030]`: a word outside it is refused, never guessed at.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum RefKind {
    File,
    Passage,
}

/// One reference column; `when` is `flavor`'s way of naming a passage only on
/// the rows whose kind column says so. A reference with `when` is stored as
/// text on the node, as `subject_id` is.
struct RefSpec {
    col: String,
    kind: RefKind,
    when: Option<(String, String)>,
}

struct Natural {
    /// The table's own id, which the patch never carries.
    surrogate: Option<String>,
    refs: Vec<RefSpec>,
}

impl Natural {
    /// The reference `col` is in this row, if it is one. `ctx` and `vals` are the
    /// row's columns and values, as far as the caller has them.
    fn ref_for(&self, col: &str, ctx: &[String], vals: &[Value]) -> Option<&RefSpec> {
        self.refs.iter().find(|r| {
            r.col == col
                && r.when.as_ref().is_none_or(|(wc, wv)| {
                    ctx.iter().position(|c| c == wc).is_some_and(|i| matches!(&vals[i], Value::Text(t) if t == wv))
                })
        })
    }
}

fn natural_of(name: &str, t: &Json, cols: &[String], have: &[String]) -> Result<Natural, String> {
    let n = &t["natural"];
    if t["create"].is_array() {
        return Err(format!("{name}: a natural entry makes no table; nothing written"));
    }
    let surrogate = match n["surrogate"].as_str() {
        Some(s) => {
            let s = ident(s)?;
            if !have.iter().any(|h| h == s) {
                return Err(format!("{name}: no surrogate column {s} here; nothing written"));
            }
            if cols.iter().any(|c| c == s) {
                return Err(format!("{name}: the surrogate {s} is carried, and must not be; nothing written"));
            }
            Some(s.to_string())
        }
        None => None,
    };
    let mut refs = Vec::new();
    for r in n["refs"].as_array().ok_or(format!("{name}: a natural entry has refs"))? {
        let col = ident(r["col"].as_str().unwrap_or(""))?;
        if !cols.iter().any(|c| c == col) {
            return Err(format!("{name}: reference column {col} is not among the patch's columns"));
        }
        let kind = match r["kind"].as_str() {
            Some("file") => RefKind::File,
            Some("passage") => RefKind::Passage,
            other => return Err(format!("{name}: {other:?} is not a kind of reference; nothing written")),
        };
        let when = match r["when"].as_array() {
            None => None,
            Some(w) if w.len() == 2 => {
                let wc = ident(w[0].as_str().unwrap_or(""))?;
                if !cols.iter().any(|c| c == wc) {
                    return Err(format!("{name}: condition column {wc} is not among the patch's columns"));
                }
                Some((wc.to_string(), w[1].as_str().ok_or(format!("{name}: a condition is a column and a word"))?.to_string()))
            }
            Some(_) => return Err(format!("{name}: a condition is a column and a word")),
        };
        refs.push(RefSpec { col: col.to_string(), kind, when });
    }
    Ok(Natural { surrogate, refs })
}

fn es(e: rusqlite::Error) -> String {
    e.to_string()
}

/// This node's id for what `nat` names, if it holds it.
fn to_local(tx: &Connection, kind: RefKind, nat: &Value) -> Result<Option<i64>, String> {
    let Value::Text(text) = nat else { return Ok(None) };
    match kind {
        RefKind::File => tx
            .prepare_cached("SELECT file_id FROM files WHERE audio_md5 = ?1")
            .map_err(es)?
            .query_row([text], |r| r.get::<_, i64>(0))
            .optional()
            .map_err(es),
        RefKind::Passage => {
            let p: Json = serde_json::from_str(text).map_err(|_| format!("not a passage reference: {text}"))?;
            let a = p.as_array().filter(|a| a.len() == 4).ok_or_else(|| format!("not a passage reference: {text}"))?;
            tx.prepare_cached(
                "SELECT p.passage_id FROM passages p JOIN files f ON f.file_id = p.file_id \
                 WHERE f.audio_md5 = ?1 AND p.kind = ?2 AND p.start_ms = ?3 AND p.end_ms = ?4",
            )
            .map_err(es)?
            .query_row(
                params![a[0].as_str().unwrap_or(""), a[1].as_str().unwrap_or(""), a[2].as_i64().unwrap_or(-1), a[3].as_i64().unwrap_or(-1)],
                |r| r.get::<_, i64>(0),
            )
            .optional()
            .map_err(es)
        }
    }
}

/// What this node's local id names, in the patch's own spelling: a file's
/// `audio_md5`, a passage's `[md5, kind, start, end]` as compact JSON text.
fn to_natural(tx: &Connection, kind: RefKind, local: &Value) -> Result<Option<Value>, String> {
    let id = match local {
        Value::Integer(i) => *i,
        Value::Text(s) => match s.parse::<i64>() {
            Ok(i) => i,
            Err(_) => return Ok(None),
        },
        _ => return Ok(None),
    };
    Ok(match kind {
        RefKind::File => tx
            .prepare_cached("SELECT audio_md5 FROM files WHERE file_id = ?1")
            .map_err(es)?
            .query_row([id], |r| r.get::<_, String>(0))
            .optional()
            .map_err(es)?
            .map(Value::Text),
        RefKind::Passage => tx
            .prepare_cached(
                "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p JOIN files f ON f.file_id = p.file_id \
                 WHERE p.passage_id = ?1",
            )
            .map_err(es)?
            .query_row([id], |r| {
                Ok(serde_json::json!([r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, i64>(2)?, r.get::<_, i64>(3)?]))
            })
            .optional()
            .map_err(es)?
            .map(|j| Value::Text(j.to_string())),
    })
}

/// A local row in the patch's own terms `[SPEC-NKP-035]`. A reference to
/// something that is not there is made to equal nothing.
fn naturalize(tx: &Connection, nat: &Natural, cols: &[String], vals: Vec<Value>) -> Result<Vec<Value>, String> {
    let mut out = vals.clone();
    for (i, c) in cols.iter().enumerate() {
        if let Some(r) = nat.ref_for(c, cols, &vals) {
            if matches!(vals[i], Value::Null) {
                continue;
            }
            out[i] = to_natural(tx, r.kind, &vals[i])?.unwrap_or_else(|| Value::Text(format!("<no {:?}: {:?}>", r.kind, vals[i])));
        }
    }
    Ok(out)
}

fn local_value(r: &RefSpec, id: i64) -> Value {
    if r.when.is_some() { Value::Text(id.to_string()) } else { Value::Integer(id) }
}

/// A patch row's values with each reference turned into this node's id. A
/// reference to what the node does not hold is an error: nothing is written.
fn localize(tx: &Connection, name: &str, nat: &Natural, cols: &[String], vals: &[Value]) -> Result<Vec<Value>, String> {
    let mut out = vals.to_vec();
    for (i, c) in cols.iter().enumerate() {
        if let Some(r) = nat.ref_for(c, cols, vals) {
            if matches!(vals[i], Value::Null) {
                continue;
            }
            match to_local(tx, r.kind, &vals[i])? {
                Some(id) => out[i] = local_value(r, id),
                None => return Err(format!("{name}: {c} names a {:?} this node does not hold; nothing written", r.kind)),
            }
        }
    }
    Ok(out)
}

/// A natural key as this node's own key, or `None` where something it names
/// is not here.
fn resolve_key(tx: &Connection, nat: &Natural, key: &[String], k: &[Value]) -> Result<Option<Vec<Value>>, String> {
    let mut out = Vec::with_capacity(k.len());
    for (i, c) in key.iter().enumerate() {
        match nat.ref_for(c, key, k) {
            Some(r) if !matches!(k[i], Value::Null) => match to_local(tx, r.kind, &k[i])? {
                Some(id) => out.push(local_value(r, id)),
                None => return Ok(None),
            },
            _ => out.push(k[i].clone()),
        }
    }
    Ok(Some(out))
}

fn where_key(key: &[String], from: usize) -> String {
    key.iter().enumerate().map(|(i, k)| format!("{k} IS ?{}", from + i)).collect::<Vec<_>>().join(" AND ")
}

/// The row at a local key, in the patch's terms.
fn select_n(tx: &Connection, name: &str, nat: &Natural, cols: &[String], key: &[String], lk: &[Value]) -> Result<Option<Vec<Value>>, String> {
    let sql = format!("SELECT {} FROM {name} WHERE {} LIMIT 1", cols.join(", "), where_key(key, 1));
    let row: Option<Vec<Value>> = tx
        .prepare_cached(&sql)
        .map_err(es)?
        .query_row(params_from_iter(lk.iter()), |r| (0..cols.len()).map(|i| r.get::<_, Value>(i)).collect())
        .optional()
        .map_err(es)?;
    row.map(|v| naturalize(tx, nat, cols, v)).transpose()
}

/// The whole row, every column, as this node holds it: what the undo keeps.
fn select_full(tx: &Connection, name: &str, have: &[String], key: &[String], lk: &[Value]) -> Result<Vec<Value>, String> {
    let sql = format!("SELECT {} FROM {name} WHERE {} LIMIT 1", have.join(", "), where_key(key, 1));
    tx.prepare_cached(&sql)
        .map_err(es)?
        .query_row(params_from_iter(lk.iter()), |r| (0..have.len()).map(|i| r.get::<_, Value>(i)).collect())
        .map_err(|e| format!("{name}: the row just written cannot be read back: {e}"))
}

/// What the check decided for one row.
struct Planned {
    /// This node's key for the row, where it holds one.
    local_key: Option<Vec<Value>>,
    /// Already as the patch makes it: nothing to do.
    already: bool,
}

/// One natural entry, checked.
struct NaturalPlan {
    name: String,
    cols: Vec<String>,
    key: Vec<String>,
    have: Vec<String>,
    nat: Natural,
    rows: Vec<Planned>,
    /// Columns this patch added, which the undo drops.
    added: Vec<String>,
}

fn names_of(name: &str, t: &Json, k: &str) -> Result<Vec<String>, String> {
    t[k].as_array()
        .ok_or(format!("{name}: no {k}"))?
        .iter()
        .map(|v| v.as_str().ok_or(format!("{name}: {k} holds a non-name")).and_then(ident).map(str::to_string))
        .collect()
}

/// `[SPEC-NKP-060]`, the check: every natural row is resolved to this node's own
/// row, in the database as it is before the patch, and is *already* as the
/// patch makes it, or *to apply* because it is as the patch says it was, or a
/// refusal of the whole patch. Nothing is written here.
fn plan_natural(tx: &Connection, entries: &[Json], allowed: Option<&[&str]>, rule: Rule) -> Result<HashMap<usize, NaturalPlan>, String> {
    let mut plans = HashMap::new();
    for (at, t) in entries.iter().enumerate() {
        if t["natural"].is_null() {
            continue;
        }
        let name = ident(t["name"].as_str().ok_or("a table has a name")?)?;
        if rule != Rule::Strict || allowed.is_some() {
            return Err(format!("{name}: a natural entry is the catalogue's, under the strict rule; nothing written"));
        }
        let (cols, key) = (names_of(name, t, "columns")?, names_of(name, t, "key")?);
        let mut have = columns(tx, name)?;
        if have.is_empty() {
            return Err(format!("{name}: not a table here; nothing written"));
        }
        // A column the node lacks is added, in this transaction, as the id-keyed path
        // adds one: a rehearsal rolls it back, and the undo drops it.
        let mut added = Vec::new();
        for d in t["add_columns"].as_array().into_iter().flatten() {
            let decl = d.as_str().unwrap_or("");
            let col = ident(decl.split_whitespace().next().unwrap_or(""))?;
            if decl.contains(';') {
                return Err(format!("{name}: not a column declaration: {decl}"));
            }
            if !have.iter().any(|h| h == col) {
                tx.execute_batch(&format!("ALTER TABLE {name} ADD COLUMN {decl}")).map_err(|e| format!("{name}: {e}"))?;
                added.push(col.to_string());
            }
        }
        if !added.is_empty() {
            have = columns(tx, name)?;
        }
        if let Some(c) = cols.iter().find(|c| !have.contains(c)) {
            return Err(format!("{name}: no column {c} here; nothing written"));
        }
        if let Some(k) = key.iter().find(|k| !cols.contains(k)) {
            return Err(format!("{name}: key column {k} is not among the patch's columns"));
        }
        let nat = natural_of(name, t, &cols, &have)?;
        let at_of = |c: &String| cols.iter().position(|x| x == c);
        let mut rows = Vec::new();
        for r in t["rows"].as_array().ok_or(format!("{name}: no rows"))? {
            let k: Vec<Value> = r["key"].as_array().ok_or("a row has a key")?.iter().map(to_sql).collect();
            let (now, was) = (row_of(&r["now"]), row_of(&r["was"]));
            let lk = resolve_key(tx, &nat, &key, &k)?;
            let cur = match &lk {
                Some(lk) => select_n(tx, name, &nat, &cols, &key, lk)?,
                None => None,
            };
            if same(&cur, &now) {
                rows.push(Planned { local_key: lk, already: true });
                continue;
            }
            // A row whose key the patch changes may already be at its new key.
            if let Some(nv) = &now {
                let nk: Vec<Value> = key.iter().filter_map(|c| at_of(c).map(|i| nv[i].clone())).collect();
                if nk != k {
                    if let Some(nlk) = resolve_key(tx, &nat, &key, &nk)? {
                        if same(&select_n(tx, name, &nat, &cols, &key, &nlk)?, &now) {
                            rows.push(Planned { local_key: Some(nlk), already: true });
                            continue;
                        }
                    }
                }
            }
            if !same(&cur, &was) {
                return Err(format!("{name}: the row keyed {} is not as the patch expects; nothing written", r["key"]));
            }
            rows.push(Planned { local_key: lk, already: false });
        }
        plans.insert(at, NaturalPlan { name: name.to_string(), cols, key, have, nat, rows, added });
    }
    Ok(plans)
}

/// `[SPEC-NKP-060]`, the write: updates and deletes by the key the check found,
/// inserts with each reference resolved once its parent is here and the
/// table's own id left to SQLite. The inverse is in this node's own terms
/// `[SPEC-NKP-070]`, so `undo` is the id-keyed one.
fn write_natural(tx: &Connection, t: &Json, np: &NaturalPlan, counts: &mut Applied) -> Result<Option<Json>, String> {
    let NaturalPlan { name, cols, key, have, nat, rows, added } = np;
    let pk = key_of(tx, name)?;
    let pk_at: Vec<usize> = pk.iter().filter_map(|c| have.iter().position(|h| h == c)).collect();
    let others: Vec<&String> = have.iter().filter(|h| !cols.contains(h) && nat.surrogate.as_ref() != Some(*h)).collect();
    let at_of = |c: &String| cols.iter().position(|x| x == c);
    let json_row = |v: &[Value]| Json::Array(v.iter().map(to_json).collect());
    let pk_of = |v: &[Value]| Json::Array(pk_at.iter().map(|&i| to_json(&v[i])).collect());
    let mut inv_rows = Vec::new();
    for (r, p) in t["rows"].as_array().ok_or(format!("{name}: no rows"))?.iter().zip(rows) {
        if p.already {
            counts.already += 1;
            continue;
        }
        let (was, now) = (row_of(&r["was"]), row_of(&r["now"]));
        match (&was, &now) {
            (None, Some(v)) => {
                let mut names = cols.clone();
                let mut vals = localize(tx, name, nat, cols, v)?;
                let lk: Vec<Value> = key.iter().filter_map(|c| at_of(c).map(|i| vals[i].clone())).collect();
                for (col, val) in r["also"].as_object().into_iter().flatten() {
                    let col = ident(col)?;
                    if !others.iter().any(|o| *o == col) {
                        return Err(format!("{name}: `also` names {col}, which is not a column outside the patch's"));
                    }
                    names.push(col.to_string());
                    vals.push(to_sql(val));
                }
                let sql = format!(
                    "INSERT INTO {name} ({}) VALUES ({})",
                    names.join(", "),
                    (1..=names.len()).map(|i| format!("?{i}")).collect::<Vec<_>>().join(", ")
                );
                tx.execute(&sql, params_from_iter(vals.iter())).map_err(|e| format!("{name}: {e}"))?;
                let after = select_full(tx, name, have, key, &lk)?;
                inv_rows.push(serde_json::json!({"key": pk_of(&after), "was": json_row(&after), "now": null}));
            }
            (Some(_), None) => {
                let lk = p.local_key.clone().ok_or(format!("{name}: a row to remove is not here; nothing written"))?;
                let before = select_full(tx, name, have, key, &lk)?;
                tx.execute(&format!("DELETE FROM {name} WHERE {}", where_key(key, 1)), params_from_iter(lk.iter()))
                    .map_err(|e| format!("{name}: {e}"))?;
                inv_rows.push(serde_json::json!({"key": pk_of(&before), "was": null, "now": json_row(&before)}));
            }
            (Some(_), Some(v)) => {
                let lk = p.local_key.clone().ok_or(format!("{name}: a row to change is not here; nothing written"))?;
                let before = select_full(tx, name, have, key, &lk)?;
                let vals = localize(tx, name, nat, cols, v)?;
                let sets = cols.iter().enumerate().map(|(i, c)| format!("{c} = ?{}", i + 1)).collect::<Vec<_>>().join(", ");
                let params: Vec<&Value> = vals.iter().chain(lk.iter()).collect();
                tx.execute(&format!("UPDATE {name} SET {sets} WHERE {}", where_key(key, cols.len() + 1)), params_from_iter(params))
                    .map_err(|e| format!("{name}: {e}"))?;
                let nk: Vec<Value> = key.iter().filter_map(|c| at_of(c).map(|i| vals[i].clone())).collect();
                let after = select_full(tx, name, have, key, &nk)?;
                inv_rows.push(serde_json::json!({"key": pk_of(&after), "was": json_row(&after), "now": json_row(&before)}));
            }
            (None, None) => unreachable!("a row absent both before and after equals `now`"),
        }
        counts.applied += 1;
    }
    if inv_rows.is_empty() && added.is_empty() {
        return Ok(None);
    }
    inv_rows.reverse();
    let mut inv = serde_json::json!({"name": name, "columns": have, "key": pk, "rows": inv_rows});
    if !added.is_empty() {
        inv["drop_columns"] = serde_json::json!(added);
    }
    Ok(Some(inv))
}

/// A table, column or index name, as the only thing ever spliced into SQL
/// here. Patches arrive signed by the mesh key, and this is the second lock.
fn ident(s: &str) -> Result<&str, String> {
    let ok = !s.is_empty()
        && s.chars().next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_');
    if ok { Ok(s) } else { Err(format!("not a name: {s:?}")) }
}

/// One `CREATE TABLE` or `CREATE [UNIQUE] INDEX` statement, and nothing else.
fn creation(s: &str) -> Result<&str, String> {
    let head = s.trim_start().to_ascii_uppercase();
    let one = !s.trim_end().trim_end_matches(';').contains(';');
    if one && ["CREATE TABLE ", "CREATE INDEX ", "CREATE UNIQUE INDEX "].iter().any(|p| head.starts_with(p)) {
        Ok(s)
    } else {
        Err(format!("not a single CREATE TABLE or INDEX: {s}"))
    }
}

/// `tables` of `db` as values, with what the hub needs to rebuild each: its
/// creating statements, columns and key `[SPEC-NSH-050]`. Rows about a
/// passage stay in, under this node's ids -- the summary says what they mean
/// `[SPEC-NSH-060]`. Read-only, and WAL-aware, so a running player is neither
/// disturbed nor missed.
pub fn export(db: &Path, tables: &[&str]) -> Result<Json, String> {
    let c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("open {}: {e}", db.display()))?;
    let tx = c.unchecked_transaction().map_err(|e| e.to_string())?; // one consistent read
    let mut out = Vec::new();
    for &t in tables {
        let cols = columns(&tx, t)?;
        if cols.is_empty() {
            continue;
        }
        let mut q = tx
            .prepare("SELECT sql FROM sqlite_master WHERE tbl_name = ?1 AND sql IS NOT NULL ORDER BY type DESC, name")
            .map_err(|e| e.to_string())?;
        let create: Vec<String> = q.query_map([t], |r| r.get(0)).map_err(|e| e.to_string())?.filter_map(Result::ok).collect();
        out.push(serde_json::json!({"name": t, "create": create, "columns": cols, "key": key_of(&tx, t)?,
                                    "rows": rows(&tx, t, &cols)?}));
    }
    Ok(serde_json::json!({ "tables": out }))
}

/// What the hub's merge reads of a node's catalogue, and no more
/// `[SPEC-NSH-060]`: each passage's anchor, and each file's `audio_md5` with the
/// node's own machine-scope columns. As [`export`]'s shape, so the hub builds
/// the two tables alike; the catalogue itself -- 1.27 GB on an appliance --
/// stays where it is.
pub fn catalogue_summary(library: &Path) -> Result<Json, String> {
    let c = Connection::open_with_flags(library, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("open {}: {e}", library.display()))?;
    let tx = c.unchecked_transaction().map_err(|e| e.to_string())?;
    let want = |t: &str, fixed: &[&str], optional: &[&str]| -> Result<Json, String> {
        let have = columns(&tx, t)?;
        let cols: Vec<String> = fixed.iter().chain(optional.iter().filter(|o| have.iter().any(|h| h == *o)))
            .map(|s| s.to_string()).collect();
        if let Some(m) = cols.iter().find(|c| !have.contains(c)) {
            return Err(format!("the catalogue's {t} has no {m}"));
        }
        Ok(serde_json::json!({"name": t, "columns": cols, "key": [cols[0].clone()], "rows": rows(&tx, t, &cols)?}))
    };
    let files = want("files", &["file_id", "audio_md5"], &MACHINE_SCOPE)?;
    let passages = want("passages", &["passage_id", "file_id", "kind", "start_ms", "end_ms"], &[])?;
    Ok(serde_json::json!({ "tables": [files, passages] }))
}

fn rows(c: &Connection, t: &str, cols: &[String]) -> Result<Vec<Json>, String> {
    let mut q = c.prepare(&format!("SELECT {} FROM {t}", cols.join(", "))).map_err(|e| e.to_string())?;
    let v = q
        .query_map([], |r| Ok(Json::Array((0..cols.len()).map(|i| r.get::<_, Value>(i).map(|v| to_json(&v))).collect::<Result<_, _>>()?)))
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;
    Ok(v)
}

/// The primary key, in its order; a table without one is keyed by the whole
/// row, as `star_patch.key_of` keys it.
fn key_of(c: &Connection, t: &str) -> Result<Vec<String>, String> {
    let mut q = c.prepare(&format!("PRAGMA table_info({t})")).map_err(|e| e.to_string())?;
    let mut pk: Vec<(i64, String)> = q
        .query_map([], |r| Ok((r.get::<_, i64>(5)?, r.get::<_, String>(1)?)))
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .filter(|(n, _)| *n > 0)
        .collect();
    pk.sort();
    if pk.is_empty() { columns(c, t) } else { Ok(pk.into_iter().map(|(_, n)| n).collect()) }
}

/// A value as a patch carries it: a blob as `{"b64": ...}`, as `star_patch.enc`.
fn to_json(v: &Value) -> Json {
    use base64::Engine;
    match v {
        Value::Null => Json::Null,
        Value::Integer(i) => Json::from(*i),
        Value::Real(f) => serde_json::Number::from_f64(*f).map_or(Json::Null, Json::Number),
        Value::Text(s) => Json::String(s.clone()),
        Value::Blob(b) => serde_json::json!({"b64": base64::engine::general_purpose::STANDARD.encode(b)}),
    }
}

fn columns(c: &Connection, table: &str) -> Result<Vec<String>, String> {
    let (schema, name) = table.split_once('.').map_or(("main", table), |(s, n)| (s, n));
    let mut q = c.prepare(&format!("PRAGMA {schema}.table_info({name})")).map_err(|e| e.to_string())?;
    let v = q.query_map([], |r| r.get::<_, String>(1)).map_err(|e| e.to_string())?.filter_map(Result::ok).collect();
    Ok(v)
}

fn to_sql(v: &Json) -> Value {
    match v {
        Json::Null => Value::Null,
        Json::Bool(b) => Value::Integer(i64::from(*b)),
        Json::Number(n) => n.as_i64().map_or_else(|| Value::Real(n.as_f64().unwrap_or(0.0)), Value::Integer),
        Json::String(s) => Value::Text(s.clone()),
        // A blob, as `star_patch.enc` writes one [SPEC-NSH-070].
        Json::Object(o) if o.len() == 1 && o.get("b64").is_some_and(Json::is_string) => {
            use base64::Engine;
            base64::engine::general_purpose::STANDARD
                .decode(o["b64"].as_str().unwrap_or(""))
                .map_or_else(|_| Value::Text(v.to_string()), Value::Blob)
        }
        other => Value::Text(other.to_string()),
    }
}

fn row_of(v: &Json) -> Option<Vec<Value>> {
    v.as_array().map(|a| a.iter().map(to_sql).collect())
}

/// Two rows equal as SQLite compares them: an integer and a real are the same
/// number when their values are, since JSON does not keep that distinction.
fn same(a: &Option<Vec<Value>>, b: &Option<Vec<Value>>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(a), Some(b)) => a.len() == b.len() && a.iter().zip(b).all(|(x, y)| value_eq(x, y)),
        _ => false,
    }
}

fn value_eq(x: &Value, y: &Value) -> bool {
    match (x, y) {
        (Value::Integer(a), Value::Real(b)) | (Value::Real(b), Value::Integer(a)) => (*a as f64) == *b,
        _ => x == y,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn listener(path: &Path) {
        let c = Connection::open(path).unwrap();
        c.execute_batch(
            "CREATE TABLE listener_preferences (subject_kind TEXT, subject_id TEXT, rotation REAL,
                 updated_at TEXT, PRIMARY KEY (subject_kind, subject_id));
             CREATE TABLE listener_flags (subject_kind TEXT, subject_id TEXT, flagged_at TEXT,
                 PRIMARY KEY (subject_kind, subject_id));
             CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY, mbid TEXT);
             INSERT INTO listener_preferences VALUES ('recording','r1',1.5,'t1'), ('artist','a1',0.000001,'t1');
             INSERT INTO listener_flags VALUES ('recording','keep','t0'), ('passage','42','t0');
             INSERT INTO listener_play_history VALUES (1,'r1');",
        )
        .unwrap();
    }

    fn tmp(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("lempi-mesh-sync-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// Only the shared tables leave, and a passage's row stays home.
    #[test]
    fn a_snapshot_carries_the_shared_edits_and_nothing_else() {
        let d = tmp("snap");
        listener(&d.join("listener.db"));
        let r = snapshot(&d.join("listener.db"), &d.join("up.db")).unwrap();
        assert_eq!(r, Snapshot { rows: 3, held_back: 1 });
        let c = Connection::open(d.join("up.db")).unwrap();
        let tables: Vec<String> = c
            .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").unwrap()
            .query_map([], |r| r.get(0)).unwrap().filter_map(Result::ok).collect();
        assert_eq!(tables, ["listener_flags", "listener_preferences"], "no plays, and no table it lacks");
        let flags: i64 = c.query_row("SELECT count(*) FROM listener_flags", [], |r| r.get(0)).unwrap();
        assert_eq!(flags, 1, "the passage flag stayed on the phone");
    }

    /// The three-way rule, row by row: taken where the phone still holds its
    /// upload, kept where it has changed since, and nothing outside the shared
    /// tables accepted.
    #[test]
    fn a_patch_changes_only_what_the_phone_has_not_changed_since() {
        let d = tmp("apply");
        let db = d.join("listener.db");
        listener(&db);
        let patch = serde_json::json!({"tables": [
            {"name": "listener_preferences", "columns": ["subject_kind","subject_id","rotation","updated_at"],
             "key": ["subject_kind","subject_id"], "rows": [
                {"key": ["recording","r1"], "was": ["recording","r1",1.5,"t1"], "now": ["recording","r1",2.0,"t2"]},
                {"key": ["artist","a1"], "was": ["artist","a1",0.5,"t0"], "now": ["artist","a1",3.0,"t3"]},
                {"key": ["recording","new"], "was": null, "now": ["recording","new",1,"t4"]}]},
            {"name": "listener_flags", "columns": ["subject_kind","subject_id","flagged_at"],
             "key": ["subject_kind","subject_id"], "rows": [
                {"key": ["recording","keep"], "was": ["recording","keep","t0"], "now": null},
                {"key": ["recording","x"], "was": null, "now": ["recording","x","t5"]}]}]});
        let r = apply(&db, &patch.to_string()).unwrap();
        assert_eq!(r, Applied { applied: 4, already: 0, kept: 1 }, "{r:?}");
        let c = Connection::open(&db).unwrap();
        let rot = |id: &str| -> f64 {
            c.query_row("SELECT rotation FROM listener_preferences WHERE subject_id=?1", [id], |r| r.get(0)).unwrap()
        };
        assert_eq!(rot("r1"), 2.0, "taken: the phone still held its upload");
        assert_eq!(rot("a1"), 0.000001, "kept: changed here since, so it goes up next time");
        assert_eq!(rot("new"), 1.0, "a row the phone never had arrives");
        let keep: i64 = c.query_row("SELECT count(*) FROM listener_flags WHERE subject_id='keep'", [], |r| r.get(0)).unwrap();
        assert_eq!(keep, 0, "a flag the household removed is removed");
        drop(c);
        let again = apply(&db, &patch.to_string()).unwrap();
        assert_eq!((again.applied, again.already), (0, 4), "a second apply changes nothing: {again:?}");

        // A value exactly as the hub holds it, to the last bit: found on the
        // Moto G, where the default parse wrote 1.146 for 1.1460000000000001.
        let exact = r#"{"tables": [{"name": "listener_preferences",
            "columns": ["subject_kind","subject_id","rotation","updated_at"], "key": ["subject_kind","subject_id"],
            "rows": [{"key": ["recording","ulp"], "was": null, "now": ["recording","ulp",1.1460000000000001,"t"]}]}]}"#;
        apply(&db, exact).unwrap();
        let got: f64 = Connection::open(&db).unwrap()
            .query_row("SELECT rotation FROM listener_preferences WHERE subject_id='ulp'", [], |r| r.get(0)).unwrap();
        assert_eq!(got.to_bits(), 1.1460000000000001_f64.to_bits(), "{got:?} lost its last bit");
        assert_ne!(got.to_bits(), 1.146_f64.to_bits());

        let bad = serde_json::json!({"tables": [{"name": "listener_play_history", "columns": ["play_id","mbid"],
            "key": ["play_id"], "rows": [{"key": [1], "was": [1,"r1"], "now": null}]}]});
        assert!(apply(&db, &bad.to_string()).unwrap_err().contains("not a shared table"));
        let plays: i64 = Connection::open(&db).unwrap()
            .query_row("SELECT count(*) FROM listener_play_history", [], |r| r.get(0)).unwrap();
        assert_eq!(plays, 1, "plays are never touched");
    }

    /// A catalogue-shaped file: a table with a machine-scope column the patch
    /// never names, and a blob.
    fn catalogue(path: &Path) {
        Connection::open(path).unwrap().execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, format TEXT, path TEXT);
             CREATE TABLE covers (file_id INTEGER PRIMARY KEY, image BLOB);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
                 start_ms INTEGER, end_ms INTEGER, gain_db REAL);
             INSERT INTO files VALUES (1,'m1','flac','/music/a.flac'), (2,'m2','mp3','/music/b.mp3');
             INSERT INTO covers VALUES (1, x'00ff10');
             INSERT INTO passages VALUES (10,1,'track',0,1000,-3.5), (11,2,'track',0,2000,NULL);",
        ).unwrap();
    }

    fn dump(path: &Path) -> Vec<String> {
        let c = Connection::open(path).unwrap();
        let mut out = Vec::new();
        let tables: Vec<String> = c.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").unwrap()
            .query_map([], |r| r.get(0)).unwrap().filter_map(Result::ok).collect();
        for t in tables {
            let cols = columns(&c, &t).unwrap();
            out.push(format!("{t}{cols:?}"));
            for r in rows(&c, &t, &cols).unwrap() {
                out.push(format!("{t} {r}"));
            }
        }
        out.sort();
        out
    }

    fn files_patch(format_was: &str) -> Json {
        serde_json::json!({"tables": [
            {"name": "files", "columns": ["file_id","audio_md5","format"], "key": ["file_id"], "rows": [
                {"key": [1], "was": [1,"m1",format_was], "now": [1,"m1","opus"]},
                {"key": [2], "was": [2,"m2","mp3"], "now": null},
                {"key": [3], "was": null, "now": [3,"m3","flac"], "also": {"path": "/music/c.flac"}}]},
            {"name": "covers", "columns": ["file_id","image"], "key": ["file_id"], "rows": [
                {"key": [1], "was": [1,{"b64":"AP8Q"}], "now": [1,{"b64":"AQID"}]}]}]})
    }

    /// `[SPEC-NSH-070]`: the catalogue's patch is all or nothing. One row not
    /// as the patch expects, and not even the rows before it are kept.
    #[test]
    fn a_strict_patch_writes_nothing_on_one_mismatch() {
        let d = tmp("strict");
        let db = d.join("library.db");
        catalogue(&db);
        let before = dump(&db);
        let err = apply_with(&db, &files_patch("wav"), None, Rule::Strict, true).unwrap_err();
        assert!(err.contains("not as the patch expects"), "{err}");
        assert_eq!(dump(&db), before, "nothing written, not even the rows before the mismatch");
        // The member rule on the same patch keeps that row and takes the rest.
        let m = apply_with(&db, &files_patch("wav"), None, Rule::Member, false).unwrap_err();
        assert!(m.contains("columns here"), "the member rule wants every column named: {m}");
    }

    /// A rehearsal checks and counts, and keeps nothing.
    #[test]
    fn a_rehearsal_counts_and_writes_nothing() {
        let d = tmp("rehearse");
        let db = d.join("library.db");
        catalogue(&db);
        let before = dump(&db);
        let r = apply_with(&db, &files_patch("flac"), None, Rule::Strict, false).unwrap();
        assert_eq!((r.counts.applied, r.counts.already, r.counts.kept), (4, 0, 0));
        assert_eq!(dump(&db), before, "a rehearsal leaves the file as it was");
    }

    /// `[SPEC-NSH-080]`: what was applied, reversed, is the backup -- an update,
    /// a deletion that must come back whole, an insertion, and a blob.
    #[test]
    fn the_inverse_puts_back_exactly_what_the_patch_changed() {
        let d = tmp("undo");
        let db = d.join("library.db");
        catalogue(&db);
        let before = dump(&db);
        let out = apply_with(&db, &files_patch("flac"), None, Rule::Strict, true).unwrap();
        assert_eq!(out.counts.applied, 4);
        let c = Connection::open(&db).unwrap();
        let (fmt, path): (String, String) =
            c.query_row("SELECT format, path FROM files WHERE file_id = 1", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!((fmt.as_str(), path.as_str()), ("opus", "/music/a.flac"), "the node's own path is untouched");
        let new: String = c.query_row("SELECT path FROM files WHERE file_id = 3", [], |r| r.get(0)).unwrap();
        assert_eq!(new, "/music/c.flac", "a new row takes its `also` columns");
        let img: Vec<u8> = c.query_row("SELECT image FROM covers WHERE file_id = 1", [], |r| r.get(0)).unwrap();
        assert_eq!(img, [1, 2, 3], "a blob arrives as bytes");
        drop(c);
        // Applying again finds every row already as it should be.
        let again = apply_with(&db, &files_patch("flac"), None, Rule::Strict, true).unwrap();
        assert_eq!((again.counts.applied, again.counts.already), (0, 4));
        undo(&db, &out.inverse).unwrap();
        assert_eq!(dump(&db), before, "undone to the byte, the deleted row's path included");
    }

    /// A table or column the file lacks is made by the catalogue's patch, and
    /// unmade by its undo; a member patch never makes one.
    #[test]
    fn schema_the_patch_adds_is_removed_by_its_undo() {
        let d = tmp("schema");
        let db = d.join("library.db");
        catalogue(&db);
        let before = dump(&db);
        let p = serde_json::json!({"tables": [
            {"name": "genres", "create": ["CREATE TABLE genres (genre_id INTEGER PRIMARY KEY, name TEXT)"],
             "columns": ["genre_id","name"], "key": ["genre_id"],
             "rows": [{"key": [1], "was": null, "now": [1,"jazz"]}]},
            {"name": "files", "add_columns": ["sha256 TEXT"], "columns": ["file_id","sha256"], "key": ["file_id"],
             "rows": [{"key": [1], "was": [1,null], "now": [1,"abc"]}]}]});
        let out = apply_with(&db, &p, None, Rule::Strict, true).unwrap();
        assert_eq!(out.counts.applied, 2);
        undo(&db, &out.inverse).unwrap();
        assert_eq!(dump(&db), before, "the new table and column are gone again");
        let member = apply_with(&db, &p, None, Rule::Member, true).unwrap_err();
        assert!(member.contains("columns here"), "a member patch makes no table: {member}");
    }

    /// Names are the only thing spliced into SQL, and a creating statement is
    /// one CREATE and nothing more.
    #[test]
    fn a_patch_cannot_smuggle_sql() {
        let d = tmp("smuggle");
        let db = d.join("library.db");
        catalogue(&db);
        let before = dump(&db);
        let bad_name = serde_json::json!({"tables": [{"name": "files; DROP TABLE files", "columns": ["file_id"],
            "key": ["file_id"], "rows": []}]});
        assert!(apply_with(&db, &bad_name, None, Rule::Strict, true).unwrap_err().contains("not a name"));
        let bad_create = serde_json::json!({"tables": [{"name": "x",
            "create": ["CREATE TABLE x (a INTEGER); DROP TABLE files"], "columns": ["a"], "key": ["a"], "rows": []}]});
        assert!(apply_with(&db, &bad_create, None, Rule::Strict, true).unwrap_err().contains("not a single CREATE"));
        let bad_decl = serde_json::json!({"tables": [{"name": "files", "add_columns": ["x TEXT); DROP TABLE files; --"],
            "columns": ["file_id"], "key": ["file_id"], "rows": []}]});
        assert!(apply_with(&db, &bad_decl, None, Rule::Strict, true).is_err());
        assert_eq!(dump(&db), before);
    }

    /// `[SPEC-NSH-050]`, `[SPEC-NSH-060]`: an appliance's share keeps its passage
    /// rows, and its summary says what those ids mean without the catalogue.
    #[test]
    fn an_export_keeps_passage_rows_and_the_summary_explains_them() {
        let d = tmp("export");
        let (lis, lib) = (d.join("listener.db"), d.join("library.db"));
        listener(&lis);
        catalogue(&lib);
        let e = export(&lis, &MERGED).unwrap();
        let names: Vec<&str> = e["tables"].as_array().unwrap().iter().map(|t| t["name"].as_str().unwrap()).collect();
        assert_eq!(names, ["listener_flags", "listener_preferences"], "held tables only; plays never");
        let flags = &e["tables"][0];
        assert_eq!(flags["rows"].as_array().unwrap().len(), 2, "the passage flag travels too");
        assert_eq!(flags["key"], serde_json::json!(["subject_kind", "subject_id"]));
        assert!(flags["create"][0].as_str().unwrap().starts_with("CREATE TABLE listener_flags"));
        let s = catalogue_summary(&lib).unwrap();
        assert_eq!(s["tables"][0]["columns"], serde_json::json!(["file_id", "audio_md5", "path"]),
                   "the machine-scope columns the file has");
        assert_eq!(s["tables"][1]["rows"][0], serde_json::json!([10, 1, "track", 0, 1000]));
        let text = s.to_string();
        assert!(!text.contains("\"flac\"") && !text.contains("-3.5"), "no hub-authored column leaves: {s}");
    }

    // ---- the catalogue patch by natural key [SPEC-NKP-010..075] ----------

    /// A catalogue as a node holds it. `files` are (file_id, audio_md5), `passages`
    /// (passage_id, file_id, start, end) -- all radio -- and `recs` (passage_id, mbid).
    fn numbered(path: &Path, files: &[(i64, &str)], passages: &[(i64, i64, i64, i64)], recs: &[(i64, &str)]) {
        let c = Connection::open(path).unwrap();
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT NOT NULL UNIQUE, path TEXT, first_seen TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL, kind TEXT NOT NULL,
                 start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL, gain REAL);
             CREATE UNIQUE INDEX passages_span ON passages(file_id, kind, start_ms, end_ms);
             CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT NOT NULL, weight REAL,
                 PRIMARY KEY (passage_id, mbid));",
        )
        .unwrap();
        for (id, md5) in files {
            c.execute("INSERT INTO files VALUES (?1, ?2, ?3, 'node-time')", params![id, md5, format!("/node/{md5}")]).unwrap();
        }
        for (id, f, s, e) in passages {
            c.execute("INSERT INTO passages VALUES (?1, ?2, 'radio', ?3, ?4, NULL)", params![id, f, s, e]).unwrap();
        }
        for (p, m) in recs {
            c.execute("INSERT INTO passage_recordings VALUES (?1, ?2, 1.0)", params![p, m]).unwrap();
        }
    }

    /// The same catalogue named without ids, so two numbered differently compare equal.
    fn nform(path: &Path) -> Vec<String> {
        let c = Connection::open(path).unwrap();
        let mut out: Vec<String> = Vec::new();
        let mut q = c.prepare("SELECT audio_md5 FROM files").unwrap();
        out.extend(q.query_map([], |r| Ok(format!("file {}", r.get::<_, String>(0)?))).unwrap().filter_map(Result::ok));
        let mut q = c
            .prepare("SELECT f.audio_md5, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id)")
            .unwrap();
        out.extend(
            q.query_map([], |r| Ok(format!("passage {} {}-{}", r.get::<_, String>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?)))
                .unwrap()
                .filter_map(Result::ok),
        );
        let mut q = c
            .prepare("SELECT f.audio_md5, p.start_ms, p.end_ms, r.mbid FROM passage_recordings r JOIN passages p USING (passage_id) JOIN files f USING (file_id)")
            .unwrap();
        out.extend(
            q.query_map([], |r| {
                Ok(format!("rec {} {}-{} {}", r.get::<_, String>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?, r.get::<_, String>(3)?))
            })
            .unwrap()
            .filter_map(Result::ok),
        );
        out.sort();
        out
    }

    /// Everything in it, ids included, to prove a refused patch changed nothing.
    fn raw(path: &Path) -> String {
        let c = Connection::open(path).unwrap();
        let mut s = String::new();
        for t in ["files", "passages", "passage_recordings"] {
            let mut q = c.prepare(&format!("SELECT * FROM {t} ORDER BY 1, 2")).unwrap();
            let n = q.column_count();
            let rows: Vec<String> = q
                .query_map([], |r| Ok((0..n).map(|i| format!("{:?}", r.get::<_, Value>(i).unwrap())).collect::<Vec<_>>().join(",")))
                .unwrap()
                .filter_map(Result::ok)
                .collect();
            s += &format!("{t}: {rows:?}\n");
        }
        s
    }

    fn files_entry(rows: Json) -> Json {
        serde_json::json!({"name": "files", "natural": {"surrogate": "file_id", "refs": []},
                           "columns": ["audio_md5", "path"], "key": ["audio_md5"], "rows": rows})
    }

    fn passages_entry(rows: Json) -> Json {
        serde_json::json!({"name": "passages", "natural": {"surrogate": "passage_id", "refs": [{"col": "file_id", "kind": "file"}]},
                           "columns": ["file_id", "kind", "start_ms", "end_ms"], "key": ["file_id", "kind", "start_ms", "end_ms"], "rows": rows})
    }

    fn recs_entry(rows: Json) -> Json {
        serde_json::json!({"name": "passage_recordings",
                           "natural": {"surrogate": null, "refs": [{"col": "passage_id", "kind": "passage"}]},
                           "columns": ["passage_id", "mbid", "weight"], "key": ["passage_id", "mbid"], "rows": rows})
    }

    /// The hub numbers A, B, C as 1, 2, 3; this node numbers them 1, 3, 2.
    fn permuted(name: &str) -> (std::path::PathBuf, std::path::PathBuf) {
        let d = tmp(name);
        let db = d.join("library.db");
        numbered(&db, &[(1, "A"), (3, "B"), (2, "C")], &[(10, 1, 0, 1000), (11, 3, 0, 500), (12, 2, 0, 700)],
                  &[(10, "m-a"), (11, "m-b"), (12, "m-c")]);
        (d, db)
    }

    fn apply_n(db: &Path, entries: Vec<Json>, commit: bool) -> Result<Outcome, String> {
        apply_with(db, &serde_json::json!({ "tables": entries }), None, Rule::Strict, commit)
    }

    /// A new file, its passage and its recording arrive under the node's own ids, the
    /// children resolved against the parents the same patch has just written.
    #[test]
    fn a_natural_patch_adds_what_is_new_under_the_nodes_own_ids() {
        let (_d, db) = permuted("nk-add");
        let span = serde_json::json!(["D", "radio", 0, 900]);
        let out = apply_n(&db, vec![
            files_entry(serde_json::json!([{"key": ["D"], "was": null, "now": ["D", "/hub/d"], "also": {"first_seen": "hub-time"}}])),
            passages_entry(serde_json::json!([{"key": ["D", "radio", 0, 900], "was": null, "now": ["D", "radio", 0, 900]}])),
            recs_entry(serde_json::json!([{"key": [span, "m-d"], "was": null, "now": [span, "m-d", 1.0]}])),
        ], true).unwrap();
        assert_eq!((out.counts.applied, out.counts.already, out.counts.kept), (3, 0, 0), "{:?}", out.counts);
        let c = Connection::open(&db).unwrap();
        let id: i64 = c.query_row("SELECT file_id FROM files WHERE audio_md5 = 'D'", [], |r| r.get(0)).unwrap();
        assert_eq!(id, 4, "the node numbers it as its own database counts, never as the hub did");
        let first: String = c.query_row("SELECT first_seen FROM files WHERE audio_md5 = 'D'", [], |r| r.get(0)).unwrap();
        assert_eq!(first, "hub-time", "a column outside the patch comes from `also`, for a row that is new");
        assert!(nform(&db).contains(&"rec D 0-900 m-d".to_string()), "{:?}", nform(&db));
    }

    /// The case found on every player: the node already holds the music, under other ids.
    #[test]
    fn rows_the_node_already_holds_under_other_ids_are_already_there() {
        let (_d, db) = permuted("nk-already");
        let before = raw(&db);
        let b = serde_json::json!(["B", "radio", 0, 500]);
        let out = apply_n(&db, vec![
            files_entry(serde_json::json!([{"key": ["B"], "was": null, "now": ["B", "/node/B"]}])),
            passages_entry(serde_json::json!([{"key": ["B", "radio", 0, 500], "was": null, "now": ["B", "radio", 0, 500]}])),
            recs_entry(serde_json::json!([{"key": [b, "m-b"], "was": null, "now": [b, "m-b", 1.0]}])),
        ], true).unwrap();
        assert_eq!((out.counts.applied, out.counts.already), (0, 3), "{:?}", out.counts);
        assert_eq!(raw(&db), before, "nothing was written");
    }

    /// A boundary edit changes a passage's natural key. The node updates the row it
    /// has, so its id, and whatever of its own points at that id, stay.
    #[test]
    fn a_changed_key_is_an_update_in_place_and_undo_puts_it_back() {
        let (_d, db) = permuted("nk-key");
        let before = raw(&db);
        let out = apply_n(&db, vec![passages_entry(serde_json::json!([
            {"key": ["B", "radio", 0, 500], "was": ["B", "radio", 0, 500], "now": ["B", "radio", 0, 450]}]))], true).unwrap();
        assert_eq!(out.counts.applied, 1);
        let c = Connection::open(&db).unwrap();
        let (id, end): (i64, i64) = c.query_row("SELECT passage_id, end_ms FROM passages WHERE file_id = 3", [], |r| Ok((r.get(0)?, r.get(1)?))).unwrap();
        assert_eq!((id, end), (11, 450), "the same local row, its span changed");
        let child: i64 = c.query_row("SELECT passage_id FROM passage_recordings WHERE mbid = 'm-b'", [], |r| r.get(0)).unwrap();
        assert_eq!(child, 11, "and its recording still points at it, untouched");
        drop(c);
        undo(&db, &out.inverse).unwrap();
        assert_eq!(raw(&db), before, "the undo restores every id and value");
    }

    /// One row not as the patch expects refuses the whole patch, in the check, before a
    /// single write -- including the valid row that came first.
    #[test]
    fn one_row_not_as_expected_writes_nothing() {
        let (_d, db) = permuted("nk-refuse");
        let before = raw(&db);
        let err = apply_n(&db, vec![
            files_entry(serde_json::json!([{"key": ["D"], "was": null, "now": ["D", "/hub/d"]}])),
            passages_entry(serde_json::json!([{"key": ["A", "radio", 0, 1000], "was": ["A", "radio", 0, 999], "now": ["A", "radio", 0, 800]}])),
        ], true).unwrap_err();
        assert!(err.contains("not as the patch expects"), "{err}");
        assert_eq!(raw(&db), before, "nothing written, the earlier insert included");
        let err = apply_n(&db, vec![recs_entry(serde_json::json!([
            {"key": [["Z", "radio", 0, 1], "m-z"], "was": null, "now": [["Z", "radio", 0, 1], "m-z", 1.0]}]))], true).unwrap_err();
        assert!(err.contains("does not hold"), "a child of a passage the node lacks is refused, not guessed: {err}");
        assert_eq!(raw(&db), before);
    }

    #[test]
    fn a_rehearsal_of_a_natural_patch_writes_nothing() {
        let (_d, db) = permuted("nk-rehearse");
        let before = raw(&db);
        let out = apply_n(&db, vec![files_entry(serde_json::json!([{"key": ["D"], "was": null, "now": ["D", "/hub/d"]}]))], false).unwrap();
        assert_eq!(out.counts.applied, 1, "it says what it would do");
        assert_eq!(raw(&db), before, "and does none of it");
    }

    #[test]
    fn a_natural_entry_must_be_in_the_vocabulary_and_the_catalogues() {
        let (_d, db) = permuted("nk-vocab");
        let mut bad = passages_entry(serde_json::json!([]));
        bad["natural"]["refs"][0]["kind"] = serde_json::json!("recording");
        assert!(apply_n(&db, vec![bad], true).unwrap_err().contains("not a kind of reference"));
        let mut carried = passages_entry(serde_json::json!([]));
        carried["columns"] = serde_json::json!(["passage_id", "file_id", "kind", "start_ms", "end_ms"]);
        assert!(apply_n(&db, vec![carried], true).unwrap_err().contains("is carried"));
        let mut creates = files_entry(serde_json::json!([]));
        creates["create"] = serde_json::json!(["CREATE TABLE x (a)"]);
        assert!(apply_n(&db, vec![creates], true).unwrap_err().contains("makes no table"));
        let member = apply_with(&db, &serde_json::json!({"tables": [files_entry(serde_json::json!([]))]}), None, Rule::Member, true);
        assert!(member.unwrap_err().contains("strict rule"), "a natural entry is the catalogue's alone");
        let shared = apply_with(&db, &serde_json::json!({"tables": [files_entry(serde_json::json!([]))]}), Some(&SHARED), Rule::Strict, true);
        assert!(shared.is_err(), "and never a member's shared tables");
    }

    /// A column the hub has gained since the last sync is added to the node's table as the
    /// id-keyed path adds one: in the transaction, rolled back by a rehearsal, dropped by the undo.
    #[test]
    fn a_natural_entry_may_add_a_column() {
        let (_d, db) = permuted("nk-addcol");
        let before = raw(&db);
        let mut e = passages_entry(serde_json::json!([
            {"key": ["B", "radio", 0, 500], "was": ["B", "radio", 0, 500, 0], "now": ["B", "radio", 0, 500, 1]}]));
        e["columns"] = serde_json::json!(["file_id", "kind", "start_ms", "end_ms", "held"]);
        e["add_columns"] = serde_json::json!(["held INTEGER DEFAULT 0"]);
        let rehearsed = apply_n(&db, vec![e.clone()], false).unwrap();
        assert_eq!(rehearsed.counts.applied, 1);
        assert_eq!(raw(&db), before, "a rehearsal adds no column and writes no row");
        let out = apply_n(&db, vec![e], true).unwrap();
        let c = Connection::open(&db).unwrap();
        let held: Vec<i64> = c.prepare("SELECT held FROM passages ORDER BY passage_id").unwrap()
            .query_map([], |r| r.get(0)).unwrap().filter_map(Result::ok).collect();
        assert_eq!(held, [0, 1, 0], "the other passages read the default, the one the patch names its new value");
        drop(c);
        undo(&db, &out.inverse).unwrap();
        assert_eq!(raw(&db), before, "and the undo takes the column away again");
    }

    /// A row the hub removed goes, with its own id; the other rows keep theirs.
    #[test]
    fn a_natural_delete_removes_the_nodes_own_row() {
        let (_d, db) = permuted("nk-delete");
        let out = apply_n(&db, vec![
            recs_entry(serde_json::json!([{"key": [["C", "radio", 0, 700], "m-c"], "was": [["C", "radio", 0, 700], "m-c", 1.0], "now": null}])),
            passages_entry(serde_json::json!([{"key": ["C", "radio", 0, 700], "was": ["C", "radio", 0, 700], "now": null}])),
        ], true).unwrap();
        assert_eq!(out.counts.applied, 2);
        let n = nform(&db);
        assert!(!n.iter().any(|x| x.contains("C 0-700")) && n.contains(&"passage A 0-1000".to_string()), "{n:?}");
        let c = Connection::open(&db).unwrap();
        let left: i64 = c.query_row("SELECT count(*) FROM passages WHERE passage_id = 12", [], |r| r.get(0)).unwrap();
        assert_eq!(left, 0);
    }

    /// The fixture both languages read: the Python builder's patch, applied by this
    /// applier to a node numbered differently, gives what the Python reference gave
    /// `[SPEC-NKP-090]`.
    #[test]
    fn the_builders_patch_applied_here_gives_the_same_catalogue() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/natural_patch");
        let read = |n: &str| std::fs::read_to_string(dir.join(n)).unwrap_or_else(|e| panic!("{n}: {e}"));
        let d = tmp("nk-fixture");
        let db = d.join("library.db");
        let c = Connection::open(&db).unwrap();
        c.execute_batch(&read("schema.sql")).unwrap();
        c.execute_batch(&read("node.sql")).unwrap();
        drop(c);
        let before = raw(&db);
        let patch: Json = serde_json::from_str(&read("patch.json")).unwrap();
        let out = apply_with(&db, &patch, None, Rule::Strict, true).unwrap();
        let expected: Vec<String> = serde_json::from_str(&read("expected.json")).unwrap();
        let c = Connection::open(&db).unwrap();
        let got = {
            let mut got: Vec<String> = Vec::new();
            let mut q = c.prepare("SELECT audio_md5 FROM files").unwrap();
            got.extend(q.query_map([], |r| Ok(format!("file {}", r.get::<_, String>(0)?))).unwrap().filter_map(Result::ok));
            let mut q = c.prepare("SELECT f.audio_md5, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id)").unwrap();
            got.extend(
                q.query_map([], |r| Ok(format!("passage {} {}-{}", r.get::<_, String>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?)))
                    .unwrap()
                    .filter_map(Result::ok),
            );
            let mut q = c
                .prepare("SELECT f.audio_md5, p.start_ms, p.end_ms, r.mbid, r.weight FROM passage_recordings r JOIN passages p USING (passage_id) JOIN files f USING (file_id)")
                .unwrap();
            got.extend(
                q.query_map([], |r| {
                    Ok(format!("rec {} {}-{} {} {:?}", r.get::<_, String>(0)?, r.get::<_, i64>(1)?, r.get::<_, i64>(2)?, r.get::<_, String>(3)?, r.get::<_, f64>(4)?))
                })
                .unwrap()
                .filter_map(Result::ok),
            );
            got.sort();
            got
        };
        assert_eq!(got, expected, "the same catalogue as the Python reference gives");
        let (d_id, c2_id, b_id): (i64, i64, i64) = (
            c.query_row("SELECT file_id FROM files WHERE audio_md5 = 'D'", [], |r| r.get(0)).unwrap(),
            c.query_row("SELECT file_id FROM files WHERE audio_md5 = 'C2'", [], |r| r.get(0)).unwrap(),
            c.query_row("SELECT file_id FROM files WHERE audio_md5 = 'B'", [], |r| r.get(0)).unwrap(),
        );
        assert_eq!((d_id, c2_id, b_id), (4, 2, 3), "the node numbered the new file itself and kept its ids for the rest");
        drop(c);
        let applied = out.counts.applied;
        assert!(applied > 0 && out.counts.already == 0, "{:?}", out.counts);
        let again = apply_with(&db, &patch, None, Rule::Strict, true).unwrap();
        assert_eq!((again.counts.applied, again.counts.already), (0, applied), "a second apply finds it all already there");
        undo(&db, &out.inverse).unwrap();
        assert_eq!(raw(&db), before, "and the undo of the first puts back every id and value");
    }

    // ---- a patch in parts [SPEC-NKP-920..940] -----------------------------

    fn three_parts() -> Vec<Json> {
        let span = serde_json::json!(["D", "radio", 0, 900]);
        vec![
            serde_json::json!({"tables": [files_entry(serde_json::json!([{"key": ["D"], "was": null, "now": ["D", "/hub/d"], "also": {"first_seen": "t"}}]))]}),
            serde_json::json!({"tables": [passages_entry(serde_json::json!([{"key": ["D", "radio", 0, 900], "was": null, "now": ["D", "radio", 0, 900]}]))]}),
            serde_json::json!({"tables": [recs_entry(serde_json::json!([{"key": [span, "m-d"], "was": null, "now": [span, "m-d", 1.0]}]))]}),
        ]
    }

    fn run_parts(db: &Path, parts: Vec<Json>, commit: bool, inverses: &mut Vec<Json>) -> Result<Applied, String> {
        let mut it = parts.into_iter().map(Ok);
        apply_parts(db, &mut it, None, Rule::Strict, commit, &mut |_, inv| {
            inverses.push(inv.clone());
            Ok(())
        })
    }

    /// Each part is checked against the database as the parts before it left it, in one
    /// transaction: a child in the third part finds the parent the first part wrote.
    #[test]
    fn parts_apply_in_order_in_one_transaction() {
        let (_d, db) = permuted("nk-parts");
        let mut inv = Vec::new();
        let got = run_parts(&db, three_parts(), true, &mut inv).unwrap();
        assert_eq!((got.applied, got.already), (3, 0));
        assert!(nform(&db).contains(&"rec D 0-900 m-d".to_string()), "{:?}", nform(&db));
        assert_eq!(inv.len(), 3, "each part's inverse was handed over as it was made, not kept");
    }

    #[test]
    fn a_bad_row_in_a_later_part_writes_nothing_from_any() {
        let (_d, db) = permuted("nk-parts-bad");
        let before = raw(&db);
        let mut parts = three_parts();
        parts[2]["tables"][0]["rows"][0]["key"] = serde_json::json!([["Z", "radio", 0, 1], "m-d"]);
        parts[2]["tables"][0]["rows"][0]["now"] = serde_json::json!([["Z", "radio", 0, 1], "m-d", 1.0]);
        let err = run_parts(&db, parts, true, &mut Vec::new()).unwrap_err();
        assert!(err.starts_with("part 3:"), "the part is named: {err}");
        assert_eq!(raw(&db), before, "and the first two parts were rolled back with it");
    }

    #[test]
    fn a_rehearsal_of_parts_writes_nothing_and_the_undo_is_one_transaction() {
        let (_d, db) = permuted("nk-parts-undo");
        let before = raw(&db);
        let got = run_parts(&db, three_parts(), false, &mut Vec::new()).unwrap();
        assert_eq!(got.applied, 3);
        assert_eq!(raw(&db), before, "a rehearsal keeps nothing");
        let mut inv = Vec::new();
        run_parts(&db, three_parts(), true, &mut inv).unwrap();
        assert_ne!(raw(&db), before);
        let mut back = inv.into_iter().rev().map(Ok);
        undo_parts(&db, &mut back).unwrap();
        assert_eq!(raw(&db), before, "the undo puts back every row, last part first");
    }
}
