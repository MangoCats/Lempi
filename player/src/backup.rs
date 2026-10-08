//! Keeping the part of the library that cannot be rebuilt `[REQ-LIB-160]`.
//!
//! The library file holds two kinds of thing, and they have opposite recovery
//! stories. The **library** — files, passages, recordings, flavor — is derived
//! from the audio on disk, and Vipunen can grind it out again from nothing but
//! time. The **listening** — 37,206 plays, 3,261 preferences, the programmes
//! and their seeds — is derived from years of a person using the thing, and
//! nothing anywhere can reproduce it. Lose it and the Program Director is a
//! random shuffle with opinions it can no longer justify.
//!
//! So this copies only the second kind. That choice is what makes the backup
//! small enough to take often: a few megabytes against a library of hundreds,
//! which is the difference between a snapshot every hour and one nobody runs.
//!
//! **A copy, not a dump.** The output is a real SQLite file, openable and
//! queryable, restorable by attaching it. A schema-and-INSERTs text dump would
//! need a working player to be useful, and the moment a backup matters is the
//! moment there isn't one.
//!
//! **Rotating, not overwriting.** Corruption that goes unnoticed for a day
//! would otherwise be faithfully copied over the last good snapshot. Keeping
//! several generations means the damage has to outrun the whole set.

use std::path::{Path, PathBuf};

use rusqlite::Connection;

use crate::db::DbError;

/// Everything a listener created, and nothing a machine can recreate.
///
/// `player_state` is included though it is only the resume point: it is one row
/// and it is the difference between resuming where you were and starting over.
///
/// Deliberately NOT here: `files`, `passages`, `recordings`, `artists`,
/// `releases`, `flavor`, `file_tags`, the caches. All of it is derived, all of
/// it is large, and all of it Vipunen can produce again.
pub const LISTENER_TABLES: &[&str] = &[
    "listener_play_history",
    // A record of what the listener rejected `[SPEC-PLAY-050]`. Small, but no
    // machine can recreate it: losing it un-suppresses everything they declined.
    "listener_rejections",
    "listener_preferences",
    "listener_likes",
    "listener_programs",
    "listener_program_seeds",
    "listener_occasions",
    "listener_occasion_points",
    // The listener's own "special" tagging `[SPEC-PREF-085]` -- which songs
    // are Christmas songs, which are for children, which are spiritual.
    // Small, and nothing recreates it: it is a person's judgment about
    // their own music, not anything derivable from the audio.
    "listener_characteristics",
    "listener_settings",
    "player_state",
    // The rest of "where you were" `[SPEC-DIR-225]`: `player_state` is the
    // passage sounding, this is the handful waiting behind it. Restored
    // together or the resume is half a resume.
    "player_queue",
];

/// How far back each tier reaches `[REQ-LIB-160]`.
///
/// Grandfather-father-son, because the value of an old snapshot is not that it
/// is old but that it predates whatever went wrong. Damage noticed the same
/// afternoon needs yesterday; damage noticed at Christmas needs March; a
/// preference quietly corrupted two years ago needs a copy from before it.
///
/// At 2.4 MB apiece the whole ladder is a few hundred megabytes after a
/// decade, which is less than the library it protects.
pub const KEEP_DAYS: usize = 7;
pub const KEEP_MONTHS: usize = 12;

