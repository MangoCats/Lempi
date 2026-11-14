//! Loading what Stage A needs, and choosing from it.
//!
//! [`frequency`](super::frequency) is pure arithmetic and stays that way. This
//! module is the only place that knows those numbers live in SQLite, which is
//! what lets the whole selection rule be tested without a database.
//!
//! Everything is loaded once and held in memory. The library is 8,079 radio
//! passages with ~37,000 plays behind it — a few megabytes — and selection then
//! costs no I/O at all, which matters on a Pi where the queue is topped up
//! every few seconds `[REQ-HW-140]`.

use std::collections::{HashMap, HashSet};

use serde::Serialize;

use crate::db::QualifyingConn;

use super::christmas::{self, Place, Reserve};
use super::frequency::{
    weigh_under, ArtistCredit, Candidate, Exclusion, Policy, ProfanityTolerance, Related, Rotation,
    TimeScale, Tuning, Weighing,
};
use super::flavor::{centroid, Flavor, FlavorIndex};
use super::occasion::{civil_from_unix, ordinal, Curve, Interp, Occasions};
use super::programme::Programme;
use super::shape::{nearest_first, shape, too_disliked, Seed, Shaping, LIKE_SEED_WEIGHT, RAND_POOL};
use crate::db::{row_to_entry, DbError, COLS, FROM};
use crate::queue::QueueEntry;

/// How a pass of Stage A treats history.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Weigh {
    /// As every ordinary pick.
    Normal,
    /// As though nothing had played: the fallback `[SPEC-DIR-232]`.
    Relaxed,
    /// History kept, rotation set aside but for the artist gap: every passage
    /// in one of the Christmas reserve's places `[SPEC-XMR-040]`,
    /// `[SPEC-XMR-050]`.
    RotationSetAside,
}

/// A radio passage and the identity Stage A weighs it by.
struct Row {
    entry: QueueEntry,
    /// `None` when the passage has no identified recording. It can still play:
    /// an unidentified passage has no history, so it weighs as never-played
    /// rather than being silently excluded.
    mbid: Option<String>,
}

/// Deterministic, seedable, and dependency-free — SplitMix64.
///
/// Seedable matters more than quality here: `[REQ-PD-110]` measures divergence
/// from MuLibPlay, and a comparison whose randomness cannot be replayed is not
/// a measurement. Ten lines is a fair price for a reproducible station.
pub struct Rng(u64);

impl Rng {
    pub fn seeded(seed: u64) -> Self {
        Self(seed)
    }
    /// Seeded from the clock, for ordinary listening.
    pub fn from_clock() -> Self {
        let n = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0x9E3779B97F4A7C15);
        Self(n)
    }
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z ^ (z >> 31)
    }
    /// Uniform in `[0, 1)`.
    pub fn unit(&mut self) -> f64 {
        // 53 bits: the most an f64 can represent exactly.
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }
    /// `items` in an order of their own, every order equally likely:
    /// Fisher-Yates, from the last place down. One copy, for every place a
    /// pick needs "no fixed order" -- passages a sort cannot tell apart, and
    /// a pool with no flow order to rank it by.
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        for i in (1..items.len()).rev() {
            let j = ((self.unit() * (i + 1) as f64) as usize).min(i);
            items.swap(i, j);
        }
    }
}

/// How many losing candidates the record keeps.
///
/// `[SPEC-DIR-190]` asks for "the runners-up that lost". All 8,000 of them is
/// not an explanation, it is a data dump; the heaviest few are what answer
/// "why not something else?".
const RUNNERS_UP: usize = 5;

/// How long a passage that has never played counts as unplayed, for the
/// fallback's preference for the longest unplayed: ten years, which outranks
/// anything that has.
pub(crate) const NEVER_PLAYED_S: f64 = 10.0 * 365.0 * 86_400.0;
/// The least a just-marked passage counts as unplayed, so the fallback can
/// still draw it when nothing else is left.
pub(crate) const IDLE_FLOOR_S: f64 = 60.0;

/// Rank decay `[SPEC-DIR-165]`. Applied over the flow-ordered pool, so the
/// passages that follow the queue tail best are favoured — without ever
/// becoming certain. At rank 99, the last of `RAND_POOL`, this is ×0.018.
pub const RANK_DECAY: f64 = 0.96;

/// A complete answer to "why this passage?" `[REQ-VIS-100]`.
///
/// Every term separately, never just the product -- a single number cannot be
/// argued with, and arguing with it is the point.
#[derive(Debug, Clone, Serialize)]
pub struct Explanation {
    pub passage_id: i64,
    pub title: String,
    pub weight: f64,
    /// The weight the roulette actually used: `weight × decay^rank`
    /// `[SPEC-DIR-165]`.
    pub decayed_weight: f64,
    /// Position in the flow order, 0 being the best follow-on.
    pub rank: usize,
    /// Flavor distance to the passage this one follows `[SPEC-DIR-160]`.
    pub flow_distance: Option<f64>,
    pub roulette_target: f64,
    pub artist_weight: f64,
    pub artist_blocked: bool,
    pub recording_restraint: f64,
    pub recording_ramp: f64,
    pub related_damping: f64,
    pub length_bonus: f64,
    pub occasion: f64,
    /// This player's profanity term `[SPEC-PTL-070]`.
    pub profanity: f64,
    /// The programme slot this pick was chosen for `[SPEC-DIR-180]`, if any:
    /// the slot in force at `starts_at`, not at the moment of choosing
    /// `[SPEC-DIR-186]`.
    pub slot: Option<String>,
    /// When this passage is projected to begin, in unix seconds: now, plus
    /// what is left of the passage on air and every passage queued ahead of
    /// it, played through without a skip `[SPEC-DIR-186]`. Every term above
    /// was judged then, and the slot is the one in force then.
    pub starts_at: i64,
    /// Its id, which `selection_decisions.program_id` records `[SPEC-DIR-190]`.
    /// A slot renewed by new seeds has a new one `[SPEC-PGM-125]`, so the id
    /// names the seeds this pick was shaped by, where the name may not.
    pub slot_id: Option<i64>,
    /// The same value under its name before 2026-11-01, written for readers
    /// that have not learned `slot` yet. Never read here.
    pub program: Option<String>,
    /// What pool shaping did `[SPEC-DIR-145]`.
    pub shaping: Shaping,
    /// Distance to each seed, in seed order `[SPEC-DIR-190]`.
    pub seed_distances: Vec<f64>,
    pub pool_size: usize,
    /// The sum of the weights the roulette drew on -- decayed, where there
    /// was a flow order.
    pub pool_weight: f64,
    /// This passage's chance of winning the draw, as a percentage: the
    /// weight it was drawn on over `pool_weight` -- the honest statement of
    /// how likely it was, as opposed to how good it is `[SPEC-DIR-191]`.
    pub share_pct: f64,
    pub runners_up: Vec<RunnerUp>,
    /// Which stages ran for this pick, in words: whether there was a flow
    /// order to decay over, or whether the relaxed fallback chose it. Stated
    /// in the record so a stored decision says how it was made.
    pub stages: &'static str,
    /// Where in the roulette list this passage stood: Stage C's own place,
    /// or one the Christmas reserve kept `[SPEC-XMR-070]`. A `filler` was
    /// weighed with rotation set aside, which is why it could play while
    /// its rotation would have held it.
    pub place: Place,
    /// What the Christmas reserve did to this pick's list `[SPEC-XMR-070]`.
    pub christmas: Reserve,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunnerUp {
    pub passage_id: i64,
    pub title: String,
    /// The recording's own mbid, and the credited artist's, so a runner-up
    /// in "It beat" reaches a preference panel the same way a queue row
    /// does `[REQ-VIS-285]`. Absent for unidentified audio or an
    /// uncredited artist -- the same case that already leaves `artist`
    /// `None`.
    pub mbid: Option<String>,
    pub artist: Option<String>,
    pub artist_mbid: Option<String>,
    pub weight: f64,
}

/// A chosen passage and the reasoning behind it.
pub struct Decision {
    pub entry: QueueEntry,
    pub why: Explanation,
}

/// The list one ordinary pick draws on, and what made it
/// ([`Director::decide`]'s first half).
struct Roulette<'a> {
    /// In rank order: the passage, its weighing, its flow distance from the
    /// queue tail, and the weight the roulette draws it on -- decayed by rank
    /// where there is a flow order `[SPEC-DIR-165]`.
    pool: Vec<(&'a QueueEntry, Weighing, Option<f64>, f64)>,
    /// Where each came from `[SPEC-XMR-040]`, one for each of `pool`.
    places: Vec<Place>,
    tally: Reserve,
    shaping: Shaping,
    seeds: Vec<Seed<'a>>,
    stages: &'static str,
}

/// A passage marked as coming, held until it is judged `[REQ-PD-112]`: played
/// ([`Director::settle`]), or declined or never played
/// ([`Director::forget_queued`]). Not `Clone`: each mark is resolved once.
#[derive(Debug)]
pub struct QueuedNote {
    token: u64,
    passage_id: i64,
}

impl QueuedNote {
    pub fn passage_id(&self) -> i64 {
        self.passage_id
    }
}

/// One rotation key a mark touches, in whichever tier it lives.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum MarkKey {
    Passage(i64),
    Recording(String),
    Work(String),
    Artist(String),
}

/// The marks still open on one key, and what the key held before the first of
/// them. `max` cannot be inverted from its result, so a forgotten mark is
/// undone by recomputing from these rather than by restoring one saved value:
/// exact whatever order the marks are resolved in (review O-05).
#[derive(Debug)]
struct OpenMarks {
    before: Option<i64>,
    marks: Vec<(u64, i64)>,
}

/// An artist tag as a rotation key `[REQ-AND-315]`: trimmed, lower case, inner
/// whitespace collapsed, so "The Beatles" and "the  beatles " are one artist.
/// The `tag:` prefix keeps it apart from every MBID.
fn tag_artist_key(tag: &str) -> String {
    let name: Vec<String> = tag.split_whitespace().map(str::to_lowercase).collect();
    format!("tag:{}", name.join(" "))
}

pub struct Director {
    rows: Vec<Row>,
    /// passage → its place in `rows`. Every per-candidate lookup goes through
    /// it: a scan of `rows` per candidate made each pick quadratic in the
    /// library, measured 34-39 ms a pick on the desktop at 10,850 radio
    /// passages against 7 ms for all of Stage A (2026-11-01).
    index: HashMap<i64, usize>,
    policy: Policy,
    recording_tuning: HashMap<String, Tuning>,
    artist_tuning: HashMap<String, Tuning>,
    /// recording → every artist it credits, sorted. Was one artist per
    /// recording until 2026-10-16, collected into a map that kept whichever
    /// credit the scan returned last -- the highest artist MBID -- so Prince
    /// + The Time rotated as The Time alone `[SPEC-DIR-114]`.
    artist_of: HashMap<String, Vec<String>>,
    /// passage → an artist key from its file's tag, for a passage `artist_of`
    /// cannot reach: no recording, or a recording with no credited artist
    /// `[REQ-AND-315]`. Found music on a phone is all like this, and so is
    /// music ingested locally anywhere. Keyed `tag:<normalised name>`, which
    /// no MBID can equal, so a tag never blocks an identified artist and is
    /// never blocked by one.
    tag_artist: HashMap<i64, String>,
    /// Radio passages held back from selection by `passages.director_hold`
    /// `[SPEC-HOLD-010]`. Empty for a catalogue that predates the column.
    held: HashSet<i64>,
    last_played: HashMap<String, i64>,
    // (see `QueuedNote` for why the previous values are handed back)
    artist_last_played: HashMap<String, i64>,
    /// recording → the works it performs `[GDE-WRK-035]`. Empty when the
    /// catalogue predates `recording_works`, which leaves the work tier
    /// inactive and the recording tier doing what it always did.
    works_of: HashMap<String, Vec<String>>,
    /// The narrowest identity tier and the widest, beside the recording tier
    /// `last_played` already carries. A passage without a recording MBID has
    /// only the first `[GDE-WRK-055]`.
    passage_last_played: HashMap<i64, i64>,
    work_last_played: HashMap<String, i64>,
    /// When each recording was last skipped, and last removed from the queue
    /// unheard `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`. Separate maps from
    /// `last_played` on purpose: a rejection suppresses and does nothing else,
    /// so it must not be reachable from anything that computes a weight.
    last_skipped: HashMap<String, i64>,
    last_dequeued: HashMap<String, i64>,
    /// Passages marked as coming and not yet judged: each mark's keys, and the
    /// marks open on each key `[REQ-PD-112]`.
    marked: HashMap<u64, (i64, Vec<MarkKey>)>,
    open: HashMap<MarkKey, OpenMarks>,
    next_token: u64,
    relations: HashMap<String, Vec<(String, f64)>>,
    occasions: Occasions,
    /// Each recording's profanity value, own over inherited `[SPEC-PTL-060]`.
    /// Only values above zero: an absent one is zero.
    profanity: HashMap<String, f64>,
    /// The radio passages whose recording has a Christmas value above zero,
    /// own over inherited `[SPEC-XMR-015]`, as places in `rows`, in `rows`
    /// order; and the same as passage ids, for the test Stage C's list needs.
    christmas_rows: Vec<usize>,
    christmas: HashSet<i64>,
    /// This player's switch for the Christmas reserve `[SPEC-XMR-080]`. On
    /// unless the player has turned it off.
    christmas_on: bool,
    flavor: FlavorIndex,
    programme: Programme,
    /// Centroids of liked and disliked flavor `[SPEC-DIR-150]`. Built once:
    /// they change only when the listener does.
    like: Option<Flavor>,
    dislike: Option<Flavor>,
}

/// An optional read in `Director::load` that failed: quiet when what it reads is
/// absent -- a library from before the feature -- and a warning otherwise, naming
/// what selection goes without.
fn optional_read(what: &str, e: &rusqlite::Error) {
    let msg = e.to_string();
    if !(msg.contains("no such table") || msg.contains("no such column")) {
        tracing::warn!("director: could not read {what}, so selection goes without them: {msg}");
    }
}

