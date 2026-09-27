//! Print the identity hash of audio files `[SPEC-RLK-150]`.
//!
//! The command line is `cli::specs::hash_audio`; `hash_audio --help` prints
//! it. The hash itself is [`lempi_player::identity::hash_audio`], and this is
//! only its door for the Python tools: Vipunen's ingest asks here rather than
//! computing an identity key its own way `[GDE-FBD-040]`.

use std::io::{BufRead, Write};
use std::path::Path;

use lempi_player::cli::specs::hash_audio as opt;
use lempi_player::identity::{hash_audio, GENERATOR};

fn main() {
    let args = opt::SPEC.parse();
    if args.has(&opt::GENERATOR) {
        println!("{GENERATOR}");
        return;
    }
    match (args.text(&opt::FILE), args.has(&opt::STDIN)) {
        (Some(path), false) => match hash_audio(Path::new(path)) {
            Ok(h) => println!("{h}"),
            Err(e) => {
                eprintln!("{path}: {e}");
                std::process::exit(1);
            }
        },
        (None, true) => {
            // A failure is an answer, not the end: a caller hashing a library
            // wants every file's outcome, and reads each line as it comes.
            let out = std::io::stdout();
            let mut out = out.lock();
            for line in std::io::stdin().lock().lines() {
                let Ok(path) = line else { break };
                let path = path.trim_end_matches('\r');
                if path.is_empty() {
                    continue;
                }
                let said = match hash_audio(Path::new(path)) {
                    Ok(h) => writeln!(out, "{h}\t{path}"),
                    Err(e) => writeln!(out, "ERR\t{path}\t{}", e.replace(['\t', '\n'], " ")),
                };
                if said.and_then(|_| out.flush()).is_err() {
                    break;
                }
            }
        }
        _ => opt::SPEC.fail("give one of --file or --stdin"),
    }
}
