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
use std::sync::mpsc;
use std::time::{Duration, Instant};

use lempi_player::db::{Library, PlayerStore, QualifyingConn, Rejection};
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

/// URI -> passage, its top recording and its length; `None` for a URI that
/// names more than one radio passage.
type UriMap = HashMap<String, Option<(i64, Option<String>, u64)>>;

/// What this program heard or was told while it ran, kept so a Director built
/// since can be told again `[SPEC-DIR-109]`: one built on its own thread read
/// the history before these may have reached it, and without `--write` they
/// never reach it at all.
enum Seen {
    Played(i64, i64),
    Rejected(Rejection, String, i64),
}

impl Seen {
    fn at(&self) -> i64 {
        match self {
            Seen::Played(_, at) | Seen::Rejected(_, _, at) => *at,
        }
    }
}

/// A Director being built on its own thread, as the player builds one
/// `[IMPL-SUI-075]`: the one in hand goes on choosing until it lands.
struct Building {
    rx: mpsc::Receiver<Result<(Director, UriMap), String>>,
    /// When it began, in unix seconds: what was written before this is in
    /// the history it read.
    started_at: i64,
    began: Instant,
}

/// Build a Director and its URI map on a thread of their own, as the player
/// builds one `[IMPL-SUI-075]`, and hand them back on the channel. Opened as
/// the player's builder opens the library: read-only, its own connection.
fn spawn_rebuild(
    db_path: &str,
    library_path: &str,
    root_norm: &str,
) -> std::io::Result<mpsc::Receiver<Result<(Director, UriMap), String>>> {
    let (tx, rx) = mpsc::channel();
    let (db, lib, root) = (db_path.to_string(), library_path.to_string(), root_norm.to_string());
    std::thread::Builder::new().name("director-rebuild".into()).spawn(move || {
        // Below the sampler, and idle on the card, as the player's builder is.
        lempi_player::session::step_aside();
        let (db, lib) = (std::path::Path::new(&db), std::path::Path::new(&lib));
        let built = Library::open_split(db, lib)
            .and_then(|l| l.director())
            .map_err(|e| format!("{e:?}"))
            .and_then(|d| {
                let c = QualifyingConn::open(db, lib, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
                    .map_err(|e| format!("{e:?}"))?;
                Ok((d, uri_map(&c, &root)))
            });
        let _ = tx.send(built);
    })?;
    Ok(rx)
}

/// URI -> passage, from the library, for naming a song this program did not
/// queue: a person's own additions feed rotation `[SPEC-MPD-115]`. Built at
/// start and again whenever the Director is rebuilt, so a file imported since
/// can be named.
///
/// **A file carrying more than one radio passage is left ambiguous**, not
/// guessed. Added whole, a DAO capture is forty songs; picking one of them
/// would attribute a play to a passage the listener never heard, which is the
/// failure `[SPEC-MPD-060]`'s third rung exists to refuse. The recording is
/// the passage's top one, as the Director reads it `[SPEC-DIR-112]`.
fn uri_map(conn: &QualifyingConn, root_norm: &str) -> UriMap {
    let mut by_uri: UriMap = HashMap::new();
    let mut q = conn
        .prepare(
            "SELECT f.path, f.duration_ms, p.passage_id, \
                (SELECT pr.mbid FROM passage_recordings pr \
                 WHERE pr.passage_id = p.passage_id ORDER BY pr.weight DESC, pr.mbid LIMIT 1) \
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
        let Some(rel) = norm.strip_prefix(root_norm) else { continue };
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
    by_uri
}

/// What `mpd_direct` last applied of what the player can change while it runs
/// `[SPEC-DIR-109]`. The player's own Director learns of these through its
/// in-process controls; this program is another process, so it reads them
/// from the listener file, every `WATCH_S`.
#[derive(Default)]
struct Watched {
    /// Slots and seeds, as one string: a change is a programme edit.
    programme: String,
    /// When the player last started a Director rebuild -- a preference saved,
    /// a star sync, a file found damaged, music imported.
    rebuilt_at: Option<String>,
}

/// How often the listener file is read for changes, in seconds.
const WATCH_S: u64 = 5;
/// How often the OS is asked for its UTC offset, as the player asks it.
const UTC_RESYNC_S: u64 = 3600;

/// The programme's slots and seeds as one comparable string.
fn programme_signature(conn: &QualifyingConn) -> String {
    conn.query_row(
        "SELECT IFNULL((SELECT group_concat(program_id || '|' || name || '|' || IFNULL(start_time, ''), ';') \
                          FROM (SELECT * FROM listener_programs ORDER BY program_id)), '') || '#' || \
                IFNULL((SELECT group_concat(program_id || '|' || mbid, ';') \
                          FROM (SELECT * FROM listener_program_seeds ORDER BY program_id, mbid)), '')",
        [],
        |r| r.get::<_, String>(0),
    )
    .unwrap_or_default()
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

    // Both halves, as every installation has them `[IMPL-DBSPLIT-025]`.
    let library_path = args.need(&opt::LIBRARY).to_string();
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

    let mut by_uri = uri_map(&conn, &root_norm);
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
    // MPD Id -> the passage an entry this program did not queue is, once
    // resolved; `None` for one that names no passage. Kept so a sticker is
    // asked about each entry once, not on every pass.
    let mut theirs: HashMap<String, Option<i64>> = HashMap::new();

    // What the player can change while this runs, read from the listener file
    // `[SPEC-DIR-109]`. The rebuild stamp and the programme start as they are,
    // so the first pass applies the rest without rebuilding what was just
    // loaded.
    let reader = PlayerStore::open(std::path::Path::new(&db_path)).ok();
    let mut watched = Watched {
        programme: programme_signature(&conn),
        rebuilt_at: reader.as_ref().and_then(|r| r.director_rebuilt_at()),
    };
    let mut last_watch: Option<Instant> = None;
    let mut utc_synced = Instant::now();
    let mut seen: Vec<Seen> = Vec::new();
    let mut building: Option<Building> = None;

    loop {
        // A Director built on its own thread, landed: swapped in, told again
        // what is queued and what was heard and declined while it built, and
        // the watch below run at once so the hold, the offset and the windows
        // reach it before it chooses anything.
        if let Some(b) = &building {
            match b.rx.try_recv() {
                Err(mpsc::TryRecvError::Empty) => {}
                landed => {
                    let b = building.take().expect("checked above");
                    match landed {
                        Ok(Ok((d, map))) => {
                            director = d;
                            by_uri = map;
                            for o in ours.values_mut() {
                                o.note = director.note_queued(o.passage_id, now());
                            }
                            for s in &seen {
                                match s {
                                    Seen::Played(pid, at) => {
                                        if let Some(n) = director.note_queued(*pid, *at) {
                                            director.settle(n);
                                        }
                                    }
                                    Seen::Rejected(kind, mbid, at) => director.note_rejection(*kind, mbid, *at),
                                }
                            }
                            // Written to history before the build began, so it
                            // read them; only the rest need keeping. Without
                            // `--write` nothing is written, and all are kept.
                            if store.is_some() {
                                seen.retain(|s| s.at() >= b.started_at - 1);
                            }
                            watched.programme = String::new();
                            last_watch = None;
                            println!(
                                "  director rebuilt, as the player's was, in {:.1}s, and swapped in",
                                b.began.elapsed().as_secs_f64()
                            );
                        }
                        Ok(Err(e)) => eprintln!("director rebuild: {e}; the one in hand goes on choosing"),
                        Err(_) => eprintln!("director rebuild stopped without answering; the one in hand goes on choosing"),
                    }
                }
            }
        }
        if last_watch.is_none_or(|t| t.elapsed().as_secs() >= WATCH_S) {
            last_watch = Some(Instant::now());
            if let Some(r) = &reader {
                // A rebuild the player started: what the Director reads at
                // load has changed. Built here too, on its own thread, the
                // way the player builds one `[IMPL-SUI-075]`, so sampling and
                // choosing go on meanwhile. One at a time: a stamp that moves
                // again while one builds is seen once it has landed.
                let stamp = r.director_rebuilt_at();
                if stamp != watched.rebuilt_at && building.is_none() {
                    watched.rebuilt_at = stamp;
                    match spawn_rebuild(&db_path, &library_path, &root_norm) {
                        Ok(rx) => {
                            building = Some(Building { rx, started_at: now(), began: Instant::now() });
                            println!("  director rebuilding, as the player's is; the one in hand goes on choosing");
                        }
                        Err(e) => eprintln!("could not start a director rebuild: {e}"),
                    }
                }
                // A programme edit: its slots, taken as the player takes them.
                let sig = programme_signature(&conn);
                if sig != watched.programme {
                    match lempi_player::director::programme::Programme::load(&conn) {
                        Ok(fresh) => {
                            director.programme_mut().replace_slots(fresh);
                            println!("  programme: {} slot(s) taken from the edit", director.programme().len());
                        }
                        Err(e) => eprintln!("programme: {e}"),
                    }
                    watched.programme = sig;
                }
                // The slot held by hand, or none.
                let held = r.load_manual_slot();
                if director.programme().manual_slot() != held {
                    director.programme_mut().set_manual_slot(held);
                    println!("  slot held by hand: {held:?}");
                }
                // The UTC offset: the OS asked hourly, as the player asks it,
                // only when writing -- and never past a hand-set hold
                // `[SPEC-DIR-183]` -- then whatever is stored applied.
                if let (Some(st), true) = (&store, utc_synced.elapsed().as_secs() >= UTC_RESYNC_S) {
                    st.sync_utc_offset();
                    utc_synced = Instant::now();
                }
                if let Some(off) = r.stored_utc_offset() {
                    if director.programme().utc_offset_minutes != off {
                        director.programme_mut().utc_offset_minutes = off;
                        println!("  utc offset {off:+} min");
                    }
                }
                // The listener's suppression windows `[SPEC-PLAY-050]`.
                if let Some(s) = r.load_settings() {
                    let windows = (s.skip_suppress_h, s.dequeue_suppress_h);
                    if director.suppress_h() != windows {
                        director.set_suppress_h(windows);
                        println!("  suppression windows {}h / {}h", windows.0, windows.1);
                    }
                }
            }
        }

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
        // `(Id, file)` in MPD's own play order: each entry opens with its
        // `file:` line and carries its `Id:` after.
        let order: Vec<(String, String)> = match mpd.cmd("playlistid") {
            Ok(lines) => {
                let mut out: Vec<(String, String)> = Vec::new();
                let mut file = String::new();
                for l in &lines {
                    if let Some(f) = l.strip_prefix("file: ") {
                        file = f.to_string();
                    } else if let Some(id) = l.strip_prefix("Id: ") {
                        out.push((id.to_string(), std::mem::take(&mut file)));
                    }
                }
                out
            }
            Err(e) => {
                eprintln!("playlistid: {e}");
                break;
            }
        };
        let live: HashSet<String> = order.iter().map(|(id, _)| id.clone()).collect();
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
                    seen.push(Seen::Played(o.passage_id, now()));
                } else {
                    skipped += 1;
                    // Its window starts now, in this Director, as the
                    // player's does `[SPEC-PLAY-050]` -- not at the next
                    // build, and with no `--write`, not never.
                    if let Some(m) = &o.mbid {
                        director.note_rejection(Rejection::Skip, m, now());
                        seen.push(Seen::Rejected(Rejection::Skip, m.clone(), now()));
                    }
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
                if let Some(m) = &o.mbid {
                    director.note_rejection(Rejection::Dequeue, m, now());
                    seen.push(Seen::Rejected(Rejection::Dequeue, m.clone(), now()));
                }
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
        // Every entry in MPD's queue as a passage, in MPD's own order, whoever
        // put it there -- this program, or a person through another client.
        // Ours are known by Id; anything else is resolved by its URI, then by
        // the sticker this MPD keeps `[SPEC-MPD-065]`, once per entry.
        theirs.retain(|id, _| live.contains(id));
        let mut queued: Vec<Option<i64>> = Vec::with_capacity(order.len());
        for (id, file) in &order {
            let pid = match ours.get(id) {
                Some(o) => Some(o.passage_id),
                None => *theirs.entry(id.clone()).or_insert_with(|| {
                    by_uri.get(file).and_then(|v| v.as_ref()).map(|(pid, _, _)| *pid).or_else(|| {
                        mpd.cmd(&format!("sticker get song {} lempi.passage", quote(file)))
                            .ok()
                            .and_then(|l| {
                                l.iter()
                                    .find_map(|s| s.strip_prefix("sticker: lempi.passage="))
                                    .and_then(|v| v.parse().ok())
                            })
                    })
                }),
            };
            queued.push(pid);
        }
        // Everything queued is kept out of the next pick, hand-added or ours
        // `[SPEC-DIR-107]` -- exactly `Session::refill`, whose queue holds the
        // listener's choices beside the Director's.
        let mut skip: Vec<i64> = queued.iter().flatten().copied().collect();
        // The flow tail is the passage each new one will follow
        // `[SPEC-DIR-160]`: the last entry in that order. Unresolvable, there
        // is nothing to flow from, and the pick says so in its record.
        let mut after: Option<i64> = queued.last().copied().flatten();
        for _ in 0..(depth - len) {
            // With every candidate held by rotation, a repeat is a better
            // answer than silence, as in `Session::refill` `[SPEC-DIR-232]`.
            let Some(decision) = director
                .decide(now(), &mut rng, &skip, after)
                .or_else(|| director.decide_relaxed(now(), &mut rng, &skip))
            else {
                println!("  director has nothing eligible to offer, even with rotation set aside");
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
                    // The next pick follows this one, and cannot be it.
                    after = Some(e.passage_id);
                    skip.push(e.passage_id);
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

#[cfg(test)]
mod tests {
    use super::{programme_signature, spawn_rebuild};

    /// A rebuild on its own thread hands back a Director and the URI map
    /// over a library as the schema makes it, with a passage under the
    /// music root named by its URI `[SPEC-DIR-109]`.
    #[test]
    fn a_rebuild_on_its_own_thread_hands_back_a_director_and_its_uri_map() {
        let f = std::env::temp_dir().join(format!("mpd_direct_rebuild_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&f);
        let c = rusqlite::Connection::open(&f).unwrap();
        c.execute_batch(include_str!("../../../sql/schema.sql")).unwrap();
        c.execute_batch(
            "INSERT INTO files (file_id, audio_md5, path, size_bytes, mtime, format, duration_ms, first_seen, last_seen)
                 VALUES (1, 'm1', '/m/a/b.mp3', 1, 1, 'mp3', 200000, 't', 't');
             INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms, boundary_src)
                 VALUES (7, 1, 'radio', 0, 200000, 'test');",
        )
        .unwrap();
        drop(c);
        let path = f.to_string_lossy().to_string();
        let rx = spawn_rebuild(&path, &path, "/m").unwrap();
        let (d, map) = rx
            .recv_timeout(std::time::Duration::from_secs(60))
            .expect("the builder answered")
            .expect("and built");
        assert_eq!(d.len(), 1, "the one radio passage");
        assert_eq!(map.get("a/b.mp3").and_then(|v| v.as_ref()).map(|v| v.0), Some(7), "named by its URI");
        let _ = std::fs::remove_file(&f);
    }
    use lempi_player::db::QualifyingConn;

    /// Every kind of programme edit reads as a change -- a slot added, renamed,
    /// re-timed, a seed added or removed -- and nothing else does
    /// `[SPEC-DIR-109]`.
    #[test]
    fn the_programme_signature_notices_every_edit() {
        let f = std::env::temp_dir().join(format!("mpd_direct_sig_{}.db", std::process::id()));
        let _ = std::fs::remove_file(&f);
        let c = QualifyingConn::open(&f, &f, rusqlite::OpenFlags::SQLITE_OPEN_READ_WRITE | rusqlite::OpenFlags::SQLITE_OPEN_CREATE)
            .unwrap();
        c.execute_batch(
            "CREATE TABLE listener_programs (program_id INTEGER PRIMARY KEY, name TEXT, start_time TEXT);
             CREATE TABLE listener_program_seeds (program_id INTEGER, mbid TEXT, position INTEGER);",
        )
        .unwrap();
        let mut seen = vec![programme_signature(&c)];
        for sql in [
            "INSERT INTO listener_programs VALUES (1, 'Soft', '04:00')",
            "INSERT INTO listener_program_seeds VALUES (1, 'a', 1)",
            "UPDATE listener_programs SET name = 'Gentle' WHERE program_id = 1",
            "UPDATE listener_programs SET start_time = '05:00' WHERE program_id = 1",
            "INSERT INTO listener_program_seeds VALUES (1, 'b', 2)",
            "DELETE FROM listener_program_seeds WHERE mbid = 'a'",
        ] {
            c.execute_batch(sql).unwrap();
            let now = programme_signature(&c);
            assert!(!seen.contains(&now), "{sql}: read as no change");
            seen.push(now);
        }
        assert_eq!(programme_signature(&c), *seen.last().unwrap(), "reading twice is no change");
        drop(c);
        let _ = std::fs::remove_file(&f);
    }
}
