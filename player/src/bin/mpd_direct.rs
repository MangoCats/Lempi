//! Stages 3 and 4: the Program Director drives MPD, and writes what happened
//! `[IMPL-MPD-040]`, `[IMPL-MPD-050]`.
//!
//! The command line is `cli::specs::mpd_direct`; `mpd_direct --help`
//! prints it.
//!
//! Stage 2 proved `rangeid` under a deliberately stupid selector. This swaps
//! that selector for `Director::decide` — the same call `Session::refill` makes
//! locally, with the same exclusion set and the same flow tail `[SPEC-DIR-160]`.
//! The selection code is *not* reimplemented here; that is the point.
//!
//! **The bookkeeping is the part to get right, not the selection**
//! `[IMPL-MPD-045]`. Locally a passage that never played is undone through
//! `take_dropped`; here the only evidence is that a song id left MPD's queue,
//! and the same event means two different things depending on whether it was
//! ever the current song.
//!
//! **Writing is opt-in.** Without `--write` this behaves exactly as stage 3
//! did, printing what it would record. With it, plays go to
//! `listener_play_history` and rejections to `listener_rejections`
//! `[SPEC-PLAY-050]` — and **nowhere else**: no scrobbler, no network, nothing
//! that leaves the machine `[SPEC-MPD-100]`, `[SPEC-DF-055]`.
//!
//! What is published instead is a **sticker**, in MPD's own database, which is
//! where MPD's own clients already look `[SPEC-MPD-050]`. A client that has
//! never heard of Lempi is unaffected; one that shows stickers gains the "why
//! this passage" panel without a line of code changing.

use std::collections::{HashMap, HashSet};
use std::time::{Duration, Instant};

use lempi_player::db::{PlayerStore, QualifyingConn, Rejection};
use lempi_player::director::library::{Director, QueuedNote, Rng};
use lempi_player::mpd::{parse, quote, Mpd};
use lempi_player::scrobble::counts_as_play;

/// What the Director handed over, and what MPD did with it.
struct Offered {
    passage_id: i64,
    title: String,
    mbid: Option<String>,
    /// The passage span, from Lempi and never from MPD `[SPEC-MPD-092]`.
    span_ms: u64,
    /// Furthest position observed, in ms. With `rangeid` MPD reports `elapsed`
    /// relative to the range start, so this needs no translation.
    furthest_ms: u64,
    /// False when MPD dropped the range end `[SPEC-MPD-096]`; the passage will
    /// otherwise play to end of file, so the Director must end it itself.
    span_honoured: bool,
    /// The undo for `note_queued`, held until the passage's fate is known
    /// `[REQ-PD-112]`.
    note: Option<QueuedNote>,
    /// Whether this song has ever been the current one. It is the whole
    /// difference between "the listener heard it" and "the listener removed
    /// it", and MPD does not report it — only the sampler can know.
    was_current: bool,
}

