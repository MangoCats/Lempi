//! Bind a library to this machine's paths, and verify it arrived `[SPEC012]`.
//!
//! The command line is `cli::specs::relink`; `relink --help` prints it.
//!
//! Reports by default and writes nothing. `--apply` performs the path updates
//! it found; everything else it only ever reports.

use std::path::{Path, PathBuf};
use std::time::Instant;

use rusqlite::Connection;
use lempi_player::cli::specs::relink as opt;
use lempi_player::relink::{hash_encoded, hasher_available, plan, walk, Found, Outcome, Row};

fn main() {
    let args = opt::SPEC.parse();
    let db_path = PathBuf::from(args.need(&opt::LIBRARY));
    let root = PathBuf::from(args.need(&opt::AUDIO_ROOT));
    let apply = args.has(&opt::APPLY);
    let quick = args.has(&opt::QUICK);

    if !hasher_available() {
        eprintln!("relink needs ffmpeg on PATH to read encoded audio streams.");
        eprintln!("  Debian/Raspberry Pi OS:  sudo apt install ffmpeg");
        std::process::exit(1);
    }
    let db = match Connection::open(&db_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("cannot open {}: {e}", db_path.display());
            std::process::exit(1);
        }
    };
    let rows: Vec<Row> = {
        let mut q = db.prepare("SELECT file_id, audio_md5, path FROM files").unwrap();
        let it = q
            .query_map([], |r| {
                Ok(Row { file_id: r.get(0)?, audio_md5: r.get(1)?, path: r.get(2)? })
            })
            .unwrap();
        it.filter_map(|r| r.ok()).collect()
    };
    let files = walk(&root);
    println!("library: {} rows   audio under {}: {}", rows.len(), root.display(), files.len());
    if quick {
        // Said in its own output, because the whole hazard is a check that
        // reports success without observing anything `[SPEC-RLK-140]`.
        println!("--quick: files already bound are NOT hashed. THIS RUN VERIFIES NOTHING.");
    }

    let bound: std::collections::HashSet<&str> = rows.iter().map(|r| r.path.as_str()).collect();
    let started = Instant::now();
    let mut found = Vec::with_capacity(files.len());
    let mut unreadable = Vec::new();
    for (i, f) in files.iter().enumerate() {
        let as_str = f.to_string_lossy().to_string();
        if quick && bound.contains(as_str.as_str()) {
            // Take the database's word for it. That is exactly what --quick
            // buys and exactly what it costs.
            if let Some(r) = rows.iter().find(|r| r.path == as_str) {
                found.push(Found { path: as_str, audio_md5: r.audio_md5.clone() });
            }
            continue;
        }
        match hash_encoded(f) {
            Ok(md5) => found.push(Found { path: as_str, audio_md5: md5 }),
            // A file that cannot be opened at all is neither bound nor
            // dismissed: it is reported, and left for a person.
            Err(e) => unreadable.push((as_str, e)),
        }
        if i > 0 && i % 250 == 0 {
            let per = started.elapsed().as_secs_f64() / (i as f64 + 1.0);
            let left = (files.len() - i) as f64 * per;
            println!("  {}/{}  {:.0}s remaining", i, files.len(), left);
        }
    }
    println!("hashed {} files in {:.0}s", found.len(), started.elapsed().as_secs_f64());

    let on_disk = |p: &str| Path::new(p).is_file();
    let p = plan(&rows, &found, &on_disk);

    let matched = p.count(|o| matches!(o, Outcome::Matched));
    let moved = p.count(|o| matches!(o, Outcome::Moved { .. }));
    let missing = p.count(|o| matches!(o, Outcome::Missing { .. }));
    let corrupt = p.count(|o| matches!(o, Outcome::Corrupt { .. }));
    println!();
    println!("  matched  {matched}");
    println!("  moved    {moved}");
    println!("  missing  {missing}");
    println!("  corrupt  {corrupt}");
    println!("  unknown  {}", p.unknown.len());
    if !p.duplicates.is_empty() {
        println!("  duplicate {}", p.duplicates.len());
    }
    if !unreadable.is_empty() {
        println!("  unreadable {}", unreadable.len());
    }

    // The bad news in full. A count of corrupt files is not actionable; a list
    // of them is.
    for (_, o) in &p.outcomes {
        if let Outcome::Corrupt { path, expected } = o {
            println!("CORRUPT  {path}  (expected {expected})");
        }
    }
    for (path, e) in unreadable.iter().take(20) {
        println!("UNREADABLE  {path}: {e}");
    }
    for (bound, also) in p.duplicates.iter().take(10) {
        println!("duplicate  {also}
           same audio as {bound}");
    }
    for path in p.unknown.iter().take(20) {
        println!("unknown  {path}");
    }
    if p.unknown.len() > 20 {
        println!("... and {} more unknown", p.unknown.len() - 20);
    }
    // The whole list, on request. A report truncated at twenty is the same
    // hazard in miniature: it looks complete and is not.
    {
        if let Some(dest) = args.text(&opt::REPORT) {
            let mut out = String::new();
            for (id, o) in &p.outcomes {
                out.push_str(&format!("{id}	{o:?}
"));
            }
            for u in &p.unknown {
                out.push_str(&format!("-	Unknown {u}
"));
            }
            for (u, e) in &unreadable {
                out.push_str(&format!("-	Unreadable {u} :: {e}
"));
            }
            match std::fs::write(dest, out) {
                Ok(()) => println!("full report written to {dest}"),
                Err(e) => eprintln!("could not write {dest}: {e}"),
            }
        }
    }

    if !apply {
        println!("\n{} path(s) would be updated. Re-run with --apply to write them.",
                 p.updates.len());
        return;
    }
    let mut wrote = 0;
    for (file_id, path) in &p.updates {
        match db.execute("UPDATE files SET path = ?1 WHERE file_id = ?2",
                         rusqlite::params![path, file_id]) {
            Ok(n) => wrote += n,
            Err(e) => eprintln!("update {file_id}: {e}"),
        }
    }
    println!("\nupdated {wrote} path(s).");
    if corrupt > 0 {
        // Exit non-zero so a script cannot treat a damaged library as a good
        // one just because the paths were written.
        std::process::exit(1);
    }
}
