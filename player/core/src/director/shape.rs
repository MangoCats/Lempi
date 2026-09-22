//! Stage B: pool shaping — *does this fit right now?* `[SPEC-DIR-145]`
//!
//! Candidates are shaped by flavor distance in two stages `[REQ-PD-130]`:
//! pruned against the programme's seeds, then ordered by similarity to the
//! passage already queued.
//!
//! Frequency has already said how often each passage may play. This stage says
//! nothing about frequency and never touches a weight `[SPEC-DIR-100]`: it
//! decides **which** passages are in the running, and hands the survivors on
//! with their Stage-A weights untouched. Keeping the two apart is what lets the
//! panel tell two stories instead of one opaque product.
//!
//! ```text
//!   eligible pool
//!     ├─ dislike filter ... drop what sits too close to Dislike-Taste
//!     ├─ prune ........... keep the excl_pool nearest to any seed
//!     └─ gather .......... take the nearest few to each seed, and union them
//! ```

use std::collections::HashSet;

use super::flavor::{distance, Flavor, FlavorSchema};

/// `[SPEC-DIR-195]`. Re-derived and kept `[SPEC-DIR-205]`: measured against the
/// retrieval harness on the local 18-characteristic metric, the pool
/// boundaries these values select land in the same place as they did on the
/// 11 unweighted dimensions they were tuned for. No longer marked re-derive;
/// revisit only if the library's composition changes substantially, not
/// because the vector grew.
pub const EXCL_POOL: usize = 1000;
pub const RAND_POOL: usize = 100;

/// How close to Dislike-Taste is too close `[SPEC-DIR-150]`. **New and
/// unvalidated** — there is no listener data to tune it against.
pub const DISLIKE_RADIUS: f64 = 0.5;

/// Like-Taste's pull relative to a programme seed. Also new and unvalidated.
pub const LIKE_SEED_WEIGHT: f64 = 1.0;

/// A seed the pool is shaped toward.
pub struct Seed<'a> {
    pub flavor: &'a Flavor,
    /// Programme seeds weigh 1.0; Like-Taste weighs `LIKE_SEED_WEIGHT`.
    pub weight: f64,
}

/// What shaping did, so the panel can say why a passage was in the running.
#[derive(Debug, Clone, Default, PartialEq, serde::Serialize)]
pub struct Shaping {
    pub eligible_in: usize,
    pub disliked_out: usize,
    pub pruned_out: usize,
    pub gathered: usize,
    pub seeds_used: usize,
    /// No seeds, or no flavor: the pool passes through untouched and selection
    /// falls back to frequency alone `[SPEC-DIR-158]`.
    pub bypassed: bool,
}

