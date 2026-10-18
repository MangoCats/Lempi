//! The programme a node runs `[SPEC062]`: its time slots, their seeds, and
//! the record of what it is called and where it came from.
//!
//! The words are SPEC023's `[ENT-PROGRAMME-010]`, `[ENT-SLOT-010]`,
//! `[ENT-SEED-010]`: a **programme** is the whole day, a **time slot** one
//! timed part of it, a **seed** a recording a slot names. The tables keep
//! their older names `[SPEC-VOC-020]`: `listener_programs` holds the time
//! slots, `listener_program_seeds` their seeds, and `listener_programme`
//! (new, one row) what the whole is called.

use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use super::{DbError, PlayerStore};

/// The slot and seed tables as `sql/schema.sql` defines them, and the record
/// of the programme. Created here because a listener file the player made
/// for itself has never been through `split_database.py`, which is where the
/// slot tables used to come from: the phone's first start, say.
pub(super) fn ensure_programme_tables(conn: &Connection) -> Result<(), DbError> {
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS listener_programs (
             program_id  INTEGER PRIMARY KEY,
             name        TEXT NOT NULL UNIQUE,
             start_time  TEXT
         );
         CREATE TABLE IF NOT EXISTS listener_program_seeds (
             program_id  INTEGER NOT NULL REFERENCES listener_programs(program_id) ON DELETE CASCADE,
             mbid        TEXT    NOT NULL,
             position    INTEGER NOT NULL,
             PRIMARY KEY (program_id, mbid)
         ) WITHOUT ROWID;
         CREATE TABLE IF NOT EXISTS listener_programme (
             id          INTEGER PRIMARY KEY CHECK (id = 1),
             name        TEXT,
             fingerprint TEXT NOT NULL,
             source      TEXT NOT NULL CHECK (source IN ('node', 'hub')),
             hub_version INTEGER,
             updated_at  TEXT NOT NULL
         );
         CREATE TABLE IF NOT EXISTS listener_programme_pending (
             id          INTEGER PRIMARY KEY CHECK (id = 1),
             name        TEXT NOT NULL,
             hub_version INTEGER NOT NULL,
             fingerprint TEXT NOT NULL,
             slots       TEXT NOT NULL,
             arrived_at  TEXT NOT NULL,
             answer      TEXT CHECK (answer IN ('kept')),
             answered_at TEXT
         );",
    )
    .map_err(|e| DbError::Open(e.to_string()))
}

/// One time slot `[ENT-SLOT-010]`, as the page edits it and the fingerprint
/// reads it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Slot {
    /// Its row in `listener_programs`. Kept across an edit, so that what
    /// `selection_decisions` recorded under it still names it; `None` for a
    /// slot the edit adds.
    #[serde(default)]
    pub id: Option<i64>,
    pub name: String,
    /// `HH:MM`, wall-clock; `None` is a slot chosen only by hand.
    #[serde(default)]
    pub start: Option<String>,
    /// Recording mbids, in order `[ENT-SEED-010]`.
    #[serde(default)]
    pub seeds: Vec<String>,
}

/// The programme as a node holds it, for the page and the picker.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ProgrammeView {
    /// `None` until someone names it.
    pub name: Option<String>,
    /// `hub` while it is the hub's version unchanged, `node` once it is
    /// edited here or was never the hub's.
    pub source: String,
    pub hub_version: Option<i64>,
    /// SHA-256 of [`canonical`], hex `[SPEC-PGM-110]`.
    pub fingerprint: String,
    /// In start-time order, slots chosen only by hand last.
    pub slots: Vec<Slot>,
}

/// A programme the hub sent this node `[SPEC-PGM-300]`: the version of the one
/// assigned to it, by name, with its whole content.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Arrival {
    pub name: String,
    pub version: i64,
    pub slots: Vec<Slot>,
}

/// An arrival waiting for this node's answer, or one it kept its own over
/// `[SPEC-PGM-050]`, kept so the question survives a restart and is not
/// asked again about the same version `[SPEC-PGM-200]`.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Pending {
    pub name: String,
    pub hub_version: i64,
    pub fingerprint: String,
    pub slots: Vec<Slot>,
    pub arrived_at: String,
    /// Answered "Keep this node's"; no longer asked.
    pub kept: bool,
}

/// What an arrival did.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Arrived {
    /// The node already runs exactly this content: now recorded as the hub's
    /// version, and nothing asked `[SPEC-PGM-110]`.
    AlreadyRuns,
    /// Waiting for a person's answer.
    Asking,
    /// This version was declined before; not asked again.
    KeptBefore,
}