/// Calendar year, month and day for a Unix timestamp, UTC.
///
/// Written out rather than pulled in: the player has no date dependency and
/// this is the only thing that ever needed one. Howard Hinnant's civil-from-
/// days, which is exact for every date this will ever see -- no approximation
/// by 365.25, which drifts a day every century and would silently file a
/// snapshot under the wrong year.
fn civil(secs: i64) -> (i64, u32, u32) {
    let z = secs.div_euclid(86_400) + 719_468;
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

/// Where snapshots live: beside the library, in their own directory, so a
/// glob for `*.db` in the data directory cannot sweep them up as libraries.
pub fn dir_for(db: &Path) -> PathBuf {
    db.parent().unwrap_or_else(|| Path::new(".")).join("listener-backups")
}

/// Take one snapshot. Returns where it was written.
///
/// Read-only on the source, so it can run while the player is playing: SQLite
/// in WAL mode lets a reader work through a writer without either waiting.
pub fn snapshot(db: &Path) -> Result<PathBuf, DbError> {
    snapshot_named(db, "listener-", true)
}

/// A snapshot taken before something risky, kept out of the rotation.
///
/// Taking an ordinary snapshot before a restore very nearly destroyed the
/// thing being restored: both fell on the same day, the ladder keeps only the
/// newest of a day, and the older one -- the snapshot about to be read -- was
/// pruned out from under the restore. A safety copy that can be rotated away
/// by the next safety copy is not a safety copy, so these carry their own
/// prefix and `prune` never looks at them.
pub fn snapshot_before_restore(db: &Path) -> Result<PathBuf, DbError> {
    snapshot_named(db, "prerestore-", false)
}

/// A path as a read-only SQLite URI. `%`, `?` and `#` would otherwise be read
/// as an escape, a query or a fragment; percent-encoded they are part of the
/// name. Shared, so the snapshot and the restore cannot escape differently --
/// they did, until 2026-10-08.
fn ro_uri(path: &Path) -> String {
    let p = path.to_string_lossy().replace('%', "%25").replace('?', "%3f").replace('#', "%23");
    format!("file:{p}?mode=ro")
}

fn snapshot_named(db: &Path, prefix: &str, rotate: bool) -> Result<PathBuf, DbError> {
    let dir = dir_for(db);
    std::fs::create_dir_all(&dir)
        .map_err(|e| DbError::Open(format!("create {}: {e}", dir.display())))?;

    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let out = dir.join(format!("{prefix}{stamp}.db"));
    // A half-written snapshot must never look like a good one, so it is built
    // under a temporary name and renamed only once it is complete. Rename is
    // atomic; a copy interrupted half way leaves a `.part` nobody will trust.
    let part = dir.join(format!("{prefix}{stamp}.part"));
    let _ = std::fs::remove_file(&part);

    // The connection owns the SNAPSHOT and attaches the library, not the other
    // way round. Two reasons, and the second is the one that matters: ATTACH
    // cannot create a database from a read-only connection, and this way the
    // library is attached `mode=ro`, so a mistake in the copy below cannot
    // write to the thing being protected.
    let conn = Connection::open(&part)
        .map_err(|e| DbError::Open(format!("create {}: {e}", part.display())))?;
    conn.busy_timeout(std::time::Duration::from_secs(10))
        .map_err(|e| DbError::Open(e.to_string()))?;
    conn.execute("ATTACH DATABASE ?1 AS src", [ro_uri(db).as_str()])
        .map_err(|e| DbError::Query(format!("attach {}: {e}", db.display())))?;

    let mut copied = 0usize;
    for table in LISTENER_TABLES {
        // A table the schema has not grown yet is not an error: a fresh
        // library has no likes, and a backup of nothing is still a backup.
        let exists: i64 = conn
            .query_row(
                "SELECT COUNT(*) FROM src.sqlite_master WHERE type='table' AND name=?1",
                [table],
                |r| r.get(0),
            )
            .unwrap_or(0);
        if exists == 0 {
            continue;
        }
        conn.execute_batch(&format!(
            "CREATE TABLE main.\"{table}\" AS SELECT * FROM src.\"{table}\";"
        ))
        .map_err(|e| DbError::Query(format!("copy {table}: {e}")))?;
        copied += 1;
    }
    conn.execute_batch("DETACH DATABASE src")
        .map_err(|e| DbError::Query(e.to_string()))?;
    drop(conn);

    if copied == 0 {
        let _ = std::fs::remove_file(&part);
        return Err(DbError::Query("no listener tables to back up".into()));
    }
    std::fs::rename(&part, &out)
        .map_err(|e| DbError::Open(format!("finish {}: {e}", out.display())))?;
    if rotate {
        prune(&dir);
    }
    Ok(out)
}


/// A backup ready to leave the machine `[REQ-AND-195]`: which snapshot, what
/// it holds, and its bytes' hash, so the copy that arrives can be checked.
#[derive(Debug)]
pub struct Export {
    pub path: PathBuf,
    pub summary: Summary,
    /// Taken just now. False when a fresh one could not be taken, and this is
    /// the newest earlier one that passed -- which the listener is told.
    pub fresh: bool,
    /// When it was taken, in seconds since the epoch.
    pub taken_at: i64,
    pub sha256: String,
    pub bytes: u64,
}

/// The newest backup that passes SQLite's integrity check, for export
/// `[REQ-AND-195]`, `[REQ-PORT-150]`. A fresh snapshot first, so what leaves
/// the phone holds the listening up to now; if one cannot be taken, or it
/// fails the check, the newest earlier snapshot that passes. A backup nobody
/// checked is not one to send away as the only copy.
pub fn for_export(db: &Path) -> Result<Export, DbError> {
    let fresh = snapshot(db);
    let mut candidates: Vec<(PathBuf, bool)> = Vec::new();
    if let Ok(p) = &fresh {
        candidates.push((p.clone(), true));
    }
    let mut older: Vec<(i64, PathBuf)> = std::fs::read_dir(dir_for(db))
        .map(|it| {
            it.flatten()
                .filter_map(|e| stamp_of(&e.path()).map(|t| (t, e.path())))
                .collect()
        })
        .unwrap_or_default();
    older.sort_by(|a, b| b.0.cmp(&a.0));
    for (_, p) in older {
        if fresh.as_ref().ok() != Some(&p) {
            candidates.push((p, false));
        }
    }
    for (path, is_fresh) in candidates {
        if !passes_integrity(&path) {
            tracing::warn!("backup {} fails its integrity check; trying an older one", path.display());
            continue;
        }
        let summary = inspect(&path)?;
        let sha256 = crate::bundle::sha256_file(&path).map_err(DbError::Open)?;
        let bytes = std::fs::metadata(&path).map(|m| m.len()).map_err(|e| DbError::Open(e.to_string()))?;
        let taken_at = stamp_of(&path).unwrap_or(0);
        return Ok(Export { path, summary, fresh: is_fresh, taken_at, sha256, bytes });
    }
    Err(DbError::Query(match fresh {
        Err(e) => format!("no backup passes its integrity check, and a fresh one could not be taken: {e}"),
        Ok(_) => "no backup passes its integrity check".into(),
    }))
}

fn passes_integrity(path: &Path) -> bool {
    Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .and_then(|c| c.query_row("PRAGMA integrity_check", [], |r| r.get::<_, String>(0)))
        .is_ok_and(|v| v == "ok")
}

/// What a snapshot holds, without committing to anything.
///
/// The first question anyone asks of a backup is "is this the right one",
/// and it must be answerable without restoring it to find out.
#[derive(Debug, Default, PartialEq)]
pub struct Summary {
    pub plays: i64,
    pub first_play: Option<i64>,
    pub last_play: Option<i64>,
    pub preferences: i64,
    pub likes: i64,
    pub programs: i64,
}

pub fn inspect(snapshot: &Path) -> Result<Summary, DbError> {
    let c = Connection::open_with_flags(snapshot, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| DbError::Open(e.to_string()))?;
    let q = |e: rusqlite::Error| DbError::Query(e.to_string());
    // A table the snapshot does not hold is a snapshot taken before it existed,
    // and reads as none. A query that FAILS is not "none": it is reported, so a
    // damaged snapshot never passes for an empty one (review O-04).
    let has = |t: &str| -> Result<bool, DbError> {
        c.query_row("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1", [t],
                    |r| r.get::<_, i64>(0))
            .map(|n| n > 0)
            .map_err(q)
    };
    let count = |t: &str| -> Result<i64, DbError> {
        if !has(t)? {
            return Ok(0);
        }
        c.query_row(&format!("SELECT COUNT(*) FROM \"{t}\""), [], |r| r.get(0)).map_err(q)
    };
    let (first_play, last_play) = if has("listener_play_history")? {
        c.query_row("SELECT MIN(played_at), MAX(played_at) FROM listener_play_history", [],
                    |r| Ok((r.get(0)?, r.get(1)?)))
            .map_err(q)?
    } else {
        (None, None)
    };
    Ok(Summary {
        plays: count("listener_play_history")?,
        first_play,
        last_play,
        preferences: count("listener_preferences")?,
        likes: count("listener_likes")?,
        programs: count("listener_programs")?,
    })
}

/// What a restore did, or would do.
#[derive(Debug, Default, PartialEq)]
pub struct Report {
    pub tables: usize,
    pub plays: i64,
    /// Plays whose passage now has a different id, matched back by recording.
    pub remapped: i64,
    /// Plays whose recording is no longer in the library at all. Kept, with
    /// their old passage id: a play that happened still happened, and throwing
    /// it away to tidy a foreign key would lose the only record of it.
    pub orphaned: i64,
    pub committed: bool,
}

/// Put a snapshot back.
///
/// **Passage ids are not stable and recording MBIDs are.** A Vipunen rebuild
/// renumbers passages, so restoring a play history by its stored `passage_id`
/// would silently attribute years of listening to whatever songs happen to
/// hold those numbers now. Every play carries the recording it was, and that
/// is what the history is re-pointed through.
///
/// **The recordings live in the catalogue** `[PI-DB-030]`. `listener` is
/// written; `library` is attached read-only and is only ever read, through
/// `attach_library`, the same way the player reads it. Before 2026-10-08 this
/// opened the listener file alone and looked for `passage_recordings` there:
/// on every split installation a rehearsal reported zeros and a commit failed
/// (review F-01).
///
/// Nothing is written unless `commit`. The default is a rehearsal that reports
/// exactly what would happen, because the first restore anyone performs is
/// usually the one they are least sure about. Every count it reports is either
/// measured or an error -- never a failed query read as zero (review O-04).
pub fn restore(snapshot: &Path, listener: &Path, library: &Path, commit: bool)
    -> Result<Report, DbError> {
    let conn = Connection::open(listener).map_err(|e| DbError::Open(e.to_string()))?;
    conn.busy_timeout(std::time::Duration::from_secs(10))
        .map_err(|e| DbError::Open(e.to_string()))?;
    let lib = crate::db::attach_library(&conn, listener, library)?;
    conn.execute("ATTACH DATABASE ?1 AS snap", [ro_uri(snapshot).as_str()])
        .map_err(|e| DbError::Query(format!("attach {}: {e}", snapshot.display())))?;

    let q = |e: rusqlite::Error| DbError::Query(e.to_string());
    let table_in = |schema: &str, table: &str| -> Result<bool, DbError> {
        conn.query_row(
            &format!("SELECT COUNT(*) FROM {schema}.sqlite_master WHERE type='table' AND name=?1"),
            [table], |r| r.get::<_, i64>(0))
            .map(|n| n > 0)
            .map_err(q)
    };
    let recordings = format!("{lib}.passage_recordings");
    // A play to re-point: its recording is in the library, and its passage no
    // longer carries it. A play whose passage still does stays where it is --
    // a recording on several passages (an album cut and a single, say) would
    // otherwise have its plays moved between them. One clause, used for the
    // count and the write, so the rehearsal's number is the restore's.
    let moved = format!(
        "h.mbid IS NOT NULL \
         AND EXISTS (SELECT 1 FROM {recordings} pr WHERE pr.mbid = h.mbid) \
         AND NOT EXISTS (SELECT 1 FROM {recordings} pr \
                          WHERE pr.mbid = h.mbid AND pr.passage_id = h.passage_id)"
    );

    let mut report = Report { committed: commit, ..Default::default() };

    // How much of the history still points at something that exists, and how
    // much has moved. Measured before anything is written, so a rehearsal and
    // a real restore report the same numbers.
    let has_hist = table_in("snap", "listener_play_history")?;
    if has_hist {
        if !table_in(lib, "passage_recordings")? {
            return Err(DbError::Query(format!(
                "{} has no passage_recordings to re-point the history through",
                library.display()
            )));
        }
        report.plays = conn
            .query_row("SELECT COUNT(*) FROM snap.listener_play_history", [], |r| r.get(0))
            .map_err(q)?;
        report.remapped = conn
            .query_row(
                &format!("SELECT COUNT(*) FROM snap.listener_play_history h WHERE {moved}"),
                [], |r| r.get(0))
            .map_err(q)?;
        report.orphaned = conn
            .query_row(
                &format!(
                    "SELECT COUNT(*) FROM snap.listener_play_history h \
                      WHERE h.mbid IS NULL \
                         OR NOT EXISTS (SELECT 1 FROM {recordings} pr \
                                         WHERE pr.mbid = h.mbid)"
                ),
                [], |r| r.get(0))
            .map_err(q)?;
    }

    // Which tables come back, and how each is copied. Decided before anything
    // is written, so a rehearsal refuses what a restore would fail on.
    let mut plan: Vec<(&str, String)> = Vec::new();
    for table in LISTENER_TABLES {
        if table_in("snap", table)? && table_in("main", table)? {
            plan.push((table, copy_for(&conn, table)?));
        }
    }

    if !commit {
        report.tables = plan.len();
        let _ = conn.execute_batch("DETACH DATABASE snap");
        return Ok(report);
    }

    // One transaction: a restore that half-applied would leave the listening
    // in a state that never existed, which is worse than either version.
    conn.execute_batch("BEGIN IMMEDIATE")
        .map_err(|e| DbError::Query(e.to_string()))?;
    let done = || -> Result<usize, DbError> {
        for (table, insert) in &plan {
            conn.execute_batch(&format!("DELETE FROM main.\"{table}\"; {insert};"))
                .map_err(|e| DbError::Query(format!("restore {table}: {e}")))?;
        }
        let n = plan.len();
        // Re-point the history through recordings, which outlive renumbering:
        // to the passage that is most nearly the recording itself (a medley
        // holding it carries a smaller weight), then the lowest id, so the
        // choice is the same every time.
        if has_hist {
            conn.execute_batch(&format!(
                "UPDATE main.listener_play_history AS h \
                    SET passage_id = (SELECT pr.passage_id FROM {recordings} pr \
                                       WHERE pr.mbid = h.mbid \
                                       ORDER BY pr.weight DESC, pr.passage_id LIMIT 1) \
                  WHERE {moved};"
            ))
            .map_err(|e| DbError::Query(format!("remap history: {e}")))?;
        }
        Ok(n)
    };
    match done() {
        Ok(n) => {
            report.tables = n;
            conn.execute_batch("COMMIT").map_err(|e| DbError::Query(e.to_string()))?;
        }
        Err(e) => {
            let _ = conn.execute_batch("ROLLBACK");
            let _ = conn.execute_batch("DETACH DATABASE snap");
            return Err(e);
        }
    }
    let _ = conn.execute_batch("DETACH DATABASE snap");
    Ok(report)
}

/// How `table` is copied back from `snap`, as the `INSERT` that does it.
///
/// **By name** when the listener file has every column the snapshot holds: a
/// snapshot taken before a column was added (`listener_occasions.label`, say)
/// restores, and the new column takes its default. A yearly snapshot is kept
/// for ever, so this is the ordinary case for an old one, not a corner.
///
/// **By position** when the names differ but the counts agree: a renamed
/// column, which keeps its place (`track_time_scale`, now
/// `recording_time_scale`). That is how every table was copied before.
///
/// Otherwise refused, naming the column: the snapshot holds something this
/// file has nowhere to put, and copying the rest would lose it quietly.
fn copy_for(conn: &Connection, table: &str) -> Result<String, DbError> {
    let columns = |schema: &str| -> Result<Vec<String>, DbError> {
        let q = |e: rusqlite::Error| DbError::Query(format!("columns of {schema}.{table}: {e}"));
        let mut st = conn.prepare(&format!("PRAGMA {schema}.table_info(\"{table}\")")).map_err(q)?;
        let names = st.query_map([], |r| r.get::<_, String>(1)).map_err(q)?;
        names.collect::<Result<Vec<_>, _>>().map_err(q)
    };
    let here = columns("main")?;
    let theirs = columns("snap")?;
    let known = |c: &String| here.iter().any(|h| h.eq_ignore_ascii_case(c));
    if theirs.iter().all(known) {
        let list = theirs.iter().map(|c| format!("\"{c}\"")).collect::<Vec<_>>().join(", ");
        return Ok(format!("INSERT INTO main.\"{table}\" ({list}) SELECT {list} FROM snap.\"{table}\""));
    }
    if theirs.len() == here.len() {
        return Ok(format!("INSERT INTO main.\"{table}\" SELECT * FROM snap.\"{table}\""));
    }
    let extra: Vec<&str> = theirs.iter().filter(|c| !known(c)).map(String::as_str).collect();
    Err(DbError::Query(format!(
        "the snapshot's {table} has {} this listener file does not; restoring it would lose {}",
        extra.join(", "),
        if extra.len() == 1 { "that column" } else { "those columns" }
    )))
}

// --- Choosing a snapshot, and a restore staged for the next start ----------
//
// A page can show the snapshots and what putting one back would do, but it
// cannot restore beneath the player serving it: the player writes the
// listener file as it plays and holds its queue and its Director in memory,
// so a restore under it would be partly overwritten and partly ignored. The
// page stages one instead, and the player applies it as it next starts,
// before anything opens the file to write `[REQ-LIB-160]`.

/// The stage: the name of the snapshot to put back at the next start.
const STAGED: &str = "restore-staged";
/// What the last staged restore did, for the page to say.
const OUTCOME: &str = "restore-outcome";

/// One snapshot, by the name a person picks it by.
#[derive(Debug, Clone, PartialEq)]
pub struct Held {
    pub name: String,
    pub path: PathBuf,
    /// When it was taken, in seconds since the epoch.
    pub taken_at: i64,
    /// A copy taken before a restore, which rotation never prunes.
    pub before_restore: bool,
}

/// The snapshots beside `listener`, newest first: the hourly rotation and the
/// copies taken before a restore. Nothing else there -- a `.part`, the stage
/// -- is offered.
pub fn held(listener: &Path) -> Vec<Held> {
    let Ok(entries) = std::fs::read_dir(dir_for(listener)) else { return Vec::new() };
    let mut out: Vec<Held> = entries
        .flatten()
        .filter_map(|e| {
            let path = e.path();
            let name = path.file_name()?.to_str()?.to_string();
            let (before_restore, rest) = match name.strip_prefix("listener-") {
                Some(rest) => (false, rest),
                None => (true, name.strip_prefix("prerestore-")?),
            };
            let taken_at = rest.strip_suffix(".db")?.parse().ok()?;
            Some(Held { name, path, taken_at, before_restore })
        })
        .collect();
    out.sort_by(|a, b| b.taken_at.cmp(&a.taken_at).then_with(|| a.name.cmp(&b.name)));
    out
}

/// A snapshot by the name [`held`] gives it, and by no other. A page sends
/// this name, so a path, or anything not in the directory, finds nothing.
pub fn find(listener: &Path, name: &str) -> Option<PathBuf> {
    held(listener).into_iter().find(|h| h.name == name).map(|h| h.path)
}

/// Stage `name` to be put back at the next start, and say what that will do.
/// Rehearsed first: a snapshot that cannot be restored is refused now, while
/// someone is looking, not at a start nobody is watching.
pub fn stage(listener: &Path, library: &Path, name: &str) -> Result<Report, DbError> {
    let path = find(listener, name)
        .ok_or_else(|| DbError::Query(format!("no snapshot called {name}")))?;
    let report = restore(&path, listener, library, false)?;
    let dir = dir_for(listener);
    let part = dir.join(format!("{STAGED}.part"));
    std::fs::write(&part, name)
        .and_then(|_| std::fs::rename(&part, dir.join(STAGED)))
        .map_err(|e| DbError::Open(format!("stage {name}: {e}")))?;
    Ok(report)
}

/// Take the stage down. `Ok(false)` when nothing was staged.
pub fn unstage(listener: &Path) -> Result<bool, DbError> {
    match std::fs::remove_file(dir_for(listener).join(STAGED)) {
        Ok(()) => Ok(true),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(e) => Err(DbError::Open(format!("unstage: {e}"))),
    }
}

/// The snapshot staged, if any.
pub fn staged(listener: &Path) -> Option<String> {
    let name = std::fs::read_to_string(dir_for(listener).join(STAGED)).ok()?;
    let name = name.trim();
    (!name.is_empty()).then(|| name.to_string())
}

/// What a staged restore did.
#[derive(Debug, Clone, PartialEq)]
pub struct Outcome {
    /// When, in seconds since the epoch.
    pub at: i64,
    pub restored: bool,
    /// One line, for the log and the page alike.
    pub said: String,
}

/// What the last staged restore did, if one has run here.
pub fn last_outcome(listener: &Path) -> Option<Outcome> {
    let s = std::fs::read_to_string(dir_for(listener).join(OUTCOME)).ok()?;
    let mut parts = s.trim_end().splitn(3, '\t');
    let at = parts.next()?.parse().ok()?;
    let restored = parts.next()? == "restored";
    Some(Outcome { at, restored, said: parts.next()?.to_string() })
}

/// Put back a staged restore, if there is one. Called as the player starts,
/// before the backup thread or the session opens the listener file.
///
/// **At most once.** The stage comes down before anything else, and if it
/// cannot, nothing is restored: a stage that outlived its restore would put
/// the same snapshot back at every start, over everything played since.
pub fn apply_staged(listener: &Path, library: &Path) -> Option<Outcome> {
    let name = staged(listener)?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs() as i64);
    let (restored, said) = match unstage(listener) {
        Err(e) => (false, format!(
            "the restore of {name} was not attempted: its stage could not be removed ({e}), \
             and left in place it would restore again at every start")),
        Ok(_) => put_back(listener, library, &name),
    };
    let outcome = Outcome { at: now, restored, said };
    let line = format!("{}\t{}\t{}\n", outcome.at,
                       if outcome.restored { "restored" } else { "not restored" }, outcome.said);
    if let Err(e) = std::fs::write(dir_for(listener).join(OUTCOME), line) {
        tracing::warn!("could not record the restore's outcome for the page: {e}");
    }
    Some(outcome)
}

