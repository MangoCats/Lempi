//! Which words a passage shows, and saving words for one `[SPEC-LYR-100]`.
//!
//! Two sources. `lyrics` is the catalogue's, keyed by recording, imported from
//! MuLibPlay and never written by the player `[SPEC-LYR-030]`. `passage_lyrics`
//! is the listener half's, written here, one entry for a passage
//! `[SPEC-LYR-140]`. A passage shows its own entry, else that of another
//! passage of the same segment of its file, else words for its recording, else
//! words for a work its recording performs.
//!
//! Everything here takes a plain connection and the catalogue's schema name
//! (`lib` on a split pair, `main` on a single file), because three callers hold
//! three kinds of connection: [`super::Library`] reads, [`super::PlayerStore`]
//! saves, and the files written for a guest's clients come from a connection
//! of the player's own `[SPEC-LYR-176]`.

use std::collections::{HashMap, HashSet};

use rusqlite::{params, Connection};

use super::DbError;

/// `[SPEC-LYR-140]`. No passage number: a number means nothing on another node
/// `[SPEC-DF-035]`, and this table goes to every node. The same text is in
/// `sql/schema.sql`, which is where a listener made from the schema gets it.
pub(crate) const PASSAGE_LYRICS_TABLE: &str = "
    CREATE TABLE IF NOT EXISTS passage_lyrics (
        audio_md5   TEXT    NOT NULL,
        kind        TEXT    NOT NULL,
        start_ms    INTEGER NOT NULL,
        end_ms      INTEGER NOT NULL,
        mbid        TEXT,
        text        TEXT    NOT NULL,
        no_words    INTEGER NOT NULL DEFAULT 0,
        updated_at  TEXT    NOT NULL,
        origin      TEXT,
        PRIMARY KEY (audio_md5, kind, start_ms));
    CREATE INDEX IF NOT EXISTS passage_lyrics_mbid ON passage_lyrics(mbid);";

/// The longest text a save accepts, in bytes `[SPEC-LYR-174]`. The longest held
/// on 2026-11-12 was 5,820 characters.
pub const LYRICS_MAX_BYTES: usize = 64 * 1024;

/// Why a save refuses `text` before reading anything, or `None` where it
/// does not: over [`LYRICS_MAX_BYTES`] as sent `[SPEC-LYR-174]`. The one
/// refusal that is the request's fault, so the route answers it 413 before
/// it opens the file, and the save refuses it too, by this same test.
pub fn lyrics_refusal(text: &str) -> Option<String> {
    (text.len() > LYRICS_MAX_BYTES).then(|| format!("the text is over {LYRICS_MAX_BYTES} bytes"))
}

fn qe(e: rusqlite::Error) -> DbError {
    DbError::query(e)
}

/// The rows of a query over a table that may not be there. An absent table is
/// a library that predates it, or a node that holds none, and reads as no
/// rows; any other failure is a failure.
///
/// Prepared through the connection's statement cache, so `sql` must be one
/// of a fixed few texts; one whose text varies goes to [`rows_once`].
fn rows<T, P: rusqlite::Params>(
    conn: &Connection,
    sql: &str,
    p: P,
    f: impl FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
) -> Result<Vec<T>, DbError> {
    match conn.prepare_cached(sql) {
        Ok(mut st) => collect(&mut st, p, f),
        Err(e) => absent_or(e),
    }
}

/// [`rows`], for SQL whose text differs from one call to the next: an
/// `IN (?, ?, …)` sized by its arguments. Prepared and dropped, because each
/// text would be an entry of its own in the statement cache, and the cache
/// -- rusqlite's 16, on a connection the player shares -- would then push
/// out the fixed statements it is there to keep. Found 2026-11-14.
fn rows_once<T, P: rusqlite::Params>(
    conn: &Connection,
    sql: &str,
    p: P,
    f: impl FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
) -> Result<Vec<T>, DbError> {
    match conn.prepare(sql) {
        Ok(mut st) => collect(&mut st, p, f),
        Err(e) => absent_or(e),
    }
}

fn collect<T, P: rusqlite::Params>(
    st: &mut rusqlite::Statement<'_>,
    p: P,
    f: impl FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
) -> Result<Vec<T>, DbError> {
    st.query_map(p, f).map_err(qe)?.collect::<rusqlite::Result<Vec<T>>>().map_err(qe)
}

/// No rows where the table is not there; the failure otherwise.
fn absent_or<T>(e: rusqlite::Error) -> Result<Vec<T>, DbError> {
    if e.to_string().contains("no such table") {
        Ok(Vec::new())
    } else {
        Err(qe(e))
    }
}

/// Two passages of one file are the same segment when the stretch they share
/// is at least half of the longer of the two `[SPEC-LYR-110]`. Measured on the
/// hub 2026-11-12: of 12,029 pairs sharing any audio, 12,027 share 60% or
/// more, two share under 20%, and none lies between.
pub fn same_segment(a: (i64, i64), b: (i64, i64)) -> bool {
    let shared = a.1.min(b.1) - a.0.max(b.0);
    let longer = (a.1 - a.0).max(b.1 - b.0);
    shared > 0 && shared * 2 >= longer
}

/// The order a passage's recordings are read in, heaviest first
/// `[SPEC-LYR-115]`: the order `COLS` takes the first of. One text for every
/// query here that asks it, over `passage_recordings pr`.
const HEAVIEST_FIRST: &str = "pr.weight DESC, pr.mbid";

/// What a passage is, as far as its words are concerned.
#[derive(Debug, Clone)]
struct Facts {
    audio_md5: String,
    kind: String,
    start_ms: i64,
    end_ms: i64,
    /// Every recording it names, heaviest first `[SPEC-LYR-115]`. One passage
    /// in the library names two (2026-11-12).
    recordings: Vec<String>,
}

impl Facts {
    /// Its heaviest recording: the one its name is taken from, and the one a
    /// save stores with the row `[SPEC-LYR-160]`.
    fn heaviest(&self) -> Option<&str> {
        self.recordings.first().map(String::as_str)
    }
}

fn facts(conn: &Connection, lib: &str, passage_id: i64) -> Result<Option<Facts>, DbError> {
    let sql = format!(
        "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms \
           FROM {lib}.passages p JOIN {lib}.files f ON f.file_id = p.file_id WHERE p.passage_id = ?1"
    );
    let mut st = conn.prepare_cached(&sql).map_err(qe)?;
    let got = st.query_row([passage_id], |r| {
        Ok(Facts {
            audio_md5: r.get(0)?,
            kind: r.get(1)?,
            start_ms: r.get(2)?,
            end_ms: r.get(3)?,
            recordings: Vec::new(),
        })
    });
    let mut f = match got {
        Ok(f) => f,
        Err(rusqlite::Error::QueryReturnedNoRows) => return Ok(None),
        Err(e) => return Err(qe(e)),
    };
    f.recordings = rows(
        conn,
        &format!(
            "SELECT pr.mbid FROM {lib}.passage_recordings pr \
              WHERE pr.passage_id = ?1 AND pr.mbid IS NOT NULL ORDER BY {HEAVIEST_FIRST}"
        ),
        [passage_id],
        |r| r.get::<_, String>(0),
    )?;
    Ok(Some(f))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    Text,
    /// "This passage has no words" `[SPEC-LYR-130]`.
    Mark,
    /// Saved empty: read as no entry at all `[SPEC-LYR-155]`.
    Cleared,
}

/// One entry: the newest row saved for one segment of one file, under one kind
/// `[SPEC-LYR-150]`. Older rows of the same segment are superseded and are not
/// here.
#[derive(Debug, Clone)]
struct Entry {
    /// The row's key, as stored.
    audio_md5: String,
    kind: String,
    start_ms: i64,
    end_ms: i64,
    /// The file it is of, a retired `audio_md5` followed `[SPEC-RLK-155]`.
    file: String,
    /// The recording it speaks for `[SPEC-LYR-160]`: its passage's present one
    /// where this node holds the file, else the one stored with it.
    recording: Option<String>,
    state: State,
    updated_at: String,
    origin: Option<String>,
}