/// `HH:MM` back from any of the forms a person types (`9:05`, `09:05`), or
/// `None` if it is not a time of day.
pub fn normalize_start(s: &str) -> Option<String> {
    let (h, m) = s.trim().split_once(':')?;
    let (h, m): (u32, u32) = (h.trim().parse().ok()?, m.trim().parse().ok()?);
    (h < 24 && m < 60).then(|| format!("{h:02}:{m:02}"))
}

fn ordered(slots: &[Slot]) -> Vec<&Slot> {
    let mut v: Vec<&Slot> = slots.iter().collect();
    // Timed slots by start, then those chosen only by hand; a name breaks a
    // tie so the order never depends on how the rows came back.
    v.sort_by(|a, b| {
        (a.start.is_none(), &a.start, a.name.to_lowercase())
            .cmp(&(b.start.is_none(), &b.start, b.name.to_lowercase()))
    });
    v
}

/// The one canonical form of a programme's content `[SPEC-PGM-110]`.
///
/// A JSON array, one object per slot in start-time order (slots chosen only
/// by hand last, by name), each with exactly the keys `name`, `seeds`,
/// `start` in that order, no spaces, UTF-8 unescaped. That is what Python's
/// `json.dumps(slots, sort_keys=True, separators=(",", ":"),
/// ensure_ascii=False)` writes, so the hub's tools compute the same
/// fingerprint without sharing this code. The programme's own name and the
/// slots' row ids are not content: a copy under another name, or on another
/// node, is the same programme by this measure.
///
/// **A slot's seeds are a set, so they are sorted here** `[SPEC-PGM-115]`.
/// The Director never reads a seed's position: it takes one per artist, the
/// least recently played, at most `MAX_SEEDS` `[SPEC-DIR-140]`. The first
/// form kept their stored order, so moving a seed up a list -- which the page
/// briefly offered -- made a programme "differ" with nothing about what plays
/// changed. Found by the maintainer's question, 2026-10-09.
pub fn canonical(slots: &[Slot]) -> String {
    #[derive(Serialize)]
    struct C<'a> {
        name: &'a str,
        seeds: Vec<&'a str>,
        start: Option<&'a str>,
    }
    let v: Vec<C<'_>> = ordered(slots)
        .into_iter()
        .map(|s| {
            let mut seeds: Vec<&str> = s.seeds.iter().map(String::as_str).collect();
            seeds.sort_unstable();
            seeds.dedup();
            C { name: &s.name, seeds, start: s.start.as_deref() }
        })
        .collect();
    serde_json::to_string(&v).unwrap_or_default()
}

/// SHA-256 of [`canonical`], lowercase hex `[SPEC-PGM-110]`.
pub fn fingerprint(slots: &[Slot]) -> String {
    let d = Sha256::digest(canonical(slots).as_bytes());
    d.iter().map(|b| format!("{b:02x}")).collect()
}

const NAME_MAX: usize = 100;
const SEEDS_MAX: usize = 50;

/// Checks a programme a person submitted, and returns it tidied: names and
/// starts trimmed and normalized. Every refusal is a sentence for the page
/// to show as it is.
pub fn validate(name: Option<&str>, slots: &[Slot]) -> Result<(Option<String>, Vec<Slot>), String> {
    let name = name.map(str::trim).filter(|n| !n.is_empty()).map(str::to_string);
    if name.as_deref().is_some_and(|n| n.chars().count() > NAME_MAX) {
        return Err(format!("A programme's name is at most {NAME_MAX} characters."));
    }
    let mut out = Vec::with_capacity(slots.len());
    let mut names = std::collections::HashSet::new();
    let mut starts = std::collections::HashSet::new();
    let mut ids = std::collections::HashSet::new();
    for s in slots {
        let n = s.name.trim();
        if n.is_empty() {
            return Err("Every time slot needs a name.".into());
        }
        if n.chars().count() > NAME_MAX {
            return Err(format!("A time slot's name is at most {NAME_MAX} characters."));
        }
        if !names.insert(n.to_lowercase()) {
            return Err(format!("Two time slots are called \"{n}\"; each needs its own name."));
        }
        let start = match s.start.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
            None => None,
            Some(t) => Some(normalize_start(t).ok_or_else(|| {
                format!("\"{t}\" is not a time of day; write it as HH:MM, e.g. 22:00.")
            })?),
        };
        if let Some(t) = &start {
            if !starts.insert(t.clone()) {
                return Err(format!("Two time slots start at {t}; only one can come on air then."));
            }
        }
        if let Some(id) = s.id {
            if !ids.insert(id) {
                return Err("The same time slot appears twice.".into());
            }
        }
        let mut seeds = Vec::new();
        for m in &s.seeds {
            let m = m.trim();
            if m.is_empty() || seeds.iter().any(|x: &String| x == m) {
                continue;
            }
            seeds.push(m.to_string());
        }
        if seeds.len() > SEEDS_MAX {
            return Err(format!("\"{n}\" has {} seeds; a time slot holds at most {SEEDS_MAX}.", seeds.len()));
        }
        out.push(Slot { id: s.id, name: n.to_string(), start, seeds });
    }
    Ok((name, out))
}