fn put_back(listener: &Path, library: &Path, name: &str) -> (bool, String) {
    let Some(path) = find(listener, name) else {
        return (false, format!("{name} was staged to be put back, but it is no longer there"));
    };
    let before = match snapshot_before_restore(listener) {
        Ok(p) => p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
        Err(e) => {
            return (false, format!(
                "{name} was not put back: the listening as it was could not be saved first ({e})"));
        }
    };
    match restore(&path, listener, library, true) {
        Ok(r) => (true, format!(
            "put back {name}: {} plays, {} re-pointed to new passage ids, {} orphaned; \
             the listening as it was is kept as {before}",
            r.plays, r.remapped, r.orphaned)),
        Err(e) => (false, format!(
            "{name} was not put back, and nothing changed ({e}); the listening is also kept \
             as {before}")),
    }
}

/// Thin the snapshots to the retention ladder.
///
/// One per day for the last week, one per month for the last year, one per
/// year for ever, and always the newest whatever else happens. Within a period
/// the LATEST is kept: it is the one holding the most listening.
///
/// Failure is deliberately silent. An undeletable old snapshot is untidy, and
/// refusing to take new ones over it would turn tidiness into data loss.
fn prune(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    let mut snaps: Vec<(i64, PathBuf)> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter_map(|p| Some((stamp_of(&p)?, p)))
        .collect();
    if snaps.is_empty() {
        return;
    }
    // Newest first, so the first seen in any period is the one to keep.
    snaps.sort_by(|a, b| b.0.cmp(&a.0));

    let mut keep: std::collections::HashSet<PathBuf> = std::collections::HashSet::new();
    keep.insert(snaps[0].1.clone());          // the newest, always

    let mut days: Vec<(i64, u32, u32)> = Vec::new();
    let mut months: Vec<(i64, u32)> = Vec::new();
    let mut years: Vec<i64> = Vec::new();
    for (secs, path) in &snaps {
        let (y, m, d) = civil(*secs);
        if !days.contains(&(y, m, d)) && days.len() < KEEP_DAYS {
            days.push((y, m, d));
            keep.insert(path.clone());
        }
        if !months.contains(&(y, m)) && months.len() < KEEP_MONTHS {
            months.push((y, m));
            keep.insert(path.clone());
        }
        // Yearly has no limit: a decade of them is ten files.
        if !years.contains(&y) {
            years.push(y);
            keep.insert(path.clone());
        }
    }
    for (_, path) in &snaps {
        if !keep.contains(path) {
            let _ = std::fs::remove_file(path);
        }
    }
}