impl Director {
    /// `load`'s optional reads -- rejections, the time scales -- fall back to
    /// nothing, so a library from before a feature still plays. A table or
    /// column that is absent is that case, and quiet; any other failure is a
    /// read that did not happen, and is said `[CLAUDE.md §6]`.
    pub fn load(conn: &QualifyingConn) -> Result<Self, DbError> {
        let q = |e: rusqlite::Error| DbError::Query(e.to_string());

        let sql = format!("SELECT {COLS} {FROM} WHERE p.kind = 'radio'");
        let mut stmt = conn.prepare(&sql).map_err(q)?;
        let rows = stmt
            .query_map([], |r| {
                let entry = row_to_entry(r)?;
                let mbid = entry.mbid.clone();
                Ok(Row { entry, mbid })
            })
            .map_err(q)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(q)?;
        drop(stmt);

        let mut recording_tuning = HashMap::new();
        let mut artist_tuning = HashMap::new();
        let mut stmt = conn
            .prepare(
                "SELECT subject_kind, subject_id, rotation, recovery, restraint \
                 FROM listener_preferences",
            )
            .map_err(q)?;
        let prefs = stmt
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, String>(1)?,
                    r.get::<_, Option<f64>>(2)?,
                    r.get::<_, Option<f64>>(3)?,
                    r.get::<_, Option<f64>>(4)?,
                ))
            })
            .map_err(q)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(q)?;
        drop(stmt);
        for (kind, id, rot, rec, res) in prefs {
            // A NULL column means "not tuned", which is not the same as zero:
            // it must fall back to the default, not suppress the subject.
            let (base, map) = match kind.as_str() {
                "artist" => (Tuning::artist_defaults(), &mut artist_tuning),
                _ => (Tuning::recording_defaults(), &mut recording_tuning),
            };
            map.insert(
                id,
                Tuning {
                    rotation: rot.unwrap_or(base.rotation),
                    recovery: rec.unwrap_or(base.recovery),
                    restraint: res.unwrap_or(base.restraint),
                },
            );
        }

        // Every credit, not one per recording: collecting these rows into a
        // plain map kept only the last, and on lempi02w that took Prince off
        // 23 of his 103 radio recordings (measured 2026-10-16).
        let mut artist_of: HashMap<String, Vec<String>> = HashMap::new();
        {
            let mut stmt = conn
                .prepare("SELECT mbid, artist_mbid FROM __LIB__.recording_artists ORDER BY mbid, artist_mbid")
                .map_err(q)?;
            let rows = stmt
                .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))
                .map_err(q)?
                .collect::<rusqlite::Result<Vec<_>>>()
                .map_err(q)?;
            for (mbid, artist) in rows {
                artist_of.entry(mbid).or_default().push(artist);
            }
        }

        // A library built before `[GDE-WRK-035]` has no `recording_works`. The
        // work tier is then inactive -- which is the behaviour that shipped for
        // years, so it is safe -- but it must say so rather than look like a
        // library that simply has no works `[GDE-DEP-060]`: those two states
        // produce identical selection and only one of them is expected.
        let mut works_of: HashMap<String, Vec<String>> = HashMap::new();
        match conn.prepare("SELECT mbid, work_mbid FROM __LIB__.recording_works") {
            Ok(mut stmt) => {
                let rows = stmt
                    .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))
                    .map_err(q)?
                    .collect::<rusqlite::Result<Vec<_>>>()
                    .map_err(q)?;
                for (mbid, work) in rows {
                    works_of.entry(mbid).or_default().push(work);
                }
            }
            Err(e) => tracing::warn!(
                "director: no recording_works ({e}); same-song blocking is \
                 limited to the recording tier [GDE-WRK-035]"
            ),
        }

        // The artist tier's fallback `[REQ-AND-315]`: a radio passage no
        // credited artist reaches is keyed by its file's artist tag. A library
        // without `file_tags` says so, the way one without `recording_works`
        // does, and such passages rotate on their own passage id alone, as
        // they always did.
        let mut tag_artist: HashMap<i64, String> = HashMap::new();
        match conn.prepare(
            "SELECT p.passage_id, t.artist FROM __LIB__.passages p \
               JOIN __LIB__.file_tags t ON t.file_id = p.file_id \
              WHERE p.kind = 'radio' AND t.artist IS NOT NULL AND TRIM(t.artist) <> '' \
                AND NOT EXISTS (SELECT 1 FROM __LIB__.passage_recordings pr \
                                  JOIN __LIB__.recording_artists ra ON ra.mbid = pr.mbid \
                                 WHERE pr.passage_id = p.passage_id)",
        ) {
            Ok(mut stmt) => {
                let rows = stmt
                    .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?)))
                    .map_err(q)?
                    .collect::<rusqlite::Result<Vec<_>>>()
                    .map_err(q)?;
                for (passage_id, artist) in rows {
                    tag_artist.insert(passage_id, tag_artist_key(&artist));
                }
            }
            Err(e) => tracing::warn!(
                "director: no file_tags ({e}); a passage with no credited artist \
                 rotates on its passage alone [REQ-AND-315]"
            ),
        }

        // Passages held back by a mark of their own `[SPEC-HOLD-010]`. The
        // column arrives on a speaker with the first catalogue sync after it
        // exists on the desktop (`star_patch.py` adds a column the node
        // lacks), so a player can meet a catalogue without it; it then holds
        // nothing, and says so, rather than failing to load at all.
        let mut held: HashSet<i64> = HashSet::new();
        match conn.prepare(
            "SELECT passage_id FROM __LIB__.passages \
              WHERE kind = 'radio' AND director_hold IS NOT NULL AND director_hold <> ''",
        ) {
            Ok(mut stmt) => {
                held = stmt
                    .query_map([], |r| r.get::<_, i64>(0))
                    .map_err(q)?
                    .collect::<rusqlite::Result<HashSet<_>>>()
                    .map_err(q)?;
            }
            Err(e) => tracing::warn!(
                "director: no passages.director_hold ({e}); no passage is held \
                 back [SPEC-HOLD-010]"
            ),
        }
        // And the passages of a file this player last found damaged, missing
        // or unreadable `[GDE-INT-135]`: what cannot play as the catalogue
        // says is not chosen. A listener without `file_checks` withholds
        // nothing; any other failure is said.
        match conn.prepare(&format!(
            "SELECT p.passage_id FROM __LIB__.passages p JOIN __LIB__.files f ON f.file_id = p.file_id \
               JOIN file_checks c ON c.audio_md5 = f.audio_md5 \
              WHERE p.kind = 'radio' AND c.outcome IN {}",
            crate::db::UNPLAYABLE_SQL
        )) {
            Ok(mut stmt) => {
                let bad = stmt
                    .query_map([], |r| r.get::<_, i64>(0))
                    .map_err(q)?
                    .collect::<rusqlite::Result<Vec<_>>>()
                    .map_err(q)?;
                if !bad.is_empty() {
                    tracing::warn!("director: {} passage(s) withheld: their files failed this player's check \
                                    [GDE-INT-135]", bad.len());
                }
                held.extend(bad);
            }
            Err(e) => optional_read("the file checks", &e),
        }

        let mut last_played = HashMap::new();
        let mut artist_last_played: HashMap<String, i64> = HashMap::new();
        let mut passage_last_played: HashMap<i64, i64> = HashMap::new();
        let mut work_last_played: HashMap<String, i64> = HashMap::new();
        // **Two aggregates, not one scan.** SQLite reduces 37,763 play rows to
        // about 8,000 of each key before any cross into Rust; pulling every row
        // and folding them here instead cost a measured 18.3 s on the appliance
        // against 9.86 s for the query this replaced `[IMPL-SUI-075]`.
        //
        // **Both columns are nullable**, and the live listener database carries
        // 52 rows with a null `passage_id` -- `player_store.rs` declares it
        // `INTEGER` with no NOT NULL, so a play can be recorded against a
        // recording alone. Each `WHERE ... IS NOT NULL` is what lets a row feed
        // whichever tiers it can and be skipped by the rest.
        let bump = |m: &mut HashMap<String, i64>, k: &str, at: i64| {
            let e = m.entry(k.to_string()).or_insert(at);
            *e = (*e).max(at);
        };
        let mut stmt = conn
            .prepare(
                "SELECT passage_id, MAX(played_at) FROM listener_play_history WHERE passage_id IS NOT NULL GROUP BY passage_id",
            )
            .map_err(q)?;
        let by_passage = stmt
            .query_map([], |r| Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?)))
            .map_err(q)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(q)?;
        drop(stmt);
        for (passage_id, at) in by_passage {
            passage_last_played.insert(passage_id, at);
            // Through the passage, not the recording: a tags-only play may
            // carry no recording at all.
            if let Some(t) = tag_artist.get(&passage_id) {
                bump(&mut artist_last_played, t, at);
            }
        }

        let mut stmt = conn
            .prepare(
                "SELECT mbid, MAX(played_at) FROM listener_play_history WHERE mbid IS NOT NULL GROUP BY mbid",
            )
            .map_err(q)?;
        let by_recording = stmt
            .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
            .map_err(q)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(q)?;
        drop(stmt);
        // Artists and works still fold with `max`: many recordings share one.
        for (mbid, at) in by_recording {
            // A play stamps every credited artist: We Can Funk is a Prince
            // play and a George Clinton play, each then on its own rotation.
            for a in artist_of.get(&mbid).into_iter().flatten() {
                bump(&mut artist_last_played, a, at);
            }
            for w in works_of.get(&mbid).into_iter().flatten() {
                bump(&mut work_last_played, w, at);
            }
            last_played.insert(mbid, at);
        }

        let mut relations: HashMap<String, Vec<(String, f64)>> = HashMap::new();
        let mut stmt = conn
            .prepare("SELECT mbid, related_mbid, strength FROM recording_relations")
            .map_err(q)?;
        let rels = stmt
            .query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, f64>(2)?))
            })
            .map_err(q)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(q)?;
        drop(stmt);
        for (a, b, s) in rels {
            relations.entry(a).or_default().push((b, s));
        }

        let occasions = load_occasions(conn)?;
        let profanity = load_profanity(conn);
        let flavor = FlavorIndex::load(conn)?;
        let programme = Programme::load(conn)?;
        // A seed without flavor shapes nothing and is passed over `[SPEC-DIR-142]`;
        // said once here, per slot, so the gap is a line rather than a silence.
        for slot in programme.slots() {
            let missing: Vec<String> = programme
                .seeds_of(slot.id)
                .iter()
                .filter(|m| flavor.get(m).is_none())
                .map(|m| match rows.iter().find(|r| r.mbid.as_deref() == Some(m.as_str())) {
                    Some(r) => format!("{} ({m})", r.entry.title()),
                    None => m.clone(),
                })
                .collect();
            if !missing.is_empty() {
                tracing::warn!(
                    "director: slot \"{}\" has {} seed(s) without flavor, passed over: {} [SPEC-DIR-142]",
                    slot.name,
                    missing.len(),
                    missing.join(", ")
                );
            }
        }
        let (like, dislike) = load_taste(conn, &flavor);

        // Absent settings are the defaults, not an error: a library that has
        // never been tuned must still play [SPEC-DIR-158].
        //
        // `recording_time_scale` was `track_time_scale` until `[SPEC-VOC-010]`
        // renamed it; `tools/rename_recording_time_scale.py` migrates an
        // existing database in place, but reading the new name unconditionally
        // would reset every unmigrated installation's tuned value to the
        // default until someone thought to run it. Reading around the gap,
        // the same way `export_changes.py`'s `has_column` already does for a
        // `boundary_reviews` table too old to have a column it wants.
        let recording_scale_col = if conn
            .prepare("SELECT recording_time_scale FROM listener_settings")
            .is_ok()
        {
            "recording_time_scale"
        } else {
            "track_time_scale"
        };
        let scales = conn
            .query_row(
                &format!(
                    "SELECT artist_time_scale, {recording_scale_col} FROM listener_settings WHERE id = 1"
                ),
                [],
                |r| Ok((r.get::<_, f64>(0)?, r.get::<_, f64>(1)?)),
            )
            .map_err(|e| optional_read("the time scales", &e))
            .ok();
        // Rejections, for suppression only `[SPEC-PLAY-050]`. An absent table is
        // a library that predates the feature, not a fault: nothing recorded
        // means nothing suppressed. Any other failure is said (review O-04): the
        // music goes on, but "nothing suppressed" because the read failed is not
        // "nothing suppressed" because nothing was rejected.
        let rejected = |kind: &str| -> HashMap<String, i64> {
            conn.prepare(
                "SELECT mbid, MAX(rejected_at) FROM listener_rejections WHERE mbid IS NOT NULL AND kind = ?1 GROUP BY mbid",
            )
            .and_then(|mut q| {
                q.query_map([kind], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                    .and_then(|rows| rows.collect::<rusqlite::Result<HashMap<_, _>>>())
            })
            .map_err(|e| optional_read(&format!("the {kind} rejections"), &e))
            .unwrap_or_default()
        };
        let last_skipped = rejected("skip");
        let last_dequeued = rejected("dequeue");

        let policy = Policy {
            artist_scale: scales.map(|s| TimeScale::new(s.0)).unwrap_or_default(),
            recording_scale: scales.map(|s| TimeScale::new(s.1)).unwrap_or_default(),
            skip_suppress_s: crate::SKIP_SUPPRESS_H as f64 * 3600.0,
            dequeue_suppress_s: crate::DEQUEUE_SUPPRESS_H as f64 * 3600.0,
            ..Default::default()
        };

        let index = rows.iter().enumerate().map(|(i, r)| (r.entry.passage_id, i)).collect();
        // From the occasion values already loaded where Christmas is a
        // registered occasion, as it is on every node; read afresh only
        // where it is not `[SPEC-XMR-015]`.
        let christmas_values = match occasions.values_of("user.christmas", "christmasy") {
            Some(mut v) => {
                v.retain(|_, x| *x > 0.0);
                v
            }
            None => load_christmas(conn),
        };
        let christmas_rows: Vec<usize> = rows
            .iter()
            .enumerate()
            .filter(|(_, r)| r.mbid.as_deref().is_some_and(|m| christmas_values.contains_key(m)))
            .map(|(i, _)| i)
            .collect();
        let christmas = christmas_rows.iter().map(|&i| rows[i].entry.passage_id).collect();
        Ok(Self {
            rows,
            index,
            policy,
            last_skipped,
            last_dequeued,
            marked: HashMap::new(),
            open: HashMap::new(),
            next_token: 0,
            recording_tuning,
            artist_tuning,
            artist_of,
            tag_artist,
            held,
            last_played,
            artist_last_played,
            works_of,
            passage_last_played,
            work_last_played,
            relations,
            occasions,
            profanity,
            christmas_rows,
            christmas,
            christmas_on: true,
            flavor,
            programme,
            like,
            dislike,
        })
    }

    /// Change the suppression windows `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]`, as
    /// `(skip, dequeue)` in hours.
    ///
    /// Live rather than rebuild-only: they are listener settings, and a control
    /// that needs a restart to take effect is a control that lies.
    pub fn set_suppress_h(&mut self, (skip, dequeue): (u64, u64)) {
        self.policy.skip_suppress_s = skip as f64 * 3600.0;
        self.policy.dequeue_suppress_s = dequeue as f64 * 3600.0;
    }

    /// The windows currently in force, in hours.
    pub fn suppress_h(&self) -> (u64, u64) {
        (
            (self.policy.skip_suppress_s / 3600.0).round() as u64,
            (self.policy.dequeue_suppress_s / 3600.0).round() as u64,
        )
    }

    /// A short human reading of a recording's flavor `[SPEC-MPD-050]`, for
    /// publishing to clients that can show a string and nothing else.
    pub fn flavor_summary(&self, mbid: &str, max_terms: usize) -> Option<String> {
        let f = self.flavor.get(mbid)?;
        let s = f.summary(&self.flavor.schema, max_terms);
        (!s.is_empty()).then_some(s)
    }

    /// Change this player's profanity tolerance `[SPEC-PTL-010]`. Live, as
    /// the windows are.
    pub fn set_profanity_tolerance(&mut self, t: ProfanityTolerance) {
        self.policy.profanity = t;
    }

    pub fn profanity_tolerance(&self) -> ProfanityTolerance {
        self.policy.profanity
    }

    /// Turn this player's Christmas reserve on or off `[SPEC-XMR-080]`. Live,
    /// as the tolerance is.
    pub fn set_christmas_reserve(&mut self, on: bool) {
        self.christmas_on = on;
    }

    pub fn christmas_reserve(&self) -> bool {
        self.christmas_on
    }

    /// The Christmas ramp for a pick projected to start at `starts_at`, on
    /// the listener's clock `[SPEC-XMR-020]`, whether or not the reserve is on.
    pub fn christmas_ramp(&self, starts_at: i64) -> f64 {
        christmas::christmas_ramp(self.programme.local_secs(starts_at))
    }

    /// How many radio passages the reserve can draw on `[SPEC-XMR-015]`.
    pub fn christmas_count(&self) -> usize {
        self.christmas_rows.len()
    }

    /// Whether a radio passage counts as Christmas `[SPEC-XMR-015]`.
    pub fn is_christmas(&self, passage_id: i64) -> bool {
        self.christmas.contains(&passage_id)
    }

    pub fn policy(&self) -> Policy {
        self.policy
    }
    pub fn set_policy(&mut self, p: Policy) {
        self.policy = p;
    }
    /// A passage's length, as the Director holds it: for projecting when the
    /// passages queued after it start `[SPEC-DIR-186]`.
    pub fn passage_ms(&self, passage_id: i64) -> Option<u64> {
        self.row(passage_id).map(|r| r.entry.duration_ms())
    }
    pub fn len(&self) -> usize {
        self.rows.len()
    }
    pub fn is_empty(&self) -> bool {
        self.rows.is_empty()
    }
    pub fn occasion_count(&self) -> usize {
        self.occasions.curve_count()
    }
    pub fn programme(&self) -> &Programme {
        &self.programme
    }
    pub fn programme_mut(&mut self) -> &mut Programme {
        &mut self.programme
    }
    pub fn flavor(&self) -> &FlavorIndex {
        &self.flavor
    }

    /// The seeds shaping the pool for a pick that will start at `starts_at`:
    /// the seeds of the slot in force then `[SPEC-DIR-186]`, plus Like-Taste as
    /// an additional seed `[SPEC-DIR-150]`. A seed without flavor is passed
    /// over before the cap `[SPEC-DIR-142]`.
    fn seeds_at(&self, starts_at: i64) -> Vec<Seed<'_>> {
        let mut out: Vec<Seed<'_>> = Vec::new();
        if let Some(p) = self.programme.active_slot(starts_at) {
            let usable = |m: &str| self.flavor.get(m).is_some();
            for mbid in self.programme.seeds_for(p.id, &self.artist_of, &self.last_played, usable) {
                if let Some(f) = self.flavor.get(&mbid) {
                    out.push(Seed { flavor: f, weight: 1.0 });
                }
            }
        }
        if let Some(l) = &self.like {
            out.push(Seed { flavor: l, weight: LIKE_SEED_WEIGHT });
        }
        out
    }

    /// The artists a passage rotates by: every artist its recording credits,
    /// or failing any, its file's artist tag `[REQ-AND-315]`. Empty when it
    /// has neither.
    fn artist_keys(&self, mbid: Option<&str>, passage_id: i64) -> &[String] {
        match mbid.and_then(|m| self.artist_of.get(m)) {
            Some(credits) if !credits.is_empty() => credits,
            _ => self.tag_artist.get(&passage_id).map(std::slice::from_ref).unwrap_or(&[]),
        }
    }

    fn age(&self, map: &HashMap<String, i64>, key: &str, now: i64) -> Option<f64> {
        // A play stamped in the future -- clock skew, or a restored backup --
        // must read as "just played", not as a negative age that would sail
        // through every block.
        map.get(key).map(|at| (now - at).max(0) as f64)
    }

    /// Weigh every radio passage. `now` is unix seconds, passed in so a
    /// selection can be replayed against a frozen history `[REQ-PD-110]`.
    pub fn weigh_all(&self, now: i64) -> Vec<(&QueueEntry, Weighing)> {
        self.weigh_all_with(now, false)
    }

    /// As [`Director::weigh_all`], and with `relaxed`, as though nothing had
    /// played: no passage, recording, work, artist or related age, so neither
    /// rotation nor recovery applies. Everything else does -- holds, the
    /// length filters, rejection windows, the listener's restraint and the
    /// occasion -- which is the whole of what the fallback may set aside
    /// (the maintainer, 2026-10-08).
    fn weigh_all_with(&self, now: i64, relaxed: bool) -> Vec<(&QueueEntry, Weighing)> {
        self.weigh_rows(self.rows.iter(), now, if relaxed { Weigh::Relaxed } else { Weigh::Normal })
    }

    /// Weigh `rows`, which need not be every row: the Christmas reserve's
    /// fillers weigh only the Christmas passages `[SPEC-XMR-050]`.
    fn weigh_rows<'a>(
        &'a self,
        rows: impl Iterator<Item = &'a Row>,
        now: i64,
        mode: Weigh,
    ) -> Vec<(&'a QueueEntry, Weighing)> {
        let relaxed = mode == Weigh::Relaxed;
        let rotation = if mode == Weigh::RotationSetAside {
            Rotation::SetAside { artist_gap: christmas::ARTIST_GAP_SHARE }
        } else {
            Rotation::Applies
        };
        let mut related_buf: Vec<Related> = Vec::new();
        let mut artist_buf: Vec<ArtistCredit> = Vec::new();
        // The day is resolved once per pass, not per passage: every candidate is
        // weighed against the same "today", which is also what keeps a selection
        // replayable from a frozen `now`. The listener's day, not UTC's: a
        // season turns at local midnight, as a slot starts at local time
        // `[SPEC-DIR-132]`.
        let (_, month, day) = civil_from_unix(self.programme.local_secs(now));
        let today = ordinal(month, day);
        rows
            .map(|row| {
                if self.held.contains(&row.entry.passage_id) {
                    return (&row.entry, Weighing::excluded(Exclusion::Held));
                }
                let mbid = row.mbid.as_deref();
                let recording = mbid
                    .and_then(|m| self.recording_tuning.get(m))
                    .copied()
                    .unwrap_or_else(Tuning::recording_defaults);
                // Each credit on its own tuning and its own age. Relaxed, the
                // ages go and the tunings stay: the fallback sets rotation
                // aside, never the listener's restraint.
                artist_buf.clear();
                for a in self.artist_keys(mbid, row.entry.passage_id) {
                    artist_buf.push(ArtistCredit {
                        tuning: self.artist_tuning.get(a).copied().unwrap_or_else(Tuning::artist_defaults),
                        age_s: if relaxed { None } else { self.age(&self.artist_last_played, a, now) },
                    });
                }

                related_buf.clear();
                if let (Some(m), false) = (mbid, relaxed) {
                    if let Some(rel) = self.relations.get(m) {
                        for (other, strength) in rel {
                            related_buf.push(Related {
                                age_s: self.age(&self.last_played, other, now),
                                strength: *strength,
                            });
                        }
                    }
                }

                let c = Candidate {
                    length_s: row.entry.duration_ms() as f64 / 1000.0,
                    depth_s: row.entry.start_ms as f64 / 1000.0,
                    recording,
                    artists: &artist_buf,
                    passage_age_s: self
                        .passage_last_played
                        .get(&row.entry.passage_id)
                        .map(|at| (now - at).max(0) as f64),
                    recording_age_s: mbid.and_then(|m| self.age(&self.last_played, m, now)),
                    // The most recent across every work it performs
                    // `[GDE-WRK-038]`: a medley that played five minutes ago
                    // must not be softened by another of its works being cold.
                    work_age_s: mbid
                        .and_then(|m| self.works_of.get(m))
                        .into_iter()
                        .flatten()
                        .filter_map(|w| self.age(&self.work_last_played, w, now))
                        .min_by(f64::total_cmp),
                    skip_age_s: mbid.and_then(|m| self.age(&self.last_skipped, m, now)),
                    dequeue_age_s: mbid.and_then(|m| self.age(&self.last_dequeued, m, now)),
                    related: &related_buf,
                    occasion: self.occasions.multiplier(mbid, today),
                    profanity: mbid.and_then(|m| self.profanity.get(m)).copied().unwrap_or(0.0),
                };
                let c = if relaxed {
                    Candidate {
                        passage_age_s: None,
                        recording_age_s: None,
                        work_age_s: None,
                        ..c
                    }
                } else {
                    c
                };
                (&row.entry, weigh_under(&c, &self.policy, rotation))
            })
            .collect()
    }

    /// Seconds since anything this passage would rotate by last played or was
    /// marked as coming -- the passage, its recording, its works, its artist --
    /// or `None` if none ever has.
    fn idle_s(&self, entry: &QueueEntry, now: i64) -> Option<f64> {
        let mbid = entry.mbid.as_deref();
        [
            self.passage_last_played.get(&entry.passage_id).map(|at| (now - at).max(0) as f64),
            mbid.and_then(|m| self.age(&self.last_played, m, now)),
        ]
        .into_iter()
        .chain(
            self.artist_keys(mbid, entry.passage_id)
                .iter()
                .map(|a| self.age(&self.artist_last_played, a, now)),
        )
        .chain(
            mbid.and_then(|m| self.works_of.get(m))
                .into_iter()
                .flatten()
                .map(|w| self.age(&self.work_last_played, w, now)),
        )
        .flatten()
        .min_by(f64::total_cmp)
    }

    /// Mark a passage as just played, so the next pick sees it.
    ///
    /// Called when a passage is QUEUED, not when it finishes -- MuLibPlay's own
    /// note says the structures update "as each new track finishes playing (or
    /// is put in the play queue)". Without it, topping up five slots at once
    /// would happily queue five recordings by one artist, because every pick
    /// would weigh against the same stale history.
    pub fn note_queued(&mut self, passage_id: i64, at: i64) -> Option<QueuedNote> {
        // A passage the Director does not carry cannot be noted; one it carries
        // without an MBID still can, and must be. Returning `None` for the
        // unidentified case is what let such a passage repeat freely
        // `[GDE-WRK-055]`.
        let row = self.row(passage_id)?;
        let mbid = row.mbid.clone();
        let mut keys = vec![MarkKey::Passage(passage_id)];
        for a in self.artist_keys(mbid.as_deref(), passage_id) {
            keys.push(MarkKey::Artist(a.clone()));
        }
        if let Some(m) = &mbid {
            for w in self.works_of.get(m).into_iter().flatten() {
                keys.push(MarkKey::Work(w.clone()));
            }
            keys.push(MarkKey::Recording(m.clone()));
        }
        let token = self.next_token;
        self.next_token += 1;
        for key in &keys {
            // What the key held before any open mark is kept, so a mark can be
            // taken back exactly: guessing at rotation history is how a
            // recording that never played ends up suppressed.
            let before = self.stamp(key);
            self.open
                .entry(key.clone())
                .or_insert(OpenMarks { before, marks: Vec::new() })
                .marks
                .push((token, at));
            self.set_stamp(key, Some(before.map_or(at, |b| b.max(at))));
        }
        self.marked.insert(token, (at, keys));
        Some(QueuedNote { token, passage_id })
    }

    /// Take back a mark for a passage that never played `[REQ-PD-112]`.
    ///
    /// A passage can be chosen, marked, and then fail to open -- an unreadable
    /// file, a path that moved -- or be declined: skipped, or taken out of the
    /// queue. Either way it was not heard, and leaving the mark would suppress
    /// its recording, its works and its artist for a full rotation on the
    /// strength of a play that never happened `[SPEC-PLAY-050]`.
    ///
    /// Exact in any order: each key is recomputed from what it held before the
    /// first open mark and the marks still open on it.
    pub fn forget_queued(&mut self, note: QueuedNote) {
        self.resolve(note.token, false);
    }

    /// The passage played: its mark stays, as the play it now is
    /// `[REQ-PD-112]`.
    pub fn settle(&mut self, note: QueuedNote) {
        self.resolve(note.token, true);
    }

    /// The listener declined a recording just now `[SPEC-PLAY-050]`,
    /// `[SPEC-PLAY-055]`: its window starts at once, in this Director, not
    /// when the next one is built. Nothing else changes, since a rejection
    /// earns no play and no mark.
    pub fn note_rejection(&mut self, kind: crate::db::Rejection, mbid: &str, at: i64) {
        let map = match kind {
            crate::db::Rejection::Skip => &mut self.last_skipped,
            crate::db::Rejection::Dequeue => &mut self.last_dequeued,
        };
        let e = map.entry(mbid.to_string()).or_insert(at);
        *e = (*e).max(at);
    }

    /// How many marks are open: passages marked as coming and not yet judged.
    pub fn open_marks(&self) -> usize {
        self.marked.len()
    }

    fn resolve(&mut self, token: u64, keep: bool) {
        let Some((at, keys)) = self.marked.remove(&token) else { return };
        for key in keys {
            let Some(open) = self.open.get_mut(&key) else { continue };
            open.marks.retain(|(t, _)| *t != token);
            if keep {
                open.before = Some(open.before.map_or(at, |b| b.max(at)));
            }
            let value = open.marks.iter().map(|(_, a)| *a).chain(open.before).max();
            if open.marks.is_empty() {
                self.open.remove(&key);
            }
            self.set_stamp(&key, value);
        }
    }

    fn stamp(&self, key: &MarkKey) -> Option<i64> {
        match key {
            MarkKey::Passage(p) => self.passage_last_played.get(p).copied(),
            MarkKey::Recording(m) => self.last_played.get(m).copied(),
            MarkKey::Work(w) => self.work_last_played.get(w).copied(),
            MarkKey::Artist(a) => self.artist_last_played.get(a).copied(),
        }
    }

    /// `None` removes the entry: "nothing was there" is a value like any
    /// other, and a zero left behind would read as a play at the epoch.
    fn set_stamp(&mut self, key: &MarkKey, value: Option<i64>) {
        fn put<K: std::hash::Hash + Eq>(m: &mut HashMap<K, i64>, k: K, v: Option<i64>) {
            match v {
                Some(v) => {
                    m.insert(k, v);
                }
                None => {
                    m.remove(&k);
                }
            }
        }
        match key {
            MarkKey::Passage(p) => put(&mut self.passage_last_played, *p, value),
            MarkKey::Recording(m) => put(&mut self.last_played, m.clone(), value),
            MarkKey::Work(w) => put(&mut self.work_last_played, w.clone(), value),
            MarkKey::Artist(a) => put(&mut self.artist_last_played, a.clone(), value),
        }
    }

    /// One pick with nothing queued and nothing to follow, discarding the
    /// reasoning: [`Director::decide`] with no skip list and no queue tail,
    /// so the pick starts now.
    pub fn choose(&self, now: i64, rng: &mut Rng) -> Option<QueueEntry> {
        self.decide(now, rng, &[], None).map(|d| d.entry)
    }

    /// As [`Director::decide`], discarding the reasoning, with the last of
    /// `skip` taken as the queue tail. For tests and tools: the pick is taken
    /// to start now, which a caller with a real queue ahead must not assume.
    pub fn choose_excluding(&self, now: i64, rng: &mut Rng, skip: &[i64]) -> Option<QueueEntry> {
        self.decide(now, rng, skip, skip.last().copied()).map(|d| d.entry)
    }

    /// One pick through all four stages `[SPEC-DIR-110..165]`, with the
    /// record of why. `None` when nothing is eligible; the caller then asks
    /// [`Director::decide_relaxed`].
    ///
    /// `skip` is what is already queued, kept out of this pick.
    /// [`Director::note_queued`] already blocks anything identified by making
    /// it its own rotation block; this is the structural guarantee for the
    /// rest, so an unidentified passage cannot appear twice in one queue.
    ///
    /// `after` is the passage this one will follow — the queue tail. Flow
    /// ordering is measured from it `[SPEC-DIR-160]`. Passed explicitly rather
    /// than taken as the last of `skip`, because "do not pick these" and "this
    /// is what plays before" are different questions that happen to share a
    /// list today.
    ///
    /// **Every judgement is made at `starts_at`** `[SPEC-DIR-186]`: when the
    /// pick will begin -- now, plus what is left of the passage on air and
    /// every passage queued ahead of it, played through without a skip -- not
    /// when it is chosen. Rotation and recovery, the rejection windows, the
    /// season and the programme slot are all read then: a pick queued at 21:50
    /// to play at 22:05 is the 22:00 slot's, and an artist that will have
    /// rested its ten hours by 22:05 may play though it has not by 21:50.
    pub fn decide(
        &self,
        starts_at: i64,
        rng: &mut Rng,
        skip: &[i64],
        after: Option<i64>,
    ) -> Option<Decision> {
        let r = self.roulette(starts_at, rng, skip, after)?;
        let pool: Vec<(&QueueEntry, &Weighing, Option<f64>, f64)> =
            r.pool.iter().map(|(e, w, d, x)| (*e, w, *d, *x)).collect();
        self.draw(starts_at, rng, &pool, &r.places, r.tally, &r.shaping, &r.seeds, r.stages)
    }

    /// The list [`Director::decide`] draws on: Stages A to C and the
    /// Christmas reserve, everything short of the draw. Apart so the list
    /// itself can be looked at -- which passages, in which places, at which
    /// weights -- where a pick shows only its winner.
    fn roulette(&self, starts_at: i64, rng: &mut Rng, skip: &[i64], after: Option<i64>) -> Option<Roulette<'_>> {
        let weighed = self.weigh_all(starts_at);

        // Stage B shapes WHICH passages are in the running; it never touches a
        // weight [SPEC-DIR-100]. Survivors carry their Stage-A weights forward
        // unchanged, so frequency and character stay separable in the panel.
        let seeds = self.seeds_at(starts_at);
        let mut shaping = Shaping::default();
        let candidates: Vec<(i64, Option<&Flavor>)> = weighed
            .iter()
            .filter(|(e, w)| w.is_eligible() && !skip.contains(&e.passage_id))
            .map(|(e, _)| (e.passage_id, self.flavor_of(e.passage_id)))
            .collect();
        let shaped: std::collections::HashSet<i64> = shape(
            &self.flavor.schema,
            &candidates,
            &seeds,
            self.dislike.as_ref(),
            &mut shaping,
        )
        .into_iter()
        .collect();

        let live = |(e, w): &&(&QueueEntry, Weighing)| {
            w.is_eligible() && !skip.contains(&e.passage_id) && shaped.contains(&e.passage_id)
        };

        // --- Stage C: flow [SPEC-DIR-160] ---
        // Re-sort by distance to the passage already queued, so consecutive
        // passages blend. This is also what makes a hard programme switch
        // acceptable [SPEC-DIR-180]: continuity comes from here, not from
        // blending programmes.
        let anchor = after.and_then(|id| self.flavor_of(id));
        let mut ranked: Vec<(&QueueEntry, &Weighing, Option<f64>)> = weighed
            .iter()
            .filter(live)
            .map(|(e, w)| (*e, w, self.flow_distance(anchor, e.passage_id)))
            .collect();
        if ranked.is_empty() {
            return None;
        }
        // A flow order needs something to measure: a tail with flavor, and a
        // pool with at least one passage it can be measured against. A pool
        // of passages without flavor -- the last resort `[SPEC-DIR-147]` --
        // has no rank, however well the tail is known.
        let flowed = anchor.is_some() && ranked.iter().any(|x| x.2.is_some());
        if flowed {
            // One that cannot be measured from the tail sorts last: it can
            // still play, it simply cannot claim to follow well.
            ranked.sort_by(|a, b| nearest_first(a.2, b.2));
            // **And among those, no order at all.** Left as they sorted, they
            // kept the order the rows were read in, and the cut to `RAND_POOL`
            // below then let only the first of them into the draw, decayed by
            // row position -- the same passages every time.
            let first_unmeasured = ranked.iter().position(|x| x.2.is_none()).unwrap_or(ranked.len());
            rng.shuffle(&mut ranked[first_unmeasured..]);
        }

        // --- Stage D: rank decay, then weighted roulette [SPEC-DIR-165] ---
        // Decay only makes sense over a MEANINGFUL rank. With no queue tail
        // there is no flow order, so rank would be arbitrary and decay would
        // favour whatever the scan happened to visit first.
        let take = if flowed { RAND_POOL } else { ranked.len() };

        // --- the Christmas reserve [SPEC-XMR-040] ---
        // Some of the `take` places kept for Christmas, the last ones: the
        // roulette's lowest ranks, so the reserve starts slow and climbs as
        // the ramp reaches up the rank (the maintainer, 2026-11-09). With
        // nothing reserved the list is Stage C's first `take`, as it was.
        let ramp = self.christmas_ramp(starts_at);
        let due = if self.christmas_on { christmas::reserved(ramp, take) } else { 0 };
        // **None from the last resort** `[SPEC-XMR-045]`: a pool of passages
        // without flavor is a station with nothing to place Christmas by, and
        // a Christmas passage with flavor beside them would be drawn where
        // `[SPEC-DIR-147]` says none may be (the maintainer, 2026-11-13).
        let reserved = if shaping.last_resort { 0 } else { due };
        // **With no flow order the list has no rank**, so the places given up
        // to Christmas were the last rows read -- the same passages, the
        // newest imports, out of every such pick all season. Shuffled, the
        // places given up are any; the draw is the same, with no decay to
        // favour a position `[SPEC-XMR-030]`. Only when places are kept, so a
        // pick out of season draws exactly as it did.
        if reserved > 0 && !flowed {
            rng.shuffle(&mut ranked);
        }
        let is_christmas = |x: &(&QueueEntry, &Weighing, Option<f64>)| self.christmas.contains(&x.0.passage_id);
        // Fillers are weighed only when moving up cannot fill the places:
        // it costs a pass over every Christmas passage.
        let moving_up = ranked.iter().skip(take - reserved).filter(|x| is_christmas(x)).count();
        let filler_store = if moving_up < reserved {
            let listed: HashSet<i64> = ranked.iter().map(|x| x.0.passage_id).collect();
            self.christmas_fillers(starts_at, rng, skip, &listed, &seeds, anchor, reserved - moving_up)
        } else {
            Vec::new()
        };
        let fillers: Vec<(&QueueEntry, &Weighing, Option<f64>)> =
            filler_store.iter().map(|(e, w, d)| (*e, w, *d)).collect();
        let (listed, mut tally) = christmas::reserve(&ranked, take, reserved, is_christmas, &fillers);
        tally.enabled = self.christmas_on;
        tally.ramp = ramp;
        tally.last_resort = due > 0 && shaping.last_resort;
        let places: Vec<Place> = listed.iter().map(|x| x.1).collect();

        // **Every reserved place on one scale** `[SPEC-XMR-040]`. A passage
        // moved up is weighed as a filler is, with rotation set aside. Kept at
        // Stage C's weight, its recovery ran from its rotation's end while a
        // filler's ran from its last play, and one heard a day ago outweighed
        // one heard five days ago (found 2026-11-13). Never lighter for it:
        // setting rotation aside only lengthens a ramp. The places above the
        // cutoff keep Stage C's weights.
        let moved: Vec<&Row> =
            listed.iter().filter(|x| x.1 == Place::MovedUp).filter_map(|x| self.row(x.0 .0.passage_id)).collect();
        let reweighed: HashMap<i64, Weighing> = self
            .weigh_rows(moved.into_iter(), starts_at, Weigh::RotationSetAside)
            .into_iter()
            .filter(|(_, w)| w.is_eligible())
            .map(|(e, w)| (e.passage_id, w))
            .collect();

        let pool: Vec<(&QueueEntry, Weighing, Option<f64>, f64)> = listed
            .into_iter()
            .enumerate()
            .map(|(rank, ((e, w, d), place))| {
                let w = if place == Place::MovedUp { reweighed.get(&e.passage_id).unwrap_or(w) } else { w };
                let decayed =
                    if flowed { w.weight * RANK_DECAY.powi(rank as i32) } else { w.weight };
                (e, w.clone(), d, decayed)
            })
            .collect();

        let stages = if flowed {
            "frequency, shaping, flow, rank decay"
        } else if anchor.is_some() {
            "frequency, shaping; no flow -- nothing in the pool has flavor to follow by"
        } else {
            "frequency, shaping; no flow -- nothing with flavor queued to follow"
        };
        Some(Roulette { pool, places, tally, shaping, seeds, stages })
    }

    /// The Christmas reserve's fillers `[SPEC-XMR-050]`: at most `need`
    /// Christmas passages from outside Stage C's list, nearest the
    /// programme's seeds first, each weighed with rotation set aside -- its
    /// recovery measured from its last play, and an artist still kept a sixth
    /// of its rotation apart.
    ///
    /// Not one already listed or queued, and not one Stage A would refuse for
    /// anything but rotation: a hold, the length filters, the profanity
    /// tolerance, a rejection window, the weight floor.
    ///
    /// **Stage B's rules, by Stage B's code.** One too close to Dislike-Taste
    /// is left out exactly where Stage B leaves one out
    /// (`shape::too_disliked`, `[SPEC-DIR-222]`). One without flavor is never
    /// used: the reserve keeps places only in a pool with flavor
    /// `[SPEC-XMR-045]`, and one without is never drawn beside those
    /// `[SPEC-DIR-147]`. Until 2026-11-13 it was kept and sorted last, which
    /// with no seeds was no order at all.
    ///
    /// With no seeds to measure by, nearest the queue tail first, as Stage C
    /// ranks. What neither can tell apart comes in no fixed order. Each
    /// carries its flow distance from the queue tail, for the record.
    #[allow(clippy::too_many_arguments)]
    fn christmas_fillers(
        &self,
        starts_at: i64,
        rng: &mut Rng,
        skip: &[i64],
        listed: &HashSet<i64>,
        seeds: &[Seed<'_>],
        anchor: Option<&Flavor>,
        need: usize,
    ) -> Vec<(&QueueEntry, Weighing, Option<f64>)> {
        let schema = &self.flavor.schema;
        let rows = self
            .christmas_rows
            .iter()
            .map(|&i| &self.rows[i])
            .filter(|r| !listed.contains(&r.entry.passage_id) && !skip.contains(&r.entry.passage_id));
        // (passage, weighing, flow distance, the distance it is ordered by)
        let mut found: Vec<(&QueueEntry, Weighing, Option<f64>, Option<f64>)> = self
            .weigh_rows(rows, starts_at, Weigh::RotationSetAside)
            .into_iter()
            .filter(|(_, w)| w.is_eligible())
            .filter_map(|(e, w)| {
                let f = self.flavor_of(e.passage_id)?;
                if too_disliked(schema, f, seeds, self.dislike.as_ref()) {
                    return None;
                }
                let flow = self.flow_distance(anchor, e.passage_id);
                let by = if seeds.is_empty() { flow } else { super::shape::nearest_seed(schema, f, seeds) };
                Some((e, w, flow, by))
            })
            .collect();
        // Shuffled before the sort, which is stable: a tie comes in no fixed
        // order.
        rng.shuffle(&mut found);
        found.sort_by(|a, b| nearest_first(a.3, b.3));
        found.truncate(need);
        found.into_iter().map(|(e, w, flow, _)| (e, w, flow)).collect()
    }

    /// Choose when [`Director::decide`] cannot: every candidate is blocked
    /// `[REQ-PD-100]`, `[SPEC-DIR-230]`. A repeat is a better answer than
    /// silence, so **rotation and recovery are set aside, and nothing else**
    /// (the maintainer, 2026-10-08): a held passage, one under the weight floor
    /// for its occasion today -- children's, or out of season -- one inside a
    /// rejection window, or one the listener has turned all the way down is
    /// still never chosen. Already-queued passages are skipped, and the
    /// programme still shapes the pool.
    ///
    /// In rotation's place, the longest unplayed is the likeliest: each weight
    /// is scaled by how long since anything it rotates by last played, and
    /// drawn, so it is not the same passage every time.
    ///
    /// Every judgement at `starts_at`, as in [`Director::decide`]
    /// `[SPEC-DIR-186]`.
    pub fn decide_relaxed(&self, starts_at: i64, rng: &mut Rng, skip: &[i64]) -> Option<Decision> {
        let weighed = self.weigh_all_with(starts_at, true);
        let open: Vec<(&QueueEntry, &Weighing)> = weighed
            .iter()
            .filter(|(e, w)| w.is_eligible() && !skip.contains(&e.passage_id))
            .map(|(e, w)| (*e, w))
            .collect();
        if open.is_empty() {
            return None;
        }
        let seeds = self.seeds_at(starts_at);
        let mut shaping = Shaping::default();
        let candidates: Vec<(i64, Option<&Flavor>)> =
            open.iter().map(|(e, _)| (e.passage_id, self.flavor_of(e.passage_id))).collect();
        let shaped: HashSet<i64> = shape(
            &self.flavor.schema,
            &candidates,
            &seeds,
            self.dislike.as_ref(),
            &mut shaping,
        )
        .into_iter()
        .collect();
        // The weight the draw uses, carried on the weighing so the panel shows
        // the number that was actually drawn on.
        let idled: Vec<(&QueueEntry, Weighing)> = open
            .iter()
            .filter(|(e, _)| shaped.is_empty() || shaped.contains(&e.passage_id))
            .map(|(e, w)| {
                let idle = self.idle_s(e, starts_at).unwrap_or(NEVER_PLAYED_S).max(IDLE_FLOOR_S);
                (*e, Weighing { weight: w.weight * idle, ..(*w).clone() })
            })
            .collect();
        let pool: Vec<(&QueueEntry, &Weighing, Option<f64>, f64)> =
            idled.iter().map(|(e, w)| (*e, w, None, w.weight)).collect();
        // No reserve here: this is the answer to a pool rotation emptied, and
        // it sets rotation aside for every passage alike `[SPEC-XMR-090]`.
        let tally = Reserve { enabled: self.christmas_on, ramp: self.christmas_ramp(starts_at), ..Reserve::default() };
        self.draw(
            starts_at,
            rng,
            &pool,
            &[],
            tally,
            &shaping,
            &seeds,
            "fallback: every candidate was blocked by rotation; rotation and recovery \
             set aside, the longest unplayed favoured",
        )
    }

    /// The roulette over a ranked pool, and the record of why `[SPEC-DIR-165]`,
    /// `[REQ-VIS-100]`. `pool` is (entry, weighing, flow distance, the weight
    /// actually drawn on); `starts_at` names the slot the pick is for
    /// `[SPEC-DIR-186]`. `places` says where each came from in the Christmas
    /// reserve, `Flow` where it is shorter than `pool` `[SPEC-XMR-070]`.
    #[allow(clippy::too_many_arguments)]
    fn draw(
        &self,
        starts_at: i64,
        rng: &mut Rng,
        pool: &[(&QueueEntry, &Weighing, Option<f64>, f64)],
        places: &[Place],
        christmas: Reserve,
        shaping: &Shaping,
        seeds: &[Seed<'_>],
        stages: &'static str,
    ) -> Option<Decision> {
        let total: f64 = pool.iter().map(|x| x.3).sum();
        // Negated `>` so a NaN total is refused rather than divided by; see
        // the same shape in `frequency::weigh`.
        #[allow(clippy::neg_cmp_op_on_partial_ord)]
        if !(total > 0.0) {
            return None;
        }
        let roulette_target = rng.unit() * total;
        let mut target = roulette_target;
        let mut hit: Option<usize> = None;
        for (i, x) in pool.iter().enumerate() {
            target -= x.3;
            if target <= 0.0 {
                hit = Some(i);
                break;
            }
        }
        // Floating-point drift can leave a hair of `target` after the loop.
        // Taking the last candidate is correct, not a fallback.
        let idx = hit.unwrap_or(pool.len() - 1);
        let (entry, w, flow_distance, decayed) = pool[idx];

        // The heaviest losers AFTER decay -- "why not something else?" is about
        // what nearly won, which is the decayed weight, not the raw one.
        let mut rest: Vec<&(&QueueEntry, &Weighing, Option<f64>, f64)> =
            pool.iter().filter(|x| x.0.passage_id != entry.passage_id).collect();
        rest.sort_by(|a, b| b.3.partial_cmp(&a.3).unwrap_or(std::cmp::Ordering::Equal));

        let pool_size = pool.len();
        let on_air = self.programme.active_slot(starts_at);
        let slot = on_air.map(|p| p.name.clone());
        let slot_id = on_air.map(|p| p.id);
        Some(Decision {
            entry: (*entry).clone(),
            why: Explanation {
                passage_id: entry.passage_id,
                title: entry.title(),
                weight: w.weight,
                decayed_weight: decayed,
                rank: idx,
                flow_distance,
                roulette_target,
                artist_weight: w.artist_weight,
                artist_blocked: w.artist_blocked,
                recording_restraint: w.recording_restraint,
                recording_ramp: w.recording_ramp,
                related_damping: w.related_damping,
                length_bonus: w.length_bonus,
                occasion: w.occasion,
                profanity: w.profanity,
                pool_size,
                pool_weight: total,
                slot: slot.clone(),
                starts_at,
                slot_id,
                program: slot,
                shaping: shaping.clone(),
                seed_distances: self.seed_distances(entry.passage_id, seeds),
                // The weight it was drawn on, over the total of the same
                // weights: decayed against decayed. The undecayed weight over
                // a decayed total overstated a rank-99 pick some 57-fold
                // `[SPEC-DIR-191]`.
                share_pct: if total > 0.0 { decayed / total * 100.0 } else { 0.0 },
                runners_up: rest
                    .iter()
                    .take(RUNNERS_UP)
                    .map(|x| RunnerUp {
                        passage_id: x.0.passage_id,
                        title: x.0.title(),
                        mbid: x.0.mbid.clone(),
                        artist: x.0.artist(),
                        artist_mbid: x.0.naming.artist_mbid.clone(),
                        weight: x.3,
                    })
                    .collect(),
                stages,
                place: places.get(idx).copied().unwrap_or_default(),
                christmas,
            },
        })
    }

    /// The row a passage is, through the index rather than a scan.
    fn row(&self, passage_id: i64) -> Option<&Row> {
        self.index.get(&passage_id).map(|&i| &self.rows[i])
    }

    /// The flavor of whatever recording a passage carries.
    fn flavor_of(&self, passage_id: i64) -> Option<&Flavor> {
        self.flavor.get(self.row(passage_id)?.mbid.as_deref()?)
    }

    /// How far a passage is from `anchor`, the queue tail's flavor: Stage C's
    /// measure `[SPEC-DIR-160]`, for its order and for the Christmas reserve's
    /// fillers alike. `None` where either has no flavor.
    fn flow_distance(&self, anchor: Option<&Flavor>, passage_id: i64) -> Option<f64> {
        super::flavor::distance(&self.flavor.schema, self.flavor_of(passage_id)?, anchor?)
    }

    /// Distance from the chosen passage to each seed, for the panel
    /// `[SPEC-DIR-190]`.
    fn seed_distances(&self, passage_id: i64, seeds: &[Seed<'_>]) -> Vec<f64> {
        let Some(f) = self.flavor_of(passage_id) else { return Vec::new() };
        seeds
            .iter()
            .filter_map(|s| super::flavor::distance(&self.flavor.schema, f, s.flavor))
            .collect()
    }

    /// Why the pool is the size it is — for the panel, and for diagnosing a
    /// station that has gone quiet.
    pub fn census(&self, now: i64) -> Census {
        let mut c = Census::default();
        for (_, w) in self.weigh_all(now) {
            match w.excluded {
                None => {
                    c.eligible += 1;
                    c.total_weight += w.weight;
                }
                Some(Exclusion::ArtistRotationBlock) => c.artist_blocked += 1,
                Some(Exclusion::RecordingRotationBlock) => c.recording_blocked += 1,
                Some(Exclusion::RelatedRotationBlock) => c.related_blocked += 1,
                Some(Exclusion::WorkRotationBlock) => c.work_blocked += 1,
                Some(Exclusion::BelowMinWeight) => c.below_min_weight += 1,
                // Counted apart from `filtered`, which means "the wrong shape".
                // A suppressed passage is the right shape and is coming back
                // `[SPEC-PLAY-050]`; folding the two together would report a
                // library as permanently smaller than it is.
                Some(Exclusion::SkipSuppressed) | Some(Exclusion::DequeueSuppressed) => {
                    c.suppressed += 1
                }
                // Apart from `filtered` too: the right shape, held by a mark.
                Some(Exclusion::Held) => c.held += 1,
                // A setting's doing, not the passage's shape `[SPEC-PTL-070]`.
                Some(Exclusion::Profanity) => c.profanity += 1,
                Some(_) => c.filtered += 1,
            }
        }
        c
    }
}

#[derive(Debug, Default, Clone, Copy, PartialEq)]
pub struct Census {
    pub eligible: usize,
    /// Held out because the listener recently declined them `[SPEC-PLAY-050]`.
    /// Temporary by construction, unlike `filtered`.
    pub suppressed: usize,
    pub artist_blocked: usize,
    pub recording_blocked: usize,
    pub related_blocked: usize,
    /// Held out because another passage of the same song played `[GDE-WRK-035]`.
    /// Counted apart from `recording_blocked` so the panel can say which of the
    /// three identity tiers fired, which is the whole point of keeping them
    /// distinct `[SPEC-DIR-190]`.
    pub work_blocked: usize,
    pub below_min_weight: usize,
    pub filtered: usize,
    /// Held back by a mark on the passage `[SPEC-HOLD-010]`: lasting, unlike
    /// `suppressed`, and the right shape, unlike `filtered`.
    pub held: usize,
    /// At or above this player's profanity tolerance `[SPEC-PTL-070]`.
    pub profanity: usize,
    pub total_weight: f64,
}

impl Census {
    /// Every passage weighed, whatever became of it.
    ///
    /// `census` puts each passage in exactly one bucket, so this is the radio
    /// passage count. It lives here rather than at the call site because it
    /// was a hand-written sum in `publish_pool` and drifted twice: `suppressed`
    /// was never in it, so a skipped passage made the browser's pool read
    /// short, and `work_blocked` had to be remembered when it was added
    /// `[GDE-WRK-240]`. A new exclusion now reaches the total by existing.
    pub fn total(&self) -> usize {
        let Census {
            eligible,
            suppressed,
            artist_blocked,
            recording_blocked,
            related_blocked,
            work_blocked,
            below_min_weight,
            filtered,
            held,
            profanity,
            total_weight: _,
        } = self;
        eligible
            + suppressed
            + artist_blocked
            + recording_blocked
            + related_blocked
            + work_blocked
            + below_min_weight
            + filtered
            + held
            + profanity
    }
}

/// Curves and the per-subject values they apply to `[SPEC-DIR-130]`.
///
/// Missing tables are not an error: a library with no occasions defined simply
/// has no seasons, and every multiplier is 1.0.
pub(crate) fn load_occasions(conn: &QualifyingConn) -> Result<Occasions, DbError> {
    let mut modes: HashMap<(String, String), Interp> = HashMap::new();
    let mut pts: HashMap<(String, String), Vec<(u16, f64)>> = HashMap::new();

    let Ok(mut stmt) = conn.prepare("SELECT characteristic, class, interp FROM listener_occasions")
    else {
        return Ok(Occasions::default());
    };
    if let Ok(rows) = stmt.query_map([], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?))
    }) {
        for row in rows.flatten() {
            modes.insert((row.0, row.1), Interp::parse(&row.2));
        }
    }
    drop(stmt);
    if modes.is_empty() {
        return Ok(Occasions::default());
    }

    if let Ok(mut stmt) = conn.prepare(
        "SELECT characteristic, class, month, day, multiplier FROM listener_occasion_points",
    ) {
        if let Ok(rows) = stmt.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, u32>(2)?,
                r.get::<_, u32>(3)?,
                r.get::<_, f64>(4)?,
            ))
        }) {
            for (ch, cl, m, d, mult) in rows.flatten() {
                // Nothing below ×0.000001 `[SPEC-DIR-132]`: `Curve::new`
                // raises such a point to the floor and drops one that is not
                // a number. Said here, where the occasion has a name.
                #[allow(clippy::neg_cmp_op_on_partial_ord)]
                if !(mult >= crate::director::occasion::MIN_CURVE_POINT) {
                    tracing::warn!(
                        "director: occasion {ch}/{cl} on {m:02}-{d:02} has multiplier {mult}, \
                         below the floor of {} or not a number; raised or dropped [SPEC-DIR-132]",
                        crate::director::occasion::MIN_CURVE_POINT
                    );
                }
                pts.entry((ch, cl)).or_default().push((ordinal(m, d), mult));
            }
        }
    }

    let mut curves = HashMap::new();
    for (key, points) in pts {
        let interp = modes.get(&key).copied().unwrap_or(Interp::Step);
        // A registered occasion with no control points has no curve and is
        // dropped: silently neutral is how a mis-entered occasion would go
        // unnoticed for a year.
        if let Some(c) = Curve::new(interp, points) {
            curves.insert(key, c);
        }
    }

    // Only characteristics that ARE occasions. `flavor` also holds 71
    // dimensions of musical flavor, and none of those are seasonal.
    let mut values: crate::director::occasion::SubjectValues = HashMap::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT subject_id, characteristic, class, value FROM __LIB__.flavor \
         WHERE subject_kind = 'recording'",
    ) {
        if let Ok(rows) = stmt.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, f64>(3)?,
            ))
        }) {
            for (subject, ch, cl, v) in rows.flatten() {
                let key = (ch, cl);
                if curves.contains_key(&key) {
                    values.entry(subject).or_default().push((key, v));
                }
            }
        }
    }

    // The listener's own hand-set values, laid over the inherited ones
    // `[SPEC-PREF-085]`. Last, and replacing rather than adding to what
    // `flavor` said: a person who marked a recording "not a Christmas song"
    // after the migration decided it was one has to win, or the panel would
    // accept an edit the engine then ignored. A row here for a
    // characteristic no curve covers is skipped by the same
    // `curves.contains_key` test the pass above uses -- an occasion can be
    // retired from the registry without its tagging having to be deleted
    // first.
    //
    // A missing table is not an error, for the same reason a missing
    // `listener_occasions` is not: an installation that has never set a
    // special simply has none to lay over.
    if let Ok(mut stmt) = conn.prepare(
        "SELECT subject_id, characteristic, class, value FROM listener_characteristics WHERE subject_kind = 'recording'",
    ) {
        if let Ok(rows) = stmt.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, f64>(3)?,
            ))
        }) {
            for (subject, ch, cl, v) in rows.flatten() {
                let key = (ch, cl);
                if !curves.contains_key(&key) {
                    continue;
                }
                let entry = values.entry(subject).or_default();
                match entry.iter_mut().find(|(k, _)| *k == key) {
                    Some(slot) => slot.1 = v,
                    None => entry.push((key, v)),
                }
            }
        }
    }
    Ok(Occasions::new(curves, values))
}

