//! The programme, its slots and their seeds `[SPEC-DIR-140]`, `[SPEC-DIR-180]`,
//! `[REQ-PD-140]`, `[SPEC-PGM-010]`.
//!
//! A **programme** is a whole day: a set of **programme slots**, each with a
//! start time and its own seeds. A slot runs from its start until the next
//! slot's start, so the slots always cover the day with no gap. The seeds are
//! exemplar recordings, not a set of tuned parameters. Naming songs beats
//! moving sliders, and it is the mechanism MuLibPlay's user actually exercised
//! — eight slots, 49 seeds, over six years.
//!
//! The slot in force is whichever most recently started. A **hard switch**,
//! deliberately: blending was rejected because the flow stage already smooths
//! transitions `[SPEC-DIR-160]` and blending would make "which slot am I in?"
//! ambiguous for a tunable nobody asked for.
//!
//! The tables keep their older names: `listener_programs` holds slots and
//! `program_id` is a slot's id.

use std::collections::{HashMap, HashSet};

use rusqlite::Connection;

use crate::db::DbError;

/// `[SPEC-DIR-195]`, proven.
pub const MAX_SEEDS: usize = 5;

const MINUTES_PER_DAY: i64 = 24 * 60;

/// The widest offset from UTC a stored value may hold: 18 hours either way,
/// the same limit a person may type `[SPEC-DIR-183]`. One number, read by
/// every place that loads the offset, so the limit cannot differ between them.
pub const UTC_OFFSET_MAX_MIN: i64 = 18 * 60;

/// One programme slot: a name, a start time, and (in [`Programme`]) its seeds.
#[derive(Debug, Clone)]
pub struct ProgrammeSlot {
    pub id: i64,
    pub name: String,
    /// Minutes past local midnight.
    pub start_minute: i64,
}

/// `HH:MM` to minutes past midnight. Returns `None` for anything unparseable,
/// so a malformed row drops out rather than silently becoming midnight and
/// winning every comparison.
fn parse_hhmm(s: &str) -> Option<i64> {
    let (h, m) = s.split_once(':')?;
    let h: i64 = h.trim().parse().ok()?;
    let m: i64 = m.trim().parse().ok()?;
    if !(0..24).contains(&h) || !(0..60).contains(&m) {
        return None;
    }
    Some(h * 60 + m)
}

/// What the operating system believes the local UTC offset is right now, in
/// minutes east of UTC `[SPEC-DIR-180]`.
///
/// `std` has no timezone of its own -- deliberately, per the schema comment
/// this exists to make good on ("the appliance stores its offset rather than
/// the player guessing"). Nothing ever wrote it, so every library sat at the
/// column's own default of 0 and every programme was chosen against raw UTC
/// clock time instead of the listener's own. The OS already knows its
/// offset; asking it -- at start, and hourly after that, so a change of
/// daylight saving time lands without a restart -- is simpler and more honest
/// than asking a person to type a number nobody enjoys getting right twice a
/// year.
///
/// `None` on a platform this cannot ask, or if the ask fails -- the caller's
/// job is to fall back to whatever is already stored, not to invent one.
#[cfg(unix)]
pub fn os_utc_offset_minutes() -> Option<i64> {
    // SAFETY: `tm` is zeroed before use and `localtime_r` only ever writes
    // into it; `now` is a valid `time_t` just read from the same call.
    unsafe {
        let now = libc::time(std::ptr::null_mut());
        let mut tm: libc::tm = std::mem::zeroed();
        if libc::localtime_r(&now, &mut tm).is_null() {
            return None;
        }
        Some(tm.tm_gmtoff / 60)
    }
}

