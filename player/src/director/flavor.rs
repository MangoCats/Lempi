//! Flavor distance `[SPEC005]` — how alike two passages sound.
//!
//! Partial data degrades rather than disqualifies `[REQ-PD-160]`: a passage
//! with 11 known characteristics stays selectable beside one with 71, because
//! aggregation divides by what is actually known.
//!
//! Per characteristic the distance is **total variation**, `½·Σ|a−b|`, which
//! unifies binary and complex characteristics without a fudge factor: for K=2
//! it reduces exactly to `|Δp|`, for K≥3 it is the probability mass that must
//! move `[SPEC-FD-030]`. Aggregation divides each by its measured natural
//! scale `β_c`, weights by measured reliability `w_c`, and normalises by the
//! weight actually used `[SPEC-FD-040]`.
//!
//! Only characteristics **present in both** vectors take part. Partial vectors
//! are normal, and assuming a missing characteristic is zero would read
//! "unmeasured" as "maximally different" `[MFL-DIST-010]`.
//!
//! Vectors are stored flat with a presence bitmask rather than as maps: Stage B
//! weighs the whole library against every seed, so this runs tens of thousands
//! of times per selection and a `HashMap` lookup per class would dominate.

use std::collections::HashMap;

use crate::db::{DbError, QualifyingConn};

/// Classes must sum to 1.0 within this `[SPEC-FD-100]`.
const SUM_TOLERANCE: f64 = 1e-4;

/// Presence is a `u64` bitmask, so this is the ceiling on distinct
/// characteristics. AcousticBrainz supplies 18; the rest is headroom for
/// user-defined ones `[SPEC-FD-110]`.
pub const MAX_CHARACTERISTICS: usize = 64;

/// Defaults for a characteristic with no measured constants — user-defined
/// ones, which cannot be measured from AcousticBrainz submissions
/// `[SPEC-FD-110]`. `β` is estimated from the library instead; see
/// [`FlavorSchema::estimate_missing_beta`].
const DEFAULT_WEIGHT: f64 = 1.0;

/// The characteristics, their class layout, and their corpus constants.
///
/// Built once per library. `β_c` and `w_c` are corpus constants computed per
/// flavor source and stored, never recomputed per query `[SPEC-FD-090]`.
pub struct FlavorSchema {
    names: Vec<String>,
    index: HashMap<String, usize>,
    /// Class name → position within the characteristic's slice.
    class_index: Vec<HashMap<String, usize>>,
    /// Start of each characteristic's slice in the flat value vector.
    offset: Vec<usize>,
    width: Vec<usize>,
    beta: Vec<f64>,
    weight: Vec<f64>,
    total: usize,
}

impl FlavorSchema {
    pub fn characteristic_count(&self) -> usize {
        self.names.len()
    }
    pub fn name(&self, c: usize) -> &str {
        &self.names[c]
    }
    pub fn beta(&self, c: usize) -> f64 {
        self.beta[c]
    }
    pub fn weight(&self, c: usize) -> f64 {
        self.weight[c]
    }
    pub fn index_of(&self, name: &str) -> Option<usize> {
        self.index.get(name).copied()
    }
    /// How many classes this characteristic has. Two for `danceability`, ten
    /// for `genre_dortmund`.
    pub fn width(&self, c: usize) -> usize {
        self.width[c]
    }
    /// The name of one class, for saying what a vector *means* rather than
    /// what it measures `[SPEC-MPD-050]`.
    pub fn class_name(&self, c: usize, k: usize) -> Option<&str> {
        self.class_index[c]
            .iter()
            .find(|(_, &pos)| pos == k)
            .map(|(name, _)| name.as_str())
    }
}

/// One subject's flavor: a flat vector plus which characteristics are present.
#[derive(Clone, Debug, PartialEq)]
pub struct Flavor {
    values: Vec<f32>,
    present: u64,
}

impl Flavor {
    pub fn is_empty(&self) -> bool {
        self.present == 0
    }
    pub fn has(&self, c: usize) -> bool {
        c < MAX_CHARACTERISTICS && self.present & (1u64 << c) != 0
    }
    pub fn present_count(&self) -> u32 {
        self.present.count_ones()
    }