impl Entry {
    /// The row's key, which is the table's.
    fn key(&self) -> (&str, &str, i64) {
        (&self.audio_md5, &self.kind, self.start_ms)
    }
}

/// One row of `passage_lyrics`, without its text.
struct Raw {
    audio_md5: String,
    kind: String,
    start_ms: i64,
    end_ms: i64,
    mbid: Option<String>,
    no_words: bool,
    empty: bool,
    updated_at: String,
    origin: Option<String>,
}

const RAW_COLS: &str =
    "audio_md5, kind, start_ms, end_ms, mbid, no_words, length(trim(text)) = 0, updated_at, origin";

fn raw_row(r: &rusqlite::Row<'_>) -> rusqlite::Result<Raw> {
    Ok(Raw {
        audio_md5: r.get(0)?,
        kind: r.get(1)?,
        start_ms: r.get(2)?,
        end_ms: r.get(3)?,
        mbid: r.get(4)?,
        no_words: r.get::<_, i64>(5)? != 0,
        empty: r.get(6)?,
        updated_at: r.get(7)?,
        origin: r.get(8)?,
    })
}

/// Every retired `audio_md5` and the one that replaced it `[SPEC-RLK-155]`.
/// 61 rows on the hub, 2026-11-13.
fn aliases(conn: &Connection, lib: &str) -> Result<HashMap<String, String>, DbError> {
    Ok(rows(conn, &format!("SELECT old_md5, new_md5 FROM {lib}.audio_md5_aliases"), [], |r| {
        Ok((r.get(0)?, r.get(1)?))
    })?
    .into_iter()
    .collect())
}

/// Saved entries, without their text: every one of them ([`SavedLyrics::load`]),
/// or those of some files (`of_files`).
///
/// **All of them only for a whole run of the file writers.** Until 2026-11-13
/// a request for one passage read all of them too, and then asked the
/// catalogue once per entry for its passage's present recording -- on every
/// `GET /lyrics`, which is every track change on every node, and every node
/// holds every row `[SPEC-LYR-200]`. A request now reads the rows that can bear
/// on its passage and no others ([`Reach::Asked`], `[SPEC-LYR-178]`).
pub struct SavedLyrics {
    /// Every row read, of the file it is of; newest first, a tie by key,
    /// which is the order of `[SPEC-LYR-120]`. A passage's own entry, and its
    /// tier 2, are found here, against the passage as it is now.
    rows: Vec<Entry>,
    /// The rows that speak for a recording in tiers 3 and 4, in the same
    /// order: each passage's entry where this node holds its file, and each
    /// segment's newest row where it does not `[SPEC-LYR-160]`.
    speaking: Vec<Entry>,
}

impl SavedLyrics {
    fn empty() -> Self {
        Self { rows: Vec::new(), speaking: Vec::new() }
    }

    pub fn load(conn: &Connection, lib: &str) -> Result<Self, DbError> {
        let raw = rows(
            conn,
            &format!("SELECT {RAW_COLS} FROM passage_lyrics ORDER BY updated_at DESC, audio_md5, kind, start_ms"),
            [],
            raw_row,
        )?;
        if raw.is_empty() {
            return Ok(Self::empty());
        }
        Self::from_rows(conn, lib, raw, &aliases(conn, lib)?)
    }

    /// The entries of `files` alone, each a file's present `audio_md5`.
    ///
    /// **Every row of each file, whatever it was stored under**, so that which
    /// rows are superseded `[SPEC-LYR-150]` is decided over the same rows
    /// [`SavedLyrics::load`] decides it over: a row is of the file its
    /// `audio_md5` names, or of the one that retired it.
    fn of_files(
        conn: &Connection,
        lib: &str,
        files: &HashSet<String>,
        aliases: &HashMap<String, String>,
    ) -> Result<Self, DbError> {
        let mut stored: Vec<&str> =
            files.iter().filter(|f| !aliases.contains_key(*f)).map(String::as_str).collect();
        stored.extend(aliases.iter().filter(|(_, new)| files.contains(*new)).map(|(old, _)| old.as_str()));
        stored.sort_unstable();
        stored.dedup();
        let sql = format!("SELECT {RAW_COLS} FROM passage_lyrics WHERE audio_md5 = ?1");
        let mut raw = Vec::new();
        for md5 in stored {
            raw.extend(rows(conn, &sql, [md5], raw_row)?);
        }
        if raw.is_empty() {
            return Ok(Self::empty());
        }
        // The order `load` asks SQLite for; both compare text as bytes.
        raw.sort_by(|a, b| {
            b.updated_at
                .cmp(&a.updated_at)
                .then_with(|| (&a.audio_md5, &a.kind, a.start_ms).cmp(&(&b.audio_md5, &b.kind, b.start_ms)))
        });
        Self::from_rows(conn, lib, raw, aliases)
    }

    /// `raw` newest first, a tie by key; and every row of each file it names.
    ///
    /// **Which rows are superseded is decided against the passages as they are
    /// now** `[SPEC-LYR-150]`, where this node holds the file: a passage's
    /// entry is the newest row that is the same segment as it, and a row that
    /// is no passage's entry speaks for nothing. Until 2026-11-13 the rows were
    /// measured against each other, so after two boundary edits a passage
    /// could lose its own words: saved at 0-100, saved again at 50-150 -- half
    /// shared, so the first was superseded -- and moved back to 0-90, which
    /// the second no longer reaches. Where the file is not here there is no
    /// passage to measure by, and the rows are measured against each other
    /// still: each segment's newest speaks, for the recording stored with it
    /// `[SPEC-LYR-160]`.
    fn from_rows(
        conn: &Connection,
        lib: &str,
        raw: Vec<Raw>,
        aliases: &HashMap<String, String>,
    ) -> Result<Self, DbError> {
        let rows_read: Vec<Entry> = raw
            .into_iter()
            .map(|r| Entry {
                file: aliases.get(&r.audio_md5).cloned().unwrap_or_else(|| r.audio_md5.clone()),
                audio_md5: r.audio_md5,
                kind: r.kind,
                start_ms: r.start_ms,
                end_ms: r.end_ms,
                recording: r.mbid,
                state: if r.no_words {
                    State::Mark
                } else if r.empty {
                    State::Cleared
                } else {
                    State::Text
                },
                updated_at: r.updated_at,
                origin: r.origin,
            })
            .collect();
        let span = |i: usize| (rows_read[i].start_ms, rows_read[i].end_ms);

        // Each (file, kind)'s rows, newest first, in the order first met.
        let mut groups: Vec<((String, String), Vec<usize>)> = Vec::new();
        let mut group_of: HashMap<(String, String), usize> = HashMap::new();
        for (i, e) in rows_read.iter().enumerate() {
            let key = (e.file.clone(), e.kind.clone());
            let g = *group_of.entry(key.clone()).or_insert_with(|| {
                groups.push((key, Vec::new()));
                groups.len() - 1
            });
            groups[g].1.push(i);
        }

        // The passages of a file and kind as they are now, each with its
        // heaviest recording.
        let sql = format!(
            "SELECT p.start_ms, p.end_ms, \
                    (SELECT pr.mbid FROM {lib}.passage_recordings pr WHERE pr.passage_id = p.passage_id \
                      ORDER BY {HEAVIEST_FIRST} LIMIT 1) \
               FROM {lib}.files f JOIN {lib}.passages p ON p.file_id = f.file_id \
              WHERE f.audio_md5 = ?1 AND p.kind = ?2 ORDER BY p.start_ms"
        );
        // Row -> the recording it speaks for.
        let mut speaks: HashMap<usize, Option<String>> = HashMap::new();
        for ((file, kind), members) in &groups {
            let held: Vec<(i64, i64, Option<String>)> =
                rows(conn, &sql, params![file, kind], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
            if held.is_empty() {
                let mut heads: Vec<usize> = Vec::new();
                for &i in members {
                    if !heads.iter().any(|&h| same_segment(span(h), span(i))) {
                        heads.push(i);
                        speaks.insert(i, rows_read[i].recording.clone());
                    }
                }
                continue;
            }
            // Which passage a row heading two speaks for: the one at its own
            // start, else the last -- as before this was measured per passage.
            let mut at_start: HashSet<usize> = HashSet::new();
            for (s, t, mbid) in &held {
                let Some(&i) = members.iter().find(|&&i| same_segment(span(i), (*s, *t))) else { continue };
                let here = *s == rows_read[i].start_ms;
                if here || !at_start.contains(&i) {
                    speaks.insert(i, mbid.clone().or_else(|| rows_read[i].recording.clone()));
                }
                if here {
                    at_start.insert(i);
                }
            }
        }

        let speaking = rows_read
            .iter()
            .enumerate()
            .filter_map(|(i, e)| speaks.get(&i).map(|rec| Entry { recording: rec.clone(), ..e.clone() }))
            .collect();
        Ok(Self { rows: rows_read, speaking })
    }

    /// `f`'s own audio: for each kind, the newest row of its file that is the
    /// same segment as `f` is now `[SPEC-LYR-150]` -- its own entry under its
    /// own kind, and tier 2 under the others.
    fn of_passage(&self, f: &Facts) -> Vec<Entry> {
        let mut kinds: HashSet<&str> = HashSet::new();
        self.rows
            .iter()
            .filter(|e| e.file == f.audio_md5 && same_segment((e.start_ms, e.end_ms), (f.start_ms, f.end_ms)))
            .filter(|e| kinds.insert(e.kind.as_str()))
            .cloned()
            .collect()
    }

    /// The entries that speak for one of `recordings` `[SPEC-LYR-160]`.
    fn speaking_for(&self, recordings: &[String]) -> Vec<Entry> {
        self.speaking
            .iter()
            .filter(|e| e.recording.as_ref().is_some_and(|r| recordings.contains(r)))
            .cloned()
            .collect()
    }
}

/// Where the tiers read saved entries from.
enum Reach<'a> {
    /// Every entry, read once: a whole run of the file writers, which asks
    /// about every passage there is.
    Whole(&'a SavedLyrics),
    /// Read as each tier asks, for one passage `[SPEC-LYR-178]`: the rows of
    /// its file; then, only where those settle nothing, the rows that can speak
    /// for its recording; then for its work's other recordings. Each read is
    /// whole files' rows, so it answers as [`SavedLyrics::load`] would.
    Asked {
        aliases: HashMap<String, String>,
        /// Whether the table holds a row at all. Where it holds none, which
        /// was every node until 2026-11-12, nothing more is read.
        any: bool,
    },
}

impl Reach<'_> {
    fn asked(conn: &Connection, lib: &str) -> Result<Self, DbError> {
        let any = !rows(conn, "SELECT 1 FROM passage_lyrics LIMIT 1", [], |r| r.get::<_, i64>(0))?.is_empty();
        Ok(Reach::Asked { aliases: if any { aliases(conn, lib)? } else { HashMap::new() }, any })
    }