/// The timestamp a snapshot's name carries, or `None` if it is not one.
fn stamp_of(path: &Path) -> Option<i64> {
    let name = path.file_name()?.to_str()?;
    let rest = name.strip_prefix("listener-")?.strip_suffix(".db")?;
    rest.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn library(dir: &Path) -> PathBuf {
        let db = dir.join("lib.db");
        let c = Connection::open(&db).unwrap();
        c.execute_batch(
            "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             CREATE TABLE listener_settings (id INTEGER PRIMARY KEY, artist_time_scale REAL);
             CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT);
             INSERT INTO listener_play_history VALUES (1, 100, 7, 'abc');
             INSERT INTO listener_play_history VALUES (2, 200, 8, 'def');
             INSERT INTO listener_settings VALUES (1, 1.0);
             INSERT INTO files VALUES (1, '/music/a.mp3');",
        )
        .unwrap();
        db
    }

    #[test]
    fn a_snapshot_holds_the_listening_and_not_the_library() {
        let tmp = std::env::temp_dir().join(format!(
            "lempi-bk-{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let db = library(&tmp);

        let out = snapshot(&db).expect("snapshot");
        let c = Connection::open(&out).unwrap();
        let plays: i64 = c
            .query_row("SELECT COUNT(*) FROM listener_play_history", [], |r| r.get(0))
            .unwrap();
        assert_eq!(plays, 2, "the listening is copied");
        // The library is what Vipunen can rebuild, and copying it would make the
        // snapshot too big to take often -- which is how backups stop happening.
        let has_files: i64 = c
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE name='files'",
                [],
                |r| r.get(0),
            )
            .unwrap();
        assert_eq!(has_files, 0, "the derived library is not copied");
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// `[REQ-AND-195]`: an export is a fresh snapshot, integrity-checked, with
    /// what it holds and its bytes' hash.
    #[test]
    fn an_export_is_a_fresh_checked_snapshot() {
        let tmp = std::env::temp_dir().join(format!("lempi-bk-export-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();
        let db = library(&tmp);
        let e = for_export(&db).unwrap();
        assert!(e.fresh);
        assert_eq!(e.summary.plays, 2);
        assert_eq!(e.sha256, crate::bundle::sha256_file(&e.path).unwrap());
        assert_eq!(e.bytes, std::fs::metadata(&e.path).unwrap().len());
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// Without a fresh snapshot, the newest earlier one that passes -- not a
    /// newer one that is damaged -- and the export says it is not fresh.
    #[test]
    fn an_export_falls_back_past_a_damaged_backup() {
        let tmp = std::env::temp_dir().join(format!("lempi-bk-fallback-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&tmp);
        std::fs::create_dir_all(&tmp).unwrap();
        let good = snapshot(&library(&tmp)).unwrap();
        let dir = good.parent().unwrap().to_path_buf();
        let newer = dir.join(format!("listener-{}.db", stamp_of(&good).unwrap() + 1000));
        std::fs::write(&newer, b"not a database at all").unwrap();
        // A listener database that cannot be read: no fresh snapshot.
        let e = for_export(&tmp.join("absent.db")).unwrap();
        assert!(!e.fresh, "not fresh, and said so");
        assert_eq!(e.path, good, "the newest that passes, not the damaged newer one");
        assert_eq!(e.summary.plays, 2);
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A backup that vanishes when a table is missing is worse than useless.
    #[test]
    fn a_missing_table_is_skipped_rather_than_fatal() {
        let tmp = std::env::temp_dir().join(format!(
            "lempi-bk2-{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let db = library(&tmp);           // has no listener_likes at all
        let out = snapshot(&db).expect("a partial schema still backs up");
        assert!(out.exists());
        std::fs::remove_dir_all(&tmp).ok();
    }


    /// A fresh directory for one test's files.
    fn scratch(tag: &str) -> PathBuf {
        let tmp = std::env::temp_dir().join(format!(
            "lempi-{tag}-{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        tmp
    }

    /// The point of the whole exercise: a Vipunen rebuild renumbers passages, and
    /// the history has to follow the RECORDING rather than the number, or years
    /// of listening are silently reattributed to whatever songs hold those ids
    /// now `[REQ-LIB-160]`.
    ///
    /// **On a split pair, as every installation is** `[PI-DB-030]`: the plays in
    /// the listener file, the recordings in the catalogue. The single-file
    /// fixture this test used to build is how F-01 went unseen.
    #[test]
    fn a_restore_follows_recordings_through_renumbering() {
        let tmp = scratch("rs");
        let listener = tmp.join("listener.db");
        let library = tmp.join("library.db");
        let l = Connection::open(&listener).unwrap();
        l.execute_batch(
            "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             INSERT INTO listener_play_history VALUES (1, 100, 10, 'rec-a');
             INSERT INTO listener_play_history VALUES (2, 200, 11, 'rec-b');
             INSERT INTO listener_play_history VALUES (3, 300, 12, 'rec-gone');",
        )
        .unwrap();
        let c = Connection::open(&library).unwrap();
        c.execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL);
             INSERT INTO passage_recordings VALUES (10, 'rec-a', 1.0);
             INSERT INTO passage_recordings VALUES (11, 'rec-b', 1.0);",
        )
        .unwrap();
        let snap = snapshot(&listener).expect("snapshot");

        // Vipunen rebuilds: same recordings, entirely different passage numbers.
        c.execute_batch(
            "DELETE FROM passage_recordings;
             INSERT INTO passage_recordings VALUES (77, 'rec-a', 1.0);
             INSERT INTO passage_recordings VALUES (88, 'rec-b', 1.0);",
        )
        .unwrap();
        l.execute_batch("DELETE FROM listener_play_history;").unwrap();

        let dry = restore(&snap, &listener, &library, false).expect("rehearsal");
        assert_eq!(dry.plays, 3);
        assert_eq!(dry.remapped, 2, "two plays moved to new passage ids");
        assert_eq!(dry.orphaned, 1, "one recording is no longer in the library");
        assert!(!dry.committed);
        let still: i64 = l
            .query_row("SELECT COUNT(*) FROM listener_play_history", [], |r| r.get(0))
            .unwrap();
        assert_eq!(still, 0, "a rehearsal must not write");

        let done = restore(&snap, &listener, &library, true).expect("restore");
        assert!(done.committed);
        let at: i64 = l
            .query_row("SELECT passage_id FROM listener_play_history WHERE mbid='rec-a'",
                       [], |r| r.get(0))
            .unwrap();
        assert_eq!(at, 77, "the play followed its recording, not its old number");
        // A play whose recording has left the library still happened, and is
        // kept rather than tidied away for the sake of a foreign key.
        let orphan: i64 = l
            .query_row("SELECT COUNT(*) FROM listener_play_history WHERE mbid='rec-gone'",
                       [], |r| r.get(0))
            .unwrap();
        assert_eq!(orphan, 1);
        // And the catalogue was only read.
        let recs: i64 = c
            .query_row("SELECT COUNT(*) FROM passage_recordings", [], |r| r.get(0))
            .unwrap();
        assert_eq!(recs, 2, "the catalogue is untouched");
        drop((l, c));
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A play whose passage still carries its recording is not moved, and each
    /// play counts once however many passages hold its recording. Measured on
    /// a real snapshot: 42,583 "re-pointed" of 39,408 plays, because the count
    /// was a join and the write moved every play to an arbitrary passage of
    /// its recording.
    #[test]
    fn a_restore_moves_only_the_plays_whose_passage_lost_their_recording() {
        let tmp = scratch("rs-keep");
        let listener = tmp.join("listener.db");
        let library = tmp.join("library.db");
        let l = Connection::open(&listener).unwrap();
        l.execute_batch(
            "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             INSERT INTO listener_play_history VALUES (1, 100, 20, 'rec-a');
             INSERT INTO listener_play_history VALUES (2, 200, 15, 'rec-a');",
        )
        .unwrap();
        // rec-a is an album cut (10), a single (20) and part of a medley (5);
        // passage 15 is gone.
        let c = Connection::open(&library).unwrap();
        c.execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL);
             INSERT INTO passage_recordings VALUES (5, 'rec-a', 0.3);
             INSERT INTO passage_recordings VALUES (10, 'rec-a', 1.0);
             INSERT INTO passage_recordings VALUES (20, 'rec-a', 1.0);",
        )
        .unwrap();
        let snap = snapshot(&listener).expect("snapshot");

        let dry = restore(&snap, &listener, &library, false).expect("rehearsal");
        assert_eq!(dry.plays, 2);
        assert_eq!(dry.remapped, 1, "only the play on the vanished passage moves, once");

        let done = restore(&snap, &listener, &library, true).expect("restore");
        assert_eq!(done.remapped, dry.remapped);
        let at = |id: i64| -> i64 {
            l.query_row("SELECT passage_id FROM listener_play_history WHERE play_id=?1",
                        [id], |r| r.get(0))
                .unwrap()
        };
        assert_eq!(at(1), 20, "the single still carries the recording: the play stays");
        assert_eq!(at(2), 10, "the whole recording before the medley, then the lowest id");
        drop((l, c));
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A listener file and a catalogue, and a snapshot written by hand in an
    /// older or newer shape than the file's: what a yearly snapshot is after a
    /// migration.
    fn shapes(tag: &str, file: &str, snap: &str) -> (PathBuf, PathBuf, PathBuf, PathBuf) {
        let tmp = scratch(tag);
        let (listener, library, snapshot) =
            (tmp.join("listener.db"), tmp.join("library.db"), tmp.join("old.db"));
        Connection::open(&listener).unwrap().execute_batch(file).unwrap();
        Connection::open(&library).unwrap()
            .execute_batch("CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL);")
            .unwrap();
        Connection::open(&snapshot).unwrap().execute_batch(snap).unwrap();
        (tmp, listener, library, snapshot)
    }

    /// `listener_occasions.label` was added by a migration, so every snapshot
    /// from before it lacks the column. It restores, by name, and the new
    /// column takes its default.
    #[test]
    fn a_snapshot_from_before_a_column_was_added_restores() {
        let (tmp, listener, library, snap) = shapes(
            "rs-older",
            "CREATE TABLE listener_occasions (occasion_id INTEGER PRIMARY KEY, name TEXT, label TEXT);
             INSERT INTO listener_occasions VALUES (9, 'today', 'Today');",
            "CREATE TABLE listener_occasions (occasion_id INTEGER PRIMARY KEY, name TEXT);
             INSERT INTO listener_occasions VALUES (1, 'christmas');",
        );
        let dry = restore(&snap, &listener, &library, false).expect("rehearsal");
        assert_eq!(dry.tables, 1);
        let done = restore(&snap, &listener, &library, true).expect("an older snapshot restores");
        assert_eq!(done.tables, dry.tables, "the rehearsal counts what the restore does");
        let rows: Vec<(i64, String, Option<String>)> = Connection::open(&listener).unwrap()
            .prepare("SELECT occasion_id, name, label FROM listener_occasions").unwrap()
            .query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).unwrap()
            .collect::<Result<_, _>>().unwrap();
        assert_eq!(rows, vec![(1, "christmas".to_string(), None)]);
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A renamed column keeps its place, so it is copied by position, as every
    /// table was before: `track_time_scale` became `recording_time_scale`.
    #[test]
    fn a_snapshot_from_before_a_column_was_renamed_restores() {
        let (tmp, listener, library, snap) = shapes(
            "rs-renamed",
            "CREATE TABLE listener_settings (id INTEGER PRIMARY KEY, recording_time_scale REAL);",
            "CREATE TABLE listener_settings (id INTEGER PRIMARY KEY, track_time_scale REAL);
             INSERT INTO listener_settings VALUES (1, 0.25);",
        );
        restore(&snap, &listener, &library, true).expect("a renamed column restores");
        let v: f64 = Connection::open(&listener).unwrap()
            .query_row("SELECT recording_time_scale FROM listener_settings", [], |r| r.get(0))
            .unwrap();
        assert_eq!(v, 0.25);
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A snapshot holding a column the file has nowhere to put is refused, in
    /// the rehearsal as in the restore, and the file is left as it was.
    #[test]
    fn a_snapshot_with_a_column_the_file_lacks_is_refused() {
        let (tmp, listener, library, snap) = shapes(
            "rs-newer",
            "CREATE TABLE listener_likes (like_id INTEGER PRIMARY KEY, mbid TEXT);
             INSERT INTO listener_likes VALUES (7, 'keep');",
            "CREATE TABLE listener_likes (like_id INTEGER PRIMARY KEY, mbid TEXT, why TEXT);
             INSERT INTO listener_likes VALUES (1, 'x', 'y');",
        );
        let said = restore(&snap, &listener, &library, false).expect_err("the rehearsal refuses");
        assert!(said.to_string().contains("listener_likes has why"), "{said}");
        restore(&snap, &listener, &library, true).expect_err("and so does the restore");
        let kept: String = Connection::open(&listener).unwrap()
            .query_row("SELECT mbid FROM listener_likes", [], |r| r.get(0))
            .unwrap();
        assert_eq!(kept, "keep");
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A split pair with one play, snapshotted.
    fn one_play(tag: &str) -> (PathBuf, PathBuf, PathBuf, String) {
        let tmp = scratch(tag);
        let (listener, library) = (tmp.join("listener.db"), tmp.join("library.db"));
        Connection::open(&listener).unwrap().execute_batch(
            "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             INSERT INTO listener_play_history VALUES (1, 100, 10, 'rec-a');",
        ).unwrap();
        Connection::open(&library).unwrap().execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL);
             INSERT INTO passage_recordings VALUES (10, 'rec-a', 1.0);",
        ).unwrap();
        let snap = snapshot(&listener).unwrap();
        let name = snap.file_name().unwrap().to_string_lossy().into_owned();
        (tmp, listener, library, name)
    }

    fn plays(listener: &Path) -> i64 {
        Connection::open(listener).unwrap()
            .query_row("SELECT COUNT(*) FROM listener_play_history", [], |r| r.get(0))
            .unwrap()
    }

    /// The page's restore: staged, then put back as the player starts, with
    /// the listening as it was kept first -- and only once, since a stage left
    /// behind would put the same snapshot back at every start.
    #[test]
    fn a_staged_restore_is_put_back_once_at_the_next_start() {
        let (tmp, listener, library, name) = one_play("stage");
        Connection::open(&listener).unwrap()
            .execute_batch("DELETE FROM listener_play_history;").unwrap();
        assert!(apply_staged(&listener, &library).is_none(), "nothing staged, nothing done");

        let report = stage(&listener, &library, &name).expect("stage");
        assert_eq!(report.plays, 1, "staging says what the restore will do");
        assert_eq!(staged(&listener).as_deref(), Some(name.as_str()));
        assert_eq!(plays(&listener), 0, "staging writes nothing to the listening");

        let o = apply_staged(&listener, &library).expect("applied");
        assert!(o.restored, "{}", o.said);
        assert_eq!(plays(&listener), 1);
        assert!(staged(&listener).is_none(), "the stage is gone");
        assert!(held(&listener).iter().any(|h| h.before_restore), "the state before was kept");
        assert_eq!(last_outcome(&listener).as_ref(), Some(&o), "and the page can say what happened");

        Connection::open(&listener).unwrap()
            .execute_batch("INSERT INTO listener_play_history VALUES (2, 200, 10, 'rec-a');").unwrap();
        assert!(apply_staged(&listener, &library).is_none(), "a second start restores nothing");
        assert_eq!(plays(&listener), 2, "and the play since is still there");
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A staged snapshot that has gone by the next start is said, not retried.
    #[test]
    fn a_staged_snapshot_that_has_gone_is_reported_once() {
        let (tmp, listener, library, name) = one_play("stage-gone");
        stage(&listener, &library, &name).expect("stage");
        std::fs::remove_file(dir_for(&listener).join(&name)).unwrap();
        let o = apply_staged(&listener, &library).expect("reported");
        assert!(!o.restored && o.said.contains("no longer there"), "{}", o.said);
        assert!(staged(&listener).is_none());
        assert!(!held(&listener).iter().any(|h| h.before_restore), "nothing to keep a copy for");
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// Only a name the directory lists can be staged, and a snapshot whose
    /// rehearsal fails is refused while someone is looking.
    #[test]
    fn only_a_listed_restorable_snapshot_can_be_staged() {
        let (tmp, listener, library, _) = one_play("stage-refuse");
        for name in ["listener-9.db", "../listener.db", "restore-staged", ""] {
            assert!(stage(&listener, &library, name).is_err(), "{name:?} was staged");
        }
        std::fs::write(dir_for(&listener).join("listener-5.db"), b"not a database").unwrap();
        assert!(stage(&listener, &library, "listener-5.db").is_err(), "an unreadable snapshot");
        assert!(staged(&listener).is_none());
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A catalogue the history cannot be re-pointed through is an error, in a
    /// rehearsal as much as a commit -- never a report of zero moved and zero
    /// orphaned that looks like a clean answer (review O-04).
    #[test]
    fn a_restore_without_recordings_to_follow_is_an_error_not_a_zero() {
        let tmp = scratch("rs-norec");
        let listener = tmp.join("listener.db");
        let library = tmp.join("library.db");
        Connection::open(&listener).unwrap().execute_batch(
            "CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             INSERT INTO listener_play_history VALUES (1, 100, 10, 'rec-a');",
        )
        .unwrap();
        Connection::open(&library).unwrap()
            .execute_batch("CREATE TABLE files (file_id INTEGER PRIMARY KEY);")
            .unwrap();
        let snap = snapshot(&listener).expect("snapshot");
        let err = restore(&snap, &listener, &library, false).expect_err("rehearsal");
        assert!(err.message().contains("passage_recordings"), "says what is missing: {err}");
        assert!(restore(&snap, &listener, &library, true).is_err(), "and a commit refuses too");
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// A snapshot has to be readable before it is trusted.
    #[test]
    fn a_snapshot_can_be_inspected_without_restoring_it() {
        let tmp = std::env::temp_dir().join(format!(
            "lempi-in-{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let db = library(&tmp);
        let snap = snapshot(&db).unwrap();
        let s = inspect(&snap).expect("inspect");
        assert_eq!(s.plays, 2);
        assert_eq!(s.first_play, Some(100));
        assert_eq!(s.last_play, Some(200));
        std::fs::remove_dir_all(&tmp).ok();
    }
    /// The date arithmetic has to be exact, because a snapshot filed under the
    /// wrong year is one that survives when it should go, or goes when it is
    /// the only copy of that year.
    #[test]
    fn civil_dates_are_exact_at_the_awkward_boundaries() {
        assert_eq!(civil(0), (1970, 1, 1));
        assert_eq!(civil(86_399), (1970, 1, 1), "one second before midnight");
        assert_eq!(civil(86_400), (1970, 1, 2));
        assert_eq!(civil(951_782_400), (2000, 2, 29), "a leap day in a leap century");
        assert_eq!(civil(1_709_164_800), (2024, 2, 29));
        assert_eq!(civil(1_735_689_599), (2024, 12, 31), "one second before new year");
        assert_eq!(civil(1_735_689_600), (2025, 1, 1));
    }

    /// Three years of hourly snapshots must thin to a ladder, not a pile.
    #[test]
    fn retention_keeps_a_day_a_month_and_a_year() {
        let tmp = std::env::temp_dir().join(format!(
            "lempi-bk3-{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let dir = tmp.join("listener-backups");
        std::fs::create_dir_all(&dir).unwrap();

        // Every six hours for three years, ending "now". Six-hourly rather
        // than hourly only to keep the test quick: it exercises every tier
        // identically and writes a quarter of the files.
        let now = 1_800_000_000i64;
        let mut made = 0;
        for h in 0..(4 * 365 * 3) {
            let t = now - h * 21_600;
            std::fs::write(dir.join(format!("listener-{t}.db")), b"x").unwrap();
            made += 1;
        }
        prune(&dir);

        let left: Vec<i64> = std::fs::read_dir(&dir)
            .unwrap()
            .filter_map(|e| stamp_of(&e.ok()?.path()))
            .collect();
        // Seven days + twelve months + four calendar years, minus the overlaps
        // where one file satisfies several tiers at once.
        assert!(made > 4_000, "the pile really was a pile");
        assert!(left.len() <= KEEP_DAYS + KEEP_MONTHS + 5,
                "thinned to a ladder, got {}", left.len());
        assert!(left.len() >= KEEP_DAYS, "and the recent week survives: {}", left.len());
        assert!(left.contains(&now), "the newest is always kept");

        // A snapshot from each of the three years must remain, or "one per
        // year indefinitely" is not what happened.
        let years: std::collections::HashSet<i64> =
            left.iter().map(|t| civil(*t).0).collect();
        assert!(years.len() >= 3, "one per year survives: {years:?}");
        std::fs::remove_dir_all(&tmp).ok();
    }
}
