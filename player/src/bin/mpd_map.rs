//! Stage 0: can Lempi name a passage in MPD's terms? `[IMPL-MPD-010]`
//!
//! The command line is `cli::specs::mpd_map`; `mpd_map --help` prints it.
//!
//! `--uris` runs the same ladder against a **captured** URI list, one relative
//! path per line, as a Linux MPD would report them. That is how the
//! cross-platform half of stage 0 is measured without standing a second MPD up:
//! a bind-mounted container would not reproduce it, because Linux reading NTFS
//! sees the very `U+F03A` bytes Windows stored. Only a tree whose names were
//! *translated* on the way across shows the real condition.
//!
//! **Writes to nothing.** It reads `lempi.db`, reads MPD's database, walks the
//! resolution ladder `[SPEC-MPD-060]`, and prints how far each rung got. This
//! is the piece both SPEC015 and GUIDE007 say to build first, because a clean
//! trait over an unreliable mapping is a well-organised way to play the wrong
//! song.
//!
//! Ambiguity is counted apart from failure throughout. Two library rows
//! resolving to one URI is a different problem from a row resolving to nothing,
//! and averaging them into a "coverage" percentage would hide the one that
//! plays the wrong music.

use std::collections::HashMap;
use std::path::PathBuf;

use rusqlite::Connection;
use lempi_player::cli::specs::mpd_map as opt;
use lempi_player::mpd::Mpd;

/// A library row, as the ladder needs it.
struct Row {
    path: String,
    mbid: Option<String>,
}

fn main() {
    let args = opt::SPEC.parse();
    let db_path = PathBuf::from(args.need(&opt::LIBRARY));
    // Normalised the way MPD reports URIs: forward slashes, no trailing one.
    let root = args.need(&opt::ROOT).replace('\\', "/").trim_end_matches('/').to_string();
    let uri_file = args.text(&opt::URIS).map(str::to_string);
    let addr = args.need(&opt::ADDR).to_string();

    let db = Connection::open(&db_path).unwrap_or_else(|e| {
        eprintln!("cannot open {}: {e}", db_path.display());
        std::process::exit(1);
    });
    let rows: Vec<Row> = {
        let mut q = db
            .prepare(
                "SELECT f.path, (SELECT pr.mbid FROM passages p \
                   JOIN passage_recordings pr USING(passage_id) \
                   WHERE p.file_id = f.file_id LIMIT 1) \
                 FROM files f",
            )
            .expect("query files");
        let it = q
            .query_map([], |r| Ok(Row { path: r.get(0)?, mbid: r.get(1).ok() }))
            .expect("read files");
        it.filter_map(|r| r.ok()).collect()
    };

    let songs: Vec<lempi_player::mpd::Song> = match &uri_file {
        Some(f) => {
            let text = std::fs::read_to_string(f).unwrap_or_else(|e| {
                eprintln!("cannot read {f}: {e}");
                std::process::exit(1);
            });
            println!("URIs from {f} (captured; no tags, so rung 2 cannot run)");
            text.lines()
                .map(|l| l.trim())
                .filter(|l| !l.is_empty())
                .map(|l| lempi_player::mpd::Song {
                    uri: l.replace('\\', "/"),
                    ..Default::default()
                })
                .collect()
        }
        None => {
            let mut mpd = Mpd::connect(&addr).unwrap_or_else(|e| {
                eprintln!("{e}");
                std::process::exit(1);
            });
            println!("MPD protocol {} at {addr}", mpd.version);
            mpd.songs().unwrap_or_else(|e| {
                eprintln!("listallinfo: {e}");
                std::process::exit(1);
            })
        }
    };
    println!("library rows {}   MPD songs {}\n", rows.len(), songs.len());

    // Indexes for the two rungs. Both count collisions rather than silently
    // keeping the last writer: a duplicated key is a finding, not a detail.
    let mut by_uri: HashMap<&str, usize> = HashMap::new();
    let mut mbid_dupes = 0usize;
    let mut by_mbid: HashMap<&str, &str> = HashMap::new();
    for s in &songs {
        *by_uri.entry(s.uri.as_str()).or_insert(0) += 1;
        if let Some(m) = &s.recording_mbid {
            if by_mbid.insert(m.as_str(), s.uri.as_str()).is_some() {
                mbid_dupes += 1;
            }
        }
    }
    let with_mbid = songs.iter().filter(|s| s.recording_mbid.is_some()).count();

    let (mut by_prefix, mut by_mbid_hit, mut unresolved, mut ambiguous) = (0, 0, 0, 0);
    let mut pua_total = 0usize;
    let mut pua_resolved = 0usize;
    let mut examples: Vec<String> = Vec::new();

    for row in &rows {
        // Private-use codepoints are what Windows substitutes for characters a
        // filename may not contain `[SPEC-RLK-025]`. Counted separately here
        // because the whole question on this platform is whether MPD sees the
        // same substitution Lempi stored, or the character it stands for.
        let has_pua = row.path.chars().any(|c| ('\u{E000}'..='\u{F8FF}').contains(&c));
        if has_pua {
            pua_total += 1;
        }

        let norm = row.path.replace('\\', "/");
        let rel = norm.strip_prefix(&root).map(|r| r.trim_start_matches('/'));

        let hit = match rel {
            Some(r) if by_uri.contains_key(r) => {
                if by_uri[r] > 1 {
                    ambiguous += 1;
                }
                by_prefix += 1;
                true
            }
            _ => match row.mbid.as_deref().and_then(|m| by_mbid.get(m)) {
                Some(_) => {
                    by_mbid_hit += 1;
                    true
                }
                None => {
                    unresolved += 1;
                    if examples.len() < 5 {
                        examples.push(row.path.escape_debug().to_string());
                    }
                    false
                }
            },
        };
        if has_pua && hit {
            pua_resolved += 1;
        }
    }

    let n = rows.len().max(1);
    let pc = |v: usize| 100.0 * v as f64 / n as f64;
    println!("resolution ladder `[SPEC-MPD-060]`");
    println!("  1. same-tree prefix   {by_prefix:6}  ({:5.1}%)", pc(by_prefix));
    println!("  2. recording MBID     {by_mbid_hit:6}  ({:5.1}%)", pc(by_mbid_hit));
    println!("  3. unresolved         {unresolved:6}  ({:5.1}%)", pc(unresolved));
    println!();
    println!("  ambiguous (row -> >1 MPD song)   {ambiguous}");
    println!("  MPD songs carrying a recording MBID {with_mbid} of {}", songs.len());
    println!("  MPD MBIDs shared by >1 song         {mbid_dupes}");
    println!();
    println!("private-use paths `[SPEC-RLK-025]`");
    println!("  carrying one   {pua_total}");
    println!("  resolved       {pua_resolved}");
    if pua_total > 0 && pua_resolved == pua_total {
        println!("  -> MPD sees the SAME substitution Lempi stored, so the prefix holds here.");
        println!("     This is the same-platform case; a Linux MPD would not.");
    } else if pua_total > 0 {
        println!("  -> {} did NOT resolve: the substitution is not shared.", pua_total - pua_resolved);
    }
    for e in &examples {
        println!("\nunresolved e.g. {e}");
    }
}