    fn of_passage(&self, conn: &Connection, lib: &str, f: &Facts) -> Result<Vec<Entry>, DbError> {
        match self {
            Reach::Whole(saved) => Ok(saved.of_passage(f)),
            Reach::Asked { any: false, .. } => Ok(Vec::new()),
            Reach::Asked { aliases, .. } => {
                let files = HashSet::from([f.audio_md5.clone()]);
                Ok(SavedLyrics::of_files(conn, lib, &files, aliases)?.of_passage(f))
            }
        }
    }

    /// Every entry that speaks for one of `recordings`, newest first.
    ///
    /// Asked for one passage, the files such an entry can be of are found two
    /// ways, because an entry speaks for its passage's present recording where
    /// this node holds the file and for the one stored with it where it does
    /// not `[SPEC-LYR-160]`: the rows stored with one of `recordings`, by the
    /// table's index; and the files here that hold one of them, by one pass
    /// over `passage_recordings`, which has no index by recording -- 0.8 ms on
    /// the hub's 24,226 rows, 2026-11-13, and the same however many words are
    /// saved.
    fn speaking_for(&self, conn: &Connection, lib: &str, recordings: &[String]) -> Result<Vec<Entry>, DbError> {
        let aliases = match self {
            Reach::Whole(saved) => return Ok(saved.speaking_for(recordings)),
            Reach::Asked { any: false, .. } => return Ok(Vec::new()),
            Reach::Asked { aliases, .. } => aliases,
        };
        let marks = vec!["?"; recordings.len()].join(", ");
        let named = || rusqlite::params_from_iter(recordings.iter());
        let mut files: HashSet<String> = rows_once(
            conn,
            &format!("SELECT DISTINCT audio_md5 FROM passage_lyrics WHERE mbid IN ({marks})"),
            named(),
            |r| r.get::<_, String>(0),
        )?
        .into_iter()
        .map(|md5| aliases.get(&md5).cloned().unwrap_or(md5))
        .collect();
        files.extend(rows_once(
            conn,
            &format!(
                "SELECT DISTINCT f.audio_md5 FROM {lib}.passage_recordings pr \
                   JOIN {lib}.passages p ON p.passage_id = pr.passage_id \
                   JOIN {lib}.files f ON f.file_id = p.file_id WHERE pr.mbid IN ({marks})"
            ),
            named(),
            |r| r.get::<_, String>(0),
        )?);
        Ok(SavedLyrics::of_files(conn, lib, &files, aliases)?.speaking_for(recordings))
    }
}

/// Imported words, one recording's.
#[derive(Debug, Clone)]
struct Imported {
    mbid: String,
    text: String,
    source: String,
    fetched_at: String,
}

fn imported(conn: &Connection, lib: &str, recordings: &[String]) -> Result<Vec<Imported>, DbError> {
    let sql = format!("SELECT mbid, text, source, fetched_at FROM {lib}.lyrics WHERE mbid = ?1");
    let mut out = Vec::new();
    for m in recordings {
        out.extend(rows(conn, &sql, [m], |r| {
            Ok(Imported { mbid: r.get(0)?, text: r.get(1)?, source: r.get(2)?, fetched_at: r.get(3)? })
        })?);
    }
    out.retain(|i| !i.text.trim().is_empty());
    out.sort_by(|a, b| b.fetched_at.cmp(&a.fetched_at).then_with(|| a.mbid.cmp(&b.mbid)));
    Ok(out)
}

/// Every other recording of a work one of `recordings` performs
/// `[SPEC-LYR-100]`, in a fixed order. A recording of several works matches
/// through any of them, and none of `recordings` is its own mate.
fn work_mates(conn: &Connection, lib: &str, recordings: &[String]) -> Result<Vec<String>, DbError> {
    let mut works: Vec<String> = Vec::new();
    for m in recordings {
        works.extend(rows(
            conn,
            &format!("SELECT work_mbid FROM {lib}.recording_works WHERE mbid = ?1"),
            [m],
            |r| r.get::<_, String>(0),
        )?);
    }
    let sql = format!("SELECT mbid FROM {lib}.recording_works WHERE work_mbid = ?1");
    let mut mates: Vec<String> = Vec::new();
    for w in &works {
        mates.extend(rows(conn, &sql, [w], |r| r.get::<_, String>(0))?);
    }
    mates.retain(|m| !recordings.contains(m));
    mates.sort();
    mates.dedup();
    Ok(mates)
}

/// Where one source of words is.
#[derive(Debug, Clone)]
enum Pick {
    Saved(Entry),
    Imported(Imported),
}

struct Resolved {
    facts: Facts,
    /// The passage's own entry, whatever its state.
    own: Option<Entry>,
    /// Every source below the passage's own that has text, by tier and then
    /// by `[SPEC-LYR-120]`. Complete only when asked for all of it.
    sources: Vec<(u8, Pick)>,
    /// The tier shown, and which of `sources` it is; `None` where nothing is.
    /// Tier 1 is the passage's own and names no source.
    shown: Option<(u8, Option<usize>)>,
}