fn q(e: rusqlite::Error) -> DbError {
    DbError::Query(e.to_string())
}

fn read_slots(conn: &Connection) -> Result<Vec<Slot>, DbError> {
    let mut slots: Vec<Slot> = conn
        .prepare("SELECT program_id, name, start_time FROM listener_programs")
        .map_err(q)?
        .query_map([], |r| {
            Ok(Slot {
                id: Some(r.get(0)?),
                name: r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                start: r.get::<_, Option<String>>(2)?.and_then(|t| normalize_start(&t)),
                seeds: Vec::new(),
            })
        })
        .map_err(q)?
        .collect::<Result<_, _>>()
        .map_err(q)?;
    let mut st = conn
        .prepare("SELECT program_id, mbid FROM listener_program_seeds ORDER BY program_id, position")
        .map_err(q)?;
    let rows = st
        .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
        .map_err(q)?;
    for row in rows {
        let (id, mbid) = row.map_err(q)?;
        if let Some(s) = slots.iter_mut().find(|s| s.id == Some(id)) {
            s.seeds.push(mbid);
        }
    }
    Ok(ordered(&slots).into_iter().cloned().collect())
}

impl PlayerStore {
    /// The programme this node runs `[SPEC-PGM-200]`.
    ///
    /// The first time it is asked, the record is made: unnamed, the node's
    /// own, with the fingerprint of the slots it already had. If the slots
    /// have changed since the record was written -- by a tool, by hand --
    /// what it runs is no longer what the record says it was given, so it
    /// reads as the node's own.
    pub fn programme(&self) -> Result<ProgrammeView, DbError> {
        let slots = read_slots(&self.conn)?;
        let fp = fingerprint(&slots);
        let rec: Option<(Option<String>, String, String, Option<i64>)> = self
            .conn
            .query_row(
                "SELECT name, fingerprint, source, hub_version FROM listener_programme WHERE id = 1",
                [],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
            )
            .ok();
        let (name, source, hub_version) = match rec {
            None => {
                self.conn
                    .execute(
                        "INSERT INTO listener_programme (id, name, fingerprint, source, updated_at)
                         VALUES (1, NULL, ?1, 'node', datetime('now'))",
                        [&fp],
                    )
                    .map_err(q)?;
                (None, "node".to_string(), None)
            }
            Some((name, recorded, source, v)) if recorded == fp => (name, source, v),
            // Different content from what the record says it was given: the
            // node's own now, and the record says so, with the fingerprint it
            // has -- which also brings a fingerprint taken in the first,
            // seed-order form up to date `[SPEC-PGM-115]`.
            Some((name, _, _, _)) => {
                self.conn
                    .execute(
                        "UPDATE listener_programme SET fingerprint = ?1, source = 'node',
                             hub_version = NULL, updated_at = datetime('now') WHERE id = 1",
                        [&fp],
                    )
                    .map_err(q)?;
                (name, "node".to_string(), None)
            }
        };
        Ok(ProgrammeView { name, source, hub_version, fingerprint: fp, slots })
    }

