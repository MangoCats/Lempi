//! Put a listener-state snapshot back `[REQ-LIB-160]`.
//!
//! A backup nobody has ever restored is a file of unknown value, and the moment
//! it matters is the worst moment to find out the procedure does not work. So
//! this is meant to be run for practice, not only in an emergency.
//!
//!     restore_listener --list                       what is there
//!     restore_listener <snapshot>                   REHEARSE: report, write nothing
//!     restore_listener <snapshot> --commit          do it
//!
//! Rehearsal is the default. The numbers it prints are the ones a real restore
//! would produce, because both are measured from the same query before anything
//! is written.

use std::path::{Path, PathBuf};

use lempi_player::backup;

fn when(t: Option<i64>) -> String {
    // Whole days, which is all anyone needs to recognise a snapshot.
    match t {
        Some(s) => {
            let days = s / 86_400;
            format!("day {days} (unix {s})")
        }
        None => "never".into(),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let db = PathBuf::from(
        std::env::var("LEMPI_DB").unwrap_or_else(|_| "data/lempi_new.db".into()),
    );

    if args.is_empty() || args[0] == "--list" {
        let dir = backup::dir_for(&db);
        let Ok(entries) = std::fs::read_dir(&dir) else {
            eprintln!("no snapshots in {}", dir.display());
            std::process::exit(1);
        };
        let mut snaps: Vec<PathBuf> = entries
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "db"))
            .collect();
        snaps.sort();
        println!("{} snapshot(s) in {}", snaps.len(), dir.display());
        for s in snaps.iter().rev() {
            match backup::inspect(s) {
                Ok(i) => println!(
                    "  {}  {} plays, {} preferences, last play {}",
                    s.file_name().unwrap_or_default().to_string_lossy(),
                    i.plays, i.preferences, when(i.last_play)
                ),
                Err(e) => println!("  {}  UNREADABLE: {e}",
                                   s.file_name().unwrap_or_default().to_string_lossy()),
            }
        }
        return;
    }

    let snap = Path::new(&args[0]);
    let commit = args.iter().any(|a| a == "--commit");

    match backup::inspect(snap) {
        Ok(i) => println!(
            "snapshot: {} plays ({} to {}), {} preferences, {} likes, {} programmes",
            i.plays, when(i.first_play), when(i.last_play),
            i.preferences, i.likes, i.programs
        ),
        Err(e) => {
            eprintln!("cannot read {}: {e}", snap.display());
            std::process::exit(1);
        }
    }

    // Before overwriting the listening, keep what is about to be replaced.
    // Restoring the wrong snapshot is a mistake someone should be able to undo.
    if commit {
        match backup::snapshot_before_restore(&db) {
            Ok(p) => println!("current state saved first to {}", p.display()),
            Err(e) => {
                eprintln!("refusing to restore: could not save current state first ({e})");
                std::process::exit(1);
            }
        }
    }

    match backup::restore(snap, &db, commit) {
        Ok(r) => {
            println!(
                "{} {} table(s): {} plays, {} re-pointed to new passage ids, {} orphaned",
                if r.committed { "restored" } else { "would restore" },
                r.tables, r.plays, r.remapped, r.orphaned
            );
            if r.orphaned > 0 {
                println!(
                    "  {} play(s) name a recording the library no longer holds. They are \
                     kept as they are: a play that happened still happened.",
                    r.orphaned
                );
            }
            if !r.committed {
                println!("\nnothing was written. Re-run with --commit to do it.");
            }
        }
        Err(e) => {
            eprintln!("restore failed: {e}");
            std::process::exit(1);
        }
    }
}
