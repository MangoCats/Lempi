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

fn qe(e: rusqlite::Error) -> DbError {
    DbError::Query(e.to_string())
}

/// The rows of a query over a table that may not be there. An absent table is
/// a library that predates it, or a node that holds none, and reads as no
/// rows; any other failure is a failure.
fn rows<T, P: rusqlite::Params>(
    conn: &Connection,
    sql: &str,
    p: P,
    f: impl FnMut(&rusqlite::Row<'_>) -> rusqlite::Result<T>,
) -> Result<Vec<T>, DbError> {
    let mut st = match conn.prepare_cached(sql) {
        Ok(st) => st,
        Err(e) if e.to_string().contains("no such table") => return Ok(Vec::new()),
        Err(e) => return Err(qe(e)),
    };
    let out = st.query_map(p, f).map_err(qe)?.collect::<rusqlite::Result<Vec<T>>>().map_err(qe)?;
    Ok(out)
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

/// What a passage is, as far as its words are concerned.
#[derive(Debug, Clone)]
struct Facts {
    audio_md5: String,
    kind: String,
    start_ms: i64,
    end_ms: i64,
    /// Its heaviest recording `[SPEC-LYR-115]`, as `COLS` chooses it.
    mbid: Option<String>,
}

fn facts(conn: &Connection, lib: &str, passage_id: i64) -> Result<Option<Facts>, DbError> {
    let sql = format!(
        "SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms, \
                (SELECT pr.mbid FROM {lib}.passage_recordings pr WHERE pr.passage_id = p.passage_id \
                  ORDER BY pr.weight DESC, pr.mbid LIMIT 1) \
           FROM {lib}.passages p JOIN {lib}.files f ON f.file_id = p.file_id WHERE p.passage_id = ?1"
    );
    let mut st = conn.prepare_cached(&sql).map_err(qe)?;
    let got = st.query_row([passage_id], |r| {
        Ok(Facts { audio_md5: r.get(0)?, kind: r.get(1)?, start_ms: r.get(2)?, end_ms: r.get(3)?, mbid: r.get(4)? })
    });
    match got {
        Ok(f) => Ok(Some(f)),
        Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
        Err(e) => Err(qe(e)),
    }
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

/// Every saved entry, without its text. Read once for a request, and once for
/// a whole run of the file writers, so that neither asks the catalogue which
/// files hold a recording: `passage_recordings` has no index by recording.
pub struct SavedLyrics {
    /// Newest first, a tie by key, which is the order of `[SPEC-LYR-120]`.
    entries: Vec<Entry>,
}

impl SavedLyrics {
    pub fn load(conn: &Connection, lib: &str) -> Result<Self, DbError> {
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
        let raw = rows(
            conn,
            "SELECT audio_md5, kind, start_ms, end_ms, mbid, no_words, length(trim(text)) = 0, \
                    updated_at, origin \
               FROM passage_lyrics ORDER BY updated_at DESC, audio_md5, kind, start_ms",
            [],
            |r| {
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
            },
        )?;
        if raw.is_empty() {
            return Ok(Self { entries: Vec::new() });
        }
        let aliases: HashMap<String, String> = rows(
            conn,
            &format!("SELECT old_md5, new_md5 FROM {lib}.audio_md5_aliases"),
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?
        .into_iter()
        .collect();

        let mut entries: Vec<Entry> = Vec::new();
        // (file, kind) -> the entries already taken for it, each newer than
        // any row still to come.
        let mut taken: HashMap<(String, String), Vec<usize>> = HashMap::new();
        for r in raw {
            let file = aliases.get(&r.audio_md5).cloned().unwrap_or_else(|| r.audio_md5.clone());
            let heads = taken.entry((file.clone(), r.kind.clone())).or_default();
            let span = (r.start_ms, r.end_ms);
            if heads.iter().any(|&i| same_segment((entries[i].start_ms, entries[i].end_ms), span)) {
                continue; // superseded
            }
            heads.push(entries.len());
            entries.push(Entry {
                audio_md5: r.audio_md5,
                kind: r.kind,
                start_ms: r.start_ms,
                end_ms: r.end_ms,
                file,
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
            });
        }

        // The present recording of each entry's passage, where the file is here.
        let sql = format!(
            "SELECT p.start_ms, p.end_ms, \
                    (SELECT pr.mbid FROM {lib}.passage_recordings pr WHERE pr.passage_id = p.passage_id \
                      ORDER BY pr.weight DESC, pr.mbid LIMIT 1) \
               FROM {lib}.files f JOIN {lib}.passages p ON p.file_id = f.file_id \
              WHERE f.audio_md5 = ?1 AND p.kind = ?2 ORDER BY p.start_ms"
        );
        for e in &mut entries {
            let held: Vec<(i64, i64, Option<String>)> =
                rows(conn, &sql, params![e.file, e.kind], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)))?;
            let span = (e.start_ms, e.end_ms);
            let here = held
                .iter()
                .filter(|(s, t, _)| same_segment((*s, *t), span))
                .max_by_key(|(s, _, _)| *s == e.start_ms);
            if let Some((_, _, Some(mbid))) = here {
                e.recording = Some(mbid.clone());
            }
        }
        Ok(Self { entries })
    }

    /// The entries saved for `f`'s own audio: its file, and its segment.
    fn of_passage(&self, f: &Facts) -> Vec<usize> {
        (0..self.entries.len())
            .filter(|&i| {
                let e = &self.entries[i];
                e.file == f.audio_md5 && same_segment((e.start_ms, e.end_ms), (f.start_ms, f.end_ms))
            })
            .collect()
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

/// Every other recording of a work `mbid` performs `[SPEC-LYR-100]`, in a
/// fixed order. A recording of several works matches through any of them.
fn work_mates(conn: &Connection, lib: &str, mbid: &str) -> Result<Vec<String>, DbError> {
    let works: Vec<String> =
        rows(conn, &format!("SELECT work_mbid FROM {lib}.recording_works WHERE mbid = ?1"), [mbid], |r| r.get(0))?;
    let sql = format!("SELECT mbid FROM {lib}.recording_works WHERE work_mbid = ?1");
    let mut mates: Vec<String> = Vec::new();
    for w in &works {
        mates.extend(rows(conn, &sql, [w], |r| r.get::<_, String>(0))?);
    }
    mates.retain(|m| m != mbid);
    mates.sort();
    mates.dedup();
    Ok(mates)
}

/// Where one source of words is.
#[derive(Debug, Clone)]
enum Pick {
    Saved(usize),
    Imported(Imported),
}

struct Resolved {
    facts: Facts,
    /// The passage's own entry, whatever its state.
    own: Option<usize>,
    /// Every source below the passage's own that has text, by tier and then
    /// by `[SPEC-LYR-120]`. Complete only when asked for all of it.
    sources: Vec<(u8, Pick)>,
    /// The tier shown, and which of `sources` it is; `None` where nothing is.
    /// Tier 1 is the passage's own and names no source.
    shown: Option<(u8, Option<usize>)>,
}

/// Walk the tiers for `passage_id`. With `all` false it stops as soon as what
/// is shown is known, which is what a player asking for one text needs.
fn resolve(
    conn: &Connection,
    lib: &str,
    saved: &SavedLyrics,
    passage_id: i64,
    all: bool,
) -> Result<Option<Resolved>, DbError> {
    let Some(f) = facts(conn, lib, passage_id)? else { return Ok(None) };
    let mine = saved.of_passage(&f);
    let own = mine.iter().copied().find(|&i| saved.entries[i].kind == f.kind);
    let own_state = own.map(|i| saved.entries[i].state);

    let mut sources: Vec<(u8, Pick)> = Vec::new();
    // Decided: `Some(None)` is "nothing, by a mark"; `Some(Some(..))` a text.
    let mut shown: Option<Option<(u8, Option<usize>)>> = match own_state {
        Some(State::Text) => Some(Some((1, None))),
        Some(State::Mark) => Some(None),
        _ => None,
    };

    // Tier 2: the same audio under another kind. A mark there is an answer
    // too `[SPEC-LYR-130]`: what is true of one cut is true of the other.
    for &i in mine.iter().filter(|&&i| saved.entries[i].kind != f.kind) {
        match saved.entries[i].state {
            State::Cleared => {}
            State::Mark => {
                shown.get_or_insert(None);
            }
            State::Text => {
                sources.push((2, Pick::Saved(i)));
                shown.get_or_insert(Some((2, Some(sources.len() - 1))));
            }
        }
    }

    let taken: HashSet<usize> = mine.iter().copied().collect();
    if let Some(m) = f.mbid.clone().filter(|_| all || shown.is_none()) {
        // Tiers 3 and 4. A mark is never read here: another recording of a
        // song may be sung where this one is not.
        for tier in [3u8, 4] {
            if !all && shown.is_some() {
                break;
            }
            let recordings = if tier == 3 { vec![m.clone()] } else { work_mates(conn, lib, &m)? };
            if recordings.is_empty() {
                continue;
            }
            let before = sources.len();
            for (i, e) in saved.entries.iter().enumerate() {
                let speaks = e.recording.as_ref().is_some_and(|r| recordings.contains(r));
                if speaks && e.state == State::Text && !taken.contains(&i) {
                    sources.push((tier, Pick::Saved(i)));
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

fn pick_text(conn: &Connection, saved: &SavedLyrics, pick: &Pick) -> Result<String, DbError> {
    match pick {
        Pick::Saved(i) => saved_text(conn, &saved.entries[*i]),
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
    /// Whether these come before `other`: words a person saved before
    /// imported ones, and newer before older `[SPEC-LYR-120]`. For choosing
    /// between two passages that one file must speak for `[SPEC-LYR-176]`.
    pub fn before(&self, other: &Self) -> bool {
        match (&self.saved_at, &other.saved_at) {
            (Some(mine), Some(theirs)) => mine > theirs,
            (Some(_), None) => true,
            _ => false,
        }
    }
}

/// The words `passage_id` shows, by `[SPEC-LYR-100]`: `None` where the library
/// has none for it, where it is marked as having none, and where there is no
/// such passage.
pub fn shown_words(
    conn: &Connection,
    lib: &str,
    saved: &SavedLyrics,
    passage_id: i64,
) -> Result<Option<ShownLyrics>, DbError> {
    let Some(r) = resolve(conn, lib, saved, passage_id, false)? else { return Ok(None) };
    let (tier, pick) = match r.shown {
        None => return Ok(None),
        Some((tier, None)) => match r.own {
            Some(i) => (tier, Pick::Saved(i)),
            None => return Ok(None),
        },
        Some((tier, Some(n))) => (tier, r.sources[n].1.clone()),
    };
    let saved_at = match &pick {
        Pick::Saved(i) => Some(saved.entries[*i].updated_at.clone()),
        Pick::Imported(_) => None,
    };
    Ok(Some(ShownLyrics { text: pick_text(conn, saved, &pick)?, tier, saved_at }))
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
    let saved = SavedLyrics::load(conn, lib)?;
    let Some(r) = resolve(conn, lib, &saved, passage_id, true)? else { return Ok(None) };
    let own = match r.own.map(|i| &saved.entries[i]) {
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
        let text = pick_text(conn, &saved, pick)?;
        // Listed once, at its highest place.
        if !seen.insert(text.trim().to_string()) {
            continue;
        }
        let (recording, kind, imported, updated_at, origin) = match pick {
            Pick::Saved(i) => {
                let e = &saved.entries[*i];
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
    let (title, artist, album) = passage_naming(conn, lib, passage_id, r.facts.mbid.as_deref());
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
    if text.len() > LYRICS_MAX_BYTES {
        return Err(DbError::Query(format!("the text is over {LYRICS_MAX_BYTES} bytes")));
    }
    let Some(f) = facts(conn, lib, passage_id)? else { return Ok(None) };
    let text = normalise_lyrics(text);
    let saved = SavedLyrics::load(conn, lib)?;
    let had = saved.of_passage(&f).into_iter().any(|i| saved.entries[i].kind == f.kind);
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
        params![f.audio_md5, f.kind, f.start_ms, f.end_ms, f.mbid, text, no_words as i64, origin],
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

    fn shown(c: &Connection, passage_id: i64) -> Option<String> {
        let saved = SavedLyrics::load(c, "main").unwrap();
        shown_lyrics(c, "main", &saved, passage_id).unwrap()
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