    /// The likeliest class of one characteristic, as `(class index, value)`.
    pub fn top_class(&self, schema: &FlavorSchema, c: usize) -> Option<(usize, f32)> {
        if !self.has(c) {
            return None;
        }
        let (start, w) = (schema.offset[c], schema.width(c));
        self.values
            .get(start..start + w)?
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.total_cmp(b.1))
            .map(|(k, v)| (k, *v))
    }

    /// A short human reading of this vector — `danceable · rock · male`.
    ///
    /// For publishing to clients that can show a string and nothing else
    /// `[SPEC-MPD-050]`. Only classes that actually won their characteristic
    /// appear: a ten-class genre whose best guess is 0.2 says nothing worth
    /// printing, and printing it anyway would dress a shrug as a finding.
    pub fn summary(&self, schema: &FlavorSchema, max_terms: usize) -> String {
        let mut terms: Vec<(f32, String)> = (0..schema.characteristic_count())
            .filter_map(|c| {
                let (k, v) = self.top_class(schema, c)?;
                if v < 0.5 {
                    return None;
                }
                Some((v, schema.class_name(c, k)?.replace('_', " ")))
            })
            .collect();
        terms.sort_by(|a, b| b.0.total_cmp(&a.0));
        terms.truncate(max_terms);
        terms.into_iter().map(|(_, n)| n).collect::<Vec<_>>().join(" · ")
    }
}

/// The weighted mean of several vectors — a Taste centroid `[SPEC-DIR-150]`.
///
/// Averaging distributions class-by-class yields a distribution, so a centroid
/// is an ordinary flavor vector and takes part in [`distance`] unchanged. A
/// characteristic is present if **any** member has it, and averages only over
/// those that do: a member missing a characteristic should not drag it toward
/// zero, which would read "unmeasured" as "none of this".
pub fn centroid(schema: &FlavorSchema, members: &[(&Flavor, f64)]) -> Option<Flavor> {
    if members.is_empty() {
        return None;
    }
    let mut values = vec![0.0f32; schema.total];
    let mut present = 0u64;
    for c in 0..schema.names.len() {
        let (o, w) = (schema.offset[c], schema.width[c]);
        let mut total = 0.0f64;
        let mut acc = vec![0.0f64; w];
        for (f, weight) in members {
            if !f.has(c) || *weight <= 0.0 {
                continue;
            }
            for (k, a) in acc.iter_mut().enumerate() {
                *a += f.values[o + k] as f64 * weight;
            }
            total += weight;
        }
        if total <= 0.0 {
            continue;
        }
        for k in 0..w {
            values[o + k] = (acc[k] / total) as f32;
        }
        present |= 1u64 << c;
    }
    if present == 0 {
        return None;
    }
    Some(Flavor { values, present })
}

/// Total variation between one characteristic of two vectors.
///
/// `½·Σ|a−b|`, bounded [0,1]. For K=2 this is exactly `|Δp|` — the binary case
/// counted once rather than twice `[SPEC-FD-020]`.
fn total_variation(a: &[f32], b: &[f32]) -> f64 {
    let mut sum = 0.0f64;
    for (x, y) in a.iter().zip(b.iter()) {
        sum += (*x as f64 - *y as f64).abs();
    }
    sum * 0.5
}

/// Aggregate distance `[SPEC-FD-040]`.
///
/// `None` when the vectors share no characteristic: they are not comparable,
/// which is a different statement from "maximally distant" and must not be
/// silently turned into one.
pub fn distance(schema: &FlavorSchema, a: &Flavor, b: &Flavor) -> Option<f64> {
    let shared = a.present & b.present;
    if shared == 0 {
        return None;
    }
    let mut num = 0.0f64;
    let mut den = 0.0f64;
    let mut bits = shared;
    while bits != 0 {
        let c = bits.trailing_zeros() as usize;
        bits &= bits - 1;
        let (o, w) = (schema.offset[c], schema.width[c]);
        let tv = total_variation(&a.values[o..o + w], &b.values[o..o + w]);
        let beta = schema.beta[c];
        if beta <= 0.0 {
            continue;
        }
        let wc = schema.weight[c];
        num += wc * (tv / beta);
        den += wc;
    }
    if den <= 0.0 {
        return None;
    }
    Some(num / den)
}

