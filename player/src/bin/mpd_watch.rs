//! Stage 1: watch MPD and judge what played `[IMPL-MPD-020]`.
//!
//! The command line is `cli::specs::mpd_watch`; `mpd_watch --help` prints it.
//!
//! **Writes to nothing** — not `listener_play_history`, not the queue, not a
//! sticker. It reports what it *would* record, which is the whole of what stage
//! 1 is for.
//!
//! The rule is the scrobbling rule `[SPEC-MPD-090]`: a passage counts as played
//! once it has reached **half its length or four minutes, whichever comes
//! first**. Last.fm and ListenBrainz both use it, so Lempi's rotation ledger and
//! whatever scrobbler the listener already runs will agree about what happened
//! without either writing the other's data.
//!
//! **Minus their minimum-length exclusion**, deliberately. Last.fm ignores
//! tracks under 30 s and ListenBrainz under 5; that is an anti-spam rule for a
//! public service, and this is a private rotation ledger. The shortest radio
//! passage here is 12 s and one that played in full did play.
//!
//! Sampling is necessary rather than lazy. MPD's `idle` fires *after* a change,
//! when `status` already describes the next song, and `consume` retires a
//! skipped song exactly as it retires a finished one — so how far the outgoing
//! passage got is only knowable if someone was watching while it played.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use rusqlite::Connection;
use lempi_player::mpd::Mpd;
use lempi_player::scrobble::{counts_as_play_s, FOUR_MINUTES_MS};

/// Lempi's durations, keyed by the URI MPD would report `[SPEC-MPD-060]`.
///
/// **MPD's own `duration` is not trustworthy enough to judge against.** For an
/// MP3 without a Xing header MPD estimates it as size over bitrate, and
/// embedded cover art is part of that size — so a 12.07 s passage carrying a
/// picture was reported as 22.8 s (546659 bytes x 8 / 192000 bps). Judged
/// against that, a passage that played *in full* needed 11.4 s, reached 10.8 s at
/// its last sample, and was recorded as a skip. Lempi's duration comes from a
/// decode, so it is the one to use, and stage 2 replaces even this with the
/// passage span, which is authoritative by construction `[SPEC-DF-030]`.
fn lempi_durations(db_path: &str, root: &str) -> Result<HashMap<String, f64>, String> {
    let db = Connection::open(db_path).map_err(|e| format!("{db_path}: {e}"))?;
    let root = root.replace('\\', "/").trim_end_matches('/').to_string();
    let mut q = db
        .prepare("SELECT path, duration_ms FROM files WHERE duration_ms IS NOT NULL")
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
        .map_err(|e| e.to_string())?;
    let mut out = HashMap::new();
    for row in rows.flatten() {
        let norm = row.0.replace('\\', "/");
        if let Some(rel) = norm.strip_prefix(&root) {
            out.insert(rel.trim_start_matches('/').to_string(), row.1 as f64 / 1000.0);
        }
    }
    Ok(out)
}

/// What the sampler knows about the song currently on air.
struct Watching {
    songid: String,
    file: String,
    duration_s: f64,
    /// The furthest point observed. A seek backwards should not un-hear what
    /// was already heard, and a seek forwards is not listening — this is the
    /// closest a position-based sampler gets to either, and the reason it is an
    /// approximation is worth stating rather than hiding.
    furthest_s: f64,
    /// Whether `duration_s` came from Lempi's decode or from MPD's estimate.
    /// Printed, because a verdict resting on an estimate is a weaker claim.
    from_lempi: bool,
    samples: u32,
    first_seen: Instant,
}