/// The Windows CRT's `tm` carries no `tm_gmtoff` `[SPEC-DIR-180]`, so this asks
/// the system directly. `GetTimeZoneInformation` gives its biases in minutes
/// west of UTC and says which of the zone's two is in force now; the offset
/// east of UTC is the negated sum. It replaced a PowerShell child process that cost ~750 ms
/// at every start and was far too slow to ask hourly (review F-09,
/// 2026-10-08). Best-effort like its Unix sibling: an invalid answer is
/// `None`, never a guess.
#[cfg(windows)]
pub fn os_utc_offset_minutes() -> Option<i64> {
    use windows_sys::Win32::System::Time::{
        GetTimeZoneInformation, TIME_ZONE_ID_INVALID, TIME_ZONE_INFORMATION,
    };
    let mut tz = TIME_ZONE_INFORMATION::default();
    // SAFETY: `tz` is a valid, zeroed `TIME_ZONE_INFORMATION` the call only
    // writes into, and it outlives the call.
    let id = unsafe { GetTimeZoneInformation(&mut tz) };
    // 0 is TIME_ZONE_ID_UNKNOWN (a zone with no daylight rules), 1 STANDARD,
    // 2 DAYLIGHT; windows-sys names only the invalid one.
    let extra = match id {
        TIME_ZONE_ID_INVALID => return None,
        1 => tz.StandardBias,
        2 => tz.DaylightBias,
        _ => 0,
    };
    Some(-(i64::from(tz.Bias) + i64::from(extra)))
}

#[cfg(not(any(unix, windows)))]
pub fn os_utc_offset_minutes() -> Option<i64> {
    None
}

/// The whole day's programme: its slots, each slot's seeds, and which slot is
/// held by hand.
pub struct Programme {
    slots: Vec<ProgrammeSlot>,
    /// Slot id → its seeds' recording MBIDs, in the listener's order.
    seeds: HashMap<i64, Vec<String>>,
    /// Minutes to add to UTC to get local wall-clock time.
    ///
    /// Programme times are wall-clock — a 22:00 "Mellow" means ten at night
    /// where the listener is, not in Greenwich. There is no timezone in `std`,
    /// so the appliance stores its offset rather than the player guessing.
    pub utc_offset_minutes: i64,
    /// Set by the listener, overriding the clock until they revert
    /// `[SPEC-DIR-185]`.
    manual: Option<i64>,
}

impl Programme {
    pub fn is_empty(&self) -> bool {
        self.slots.is_empty()
    }
    pub fn len(&self) -> usize {
        self.slots.len()
    }
    pub fn slots(&self) -> &[ProgrammeSlot] {
        &self.slots
    }
    pub fn slot(&self, id: i64) -> Option<&ProgrammeSlot> {
        self.slots.iter().find(|p| p.id == id)
    }

    /// A slot's seeds as stored, before any down-selection.
    pub fn seeds_of(&self, slot_id: i64) -> &[String] {
        self.seeds.get(&slot_id).map(Vec::as_slice).unwrap_or(&[])
    }

    /// Unix seconds moved onto the listener's wall clock, for anything that
    /// asks "what time of day, what day of the year" `[SPEC-DIR-180]`,
    /// `[SPEC-DIR-132]`: slots and seasons both read local time.
    pub fn local_secs(&self, now: i64) -> i64 {
        now + self.utc_offset_minutes * 60
    }

    /// Choose a slot by hand, overriding time of day `[SPEC-DIR-185]`.
    pub fn set_manual_slot(&mut self, id: Option<i64>) {
        self.manual = id;
    }
    pub fn manual_slot(&self) -> Option<i64> {
        self.manual
    }

    /// Take the time slots of an edited programme `[SPEC-PGM-400]`, keeping
    /// what this Director knows that the tables do not: the clock's offset,
    /// and a slot held by hand -- unless that slot is gone, when the hold goes
    /// with it and time of day decides again.
    pub fn replace_slots(&mut self, fresh: Programme) {
        let manual = self.manual.filter(|id| fresh.slot(*id).is_some());
        let offset = self.utc_offset_minutes;
        *self = Programme { utc_offset_minutes: offset, manual, ..fresh };
    }

    /// The slot in force at `now` (unix seconds).
    ///
    /// Whichever started most recently, wrapping through midnight — before the
    /// first start time of the day, the one that began last night is still
    /// running.
    pub fn active_slot(&self, now: i64) -> Option<&ProgrammeSlot> {
        if let Some(id) = self.manual {
            if let Some(p) = self.slot(id) {
                return Some(p);
            }
        }
        if self.slots.is_empty() {
            return None;
        }
        let local = (self.local_secs(now) / 60).rem_euclid(MINUTES_PER_DAY);
        // Largest start <= now, else the latest of the day (yesterday's).
        self.slots
            .iter()
            .filter(|p| p.start_minute <= local)
            .max_by_key(|p| p.start_minute)
            .or_else(|| self.slots.iter().max_by_key(|p| p.start_minute))
    }

