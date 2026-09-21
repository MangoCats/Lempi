//! The acceptance gate for `[REQ-AUD-110]` / [GDE-ARC-050], run as a program
//! rather than asserted. `verify-targets.sh` invokes it when `LEMPI_LONG_FILE`
//! names a long file, and reports SKIPPED when it does not.
//!
//! Lempi v1's play/skip latency and memory use came from one decision: decode
//! the whole file [GDE-V1-030]. This decodes a passage of arbitrary length
//! through a fixed-capacity buffer and reports peak RSS, so the claim "memory is
//! independent of passage length" is measured on the worst file in the library
//! rather than believed.
//!
//! The command line is `cli::specs::memcheck`; `memcheck --help` prints it.

use std::path::PathBuf;
use std::time::Instant;

use lempi_player::cli::specs::memcheck as opt;
use lempi_player::{decoder::PassageDecoder, peak_rss_bytes, BUFFER_FRAMES};

fn mb(bytes: u64) -> f64 {
    bytes as f64 / 1_048_576.0
}

fn main() {
    let args = opt::SPEC.parse();
    let path = PathBuf::from(args.need(&opt::FILE));
    let start_ms: u64 = args.must_int(&opt::START_MS);
    let end_ms: Option<u64> = args.int(&opt::END_MS);

    let baseline = peak_rss_bytes().unwrap_or(0);
    let t0 = Instant::now();

    let mut dec = match PassageDecoder::open(&path, start_ms, end_ms) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("open failed: {e}");
            std::process::exit(1);
        }
    };
    println!("file       : {}", path.display());
    println!("format     : {} Hz, {} ch", dec.sample_rate, dec.channels);
    println!("passage    : {start_ms} ms .. {}",
             end_ms.map(|e| format!("{e} ms")).unwrap_or_else(|| "EOF".into()));

    // The bounded buffer. Its capacity is the whole point: it is allocated once,
    // and the loop below drains it rather than growing it.
    let mut ring: Vec<f32> = Vec::with_capacity(BUFFER_FRAMES * dec.channels);
    let cap = ring.capacity();
    let mut packets: u64 = 0;
    let mut peak_ring = 0usize;

    loop {
        match dec.next() {
            Ok(Some(chunk)) => {
                packets += 1;
                // Simulate the mixer draining: keep at most BUFFER_FRAMES.
                ring.extend_from_slice(chunk);
                peak_ring = peak_ring.max(ring.len());
                let max = BUFFER_FRAMES * dec.channels;
                if ring.len() > max {
                    let excess = ring.len() - max;
                    ring.drain(0..excess);
                }
            }
            Ok(None) => break,
            Err(e) => {
                eprintln!("decode error: {e}");
                std::process::exit(1);
            }
        }
    }

    let secs = dec.frames_emitted() as f64 / dec.sample_rate as f64;
    let elapsed = t0.elapsed().as_secs_f64();
    let peak = peak_rss_bytes().unwrap_or(0);

    println!("decoded    : {:.1} s of audio ({} packets) in {:.2} s = {:.0}x realtime",
             secs, packets, elapsed, secs / elapsed.max(1e-9));
    println!("ring buffer: capacity {:.2} MB, high-water {:.2} MB",
             mb((cap * 4) as u64), mb((peak_ring * 4) as u64));
    println!("peak RSS   : {:.1} MB (baseline {:.1} MB)", mb(peak), mb(baseline));

    // If decoding a 4-hour passage cost memory proportional to its length, RSS
    // would be in the gigabytes. The gate is the target for the whole process
    // [REQ-HW-100], so a single decoder must sit far below it.
    let limit_mb = 150.0;
    if mb(peak) > limit_mb {
        eprintln!("\nFAIL: peak RSS {:.1} MB exceeds the {:.0} MB budget [REQ-HW-100]",
                  mb(peak), limit_mb);
        std::process::exit(1);
    }
    println!("\nPASS: bounded decode held {:.1} MB for {:.1} minutes of audio",
             mb(peak), secs / 60.0);
}
