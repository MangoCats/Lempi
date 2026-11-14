//! The real Director, run forward over a copy of a node's pair, with and
//! without the Christmas reserve `[SPEC-XMR-100]`.
//!
//! SPEC078's estimate was a model of Stage A written in Python, with Stage B
//! and flow stood in for by a calibration. This asks the Director itself.
//! From now to the end of `--until`, one pick at a time:
//!
//! - each pick is chosen at the moment it starts, by `decide` -- or by
//!   `decide_relaxed` when nothing is eligible, as the session does;
//! - it is then marked and settled as a play at that moment, exactly as a
//!   passage that plays through is, and the clock moves on by its length;
//! - the last `queue_depth` picks are kept out of the next, as the queue
//!   keeps what it holds out of a refill.
//!
//! Nothing is skipped, nobody queues anything by hand, and nothing is
//! written: the Director holds its history in memory once loaded, and the
//! pair is opened once, read-only, through `Library`. Until 2026-11-13 the
//! player's settings came through a `PlayerStore`, which opens read-write and
//! makes its tables on an older listener -- so a run could change the copy it
//! measured. It still opens the files through SQLite, so give it copies, not a
//! node's live pair; and a copy made by SQLite's backup, never a file copy.
//!
//! The command line is `cli::specs::seasonsim`; `seasonsim --help` prints it.

use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::time::Instant;

use lempi_player::cli::specs::seasonsim as opt;
use lempi_player::db::Library;
use lempi_player::director::christmas::Place;
use lempi_player::director::library::{Director, Rng};
use lempi_player::director::occasion::civil_from_unix;

/// One local day of one run.
#[derive(Default, Clone)]
struct Day {
    picks: usize,
    christmas: usize,
    relaxed: usize,
    fillers: usize,
    ramp: f64,
    per: HashMap<i64, usize>,
}

/// Days since 1970-01-01 of a civil date, Howard Hinnant's `days_from_civil`:
/// the inverse of the `civil_from_unix` the Director uses.
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn parse_date(s: &str) -> Option<(i64, i64, i64)> {
    let mut it = s.split('-').map(|p| p.parse::<i64>().ok());
    let (y, m, d) = (it.next()??, it.next()??, it.next()??);
    ((1..=12).contains(&m) && (1..=31).contains(&d)).then_some((y, m, d))
}

