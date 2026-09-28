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
    let mut c = Connection::open_with_flags(listener, OpenFlags::SQLITE_OPEN_READ_WRITE)
        .map_err(|e| format!("open {}: {e}", listener.display()))?;
    c.busy_timeout(std::time::Duration::from_secs(10)).map_err(|e| e.to_string())?;
    let tx = c.transaction().map_err(|e| e.to_string())?;
    let mut rep = Applied::default();
    for t in p["tables"].as_array().ok_or("a patch has tables")? {
        let name = t["name"].as_str().ok_or("a table has a name")?;
        if !SHARED.contains(&name) {
            return Err(format!("{name} is not a shared table: refused, and nothing written"));
        }
        let strs = |k: &str| -> Result<Vec<String>, String> {
            t[k].as_array()
                .ok_or(format!("{name}: no {k}"))?
                .iter()
                .map(|v| v.as_str().map(str::to_string).ok_or(format!("{name}: {k} holds a non-name")))
                .collect()
        };
        let (cols, key) = (strs("columns")?, strs("key")?);
        let mut have = columns(&tx, name)?;
        let mut want = cols.clone();
        have.sort();
        want.sort();
        if have != want {
            return Err(format!("{name}: columns here {have:?} are not the patch's {want:?}; nothing written"));
        }
        let wh = key.iter().enumerate().map(|(i, k)| format!("{k} IS ?{}", i + 1)).collect::<Vec<_>>().join(" AND ");
        let select = format!("SELECT {} FROM {name} WHERE {wh} LIMIT 1", cols.join(", "));
        let delete = format!("DELETE FROM {name} WHERE {wh}");
        let insert = format!(
            "INSERT INTO {name} ({}) VALUES ({})",
            cols.join(", "),
            (1..=cols.len()).map(|i| format!("?{i}")).collect::<Vec<_>>().join(", ")
        );
        for r in t["rows"].as_array().ok_or(format!("{name}: no rows"))? {
            let k: Vec<Value> = r["key"].as_array().ok_or("a row has a key")?.iter().map(to_sql).collect();
            let cur: Option<Vec<Value>> = tx
                .query_row(&select, params_from_iter(k.iter()), |row| {
                    (0..cols.len()).map(|i| row.get::<_, Value>(i)).collect()
                })
                .ok();
            let now = row_of(&r["now"]);
            let was = row_of(&r["was"]);
            if same(&cur, &now) {
                rep.already += 1;
            } else if same(&cur, &was) {
                tx.execute(&delete, params_from_iter(k.iter())).map_err(|e| format!("{name}: {e}"))?;
                if let Some(v) = &now {
                    tx.execute(&insert, params_from_iter(v.iter())).map_err(|e| format!("{name}: {e}"))?;
                }
                rep.applied += 1;
            } else {
                rep.kept += 1;
            }
        }
    }
    tx.commit().map_err(|e| e.to_string())?;
    Ok(rep)
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
}