/// Each recording's profanity value, as the specials panel shows it
/// `[SPEC-PTL-060]`: the listener's own over the inherited, and only those
/// above zero.
///
/// Read whether or not `user.profanity` is a registered occasion -- a node
/// without the registry still holds the values -- and never an error: a
/// library from before either table simply has none, and the tolerance then
/// has nothing to act on.
pub(crate) fn load_profanity(conn: &QualifyingConn) -> HashMap<String, f64> {
    load_positive_values(conn, "user.profanity", "profane", "the profanity values")
}

/// Each recording's value in one class of one characteristic, as the
/// specials panel shows it: the listener's own over the inherited, and only
/// those above zero. The one reading behind [`load_profanity`] and
/// [`load_christmas`], which differ only in what they ask for.
///
/// Never an error: a library from before either table simply has none, and a
/// failed read is said as `what` and read as nothing.
fn load_positive_values(
    conn: &QualifyingConn,
    characteristic: &str,
    class: &str,
    what: &str,
) -> HashMap<String, f64> {
    let mut out = HashMap::new();
    // The listener's second, so its own value replaces the inherited one.
    for table in ["__LIB__.flavor", "listener_characteristics"] {
        let sql = format!(
            "SELECT subject_id, value FROM {table} \
              WHERE subject_kind = 'recording' AND characteristic = ?1 AND class = ?2"
        );
        let read = conn.prepare(&sql).and_then(|mut stmt| {
            stmt.query_map([characteristic, class], |r| Ok((r.get::<_, String>(0)?, r.get::<_, f64>(1)?)))
                .and_then(|rows| rows.collect::<rusqlite::Result<Vec<_>>>())
        });
        match read {
            Ok(rows) => {
                for (mbid, v) in rows {
                    out.insert(mbid, v);
                }
            }
            Err(e) => optional_read(what, &e),
        }
    }
    out.retain(|_, v| *v > 0.0);
    out
}