fn parse(lines: &[String]) -> HashMap<String, String> {
    lines
        .iter()
        .filter_map(|l| l.split_once(": "))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn main() {
    use lempi_player::cli::specs::mpd_watch as opt;
    let args = opt::SPEC.parse();
    // Default five seconds `[SPEC-MPD-105]`, and adjustable because the right
    // value depends on how short the shortest passage is `[SPEC-MPD-110]`.
    let interval: f64 = args.must_real(&opt::INTERVAL);
    // `--for` bounds the run so it ends by returning rather than by being
    // killed, which is the only way the song still on air at the end gets
    // judged at all. A hard kill silently drops it.
    let run_for: Option<f64> = args.real(&opt::RUN_FOR);
    let addr = args.need(&opt::ADDR).to_string();
    let started = Instant::now();

    let durations = match (args.text(&opt::LIBRARY), args.text(&opt::ROOT)) {
        (Some(db), Some(root)) => lempi_durations(db, root).unwrap_or_else(|e| {
            eprintln!("{e}");
            std::process::exit(1);
        }),
        _ => HashMap::new(),
    };

    let mut mpd = Mpd::connect(&addr).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    println!("MPD protocol {} at {addr}", mpd.version);
    println!("sampling every {interval}s; the rule is half-or-four-minutes, no length floor");
    if durations.is_empty() {
        println!(
            "durations from MPD (an ESTIMATE -- pass {} and {} for Lempi's)",
            opt::LIBRARY.name, opt::ROOT.name
        );
    } else {
        println!(
            "durations from Lempi for {} file(s); MPD's estimate is the fallback",
            durations.len()
        );
    }
    println!("writing to nothing\n");

    let mut current: Option<Watching> = None;
    let (mut plays, mut skips) = (0u32, 0u32);
    let mut disagreements = 0u32;

    loop {
        let status = match mpd.cmd("status") {
            Ok(l) => parse(&l),
            Err(e) => {
                eprintln!("status: {e}");
                break;
            }
        };
        let state = status.get("state").cloned().unwrap_or_default();
        let stopped = state == "stop";
        // MPD **retains `songid` across a stop**, so an absent id is not how a
        // stop announces itself. Watching only the id let a passage the
        // listener stopped 57% of the way through go unjudged until some
        // later song started, and never at all if none did.
        let songid = if stopped { None } else { status.get("songid").cloned() };
        let elapsed: f64 = status.get("elapsed").and_then(|v| v.parse().ok()).unwrap_or(0.0);
        let duration: f64 = status.get("duration").and_then(|v| v.parse().ok()).unwrap_or(0.0);

        // Has the song on air changed since the last sample?
        //
        // A **pause** deliberately does not end the watch: elapsed holds still,
        // the listener is coming back, and judging them for stepping away would
        // be wrong. Only a stop or a different song closes the book.
        let changed = match (&current, &songid) {
            (Some(c), Some(id)) => &c.songid != id,
            (Some(_), None) => true, // stopped
            _ => false,
        };
        if changed {
            if let Some(c) = current.take() {
                verdict(&c, &mut plays, &mut skips);
            }
        }

        if let Some(id) = songid {
            match current.as_mut() {
                Some(c) => {
                    c.furthest_s = c.furthest_s.max(elapsed);
                    c.samples += 1;
                }
                None => {
                    let file = mpd
                        .cmd("currentsong")
                        .map(|l| parse(&l).get("file").cloned().unwrap_or_default())
                        .unwrap_or_default();
                    // Lempi's decoded duration wins wherever Lempi knows the
                    // file; MPD's estimate is the fallback, not the reference.
                    let known = durations.get(&file).copied();
                    if let Some(v) = known {
                        if duration > 0.0 && (v - duration).abs() > 1.0 {
                            disagreements += 1;
                            println!(
                                "  note: MPD says {duration:.1}s, Lempi {v:.1}s -- using Lempi's  {}",
                                file.rsplit('/').next().unwrap_or(&file)
                            );
                        }
                    }
                    current = Some(Watching {
                        songid: id,
                        file,
                        duration_s: known.unwrap_or(duration),
                        from_lempi: known.is_some(),
                        furthest_s: elapsed,
                        samples: 1,
                        first_seen: Instant::now(),
                    });
                }
            }
        }

        if run_for.is_some_and(|s| started.elapsed().as_secs_f64() >= s) {
            break;
        }
        std::thread::sleep(Duration::from_secs_f64(interval));
    }

    if let Some(c) = current.take() {
        verdict(&c, &mut plays, &mut skips);
    }
    println!("\nwould record {plays} play(s); {skips} skip(s) ignored");
    if disagreements > 0 {
        println!("{disagreements} duration disagreement(s); Lempi's decode was used for each");
    }
}

fn verdict(c: &Watching, plays: &mut u32, skips: &mut u32) {
    let four_minutes = FOUR_MINUTES_MS as f64 / 1000.0;
    let threshold = (c.duration_s / 2.0).min(four_minutes);
    let played = counts_as_play_s(c.furthest_s, c.duration_s);
    if played {
        *plays += 1;
    } else {
        *skips += 1;
    }
    let name = c.file.rsplit('/').next().unwrap_or(&c.file);
    if c.duration_s <= 0.0 {
        println!("{:<5} duration unknown -- not judged  {}", "-", name);
        return;
    }
    println!(
        "{:<5} {:>6.1}s of {:>6.1}s{}  (needed {:>5.1}s, {} sample(s), {:.0}s watched)  {}",
        if played { "PLAY" } else { "skip" },
        c.furthest_s,
        c.duration_s,
        if c.from_lempi { "" } else { "~" },
        threshold,
        c.samples,
        c.first_seen.elapsed().as_secs_f64(),
        name
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The rule itself is tested in `lempi_player::scrobble`. What matters here
    /// is that this binary asks *that* question rather than its own -- the two
    /// having drifted apart is what `[SPEC-PLAY-030]` exists to prevent.
    #[test]
    fn the_observer_judges_by_the_shared_rule() {
        assert!(counts_as_play_s(6.0, 12.0));
        assert!(!counts_as_play_s(5.0, 12.0));
        assert!(!counts_as_play_s(0.0, 0.0), "an unknown duration is never a play");
    }
}
