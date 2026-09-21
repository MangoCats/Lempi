//! Read every file's own tags into the library.
//!
//! The player does this by itself in the background at startup, incrementally
//! `[REQ-VIS-180]`. This tool exists for the cases that are not a startup: a
//! library being prepared before it is ever played, or one whose files have
//! been re-tagged and need reading again.
//!
//! The command line is `cli::specs::tagscan`; `tagscan --help` prints it.

use lempi_player::cli::specs::tagscan as opt;

fn main() {
    let args = opt::SPEC.parse();
    let db = std::path::Path::new(args.need(&opt::LIBRARY));

    if args.has(&opt::ALL) {
        // Forget what is known, so every file is read afresh.
        match lempi_player::db::Library::open_writable(db) {
            Ok(lib) => {
                if let Err(e) = lib.forget_tags() {
                    eprintln!("clear tags: {e}");
                    std::process::exit(1);
                }
            }
            Err(e) => {
                eprintln!("open {}: {e}", db.display());
                std::process::exit(1);
            }
        }
    }

    match lempi_player::tags::backfill(db, true) {
        Ok((0, _)) => println!("nothing to scan: every file already has tags"),
        Ok((n, art)) => println!("{n} file(s) scanned, {art} with cover art"),
        Err(e) => {
            eprintln!("{e}");
            std::process::exit(1);
        }
    }
}