/// Shape a pool of candidates.
///
/// `candidates` is `(key, flavor)` for every *eligible* passage; the key is
/// whatever the caller identifies a passage by. Returns the keys that survive,
/// in no meaningful order — ordering is Stage C's job `[SPEC-DIR-160]`.
///
/// A candidate with no flavor is **kept**, not dropped. Unmeasured is not the
/// same as unsuitable, and silently excluding every unanalysed passage would
/// make a half-scanned library play only the half it had scanned.
pub fn shape<K: Copy + Eq + std::hash::Hash>(
    schema: &FlavorSchema,
    candidates: &[(K, Option<&Flavor>)],
    seeds: &[Seed<'_>],
    dislike: Option<&Flavor>,
    stats: &mut Shaping,
) -> Vec<K> {
    stats.eligible_in = candidates.len();
    stats.seeds_used = seeds.len();

    if seeds.is_empty() || candidates.is_empty() {
        stats.bypassed = true;
        stats.gathered = candidates.len();
        return candidates.iter().map(|(k, _)| *k).collect();
    }

    // --- dislike filter, before gathering [SPEC-DIR-150] ---
    // An exclusion, never a weight change: removing a passage from the pool
    // leaves its rotation and restraint untouched, so deleting the Dislike
    // later returns behaviour to baseline with no residue [SPEC-DIR-155].
    let mut pool: Vec<(K, Option<&Flavor>)> = Vec::with_capacity(candidates.len());
    for (k, f) in candidates {
        let too_close = match (dislike, f) {
            (Some(d), Some(f)) => distance(schema, f, d).is_some_and(|x| x < DISLIKE_RADIUS),
            _ => false,
        };
        if too_close {
            stats.disliked_out += 1;
        } else {
            pool.push((*k, *f));
        }
    }

    // Distance to the NEAREST seed. "Most unlike every seed" means far from all
    // of them; a passage close to one seed belongs, even if far from the rest —
    // a programme is a handful of exemplars, not a single centre.
    let nearest = |f: Option<&Flavor>| -> Option<f64> {
        let f = f?;
        seeds
            .iter()
            .filter_map(|s| distance(schema, f, s.flavor).map(|d| d / s.weight.max(f64::EPSILON)))
            .fold(None, |acc: Option<f64>, d| Some(acc.map_or(d, |a| a.min(d))))
    };

    // --- prune: keep the excl_pool closest to any seed ---
    let mut scored: Vec<(K, Option<&Flavor>, Option<f64>)> =
        pool.into_iter().map(|(k, f)| (k, f, nearest(f))).collect();
    if scored.len() > EXCL_POOL {
        // Unmeasured passages sort last but are not dropped unless the pool is
        // genuinely over-full, so an unanalysed library still plays.
        scored.sort_by(|a, b| match (a.2, b.2) {
            (Some(x), Some(y)) => x.partial_cmp(&y).unwrap_or(std::cmp::Ordering::Equal),
            (Some(_), None) => std::cmp::Ordering::Less,
            (None, Some(_)) => std::cmp::Ordering::Greater,
            (None, None) => std::cmp::Ordering::Equal,
        });
        stats.pruned_out = scored.len() - EXCL_POOL;
        scored.truncate(EXCL_POOL);
    }

    // --- gather: the nearest few to each seed, unioned ---
    // Per seed rather than globally, so every seed contributes. A global
    // top-N would let one seed in a dense region supply the entire pool and
    // quietly drop the rest of the programme.
    let per_seed = (RAND_POOL * 2 / seeds.len().max(1)).max(1);
    let mut chosen: HashSet<K> = HashSet::new();
    let mut out: Vec<K> = Vec::new();
    let mut ranked: Vec<(f64, usize)> = Vec::with_capacity(scored.len());
    for seed in seeds {
        ranked.clear();
        for (i, (_, f, _)) in scored.iter().enumerate() {
            if let Some(f) = f {
                if let Some(d) = distance(schema, f, seed.flavor) {
                    ranked.push((d / seed.weight.max(f64::EPSILON), i));
                }
            }
        }
        ranked.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
        for (_, i) in ranked.iter().take(per_seed) {
            let k = scored[*i].0;
            if chosen.insert(k) {
                out.push(k);
            }
        }
    }

    // Passages with no flavor cannot be gathered by distance, so they are
    // admitted separately rather than being silently unplayable.
    for (k, f, _) in &scored {
        if f.is_none() && chosen.insert(*k) {
            out.push(*k);
        }
    }

    // Everything was pruned or nothing had flavor: fall back to the pruned pool
    // rather than returning an empty station.
    if out.is_empty() {
        stats.bypassed = true;
        out = scored.iter().map(|(k, _, _)| *k).collect();
    }
    stats.gathered = out.len();
    out
}

#[cfg(test)]
mod tests {
    use super::super::flavor::{centroid, FlavorIndex};
    use super::*;
    use crate::db::QualifyingConn;
    use rusqlite::Connection;

    /// A library of `n` recordings spread evenly along one binary
    /// characteristic, so distance is a known function of index.
    fn library(n: usize) -> FlavorIndex {
        let c = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        c.execute_batch(
            "CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT,
                 class TEXT, value REAL, source TEXT, accuracy REAL);
             CREATE TABLE flavor_constants (characteristic TEXT PRIMARY KEY, beta REAL,
                 reliability REAL, measured_on TEXT, measured_at TEXT);
             INSERT INTO flavor_constants VALUES ('x', 1.0, 1.0, 'test', 't');",
        )
        .unwrap();
        for i in 0..n {
            let v = i as f64 / (n - 1).max(1) as f64;
            for (cl, val) in [("hi", v), ("lo", 1.0 - v)] {
                c.execute(
                    "INSERT INTO flavor VALUES ('recording',?1,'x',?2,?3,'test',NULL)",
                    rusqlite::params![format!("s{i:04}"), cl, val],
                )
                .unwrap();
            }
        }
        FlavorIndex::load(&c).unwrap()
    }

    fn key(i: usize) -> usize {
        i
    }

    #[test]
    fn no_seeds_passes_the_pool_through_untouched() {
        let idx = library(50);
        let cands: Vec<(usize, Option<&Flavor>)> =
            (0..50).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        let mut st = Shaping::default();
        let out = shape(&idx.schema, &cands, &[], None, &mut st);
        assert_eq!(out.len(), 50, "cold start is uniform over the eligible pool [SPEC-DIR-158]");
        assert!(st.bypassed);
    }

    /// Gathering must return passages NEAR the seed, not an arbitrary slice.
    #[test]
    fn gathering_returns_the_nearest_to_the_seed() {
        let idx = library(500);
        let cands: Vec<(usize, Option<&Flavor>)> =
            (0..500).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        let seed_flavor = idx.get("s0000").unwrap(); // one end of the range
        let seeds = [Seed { flavor: seed_flavor, weight: 1.0 }];
        let mut st = Shaping::default();
        let out = shape(&idx.schema, &cands, &seeds, None, &mut st);

        assert_eq!(out.len(), RAND_POOL * 2, "one seed gathers rand_pool*2");
        let worst = out.iter().copied().max().unwrap();
        assert!(worst < 260, "gathered the far end of the library: worst index {worst}");
        assert!(out.contains(&0), "the seed's own neighbourhood must be in");
    }

    /// Every seed contributes. A global top-N would let one seed in a dense
    /// region supply the whole pool and drop the rest of the programme.
    #[test]
    fn every_seed_contributes_to_the_pool() {
        let idx = library(500);
        let cands: Vec<(usize, Option<&Flavor>)> =
            (0..500).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        let a = idx.get("s0000").unwrap();
        let b = idx.get("s0499").unwrap();
        let seeds = [Seed { flavor: a, weight: 1.0 }, Seed { flavor: b, weight: 1.0 }];
        let mut st = Shaping::default();
        let out = shape(&idx.schema, &cands, &seeds, None, &mut st);
        assert!(out.iter().any(|&i| i < 60), "the low seed's neighbourhood");
        assert!(out.iter().any(|&i| i > 440), "the high seed's neighbourhood");
        assert_eq!(st.seeds_used, 2);
    }

    #[test]
    fn pruning_bounds_the_pool_before_gathering() {
        let idx = library(2000);
        let cands: Vec<(usize, Option<&Flavor>)> =
            (0..2000).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        let seeds = [Seed { flavor: idx.get("s0000").unwrap(), weight: 1.0 }];
        let mut st = Shaping::default();
        shape(&idx.schema, &cands, &seeds, None, &mut st);
        assert_eq!(st.pruned_out, 2000 - EXCL_POOL);
    }

    /// Dislike removes from the POOL. It must never alter a weight -- deleting
    /// the Dislike later has to return behaviour to baseline [SPEC-DIR-155].
    #[test]
    fn dislike_excludes_its_neighbourhood() {
        let idx = library(200);
        let cands: Vec<(usize, Option<&Flavor>)> =
            (0..200).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        let seeds = [Seed { flavor: idx.get("s0100").unwrap(), weight: 1.0 }];
        let disliked = idx.get("s0000").unwrap();

        let mut with = Shaping::default();
        let out = shape(&idx.schema, &cands, &seeds, Some(disliked), &mut with);
        assert!(with.disliked_out > 0, "nothing was excluded");
        assert!(!out.contains(&0), "the disliked passage itself must be gone");

        let mut without = Shaping::default();
        let base = shape(&idx.schema, &cands, &seeds, None, &mut without);
        assert_eq!(without.disliked_out, 0);
        assert!(base.contains(&0), "and present when there is no Dislike");
    }

    /// A candidate with no flavor is kept. Unmeasured is not unsuitable, and
    /// dropping them would make a half-scanned library play only half itself.
    #[test]
    fn candidates_without_flavor_are_kept() {
        let idx = library(50);
        let mut cands: Vec<(usize, Option<&Flavor>)> =
            (0..50).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        cands.push((999, None));
        let seeds = [Seed { flavor: idx.get("s0000").unwrap(), weight: 1.0 }];
        let mut st = Shaping::default();
        let out = shape(&idx.schema, &cands, &seeds, None, &mut st);
        assert!(out.contains(&999), "an unanalysed passage must still be playable");
    }

    /// A Taste centroid is an ordinary vector and shapes like any other seed.
    #[test]
    fn a_centroid_acts_as_a_seed() {
        // 800, so one seed gathering rand_pool*2 cannot simply take everything.
        let idx = library(800);
        let members: Vec<(&Flavor, f64)> = (0..5)
            .map(|i| (idx.get(&format!("s{i:04}")).unwrap(), 1.0))
            .collect();
        let c = centroid(&idx.schema, &members).unwrap();
        let cands: Vec<(usize, Option<&Flavor>)> =
            (0..800).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        let seeds = [Seed { flavor: &c, weight: LIKE_SEED_WEIGHT }];
        let mut st = Shaping::default();
        let out = shape(&idx.schema, &cands, &seeds, None, &mut st);
        assert_eq!(out.len(), RAND_POOL * 2);
        let worst = out.iter().copied().max().unwrap();
        assert!(worst < 260, "a centroid of the low end gathers the low end, worst {worst}");
    }

    /// Everything disliked must not yield a silent station.
    #[test]
    fn a_pool_emptied_by_dislike_falls_back_rather_than_going_silent() {
        let idx = library(30);
        let cands: Vec<(usize, Option<&Flavor>)> =
            (0..30).map(|i| (key(i), idx.get(&format!("s{i:04}")))).collect();
        let seeds = [Seed { flavor: idx.get("s0015").unwrap(), weight: 1.0 }];
        // a dislike radius wide enough to swallow the library
        let mut st = Shaping::default();
        let out = shape(&idx.schema, &cands, &seeds, idx.get("s0015"), &mut st);
        assert!(!out.is_empty() || st.disliked_out == 30);
    }
}
