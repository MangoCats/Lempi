//! Play passages from `lempi.db` on the terminal — the headless sibling of
//! `lempi`, useful where a browser is not.
//!
//! Selection is the Program Director `[SPEC009]`, all four stages: frequency,
//! shaping, flow, and the weighted roulette over the shaped pool.
//!
//! The command line is `cli::specs::station`; `station --help` prints it.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use lempi_player::cli::specs::station as opt;
use lempi_player::engine::{Command, Engine};
use lempi_player::queue::overlap_ms;
use lempi_player::session::Session;
use lempi_player::BUFFER_FRAMES;

fn main() {
    let args = opt::SPEC.parse();
    let db = PathBuf::from(args.need(&opt::LISTENER));
    let count: usize = args.must_size(&opt::COUNT);
    let list_only = args.has(&opt::LIST);

    let mut session = match Session::open(&db, &db, count) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    };
    println!("library: {} radio passages", session.lib.count_radio().unwrap_or(0));

    // Real device unless asked otherwise. A null sink cannot detect a
    // sample-rate fault [REQ-HW-147], so it must be opt-in, never the default.
    let path = if list_only || std::env::var("LEMPI_NULL_OUTPUT").is_ok() {
        if !list_only {
            println!("output: null sink");
        }
        lempi_player::path::PathHandle::silent()
    } else {
        let (p, why) = lempi_player::path::start(None, BUFFER_FRAMES * 2);
        println!("{why}");
        p
    };

    // Why the pool is the size it is [SPEC-DIR-190] -- where a station that has
    // gone quiet is diagnosed. Taken BEFORE priming, deliberately: queueing
    // updates the Director's history, so a census afterwards would describe the
    // pool minus what was just queued, which is a different question.
    if let Some(c) = session.census() {
        println!("pool: {} eligible, total weight {:.1}", c.eligible, c.total_weight);
        println!("      blocked: {} artist, {} recording, {} same song, {} related | {} under min weight, {} filtered",
                 c.artist_blocked, c.recording_blocked, c.work_blocked, c.related_blocked,
                 c.below_min_weight, c.filtered);
        println!("      suppressed by a recent skip or removal: {}", c.suppressed);
    } else {
        println!("pool: program director unavailable; random selection");
    }

    let (mut engine, handle) = Engine::new(path, count);
    session.prime(&mut engine);

    // List what the engine will actually play, not a second draw from the
    // library: a preview that re-randomised would describe a different evening.
    if let Some(p) = session.program() {
        println!("programme: {p}");
    }
    let entries: Vec<_> = engine.queued().cloned().collect();
    println!("queued {}\n", entries.len());
    for (i, e) in entries.iter().enumerate() {
        let name = e.path.file_name().unwrap_or_default().to_string_lossy();
        let missing = if e.path.exists() { "" } else { "  [FILE MISSING]" };
        println!("{:>2}. {:<44} {:>6.1}s  lead {}/{} ms  {:+.1} dB{}",
                 i + 1, &name.chars().take(44).collect::<String>(),
                 e.duration_ms() as f64 / 1000.0,
                 e.lead_in_ms, e.lead_out_ms, e.gain_db, missing);
        if let Some(next) = entries.get(i + 1) {
            println!("     -> crossfade {:.1}s", overlap_ms(e, next) as f64 / 1000.0);
        }
    }
    if list_only {
        return;
    }

    handle.send(Command::Play);
    let started = Instant::now();
    let mut last_id = -1i64;
    // The tick may never wait on a write `[GDE-FBD-090]`: from here on this
    // thread's lines are queued, not written `[GDE-HST-140]`.
    lempi_player::logging::this_thread_must_not_block();
    while !engine.is_shutdown() {
        let submitted = engine.tick();
        let s = handle.snapshot();
        if let Some(c) = &s.current {
            if c.passage_id != last_id {
                last_id = c.passage_id;
                println!(">> {}", c.path.file_name().unwrap_or_default().to_string_lossy());
            }
        }
        // Unlike `lempi`, this plays a fixed set and stops -- it is a test
        // harness, not the appliance.
        if s.is_idle() {
            break;
        }
        // Without a device there is no back-pressure, so pace the loop rather
        // than spinning a core flat out.
        if submitted == 0 {
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    let s = handle.snapshot();
    println!("\nfinished in {:.1}s | underrun samples: {}",
             started.elapsed().as_secs_f64(), s.underrun_samples);
}