/// Every subject's flavor, plus the schema they share.
pub struct FlavorIndex {
    pub schema: FlavorSchema,
    by_subject: HashMap<String, Flavor>,
    /// Characteristics whose classes did not sum to 1.0 `[SPEC-FD-100]`.
    pub malformed: usize,
}

impl FlavorIndex {
    pub fn get(&self, mbid: &str) -> Option<&Flavor> {
        self.by_subject.get(mbid)
    }
    pub fn len(&self) -> usize {
        self.by_subject.len()
    }
    pub fn is_empty(&self) -> bool {
        self.by_subject.is_empty()
    }
    /// Every subject that has any flavor.
    pub fn subjects(&self) -> impl Iterator<Item = &String> {
        self.by_subject.keys()
    }
    pub fn distance(&self, a: &str, b: &str) -> Option<f64> {
        distance(&self.schema, self.get(a)?, self.get(b)?)
    }

    /// Load the library's flavor `[SPEC-FD-090]`.
    ///
    /// Absent tables are not an error — a library with no flavor simply cannot
    /// be shaped by character, and the Director falls back to frequency alone.
    pub fn load(conn: &QualifyingConn) -> Result<Self, DbError> {
        let mut constants: HashMap<String, (f64, f64)> = HashMap::new();
        if let Ok(mut stmt) =
            conn.prepare("SELECT characteristic, beta, reliability FROM flavor_constants")
        {
            if let Ok(rows) = stmt.query_map([], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, f64>(1)?, r.get::<_, f64>(2)?))
            }) {
                for (c, b, w) in rows.flatten() {
                    constants.insert(c, (b, w));
                }
            }
        }

        // First pass: discover the characteristics and classes actually
        // present. The schema is built from the DATA, not from the constants
        // table, so a characteristic with constants but no values -- as the six
        // complex ones are until Vipunen extracts them -- simply does not appear.
        let mut rows: Vec<(String, String, String, f64)> = Vec::new();
        let Ok(mut stmt) = conn.prepare(
            "SELECT subject_id, characteristic, class, value FROM __LIB__.flavor \
             WHERE subject_kind = 'recording'",
        ) else {
            return Ok(Self::empty());
        };
        if let Ok(iter) = stmt.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, String>(2)?,
                r.get::<_, f64>(3)?,
            ))
        }) {
            rows.extend(iter.flatten());
        }
        drop(stmt);
        if rows.is_empty() {
            return Ok(Self::empty());
        }

        let mut names: Vec<String> = Vec::new();
        let mut index: HashMap<String, usize> = HashMap::new();
        let mut classes: Vec<Vec<String>> = Vec::new();
        for (_, ch, cl, _) in &rows {
            let c = *index.entry(ch.clone()).or_insert_with(|| {
                names.push(ch.clone());
                classes.push(Vec::new());
                names.len() - 1
            });
            if c < MAX_CHARACTERISTICS && !classes[c].iter().any(|x| x == cl) {
                classes[c].push(cl.clone());
            }
        }
        // Beyond the bitmask width there is no room to record presence, and a
        // characteristic that cannot be marked present would silently never
        // take part. Dropping the excess is visible; half-loading is not.
        names.truncate(MAX_CHARACTERISTICS);
        classes.truncate(MAX_CHARACTERISTICS);
        index.retain(|_, v| *v < MAX_CHARACTERISTICS);

        // Class order is sorted so the layout is stable across runs -- a
        // distance that depended on row order would be irreproducible.
        for cl in classes.iter_mut() {
            cl.sort();
        }

        let mut offset = Vec::with_capacity(names.len());
        let mut width = Vec::with_capacity(names.len());
        let mut total = 0usize;
        for cl in &classes {
            offset.push(total);
            width.push(cl.len());
            total += cl.len();
        }
        let class_index: Vec<HashMap<String, usize>> = classes
            .iter()
            .map(|cl| cl.iter().enumerate().map(|(i, n)| (n.clone(), i)).collect())
            .collect();

        let beta: Vec<f64> = names
            .iter()
            .map(|n| constants.get(n).map(|c| c.0).unwrap_or(0.0))
            .collect();
        let weight: Vec<f64> = names
            .iter()
            .map(|n| constants.get(n).map(|c| c.1).unwrap_or(DEFAULT_WEIGHT))
            .collect();

        let mut schema =
            FlavorSchema { names, index, class_index, offset, width, beta, weight, total };

        // Second pass: fill the vectors.
        let mut by_subject: HashMap<String, Flavor> = HashMap::new();
        for (subject, ch, cl, v) in &rows {
            let Some(&c) = schema.index.get(ch) else { continue };
            let Some(&k) = schema.class_index[c].get(cl) else { continue };
            let f = by_subject.entry(subject.clone()).or_insert_with(|| Flavor {
                values: vec![0.0; schema.total],
                present: 0,
            });
            f.values[schema.offset[c] + k] = *v as f32;
            f.present |= 1u64 << c;
        }

        // Validate, then drop what does not sum to 1.0. A characteristic that
        // is not a distribution is not a distribution, and letting it through
        // would quietly bias every distance it takes part in [SPEC-FD-100].
        let mut malformed = 0usize;
        for f in by_subject.values_mut() {
            let mut bits = f.present;
            while bits != 0 {
                let c = bits.trailing_zeros() as usize;
                bits &= bits - 1;
                let (o, w) = (schema.offset[c], schema.width[c]);
                let sum: f64 = f.values[o..o + w].iter().map(|x| *x as f64).sum();
                if (sum - 1.0).abs() > SUM_TOLERANCE {
                    malformed += 1;
                    f.present &= !(1u64 << c);
                }
            }
        }

        schema.estimate_missing_beta(&by_subject);
        Ok(Self { schema, by_subject, malformed })
    }

    fn empty() -> Self {
        Self {
            schema: FlavorSchema {
                names: Vec::new(),
                index: HashMap::new(),
                class_index: Vec::new(),
                offset: Vec::new(),
                width: Vec::new(),
                beta: Vec::new(),
                weight: Vec::new(),
                total: 0,
            },
            by_subject: HashMap::new(),
            malformed: 0,
        }
    }
}