/// Walk the tiers for `passage_id`. With `all` false it stops as soon as what
/// is shown is known, which is what a player asking for one text needs -- and
/// asked for one passage, a tier that is not reached is not read.
fn resolve(
    conn: &Connection,
    lib: &str,
    reach: &Reach<'_>,
    passage_id: i64,
    all: bool,
) -> Result<Option<Resolved>, DbError> {
    let Some(f) = facts(conn, lib, passage_id)? else { return Ok(None) };
    let mine = reach.of_passage(conn, lib, &f)?;
    let own = mine.iter().find(|e| e.kind == f.kind).cloned();
    let own_state = own.as_ref().map(|e| e.state);

    let mut sources: Vec<(u8, Pick)> = Vec::new();
    // Decided: `Some(None)` is "nothing, by a mark"; `Some(Some(..))` a text.
    let mut shown: Option<Option<(u8, Option<usize>)>> = match own_state {
        Some(State::Text) => Some(Some((1, None))),
        Some(State::Mark) => Some(None),
        _ => None,
    };

    // Tier 2: the same audio under another kind. A mark there is an answer
    // too `[SPEC-LYR-130]`: what is true of one cut is true of the other.
    for e in mine.iter().filter(|e| e.kind != f.kind) {
        match e.state {
            State::Cleared => {}
            State::Mark => {
                shown.get_or_insert(None);
            }
            State::Text => {
                sources.push((2, Pick::Saved(e.clone())));
                shown.get_or_insert(Some((2, Some(sources.len() - 1))));
            }
        }
    }

    let taken: HashSet<(&str, &str, i64)> = mine.iter().map(Entry::key).collect();
    if !f.recordings.is_empty() && (all || shown.is_none()) {
        // Tiers 3 and 4. A mark is never read here: another recording of a
        // song may be sung where this one is not.
        //
        // **Tier 3 asks each recording in turn, heaviest first**, and shows the
        // first that has words `[SPEC-LYR-115]`: what `GET /lyrics` did before
        // 2026-11-12, by joining the imported table and then taking the
        // heaviest. Asking the heaviest alone left a passage of two
        // recordings, the heavier without words, showing nothing.
        let mut asks: Vec<(u8, Vec<String>)> = f.recordings.iter().map(|m| (3u8, vec![m.clone()])).collect();
        asks.push((4, Vec::new())); // its works, read only if reached
        for (tier, mut recordings) in asks {
            if !all && shown.is_some() {
                break;
            }
            if tier == 4 {
                recordings = work_mates(conn, lib, &f.recordings)?;
            }
            if recordings.is_empty() {
                continue;
            }
            let before = sources.len();
            for e in reach.speaking_for(conn, lib, &recordings)? {
                if e.state == State::Text && !taken.contains(&e.key()) {
                    sources.push((tier, Pick::Saved(e)));
                }
            }
            for found in imported(conn, lib, &recordings)? {
                sources.push((tier, Pick::Imported(found)));
            }
            if sources.len() > before {
                shown.get_or_insert(Some((tier, Some(before))));
            }
        }
    }
    Ok(Some(Resolved { facts: f, own, sources, shown: shown.flatten() }))
}

fn saved_text(conn: &Connection, e: &Entry) -> Result<String, DbError> {
    let mut st = conn
        .prepare_cached("SELECT text FROM passage_lyrics WHERE audio_md5 = ?1 AND kind = ?2 AND start_ms = ?3")
        .map_err(qe)?;
    st.query_row(params![e.audio_md5, e.kind, e.start_ms], |r| r.get(0)).map_err(qe)
}

fn pick_text(conn: &Connection, pick: &Pick) -> Result<String, DbError> {
    match pick {
        Pick::Saved(e) => saved_text(conn, e),
        Pick::Imported(i) => Ok(i.text.clone()),
    }
}

/// The words a passage shows, and where they stand by `[SPEC-LYR-120]`.
#[derive(Debug, Clone, PartialEq)]
pub struct ShownLyrics {
    pub text: String,
    /// 1 to 4 `[SPEC-LYR-100]`.
    pub tier: u8,
    /// When a person saved them; `None` for imported words.
    pub saved_at: Option<String>,
}

impl ShownLyrics {
    /// Whether these come before `other`, for choosing between two passages
    /// that one file must speak for `[SPEC-LYR-176]`: the nearer tier first
    /// `[SPEC-LYR-100]`, so a passage's own words are never displaced by words
    /// another passage only borrows; then, within a tier, words a person saved
    /// before imported ones, and newer before older `[SPEC-LYR-120]`.
    ///
    /// Until 2026-11-13 the tier was not compared. Two passages of one artist
    /// and title, one with words of its own and one showing a cover's words
    /// saved later through its work, gave the file the cover's -- and a file
    /// already there is never replaced `[SPEC-LYR-900]`.
    pub fn before(&self, other: &Self) -> bool {
        if self.tier != other.tier {
            return self.tier < other.tier;
        }
        match (&self.saved_at, &other.saved_at) {
            (Some(mine), Some(theirs)) => mine > theirs,
            (Some(_), None) => true,
            _ => false,
        }
    }
}

fn shown_by(
    conn: &Connection,
    lib: &str,
    reach: &Reach<'_>,
    passage_id: i64,
) -> Result<Option<ShownLyrics>, DbError> {
    let Some(r) = resolve(conn, lib, reach, passage_id, false)? else { return Ok(None) };
    let (tier, pick) = match r.shown {
        None => return Ok(None),
        Some((tier, None)) => match r.own {
            Some(e) => (tier, Pick::Saved(e)),
            None => return Ok(None),
        },
        Some((tier, Some(n))) => (tier, r.sources[n].1.clone()),
    };
    let saved_at = match &pick {
        Pick::Saved(e) => Some(e.updated_at.clone()),
        Pick::Imported(_) => None,
    };
    Ok(Some(ShownLyrics { text: pick_text(conn, &pick)?, tier, saved_at }))
}

/// The words `passage_id` shows, by `[SPEC-LYR-100]`: `None` where the library
/// has none for it, where it is marked as having none, and where there is no
/// such passage. Against every entry, read once: for a run over every passage.
pub fn shown_words(
    conn: &Connection,
    lib: &str,
    saved: &SavedLyrics,
    passage_id: i64,
) -> Result<Option<ShownLyrics>, DbError> {
    shown_by(conn, lib, &Reach::Whole(saved), passage_id)
}

/// [`shown_words`], the text alone.
pub fn shown_lyrics(
    conn: &Connection,
    lib: &str,
    saved: &SavedLyrics,
    passage_id: i64,
) -> Result<Option<String>, DbError> {
    Ok(shown_words(conn, lib, saved, passage_id)?.map(|s| s.text))
}

/// Whether `passage_id` shows any words: [`shown_words`] is `Some`, without
/// reading the saved text it would show. For a file writer that only counts a
/// passage it writes nothing for -- one inside a capture `[SPEC-LYR-080]`.
pub fn shows_words(conn: &Connection, lib: &str, saved: &SavedLyrics, passage_id: i64) -> Result<bool, DbError> {
    let Some(r) = resolve(conn, lib, &Reach::Whole(saved), passage_id, false)? else { return Ok(false) };
    Ok(match r.shown {
        None => false,
        Some((_, None)) => r.own.is_some(),
        Some((_, Some(_))) => true,
    })
}

/// The words one passage shows, reading only the saved rows that can bear on
/// it `[SPEC-LYR-178]`: what a request asks. The same answer as
/// [`shown_lyrics`] over every entry, which the tests hold it to.
pub fn passage_shown_lyrics(conn: &Connection, lib: &str, passage_id: i64) -> Result<Option<String>, DbError> {
    Ok(shown_by(conn, lib, &Reach::asked(conn, lib)?, passage_id)?.map(|s| s.text))
}

/// A passage's own entry, as the editor shows it `[SPEC-LYR-172]`.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct OwnLyrics {
    /// Kept under a mark, and not shown while it stands `[SPEC-LYR-188]`.
    pub text: String,
    pub no_words: bool,
    pub updated_at: String,
    pub origin: Option<String>,
}