fn main() {
    let args = opt::SPEC.parse();
    let db = PathBuf::from(args.need(&opt::LISTENER));
    let library = PathBuf::from(args.need(&opt::LIBRARY));
    let Some((y, m, d)) = parse_date(args.need(&opt::UNTIL)) else {
        opt::SPEC.fail("--until must be YYYY-MM-DD");
    };
    let runs = args.must_size(&opt::RUNS).max(1);
    let seed = args.int(&opt::SEED).unwrap_or(1);
    let modes: Vec<bool> = match args.need(&opt::RESERVE) {
        "on" => vec![true],
        "off" => vec![false],
        "both" => vec![false, true],
        _ => opt::SPEC.fail("--reserve is on, off or both"),
    };

    let lib = Library::open_split(&db, &library).unwrap_or_else(|e| {
        eprintln!("cannot open {} with {}: {e:?}", db.display(), library.display());
        std::process::exit(1);
    });
    // What the session would hand its Director from the player's own
    // settings: the windows, the tolerance, a held slot, the queue depth.
    // Read through the same read-only connection.
    let settings = lib.load_settings().unwrap_or_default();
    let tolerance = lib.load_profanity_tolerance();
    let manual = lib.load_manual_slot();
    let depth = settings.queue_depth.max(1);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);

    for on in modes {
        let mut totals: BTreeMap<(i32, u32, u32), Vec<Day>> = BTreeMap::new();
        let mut pick_ms: Vec<f64> = Vec::new();
        for run in 0..runs {
            let t0 = Instant::now();
            let mut dir: Director = lib.director().unwrap_or_else(|e| {
                eprintln!("director load failed: {e:?}");
                std::process::exit(1);
            });
            let loaded = t0.elapsed();
            dir.set_suppress_h((settings.skip_suppress_h, settings.dequeue_suppress_h));
            dir.set_profanity_tolerance(tolerance);
            dir.programme_mut().set_manual_slot(manual);
            dir.set_christmas_reserve(on);
            let offset = dir.programme().utc_offset_minutes * 60;
            let end = (days_from_civil(y, m, d) + 1) * 86_400 - offset;
            if run == 0 {
                println!(
                    "reserve {}: {} radio passages, {} Christmas, offset {:+} min, queue depth {depth}, \
                     loaded in {:.1} s; {} run(s) to {y:04}-{m:02}-{d:02}",
                    if on { "ON" } else { "OFF" },
                    dir.len(),
                    dir.christmas_count(),
                    offset / 60,
                    loaded.as_secs_f64(),
                    runs
                );
            }

            let mut rng = Rng::seeded(seed.wrapping_add(run as u64 * 7919));
            let mut recent: Vec<i64> = Vec::new();
            let mut days: BTreeMap<(i32, u32, u32), Day> = BTreeMap::new();
            let mut t = now;
            while t < end {
                let tp = Instant::now();
                let tail = recent.last().copied();
                let (decision, relaxed) = match dir.decide(t, &mut rng, &recent, tail) {
                    Some(x) => (Some(x), false),
                    None => (dir.decide_relaxed(t, &mut rng, &recent), true),
                };
                pick_ms.push(tp.elapsed().as_secs_f64() * 1000.0);
                let Some(decision) = decision else {
                    eprintln!("nothing to choose at {t}; stopping this run");
                    break;
                };
                let pid = decision.entry.passage_id;
                let day = civil_from_unix(dir.programme().local_secs(t));
                let e = days.entry(day).or_default();
                e.picks += 1;
                e.relaxed += usize::from(relaxed);
                e.ramp = e.ramp.max(decision.why.christmas.ramp);
                if decision.why.place == Place::Filler {
                    e.fillers += 1;
                }
                if dir.is_christmas(pid) {
                    e.christmas += 1;
                    *e.per.entry(pid).or_default() += 1;
                }
                if let Some(note) = dir.note_queued(pid, t) {
                    dir.settle(note);
                }
                recent.push(pid);
                if recent.len() > depth {
                    recent.remove(0);
                }
                t += (dir.passage_ms(pid).unwrap_or(180_000) / 1000).max(1) as i64;
            }
            for (k, v) in days {
                totals.entry(k).or_default().push(v);
            }
        }

        println!("\nreserve {}  --  mean over {runs} run(s)", if on { "ON" } else { "OFF" });
        println!("date        ramp  picks  xmas   share  distinct  max/song  fillers  relaxed"); // columns
        let mut dec = (0.0, 0.0);
        for ((yy, mm, dd), v) in &totals {
            let n = v.len() as f64;
            let mean = |f: &dyn Fn(&Day) -> f64| v.iter().map(f).sum::<f64>() / n;
            let picks = mean(&|x| x.picks as f64);
            let xmas = mean(&|x| x.christmas as f64);
            if *mm == 12 {
                dec.0 += picks;
                dec.1 += xmas;
            }
            // Every day of December; before it, every fifth.
            if *mm != 12 && *dd % 5 != 0 {
                continue;
            }
            println!(
                "{yy:04}-{mm:02}-{dd:02}  {:4.2}  {:5.0}  {:5.1}  {:5.1}%  {:6.1}  {:6.1}  {:6.1}  {:6.1}",
                mean(&|x| x.ramp),
                picks,
                xmas,
                if picks > 0.0 { 100.0 * xmas / picks } else { 0.0 },
                mean(&|x| x.per.len() as f64),
                mean(&|x| x.per.values().copied().max().unwrap_or(0) as f64),
                mean(&|x| x.fillers as f64),
                mean(&|x| x.relaxed as f64),
            );
        }
        if dec.0 > 0.0 {
            println!("December: {:.0} Christmas of {:.0} picks = {:.1}%", dec.1, dec.0, 100.0 * dec.1 / dec.0);
        }
        pick_ms.sort_by(f64::total_cmp);
        if !pick_ms.is_empty() {
            println!(
                "pick time: median {:.1} ms, 99th percentile {:.1} ms, over {} picks",
                pick_ms[pick_ms.len() / 2],
                pick_ms[pick_ms.len() * 99 / 100],
                pick_ms.len()
            );
        }
    }
}
