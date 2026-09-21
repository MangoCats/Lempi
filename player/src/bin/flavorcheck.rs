//! What flavor distance actually does on a real library.
//!
//! Not a test — a measurement. It reports the schema it found, the distance
//! distribution, and nearest/farthest neighbours for a few recordings, because
//! `[SPEC-FD-070]` is explicit that the retrieval tests validate *robustness*
//! and not perceptual similarity. Reading actual neighbour lists is the only
//! check available on that until `[SPEC-FD-080]` can run.
//!
//! The command line is `cli::specs::flavorcheck`; `flavorcheck --help` prints it.

use std::path::PathBuf;
use std::time::Instant;

use lempi_player::cli::specs::flavorcheck as opt;
use lempi_player::db::QualifyingConn;
use lempi_player::director::flavor::FlavorIndex;

fn main() {
    let args = opt::SPEC.parse();
    let db = PathBuf::from(args.need(&opt::LIBRARY));
    let samples: usize = args.must_size(&opt::SAMPLES);

    // Single path, `db` playing both roles: this tool measures whatever file
    // it's pointed at, split or not -- point it straight at `library.db` on
    // an installation that has split, since `flavor` lives there.
    let conn = match QualifyingConn::open(&db, &db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("open: {e}");
            std::process::exit(1);
        }
    };

    let t0 = Instant::now();
    let idx = match FlavorIndex::load(&conn) {
        Ok(i) => i,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    };
    println!("loaded {} subjects in {:.2}s", idx.len(), t0.elapsed().as_secs_f64());
    println!("malformed characteristic instances: {} [SPEC-FD-100]", idx.malformed);
    println!("\ncharacteristics in use:");
    println!("  {:<24} {:>8} {:>8}", "name", "beta", "weight");
    for c in 0..idx.schema.characteristic_count() {
        println!(
            "  {:<24} {:>8.4} {:>8.4}",
            idx.schema.name(c),
            idx.schema.beta(c),
            idx.schema.weight(c)
        );
    }

    // Titles, so a neighbour list can be read rather than merely counted.
    let mut title = std::collections::HashMap::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT r.mbid, r.title, a.name FROM recordings r \
         LEFT JOIN recording_artists ra ON ra.mbid = r.mbid \
         LEFT JOIN artists a ON a.mbid = ra.artist_mbid",
    ) {
        if let Ok(rows) = stmt.query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, Option<String>>(1)?.unwrap_or_default(),
                r.get::<_, Option<String>>(2)?.unwrap_or_default(),
            ))
        }) {
            for (m, t, a) in rows.flatten() {
                title.insert(m, format!("{a} — {t}"));
            }
        }
    }

    let ids: Vec<String> = {
        let mut v: Vec<String> = idx.subjects().cloned().collect();
        v.sort();
        v
    };
    if ids.len() < 2 {
        println!("\nnot enough flavor to compare");
        return;
    }

    // Distance distribution over a strided sample of pairs.
    let stride = (ids.len() / 7).max(1);
    let mut ds: Vec<f64> = Vec::new();
    let mut incomparable = 0usize;
    let t1 = Instant::now();
    for i in 0..ids.len().min(samples) {
        let j = (i * stride + 1) % ids.len();
        if i == j {
            continue;
        }
        match idx.distance(&ids[i], &ids[j]) {
            Some(d) => ds.push(d),
            None => incomparable += 1,
        }
    }
    ds.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let pct = |p: f64| ds[((ds.len() - 1) as f64 * p) as usize];
    println!(
        "\n{} pairs in {:.0} ms | incomparable {}",
        ds.len(),
        t1.elapsed().as_secs_f64() * 1000.0,
        incomparable
    );
    if !ds.is_empty() {
        println!(
            "distance  min {:.3}  p05 {:.3}  median {:.3}  p95 {:.3}  max {:.3}",
            ds[0],
            pct(0.05),
            pct(0.50),
            pct(0.95),
            ds[ds.len() - 1]
        );
    }

    // Nearest and farthest for a handful of recordings -- the only perceptual
    // check available before [SPEC-FD-080].
    let t2 = Instant::now();
    let mut compared = 0usize;
    for probe in ids.iter().step_by(ids.len() / 3).take(3) {
        let mut scored: Vec<(f64, &String)> = ids
            .iter()
            .filter(|o| *o != probe)
            .filter_map(|o| idx.distance(probe, o).map(|d| (d, o)))
            .collect();
        compared += scored.len();
        scored.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
        let name = |m: &String| title.get(m).cloned().unwrap_or_else(|| m.clone());
        println!("\n{}", name(probe));
        for (d, m) in scored.iter().take(3) {
            println!("   near  {d:.3}  {}", name(m));
        }
        for (d, m) in scored.iter().rev().take(1) {
            println!("   far   {d:.3}  {}", name(m));
        }
    }
    println!(
        "\n{compared} distances in {:.0} ms ({:.1} us each)",
        t2.elapsed().as_secs_f64() * 1000.0,
        t2.elapsed().as_secs_f64() * 1e6 / compared.max(1) as f64
    );

    seed_separation(&conn, &idx);
}