/// One other source of words for a passage, read-only `[SPEC-LYR-172]`.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct LyricSource {
    /// 2: the same segment of the same file. 3: the same recording. 4: the
    /// same work.
    pub tier: u8,
    pub text: String,
    /// Tier 2: the other cut's kind.
    pub kind: Option<String>,
    /// Tiers 3 and 4: the recording these words are for.
    pub title: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
    /// Where imported words came from (`mulibplay`); `None` for saved ones.
    pub imported: Option<String>,
    /// When and where saved words were saved.
    pub updated_at: Option<String>,
    pub origin: Option<String>,
}

/// Everything the lyric editor shows for one passage `[SPEC-LYR-172]`.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct LyricsView {
    pub passage_id: i64,
    pub kind: String,
    pub title: Option<String>,
    pub artist: Option<String>,
    pub album: Option<String>,
    /// `None` where the passage has no entry, or a cleared one.
    pub own: Option<OwnLyrics>,
    /// The tier the player shows words from, 1 to 4; `None` where it shows
    /// nothing.
    pub shown_tier: Option<u8>,
    pub sources: Vec<LyricSource>,
}

/// One optional text, from a query that may find nothing or meet a library
/// without the table: a name that cannot be read is left out, never an error.
fn one(conn: &Connection, sql: &str, p: impl rusqlite::Params) -> Option<String> {
    conn.prepare_cached(sql).ok()?.query_row(p, |r| r.get::<_, Option<String>>(0)).ok().flatten()
}

/// Title, artist and release of a recording, by the expressions a history row
/// uses `[SPEC-PREF-090]`.
fn recording_naming(conn: &Connection, lib: &str, mbid: &str) -> (Option<String>, Option<String>, Option<String>) {
    (
        one(conn, &format!("SELECT title FROM {lib}.recordings WHERE mbid = ?1"), [mbid]),
        one(
            conn,
            &format!(
                "SELECT a.name FROM {lib}.recording_artists ra JOIN {lib}.artists a ON a.mbid = ra.artist_mbid \
                  WHERE ra.mbid = ?1 ORDER BY ra.weight DESC, a.name LIMIT 1"
            ),
            [mbid],
        ),
        one(
            conn,
            &format!(
                "SELECT rel.title FROM {lib}.release_recordings rr JOIN {lib}.releases rel ON rel.mbid = rr.release_mbid \
                  WHERE rr.mbid = ?1 ORDER BY rr.chosen DESC, rel.release_date, rel.title LIMIT 1"
            ),
            [mbid],
        ),
    )
}

/// What the passage itself is called: its recording, else its file's tags, and
/// the release its file was taken from before its recording's chosen one
/// `[SPEC-SC-125]`.
fn passage_naming(
    conn: &Connection,
    lib: &str,
    passage_id: i64,
    mbid: Option<&str>,
) -> (Option<String>, Option<String>, Option<String>) {
    let (title, artist, album) = mbid.map(|m| recording_naming(conn, lib, m)).unwrap_or_default();
    let tag = |col: &str| {
        one(
            conn,
            &format!(
                "SELECT t.{col} FROM {lib}.passages p JOIN {lib}.file_tags t ON t.file_id = p.file_id \
                  WHERE p.passage_id = ?1"
            ),
            [passage_id],
        )
        .filter(|s| !s.trim().is_empty())
    };
    let of_file = one(
        conn,
        &format!(
            "SELECT rel.title FROM {lib}.passages p JOIN {lib}.files f ON f.file_id = p.file_id \
               JOIN {lib}.file_releases fr ON fr.audio_md5 = f.audio_md5 \
               JOIN {lib}.releases rel ON rel.mbid = fr.release_mbid WHERE p.passage_id = ?1"
        ),
        [passage_id],
    );
    (title.or_else(|| tag("title")), artist.or_else(|| tag("artist")), of_file.or(album).or_else(|| tag("album")))
}

/// Everything the editor shows for `passage_id`, or `None` for no such
/// passage `[SPEC-LYR-172]`.
pub fn lyrics_view(conn: &Connection, lib: &str, passage_id: i64) -> Result<Option<LyricsView>, DbError> {
    let reach = Reach::asked(conn, lib)?;
    let Some(r) = resolve(conn, lib, &reach, passage_id, true)? else { return Ok(None) };
    let own = match &r.own {
        Some(e) if e.state != State::Cleared => Some(OwnLyrics {
            text: saved_text(conn, e)?,
            no_words: e.state == State::Mark,
            updated_at: e.updated_at.clone(),
            origin: e.origin.clone(),
        }),
        _ => None,
    };
    let mut seen: HashSet<String> = HashSet::new();
    let mut sources = Vec::new();
    for (tier, pick) in &r.sources {
        let text = pick_text(conn, pick)?;
        // Listed once, at its highest place.
        if !seen.insert(text.trim().to_string()) {
            continue;
        }
        let (recording, kind, imported, updated_at, origin) = match pick {
            Pick::Saved(e) => {
                (e.recording.clone(), Some(e.kind.clone()), None, Some(e.updated_at.clone()), e.origin.clone())
            }
            Pick::Imported(i) => (Some(i.mbid.clone()), None, Some(i.source.clone()), None, None),
        };
        let (title, artist, album) = match (&recording, *tier) {
            (Some(m), 3 | 4) => recording_naming(conn, lib, m),
            _ => (None, None, None),
        };
        sources.push(LyricSource {
            tier: *tier,
            text,
            kind: kind.filter(|_| *tier == 2),
            title,
            artist,
            album,
            imported,
            updated_at,
            origin,
        });
    }
    let (title, artist, album) = passage_naming(conn, lib, passage_id, r.facts.heaviest());
    Ok(Some(LyricsView {
        passage_id,
        kind: r.facts.kind.clone(),
        title,
        artist,
        album,
        own,
        shown_tier: r.shown.map(|(tier, _)| tier),
        sources,
    }))
}

/// Line ends as `\n`, and a text of only white space empty `[SPEC-LYR-174]`.
/// Otherwise as typed, but for white space after the last line.
pub fn normalise_lyrics(text: &str) -> String {
    let t = text.replace("\r\n", "\n").replace('\r', "\n");
    if t.trim().is_empty() {
        String::new()
    } else {
        t.trim_end().to_string()
    }
}

/// Save the words of one passage, and of no other `[SPEC-LYR-174]`.
///
/// An empty `text` clears the passage's own words, and the lower tiers show
/// again; `no_words` marks it as having none, with `text` kept under the mark
/// `[SPEC-LYR-188]`. Written under the passage's present start, so an entry
/// saved before a boundary edit is superseded rather than moved, and no row is
/// ever deleted `[SPEC-LYR-150]`, `[SPEC-LYR-155]`.
///
/// `Ok(None)` where there is no such passage. Clearing a passage that never
/// had an entry writes nothing.
pub fn save_passage_lyrics(
    conn: &Connection,
    lib: &str,
    passage_id: i64,
    text: &str,
    no_words: bool,
    origin: &str,
) -> Result<Option<Option<OwnLyrics>>, DbError> {
    if let Some(why) = lyrics_refusal(text) {
        return Err(DbError::Query(why));
    }
    let Some(f) = facts(conn, lib, passage_id)? else { return Ok(None) };
    let text = normalise_lyrics(text);
    let had = Reach::asked(conn, lib)?.of_passage(conn, lib, &f)?.iter().any(|e| e.kind == f.kind);
    if !had && text.is_empty() && !no_words {
        return Ok(Some(None));
    }
    // To the millisecond: two saves a moment apart, on either side of a
    // boundary edit, are two rows, and the newer has to read as newer.
    conn.execute(
        "INSERT INTO passage_lyrics \
             (audio_md5, kind, start_ms, end_ms, mbid, text, no_words, updated_at, origin) \
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, strftime('%Y-%m-%d %H:%M:%f', 'now'), ?8) \
         ON CONFLICT(audio_md5, kind, start_ms) DO UPDATE SET \
             end_ms = excluded.end_ms, mbid = excluded.mbid, text = excluded.text, \
             no_words = excluded.no_words, updated_at = excluded.updated_at, origin = excluded.origin",
        params![f.audio_md5, f.kind, f.start_ms, f.end_ms, f.heaviest(), text, no_words as i64, origin],
    )
    .map_err(qe)?;
    let own = conn
        .query_row(
            "SELECT text, no_words, updated_at, origin FROM passage_lyrics \
              WHERE audio_md5 = ?1 AND kind = ?2 AND start_ms = ?3",
            params![f.audio_md5, f.kind, f.start_ms],
            |r| {
                Ok(OwnLyrics {
                    text: r.get(0)?,
                    no_words: r.get::<_, i64>(1)? != 0,
                    updated_at: r.get(2)?,
                    origin: r.get(3)?,
                })
            },
        )
        .map_err(qe)?;
    Ok(Some(Some(own).filter(|o| o.no_words || !o.text.is_empty())))
}

