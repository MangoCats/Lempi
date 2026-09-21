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

use std::collections::HashMap;

use rusqlite::Connection;
use serde::Serialize;

use crate::db::QualifyingConn;

use super::frequency::{
    weigh, Candidate, Exclusion, Policy, Related, TimeScale, Tuning, Weighing,
};
use super::flavor::{centroid, Flavor, FlavorIndex};
use super::occasion::{civil_from_unix, ordinal, Curve, Interp, Occasions};
use super::program::Programs;
use super::shape::{shape, Seed, Shaping, LIKE_SEED_WEIGHT, RAND_POOL};
use crate::db::{row_to_entry, DbError, COLS, FROM};
use crate::queue::QueueEntry;

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
}

/// How many losing candidates the record keeps.
///
/// `[SPEC-DIR-190]` asks for "the runners-up that lost". All 8,000 of them is
/// not an explanation, it is a data dump; the heaviest few are what answer
/// "why not something else?".
const RUNNERS_UP: usize = 5;

/// Rank decay `[SPEC-DIR-165]`. Applied over the flow-ordered pool, so the
/// passages that follow the queue tail best are favoured — without ever
/// becoming certain. At rank 100 this is ×0.017.
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
    /// Eligible candidates it beat.
    /// The programme in force `[SPEC-DIR-180]`, if any.
    pub program: Option<String>,
    /// What pool shaping did `[SPEC-DIR-145]`.
    pub shaping: Shaping,
    /// Distance to each seed, in seed order `[SPEC-DIR-190]`.
    pub seed_distances: Vec<f64>,
    pub pool_size: usize,
    pub pool_weight: f64,
    /// This passage's share of the total weight, as a percentage — the honest
    /// statement of how likely it was, as opposed to how good it is.
    pub share_pct: f64,
    pub runners_up: Vec<RunnerUp>,
    /// Stages B and C have not run: there is no flavor shaping yet
    /// `[SPEC-FD-040]`. Stated in the record so a stored decision is not later
    /// mistaken for a shaped one.
    pub stages: &'static str,
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

/// Enough to undo a `note_queued` exactly `[REQ-PD-112]`.
///
/// Held between choosing a passage and learning whether it could be opened. It
/// carries the values that were there before, because `max` cannot be inverted
/// from its result alone.
#[derive(Debug, Clone)]
pub struct QueuedNote {
    passage_id: i64,
    prev_passage: Option<i64>,
    mbid: Option<String>,
    prev_recording: Option<i64>,
    artist: Option<String>,
    prev_artist: Option<i64>,
    /// One entry per work the recording performs `[GDE-WRK-070]`, each with
    /// the stamp that was there before.
    works: Vec<(String, Option<i64>)>,
}

pub struct Director {
    rows: Vec<Row>,
    policy: Policy,
    recording_tuning: HashMap<String, Tuning>,
    artist_tuning: HashMap<String, Tuning>,
    /// recording → its artist. One artist per recording, as migrated.
    artist_of: HashMap<String, String>,
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
    relations: HashMap<String, Vec<(String, f64)>>,
    occasions: Occasions,
    flavor: FlavorIndex,
    programs: Programs,
    /// Centroids of liked and disliked flavor `[SPEC-DIR-150]`. Built once:
    /// they change only when the listener does.
    like: Option<Flavor>,
    dislike: Option<Flavor>,
}

impl Director {
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