impl FlavorSchema {
    /// `β_c` for characteristics with no measured constant — user-defined ones
    /// `[SPEC-FD-110]` — is the observed mean between-recording total
    /// variation, estimated from a bounded sample of pairs.
    ///
    /// Sampled rather than exhaustive: the full computation is O(n²) over
    /// thousands of subjects for a constant whose third decimal place changes
    /// nothing. A characteristic that never varies gets β = 0 and is skipped by
    /// [`distance`] rather than dividing by zero.
    fn estimate_missing_beta(&mut self, subjects: &HashMap<String, Flavor>) {
        const PAIRS: usize = 4_000;
        let missing: Vec<usize> = (0..self.names.len()).filter(|c| self.beta[*c] <= 0.0).collect();
        if missing.is_empty() || subjects.len() < 2 {
            return;
        }
        let all: Vec<&Flavor> = subjects.values().collect();
        for c in missing {
            // Sample only among subjects that HAVE the characteristic. A
            // user-defined one may sit on a few dozen recordings out of
            // thousands, and drawing pairs from the whole library would almost
            // never find two that share it -- yielding no estimate at all.
            let held: Vec<&&Flavor> = all.iter().filter(|f| f.has(c)).collect();
            if held.len() < 2 {
                self.beta[c] = 0.0;
                continue;
            }
            // A fixed stride rather than a random walk: reproducible, and no
            // RNG to thread through a constant that must be identical each run.
            let stride = (held.len() / 7).max(1);
            let (o, w) = (self.offset[c], self.width[c]);
            let (mut sum, mut n) = (0.0f64, 0usize);
            let mut i = 0usize;
            while n < PAIRS && i < held.len() {
                let j = (i + stride) % held.len();
                if i != j {
                    sum += total_variation(&held[i].values[o..o + w], &held[j].values[o..o + w]);
                    n += 1;
                }
                i += 1;
            }
            // Zero is a real answer, not a failure: a characteristic that never
            // varies among those carrying it cannot discriminate between them,
            // and distance() skips it rather than dividing by zero.
            self.beta[c] = if n > 0 { sum / n as f64 } else { 0.0 };
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    // Only the fixtures open a bare connection; the module itself works
    // through `QualifyingConn`. Imported here rather than at file scope so a
    // non-test build does not carry an unused import.
    use rusqlite::Connection;

    fn fixture() -> QualifyingConn {
        let c = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        c.execute_batch(
            "CREATE TABLE flavor (subject_kind TEXT, subject_id TEXT, characteristic TEXT,
                 class TEXT, value REAL, source TEXT, accuracy REAL);
             CREATE TABLE flavor_constants (characteristic TEXT PRIMARY KEY, beta REAL,
                 reliability REAL, measured_on TEXT, measured_at TEXT);
             INSERT INTO flavor_constants VALUES ('mood_happy', 0.2867, 0.568, 'lib', 't');
             INSERT INTO flavor_constants VALUES ('genre_x', 0.5000, 0.700, 'lib', 't');",
        )
        .unwrap();
        c
    }

    fn add(c: &Connection, subject: &str, ch: &str, vals: &[(&str, f64)]) {
        for (cl, v) in vals {
            c.execute(
                "INSERT INTO flavor VALUES ('recording',?1,?2,?3,?4,'test',NULL)",
                rusqlite::params![subject, ch, cl, v],
            )
            .unwrap();
        }
    }

    /// For a binary characteristic total variation must equal |Δp| exactly --
    /// not 2|Δp|, which is what raw Euclidean over both classes gives
    /// [SPEC-FD-020].
    #[test]
    fn binary_total_variation_is_the_probability_difference() {
        let a = [0.8f32, 0.2];
        let b = [0.3f32, 0.7];
        // 1e-6, not 1e-9: values are stored f32 to keep the library's vectors
        // small, and 0.8f32 - 0.3f32 lands about 1e-8 from the exact answer.
        assert!((total_variation(&a, &b) - 0.5).abs() < 1e-6);
    }

    #[test]
    fn total_variation_is_bounded_symmetric_and_zero_on_identity() {
        let a = [1.0f32, 0.0, 0.0];
        let b = [0.0f32, 0.0, 1.0];
        assert!((total_variation(&a, &b) - 1.0).abs() < 1e-9, "disjoint distributions are 1.0");
        assert_eq!(total_variation(&a, &b), total_variation(&b, &a));
        assert_eq!(total_variation(&a, &a), 0.0);
    }

    #[test]
    fn a_characteristic_is_scaled_by_beta_and_weighted_by_reliability() {
        let c = fixture();
        add(&c, "a", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
        add(&c, "b", "mood_happy", &[("happy", 0.0), ("not_happy", 1.0)]);
        let idx = FlavorIndex::load(&c).unwrap();
        // one characteristic: weights cancel, leaving TV/beta = 1.0 / 0.2867
        let d = idx.distance("a", "b").unwrap();
        assert!((d - 1.0 / 0.2867).abs() < 1e-6, "distance {d}");
    }

    /// Only characteristics present in BOTH take part. A missing one must not
    /// be read as zero, which would say "unmeasured" means "identical"
    /// [SPEC-FD-040].
    #[test]
    fn only_shared_characteristics_take_part() {
        let c = fixture();
        add(&c, "a", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
        add(&c, "a", "genre_x", &[("p", 1.0), ("q", 0.0)]);
        add(&c, "b", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
        let idx = FlavorIndex::load(&c).unwrap();
        // identical on the only shared characteristic
        assert_eq!(idx.distance("a", "b").unwrap(), 0.0);
        assert_eq!(idx.get("a").unwrap().present_count(), 2);
        assert_eq!(idx.get("b").unwrap().present_count(), 1);
    }

    #[test]
    fn vectors_sharing_nothing_are_not_comparable() {
        let c = fixture();
        add(&c, "a", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
        add(&c, "b", "genre_x", &[("p", 1.0), ("q", 0.0)]);
        let idx = FlavorIndex::load(&c).unwrap();
        assert!(idx.distance("a", "b").is_none(), "no shared characteristic is None, not 0.0");
    }

    /// Normalising by the weight actually used keeps distances comparable
    /// between pairs that share different characteristics.
    #[test]
    fn distance_is_comparable_across_differing_overlaps() {
        let c = fixture();
        // a and b share both; a and d share only mood_happy. Same per-
        // characteristic difference, so the aggregate must match.
        add(&c, "a", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
        add(&c, "a", "genre_x", &[("p", 1.0), ("q", 0.0)]);
        add(&c, "b", "mood_happy", &[("happy", 0.5), ("not_happy", 0.5)]);
        add(&c, "b", "genre_x", &[("p", 0.5), ("q", 0.5)]);
        add(&c, "d", "mood_happy", &[("happy", 0.5), ("not_happy", 0.5)]);
        let idx = FlavorIndex::load(&c).unwrap();
        let ab = idx.distance("a", "b").unwrap();
        let ad = idx.distance("a", "d").unwrap();
        // both are a weighted mean of per-characteristic terms; the mood term
        // is identical in each, so ad equals the mood term alone
        assert!((ad - 0.5 / 0.2867).abs() < 1e-6, "ad {ad}");
        assert!(ab > 0.0 && ab < ad, "sharing a wider-scaled characteristic pulls the mean down");
    }

    /// A characteristic whose classes do not sum to 1.0 is not a distribution
    /// and must not quietly bias every distance it appears in.
    #[test]
    fn malformed_characteristics_are_dropped_and_counted() {
        let c = fixture();
        add(&c, "a", "mood_happy", &[("happy", 0.7), ("not_happy", 0.7)]); // sums to 1.4
        add(&c, "a", "genre_x", &[("p", 1.0), ("q", 0.0)]);
        add(&c, "b", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
        add(&c, "b", "genre_x", &[("p", 1.0), ("q", 0.0)]);
        let idx = FlavorIndex::load(&c).unwrap();
        assert_eq!(idx.malformed, 1);
        assert!(!idx.get("a").unwrap().has(idx.schema.index_of("mood_happy").unwrap()));
        // genre_x survives and is identical, so the pair is distance zero
        assert_eq!(idx.distance("a", "b").unwrap(), 0.0);
    }

    /// A user-defined characteristic has no measured constants, so beta is
    /// estimated from the library and reliability defaults to 1.0
    /// [SPEC-FD-110].
    #[test]
    fn user_characteristics_get_an_estimated_scale() {
        let c = fixture();
        for i in 0..20 {
            let v = if i % 2 == 0 { 1.0 } else { 0.0 };
            add(&c, &format!("s{i}"), "user.x", &[("yes", v), ("no", 1.0 - v)]);
        }
        let idx = FlavorIndex::load(&c).unwrap();
        let ci = idx.schema.index_of("user.x").unwrap();
        assert_eq!(idx.schema.weight(ci), DEFAULT_WEIGHT);
        assert!(idx.schema.beta(ci) > 0.0, "beta must be estimated, not left at zero");
        assert!(idx.schema.beta(ci) <= 1.0);
    }

    /// A characteristic with constants but NO data must simply not appear --
    /// the six complex ones are in this state until Vipunen extracts them.
    #[test]
    fn constants_without_data_create_no_characteristic() {
        let c = fixture();
        add(&c, "a", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
        let idx = FlavorIndex::load(&c).unwrap();
        assert_eq!(idx.schema.characteristic_count(), 1);
        assert!(idx.schema.index_of("genre_x").is_none(), "constants alone are not a dimension");
    }

    #[test]
    fn an_absent_flavor_table_is_not_an_error() {
        let c = QualifyingConn::wrap_unsplit(Connection::open_in_memory().unwrap());
        let idx = FlavorIndex::load(&c).unwrap();
        assert!(idx.is_empty());
        assert_eq!(idx.schema.characteristic_count(), 0);
    }

    /// Class order must not depend on the order rows came back, or a distance
    /// would differ between runs.
    #[test]
    fn class_layout_is_stable_regardless_of_row_order() {
        let mk = |reversed: bool| {
            let c = fixture();
            let v = [("happy", 0.25), ("not_happy", 0.75)];
            let mut v = v.to_vec();
            if reversed {
                v.reverse();
            }
            add(&c, "a", "mood_happy", &v);
            add(&c, "b", "mood_happy", &[("happy", 1.0), ("not_happy", 0.0)]);
            FlavorIndex::load(&c).unwrap().distance("a", "b").unwrap()
        };
        assert!((mk(false) - mk(true)).abs() < 1e-12);
    }
}