/// Where this player keeps its profanity tolerance `[SPEC-PTL-030]`.
pub const PROFANITY_TOLERANCE_KEY: &str = "profanity_tolerance";

/// This player's profanity tolerance, from `player_settings`
/// `[SPEC-PTL-030]`. Absent or unreadable is the default, 1.0: a player that
/// never moved the control considers no profanity, which is what it did
/// before there was one.
pub fn load_profanity_tolerance(conn: &QualifyingConn) -> ProfanityTolerance {
    conn.query_row(
        "SELECT value FROM player_settings WHERE key = ?1",
        [PROFANITY_TOLERANCE_KEY],
        |r| r.get::<_, String>(0),
    )
    .ok()
    .and_then(|v| v.trim().parse::<f64>().ok())
    .map(ProfanityTolerance::new)
    .unwrap_or_default()
}

/// Each recording's Christmas value, as the specials panel shows it
/// `[SPEC-XMR-015]`: the listener's own over the inherited, and only those
/// above zero -- "a defined Christmas value greater than 0.0".
///
/// The positive class only: `christmasy`. Every inherited recording also
/// carries `not_christmasy`, at 0.0 where `christmasy` is 1.0
/// `[SPEC-DIR-134]`, and reading both as one value is how a count of bose's
/// Christmas plays came out at 1 rather than 41 (2026-11-09). Read whether or
/// not the occasion is registered, as profanity is.
pub(crate) fn load_christmas(conn: &QualifyingConn) -> HashMap<String, f64> {
    load_positive_values(conn, "user.christmas", "christmasy", "the Christmas values")
}