/// `[SPEC-FD-080]` — the perceptual check the spec has been waiting on.
///
/// MuLibPlay's eight programmes each carry hand-picked seeds: direct human
/// judgments that these songs belong together. A sound metric should place
/// same-programme seeds closer than cross-programme ones. Fifty recordings is a
/// small sample, but it is genuine perceptual signal from the actual listener,
/// and it is the only such signal available.
fn seed_separation(conn: &rusqlite::Connection, idx: &FlavorIndex) {
    use lempi_player::director::program::Programs;

    let Ok(programs) = Programs::load(conn) else { return };
    if programs.is_empty() {
        return;
    }
    // All seeds, not the down-selected five: this measures the metric against
    // the listener's judgments, and every seed is one of those judgments.
    let mut seeds: Vec<(i64, String, String)> = Vec::new();
    if let Ok(mut stmt) = conn.prepare(
        "SELECT s.program_id, s.mbid, COALESCE(r.title,'') FROM listener_program_seeds s \
         LEFT JOIN recordings r ON r.mbid = s.mbid",
    ) {
        if let Ok(rows) = stmt.query_map([], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, String>(1)?, r.get::<_, String>(2)?))
        }) {
            seeds.extend(rows.flatten());
        }
    }
    seeds.retain(|(_, m, _)| idx.get(m).is_some());
    if seeds.len() < 4 {
        println!("\n[SPEC-FD-080] too few seeds with flavor to measure");
        return;
    }

    println!("\n=== [SPEC-FD-080] programme seed separation ===");
    println!("{} seeds with flavor across {} programmes\n", seeds.len(), programs.len());

    let (mut within, mut cross) = (Vec::new(), Vec::new());
    let mut per_program: std::collections::HashMap<i64, Vec<f64>> = Default::default();
    for i in 0..seeds.len() {
        for j in (i + 1)..seeds.len() {
            let Some(d) = idx.distance(&seeds[i].1, &seeds[j].1) else { continue };
            if seeds[i].0 == seeds[j].0 {
                within.push(d);
                per_program.entry(seeds[i].0).or_default().push(d);
            } else {
                cross.push(d);
            }
        }
    }
    let mean = |v: &Vec<f64>| if v.is_empty() { f64::NAN } else { v.iter().sum::<f64>() / v.len() as f64 };
    let (mw, mc) = (mean(&within), mean(&cross));

    println!("  {:<10} {:>6} {:>9}  vs library", "programme", "seeds", "mean d");
    let mut rows: Vec<(f64, String, usize)> = Vec::new();
    for p in programs.all() {
        let Some(ds) = per_program.get(&p.id) else { continue };
        let n = seeds.iter().filter(|s| s.0 == p.id).count();
        rows.push((mean(ds), p.name.clone(), n));
    }
    rows.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
    for (d, name, n) in &rows {
        println!("  {:<10} {:>6} {:>9.3}  {:>6.0}%", name, n, d, d / mc * 100.0);
    }

    println!("\n  within-programme mean : {mw:.4}  ({} pairs)", within.len());
    println!("  cross-programme mean  : {mc:.4}  ({} pairs)", cross.len());
    let ratio = mw / mc;
    println!("  ratio                 : {ratio:.4}");
    // A ratio below 1.0 means the listener's groupings are tighter than chance
    // -- the metric agrees with them. Above 1.0 would mean it does not.
    if ratio < 1.0 {
        println!(
            "\n  the metric agrees with the listener: same-programme seeds sit {:.1}% closer",
            (1.0 - ratio) * 100.0
        );
    } else {
        println!("\n  NO AGREEMENT: same-programme seeds are no closer than random pairs");
    }
    println!("  caveat: {} seeds over {} programmes is a small sample [SPEC-FD-080]",
             seeds.len(), rows.len());
}