    /// Replace the programme this node runs with what a person edited on the
    /// page `[SPEC-PGM-400]`, in one transaction.
    ///
    /// A slot that keeps its id is updated in place, so `selection_decisions`
    /// still names it; one that loses its id is deleted, one without an id is
    /// added. Its seeds are replaced in the order given. What the node runs is
    /// then its own (`source = 'node'`), whether the name is kept or new
    /// `[SPEC-PGM-405]`: the hub's copy is never touched from here.
    pub fn save_programme(&self, name: Option<&str>, slots: &[Slot]) -> Result<ProgrammeView, DbError> {
        let (name, slots) = validate(name, slots).map_err(DbError::Query)?;
        let current = read_slots(&self.conn)?;
        let known: Vec<i64> = current.iter().filter_map(|s| s.id).collect();
        if let Some(bad) = slots.iter().filter_map(|s| s.id).find(|id| !known.contains(id)) {
            return Err(DbError::Query(format!(
                "Time slot {bad} is no longer on this node; reload the page and edit again."
            )));
        }
        let keep: Vec<i64> = slots.iter().filter_map(|s| s.id).collect();
        let fp = fingerprint(&slots);
        // A new slot takes an id no slot has had, by the slots now or by what
        // history recorded. SQLite would hand out the highest id still present
        // plus one, so a slot added after the last one was deleted would take
        // the deleted slot's id -- and on a listener file whose tables carry
        // no foreign key, every past choice made under the old slot would
        // quietly name the new one. Found by this module's own test.
        let max_of = |sql: &str| -> i64 {
            self.conn.query_row(sql, [], |r| r.get::<_, Option<i64>>(0)).ok().flatten().unwrap_or(0)
        };
        let mut next_id = max_of("SELECT MAX(program_id) FROM listener_programs")
            .max(max_of("SELECT MAX(program_id) FROM selection_decisions"))
            + 1;
        let tx = self.conn.unchecked_transaction().map_err(q)?;
        for id in known.iter().filter(|id| !keep.contains(id)) {
            tx.execute("DELETE FROM listener_program_seeds WHERE program_id = ?1", [id]).map_err(q)?;
            tx.execute("DELETE FROM listener_programs WHERE program_id = ?1", [id]).map_err(q)?;
        }
        // Names are UNIQUE: swapping two slots' names would collide halfway,
        // so every kept slot steps aside to a name no person types first.
        for id in &keep {
            tx.execute(
                "UPDATE listener_programs SET name = char(1) || program_id WHERE program_id = ?1",
                [id],
            )
            .map_err(q)?;
        }
        for s in &slots {
            let id = match s.id {
                Some(id) => {
                    tx.execute(
                        "UPDATE listener_programs SET name = ?1, start_time = ?2 WHERE program_id = ?3",
                        rusqlite::params![s.name, s.start, id],
                    )
                    .map_err(q)?;
                    id
                }
                None => {
                    let id = next_id;
                    next_id += 1;
                    tx.execute(
                        "INSERT INTO listener_programs (program_id, name, start_time) VALUES (?1, ?2, ?3)",
                        rusqlite::params![id, s.name, s.start],
                    )
                    .map_err(q)?;
                    id
                }
            };
            tx.execute("DELETE FROM listener_program_seeds WHERE program_id = ?1", [id]).map_err(q)?;
            for (i, m) in s.seeds.iter().enumerate() {
                tx.execute(
                    "INSERT INTO listener_program_seeds (program_id, mbid, position) VALUES (?1, ?2, ?3)",
                    rusqlite::params![id, m, i as i64 + 1],
                )
                .map_err(q)?;
            }
        }
        tx.execute(
            "INSERT INTO listener_programme (id, name, fingerprint, source, hub_version, updated_at)
             VALUES (1, ?1, ?2, 'node', NULL, datetime('now'))
             ON CONFLICT(id) DO UPDATE SET name = excluded.name, fingerprint = excluded.fingerprint,
                 source = 'node', hub_version = NULL, updated_at = excluded.updated_at",
            rusqlite::params![name, fp],
        )
        .map_err(q)?;
        tx.commit().map_err(q)?;
        self.programme()
    }

    /// Make a recording a seed of one time slot, or stop it being one
    /// `[SPEC-PGM-420]` -- the preference panel's and Browse's way in, which
    /// change one seed and should not have to send the whole programme back.
    /// The same rules as a whole save: the slot must be on this node, a slot
    /// holds at most `SEEDS_MAX`, and the programme becomes the node's own.
    pub fn set_seed(&self, slot_id: i64, mbid: &str, on: bool) -> Result<ProgrammeView, DbError> {
        let mbid = mbid.trim();
        if mbid.is_empty() {
            return Err(DbError::Query("Which recording? None was named.".into()));
        }
        let mut slots = read_slots(&self.conn)?;
        let Some(slot) = slots.iter_mut().find(|s| s.id == Some(slot_id)) else {
            return Err(DbError::Query(format!(
                "Time slot {slot_id} is no longer on this node; reload and try again."
            )));
        };
        let had = slot.seeds.iter().any(|m| m == mbid);
        match (on, had) {
            (true, false) => slot.seeds.push(mbid.to_string()),
            (false, true) => slot.seeds.retain(|m| m != mbid),
            _ => return self.programme(),
        }
        let name = self.programme()?.name;
        self.save_programme(name.as_deref(), &slots)
    }