/// Where this player keeps its Christmas reserve switch `[SPEC-XMR-080]`.
pub const CHRISTMAS_RESERVE_KEY: &str = "christmas_reserve";

/// This player's Christmas reserve switch, from `player_settings`
/// `[SPEC-XMR-080]`. On unless stored as off: absent, or a value that does not
/// read, is the default the maintainer chose.
pub fn load_christmas_reserve(conn: &QualifyingConn) -> bool {
    conn.query_row(
        "SELECT value FROM player_settings WHERE key = ?1",
        [CHRISTMAS_RESERVE_KEY],
        |r| r.get::<_, String>(0),
    )
    .ok()
    .and_then(|v| parse_switch(&v))
    .unwrap_or(true)
}

/// `on`/`off`, and the other spellings a person or a URL might use.
pub fn parse_switch(v: &str) -> Option<bool> {
    match v.trim().to_ascii_lowercase().as_str() {
        "on" | "true" | "1" | "yes" => Some(true),
        "off" | "false" | "0" | "no" => Some(false),
        _ => None,
    }
}

/// Like- and Dislike-Taste centroids `[SPEC-DIR-150]`.
///
/// Weighted centroids of the flavor of liked and disliked recordings. A
/// negative weight is a dislike; its magnitude is how strongly. Returns
/// `(like, dislike)`, either of which may be absent.
///
/// **Unexercised:** `listener_likes` is empty in the migrated library, so this
/// path has unit tests and no field data behind it.
fn load_taste(conn: &QualifyingConn, flavor: &FlavorIndex) -> (Option<Flavor>, Option<Flavor>) {
    let mut likes: Vec<(&Flavor, f64)> = Vec::new();
    let mut dislikes: Vec<(&Flavor, f64)> = Vec::new();
    let Ok(mut stmt) = conn.prepare("SELECT mbid, weight FROM listener_likes") else {
        return (None, None);
    };
    let rows: Vec<(String, f64)> = match stmt.query_map([], |r| {
        Ok((r.get::<_, String>(0)?, r.get::<_, f64>(1)?))
    }) {
        Ok(it) => it.flatten().collect(),
        Err(_) => return (None, None),
    };
    for (mbid, w) in &rows {
        let Some(f) = flavor.get(mbid) else { continue };
        if *w >= 0.0 {
            likes.push((f, *w));
        } else {
            dislikes.push((f, -*w));
        }
    }
    (centroid(&flavor.schema, &likes), centroid(&flavor.schema, &dislikes))
}

#[cfg(test)]
mod tests {
    use super::*;
    // Only the fixtures open a bare connection; the module itself works
    // through `QualifyingConn`. Imported here rather than at file scope so a
    // non-test build does not carry an unused import.
    use rusqlite::Connection;

    const NOW: i64 = 1_000_000_000;
    const DAY: i64 = 86_400;