#[cfg(test)]
mod tests {
    use super::*;

    const SCHEMA: &str = include_str!("../../../../sql/schema.sql");

    /// One file of two tracks, each an album cut and a radio cut inside it;
    /// a second file of the first track's recording; a third of another
    /// recording of the same work; a fourth of unrelated music.
    fn library() -> Connection {
        let c = Connection::open_in_memory().unwrap();
        c.execute_batch(SCHEMA).unwrap();
        c.execute_batch("PRAGMA foreign_keys = OFF").unwrap();
        for (id, md5) in [(1, "aaa"), (2, "bbb"), (3, "ccc"), (4, "ddd")] {
            c.execute(
                "INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, \
                                    first_seen, last_seen) VALUES (?1, ?2, ?3, 1, 0, 'mp3', 600000, 't', 't')",
                params![id, md5, format!("/m/{md5}.mp3")],
            )
            .unwrap();
        }
        for (pid, file, kind, start, end, mbid) in [
            (11, 1, "album", 0, 200_000, "rec-song"),
            (12, 1, "radio", 4_000, 190_000, "rec-song"),
            (13, 1, "album", 200_000, 400_000, "rec-next"),
            (14, 1, "radio", 203_000, 395_000, "rec-next"),
            (21, 2, "radio", 1_000, 180_000, "rec-song"),
            (31, 3, "radio", 0, 240_000, "rec-cover"),
            (41, 4, "radio", 0, 100_000, "rec-other"),
        ] {
            c.execute(
                "INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src) \
                 VALUES (?1, ?2, ?3, ?4, ?5, 'test')",
                params![pid, file, kind, start, end],
            )
            .unwrap();
            c.execute(
                "INSERT OR IGNORE INTO recordings (mbid, title, source) VALUES (?1, ?1, 'test')",
                [mbid],
            )
            .unwrap();
            c.execute(
                "INSERT INTO passage_recordings (passage_id, mbid, weight, source) VALUES (?1, ?2, 1.0, 'test')",
                params![pid, mbid],
            )
            .unwrap();
        }
        c.execute("INSERT INTO works (mbid, title, source) VALUES ('work-song', 'Song', 'test')", []).unwrap();
        for rec in ["rec-song", "rec-cover"] {
            c.execute(
                "INSERT INTO recording_works (mbid, work_mbid, source) VALUES (?1, 'work-song', 'test')",
                [rec],
            )
            .unwrap();
        }
        c
    }

    fn import(c: &Connection, mbid: &str, text: &str) {
        c.execute(
            "INSERT INTO lyrics (mbid, text, source, fetched_at) VALUES (?1, ?2, 'mulibplay', '2026-01-01')",
            params![mbid, text],
        )
        .unwrap();
    }

    /// What a passage shows -- read both ways, which must agree: against every
    /// entry, as the file writers read, and as one request reads
    /// `[SPEC-LYR-178]`. So every test here that asks is a test of both.
    fn shown(c: &Connection, passage_id: i64) -> Option<String> {
        let saved = SavedLyrics::load(c, "main").unwrap();
        let whole = shown_lyrics(c, "main", &saved, passage_id).unwrap();
        let asked = passage_shown_lyrics(c, "main", passage_id).unwrap();
        assert_eq!(asked, whole, "passage {passage_id}: read for one request, and read whole");
        // And whether there are any, asked without the text, agrees.
        let any = shows_words(c, "main", &saved, passage_id).unwrap();
        assert_eq!(any, whole.is_some(), "passage {passage_id}: shows_words");
        whole
    }

    fn save(c: &Connection, passage_id: i64, text: &str, no_words: bool) -> Option<OwnLyrics> {
        save_passage_lyrics(c, "main", passage_id, text, no_words, "here").unwrap().expect("a passage")
    }

    /// Give a saved row a stamp of its own choosing, so order does not rest
    /// on how fast the test ran.
    fn stamp(c: &Connection, md5: &str, kind: &str, start: i64, at: &str) {
        let n = c
            .execute(
                "UPDATE passage_lyrics SET updated_at = ?4 WHERE audio_md5 = ?1 AND kind = ?2 AND start_ms = ?3",
                params![md5, kind, start, at],
            )
            .unwrap();
        assert_eq!(n, 1, "no row {md5}/{kind}/{start} to stamp");
    }

    #[test]
    fn half_of_the_longer_is_the_same_segment() {
        assert!(same_segment((0, 200), (4, 190)), "a radio cut inside its album cut");
        assert!(same_segment((0, 100), (50, 150)), "exactly half");
        assert!(!same_segment((0, 100), (51, 151)), "under half");
        assert!(!same_segment((0, 200), (200, 400)), "neighbours share nothing");
        assert!(!same_segment((0, 600), (0, 100)), "one track of a whole side");
    }

    /// `[SPEC-LYR-100]`: each tier is reached only when those above hold
    /// nothing, and the work tier reaches a passage that showed none before.
    #[test]
    fn the_four_tiers_in_order() {
        let c = library();
        assert_eq!(shown(&c, 12), None, "nothing anywhere");
        assert_eq!(shown(&c, 999), None, "no such passage");

        import(&c, "rec-cover", "the cover's words");
        assert_eq!(shown(&c, 12).as_deref(), Some("the cover's words"), "tier 4: the same work");
        assert_eq!(shown(&c, 41), None, "another work's words are not this passage's");

        import(&c, "rec-song", "the recording's words");
        assert_eq!(shown(&c, 12).as_deref(), Some("the recording's words"), "tier 3 over tier 4");
        assert_eq!(shown(&c, 21).as_deref(), Some("the recording's words"), "in any file");
        assert_eq!(shown(&c, 31).as_deref(), Some("the cover's words"), "its own recording first");

        save(&c, 11, "typed for the album cut", false);
        assert_eq!(shown(&c, 12).as_deref(), Some("typed for the album cut"), "tier 2 over tier 3");
        assert_eq!(shown(&c, 14), None, "the next track of the file is not the same segment");

        save(&c, 12, "typed for the radio cut", false);
        assert_eq!(shown(&c, 12).as_deref(), Some("typed for the radio cut"), "tier 1 over tier 2");
        assert_eq!(shown(&c, 11).as_deref(), Some("typed for the album cut"), "each cut its own");
    }

    /// `[SPEC-LYR-120]`: in the recording tier, words a person saved come
    /// before imported ones; and they reach the recording's other files.
    #[test]
    fn saved_words_come_before_imported_ones() {
        let c = library();
        import(&c, "rec-song", "imported");
        save(&c, 21, "corrected", false);
        assert_eq!(shown(&c, 12).as_deref(), Some("corrected"));
        assert_eq!(shown(&c, 11).as_deref(), Some("corrected"));
        assert_eq!(shown(&c, 31).as_deref(), Some("corrected"), "and the work's other recording");
    }

