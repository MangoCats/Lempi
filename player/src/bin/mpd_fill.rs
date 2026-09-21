//! Stage 2: enqueue passages by span, without the Director `[IMPL-MPD-030]`.
//!
//! The command line is `cli::specs::mpd_fill`; `mpd_fill --help` prints it.
//!
//! **The selector is deliberately stupid** — uniform random over resolvable
//! passages. That is the point: a failure here is a *protocol* failure and not
//! a Director one, and `rangeid` is the single most load-bearing assumption in
//! SPEC015 `[GDE-BAK-035]`. Proving it under a trivial picker proves it.
//!
//! What this does **not** do: remove, reorder, or replace anything. The queue
//! belongs to whoever is in front of it `[SPEC-MPD-095]`; the Director tops it
//! up to a depth and otherwise keeps its hands still. Twenty tracks added by a
//! person means nothing to add, and that is the correct behaviour rather than a
//! deferral.

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use rusqlite::Connection;
use lempi_player::mpd::Mpd;

/// A passage the filler may choose, already resolved to MPD's terms.
#[derive(Clone)]
struct Pick {
    passage_id: i64,
    uri: String,
    start_ms: i64,
    end_ms: i64,
    title: String,
}

impl Pick {
    fn span_s(&self) -> f64 {
        (self.end_ms - self.start_ms) as f64 / 1000.0
    }
}

/// Quote a value for the MPD protocol: wrap in `"`, backslash-escape `"` and `\`.
fn quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        if c == '"' || c == '\\' {
            out.push('\\');
        }
        out.push(c);
    }
    out.push('"');
    out
}

/// Every passage whose file resolves to a URI under `root` — rung 1 of the
/// ladder `[SPEC-MPD-060]`, which stage 0 measured at 100% same-platform.
fn load(db_path: &str, root: &str) -> Result<Vec<Pick>, String> {
    let db = Connection::open(db_path).map_err(|e| format!("{db_path}: {e}"))?;
    let root = root.replace('\\', "/").trim_end_matches('/').to_string();
    let mut q = db
        .prepare(
            "SELECT p.passage_id, f.path, p.start_ms, p.end_ms, \
                    COALESCE((SELECT r.title FROM passage_recordings pr \
                              JOIN recordings r ON r.mbid = pr.mbid \
                              WHERE pr.passage_id = p.passage_id LIMIT 1), '') \
             FROM passages p JOIN files f USING(file_id) \
             WHERE p.kind = 'radio' AND p.end_ms > p.start_ms",
        )
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| {
            Ok((
                r.get::<_, i64>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, i64>(3)?,
                r.get::<_, String>(4)?,
            ))
        })
        .map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for (passage_id, path, start_ms, end_ms, title) in rows.flatten() {
        let norm = path.replace('\\', "/");
        if let Some(rel) = norm.strip_prefix(&root) {
            out.push(Pick {
                passage_id,
                uri: rel.trim_start_matches('/').to_string(),
                start_ms,
                end_ms,
                title,
            });
        }
    }
    Ok(out)
}

fn parse(lines: &[String]) -> HashMap<String, String> {
    lines
        .iter()
        .filter_map(|l| l.split_once(": "))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

/// Enough randomness to pick a song. Not enough to guard anything.
struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        // xorshift64*, deterministic under --seed so a run can be repeated.
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: usize) -> usize {
        (self.next() % n.max(1) as u64) as usize
    }
}