        let artist_of: HashMap<String, String> = map_query(conn, "SELECT mbid, artist_mbid FROM __LIB__.recording_artists")?;

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
            Err(e) => eprintln!(
                "director: no recording_works ({e}); same-song blocking is \
                 limited to the recording tier [GDE-WRK-035]"
            ),
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
                "SELECT passage_id, MAX(played_at) FROM listener_play_history                  WHERE passage_id IS NOT NULL GROUP BY passage_id",
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
        }

        let mut stmt = conn
            .prepare(
                "SELECT mbid, MAX(played_at) FROM listener_play_history                  WHERE mbid IS NOT NULL GROUP BY mbid",
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
            if let Some(a) = artist_of.get(&mbid) {
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
        let flavor = FlavorIndex::load(conn)?;
        let programs = Programs::load(conn)?;
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
            .ok();
        // Rejections, for suppression only `[SPEC-PLAY-050]`. An absent table is
        // a library that predates the feature, not a fault: nothing recorded
        // means nothing suppressed.
        let rejected = |kind: &str| -> HashMap<String, i64> {
            conn.prepare(
                "SELECT mbid, MAX(rejected_at) FROM listener_rejections                  WHERE mbid IS NOT NULL AND kind = ?1 GROUP BY mbid",
            )
            .and_then(|mut q| {
                q.query_map([kind], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                    .map(|rows| rows.flatten().collect())
            })
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

        Ok(Self {
            rows,
            policy,
            last_skipped,
            last_dequeued,
            recording_tuning,
            artist_tuning,
            artist_of,
            last_played,
            artist_last_played,
            works_of,
            passage_last_played,
            work_last_played,
            relations,
            occasions,
            flavor,
            programs,
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

    pub fn policy(&self) -> Policy {
        self.policy
    }
    pub fn set_policy(&mut self, p: Policy) {
        self.policy = p;
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
    pub fn programs(&self) -> &Programs {
        &self.programs
    }
    pub fn programs_mut(&mut self) -> &mut Programs {
        &mut self.programs
    }
    pub fn flavor(&self) -> &FlavorIndex {
        &self.flavor
    }

    /// The seeds shaping the pool right now: the active programme's, plus
    /// Like-Taste as an additional seed `[SPEC-DIR-150]`.
    fn seeds_now(&self, now: i64) -> Vec<Seed<'_>> {
        let mut out: Vec<Seed<'_>> = Vec::new();
        if let Some(p) = self.programs.active(now) {
            for mbid in self.programs.seeds_for(p.id, &self.artist_of, &self.last_played) {
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

    fn age(&self, map: &HashMap<String, i64>, key: &str, now: i64) -> Option<f64> {
        // A play stamped in the future -- clock skew, or a restored backup --
        // must read as "just played", not as a negative age that would sail
        // through every block.
        map.get(key).map(|at| (now - at).max(0) as f64)
    }

    /// Weigh every radio passage. `now` is unix seconds, passed in so a
    /// selection can be replayed against a frozen history `[REQ-PD-110]`.
    pub fn weigh_all(&self, now: i64) -> Vec<(&QueueEntry, Weighing)> {
        let mut related_buf: Vec<Related> = Vec::new();
        // The day is resolved once per pass, not per passage: every candidate is
        // weighed against the same "today", which is also what keeps a selection
        // replayable from a frozen `now`.
        let (_, month, day) = civil_from_unix(now);
        let today = ordinal(month, day);
        self.rows
            .iter()
            .map(|row| {
                let mbid = row.mbid.as_deref();
                let recording = mbid
                    .and_then(|m| self.recording_tuning.get(m))
                    .copied()
                    .unwrap_or_else(Tuning::recording_defaults);
                let artist_id = mbid.and_then(|m| self.artist_of.get(m));
                let artist = artist_id
                    .and_then(|a| self.artist_tuning.get(a))
                    .copied()
                    .unwrap_or_else(Tuning::artist_defaults);

                related_buf.clear();
                if let Some(m) = mbid {
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
                    artist,
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
                    artist_age_s: artist_id
                        .and_then(|a| self.age(&self.artist_last_played, a, now)),
                    related: &related_buf,
                    occasion: self.occasions.multiplier(mbid, today),
                };
                (&row.entry, weigh(&c, &self.policy))
            })
            .collect()
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
        let row = self.rows.iter().find(|r| r.entry.passage_id == passage_id)?;
        let mbid = row.mbid.clone();
        let artist = mbid.as_ref().and_then(|m| self.artist_of.get(m)).cloned();
        let works: Vec<String> = mbid
            .as_ref()
            .and_then(|m| self.works_of.get(m))
            .cloned()
            .unwrap_or_default();
        // Every previous value is kept so the note can be taken back exactly.
        // `max` is not invertible: without the old value, undoing a note would
        // have to guess, and guessing at rotation history is how a recording
        // that never played ends up suppressed.
        let note = QueuedNote {
            passage_id,
            prev_passage: self.passage_last_played.get(&passage_id).copied(),
            mbid: mbid.clone(),
            prev_recording: mbid.as_ref().and_then(|m| self.last_played.get(m).copied()),
            artist: artist.clone(),
            prev_artist: artist
                .as_ref()
                .and_then(|a| self.artist_last_played.get(a).copied()),
            works: works
                .iter()
                .map(|w| (w.clone(), self.work_last_played.get(w).copied()))
                .collect(),
        };
        let e = self.passage_last_played.entry(passage_id).or_insert(at);
        *e = (*e).max(at);
        if let Some(a) = artist {
            let e = self.artist_last_played.entry(a).or_insert(at);
            *e = (*e).max(at);
        }
        for w in works {
            let e = self.work_last_played.entry(w).or_insert(at);
            *e = (*e).max(at);
        }
        if let Some(m) = mbid {
            let e = self.last_played.entry(m).or_insert(at);
            *e = (*e).max(at);
        }
        Some(note)
    }

    /// Undo a note for a passage that never played `[REQ-PD-112]`.
    ///
    /// A passage can be chosen, noted, and then fail to open -- an unreadable
    /// file, a path that moved. The engine drops it, and without this the
    /// Director would go on believing it was heard, suppressing that recording
    /// and its artist for a full rotation on the strength of a play that never
    /// happened.
    pub fn forget_queued(&mut self, note: QueuedNote) {
        let restore_i = |m: &mut HashMap<i64, i64>, k: i64, prev: Option<i64>| match prev {
            Some(p) => {
                m.insert(k, p);
            }
            None => {
                m.remove(&k);
            }
        };
        let restore_s = |m: &mut HashMap<String, i64>, k: String, prev: Option<i64>| match prev {
            Some(p) => {
                m.insert(k, p);
            }
            None => {
                m.remove(&k);
            }
        };
        restore_i(&mut self.passage_last_played, note.passage_id, note.prev_passage);
        for (w, prev) in note.works {
            // Another passage of the same work may have been noted since, and
            // restoring "nothing was there" would forget that one too -- the
            // same hazard the artist tier documents below.
            restore_s(&mut self.work_last_played, w, prev);
        }
        if let Some(mbid) = note.mbid {
            restore_s(&mut self.last_played, mbid, note.prev_recording);
        }
        if let Some(a) = note.artist {
            match note.prev_artist {
                Some(prev) => {
                    self.artist_last_played.insert(a, prev);
                }
                // Another passage by the same artist may have been noted since,
                // in which case removing the entry would forget that one too.
                // Restoring what was there is the only safe undo, and "nothing
                // was there" is a value like any other.
                None => {
                    self.artist_last_played.remove(&a);
                }
            }
        }
    }

    /// Weighted-random pick over the eligible pool.
    ///
    /// This is Stage D applied directly to Stage A, with no flavor shaping in
    /// between: stages B and C need flavor distance `[SPEC-FD-040]`. It is an
    /// honest increment -- frequency alone already beats uniform random -- and
    /// the seam is exactly where the shaped pool will be inserted.
    pub fn choose(&self, now: i64, rng: &mut Rng) -> Option<QueueEntry> {
        self.decide(now, rng, &[], None).map(|d| d.entry)
    }

    /// As [`Director::decide`], discarding the reasoning.
    pub fn choose_excluding(&self, now: i64, rng: &mut Rng, skip: &[i64]) -> Option<QueueEntry> {
        self.decide(now, rng, skip, skip.last().copied()).map(|d| d.entry)
    }

    /// As [`Director::choose`], skipping passages already in the queue.
    ///
    /// [`Director::note_queued`] handles this for anything identified, by
    /// making it its own rotation block. This is the structural guarantee for
    /// the rest: an unidentified passage has no MBID to block on, and must
    /// still not appear twice in one queue.
    /// `after` is the passage this one will follow — the queue tail. Flow
    /// ordering is measured from it `[SPEC-DIR-160]`. Passed explicitly rather
    /// than taken as the last of `skip`, because "do not pick these" and "this
    /// is what plays before" are different questions that happen to share a
    /// list today.
    pub fn decide(
        &self,
        now: i64,
        rng: &mut Rng,
        skip: &[i64],
        after: Option<i64>,
    ) -> Option<Decision> {
        let weighed = self.weigh_all(now);

        // Stage B shapes WHICH passages are in the running; it never touches a
        // weight [SPEC-DIR-100]. Survivors carry their Stage-A weights forward
        // unchanged, so frequency and character stay separable in the panel.
        let seeds = self.seeds_now(now);
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
            .map(|(e, w)| {
                let d = anchor.and_then(|a| {
                    self.flavor_of(e.passage_id)
                        .and_then(|f| super::flavor::distance(&self.flavor.schema, f, a))
                });
                (*e, w, d)
            })
            .collect();
        if ranked.is_empty() {
            return None;
        }
        let flowed = anchor.is_some();
        if flowed {
            // Unmeasured passages sort last rather than being dropped: they can
            // still play, they simply cannot claim to follow well.
            ranked.sort_by(|a, b| match (a.2, b.2) {
                (Some(x), Some(y)) => x.partial_cmp(&y).unwrap_or(std::cmp::Ordering::Equal),
                (Some(_), None) => std::cmp::Ordering::Less,
                (None, Some(_)) => std::cmp::Ordering::Greater,
                (None, None) => std::cmp::Ordering::Equal,
            });
        }

        // --- Stage D: rank decay, then weighted roulette [SPEC-DIR-165] ---
        // Decay only makes sense over a MEANINGFUL rank. With no queue tail
        // there is no flow order, so rank would be arbitrary and decay would
        // favour whatever the scan happened to visit first.
        let take = if flowed { RAND_POOL } else { ranked.len() };
        let pool: Vec<(&QueueEntry, &Weighing, Option<f64>, f64)> = ranked
            .into_iter()
            .take(take)
            .enumerate()
            .map(|(rank, (e, w, d))| {
                let decayed =
                    if flowed { w.weight * RANK_DECAY.powi(rank as i32) } else { w.weight };
                (e, w, d, decayed)
            })
            .collect();

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
                pool_size,
                pool_weight: total,
                program: self
                    .programs
                    .active(now)
                    .map(|p| p.name.clone()),
                shaping: shaping.clone(),
                seed_distances: self.seed_distances(entry.passage_id, &seeds),
                share_pct: if total > 0.0 { w.weight / total * 100.0 } else { 0.0 },
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
                stages: if flowed {
                    "frequency, shaping, flow, rank decay"
                } else {
                    "frequency, shaping; no flow -- nothing queued to follow"
                },
            },
        })
    }

    /// The flavor of whatever recording a passage carries.
    fn flavor_of(&self, passage_id: i64) -> Option<&Flavor> {
        let row = self.rows.iter().find(|r| r.entry.passage_id == passage_id)?;
        self.flavor.get(row.mbid.as_deref()?)
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
    }
}

/// Curves and the per-subject values they apply to `[SPEC-DIR-130]`.
///
/// Missing tables are not an error: a library with no occasions defined simply
/// has no seasons, and every multiplier is 1.0.
fn load_occasions(conn: &QualifyingConn) -> Result<Occasions, DbError> {
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
        "SELECT subject_id, characteristic, class, value FROM listener_characteristics          WHERE subject_kind = 'recording'",
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

fn map_query(conn: &QualifyingConn, sql: &str) -> Result<HashMap<String, String>, DbError> {
    let q = |e: rusqlite::Error| DbError::Query(e.to_string());
    let mut stmt = conn.prepare(sql).map_err(q)?;
    let rows = stmt
        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))
        .map_err(q)?
        .collect::<rusqlite::Result<Vec<_>>>()
        .map_err(q)?;
    Ok(rows.into_iter().collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: i64 = 1_000_000_000;
    const DAY: i64 = 86_400;

    /// The slice of SPEC008 selection touches. Written out rather than loaded
    /// from schema.sql so these tests pin the column names the queries need.
    fn fixture() -> QualifyingConn {
        let c = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        c.execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, path TEXT NOT NULL,
                                 duration_ms INTEGER);
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
             INSERT INTO files VALUES (1, '/m/a.mp3', 600000);
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
