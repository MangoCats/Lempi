//! Play arbitrary files, for audio not in the library.
//!
//! Uses the same [`Engine`] as `station`; it differs only in where passages
//! come from. It previously carried its own pump loop, which duplicated the
//! engine and then diverged from it — the engine gained back-pressure handling
//! this copy lacked, so identical audio behaved differently depending on which
//! binary played it.
//!
//! The command line is `cli::specs::play`; `play --help` prints it.
//!
//! Set LEMPI_NULL_OUTPUT=1 for a discard sink (headless/CI). It accepts
//! everything instantly, so it cannot reveal rate or back-pressure faults
//! `[REQ-HW-147]`.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use lempi_player::cli::specs::play as opt;
use lempi_player::engine::{Command, Engine};
use lempi_player::queue::QueueEntry;
use lempi_player::BUFFER_FRAMES;

fn main() {
    let args = opt::SPEC.parse();

    // A lead is needed on BOTH sides: overlap is min(lead_out(A), lead_in(B)),
    // so setting only one yields no crossfade at all.
    let lead_ms = (args.must_real(&opt::LEAD_S) * 1000.0) as u64;
    let mut entries: Vec<QueueEntry> = Vec::new();
    {
        let mut add = |path: &str, start: u64, end: u64, lin: u64, lout: u64| {
            entries.push(QueueEntry {
                qid: 0,
                passage_id: entries.len() as i64,
                path: PathBuf::from(path),
                start_ms: start,
                end_ms: end,
                file_ms: 0,
                lead_in_ms: lin,
                lead_out_ms: lout,
                // The production default `[SPEC-SUI-226]` -- this tool
                // exists to hear real playback, so it should hear the real
                // envelope every ordinary passage gets, not a silent one.
                fade_in_ms: 20,
                fade_out_ms: 20,
                fade_in_curve: lempi_player::fade::Curve::Exponential,
                fade_out_curve: lempi_player::fade::Curve::Exponential,
                gain_db: 0.0,
                mbid: None,
                naming: Default::default(),
                selected_by: None,
            });
        };
        // Absent means "to the end of the file", which is what u64::MAX says
        // to the queue; the option's own help line says the same in words.
        let end_of_file = u64::MAX;
        match args.text(&opt::FILE2) {
            Some(second) => {
                add(args.need(&opt::FILE), args.must_int(&opt::START_MS),
                    args.int(&opt::END_MS).unwrap_or(end_of_file), 0, lead_ms);
                add(second, args.must_int(&opt::START2_MS),
                    args.int(&opt::END2_MS).unwrap_or(end_of_file), lead_ms, 0);
            }
            None => add(args.need(&opt::FILE), args.must_int(&opt::START_MS),
                        args.int(&opt::END_MS).unwrap_or(end_of_file), 0, 0),
        }
    }

    let path = if std::env::var("LEMPI_NULL_OUTPUT").is_ok() {
        println!("output: null sink");
        lempi_player::path::PathHandle::silent()
    } else {
        let (p, why) = lempi_player::path::start(None, BUFFER_FRAMES * 2);
        println!("{why}");
        p
    };
    let rate = path.sample_rate();
    let channels = path.channels();

    let (mut engine, handle) = Engine::new(path, entries.len());
    for e in &entries {
        println!("passage: {} [{}..{}] lead {}/{} ms",
                 e.path.file_name().unwrap_or_default().to_string_lossy(),
                 e.start_ms, e.end_ms, e.lead_in_ms, e.lead_out_ms);
        engine.enqueue(e.clone());
    }
    handle.send(Command::Play);

    let t0 = Instant::now();
    let mut submitted: u64 = 0;
    // The tick may never wait on a write `[GDE-FBD-090]`: from here on this
    // thread's lines are queued, not written `[GDE-HST-140]`.
    lempi_player::logging::this_thread_must_not_block();
    while !engine.is_shutdown() {
        let n = engine.tick();
        submitted += n as u64;
        let s = handle.snapshot();
        if s.is_idle() {
            break;
        }
        if n == 0 {
            std::thread::sleep(Duration::from_millis(5));
        }
    }
    let under = handle.snapshot().underrun_samples;
    let audio_s = submitted as f64 / (rate as f64 * channels as f64);
    println!("\nsubmitted {audio_s:.1}s of audio in {:.1}s wall | underrun samples {under}",
             t0.elapsed().as_secs_f64());
}