    /// A programme the hub sent `[SPEC-PGM-050]`, held until a person answers.
    ///
    /// Content decides, never a date `[SPEC-PGM-110]`: what the node already
    /// runs is only recorded as the hub's version, and a version answered
    /// "Keep this node's" is not asked about again. Anything else replaces
    /// whatever was waiting -- a newer version asks again.
    pub fn programme_arrived(&self, a: &Arrival) -> Result<Arrived, DbError> {
        let (name, slots) = validate(Some(&a.name), &a.slots).map_err(DbError::Query)?;
        let name = name.ok_or_else(|| DbError::Query("The hub sent a programme with no name.".into()))?;
        let slots: Vec<Slot> = slots.into_iter().map(|s| Slot { id: None, ..s }).collect();
        let fp = fingerprint(&slots);
        if self.programme()?.fingerprint == fp {
            self.conn
                .execute(
                    "UPDATE listener_programme SET name = ?1, source = 'hub', hub_version = ?2,
                         updated_at = datetime('now') WHERE id = 1",
                    rusqlite::params![name, a.version],
                )
                .map_err(q)?;
            self.conn.execute("DELETE FROM listener_programme_pending", []).map_err(q)?;
            return Ok(Arrived::AlreadyRuns);
        }
        let kept: Option<String> = self
            .conn
            .query_row(
                "SELECT answer FROM listener_programme_pending WHERE id = 1 AND fingerprint = ?1",
                [&fp],
                |r| r.get(0),
            )
            .ok()
            .flatten();
        if kept.as_deref() == Some("kept") {
            return Ok(Arrived::KeptBefore);
        }
        let content = serde_json::to_string(&slots).map_err(|e| DbError::Query(e.to_string()))?;
        self.conn
            .execute(
                "INSERT INTO listener_programme_pending
                     (id, name, hub_version, fingerprint, slots, arrived_at, answer, answered_at)
                 VALUES (1, ?1, ?2, ?3, ?4, datetime('now'), NULL, NULL)
                 ON CONFLICT(id) DO UPDATE SET name = excluded.name, hub_version = excluded.hub_version,
                     fingerprint = excluded.fingerprint, slots = excluded.slots,
                     arrived_at = excluded.arrived_at, answer = NULL, answered_at = NULL",
                rusqlite::params![name, a.version, fp, content],
            )
            .map_err(q)?;
        Ok(Arrived::Asking)
    }