    /// The slice of SPEC008 selection touches. Written out rather than loaded
    /// from schema.sql so these tests pin the column names the queries need.
    fn fixture() -> QualifyingConn {
        let c = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT NOT NULL,
                                 duration_ms INTEGER, audio_md5 TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL,
                 kind TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
                 lead_in_ms INTEGER, lead_out_ms INTEGER, gain_db REAL);
             CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL DEFAULT 1.0);
             CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT);
             CREATE TABLE recording_relations (mbid TEXT, related_mbid TEXT, strength REAL);
             CREATE TABLE works (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE recording_works (mbid TEXT, work_mbid TEXT);
             CREATE TABLE listener_preferences (subject_kind TEXT, subject_id TEXT,
                 rotation REAL, recovery REAL, restraint REAL);
             CREATE TABLE listener_play_history (play_id INTEGER PRIMARY KEY,
                 played_at INTEGER, passage_id INTEGER, mbid TEXT);
             CREATE TABLE listener_settings (id INTEGER PRIMARY KEY,
                 artist_time_scale REAL, recording_time_scale REAL, updated_at TEXT);
             CREATE TABLE listener_occasions (characteristic TEXT, class TEXT, interp TEXT);
             CREATE TABLE listener_occasion_points (characteristic TEXT, class TEXT,
                 month INTEGER, day INTEGER, multiplier REAL);
             CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT,
                 class TEXT, value REAL, source TEXT, accuracy REAL);
             INSERT INTO files (file_id, path, duration_ms) VALUES (1, '/m/a.mp3', 600000);
             -- three 180 s radio passages, one album passage that must never appear
             INSERT INTO passages VALUES (1,1,'radio',0,180000,0,0,0.0);
             INSERT INTO passages VALUES (2,1,'radio',0,180000,0,0,0.0);
             INSERT INTO passages VALUES (3,1,'radio',0,180000,0,0,0.0);
             INSERT INTO passages VALUES (4,1,'album',0,180000,0,0,0.0);
             INSERT INTO passage_recordings VALUES (1,'rec-a',1.0),(2,'rec-b',1.0),(3,'rec-c',1.0);
             INSERT INTO recording_artists VALUES ('rec-a','art-1'),('rec-b','art-2'),
                                                  ('rec-c','art-3');
             -- Added after the fact, exactly like the real migration
             -- (`tools/add_fade_columns.py`) adds them to a live database
             -- `[SPEC-SUI-226]` -- so every existing bare `INSERT INTO
             -- passages VALUES (...)` above keeps working unmodified,
             -- backfilled with the same default a real ALTER TABLE gives
             -- every existing row.
             ALTER TABLE passages ADD COLUMN fade_in_ms INTEGER NOT NULL DEFAULT 20;
             ALTER TABLE passages ADD COLUMN fade_out_ms INTEGER NOT NULL DEFAULT 20;
             ALTER TABLE passages ADD COLUMN fade_in_curve TEXT NOT NULL DEFAULT 'exponential';
             ALTER TABLE passages ADD COLUMN fade_out_curve TEXT NOT NULL DEFAULT 'exponential';",
        )
        .unwrap();
        c
    }

    /// A passage that never opened must leave rotation history exactly as it
    /// found it `[REQ-PD-112]`. `max` cannot be inverted from its result, so
    /// the note carries what was there before.
    #[test]
    fn forgetting_a_queued_passage_restores_what_was_there() {
        let mut d = Director::load(&fixture()).unwrap();
        let before = d.last_played.clone();
        let before_artists = d.artist_last_played.clone();
        let note = d.note_queued(1, 5_000).expect("passage 1 is in the pool");
        assert_ne!(d.last_played, before, "noting must change something");
        d.forget_queued(note);
        assert_eq!(d.last_played, before, "recording history restored");
        assert_eq!(d.artist_last_played, before_artists, "artist history restored");
    }

    /// The common case: nothing was recorded before, so forgetting must remove
    /// the entry rather than leave a zero behind, which would read as "played
    /// at the epoch" and suppress nothing.
    #[test]
    fn forgetting_removes_an_entry_that_was_not_there() {
        let mut d = Director::load(&fixture()).unwrap();
        d.last_played.clear();
        d.artist_last_played.clear();
        let note = d.note_queued(1, 5_000).expect("passage 1 is in the pool");
        assert!(!d.last_played.is_empty());
        d.forget_queued(note);
        assert!(d.last_played.is_empty(), "no entry should remain");
        assert!(d.artist_last_played.is_empty());
    }

    /// Two marks on one recording, and the OLDER one forgotten first: the
    /// newer must survive. Undoing by restoring a saved value -- how this
    /// worked until 2026-10-08 -- erased it (review O-05), and with skips and
    /// a person's picks now forgotten as they happen, out of order is common.
    #[test]
    fn forgetting_in_any_order_is_exact() {
        let mut d = Director::load(&fixture()).unwrap();
        d.last_played.clear();
        d.artist_last_played.clear();
        let first = d.note_queued(1, 5_000).unwrap();
        let second = d.note_queued(1, 6_000).unwrap();
        assert_eq!(d.open_marks(), 2);
        d.forget_queued(first);
        assert_eq!(d.last_played.get("rec-a"), Some(&6_000), "the newer mark stands");
        assert_eq!(d.artist_last_played.get("art-1"), Some(&6_000));
        d.forget_queued(second);
        assert!(d.last_played.is_empty() && d.artist_last_played.is_empty());
        assert!(!d.passage_last_played.contains_key(&1));
        assert_eq!(d.open_marks(), 0);
    }

    /// A settled mark is a play: forgetting a later one goes back to it, not
    /// to before it.
    #[test]
    fn a_settled_mark_outlives_a_later_one_forgotten() {
        let mut d = Director::load(&fixture()).unwrap();
        d.last_played.clear();
        let played = d.note_queued(1, 5_000).unwrap();
        let skipped = d.note_queued(1, 6_000).unwrap();
        d.settle(played);
        d.forget_queued(skipped);
        assert_eq!(d.last_played.get("rec-a"), Some(&5_000));
        assert_eq!(d.open_marks(), 0);
    }

    /// A rejection holds the recording out of THIS Director at once
    /// `[SPEC-PLAY-050]`, `[SPEC-PLAY-055]` -- before 2026-10-08 only a
    /// rebuild read it -- and marks nothing else: rec-a's artist stays free.
    #[test]
    fn a_rejection_suppresses_at_once_and_marks_nothing() {
        let mut d = Director::load(&fixture()).unwrap();
        let excluded = |d: &Director, id: i64| {
            d.weigh_all(NOW).into_iter().find(|(e, _)| e.passage_id == id).unwrap().1.excluded
        };
        assert_eq!(excluded(&d, 1), None);
        d.note_rejection(crate::db::Rejection::Skip, "rec-a", NOW);
        assert_eq!(excluded(&d, 1), Some(Exclusion::SkipSuppressed));
        assert!(!d.artist_last_played.contains_key("art-1"), "a skip earns no artist mark");
        d.note_rejection(crate::db::Rejection::Dequeue, "rec-b", NOW);
        assert_eq!(excluded(&d, 2), Some(Exclusion::DequeueSuppressed));
    }

    /// A passage marked `director_hold` -- a damaged rip, or a person's choice
    /// -- is never chosen, and is counted as held, not lost `[SPEC-HOLD-010]`.
    /// An empty mark holds nothing. The fixture without the column is every
    /// other test here: a catalogue that predates it holds nothing.
    #[test]
    fn a_held_passage_is_never_chosen() {
        let c = fixture();
        c.execute_batch(
            "ALTER TABLE passages ADD COLUMN director_hold TEXT;
             UPDATE passages SET director_hold = 'damaged rip: track 2' WHERE passage_id = 2;
             UPDATE passages SET director_hold = '' WHERE passage_id = 3;",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        let cen = d.census(NOW);
        assert_eq!((cen.eligible, cen.held), (2, 1), "passage 2 held, the empty mark on 3 holds nothing");
        assert_eq!(cen.total(), 3, "a held passage is still counted in the pool's total");
        let mut rng = Rng::seeded(7);
        for _ in 0..200 {
            assert_ne!(d.choose(NOW, &mut rng).unwrap().passage_id, 2, "a held passage is never chosen");
        }
    }

    /// `[GDE-INT-135]` A passage whose file this player found damaged,
    /// missing or unreadable is held like a marked one; one re-tagged plays.
    #[test]
    fn a_passage_whose_file_failed_its_check_is_never_chosen() {
        let c = fixture();
        c.execute_batch(&format!(
            "UPDATE files SET audio_md5 = 'whole' WHERE file_id = 1;
             INSERT INTO files (file_id, path, duration_ms, audio_md5) VALUES (2, '/m/b.mp3', 600000, 'rot'),
                                                                          (3, '/m/c.mp3', 600000, 'tags');
             UPDATE passages SET file_id = 2 WHERE passage_id = 2;
             UPDATE passages SET file_id = 3 WHERE passage_id = 3;
             {}
             INSERT INTO file_checks (audio_md5, checked_at, outcome) VALUES ('rot', 't', 'damaged'),
                                                                            ('tags', 't', 'retagged'),
                                                                            ('whole', 't', 'ok');",
            crate::db::FILE_CHECKS_TABLE
        ))
        .unwrap();
        let d = Director::load(&c).unwrap();
        let cen = d.census(NOW);
        assert_eq!((cen.eligible, cen.held), (2, 1), "the damaged file's passage held; the re-tagged one plays");
        let mut rng = Rng::seeded(7);
        for _ in 0..200 {
            assert_ne!(d.choose(NOW, &mut rng).unwrap().passage_id, 2, "never chosen");
        }
    }

    #[test]
    fn loads_only_radio_passages() {
        let d = Director::load(&fixture()).unwrap();
        assert_eq!(d.len(), 3, "the album passage must not be selectable [REQ-PD-120]");
    }

    /// With no history everything is eligible and equally weighted, so the
    /// station is uniform random rather than silently biased [SPEC-DIR-158].
    #[test]
    fn a_fresh_library_is_all_eligible() {
        let d = Director::load(&fixture()).unwrap();
        let c = d.census(NOW);
        assert_eq!(c.eligible, 3);
        assert!((c.total_weight - 3.0).abs() < 1e-9, "weights {}", c.total_weight);
    }

    #[test]
    fn a_recent_play_blocks_that_recording_and_its_artist() {
        let c = fixture();
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 1, 'rec-a')", [NOW - 60])
            .unwrap();
        let d = Director::load(&c).unwrap();
        let cen = d.census(NOW);
        assert_eq!(cen.eligible, 2, "the played recording drops out");
        assert_eq!(cen.artist_blocked + cen.recording_blocked, 1);
    }

    /// The Christmas reserve end to end `[SPEC-XMR-040]`, `[SPEC-XMR-050]`:
    /// rec-c is a Christmas recording played a day ago, inside its 4.2-day
    /// rotation. Out of season, or with the reserve off, it never plays; on
    /// 24 December it is a filler, weighed from its last play, and it plays
    /// at about that weight's share.
    #[test]
    fn the_christmas_reserve_fills_with_rotation_set_aside() {
        // 2026-12-24T12:00, read as local time: this fixture stores no offset.
        const DEC_24_NOON: i64 = 1_796_083_200 + 23 * 86_400 + 12 * 3600;
        const NOV_9_NOON: i64 = DEC_24_NOON - 45 * 86_400;
        let c = fixture();
        // All three carry flavor, so Stage B, which prefers a passage with
        // flavor to one without `[SPEC-DIR-147]`, keeps both of the others.
        // rec-b's Christmas value is defined and zero: not a Christmas song.
        c.execute_batch(
            "INSERT INTO flavor VALUES ('recording','rec-c','user.christmas','christmasy',1.0,'t',NULL);
             INSERT INTO flavor VALUES ('recording','rec-c','user.christmas','not_christmasy',0.0,'t',NULL);
             INSERT INTO flavor VALUES ('recording','rec-b','user.christmas','christmasy',0.0,'t',NULL);
             INSERT INTO flavor VALUES ('recording','rec-a','user.christmas','not_christmasy',1.0,'t',NULL);",
        )
        .unwrap();
        for at in [DEC_24_NOON, NOV_9_NOON] {
            c.execute("INSERT INTO listener_play_history (played_at, passage_id, mbid) VALUES (?1, 3, 'rec-c')", [at - 86_400])
                .unwrap();
        }
        let mut d = Director::load(&c).unwrap();
        assert_eq!(d.christmas_count(), 1, "rec-c, and not rec-b's negative class");
        assert!(d.christmas_reserve(), "on unless turned off");

        let picks = |d: &Director, at: i64, skip: &[i64]| -> Vec<Decision> {
            (0..400).filter_map(|s| d.decide(at, &mut Rng::seeded(s), skip, None)).collect()
        };
        let threes = |ds: &[Decision]| ds.iter().filter(|x| x.entry.passage_id == 3).count();

        let season = picks(&d, DEC_24_NOON, &[]);
        let n = threes(&season);
        // Weighed from its last play: a day of 16.6 days' recovery, ×0.06
        // against two passages at ×1, one of which it displaced.
        assert!((5..60).contains(&n), "rec-c played {n} of 400");
        let why = &season.iter().find(|x| x.entry.passage_id == 3).unwrap().why;
        assert_eq!(why.place, Place::Filler);
        assert!((why.recording_ramp - 86_400.0 / super::super::frequency::seconds(2.6)).abs() < 1e-9);
        // 558 of the rise's 576 hours.
        assert!((why.christmas.ramp - 558.0 / 576.0).abs() < 1e-12, "ramp {}", why.christmas.ramp);
        // No flow order here, so the list is Stage C's two: both reserved,
        // one filled by rec-c and the other backfilled.
        assert_eq!(
            (why.christmas.reserved, why.christmas.moved_up, why.christmas.fillers, why.christmas.backfilled),
            (2, 0, 1, 1)
        );
        assert_eq!(why.pool_size, 2);

        assert_eq!(threes(&picks(&d, DEC_24_NOON, &[3])), 0, "a queued passage is never a filler");
        assert_eq!(threes(&picks(&d, NOV_9_NOON, &[])), 0, "out of season, rotation holds it");
        let off = picks(&d, NOV_9_NOON, &[]);
        assert!(off.iter().all(|x| x.why.christmas.reserved == 0 && x.why.place == Place::Flow));

        d.set_christmas_reserve(false);
        let off = picks(&d, DEC_24_NOON, &[]);
        assert_eq!(threes(&off), 0, "the reserve turned off");
        assert!(off.iter().all(|x| !x.why.christmas.enabled && x.why.christmas.reserved == 0));
    }

    /// With Christmas a registered occasion, the set comes from the values
    /// the occasions already loaded, and agrees with the direct read
    /// `[SPEC-XMR-015]`.
    #[test]
    fn the_christmas_set_comes_from_the_registered_occasion() {
        let c = fixture();
        c.execute_batch(
            "INSERT INTO flavor VALUES ('recording','rec-c','user.christmas','christmasy',1.0,'t',NULL);
             INSERT INTO flavor VALUES ('recording','rec-c','user.christmas','not_christmasy',0.0,'t',NULL);
             INSERT INTO flavor VALUES ('recording','rec-a','user.christmas','christmasy',0.0,'t',NULL);
             INSERT INTO flavor VALUES ('recording','rec-a','user.christmas','not_christmasy',1.0,'t',NULL);",
        )
        .unwrap();
        let unregistered = Director::load(&c).unwrap();
        c.execute_batch(
            "INSERT INTO listener_occasions VALUES ('user.christmas','christmasy','linear');
             INSERT INTO listener_occasion_points VALUES ('user.christmas','christmasy',12,25,10.0);",
        )
        .unwrap();
        let registered = Director::load(&c).unwrap();
        for d in [&unregistered, &registered] {
            assert_eq!(d.christmas_count(), 1);
            assert!(d.is_christmas(3) && !d.is_christmas(1) && !d.is_christmas(2));
        }
        assert_eq!(registered.occasion_count(), 1, "read through the occasion");
    }

    /// 2026-12-19T06:00, read as local time: three quarters up the ramp, so
    /// 75 of a flow order's 100 places are reserved `[SPEC-XMR-030]`.
    const DEC_19: i64 = 1_796_083_200 + 18 * DAY + 6 * 3600;

    /// The Christmas reserve at full size `[SPEC-XMR-040]`: `n` radio
    /// passages spread along one characteristic, so passage 1 is nearest 2
    /// and furthest from `n`, each its own recording `r{i}` and artist `a{i}`.
    /// Those in `xmas` are Christmas. No programme, so no seeds.
    fn reserve_library(n: i64, xmas: &[i64]) -> QualifyingConn {
        let c = fixture();
        c.execute_batch("DELETE FROM passages; DELETE FROM passage_recordings; DELETE FROM recording_artists;")
            .unwrap();
        for i in 1..=n {
            let r = format!("r{i}");
            c.execute(
                "INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms, lead_out_ms, gain_db) \
                 VALUES (?1, 1, 'radio', 0, 180000, 0, 0, 0.0)",
                [i],
            )
            .unwrap();
            c.execute("INSERT INTO passage_recordings VALUES (?1, ?2, 1.0)", rusqlite::params![i, r]).unwrap();
            c.execute("INSERT INTO recording_artists VALUES (?1, ?2)", rusqlite::params![r, format!("a{i}")])
                .unwrap();
            let v = i as f64 / n as f64;
            for (class, val) in [("hi", v), ("lo", 1.0 - v)] {
                c.execute(
                    "INSERT INTO flavor VALUES ('recording', ?1, 'x', ?2, ?3, 'test', NULL)",
                    rusqlite::params![r, class, val],
                )
                .unwrap();
            }
            if xmas.contains(&i) {
                c.execute(
                    "INSERT INTO flavor VALUES ('recording', ?1, 'user.christmas', 'christmasy', 1.0, 'test', NULL)",
                    [&r],
                )
                .unwrap();
            }
        }
        c
    }

    /// Passage `p` of [`reserve_library`] heard at `at`.
    fn played(c: &QualifyingConn, p: i64, at: i64) {
        c.execute(
            "INSERT INTO listener_play_history (played_at, passage_id, mbid) VALUES (?1, ?2, ?3)",
            rusqlite::params![at, p, format!("r{p}")],
        )
        .unwrap();
    }

    /// A Christmas value that is the listener's own, on a recording with no
    /// flavor in the catalogue: a Christmas passage without flavor.
    fn christmas_by_the_listener(c: &QualifyingConn, recording: &str) {
        c.execute_batch(
            "CREATE TABLE IF NOT EXISTS listener_characteristics (subject_kind TEXT, subject_id TEXT,
                 characteristic TEXT, class TEXT, value REAL, updated_at TEXT);",
        )
        .unwrap();
        c.execute("DELETE FROM flavor WHERE subject_id = ?1", [recording]).unwrap();
        c.execute(
            "INSERT INTO listener_characteristics VALUES ('recording', ?1, 'user.christmas', 'christmasy', 1.0, 'now')",
            [recording],
        )
        .unwrap();
    }

    /// Where passage `id` stands in a roulette list: rank, place, weighing,
    /// and the weight it is drawn at.
    fn standing<'a>(r: &'a Roulette<'_>, id: i64) -> Option<(usize, Place, &'a Weighing, f64)> {
        r.pool
            .iter()
            .zip(&r.places)
            .enumerate()
            .find(|(_, ((e, ..), _))| e.passage_id == id)
            .map(|(rank, ((_, w, _, x), p))| (rank, *p, w, *x))
    }

    /// Every reserved place on one scale `[SPEC-XMR-040]`, in a full flow
    /// order. Christmas passage 150, heard five days ago, is out of rotation,
    /// ranked far below the cutoff, and moved up; 160, heard a day ago, is in
    /// rotation and a filler. Both are weighed from their last play, so the
    /// one heard longer ago weighs more. Until 2026-11-13 the one moved up
    /// kept Stage C's ramp from its rotation's end, ×0.05 against the
    /// filler's ×0.06: the reserve favoured what had played most recently.
    #[test]
    fn the_reserved_places_are_weighed_on_one_scale() {
        use super::super::frequency::{seconds, DEF_RECOVERY_REC};
        let c = reserve_library(200, &[150, 160]);
        played(&c, 150, DEC_19 - 5 * DAY);
        played(&c, 160, DEC_19 - DAY);
        let d = Director::load(&c).unwrap();
        let r = d.roulette(DEC_19, &mut Rng::seeded(1), &[1], Some(1)).unwrap();
        let rec = seconds(DEF_RECOVERY_REC);

        let (rank_x, place_x, x, _) = standing(&r, 150).expect("150 is listed");
        let (rank_y, place_y, y, _) = standing(&r, 160).expect("160 is listed");
        assert_eq!((place_x, place_y), (Place::MovedUp, Place::Filler));
        assert!((x.recording_ramp - 5.0 * DAY as f64 / rec).abs() < 1e-9, "moved up: {}", x.recording_ramp);
        assert!((y.recording_ramp - DAY as f64 / rec).abs() < 1e-9, "filler: {}", y.recording_ramp);
        assert!(x.weight > y.weight, "heard five days ago {} under heard yesterday {}", x.weight, y.weight);
        assert!(rank_x < rank_y, "moved up first, then the fillers");
        // Stage C's own weighing of 150 is lighter: that is what was replaced.
        let own = d.weigh_all(DEC_19).into_iter().find(|(e, _)| e.passage_id == 150).unwrap().1;
        assert!(own.is_eligible() && own.weight < x.weight, "Stage C {} against {}", own.weight, x.weight);
    }

    /// The list in full `[SPEC-XMR-040]`: 100 places, Stage C's first 25 as
    /// ranked, then the 75 reserved -- moved up, fillers, backfill, in that
    /// order -- each drawn at weight × 0.96^rank, so the reserved places are
    /// the roulette's lowest ranks. Of 21 Christmas passages, the ten never
    /// heard are ranked below the cutoff and move up; the eleven heard
    /// yesterday are fillers; the 54 places left are backfilled.
    #[test]
    fn the_reserved_places_are_the_lists_last_and_decay_by_rank() {
        let xmas: Vec<i64> = (100..=200).step_by(5).collect();
        let c = reserve_library(200, &xmas);
        for &i in xmas.iter().filter(|i| *i % 10 == 0) {
            played(&c, i, DEC_19 - DAY);
        }
        let d = Director::load(&c).unwrap();
        let r = d.roulette(DEC_19, &mut Rng::seeded(2), &[1], Some(1)).unwrap();
        assert_eq!(r.pool.len(), 100);
        let t = r.tally;
        assert_eq!((t.reserved, t.moved_up, t.fillers, t.backfilled), (75, 10, 11, 54));
        assert!(!t.last_resort);
        assert!(r.places[..25].iter().all(|p| *p == Place::Flow));
        let order = |p: &Place| match p {
            Place::Flow => 0,
            Place::MovedUp => 1,
            Place::Filler => 2,
            Place::Backfill => 3,
        };
        assert!(r.places[25..].windows(2).all(|w| order(&w[0]) <= order(&w[1])), "{:?}", r.places);
        assert!(r.places[25..].iter().all(|p| *p != Place::Flow));
        for (rank, (_, w, _, x)) in r.pool.iter().enumerate() {
            assert!((x - w.weight * RANK_DECAY.powi(rank as i32)).abs() < 1e-12, "rank {rank}");
        }
    }

    /// With no flow order, the places given up are any `[SPEC-XMR-030]`.
    /// Until 2026-11-13 they were the last rows read, so the same passages --
    /// the newest imports -- were shut out of every such pick all season. 30
    /// Christmas passages heard yesterday are fillers for 90 of the 120
    /// places, and 30 of the 120 others give theirs up: a different 30 each
    /// time.
    #[test]
    fn without_a_flow_order_the_places_given_up_are_any() {
        let xmas: Vec<i64> = (121..=150).collect();
        let c = reserve_library(150, &xmas);
        for &i in &xmas {
            played(&c, i, DEC_19 - DAY);
        }
        let d = Director::load(&c).unwrap();
        let mut ever: HashSet<i64> = HashSet::new();
        for seed in 0..60 {
            let r = d.roulette(DEC_19, &mut Rng::seeded(seed), &[], None).unwrap();
            assert_eq!((r.tally.reserved, r.tally.fillers), (90, 30), "seed {seed}");
            let others: Vec<i64> = r.pool.iter().map(|x| x.0.passage_id).filter(|i| *i <= 120).collect();
            assert_eq!((r.pool.len(), others.len()), (120, 90), "seed {seed}");
            ever.extend(others);
        }
        assert_eq!(ever.len(), 120, "every one of them is in some pick's list");
        // Out of season nothing is kept, and nothing reordered.
        let nov = d.roulette(DEC_19 - 30 * DAY, &mut Rng::seeded(0), &[], None).unwrap();
        assert_eq!(nov.tally.reserved, 0);
        assert!(nov.places.iter().all(|p| *p == Place::Flow));
    }

    /// A Christmas passage without flavor is never a filler `[SPEC-XMR-050]`:
    /// the pool has flavor, and one without is never drawn beside those
    /// `[SPEC-DIR-147]`. Passage 160 is Christmas by the listener's own value
    /// alone. Until 2026-11-13 it was a filler, sorted last -- and with no
    /// seeds, as here, not even last.
    #[test]
    fn a_christmas_passage_without_flavor_is_never_a_filler() {
        let c = reserve_library(200, &[150]);
        christmas_by_the_listener(&c, "r160");
        played(&c, 150, DEC_19 - DAY);
        played(&c, 160, DEC_19 - DAY);
        let d = Director::load(&c).unwrap();
        assert!(d.is_christmas(160));
        for (seed, after) in [(1, Some(1)), (2, None)] {
            let r = d.roulette(DEC_19, &mut Rng::seeded(seed), &[1], after).unwrap();
            assert_eq!(standing(&r, 150).map(|s| s.1), Some(Place::Filler), "with flavor, after {after:?}");
            assert!(standing(&r, 160).is_none(), "without, after {after:?}");
        }
    }

    /// A filler keeps a sixth of its artist's rotation apart
    /// `[SPEC-XMR-050]`: Christmas passage 160, never heard, shares an artist
    /// with passage 1, the queue tail. Stage A holds it out by that artist's
    /// ten hours, so it can only be a filler. Heard four minutes ago, the
    /// artist holds it out there too; two hours ago, past the gap of
    /// 1 h 40 min, it does not. Until 2026-11-13 it could follow at once: a
    /// recording fully recovered weighs ×0.0067 by its artist four minutes
    /// on, over the floor.
    #[test]
    fn a_filler_does_not_follow_its_own_artist() {
        for (ago, kept) in [(240, false), (2 * 3600, true)] {
            let c = reserve_library(200, &[160]);
            c.execute("UPDATE recording_artists SET artist_mbid = 'a1' WHERE mbid = 'r160'", []).unwrap();
            played(&c, 1, DEC_19 - ago);
            let d = Director::load(&c).unwrap();
            let r = d.roulette(DEC_19, &mut Rng::seeded(1), &[1], Some(1)).unwrap();
            assert_eq!(standing(&r, 160).is_some(), kept, "the artist heard {ago} s ago");
        }
    }

    /// No reserve from the last resort `[SPEC-XMR-045]`: nothing in the pool
    /// has flavor, so no places are kept, and the record says why. Out of
    /// season nothing was due, so there is nothing to explain.
    #[test]
    fn a_last_resort_pool_keeps_no_christmas_places() {
        let c = reserve_library(40, &[]);
        c.execute_batch("DELETE FROM flavor;").unwrap();
        christmas_by_the_listener(&c, "r40");
        played(&c, 40, DEC_19 - DAY);
        let d = Director::load(&c).unwrap();
        assert!(d.is_christmas(40));
        let r = d.roulette(DEC_19, &mut Rng::seeded(1), &[], None).unwrap();
        assert!(r.shaping.last_resort);
        assert_eq!(r.tally.reserved, 0);
        assert!(r.tally.last_resort && r.tally.enabled && r.tally.ramp > 0.7);
        assert!(r.places.iter().all(|p| *p == Place::Flow));
        assert!(standing(&r, 40).is_none(), "in rotation, and no filler to set it aside");
        let nov = d.roulette(DEC_19 - 30 * DAY, &mut Rng::seeded(1), &[], None).unwrap();
        assert!(nov.shaping.last_resort && !nov.tally.last_resort);
    }

    #[test]
    fn the_christmas_switch_reads_on_unless_stored_off() {
        let c = fixture();
        c.execute_batch("CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);")
            .unwrap();
        assert!(load_christmas_reserve(&c), "absent is on");
        c.execute("INSERT INTO player_settings VALUES ('christmas_reserve', 'off', 'now')", []).unwrap();
        assert!(!load_christmas_reserve(&c));
        c.execute("UPDATE player_settings SET value = 'ON' WHERE key = 'christmas_reserve'", []).unwrap();
        assert!(load_christmas_reserve(&c));
        c.execute("UPDATE player_settings SET value = 'sometimes' WHERE key = 'christmas_reserve'", []).unwrap();
        assert!(load_christmas_reserve(&c), "unreadable is the default, on");
        assert!(load_christmas_reserve(&fixture()), "no table at all is on");
    }

    /// lempi02w, 2026-10-16, in miniature: art-1 is Prince on a 26.5-hour
    /// rotation, art-2 a co-artist on the default 10. rec-a credits both;
    /// rec-c is Prince alone, rec-b the co-artist alone. art-2 is the higher
    /// MBID on purpose -- the one a map keeping a single credit per recording
    /// would have kept.
    fn with_prince(c: &QualifyingConn) {
        c.execute_batch(
            "INSERT INTO recording_artists VALUES ('rec-a','art-2');
             UPDATE recording_artists SET artist_mbid = 'art-1' WHERE mbid = 'rec-c';
             INSERT INTO listener_preferences VALUES ('artist','art-1',1.4232,NULL,NULL);",
        )
        .unwrap();
    }

    fn excluded(d: &Director, passage_id: i64) -> Option<Exclusion> {
        d.weigh_all(NOW).into_iter().find(|(e, _)| e.passage_id == passage_id).unwrap().1.excluded
    }

    /// Prince alone 21 hours ago holds Prince + co-artist: any credit in
    /// rotation blocks.
    #[test]
    fn a_play_by_one_credited_artist_holds_a_recording_crediting_several() {
        let c = fixture();
        with_prince(&c);
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 3, 'rec-c')", [NOW - 21 * 3600])
            .unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(excluded(&d, 1), Some(Exclusion::ArtistRotationBlock), "rec-a credits Prince");
        assert_eq!(excluded(&d, 2), None, "rec-b does not");
    }

    /// Prince + co-artist 12 hours ago stamps both, each on its own clock: the
    /// co-artist's own recording is free after his 10 hours, Prince's is not.
    #[test]
    fn a_play_crediting_several_artists_starts_each_ones_own_rotation() {
        let c = fixture();
        with_prince(&c);
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 1, 'rec-a')", [NOW - 12 * 3600])
            .unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(excluded(&d, 3), Some(Exclusion::ArtistRotationBlock), "Prince: 26.5 h");
        assert_eq!(excluded(&d, 2), None, "the co-artist: past 10 h");
    }

    /// Queueing one marks every credit at once, so a top-up of several slots
    /// cannot follow Prince + co-artist with Prince.
    #[test]
    fn queueing_a_recording_marks_every_credited_artist() {
        let c = fixture();
        with_prince(&c);
        let mut d = Director::load(&c).unwrap();
        d.note_queued(1, NOW).unwrap();
        assert_eq!(excluded(&d, 3), Some(Exclusion::ArtistRotationBlock));
        assert_eq!(excluded(&d, 2), Some(Exclusion::ArtistRotationBlock));
    }

    /// Three tags-only passages beside the fixture's credited ones: 5 and 6
    /// by one artist, tagged two ways; 7 by another; 1 (rec-a, credited
    /// art-1) also carries that tag on its file. `[REQ-AND-315]`
    fn with_tags(c: &QualifyingConn) {
        c.execute_batch(
            "CREATE TABLE file_tags (file_id INTEGER PRIMARY KEY, artist TEXT);
             INSERT INTO files (file_id, path, duration_ms) VALUES (2, '/m/b.mp3', 600000), (3, '/m/c.mp3', 600000),
                                      (4, '/m/d.mp3', 600000);
             INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, lead_in_ms,
                                   lead_out_ms, gain_db)
                  VALUES (5,2,'radio',0,180000,0,0,0.0), (6,3,'radio',0,180000,0,0,0.0),
                         (7,4,'radio',0,180000,0,0,0.0);
             INSERT INTO file_tags VALUES (1, 'The Beatles'), (2, 'The Beatles'),
                                          (3, '  the   BEATLES '), (4, 'Someone Else');",
        )
        .unwrap();
    }

    #[test]
    fn a_tag_is_normalised_into_a_key_no_mbid_can_equal() {
        assert_eq!(tag_artist_key("  The   Beatles "), "tag:the beatles");
        assert_eq!(tag_artist_key("the beatles"), tag_artist_key("THE BEATLES"));
        assert!(tag_artist_key("x").starts_with("tag:"));
    }

    /// `[REQ-AND-315]`: a play of one tags-only passage holds another by the
    /// same tagged artist, however the tag is spelled, and not a third by
    /// someone else.
    #[test]
    fn tags_only_passages_rotate_by_their_artist_tag() {
        let c = fixture();
        with_tags(&c);
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 5, NULL)", [NOW - 60])
            .unwrap();
        let d = Director::load(&c).unwrap();
        let w = |id: i64| d.weigh_all(NOW).into_iter()
            .find(|(e, _)| e.passage_id == id).map(|(_, w)| w).unwrap();
        assert!(w(6).artist_blocked, "6 shares 5's artist tag, spelled differently");
        assert!(w(6).excluded.is_some());
        assert!(!w(7).artist_blocked && w(7).excluded.is_none(), "7 is someone else");
    }

    /// The key keeps tags and identities apart: a recent tags-only play does
    /// not hold rec-a, although rec-a's file carries the same tag, because
    /// rec-a rotates by its credited artist.
    #[test]
    fn a_tag_never_blocks_an_identified_artist() {
        let c = fixture();
        with_tags(&c);
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 5, NULL)", [NOW - 60])
            .unwrap();
        let d = Director::load(&c).unwrap();
        let passage_1 = d.weigh_all(NOW).into_iter()
            .find(|(e, _)| e.passage_id == 1).map(|(_, w)| w).unwrap();
        assert!(!passage_1.artist_blocked, "a tag must not block a credited artist");
    }

    /// Queueing counts as playing for rotation, for tag keys as for MBIDs: a
    /// fill of several slots must not queue two passages by one tagged artist.
    #[test]
    fn queueing_a_tags_only_passage_holds_its_artist_at_once() {
        let c = fixture();
        with_tags(&c);
        let mut d = Director::load(&c).unwrap();
        d.note_queued(5, NOW).expect("a carried passage can be noted");
        assert_eq!(d.artist_last_played.get("tag:the beatles"), Some(&NOW));
        let six = d.weigh_all(NOW).into_iter()
            .find(|(e, _)| e.passage_id == 6).map(|(_, w)| w).unwrap();
        assert!(six.artist_blocked, "6 is held the moment 5 is queued");
    }

    /// The repair, end to end: a play of rec-b blocks rec-a through their
    /// relation, even though rec-a itself has never played.
    #[test]
    fn a_relation_blocks_across_recordings() {
        let c = fixture();
        c.execute("INSERT INTO recording_relations VALUES ('rec-a','rec-b',1.0)", [])
            .unwrap();
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 2, 'rec-b')", [NOW - 60])
            .unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.census(NOW).related_blocked, 1, "rec-a must be blocked by rec-b");
    }

    /// The incident's own shape, end to end, and the cover decision with it
    /// `[GDE-WRK-050]`: rec-a and rec-b are different recordings by *different
    /// artists* sharing one work. A play of rec-b must hold rec-a, which
    /// neither the recording tier nor the artist tier can see.
    #[test]
    fn a_shared_work_blocks_across_recordings_and_artists() {
        let c = fixture();
        c.execute("INSERT INTO recording_works VALUES ('rec-a','work-1'),('rec-b','work-1')", [])
            .unwrap();
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 2, 'rec-b')", [NOW - 60])
            .unwrap();
        let d = Director::load(&c).unwrap();
        let cen = d.census(NOW);
        assert_eq!(cen.work_blocked, 1, "rec-a must be held by the work it shares with rec-b");
        assert_eq!(cen.eligible, 1, "only rec-c is left");
    }

    /// `[GDE-WRK-240]`: the pool total the browser sees must account for every
    /// passage, or a suppressed one makes the library look smaller than it is.
    /// Pinned against the passage count rather than a literal so a new
    /// exclusion cannot quietly fall out of it.
    #[test]
    fn the_census_accounts_for_every_passage() {
        let c = fixture();
        c.execute_batch(
            "CREATE TABLE listener_rejections (mbid TEXT, rejected_at INTEGER, kind TEXT);",
        )
        .unwrap();
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 1, 'rec-a')", [NOW - 60])
            .unwrap();
        c.execute("INSERT INTO listener_rejections VALUES ('rec-b', ?1, 'skip')", [NOW - 60])
            .unwrap();
        let mut d = Director::load(&c).unwrap();
        d.set_suppress_h((24, 6)); // a skip window, or nothing is suppressed
        let cen = d.census(NOW);
        assert!(cen.suppressed > 0, "the fixture must actually exercise suppression");
        assert_eq!(cen.total(), d.len(), "every radio passage lands in exactly one bucket");
    }

    /// **Both history columns are nullable**, and the live listener database
    /// carries 52 rows of 37,763 with a null `passage_id`. Reading it as a
    /// plain `i64` made `Director::load` fail outright against real data while
    /// every test stayed green, because every other fixture row here has one.
    /// Each tier must still stamp from whichever column the row does carry.
    #[test]
    fn a_play_row_with_a_null_passage_or_mbid_still_loads_and_still_blocks() {
        let c = fixture();
        // No passage_id: the recording tier is all this row can feed.
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, NULL, 'rec-a')", [NOW - 60])
            .unwrap();
        // No mbid: the passage tier is all this row can feed.
        c.execute("INSERT INTO listener_play_history VALUES (2, ?1, 2, NULL)", [NOW - 60])
            .unwrap();
        // Neither: it must be skipped rather than panicking or stamping nothing.
        c.execute("INSERT INTO listener_play_history VALUES (3, ?1, NULL, NULL)", [NOW - 60])
            .unwrap();
        let d = Director::load(&c).expect("null columns must not fail the load");
        let cen = d.census(NOW);
        assert_eq!(cen.eligible, 1, "rec-a by its recording, passage 2 by itself");
        // rec-a's row also stamps its artist, and the artist pass runs first,
        // so that passage reports the artist block; passage 2's row carries no
        // mbid at all, so only the passage tier can be what holds it.
        assert_eq!(cen.artist_blocked, 1, "the null-passage row still fed the recording/artist tiers");
        assert_eq!(cen.recording_blocked, 1, "the null-mbid row still fed the passage tier");
    }

    /// A catalogue built before `[GDE-WRK-035]` has no `recording_works`. It
    /// must still load, with the work tier simply inactive -- the behaviour
    /// that shipped for years.
    #[test]
    fn a_catalogue_without_recording_works_still_loads() {
        let c = fixture();
        c.execute("DROP TABLE recording_works", []).unwrap();
        let d = Director::load(&c).expect("an older catalogue must still load");
        assert_eq!(d.census(NOW).eligible, 3);
    }

    /// `[GDE-WRK-055]`: noting must work for a passage with no recording MBID.
    /// It returned `None` before, recording nothing, so such a passage carried
    /// no history at all and could repeat freely.
    #[test]
    fn an_unidentified_passage_is_still_noted() {
        let c = fixture();
        c.execute("DELETE FROM passage_recordings WHERE passage_id = 3", []).unwrap();
        let mut d = Director::load(&c).unwrap();
        let note = d.note_queued(3, NOW).expect("an unidentified passage must still be noted");
        assert_eq!(d.census(NOW).eligible, 2, "passage 3 is now held by its own play");
        d.forget_queued(note);
        assert_eq!(d.census(NOW).eligible, 3, "and the note must be exactly reversible");
    }

    /// Ages come from the history, so an old play must recover. Ten days is
    /// past the 4.2-day default rotation but inside the 16.6-day ramp.
    #[test]
    fn an_old_play_is_damped_rather_than_blocked() {
        let c = fixture();
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 1, 'rec-a')",
                  [NOW - 10 * DAY]).unwrap();
        let d = Director::load(&c).unwrap();
        let w = d.weigh_all(NOW);
        let (_, a) = w.iter().find(|(e, _)| e.passage_id == 1).unwrap();
        assert!(a.is_eligible(), "10 days is past the 4.2-day rotation: {:?}", a.excluded);
        assert!(a.recording_ramp > 0.0 && a.recording_ramp < 1.0, "mid-ramp, got {}", a.recording_ramp);
    }

    /// A play stamped in the future -- clock skew, a restored backup -- must
    /// read as "just played", never as a negative age that clears every block.
    #[test]
    fn a_future_play_does_not_clear_the_block() {
        let c = fixture();
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 1, 'rec-a')",
                  [NOW + 30 * DAY]).unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.census(NOW).eligible, 2, "the future play must still block");
    }

    #[test]
    fn tuning_is_read_and_nulls_fall_back_to_defaults() {
        let c = fixture();
        // rotation tuned to 3.0 (41 days), recovery left NULL
        c.execute("INSERT INTO listener_preferences VALUES ('recording','rec-a',3.0,NULL,NULL)", [])
            .unwrap();
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 1, 'rec-a')",
                  [NOW - 10 * DAY]).unwrap();
        let d = Director::load(&c).unwrap();
        let w = d.weigh_all(NOW);
        let (_, a) = w.iter().find(|(e, _)| e.passage_id == 1).unwrap();
        assert_eq!(a.excluded, Some(Exclusion::RecordingRotationBlock),
                   "a 41-day rotation must still block a 10-day-old play");
    }

    #[test]
    fn time_scales_are_read_from_settings() {
        let c = fixture();
        c.execute("INSERT INTO listener_settings VALUES (1, 0.5, 0.25, 't')", []).unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.policy().artist_scale.get(), 0.5);
        assert_eq!(d.policy().recording_scale.get(), 0.25);
    }

    /// `[SPEC-VOC-010]` A database `tools/rename_recording_time_scale.py`
    /// has not yet reached still has `track_time_scale` -- unmigrated, not
    /// broken, so the value must still be read, not silently reset to the
    /// default `[SPEC-DIR-158]`.
    #[test]
    fn a_pre_migration_column_name_still_reads_the_tuned_value() {
        let c = fixture();
        c.execute_batch(
            "ALTER TABLE listener_settings RENAME COLUMN recording_time_scale TO track_time_scale;
             INSERT INTO listener_settings VALUES (1, 0.5, 0.25, 't');",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.policy().artist_scale.get(), 0.5, "the unrenamed column reads as before");
        assert_eq!(d.policy().recording_scale.get(), 0.25,
                   "the old column name must still populate the new field");
    }

    /// Halving the recording scale must actually shorten a block, not merely load.
    #[test]
    fn a_scaled_block_expires_sooner() {
        let c = fixture();
        // 3 days: inside the 4.2-day default rotation, outside a halved one.
        c.execute("INSERT INTO listener_play_history VALUES (1, ?1, 1, 'rec-a')",
                  [NOW - 3 * DAY]).unwrap();
        assert_eq!(Director::load(&c).unwrap().census(NOW).recording_blocked, 1);
        c.execute("INSERT INTO listener_settings VALUES (1, 1.0, 0.5, 't')", []).unwrap();
        assert_eq!(Director::load(&c).unwrap().census(NOW).recording_blocked, 0,
                   "half the rotation means the block has expired");
    }

    /// The record must be able to answer "why not something else?", so the
    /// losers travel with the winner.
    #[test]
    fn a_decision_carries_its_decomposition_and_its_losers() {
        let d = Director::load(&fixture()).unwrap();
        let dec = d.decide(NOW, &mut Rng::seeded(11), &[], None).unwrap();
        assert_eq!(dec.why.pool_size, 3);
        assert!((dec.why.pool_weight - 3.0).abs() < 1e-9);
        assert!((dec.why.share_pct - 33.333).abs() < 0.01, "share {}", dec.why.share_pct);
        assert_eq!(dec.why.runners_up.len(), 2, "the other two eligible passages");
        assert!(dec.why.runners_up.iter().all(|r| r.passage_id != dec.why.passage_id),
                "the winner must not be listed among those it beat");
        assert!(!dec.why.title.is_empty());
    }

    /// A pick names the slot in force by id as well as by name, for
    /// `selection_decisions.program_id` `[SPEC-DIR-190]`; with none, neither.
    #[test]
    fn a_decision_names_its_slot_by_id() {
        let c = fixture();
        let d = Director::load(&c).unwrap();
        let dec = d.decide(NOW, &mut Rng::seeded(3), &[], None).unwrap();
        assert_eq!((dec.why.slot.as_deref(), dec.why.slot_id), (None, None), "no slot, no id");
        c.execute_batch(
            "CREATE TABLE IF NOT EXISTS listener_programs (program_id INTEGER PRIMARY KEY, name TEXT, start_time TEXT);
             CREATE TABLE IF NOT EXISTS listener_program_seeds (program_id INTEGER, mbid TEXT, position INTEGER);
             INSERT INTO listener_programs VALUES (7, 'Allday', '00:00');",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        let dec = d.decide(NOW, &mut Rng::seeded(3), &[], None).unwrap();
        assert_eq!((dec.why.slot.as_deref(), dec.why.slot_id), (Some("Allday"), Some(7)));
    }

    /// The slot a pick is chosen for is the one in force when it will START,
    /// not when it is chosen `[SPEC-DIR-186]`: queued at 01:46 behind an hour
    /// of music, it plays at 02:46, in the slot that began at 02:00, and the
    /// record says when the pick was projected to begin.
    #[test]
    fn the_slot_is_the_one_in_force_when_the_pick_will_start() {
        let c = fixture();
        c.execute_batch(
            "CREATE TABLE IF NOT EXISTS listener_programs (program_id INTEGER PRIMARY KEY, name TEXT, start_time TEXT);
             CREATE TABLE IF NOT EXISTS listener_program_seeds (program_id INTEGER, mbid TEXT, position INTEGER);
             INSERT INTO listener_programs VALUES (8, 'Late', '00:00'), (9, 'Dawn', '02:00');",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        // NOW is 01:46:40 UTC, and the fixture's offset is UTC.
        let now = d.decide(NOW, &mut Rng::seeded(3), &[], None).unwrap();
        assert_eq!((now.why.slot.as_deref(), now.why.starts_at), (Some("Late"), NOW));
        let later = d.decide(NOW + 3600, &mut Rng::seeded(3), &[], None).unwrap();
        assert_eq!((later.why.slot.as_deref(), later.why.slot_id), (Some("Dawn"), Some(9)));
        assert_eq!(later.why.starts_at, NOW + 3600, "the record says when it was projected to start");
        let relaxed = d.decide_relaxed(NOW + 3600, &mut Rng::seeded(3), &[]).unwrap();
        assert_eq!(relaxed.why.slot.as_deref(), Some("Dawn"), "the relaxed fallback reads the same time");
    }

    /// Rotation, the rejection windows and the season are judged when the
    /// pick will start, as the slot is `[SPEC-DIR-186]`. rec-a played three
    /// days ago, inside its 4.2-day rotation: a pick starting now cannot be
    /// it, and one starting two days from now can. rec-b was skipped six days
    /// ago, inside the 156-hour window: held out now, free a day later.
    #[test]
    fn rotation_and_rejection_are_judged_when_the_pick_will_start() {
        let c = fixture();
        c.execute_batch(&format!(
            "INSERT INTO listener_play_history (played_at, passage_id, mbid) VALUES ({}, 1, 'rec-a');
             CREATE TABLE listener_rejections (rejection_id INTEGER PRIMARY KEY, rejected_at INTEGER,
                 kind TEXT, passage_id INTEGER, mbid TEXT, heard_ms INTEGER, span_ms INTEGER);
             INSERT INTO listener_rejections (rejected_at, kind, passage_id, mbid) VALUES ({}, 'skip', 2, 'rec-b');",
            NOW - 3 * DAY,
            NOW - 6 * DAY,
        ))
        .unwrap();
        let d = Director::load(&c).unwrap();
        let picks = |at: i64| -> HashSet<i64> {
            (0..60).filter_map(|s| d.decide(at, &mut Rng::seeded(s), &[], None)).map(|x| x.why.passage_id).collect()
        };
        assert_eq!(picks(NOW), HashSet::from([3]), "now: 1 in rotation, 2 inside its window");
        let later = picks(NOW + 2 * DAY);
        assert!(later.contains(&1), "two days on, 1 has rested its rotation: {later:?}");
        assert!(later.contains(&2), "and 2 is past its window: {later:?}");
    }

    /// Runners-up are the HEAVIEST losers, not an arbitrary five.
    #[test]
    fn runners_up_are_ordered_by_weight() {
        let c = fixture();
        c.execute("INSERT INTO listener_preferences VALUES ('recording','rec-c',NULL,NULL,-1.0)", [])
            .unwrap();
        let d = Director::load(&c).unwrap();
        // rec-c is 10x; whenever it does not win it must head the losers.
        for seed in 0..20 {
            let dec = d.decide(NOW, &mut Rng::seeded(seed), &[], None).unwrap();
            if dec.why.passage_id != 3 {
                assert_eq!(dec.why.runners_up[0].passage_id, 3,
                           "the heaviest loser must be listed first");
            }
        }
    }

    /// July, with a Christmas curve: the seasonal term must suppress the
    /// christmasy recording and leave the others alone.
    #[test]
    fn an_out_of_season_occasion_suppresses_only_its_own() {
        let c = fixture();
        c.execute_batch(
            "INSERT INTO listener_occasions VALUES ('user.christmas','christmasy','step');
             INSERT INTO listener_occasion_points VALUES
                 ('user.christmas','christmasy',1,1,0.000001),
                 ('user.christmas','christmasy',12,1,5.0);
             INSERT INTO flavor VALUES ('recording','rec-a','user.christmas','christmasy',
                 1.0,'user',NULL);",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.occasion_count(), 1);

        let july = 1_782_000_000; // 21 June 2026 onwards -- high summer
        let w = d.weigh_all(july);
        let (_, a) = w.iter().find(|(e, _)| e.passage_id == 1).unwrap();
        let (_, b) = w.iter().find(|(e, _)| e.passage_id == 2).unwrap();
        assert!(a.occasion < 0.001, "christmasy out of season: {}", a.occasion);
        assert_eq!(a.excluded, Some(Exclusion::BelowMinWeight));
        assert_eq!(b.occasion, 1.0, "a recording with no occasion value is untouched");
        assert!(b.is_eligible());
    }

    /// The listener's own profanity value replaces the inherited one, a zero
    /// of their own included, and is read with no occasion registered at all
    /// `[SPEC-PTL-060]`; the tolerance acts live, and the census says what it
    /// held out `[SPEC-PTL-070]`.
    #[test]
    fn the_profanity_tolerance_reads_the_values_the_panel_shows() {
        let c = fixture();
        c.execute_batch(
            "CREATE TABLE listener_characteristics (subject_kind TEXT, subject_id TEXT,
                 characteristic TEXT, class TEXT, value REAL, updated_at TEXT);
             CREATE TABLE player_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
             INSERT INTO flavor VALUES
                 ('recording','rec-a','user.profanity','profane',0.9,'inherited:mulib',NULL),
                 ('recording','rec-b','user.profanity','profane',0.6,'inherited:mulib',NULL);
             INSERT INTO listener_characteristics VALUES
                 ('recording','rec-a','user.profanity','profane',0.25,'now'),
                 ('recording','rec-b','user.profanity','profane',0.0,'now');",
        )
        .unwrap();
        let mut d = Director::load(&c).unwrap();
        assert_eq!(d.profanity_tolerance(), ProfanityTolerance::default(), "absent is 1.0");
        let now = 1_782_000_000;
        let term = |d: &Director, id: i64| {
            let w = d.weigh_all(now);
            w.iter().find(|(e, _)| e.passage_id == id).unwrap().1.clone()
        };
        assert_eq!(term(&d, 1).profanity, 1.0, "not considered at the default");

        d.set_profanity_tolerance(ProfanityTolerance::new(0.5));
        assert!((term(&d, 1).profanity - 0.5).abs() < 1e-12, "the own 0.25, not the inherited 0.9");
        assert_eq!(term(&d, 2).profanity, 1.0, "an own zero is no profanity");

        d.set_profanity_tolerance(ProfanityTolerance::new(0.0));
        assert_eq!(term(&d, 1).excluded, Some(Exclusion::Profanity));
        let census = d.census(now);
        assert_eq!((census.profanity, census.eligible), (1, 2));
        assert_eq!(census.total(), 3, "every radio passage in one bucket");

        // And as the player stores it.
        c.execute("INSERT INTO player_settings VALUES ('profanity_tolerance', '0.3', 'now')", [])
            .unwrap();
        assert_eq!(load_profanity_tolerance(&c).get(), 0.3);
        c.execute("UPDATE player_settings SET value = 'half' WHERE key = 'profanity_tolerance'", [])
            .unwrap();
        assert_eq!(load_profanity_tolerance(&c), ProfanityTolerance::default(), "unreadable is 1.0");
    }

    /// In season the same recording is boosted, scaled by its value.
    #[test]
    fn an_in_season_occasion_boosts() {
        let c = fixture();
        c.execute_batch(
            "INSERT INTO listener_occasions VALUES ('user.christmas','christmasy','step');
             INSERT INTO listener_occasion_points VALUES
                 ('user.christmas','christmasy',1,1,0.000001),
                 ('user.christmas','christmasy',12,1,5.0);
             INSERT INTO flavor VALUES ('recording','rec-a','user.christmas','christmasy',
                 0.5,'user',NULL);",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        let dec = 1_796_904_000; // 10 December 2026
        let w = d.weigh_all(dec);
        let (_, a) = w.iter().find(|(e, _)| e.passage_id == 1).unwrap();
        // value 0.5 against a x5.0 curve: 1 + 0.5*(5-1) = 3.0
        assert!((a.occasion - 3.0).abs() < 1e-9, "occasion {}", a.occasion);
    }

    /// The listener said "not really a Christmas song" about a recording the
    /// MuLibPlay migration tagged as one `[SPEC-PREF-085]`. Their value has
    /// to win, or the preference panel would accept an edit the engine then
    /// quietly ignored.
    #[test]
    fn a_listener_set_special_overrides_the_inherited_flavor_value() {
        let c = fixture();
        c.execute_batch(
            "CREATE TABLE listener_characteristics (subject_kind TEXT, subject_id TEXT,
                 characteristic TEXT, class TEXT, value REAL, updated_at TEXT);
             INSERT INTO listener_occasions VALUES ('user.christmas','christmasy','step');
             INSERT INTO listener_occasion_points VALUES
                 ('user.christmas','christmasy',1,1,0.000001),
                 ('user.christmas','christmasy',12,1,5.0);
             INSERT INTO flavor VALUES ('recording','rec-a','user.christmas','christmasy',
                 1.0,'inherited:mulib',NULL);
             INSERT INTO listener_characteristics VALUES ('recording','rec-a',
                 'user.christmas','christmasy',0.5,'now');",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        let dec = 1_796_904_000; // 10 December 2026
        let (_, a) = d.weigh_all(dec).into_iter().find(|(e, _)| e.passage_id == 1).unwrap();
        // The listener's 0.5, not the migration's 1.0: 1 + 0.5*(5-1) = 3.0,
        // where the inherited value alone would have given 5.0.
        assert!((a.occasion - 3.0).abs() < 1e-9, "occasion {}", a.occasion);
    }

    /// Tagging something the migration never knew about -- the ordinary case
    /// for music added since, and the only case there is for a special
    /// `flavor` carries no values for at all (profanity).
    #[test]
    fn a_listener_set_special_applies_where_flavor_had_nothing() {
        let c = fixture();
        c.execute_batch(
            "CREATE TABLE listener_characteristics (subject_kind TEXT, subject_id TEXT,
                 characteristic TEXT, class TEXT, value REAL, updated_at TEXT);
             INSERT INTO listener_occasions VALUES ('user.christmas','christmasy','step');
             INSERT INTO listener_occasion_points VALUES
                 ('user.christmas','christmasy',12,1,5.0);
             INSERT INTO listener_characteristics VALUES ('recording','rec-a',
                 'user.christmas','christmasy',1.0,'now');",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        let dec = 1_796_904_000;
        let w = d.weigh_all(dec);
        let (_, a) = w.iter().find(|(e, _)| e.passage_id == 1).unwrap();
        let (_, b) = w.iter().find(|(e, _)| e.passage_id == 2).unwrap();
        assert!((a.occasion - 5.0).abs() < 1e-9, "occasion {}", a.occasion);
        assert_eq!(b.occasion, 1.0, "an untagged recording is still untouched");
    }

    /// A value for a characteristic no curve covers is skipped, exactly as
    /// the `flavor` pass skips one -- so retiring an occasion from the
    /// registry does not require deleting the tagging first.
    #[test]
    fn a_listener_set_value_for_an_unregistered_characteristic_is_ignored() {
        let c = fixture();
        c.execute_batch(
            "CREATE TABLE listener_characteristics (subject_kind TEXT, subject_id TEXT,
                 characteristic TEXT, class TEXT, value REAL, updated_at TEXT);
             INSERT INTO listener_characteristics VALUES ('recording','rec-a',
                 'user.christmas','christmasy',1.0,'now');",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.occasion_count(), 0);
        assert!(d.weigh_all(NOW).iter().all(|(_, x)| x.occasion == 1.0));
    }

    /// Ordinary flavor characteristics are not seasons and must not be taken
    /// for them -- `flavor` holds 71 dimensions that are nothing of the kind.
    #[test]
    fn flavor_that_is_not_an_occasion_is_ignored() {
        let c = fixture();
        c.execute_batch(
            "INSERT INTO flavor VALUES ('recording','rec-a','mood_happy','happy',
                 0.9,'dump',NULL);",
        )
        .unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.occasion_count(), 0);
        assert!(d.weigh_all(NOW).iter().all(|(_, x)| x.occasion == 1.0));
    }

    /// Give the three recordings flavor spread along one characteristic, so
    /// distance is a known function of which is which.
    fn with_flavor(c: &Connection) {
        for (mbid, v) in [("rec-a", 0.0f64), ("rec-b", 0.4), ("rec-c", 1.0)] {
            for (class, val) in [("hi", v), ("lo", 1.0 - v)] {
                c.execute(
                    "INSERT INTO flavor VALUES ('recording',?1,'x',?2,?3,'test',NULL)",
                    rusqlite::params![mbid, class, val],
                )
                .unwrap();
            }
        }
    }

    /// A passage without flavor is the last resort `[SPEC-DIR-147]`, on every
    /// path the Director has: with a flow order, without one, and relaxed.
    /// Passage 3 loses its flavor; while 2 is there to choose, 3 never is, and
    /// when 2 is queued too, 3 is chosen -- with no rank to decay by.
    #[test]
    fn a_passage_without_flavor_is_chosen_only_when_nothing_with_it_is_left() {
        let c = fixture();
        with_flavor(&c);
        c.execute_batch("DELETE FROM flavor WHERE subject_id = 'rec-c';").unwrap();
        let d = Director::load(&c).unwrap();
        for seed in 0..40 {
            let rng = &mut Rng::seeded(seed);
            let flowed = d.decide(NOW, rng, &[1], Some(1)).unwrap();
            assert_eq!(flowed.why.passage_id, 2, "seed {seed}: following a passage with flavor");
            let first = d.decide(NOW, rng, &[], None).unwrap();
            assert_ne!(first.why.passage_id, 3, "seed {seed}: the first pick of a start");
            let relaxed = d.decide_relaxed(NOW, rng, &[1]).unwrap();
            assert_eq!(relaxed.why.passage_id, 2, "seed {seed}: rotation set aside");
        }
        let last = d.decide(NOW, &mut Rng::seeded(1), &[1, 2], Some(2)).unwrap();
        assert_eq!(last.why.passage_id, 3, "nothing with flavor left: the last resort");
        assert!(last.why.flow_distance.is_none() && last.why.stages.contains("no flow"),
                "and it is not ranked by a flow it cannot be measured on: {}", last.why.stages);
    }

    /// Stage C: the pool is ordered by distance to the passage already queued,
    /// so consecutive passages blend [SPEC-DIR-160]. rec-b (0.4) is nearer to
    /// rec-a (0.0) than rec-c (1.0) is, so it must always rank first.
    #[test]
    fn flow_orders_by_distance_to_the_queue_tail() {
        let c = fixture();
        with_flavor(&c);
        let d = Director::load(&c).unwrap();
        for seed in 0..30 {
            // passage 1 is the tail: skip it, and follow it
            let dec = d.decide(NOW, &mut Rng::seeded(seed), &[1], Some(1)).unwrap();
            match dec.why.passage_id {
                2 => assert_eq!(dec.why.rank, 0, "rec-b is nearer the tail"),
                3 => assert_eq!(dec.why.rank, 1, "rec-c is further"),
                other => panic!("unexpected winner {other}"),
            }
            assert!(dec.why.flow_distance.is_some(), "a followed passage has a flow distance");
        }
    }

    /// Stage D: the roulette weight is exactly weight x decay^rank
    /// [SPEC-DIR-165].
    #[test]
    fn rank_decay_is_applied_to_the_roulette_weight() {
        let c = fixture();
        with_flavor(&c);
        let d = Director::load(&c).unwrap();
        for seed in 0..30 {
            let dec = d.decide(NOW, &mut Rng::seeded(seed), &[1], Some(1)).unwrap();
            let expect = dec.why.weight * RANK_DECAY.powi(dec.why.rank as i32);
            assert!(
                (dec.why.decayed_weight - expect).abs() < 1e-12,
                "rank {} decayed {} expected {expect}",
                dec.why.rank,
                dec.why.decayed_weight
            );
        }
    }

    /// The stated chance is the weight drawn on over the total drawn on --
    /// decayed over decayed `[SPEC-DIR-191]`. Undecayed over a decayed total
    /// overstated every pick below rank 0.
    #[test]
    fn the_stated_chance_is_the_decayed_share() {
        let c = fixture();
        with_flavor(&c);
        let d = Director::load(&c).unwrap();
        for seed in 0..30 {
            let dec = d.decide(NOW, &mut Rng::seeded(seed), &[1], Some(1)).unwrap();
            let expect = dec.why.decayed_weight / dec.why.pool_weight * 100.0;
            assert!((dec.why.share_pct - expect).abs() < 1e-9, "share {} expected {expect}", dec.why.share_pct);
            assert!(dec.why.share_pct <= 100.0 + 1e-9, "a chance above certainty");
        }
    }

    /// A lower-ranked passage must still be able to win -- selection is by
    /// weight, not by rank, and that is where the surprise lives
    /// [SPEC-DIR-165]. A pool that always returned rank 0 would be a bug.
    #[test]
    fn a_lower_ranked_passage_can_still_win() {
        let c = fixture();
        with_flavor(&c);
        let d = Director::load(&c).unwrap();
        let mut ranks = std::collections::HashSet::new();
        for seed in 0..200 {
            let dec = d.decide(NOW, &mut Rng::seeded(seed), &[1], Some(1)).unwrap();
            ranks.insert(dec.why.rank);
        }
        assert!(ranks.len() > 1, "only rank {:?} ever won", ranks);
    }

    /// With nothing queued there is no flow order, so rank would be arbitrary
    /// and decay would favour whatever the scan visited first. Neither applies.
    #[test]
    fn without_a_queue_tail_there_is_no_flow_or_decay() {
        let c = fixture();
        with_flavor(&c);
        let d = Director::load(&c).unwrap();
        let dec = d.decide(NOW, &mut Rng::seeded(3), &[], None).unwrap();
        assert!(dec.why.flow_distance.is_none());
        assert_eq!(dec.why.decayed_weight, dec.why.weight, "no rank means no decay");
        assert!(dec.why.stages.contains("no flow"), "the record must say so: {}", dec.why.stages);
    }

    #[test]
    fn selection_is_reproducible_from_a_seed() {
        let d = Director::load(&fixture()).unwrap();
        let a: Vec<_> = (0..8).map(|_| d.choose(NOW, &mut Rng::seeded(42)).unwrap().passage_id).collect();
        let b: Vec<_> = (0..8).map(|_| d.choose(NOW, &mut Rng::seeded(42)).unwrap().passage_id).collect();
        assert_eq!(a, b, "the same seed must replay the same station [REQ-PD-110]");
    }

    /// Weight must actually steer the roulette, or Stage A is decorative.
    #[test]
    fn a_heavier_passage_wins_more_often() {
        let c = fixture();
        // restraint -1.0 on rec-a is a 10x boost; the others stay at 1.0
        c.execute("INSERT INTO listener_preferences VALUES ('recording','rec-a',NULL,NULL,-1.0)", [])
            .unwrap();
        let d = Director::load(&c).unwrap();
        let mut rng = Rng::seeded(7);
        let mut wins = 0;
        for _ in 0..1000 {
            if d.choose(NOW, &mut rng).unwrap().passage_id == 1 {
                wins += 1;
            }
        }
        // 10 / 12 == 83%. Wide bounds: this asserts steering, not calibration.
        assert!((750..=900).contains(&wins), "heavy passage won {wins}/1000");
    }

    /// Queueing must feed back into selection, or a five-slot top-up could
    /// queue five recordings by one artist.
    #[test]
    fn queueing_a_passage_blocks_it_for_the_next_pick() {
        let mut d = Director::load(&fixture()).unwrap();
        assert_eq!(d.census(NOW).eligible, 3);
        d.note_queued(1, NOW);
        assert_eq!(d.census(NOW).eligible, 2, "a queued passage stops being a candidate");
    }

    #[test]
    fn the_skip_list_keeps_a_passage_out_of_its_own_queue() {
        let d = Director::load(&fixture()).unwrap();
        let mut rng = Rng::seeded(3);
        for _ in 0..50 {
            let got = d.choose_excluding(NOW, &mut rng, &[1, 2]).unwrap();
            assert_eq!(got.passage_id, 3, "only the unskipped passage may be chosen");
        }
    }

    #[test]
    fn an_entirely_blocked_library_chooses_nothing_rather_than_panicking() {
        let c = fixture();
        for (i, m) in [(1, "rec-a"), (2, "rec-b"), (3, "rec-c")] {
            c.execute("INSERT INTO listener_play_history VALUES (NULL, ?1, ?2, ?3)",
                      rusqlite::params![NOW - 60, i, m]).unwrap();
        }
        let d = Director::load(&c).unwrap();
        assert_eq!(d.census(NOW).eligible, 0);
        assert!(d.choose(NOW, &mut Rng::seeded(1)).is_none(),
                "no eligible passage must be None, so the caller can fall back");
    }

    #[test]
    fn an_unidentified_passage_still_plays() {
        let c = fixture();
        c.execute("DELETE FROM passage_recordings WHERE mbid = 'rec-a'", []).unwrap();
        let d = Director::load(&c).unwrap();
        assert_eq!(d.census(NOW).eligible, 3, "no MBID means no history, not exclusion");
    }
}