fn main() {
    use lempi_player::cli::specs::mpd_direct as opt;
    let mut args = opt::SPEC.parse();
    let db_path = args.need(&opt::LISTENER).to_string();
    let root = args.need(&opt::ROOT).to_string();
    // The listener's settings own these `[SPEC-MPD-105]`; the flags are an
    // override for a test run, not the source of truth. Read before the
    // Director so a bad value fails early rather than mid-session.
    // Neither precedence nor the fallback is written here any more -- the
    // options declare `.setting(..)` and the chain does it `[GDE-CLI-090]`.
    if let Some(saved) =
        PlayerStore::open(std::path::Path::new(&db_path)).ok().and_then(|s| s.load_settings())
    {
        for complaint in args.with_settings(|k| saved.value_of(k)) {
            eprintln!("mpd_direct: {complaint}");
        }
    }
    let depth: usize = args.must_size(&opt::DEPTH);
    let interval: f64 = args.must_real(&opt::INTERVAL);
    let run_for: Option<f64> = args.real(&opt::RUN_FOR);
    // Stage 4 is the first stage that writes `[IMPL-MPD-050]`, and it says so
    // out loud: without `--write` it behaves exactly as stage 3 did.
    let write = args.has(&opt::WRITE);
    let addr = args.need(&opt::ADDR).to_string();
    let mut rng = match args.int(&opt::SEED) {
        Some(s) => Rng::seeded(s),
        None => Rng::from_clock(),
    };

    // `--library` names a genuinely separate `library.db`, for exercising a
    // split installation from this dev tool too; absent, it defaults to
    // `db_path` itself, the same unsplit case every other caller has.
    let library_path =
        args.text(&opt::LIBRARY).map(str::to_string).unwrap_or_else(|| db_path.clone());
    let conn = QualifyingConn::open(
        std::path::Path::new(&db_path),
        std::path::Path::new(&library_path),
        rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE,
    )
    .unwrap_or_else(|e| {
        eprintln!("{db_path}: {e}");
        std::process::exit(1);
    });
    let started_load = Instant::now();
    let mut director = Director::load(&conn).unwrap_or_else(|e| {
        eprintln!("director: {e}");
        std::process::exit(1);
    });
    let root_norm = root.replace('\\', "/").trim_end_matches('/').to_string();

    // A person's own additions feed rotation `[SPEC-MPD-115]`, so a song this
    // program did not queue still has to be nameable. URI -> passage, built
    // once from the library.
    //
    // **A file carrying more than one radio passage is left ambiguous**, not
    // guessed. Added whole, a DAO capture is forty songs; picking one of them
    // would attribute a play to a passage the listener never heard, which is
    // the failure `[SPEC-MPD-060]`'s third rung exists to refuse.
    let mut by_uri: HashMap<String, Option<(i64, Option<String>, u64)>> = HashMap::new();
    {
        let mut q = conn
            .prepare(
                "SELECT f.path, f.duration_ms, p.passage_id, \
                    (SELECT pr.mbid FROM passage_recordings pr \
                     WHERE pr.passage_id = p.passage_id LIMIT 1) \
                 FROM passages p JOIN files f USING(file_id) WHERE p.kind = 'radio'",
            )
            .expect("map uris");
        let rows = q
            .query_map([], |r| {
                Ok((
                    r.get::<_, String>(0)?,
                    r.get::<_, Option<i64>>(1)?.unwrap_or(0) as u64,
                    r.get::<_, i64>(2)?,
                    r.get::<_, Option<String>>(3)?,
                ))
            })
            .expect("read uris");
        for (path, dur, pid, mbid) in rows.flatten() {
            let norm = path.replace('\\', "/");
            let Some(rel) = norm.strip_prefix(&root_norm) else { continue };
            let uri = rel.trim_start_matches('/').to_string();
            match by_uri.entry(uri) {
                std::collections::hash_map::Entry::Occupied(mut e) => {
                    e.insert(None); // a second passage: ambiguous, so refuse both
                }
                std::collections::hash_map::Entry::Vacant(e) => {
                    e.insert(Some((pid, mbid, dur)));
                }
            }
        }
    }
    let unambiguous = by_uri.values().filter(|v| v.is_some()).count();

    let mut mpd = Mpd::connect(&addr).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    println!("MPD protocol {} at {addr}", mpd.version);
    println!("director loaded in {:.1}s", started_load.elapsed().as_secs_f64());
    if let Some(c) = Some(director.census(now())) {
        println!(
            "pool: {} eligible, {} suppressed by a recent skip or removal",
            c.eligible, c.suppressed
        );
    }
    let store = if write {
        match PlayerStore::open(std::path::Path::new(&db_path)) {
            Ok(s) => Some(s),
            Err(e) => {
                eprintln!("cannot open {db_path} for writing: {e}");
                std::process::exit(1);
            }
        }
    } else {
        None
    };
    println!("{unambiguous} of {} URIs name exactly one radio passage", by_uri.len());
    println!(
        "depth {depth}, sampling {interval}s; {}",
        if write { "WRITING plays and rejections" } else { "writes nothing (pass --write)" }
    );
    println!("nothing leaves this machine: no scrobble, no network `[SPEC-MPD-100]`\n");

    if let Err(e) = mpd.cmd("consume 1") {
        eprintln!("consume: {e}");
        std::process::exit(1);
    }

    let started = Instant::now();
    let mut ours: HashMap<String, Offered> = HashMap::new();
    let (mut added, mut played, mut removed, mut unhonoured) = (0u32, 0u32, 0u32, 0u32);
    let (mut skipped, mut stickered, mut adopted) = (0u32, 0u32, 0u32);
    let mut unresolved: HashSet<String> = HashSet::new();

    loop {
        let status = match mpd.status() {
            Ok(s) => s,
            Err(e) => {
                eprintln!("status: {e}");
                break;
            }
        };
        let state = status.get("state").cloned().unwrap_or_default();
        let current = status.get("songid").cloned();
        // Only while playing `[SPEC-MPD-120]`.
        let active = state == "play";

        // Mark the current song before diffing, so a passage that started
        // between two samples is not mistaken for one removed by hand.
        let elapsed_ms = status
            .get("elapsed")
            .and_then(|v| v.parse::<f64>().ok())
            .map(|s| (s * 1000.0).round() as u64)
            .unwrap_or(0);
        // A song this program did not queue is still something the listener
        // heard `[SPEC-MPD-115]`. Adopt it once, so its play is attributed.
        if let Some(id) = &current {
            if !ours.contains_key(id) {
                let uri = mpd
                    .cmd("currentsong")
                    .map(|l| parse(&l).get("file").cloned().unwrap_or_default())
                    .unwrap_or_default();
                if !uri.is_empty() {
                    // The sticker first `[SPEC-MPD-065]`: the mapping is a
                    // property of this MPD, cached where this MPD keeps it.
                    let cached: Option<i64> = mpd
                        .cmd(&format!("sticker get song {} lempi.passage", quote(&uri)))
                        .ok()
                        .and_then(|l| {
                            l.iter()
                                .find_map(|s| s.strip_prefix("sticker: lempi.passage="))
                                .and_then(|v| v.parse().ok())
                        });
                    let found = match cached {
                        Some(pid) => by_uri
                            .get(&uri)
                            .and_then(|v| v.as_ref())
                            .map(|(_, m, d)| (pid, m.clone(), *d)),
                        None => by_uri.get(&uri).and_then(|v| v.clone()),
                    };
                    match found {
                        Some((pid, mbid, file_ms)) => {
                            if cached.is_none() {
                                let _ = mpd.cmd(&format!(
                                    "sticker set song {} {} {}",
                                    quote(&uri),
                                    quote("lempi.passage"),
                                    quote(&pid.to_string())
                                ));
                            }
                            adopted += 1;
                            println!(
                                "  adopted a hand-added song as [{pid}]{}",
                                if cached.is_some() { " (from the sticker cache)" } else { "" }
                            );
                            ours.insert(
                                id.clone(),
                                Offered {
                                    passage_id: pid,
                                    title: uri.rsplit('/').next().unwrap_or(&uri).to_string(),
                                    mbid,
                                    // Added whole, so the file's length is the
                                    // span; there is no range to be relative to.
                                    span_ms: file_ms,
                                    furthest_ms: 0,
                                    span_honoured: true,
                                    note: None,
                                    was_current: true,
                                },
                            );
                        }
                        None => {
                            if unresolved.insert(uri.clone()) {
                                println!(
                                    "  a hand-added song is ambiguous or unknown, so its play \
                                     is not attributed: {}",
                                    uri.rsplit('/').next().unwrap_or(&uri)
                                );
                            }
                        }
                    }
                }
            }
        }

        let mut overrun: Option<String> = None;
        if let Some(id) = &current {
            if let Some(o) = ours.get_mut(id) {
                o.was_current = true;
                if active {
                    o.furthest_ms = o.furthest_ms.max(elapsed_ms);
                }
                // **The Director ends what MPD would not** `[SPEC-MPD-096]`.
                // Where the span was dropped, MPD plays on to end of file — up
                // to 532 s past the passage in this library. Advancing here
                // bounds the overrun by the sample interval instead.
                if !o.span_honoured && active && o.furthest_ms >= o.span_ms {
                    overrun = Some(id.clone());
                }
            }
        }
        if let Some(id) = overrun {
            println!("  span end reached on an unhonoured range; advancing [{id}]");
            let _ = mpd.cmd("next");
        }

        // --- the queue diff, which is this stage's real work ---
        let live: HashSet<String> = match mpd.cmd("playlistid") {
            Ok(lines) => lines
                .iter()
                .filter_map(|l| l.strip_prefix("Id: "))
                .map(|s| s.to_string())
                .collect(),
            Err(e) => {
                eprintln!("playlistid: {e}");
                break;
            }
        };
        let gone: Vec<String> = ours.keys().filter(|id| !live.contains(*id)).cloned().collect();
        for id in gone {
            let Some(o) = ours.remove(&id) else { continue };
            if o.was_current {
                // It reached the front, so the listener met it, and the note
                // stands — the same as locally, where only an unopenable
                // passage is undone. Whether it *played* is a separate
                // question, judged against Lempi's span `[SPEC-PLAY-010]`.
                let heard = counts_as_play(o.furthest_ms, o.span_ms);
                let verb = if heard { "PLAY" } else { "skip" };
                println!(
                    "  {verb} [{}] {:.0}s of {:.0}s  {}",
                    o.passage_id,
                    o.furthest_ms as f64 / 1000.0,
                    o.span_ms as f64 / 1000.0,
                    o.title
                );
                if heard {
                    played += 1;
                } else {
                    skipped += 1;
                }
                if let Some(st) = &store {
                    let r = if heard {
                        // See mpd_backend.rs: no provenance tracking on
                        // this path yet `[REQ-VIS-300]`.
                        st.record_play(o.passage_id, o.mbid.as_deref(), o.furthest_ms, o.span_ms, None)
                            .map(|_| ())
                    } else {
                        // A skip is not a play, and earns the longer window
                        // `[SPEC-PLAY-055]`.
                        st.record_rejection(
                            Rejection::Skip,
                            o.passage_id,
                            o.mbid.as_deref(),
                            Some(o.furthest_ms),
                            Some(o.span_ms),
                        )
                    };
                    if let Err(e) = r {
                        eprintln!("record {verb}: {e}");
                    }
                }
            } else {
                // It never played and it left: a person took it out. Undo the
                // queueing mark `[REQ-PD-112]` so the Director does not go on
                // believing it was heard...
                removed += 1;
                if let Some(note) = o.note {
                    director.forget_queued(note);
                }
                // ...and record the removal as the weaker rejection, which is a
                // separate mechanism with a separate window `[SPEC-PLAY-055]`.
                // Undoing the note is not the same as forgiving the removal.
                println!(
                    "  removed by hand [{}] {} -- queue mark undone, dequeue recorded",
                    o.passage_id, o.title
                );
                if let Some(st) = &store {
                    // Never sounded, so there is no percentage to report
                    // `[REQ-VIS-250]`.
                    if let Err(e) = st.record_rejection(
                        Rejection::Dequeue,
                        o.passage_id,
                        o.mbid.as_deref(),
                        None,
                        None,
                    ) {
                        eprintln!("record dequeue: {e}");
                    }
                }
            }
        }

        let len = live.len();
        if !active || len >= depth {
            if run_for.is_some_and(|s| started.elapsed().as_secs_f64() >= s) {
                break;
            }
            std::thread::sleep(Duration::from_secs_f64(interval));
            continue;
        }

        // --- selection, which is not reimplemented ---
        for _ in 0..(depth - len) {
            // The exclusion set is what is already queued, and the flow tail is
            // the last of them `[SPEC-DIR-160]` — exactly `Session::refill`.
            let skip: Vec<i64> = ours.values().map(|o| o.passage_id).collect();
            let after = ours.values().last().map(|o| o.passage_id);
            let Some(decision) = director.decide(now(), &mut rng, &skip, after) else {
                println!("  director has nothing eligible to offer");
                break;
            };
            let e = &decision.entry;
            let uri = {
                let norm = e.path.to_string_lossy().replace('\\', "/");
                match norm.strip_prefix(&root_norm) {
                    Some(r) => r.trim_start_matches('/').to_string(),
                    None => {
                        println!("  [{}] does not resolve under the music root", e.passage_id);
                        continue;
                    }
                }
            };
            match mpd.add_ranged(&uri, e.start_ms as i64, e.end_ms as i64) {
                Ok(a) => {
                    if !a.span_honoured {
                        unhonoured += 1;
                        println!("  ! span not honoured for [{}]", e.passage_id);
                    }
                    // Publish into the world MPD's clients already read
                    // `[SPEC-MPD-050]`. Nothing is added to MPD to make this
                    // work, and a client that knows nothing of Lempi is
                    // unaffected by any of it.
                    let mut set = |name: &str, value: &str| {
                        let cmd = format!(
                            "sticker set song {} {} {}",
                            quote(&uri),
                            quote(name),
                            quote(value)
                        );
                        if let Err(err) = mpd.cmd(&cmd) {
                            eprintln!("sticker {name}: {err}");
                        }
                    };
                    set("lempi.passage", &e.passage_id.to_string());
                    set("lempi.chosen_at", &now().to_string());
                    match serde_json::to_string(&decision.why) {
                        Ok(j) => set("lempi.why", &j),
                        Err(err) => eprintln!("why: {err}"),
                    }
                    let flavor = e
                        .mbid
                        .as_deref()
                        .and_then(|m| director.flavor_summary(m, 3))
                        .unwrap_or_default();
                    if !flavor.is_empty() {
                        set("lempi.flavor", &flavor);
                    }
                    stickered += 1;

                    let note = director.note_queued(e.passage_id, now());
                    let title = e.title();
                    println!("  + [{:>6}] {}  {}", e.passage_id, title, flavor);
                    ours.insert(
                        a.id,
                        Offered {
                            passage_id: e.passage_id,
                            title,
                            mbid: e.mbid.clone(),
                            span_ms: e.duration_ms(),
                            furthest_ms: 0,
                            span_honoured: a.span_honoured,
                            note,
                            was_current: false,
                        },
                    );
                    added += 1;
                }
                Err(err) => println!("  {err}"),
            }
        }

        if run_for.is_some_and(|s| started.elapsed().as_secs_f64() >= s) {
            break;
        }
        std::thread::sleep(Duration::from_secs_f64(interval));
    }

    println!(
        "\noffered {added}; {played} played, {skipped} skipped, {removed} removed by hand"
    );
    println!("{stickered} passage(s) published to MPD's stickers `[SPEC-MPD-050]`");
    if adopted > 0 || !unresolved.is_empty() {
        println!(
            "{adopted} hand-added song(s) adopted `[SPEC-MPD-115]`; {} could not be named",
            unresolved.len()
        );
    }
    if unhonoured > 0 {
        println!("{unhonoured} span(s) MPD would not honour `[SPEC-MPD-096]`");
    }
    let c = director.census(now());
    println!(
        "pool now: {} eligible, {} suppressed, {} recording-blocked, {} artist-blocked",
        c.eligible, c.suppressed, c.recording_blocked, c.artist_blocked
    );
}

fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}