    /// `[SPEC-LYR-130]`: a mark silences the passage and the other cut of the
    /// same audio, and never another recording.
    #[test]
    fn a_mark_is_an_answer_for_this_audio_only() {
        let c = library();
        import(&c, "rec-song", "sung elsewhere");
        let own = save(&c, 12, "kept under the mark", true).unwrap();
        assert!(own.no_words);
        assert_eq!(own.text, "kept under the mark");
        assert_eq!(shown(&c, 12), None, "marked");
        assert_eq!(shown(&c, 11), None, "the album cut of the same audio");
        assert_eq!(shown(&c, 21).as_deref(), Some("sung elsewhere"), "another file of the recording");

        // Unmarking brings the text back.
        save(&c, 12, "kept under the mark", false);
        assert_eq!(shown(&c, 12).as_deref(), Some("kept under the mark"));
    }

    /// `[SPEC-LYR-155]`: clearing keeps the row, empty, and the lower tiers
    /// show again; clearing what was never saved writes nothing.
    #[test]
    fn clearing_shows_the_lower_tiers_again_and_deletes_nothing() {
        let c = library();
        import(&c, "rec-song", "imported");
        assert_eq!(save(&c, 12, "   \r\n ", false), None, "nothing to clear");
        let n: i64 = c.query_row("SELECT COUNT(*) FROM passage_lyrics", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 0);

        save(&c, 12, "mine", false);
        assert_eq!(shown(&c, 12).as_deref(), Some("mine"));
        assert_eq!(save(&c, 12, "", false), None, "cleared reads as no entry");
        assert_eq!(shown(&c, 12).as_deref(), Some("imported"));
        let n: i64 = c.query_row("SELECT COUNT(*) FROM passage_lyrics", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 1, "the row stays, so the clearing can travel");
    }

    /// `[SPEC-LYR-150]`: a passage whose start moves keeps its words, and a
    /// save afterwards supersedes the row under the old start.
    #[test]
    fn a_boundary_edit_does_not_orphan_the_words() {
        let c = library();
        save(&c, 12, "before the edit", false);
        stamp(&c, "aaa", "radio", 4_000, "2001-01-01 10:00:00.000");
        c.execute("UPDATE passages SET start_ms = 6500, end_ms = 188000 WHERE passage_id = 12", []).unwrap();
        assert_eq!(shown(&c, 12).as_deref(), Some("before the edit"));

        save(&c, 12, "after the edit", false);
        assert_eq!(shown(&c, 12).as_deref(), Some("after the edit"));
        let n: i64 = c.query_row("SELECT COUNT(*) FROM passage_lyrics", [], |r| r.get(0)).unwrap();
        assert_eq!(n, 2, "the old row is superseded, not deleted");
        let view = lyrics_view(&c, "main", 11).unwrap().unwrap();
        assert_eq!(view.sources.len(), 1, "and is never offered: {:?}", view.sources);
    }

    /// `[SPEC-LYR-150]`: which row is superseded is measured against the
    /// passage as it is now, not row against row. Passage 41 is saved at
    /// 0-100 s, moved and saved again at 50-150 s -- exactly half shared --
    /// and moved back to 0-90 s, which is the first row's segment and not the
    /// second's. Until 2026-11-13 the second row had superseded the first for
    /// good, and the passage showed nothing of its own.
    #[test]
    fn a_row_is_superseded_against_the_passage_as_it_is_now() {
        let c = library();
        save(&c, 41, "A", false);
        stamp(&c, "ddd", "radio", 0, "2026-11-01 10:00:00.000");
        c.execute("UPDATE passages SET start_ms = 50000, end_ms = 150000 WHERE passage_id = 41", []).unwrap();
        save(&c, 41, "B", false);
        stamp(&c, "ddd", "radio", 50_000, "2026-11-02 10:00:00.000");
        assert_eq!(shown(&c, 41).as_deref(), Some("B"), "the newer, while both reach it");

        c.execute("UPDATE passages SET start_ms = 0, end_ms = 90000 WHERE passage_id = 41", []).unwrap();
        assert_eq!(shown(&c, 41).as_deref(), Some("A"), "the newest row of its segment as it is now");
        let view = lyrics_view(&c, "main", 41).unwrap().unwrap();
        assert_eq!(view.own.map(|o| o.text).as_deref(), Some("A"), "and the editor holds it");
        assert!(sources(&c, 41).is_empty(), "a row no passage reaches now is offered nowhere");
    }

    /// `[SPEC-LYR-115]`: a passage of two recordings shows the words of the
    /// heaviest that has any, as `GET /lyrics` did before 2026-11-12. Until
    /// 2026-11-13 only the heaviest was asked, and with no words of its own
    /// the passage showed nothing.
    #[test]
    fn the_heaviest_recording_with_words_is_shown() {
        let c = library();
        c.execute("INSERT INTO recordings (mbid, title, source) VALUES ('rec-light', 'Light', 'test')", []).unwrap();
        c.execute(
            "INSERT INTO passage_recordings (passage_id, mbid, weight, source) VALUES (41, 'rec-light', 0.5, 'test')",
            [],
        )
        .unwrap();
        import(&c, "rec-light", "the lighter recording's words");
        assert_eq!(shown(&c, 41).as_deref(), Some("the lighter recording's words"));

        import(&c, "rec-other", "the heavier recording's words");
        assert_eq!(shown(&c, 41).as_deref(), Some("the heavier recording's words"), "the heavier first");
        assert_eq!(
            sources(&c, 41),
            vec![(3, "the heavier recording's words".to_string()), (3, "the lighter recording's words".to_string())]
        );
        // What a save stores with the row is still the heaviest.
        save(&c, 41, "typed", false);
        let stored: Option<String> = c.query_row("SELECT mbid FROM passage_lyrics", [], |r| r.get(0)).unwrap();
        assert_eq!(stored.as_deref(), Some("rec-other"));
    }

    /// `[SPEC-LYR-160]`: a row for a file this node lacks speaks for the
    /// recording stored with it; where the file is here, for the passage's
    /// present recording.
    #[test]
    fn a_row_speaks_for_its_stored_recording_only_where_the_file_is_not_here() {
        let c = library();
        c.execute(
            "INSERT INTO passage_lyrics VALUES ('zzz', 'radio', 0, 200000, 'rec-song', 'from another node', 0, \
                                               '2026-11-02 08:00:00.000', 'elsewhere')",
            [],
        )
        .unwrap();
        assert_eq!(shown(&c, 12).as_deref(), Some("from another node"));

        // Saved here as rec-song, then identified again as something else.
        save(&c, 21, "typed here", false);
        stamp(&c, "bbb", "radio", 1_000, "2026-11-03 08:00:00.000");
        assert_eq!(shown(&c, 12).as_deref(), Some("typed here"), "newer than the other node's");
        c.execute("UPDATE passage_recordings SET mbid = 'rec-other' WHERE passage_id = 21", []).unwrap();
        assert_eq!(shown(&c, 12).as_deref(), Some("from another node"), "no longer this recording's");
        assert_eq!(shown(&c, 41).as_deref(), Some("typed here"), "but the one it is now");
    }