    /// What arrived and is waiting, or was kept over; `None` if nothing has.
    pub fn programme_pending(&self) -> Result<Option<Pending>, DbError> {
        let row = self.conn.query_row(
            "SELECT name, hub_version, fingerprint, slots, arrived_at, answer
               FROM listener_programme_pending WHERE id = 1",
            [],
            |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, i64>(1)?,
                    r.get::<_, String>(2)?,
                    r.get::<_, String>(3)?,
                    r.get::<_, String>(4)?,
                    r.get::<_, Option<String>>(5)?,
                ))
            },
        );
        let (name, hub_version, fingerprint, slots, arrived_at, answer) = match row {
            Ok(r) => r,
            Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(None),
            Err(e) => return Err(q(e)),
        };
        let slots = serde_json::from_str(&slots).map_err(|e| DbError::Query(e.to_string()))?;
        Ok(Some(Pending { name, hub_version, fingerprint, slots, arrived_at, kept: answer.is_some() }))
    }

    /// A person's answer to the arrival with `fingerprint` `[SPEC-PGM-050]`.
    ///
    /// The fingerprint is the one the question showed: a newer version that
    /// arrived while it was on screen is not what they agreed to, and is
    /// refused rather than taken. **Replace** is complete -- every slot is the
    /// arrival's -- but a slot whose name the node already has keeps its id,
    /// so what history recorded under it still names it. **Keep this node's**
    /// leaves the node as it was, and that version is not asked about again.
    pub fn answer_programme(&self, fingerprint: &str, replace: bool) -> Result<ProgrammeView, DbError> {
        let p = self
            .programme_pending()?
            .filter(|p| !p.kept && p.fingerprint == fingerprint)
            .ok_or_else(|| {
                DbError::Query("That programme is no longer waiting for an answer; reload the page.".into())
            })?;
        if !replace {
            self.conn
                .execute(
                    "UPDATE listener_programme_pending SET answer = 'kept', answered_at = datetime('now')
                      WHERE id = 1",
                    [],
                )
                .map_err(q)?;
            return self.programme();
        }
        let current = read_slots(&self.conn)?;
        let slots: Vec<Slot> = p
            .slots
            .iter()
            .map(|s| Slot {
                id: current.iter().find(|c| c.name.to_lowercase() == s.name.to_lowercase()).and_then(|c| c.id),
                ..s.clone()
            })
            .collect();
        self.save_programme(Some(&p.name), &slots)?;
        self.conn
            .execute(
                "UPDATE listener_programme SET source = 'hub', hub_version = ?1, updated_at = datetime('now')
                  WHERE id = 1",
                [p.hub_version],
            )
            .map_err(q)?;
        self.conn.execute("DELETE FROM listener_programme_pending", []).map_err(q)?;
        self.programme()
    }

    /// The time slots as the Director reads them, for a running Director to
    /// take after an edit `[SPEC-PGM-400]`.
    pub fn load_programs(&self) -> Result<crate::director::program::Programs, DbError> {
        crate::director::program::Programs::load(&self.conn)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::QualifyingConn;

    fn slot(name: &str, start: Option<&str>, seeds: &[&str]) -> Slot {
        Slot {
            id: None,
            name: name.into(),
            start: start.map(str::to_string),
            seeds: seeds.iter().map(|s| s.to_string()).collect(),
        }
    }

    fn store() -> PlayerStore {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch("PRAGMA foreign_keys = ON;").unwrap();
        ensure_programme_tables(&c).unwrap();
        PlayerStore { conn: QualifyingConn::wrap_unsplit(c) }
    }

    /// The canonical form is what Python's `json.dumps(sort_keys=True,
    /// separators=(",", ":"), ensure_ascii=False)` writes, so the hub can
    /// compute the same fingerprint with no shared code `[SPEC-PGM-110]`.
    #[test]
    fn the_canonical_form_is_python_s_sorted_compact_json() {
        let slots = vec![slot("Mellow", Some("22:00"), &["b", "a"]), slot("Soft", Some("04:00"), &["c"])];
        assert_eq!(
            canonical(&slots),
            r#"[{"name":"Soft","seeds":["c"],"start":"04:00"},{"name":"Mellow","seeds":["a","b"],"start":"22:00"}]"#
        );
        assert_eq!(canonical(&[slot("Ä", None, &[])]), r#"[{"name":"Ä","seeds":[],"start":null}]"#);
    }

    /// Content decides, not how it arrived: slot order, row ids, the
    /// programme's name and the order of a slot's seeds change nothing
    /// `[SPEC-PGM-115]`; a seed added, removed or moved to another slot does.
    #[test]
    fn the_fingerprint_follows_content_alone() {
        let a = vec![slot("Soft", Some("04:00"), &["x", "y"]), slot("Mellow", Some("22:00"), &[])];
        let mut b = vec![a[1].clone(), a[0].clone()];
        b[0].id = Some(7);
        assert_eq!(fingerprint(&a), fingerprint(&b));
        let mut c = a.clone();
        c[0].seeds.reverse();
        assert_eq!(fingerprint(&a), fingerprint(&c), "a slot's seeds are a set");
        let mut d = a.clone();
        let moved = d[0].seeds.pop().unwrap();
        d[1].seeds.push(moved);
        assert_ne!(fingerprint(&a), fingerprint(&d));
        assert_eq!(fingerprint(&a).len(), 64);
    }

    /// One seed in or out, as the panel and Browse send it: the slot must be
    /// here, a repeat changes nothing, and the programme becomes the node's
    /// own under the name it had.
    #[test]
    fn one_seed_is_added_or_removed_and_nothing_else_moves() {
        let st = store();
        st.conn
            .execute_batch(
                "INSERT INTO listener_programs VALUES (1, 'Soft', '04:00'), (2, 'Mellow', '22:00');
                 INSERT INTO listener_program_seeds VALUES (1, 'x', 1);
                 INSERT INTO listener_programme VALUES (1, 'WKMP', 'old', 'hub', 1, 't');",
            )
            .unwrap();
        let v = st.set_seed(2, "y", true).unwrap();
        assert_eq!(v.name.as_deref(), Some("WKMP"));
        assert_eq!(v.source, "node");
        let seeds = |v: &ProgrammeView, id: i64| v.slots.iter().find(|s| s.id == Some(id)).unwrap().seeds.clone();
        assert_eq!(seeds(&v, 2), vec!["y".to_string()]);
        assert_eq!(seeds(&v, 1), vec!["x".to_string()]);
        let again = st.set_seed(2, "y", true).unwrap();
        assert_eq!(again.fingerprint, v.fingerprint, "adding a seed it has changes nothing");
        assert!(seeds(&st.set_seed(1, "x", false).unwrap(), 1).is_empty());
        assert!(st.set_seed(99, "y", true).unwrap_err().message().contains("no longer on this node"));
        assert!(st.set_seed(1, " ", true).is_err());
    }

    /// A record whose fingerprint was taken in another form -- the first,
    /// which kept seed order -- is brought up to date on the next read, and
    /// the name stays.
    #[test]
    fn a_stale_fingerprint_is_refreshed_on_read() {
        let st = store();
        st.conn
            .execute_batch(
                "INSERT INTO listener_programs VALUES (1, 'Soft', '04:00');
                 INSERT INTO listener_programme VALUES (1, 'WKMP', 'first-form', 'node', NULL, 't');",
            )
            .unwrap();
        let v = st.programme().unwrap();
        let recorded: String =
            st.conn.query_row("SELECT fingerprint FROM listener_programme", [], |r| r.get(0)).unwrap();
        assert_eq!(recorded, v.fingerprint);
        assert_eq!(v.name.as_deref(), Some("WKMP"));
    }

    #[test]
    fn a_submission_is_tidied_or_refused_in_words() {
        let (n, s) = validate(Some("  WKMP "), &[slot(" Soft ", Some("4:00"), &["x", " x", ""])]).unwrap();
        assert_eq!(n.as_deref(), Some("WKMP"));
        assert_eq!(s[0].name, "Soft");
        assert_eq!(s[0].start.as_deref(), Some("04:00"));
        assert_eq!(s[0].seeds, vec!["x".to_string()]);
        assert_eq!(validate(Some("  "), &[]).unwrap().0, None);
        let twice = validate(None, &[slot("Soft", Some("04:00"), &[]), slot("soft", Some("05:00"), &[])]);
        assert!(twice.unwrap_err().contains("Two time slots are called"));
        let same_start = validate(None, &[slot("A", Some("04:00"), &[]), slot("B", Some("4:00"), &[])]);
        assert!(same_start.unwrap_err().contains("start at 04:00"));
        assert!(validate(None, &[slot("A", Some("25:00"), &[])]).unwrap_err().contains("not a time of day"));
        assert!(validate(None, &[slot("", None, &[])]).unwrap_err().contains("needs a name"));
    }

    /// The first read records what the node already runs, unnamed and its own.
    #[test]
    fn the_first_read_records_the_programme_it_finds() {
        let st = store();
        st.conn
            .execute_batch(
                "INSERT INTO listener_programs VALUES (1, 'Soft', '04:00'), (2, 'Mellow', '22:00');
                 INSERT INTO listener_program_seeds VALUES (1, 'x', 1), (2, 'y', 2), (2, 'z', 1);",
            )
            .unwrap();
        let v = st.programme().unwrap();
        assert_eq!(v.name, None);
        assert_eq!(v.source, "node");
        assert_eq!(v.slots.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(), ["Soft", "Mellow"]);
        assert_eq!(v.slots[1].seeds, vec!["z".to_string(), "y".to_string()]);
        let recorded: String =
            st.conn.query_row("SELECT fingerprint FROM listener_programme", [], |r| r.get(0)).unwrap();
        assert_eq!(recorded, v.fingerprint);
    }

    /// An edit keeps the ids of the slots it keeps -- so history still names
    /// them -- swaps names without colliding, and makes the programme the
    /// node's own.
    #[test]
    fn saving_keeps_slot_ids_and_swaps_names_cleanly() {
        let st = store();
        st.conn
            .execute_batch(
                "INSERT INTO listener_programs VALUES (1, 'Soft', '04:00'), (2, 'Mellow', '22:00'), (3, 'Gone', '12:00');
                 INSERT INTO listener_programme VALUES (1, 'WKMP', 'old', 'hub', 1, 't');",
            )
            .unwrap();
        let mut a = slot("Mellow", Some("04:00"), &["s1", "s2"]);
        a.id = Some(1);
        let mut b = slot("Soft", Some("22:00"), &[]);
        b.id = Some(2);
        let v = st.save_programme(Some("WKMP Kitchen"), &[a, b, slot("New", Some("12:30"), &["n"])]).unwrap();
        assert_eq!(v.name.as_deref(), Some("WKMP Kitchen"));
        assert_eq!(v.source, "node");
        assert_eq!(v.hub_version, None);
        let by_name = |n: &str| v.slots.iter().find(|s| s.name == n).unwrap().clone();
        assert_eq!(by_name("Mellow").id, Some(1));
        assert_eq!(by_name("Soft").id, Some(2));
        assert_eq!(by_name("Mellow").seeds, vec!["s1".to_string(), "s2".to_string()]);
        assert!(by_name("New").id.is_some_and(|id| id > 3));
        assert!(v.slots.iter().all(|s| s.name != "Gone"));
        assert_eq!(st.programme().unwrap().fingerprint, v.fingerprint);
    }

    #[test]
    fn a_slot_that_is_no_longer_there_is_refused() {
        let st = store();
        let mut s = slot("Soft", Some("04:00"), &[]);
        s.id = Some(99);
        assert!(st.save_programme(None, &[s]).unwrap_err().message().contains("no longer on this node"));
    }

    /// Slots changed behind the record's back -- by a tool, by hand -- make the
    /// programme the node's own, though the name stays.
    #[test]
    fn slots_changed_outside_read_as_the_node_s_own() {
        let st = store();
        st.conn
            .execute_batch(
                "INSERT INTO listener_programs VALUES (1, 'Soft', '04:00');
                 INSERT INTO listener_programme VALUES (1, 'WKMP', 'not-this', 'hub', 1, 't');",
            )
            .unwrap();
        let v = st.programme().unwrap();
        assert_eq!((v.name.as_deref(), v.source.as_str(), v.hub_version), (Some("WKMP"), "node", None));
    }

    fn arrival(version: i64, slots: Vec<Slot>) -> Arrival {
        Arrival { name: "WKMP".into(), version, slots }
    }

    /// The same content, from wherever it came, asks nothing: the node is
    /// recorded as running the hub's version `[SPEC-PGM-110]`, `[SPEC-PGM-500]`.
    #[test]
    fn an_arrival_the_node_already_runs_is_recorded_not_asked() {
        let st = store();
        st.save_programme(None, &[slot("Soft", Some("04:00"), &["b", "a"])]).unwrap();
        let got = st.programme_arrived(&arrival(1, vec![slot("Soft", Some("4:00"), &["a", "b"])])).unwrap();
        assert_eq!(got, Arrived::AlreadyRuns);
        let v = st.programme().unwrap();
        assert_eq!((v.name.as_deref(), v.source.as_str(), v.hub_version), (Some("WKMP"), "hub", Some(1)));
        assert_eq!(st.programme_pending().unwrap(), None);
    }

    /// Replace is complete, and a slot the node already has by name keeps its
    /// id, so history still names it `[SPEC-PGM-050]`.
    #[test]
    fn replacing_takes_every_slot_and_keeps_ids_by_name() {
        let st = store();
        let before = st
            .save_programme(Some("Mine"), &[slot("Soft", Some("04:00"), &["a"]), slot("Loud", Some("12:00"), &["x"])])
            .unwrap();
        let soft_id = before.slots.iter().find(|s| s.name == "Soft").unwrap().id;
        let sent = vec![slot("soft", Some("05:00"), &["c"]), slot("Night", Some("22:00"), &["n"])];
        assert_eq!(st.programme_arrived(&arrival(2, sent)).unwrap(), Arrived::Asking);
        let p = st.programme_pending().unwrap().unwrap();
        assert!(!p.kept);
        // Nothing changes until a person answers.
        assert_eq!(st.programme().unwrap().name.as_deref(), Some("Mine"));

        let v = st.answer_programme(&p.fingerprint, true).unwrap();
        assert_eq!((v.name.as_deref(), v.source.as_str(), v.hub_version), (Some("WKMP"), "hub", Some(2)));
        let names: Vec<&str> = v.slots.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["soft", "Night"]);
        assert_eq!(v.slots[0].id, soft_id, "a slot kept by name keeps its id");
        assert_eq!(v.fingerprint, p.fingerprint);
        assert_eq!(st.programme_pending().unwrap(), None);
    }

    /// Keeping its own leaves the node as it was and is not asked again about
    /// that version; a newer one asks again `[SPEC-PGM-050]`.
    #[test]
    fn keeping_is_remembered_for_that_version_only() {
        let st = store();
        st.save_programme(Some("Mine"), &[slot("Soft", Some("04:00"), &["a"])]).unwrap();
        let v1 = vec![slot("Soft", Some("06:00"), &["a"])];
        assert_eq!(st.programme_arrived(&arrival(1, v1.clone())).unwrap(), Arrived::Asking);
        let fp = st.programme_pending().unwrap().unwrap().fingerprint;
        let v = st.answer_programme(&fp, false).unwrap();
        assert_eq!((v.name.as_deref(), v.source.as_str()), (Some("Mine"), "node"));
        assert!(st.programme_pending().unwrap().unwrap().kept);
        assert_eq!(st.programme_arrived(&arrival(1, v1)).unwrap(), Arrived::KeptBefore);
        let v2 = vec![slot("Soft", Some("07:00"), &["a"])];
        assert_eq!(st.programme_arrived(&arrival(2, v2)).unwrap(), Arrived::Asking);
        assert!(!st.programme_pending().unwrap().unwrap().kept);
    }

    /// An answer is to the version the question showed; one that arrived
    /// while it was on screen is refused, not taken.
    #[test]
    fn an_answer_to_a_version_no_longer_waiting_is_refused() {
        let st = store();
        st.programme_arrived(&arrival(1, vec![slot("A", Some("01:00"), &[])])).unwrap();
        let old = st.programme_pending().unwrap().unwrap().fingerprint;
        st.programme_arrived(&arrival(2, vec![slot("B", Some("02:00"), &[])])).unwrap();
        let e = st.answer_programme(&old, true).unwrap_err().to_string();
        assert!(e.contains("no longer waiting"), "{e}");
        assert!(st.programme().unwrap().slots.is_empty());
    }
}
