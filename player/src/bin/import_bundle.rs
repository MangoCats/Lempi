//! Receive a bundle from Vipunen `[SPEC014]`, `[SPEC-SUI-130]`.
//!
//! The command line is `cli::specs::import_bundle`; `import_bundle --help`
//! prints it.
//!
//! Reports by default and writes nothing. `--apply` performs the import, in one
//! transaction: either the whole bundle lands or the library is untouched
//! `[SPEC-PL-070]`.

use std::path::{Path, PathBuf};

use rusqlite::Connection;
use lempi_player::cli::specs::import_bundle as opt;
use lempi_player::bundle::{import, unacceptable, Landed};

fn main() {
    let args = opt::SPEC.parse();
    let db_path = PathBuf::from(args.need(&opt::LIBRARY));
    let mut db = match Connection::open(&db_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("cannot open {}: {e}", db_path.display());
            std::process::exit(1);
        }
    };

    // What this library holds, for the sender to subtract `[SPEC-SUI-105]`.
    // A file, not a service: the exporter reads it, and no Lempi process is
    // ever asked a question `[SPEC-SUI-025]`.
    if args.has(&opt::INVENTORY) {
        let mut q = db.prepare("SELECT audio_md5 FROM files ORDER BY audio_md5").unwrap();
        let rows = q.query_map([], |r| r.get::<_, String>(0)).unwrap();
        for r in rows.flatten() {
            println!("{r}");
        }
        return;
    }

    // The one condition the grammar cannot state: required unless
    // `--inventory` already returned above. Refused through the same usage
    // text every other refusal prints.
    let Some(bundle) = args.text(&opt::BUNDLE).map(PathBuf::from) else {
        opt::SPEC.fail("`--bundle` is required unless `--inventory` is given")
    };
    let payload_path = bundle.join("payload.json");
    let text = match std::fs::read_to_string(&payload_path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("cannot read {}: {e}", payload_path.display());
            std::process::exit(1);
        }
    };
    let doc: serde_json::Value = match serde_json::from_str(&text) {
        Ok(d) => d,
        Err(e) => {
            eprintln!("{} is not valid JSON: {e}", payload_path.display());
            std::process::exit(1);
        }
    };

    // Where the audio actually is. Default: the bundle's own `audio/`. On an
    // appliance where rsync has already placed it under the library root, name
    // that instead -- the hash decides either way `[SPEC-PL-025]`.
    let audio_root =
        args.text(&opt::AUDIO_ROOT).map(PathBuf::from).unwrap_or_else(|| bundle.join("audio"));
    let apply = args.has(&opt::APPLY);

    if !lempi_player::relink::hasher_available() {
        eprintln!("import needs ffmpeg on PATH to verify arriving audio.");
        std::process::exit(1);
    }

    println!("payload  {}", payload_path.display());
    println!("audio    {}", audio_root.display());
    let refused = unacceptable(&doc);
    if !refused.is_empty() {
        // Whole, and it names what was unmet. Nothing is written.
        println!("\nREFUSED — the library is unchanged.");
        for r in refused.iter().take(30) {
            println!("  {r}");
        }
        if refused.len() > 30 {
            println!("  ... and {} more", refused.len() - 30);
        }
        std::process::exit(1);
    }

    let rep = match import(&mut db, &doc, &text, Path::new(&audio_root), apply) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("import failed: {e}");
            std::process::exit(1);
        }
    };

    let imported = rep.count(|o| matches!(o, Landed::Imported));
    let already = rep.count(|o| matches!(o, Landed::Already));
    let waiting = rep.count(|o| matches!(o, Landed::AwaitingAudio { .. }));
    let corrupt = rep.count(|o| matches!(o, Landed::Corrupt { .. }));
    println!();
    println!("  imported  {imported}");
    println!("  already   {already}");
    println!("  awaiting  {waiting}");
    println!("  corrupt   {corrupt}");
    if rep.kept_local > 0 {
        println!("  kept local {} (manual outranks an arriving value)", rep.kept_local);
    }

    for (md5, o) in &rep.outcomes {
        match o {
            Landed::Corrupt { expected, found } => {
                println!("CORRUPT   {md5}  expected {expected}, found {found}")
            }
            Landed::AwaitingAudio { at } => println!("AWAITING  {md5}  no audio at {at}"),
            _ => {}
        }
    }

    // Say the scope, not just the verdict `[SPEC-RLK-140]`, `[SPEC-PL-085]`.
    // Two denominators live here and an earlier draft mixed them, printing
    // "verified 4 of 3": the number hashed belongs to the bundle, the library
    // total to the library, and a ratio across the two means nothing.
    let total: i64 = db
        .query_row("SELECT count(*) FROM files", [], |r| r.get(0))
        .unwrap_or(0);
    println!(
        "\nhashed {} of this bundle's {} encoding(s); the library holds {total} file(s), \
         and the rest of it was not examined.",
        imported + corrupt,
        rep.outcomes.len()
    );

    if !apply {
        println!("nothing was written. Re-run with --apply to do it.");
    } else {
        println!("wrote {} row(s).", rep.rows_written);
    }
    if corrupt > 0 {
        std::process::exit(1);
    }
}