    /// The seeds of a slot, down-selected `[SPEC-DIR-140]`.
    ///
    /// One per artist, preferring the least-recently-played, then capped at
    /// `MAX_SEEDS`. Preferring the least-recently-played keeps a slot from
    /// being defined by whatever it happened to play an hour ago, and makes the
    /// target drift slowly around the exemplars rather than sitting still.
    ///
    /// Only seeds `usable` accepts are considered -- the Director passes "has
    /// flavor". A seed without flavor shapes nothing, so it is passed over
    /// **before** the cap rather than taking a place a seed with flavor could
    /// fill `[SPEC-DIR-142]`.
    ///
    /// A seed crediting several artists takes all of them: once a Prince +
    /// The Time seed is chosen, neither a Prince seed nor a The Time seed
    /// follows it. Walking the seeds least-recently-played first and taking
    /// each whose artists are all still free is what keeps that a choice of
    /// the stalest, and for single-artist seeds it picks exactly what one
    /// best-per-artist pass did.
    pub fn seeds_for(
        &self,
        slot_id: i64,
        artist_of: &HashMap<String, Vec<String>>,
        last_played: &HashMap<String, i64>,
        usable: impl Fn(&str) -> bool,
    ) -> Vec<String> {
        let Some(all) = self.seeds.get(&slot_id) else { return Vec::new() };

        // Ordering is by (last played, mbid). The mbid breaks ties so the
        // choice is stable: two never-played seeds must not swap between runs.
        let key = |m: &String| (last_played.get(m).copied().unwrap_or(i64::MIN), m.clone());

        let mut ordered: Vec<&String> = all.iter().filter(|m| usable(m)).collect();
        ordered.sort_by_key(|m| key(m));
        let mut taken: HashSet<&str> = HashSet::new();
        let mut chosen: Vec<String> = Vec::new();
        for m in ordered {
            if chosen.len() == MAX_SEEDS {
                break;
            }
            // No known artist: it cannot collide with anyone, so it stands
            // on its own rather than being grouped under a shared "unknown".
            let artists = artist_of.get(m).map(Vec::as_slice).unwrap_or(&[]);
            if artists.iter().any(|a| taken.contains(a.as_str())) {
                continue;
            }
            taken.extend(artists.iter().map(String::as_str));
            chosen.push(m.clone());
        }
        chosen
    }

    /// The programme as stored. **A table that is absent is a library from
    /// before programmes**, and reads as none; any other failure is an error
    /// `[CLAUDE.md §6]`. It read as an empty programme until 2026-11-01, which
    /// a running Director then took as an edit: a read that failed for a
    /// moment replaced every slot with nothing, and no seed shaped a pick
    /// until the next edit.
    pub fn load(conn: &Connection) -> Result<Self, DbError> {
        // Absent is quiet; anything else is the caller's to hear.
        fn absent(e: &rusqlite::Error) -> bool {
            let m = e.to_string();
            m.contains("no such table") || m.contains("no such column")
        }
        let q = |e: rusqlite::Error| DbError::query(e).during("programme");
        // A row whose value will not convert is the row's fault and is left
        // out, said; a read that fails is the read's, and is an error.
        fn rows_of<T>(
            it: impl Iterator<Item = rusqlite::Result<T>>,
            what: &str,
        ) -> Result<Vec<T>, rusqlite::Error> {
            let mut out = Vec::new();
            for r in it {
                match r {
                    Ok(v) => out.push(v),
                    Err(e @ (rusqlite::Error::InvalidColumnType(..)
                    | rusqlite::Error::FromSqlConversionFailure(..)
                    | rusqlite::Error::IntegralValueOutOfRange(..))) => {
                        tracing::warn!("programme: a {what} row is malformed and left out: {e}")
                    }
                    Err(e) => return Err(e),
                }
            }
            Ok(out)
        }

        let mut slots = Vec::new();
        let rows: Vec<(i64, String, Option<String>)> =
            match conn.prepare("SELECT program_id, name, start_time FROM listener_programs") {
                Ok(mut stmt) => rows_of(
                    stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?))).map_err(q)?,
                    "slot",
                )
                .map_err(q)?,
                Err(e) if absent(&e) => Vec::new(),
                Err(e) => return Err(q(e)),
            };
        for (id, name, start) in rows {
            match start.as_deref().and_then(parse_hhmm) {
                Some(start_minute) => slots.push(ProgrammeSlot { id, name, start_minute }),
                // Every slot has a start `[ENT-SLOT-010]`, and the page and the
                // hub refuse one without. A row that has none anyway -- a tool,
                // an older build -- is left out, and said, rather than
                // silently missing.
                None => tracing::warn!(
                    "programme: slot \"{name}\" has no usable start time ({start:?}); \
                     left out, so it is never on air and cannot be held [ENT-SLOT-010]"
                ),
            }
        }

        let mut seeds: HashMap<i64, Vec<String>> = HashMap::new();
        let pairs: Vec<(i64, String)> = match conn
            .prepare("SELECT program_id, mbid FROM listener_program_seeds ORDER BY position")
        {
            Ok(mut stmt) => {
                rows_of(stmt.query_map([], |r| Ok((r.get(0)?, r.get(1)?))).map_err(q)?, "seed")
                    .map_err(q)?
            }
            Err(e) if absent(&e) => Vec::new(),
            Err(e) => return Err(q(e)),
        };
        for (id, mbid) in pairs {
            seeds.entry(id).or_default().push(mbid);
        }

        // No row is a library never set, at the column's own default.
        let utc_offset_minutes = match conn.query_row(
            "SELECT utc_offset_minutes FROM listener_settings WHERE id = 1",
            [],
            |r| r.get::<_, i64>(0),
        ) {
            Ok(m) => m,
            Err(rusqlite::Error::QueryReturnedNoRows) => 0,
            Err(e) if absent(&e) => 0,
            Err(e) => return Err(q(e)),
        }
        .clamp(-UTC_OFFSET_MAX_MIN, UTC_OFFSET_MAX_MIN);

        Ok(Self { slots, seeds, utc_offset_minutes, manual: None })
    }
}