    /// Every source of a passage's words, as `(tier, text)`, read both ways.
    fn sources(c: &Connection, passage_id: i64) -> Vec<(u8, String)> {
        let list = |reach: &Reach<'_>| -> Vec<(u8, String)> {
            let r = resolve(c, "main", reach, passage_id, true).unwrap().unwrap();
            r.sources.iter().map(|(tier, pick)| (*tier, pick_text(c, pick).unwrap())).collect()
        };
        let saved = SavedLyrics::load(c, "main").unwrap();
        let whole = list(&Reach::Whole(&saved));
        let asked = list(&Reach::asked(c, "main").unwrap());
        assert_eq!(asked, whole, "passage {passage_id}: every source, read for one request and read whole");
        whole
    }

    const PASSAGES: [i64; 7] = [11, 12, 13, 14, 21, 31, 41];

    /// `[SPEC-LYR-178]`: a request reads the rows that can bear on its passage
    /// and no others, however many are saved, and answers as a read of every
    /// row does -- through a retired `audio_md5`, a recording corrected since
    /// the words were saved, a row for a file this node lacks, a superseded
    /// row, a mark and a cleared row.
    #[test]
    fn a_request_reads_only_what_bears_on_its_passage() {
        let c = library();
        import(&c, "rec-cover", "the cover's imported words");
        // 400 rows about music this node does not hold, of other recordings.
        for i in 0..400 {
            c.execute(
                "INSERT INTO passage_lyrics VALUES (?1, 'radio', 0, 100000, ?2, 'elsewhere', 0, \
                                                   '2026-11-01 08:00:00.000', 'elsewhere')",
                params![format!("far-{i}"), format!("rec-far-{i}")],
            )
            .unwrap();
        }
        // File 1 was once known by another digest, and words were saved then.
        // The table is `rekey_identity.py`'s and the bundle import's, not
        // `schema.sql`'s, which is why no other test here has one.
        c.execute_batch(
            "CREATE TABLE audio_md5_aliases (old_md5 TEXT PRIMARY KEY, new_md5 TEXT NOT NULL, \
                                             generator TEXT NOT NULL, rekeyed_at TEXT NOT NULL);
             INSERT INTO audio_md5_aliases VALUES ('aaa-before', 'aaa', 'test', '2026-11-02');",
        )
        .unwrap();
        c.execute(
            "INSERT INTO passage_lyrics VALUES ('aaa-before', 'radio', 4000, 190000, 'rec-song', 'under the old digest', 0, \
                                               '2026-11-02 08:00:00.000', 'here')",
            [],
        )
        .unwrap();
        assert_eq!(shown(&c, 12).as_deref(), Some("under the old digest"), "followed through the alias");
        // The same cut under the present digest, newer: the old row is superseded.
        save(&c, 12, "the radio cut, saved since", false);
        stamp(&c, "aaa", "radio", 4_000, "2026-11-03 08:00:00.000");
        // A file this node lacks, of the cover's recording; and file 2, saved
        // as rec-song and identified since as the unrelated rec-other.
        c.execute(
            "INSERT INTO passage_lyrics VALUES ('zzz', 'radio', 0, 240000, 'rec-cover', 'the cover, from another node', 0, \
                                               '2026-11-04 08:00:00.000', 'elsewhere')",
            [],
        )
        .unwrap();
        save(&c, 21, "typed for file two", false);
        c.execute("UPDATE passage_recordings SET mbid = 'rec-other' WHERE passage_id = 21", []).unwrap();
        save(&c, 14, "", true);

        let al = aliases(&c, "main").unwrap();
        let of = |files: &[&str]| -> usize {
            let files: HashSet<String> = files.iter().map(|f| f.to_string()).collect();
            SavedLyrics::of_files(&c, "main", &files, &al).unwrap().speaking.len()
        };
        assert_eq!(SavedLyrics::load(&c, "main").unwrap().speaking.len(), 404, "every entry, read whole");
        assert_eq!(of(&["aaa"]), 2, "its own file's alone: one cut's words and a mark, the old digest's row superseded");
        assert_eq!(of(&["aaa-before"]), 0, "a retired digest is no file of its own");
        let reach = Reach::asked(&c, "main").unwrap();
        let speaks = |rec: &str| -> Vec<String> {
            let mut texts: Vec<String> = reach
                .speaking_for(&c, "main", &[rec.to_string()])
                .unwrap()
                .iter()
                .map(|e| saved_text(&c, e).unwrap())
                .collect();
            texts.sort();
            texts
        };
        assert_eq!(speaks("rec-other"), ["typed for file two"], "by the recording it is now, not the one stored");
        assert_eq!(speaks("rec-cover"), ["the cover, from another node"], "by the one stored, the file not here");
        assert_eq!(speaks("rec-far-7"), ["elsewhere"], "one of the 400, by the table's index");

        for p in PASSAGES {
            shown(&c, p);
            sources(&c, p);
        }
        assert_eq!(shown(&c, 21).as_deref(), Some("typed for file two"));
        assert_eq!(shown(&c, 41).as_deref(), Some("typed for file two"), "the recording file two is now");
        assert_eq!(shown(&c, 31).as_deref(), Some("the cover, from another node"));
        assert_eq!(shown(&c, 14), None, "marked");
        assert_eq!(shown(&c, 13), None, "and so is the other cut of the marked audio");
        assert_eq!(shown(&c, 12).as_deref(), Some("the radio cut, saved since"));
        assert_eq!(
            sources(&c, 11),
            vec![
                (2, "the radio cut, saved since".to_string()),
                (4, "the cover, from another node".to_string()),
                (4, "the cover's imported words".to_string()),
            ],
            "file two's words are no longer this recording's, and the superseded row is never offered"
        );
    }

    /// `[SPEC-LYR-176]`: between two passages one file must speak for, the
    /// nearer tier comes first, whenever either was saved; within a tier,
    /// saved before imported and newer before older.
    #[test]
    fn a_nearer_tier_comes_before_a_newer_save() {
        let words = |tier: u8, saved_at: Option<&str>| ShownLyrics {
            text: String::new(),
            tier,
            saved_at: saved_at.map(str::to_string),
        };
        let own_older = words(1, Some("2026-11-12 10:00:00.000"));
        let borrowed_newer = words(4, Some("2026-11-13 10:00:00.000"));
        assert!(own_older.before(&borrowed_newer), "a passage's own words, though saved earlier");
        assert!(!borrowed_newer.before(&own_older));
        assert!(words(3, None).before(&words(4, Some("2026-11-13 10:00:00.000"))), "its recording's imported words before a work's saved ones");
        assert!(words(3, Some("2026-11-12 10:00:00.000")).before(&words(3, None)), "saved before imported");
        assert!(words(3, Some("2026-11-12 10:00:00.001")).before(&words(3, Some("2026-11-12 10:00:00.000"))), "newer before older");
        assert!(!words(3, None).before(&words(3, None)), "and equals keep the one already held");
    }

    /// `[SPEC-LYR-172]`: the editor's view names each source, lists a text
    /// once, and never lists the passage's own.
    #[test]
    fn the_view_lists_every_other_source_once() {
        let c = library();
        import(&c, "rec-song", "shared words");
        import(&c, "rec-cover", "shared words");
        save(&c, 11, "the album cut's", false);
        save(&c, 12, "my own", false);

        let v = lyrics_view(&c, "main", 12).unwrap().unwrap();
        assert_eq!(v.kind, "radio");
        assert_eq!(v.title.as_deref(), Some("rec-song"));
        assert_eq!(v.own.as_ref().map(|o| o.text.as_str()), Some("my own"));
        assert_eq!(v.shown_tier, Some(1));
        let listed: Vec<(u8, &str)> = v.sources.iter().map(|s| (s.tier, s.text.as_str())).collect();
        assert_eq!(listed, vec![(2, "the album cut's"), (3, "shared words")], "the work's copy is the same text");
        assert_eq!(v.sources[0].kind.as_deref(), Some("album"));
        assert_eq!(v.sources[1].imported.as_deref(), Some("mulibplay"));
        assert_eq!(v.sources[1].title.as_deref(), Some("rec-song"));

        assert!(lyrics_view(&c, "main", 999).unwrap().is_none());
    }

    /// `[SPEC-LYR-174]`: line ends, the size limit, and no such passage.
    #[test]
    fn a_save_normalises_and_refuses() {
        let c = library();
        let own = save(&c, 12, "one\r\ntwo\rthree\n\n", false).unwrap();
        assert_eq!(own.text, "one\ntwo\nthree");
        assert_eq!(own.origin.as_deref(), Some("here"));
        assert!(save_passage_lyrics(&c, "main", 999, "x", false, "here").unwrap().is_none());
        let long = "x".repeat(LYRICS_MAX_BYTES + 1);
        assert!(save_passage_lyrics(&c, "main", 12, &long, false, "here").is_err());
    }

    /// A library from before any of these tables shows nothing, quietly.
    #[test]
    fn absent_tables_are_no_words_not_an_error() {
        let c = library();
        c.execute_batch(
            "DROP TABLE passage_lyrics; DROP TABLE lyrics; DROP TABLE recording_works; DROP TABLE works;",
        )
        .unwrap();
        assert_eq!(shown(&c, 12), None);
        let v = lyrics_view(&c, "main", 12).unwrap().unwrap();
        assert!(v.own.is_none() && v.sources.is_empty());
    }
}
