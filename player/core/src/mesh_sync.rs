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

use std::path::Path;

use rusqlite::types::Value;
use rusqlite::{params_from_iter, Connection, OpenFlags};
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
/// `MACHINE_SCOPE` in `tools/star_merge.py`. They travel in neither direction
/// `[SPEC-NSH-070]`; the summary reports them so the hub keeps them as they are.
pub const MACHINE_SCOPE: [&str; 4] = ["path", "size_bytes", "mtime", "last_seen"];

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
    let mut c = Connection::open_with_flags(db, OpenFlags::SQLITE_OPEN_READ_WRITE)
        .map_err(|e| format!("open {}: {e}", db.display()))?;
    c.busy_timeout(std::time::Duration::from_secs(10)).map_err(|e| e.to_string())?;
    let tx = c.transaction().map_err(|e| e.to_string())?;
    let entries = inverse["tables"].as_array().ok_or("an inverse has tables")?;
    let rows: Vec<&Json> = entries.iter().filter(|t| t["drop_table"] != true).collect();
    let counts = apply_tx(&tx, &serde_json::json!({ "tables": rows }), None, Rule::Strict)?.counts;
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
    tx.commit().map_err(|e| e.to_string())?;
    Ok(counts)
}

fn apply_tx(tx: &Connection, patch: &Json, allowed: Option<&[&str]>, rule: Rule) -> Result<Outcome, String> {
    let mut counts = Applied::default();
    let mut inverse = Vec::new();
    for t in patch["tables"].as_array().ok_or("a patch has tables")? {
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
}