/// Bring `listener_settings.utc_offset_minutes` into line with what the OS
/// believes right now, if it differs `[SPEC-DIR-180]`.
///
/// **Deliberately not part of `load`.** `load` is exercised directly by a
/// great many fixture-backed tests across this module and `library.rs`, and
/// every one of them passes a connection with no listener_settings row at
/// all -- relying on that reading as offset 0 to keep their time-of-day
/// assertions independent of whichever timezone happens to run the suite.
/// Folding a real OS call into `load` would have made every one of them
/// depend on the machine running `cargo test`. It is called from the
/// production path only: at start, before `load` ever reads the column, and
/// hourly from `Session` so that a change of daylight saving time reaches the
/// running Director too.
///
/// Returns what the OS said, written or already stored, so that caller can
/// update the Director it holds; `None` when the OS could not be asked.
///
/// Best-effort like everything else that touches this table off the audio
/// path: a write that fails here costs a stale offset until the next
/// restart, not a player that will not start.
pub fn sync_os_utc_offset(conn: &Connection) -> Option<i64> {
    let os = os_utc_offset_minutes()?;
    let stored: i64 = conn
        .query_row("SELECT utc_offset_minutes FROM listener_settings WHERE id = 1", [], |r| {
            r.get(0)
        })
        .unwrap_or(0);
    if os == stored {
        return Some(os);
    }
    // `updated_at` is carried even though nothing reads it back, because the
    // row may not exist yet on a library old enough to predate this column,
    // and the table's own `NOT NULL` on it has no default -- an INSERT that
    // omitted it would fail exactly on the library that most needs this to
    // work.
    let _ = conn.execute(
        "INSERT INTO listener_settings (id, utc_offset_minutes, updated_at) \
         VALUES (1, ?1, datetime('now')) \
         ON CONFLICT(id) DO UPDATE SET \
             utc_offset_minutes = excluded.utc_offset_minutes, \
             updated_at = excluded.updated_at",
        [os],
    );
    Some(os)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::QualifyingConn;

    /// An edit's time slots replace the Director's; its clock offset stays,
    /// and so does a slot held by hand -- unless the edit removed it
    /// `[SPEC-PGM-400]`.
    #[test]
    fn an_edit_s_slots_replace_these_keeping_the_offset_and_a_live_hold() {
        let mut running = Programme::load(&fixture()).unwrap();
        running.utc_offset_minutes = -240;
        running.set_manual_slot(Some(4));
        let edited = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        edited
            .execute_batch(
                "CREATE TABLE listener_programs (program_id INTEGER, name TEXT, start_time TEXT);
                 CREATE TABLE listener_program_seeds (program_id INTEGER, mbid TEXT, position INTEGER);
                 INSERT INTO listener_programs VALUES (4,'Late','23:00'),(9,'Dawn','05:00');",
            )
            .unwrap();
        running.replace_slots(Programme::load(&edited).unwrap());
        assert_eq!(running.len(), 2);
        assert_eq!(running.utc_offset_minutes, -240, "the offset is the clock's, not the edit's");
        assert_eq!(running.manual_slot(), Some(4), "the held slot is still there");
        edited.execute_batch("DELETE FROM listener_programs WHERE program_id = 4").unwrap();
        running.replace_slots(Programme::load(&edited).unwrap());
        assert_eq!(running.manual_slot(), None, "the held slot is gone, and so is the hold");
    }

    /// A read that fails is an error, never an empty programme a running
    /// Director would take as an edit; a table that is absent is a library
    /// from before programmes, and a malformed row is left out.
    #[test]
    fn a_failed_read_is_an_error_and_absence_is_not() {
        let none = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        assert!(Programme::load(&none).unwrap().is_empty(), "no tables: no programme, quietly");

        let bad = fixture();
        bad.execute_batch("INSERT INTO listener_programs VALUES ('x', 'Odd', '06:00');").unwrap();
        let p = Programme::load(&bad).unwrap();
        assert_eq!(p.len(), 4, "the malformed row is left out and the rest stand");

        // A read that fails while it runs -- here, an overflow SQLite raises
        // as it steps -- rather than one whose table is absent.
        let broken = fixture();
        broken.execute_batch("DROP TABLE listener_program_seeds;
                              CREATE VIEW listener_program_seeds AS
                              SELECT 1 AS program_id, 'm' AS mbid, abs(-9223372036854775808) AS position;").unwrap();
        assert!(Programme::load(&broken).is_err(), "a read that fails is not an empty programme");
    }

    fn fixture() -> QualifyingConn {
        let c = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        c.execute_batch(
            "CREATE TABLE listener_programs (program_id INTEGER, name TEXT, start_time TEXT);
             CREATE TABLE listener_program_seeds (program_id INTEGER, mbid TEXT, position INTEGER);
             CREATE TABLE listener_settings (id INTEGER PRIMARY KEY, utc_offset_minutes INTEGER);
             INSERT INTO listener_programs VALUES (1,'Soft','04:00'),(2,'Light','10:00'),
                 (3,'Cool','12:00'),(4,'Mellow','22:00'),(5,'Broken','not a time');
             INSERT INTO listener_program_seeds VALUES
                 (1,'a',0),(1,'b',1),(1,'c',2),(1,'d',3),(1,'e',4),(1,'f',5),(1,'g',6);",
        )
        .unwrap();
        c
    }

    /// Unix 0 is a Thursday midnight UTC.
    fn at(hour: i64, minute: i64) -> i64 {
        (hour * 60 + minute) * 60
    }

    /// The bug this exists to fix `[SPEC-DIR-180]`: a library whose offset was
    /// never set reads programmes against raw UTC clock time, and a listener
    /// anywhere but UTC gets the wrong one on. Corrected once, from a stored
    /// value that disagrees with what the OS actually reports.
    #[test]
    fn sync_os_utc_offset_corrects_a_mismatch_and_leaves_a_match_alone() {
        let Some(os) = os_utc_offset_minutes() else {
            return; // nothing to test on a platform this cannot ask
        };
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE listener_settings (id INTEGER PRIMARY KEY,
                 utc_offset_minutes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);",
        )
        .unwrap();
        // Deliberately wrong, and still a legal offset either side of it.
        let wrong = if os == 0 { 60 } else { 0 };
        c.execute(
            "INSERT INTO listener_settings (id, utc_offset_minutes, updated_at) VALUES (1, ?1, 't')",
            [wrong],
        )
        .unwrap();

        assert_eq!(sync_os_utc_offset(&c), Some(os), "it reports what the OS said");
        let read = |c: &Connection| -> i64 {
            c.query_row("SELECT utc_offset_minutes FROM listener_settings WHERE id = 1", [], |r| {
                r.get(0)
            })
            .unwrap()
        };
        assert_eq!(read(&c), os, "a stored mismatch must be corrected to what the OS reports");

        // Calling again with an already-correct value must not error or drift.
        sync_os_utc_offset(&c);
        assert_eq!(read(&c), os);
    }

    /// A library that predates this column has no row at all -- the read
    /// this already tolerates via `unwrap_or(0)`, and the write must too,
    /// or the fix would silently never apply to the libraries needing it.
    #[test]
    fn sync_os_utc_offset_creates_the_row_when_absent() {
        let Some(os) = os_utc_offset_minutes() else { return };
        if os == 0 {
            // The unconfigured default already agrees with this machine's
            // real offset; there is nothing here for the test to tell apart.
            return;
        }
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(
            "CREATE TABLE listener_settings (id INTEGER PRIMARY KEY,
                 utc_offset_minutes INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);",
        )
        .unwrap();

        sync_os_utc_offset(&c);
        let after: i64 = c
            .query_row("SELECT utc_offset_minutes FROM listener_settings WHERE id = 1", [], |r| {
                r.get(0)
            })
            .unwrap();
        assert_eq!(after, os);
    }

    #[test]
    fn a_malformed_start_time_drops_the_slot() {
        let p = Programme::load(&fixture()).unwrap();
        assert_eq!(p.len(), 4, "'not a time' must not become midnight and win every comparison");
    }

    #[test]
    fn the_active_slot_is_the_one_that_started_most_recently() {
        let p = Programme::load(&fixture()).unwrap();
        assert_eq!(p.active_slot(at(4, 0)).unwrap().name, "Soft", "exactly at its start");
        assert_eq!(p.active_slot(at(9, 59)).unwrap().name, "Soft");
        assert_eq!(p.active_slot(at(10, 0)).unwrap().name, "Light");
        assert_eq!(p.active_slot(at(13, 30)).unwrap().name, "Cool");
        assert_eq!(p.active_slot(at(23, 59)).unwrap().name, "Mellow");
    }

    /// Before the first start of the day, last night's programme is still on.
    #[test]
    fn the_small_hours_belong_to_yesterdays_programme() {
        let p = Programme::load(&fixture()).unwrap();
        assert_eq!(p.active_slot(at(2, 0)).unwrap().name, "Mellow", "22:00 is still running at 02:00");
        assert_eq!(p.active_slot(at(0, 0)).unwrap().name, "Mellow");
    }

    /// Programme times are wall-clock, so the offset must move them.
    #[test]
    fn the_utc_offset_shifts_the_schedule() {
        let c = fixture();
        c.execute("INSERT INTO listener_settings VALUES (1, -300)", []).unwrap(); // UTC-5
        let p = Programme::load(&c).unwrap();
        assert_eq!(p.utc_offset_minutes, -300);
        // 15:00 UTC is 10:00 local, so Light has just begun.
        assert_eq!(p.active_slot(at(15, 0)).unwrap().name, "Light");
        assert_eq!(p.active_slot(at(14, 59)).unwrap().name, "Soft");
    }

    #[test]
    fn a_manual_choice_overrides_the_clock() {
        let mut p = Programme::load(&fixture()).unwrap();
        assert_eq!(p.active_slot(at(13, 0)).unwrap().name, "Cool");
        p.set_manual_slot(Some(4));
        assert_eq!(p.active_slot(at(13, 0)).unwrap().name, "Mellow", "manual outranks time of day");
        p.set_manual_slot(None);
        assert_eq!(p.active_slot(at(13, 0)).unwrap().name, "Cool", "and reverting restores it");
    }

    /// A manual id that no longer exists must fall back to the clock rather
    /// than leaving the station with no slot at all.
    #[test]
    fn a_stale_manual_choice_falls_back_to_the_clock() {
        let mut p = Programme::load(&fixture()).unwrap();
        p.set_manual_slot(Some(999));
        assert_eq!(p.active_slot(at(13, 0)).unwrap().name, "Cool");
    }

    #[test]
    fn seeds_are_capped_and_one_per_artist() {
        let p = Programme::load(&fixture()).unwrap();
        let mut artist = HashMap::new();
        // a and b share an artist; the rest are distinct
        for (m, a) in [("a", "x"), ("b", "x"), ("c", "y"), ("d", "z"), ("e", "w"), ("f", "v"), ("g", "u")] {
            artist.insert(m.to_string(), vec![a.to_string()]);
        }
        let mut played = HashMap::new();
        played.insert("a".to_string(), 100i64); // a played recently, b never
        let seeds = p.seeds_for(1, &artist, &played, |_| true);
        assert_eq!(seeds.len(), MAX_SEEDS, "capped at max_seeds");
        assert!(seeds.contains(&"b".to_string()), "the least-recently-played of the pair");
        assert!(!seeds.contains(&"a".to_string()), "only one per artist");
    }

    /// Recently played seeds are the ones dropped by the cap, so the target
    /// drifts around the exemplars instead of sitting on whatever just played.
    #[test]
    fn the_cap_drops_the_most_recently_played() {
        let p = Programme::load(&fixture()).unwrap();
        let artist: HashMap<String, Vec<String>> =
            ["a", "b", "c", "d", "e", "f", "g"].iter().map(|m| (m.to_string(), vec![m.to_string()])).collect();
        let played: HashMap<String, i64> =
            [("a", 900i64), ("b", 800), ("c", 700)].iter().map(|(m, t)| (m.to_string(), *t)).collect();
        let seeds = p.seeds_for(1, &artist, &played, |_| true);
        assert_eq!(seeds.len(), MAX_SEEDS);
        assert!(!seeds.contains(&"a".to_string()), "the most recently played is dropped first");
    }

    #[test]
    fn seed_selection_is_stable_across_runs() {
        let p = Programme::load(&fixture()).unwrap();
        let artist: HashMap<String, Vec<String>> =
            ["a", "b", "c", "d", "e", "f", "g"].iter().map(|m| (m.to_string(), vec![m.to_string()])).collect();
        let played = HashMap::new(); // none played: every key ties on time
        let first = p.seeds_for(1, &artist, &played, |_| true);
        for _ in 0..5 {
            assert_eq!(p.seeds_for(1, &artist, &played, |_| true), first, "ties must break deterministically");
        }
    }

    /// A two-artist seed takes both artists: Prince + The Time rules out a
    /// later Prince seed and a later The Time seed alike.
    #[test]
    fn a_seed_with_several_artists_takes_each_of_them() {
        let p = Programme::load(&fixture()).unwrap();
        let mut artist: HashMap<String, Vec<String>> =
            ["d", "e", "f", "g"].iter().map(|m| (m.to_string(), vec![m.to_string()])).collect();
        artist.insert("a".into(), vec!["prince".into(), "the-time".into()]);
        artist.insert("b".into(), vec!["prince".into()]);
        artist.insert("c".into(), vec!["the-time".into()]);
        let seeds = p.seeds_for(1, &artist, &HashMap::new(), |_| true); // ties break by mbid: a first
        assert!(seeds.contains(&"a".to_string()));
        assert!(!seeds.contains(&"b".to_string()), "Prince is already taken");
        assert!(!seeds.contains(&"c".to_string()), "The Time is already taken");
    }

    #[test]
    fn an_unknown_slot_has_no_seeds() {
        let p = Programme::load(&fixture()).unwrap();
        assert!(p.seeds_for(99, &HashMap::new(), &HashMap::new(), |_| true).is_empty());
    }

    /// A seed without flavor is passed over before the cap, so it cannot take
    /// a place a seed with flavor would fill `[SPEC-DIR-142]`.
    #[test]
    fn a_seed_without_flavor_does_not_take_a_place() {
        let p = Programme::load(&fixture()).unwrap();
        let artist: HashMap<String, Vec<String>> =
            ["a", "b", "c", "d", "e", "f", "g"].iter().map(|m| (m.to_string(), vec![m.to_string()])).collect();
        // Never played, so ties break by mbid: a..e would be the five.
        let seeds = p.seeds_for(1, &artist, &HashMap::new(), |m| m != "a" && m != "b");
        assert_eq!(seeds.len(), MAX_SEEDS, "f and g fill the places a and b would have held");
        assert!(!seeds.contains(&"a".to_string()) && !seeds.contains(&"b".to_string()));
    }

    /// Slots and seasons read the listener's wall clock, not UTC.
    #[test]
    fn local_secs_carries_the_offset() {
        let mut p = Programme::load(&fixture()).unwrap();
        p.utc_offset_minutes = -300;
        assert_eq!(p.local_secs(at(5, 0)), at(0, 0), "05:00 UTC is midnight in UTC-5");
    }
}