fn main() {
    use lempi_player::cli::specs::mpd_fill as opt;
    let mut args = opt::SPEC.parse();
    let db_path = args.need(&opt::LISTENER).to_string();
    let root = args.need(&opt::ROOT).to_string();
    // `[SPEC-MPD-095]`: at or above the depth, the Director adds nothing.
    let run_for: Option<f64> = args.real(&opt::RUN_FOR);
    let seed: u64 = args.must_int(&opt::SEED);
    // The listener's settings own these `[SPEC-MPD-105]`; the flags override.
    // Neither precedence nor the fallback is written here any more -- the
    // options declare `.setting(..)` and the chain does it `[GDE-CLI-090]`.
    if let Some(saved) = lempi_player::db::PlayerStore::open(std::path::Path::new(&db_path))
        .ok()
        .and_then(|s| s.load_settings())
    {
        for complaint in args.with_settings(|k| saved.value_of(k)) {
            eprintln!("mpd_fill: {complaint}");
        }
    }
    let depth: usize = args.must_size(&opt::DEPTH);
    let interval: f64 = args.must_real(&opt::INTERVAL);
    let addr = args.need(&opt::ADDR).to_string();

    let picks = load(&db_path, &root).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    if picks.is_empty() {
        eprintln!("no radio passages resolve under {root}");
        std::process::exit(1);
    }

    let mut mpd = Mpd::connect(&addr).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    println!("MPD protocol {} at {addr}", mpd.version);
    println!(
        "{} resolvable radio passage(s); depth {depth}, sampling {interval}s",
        picks.len()
    );

    // `consume 1` is what makes the queue a stream rather than a playlist: a
    // played passage leaves, so "top up to depth" is a stable statement.
    if let Err(e) = mpd.cmd("consume 1") {
        eprintln!("consume: {e}");
        std::process::exit(1);
    }
    println!("consume 1 set; nothing is ever removed or reordered by this program\n");

    let mut rng = Rng(seed);
    let started = Instant::now();
    // songid -> the passage we asked for, so a disappearance can be attributed.
    let mut ours: HashMap<String, Pick> = HashMap::new();
    let mut added = 0u32;
    let mut truncated = 0u32;
    let mut last_len: Option<usize> = None;

    loop {
        let status = match mpd.cmd("status") {
            Ok(l) => parse(&l),
            Err(e) => {
                eprintln!("status: {e}");
                break;
            }
        };
        let len: usize = status.get("playlistlength").and_then(|v| v.parse().ok()).unwrap_or(0);
        // `[SPEC-MPD-120]`: the Director is active **only while playing**. A
        // stopped or paused player is a person with their hands on the queue,
        // and appending to it then is the fight the rule exists to prevent.
        let active = status.get("state").map(|s| s == "play").unwrap_or(false);

        if last_len != Some(len) {
            if let Some(prev) = last_len {
                let word = if len > prev { "grew" } else { "shrank" };
                println!("queue {word} {prev} -> {len}");
            }
            last_len = Some(len);
        }

        if !active || len >= depth {
            // The etiquette, and the whole of it: at or above depth, do nothing.
            if run_for.is_some_and(|s| started.elapsed().as_secs_f64() >= s) {
                break;
            }
            std::thread::sleep(Duration::from_secs_f64(interval));
            continue;
        }

        // Do not offer a passage the queue already holds. Locally this is the
        // exclusion set `[SPEC-DIR-160]`; here it is just "don't repeat".
        let queued: HashSet<i64> = ours.values().map(|p| p.passage_id).collect();
        for _ in 0..(depth - len) {
            let mut chosen = None;
            for _ in 0..64 {
                let c = &picks[rng.below(picks.len())];
                if !queued.contains(&c.passage_id) {
                    chosen = Some(c.clone());
                    break;
                }
            }
            let Some(p) = chosen else { break };

            let id = match mpd.cmd(&format!("addid {}", quote(&p.uri))) {
                Ok(l) => parse(&l).get("Id").cloned(),
                Err(e) => {
                    println!("  addid refused ({e}) for {}", p.uri);
                    continue;
                }
            };
            let Some(id) = id else {
                println!("  addid returned no Id for {}", p.uri);
                continue;
            };
            // The load-bearing call. Seconds, not milliseconds.
            let range = format!(
                "rangeid {id} {:.3}:{:.3}",
                p.start_ms as f64 / 1000.0,
                p.end_ms as f64 / 1000.0
            );
            if let Err(e) = mpd.cmd(&range) {
                // A file named without its span plays the whole capture -- forty
                // songs where one was wanted. Better to withdraw the add.
                println!("  rangeid refused ({e}); removing id {id} rather than play the whole file");
                let _ = mpd.cmd(&format!("deleteid {id}"));
                continue;
            }
            // **`rangeid` can succeed and still not take.** MPD compares the
            // requested end against *its own* duration -- an estimate, and one
            // this library disagrees with on 36.9% of files `[SPEC-MPD-092]`.
            // Where the end exceeds that estimate MPD silently drops it, reports
            // a shortened `Time`, and then plays to EOF anyway: measured at 508
            // of 7,994 passages, median overrun 11.2 s and worst 532 s. An `OK`
            // is not evidence the span landed, so read it back `[GOV-SRC-030]`.
            let honoured = mpd
                .cmd(&format!("playlistid {id}"))
                .ok()
                .and_then(|l| parse(&l).get("Time").and_then(|t| t.parse::<f64>().ok()))
                .map(|t| (t - p.span_s()).abs() <= 1.5);
            match honoured {
                Some(false) | None => {
                    truncated += 1;
                    println!(
                        "  ! span NOT honoured for [{}] ({:.1}s asked): MPD's duration estimate is short, \
                         it will play past the passage end",
                        p.passage_id,
                        p.span_s()
                    );
                }
                Some(true) => {}
            }
            println!(
                "  + [{:>6}] {:>7.1}s  {}  ({})",
                p.passage_id,
                p.span_s(),
                if p.title.is_empty() { p.uri.rsplit('/').next().unwrap_or(&p.uri) } else { &p.title },
                id
            );
            ours.insert(id, p);
            added += 1;
        }
        last_len = None; // re-read rather than assume the add landed

        if run_for.is_some_and(|s| started.elapsed().as_secs_f64() >= s) {
            break;
        }
        std::thread::sleep(Duration::from_secs_f64(interval));
    }

    println!("\nadded {added} passage(s); removed 0, reordered 0");
    if truncated > 0 {
        println!(
            "{truncated} span(s) MPD would not honour; those play past the passage end \
             `[SPEC-MPD-096]`"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoting_escapes_what_the_protocol_would_misread() {
        assert_eq!(quote("a/b.mp3"), "\"a/b.mp3\"");
        assert_eq!(quote("Guns N\" Roses"), "\"Guns N\\\" Roses\"");
        assert_eq!(quote("a\\b"), "\"a\\\\b\"");
    }

    #[test]
    fn a_span_is_the_passage_not_the_file() {
        let p = Pick {
            passage_id: 1,
            uri: "x.mp3".into(),
            start_ms: 440_400,
            end_ms: 698_300,
            title: String::new(),
        };
        // The capture behind this is 10047 s; the span is what must play.
        assert!((p.span_s() - 257.9).abs() < 0.001);
    }

    #[test]
    fn the_rng_is_deterministic_under_a_seed() {
        let a: Vec<usize> = (0..5).map(|_| Rng(7).below(100)).collect();
        assert!(a.iter().all(|v| *v == a[0]), "same seed must give the same pick");
        let mut r = Rng(7);
        let seq: Vec<usize> = (0..5).map(|_| r.below(1000)).collect();
        assert!(seq.iter().any(|v| *v != seq[0]), "a sequence must still vary");
    }
}
