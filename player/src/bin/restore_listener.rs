//! Put a listener-state snapshot back `[REQ-LIB-160]`.
//!
//! The command line is `cli::specs::restore_listener`; `restore_listener
//! --help` prints it.
//!
//! A backup nobody has ever restored is a file of unknown value, and the moment
//! it matters is the worst moment to find out the procedure does not work. So
//! this is meant to be run for practice, not only in an emergency.
//!
//! **A report by default.** The numbers it prints are the ones `--apply` would
//! produce, because both are measured from the same queries before anything is
//! written. `--apply` first keeps the current state as a pre-restore snapshot,
//! which rotation never prunes, so restoring the wrong one can be undone.
//!
//! **Stop the player first.** A running player keeps writing the listener file
//! and holds its queue and its Director in memory, so a restore beneath it would
//! be partly undone and partly ignored. On Linux this refuses while a `lempi`
//! process is running; elsewhere it cannot tell, and says so `[GDE-DEP-060]`.
//!
//! Until 2026-10-08 this was the cargo example `restore_listener`.

use std::path::{Path, PathBuf};

use lempi_player::backup;
use lempi_player::cli::specs::restore_listener as opt;

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

/// Is a player running on this machine? Read where it can be, and `None` where
/// it cannot -- said, never guessed.
#[cfg(target_os = "linux")]
fn player_running() -> Option<bool> {
    let dir = std::fs::read_dir("/proc").ok()?;
    Some(dir.flatten().any(|e| {
        std::fs::read_to_string(e.path().join("comm")).is_ok_and(|c| c.trim() == "lempi")
    }))
}

#[cfg(not(target_os = "linux"))]
fn player_running() -> Option<bool> {
    None
}

fn list(listener: &Path) {
    let dir = backup::dir_for(listener);
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
        let name = s.file_name().unwrap_or_default().to_string_lossy();
        match backup::inspect(s) {
            Ok(i) => println!(
                "  {name}  {} plays, {} preferences, last play {}",
                i.plays, i.preferences, when(i.last_play)
            ),
            Err(e) => println!("  {name}  UNREADABLE: {e}"),
        }
    }
}

fn main() {
    let args = opt::SPEC.parse();
    let listener = PathBuf::from(args.need(&opt::LISTENER));

    if args.has(&opt::LIST) {
        list(&listener);
        return;
    }

    // The two conditions the grammar cannot state: required unless `--list`
    // already returned above.
    let Some(snap) = args.text(&opt::SNAPSHOT).map(PathBuf::from) else {
        opt::SPEC.fail("`--snapshot` is required unless `--list` is given")
    };
    let Some(library) = args.text(&opt::LIBRARY).map(PathBuf::from) else {
        opt::SPEC.fail("`--library` is required unless `--list` is given")
    };
    let apply = args.has(&opt::APPLY);

    match backup::inspect(&snap) {
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

    if apply {
        match player_running() {
            Some(true) => {
                eprintln!(
                    "refusing: a `lempi` process is running, and it would write over the \
                     restore and keep its own state. Stop it first (on an appliance, \
                     `sudo systemctl stop lempi`)."
                );
                std::process::exit(1);
            }
            Some(false) => {}
            None => eprintln!(
                "note: this platform cannot be asked whether a player is using {}; \
                 stop it before restoring.",
                listener.display()
            ),
        }
        // Before overwriting the listening, keep what is about to be replaced.
        // Restoring the wrong snapshot is a mistake someone should be able to undo.
        match backup::snapshot_before_restore(&listener) {
            Ok(p) => println!("current state saved first to {}", p.display()),
            Err(e) => {
                eprintln!("refusing to restore: could not save current state first ({e})");
                std::process::exit(1);
            }
        }
    }

    match backup::restore(&snap, &listener, &library, apply) {
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
                println!("\nnothing was written. Re-run with --apply to do it.");
            }
        }
        Err(e) => {
            eprintln!("restore failed: {e}");
            std::process::exit(1);
        }
    }
}
