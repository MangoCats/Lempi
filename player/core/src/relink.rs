//! Bind a transported library to this machine's paths, and verify it arrived
//! `[SPEC012]`.
//!
//! A database knows what its music *is* — `audio_md5`, the MD5 of the encoded
//! audio stream with container and tags excluded. It knows nothing true about
//! where this machine keeps it: `path` is machine scope and never survives
//! transport `[SPEC-DF-030]`. So the paths are discovered here, by asking each
//! file on disk what it is.
//!
//! This is MuLibPlay's `scanFile` `[GDE-BMK-050]`, which hashed a *file*.
//! Hashing the encoded stream instead means the binding also survives the
//! metadata writes `[SPEC-DF-060]` uses as a transport.
//!
//! **It is also the integrity check `[SPEC-RLK-140]`.** A hash that matches
//! proves the bytes arrived intact, and the walk has already paid for it. That
//! forbids the obvious optimisation: skipping files whose path already looks
//! right would make `Matched` mean "the path resolves", which is an assumption
//! wearing the costume of a result.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

/// What became of one row or one file `[SPEC-RLK-050]`.
#[derive(Debug, Clone, PartialEq)]
pub enum Outcome {
    /// Hashed, and the hash agrees with the path already held.
    Matched,
    /// Hashed, found elsewhere. The path is updated to `found`.
    Moved { was: String, found: String },
    /// A row whose audio is nowhere on this machine.
    Missing { path: String },
    /// Bytes present where the row expects them, and they do not hash to it.
    /// A failed transfer, not a discovery `[SPEC-RLK-055]`.
    Corrupt { path: String, expected: String },
    /// Audio here that no row claims. Ingest, not relink `[SPEC-RLK-090]`.
    Unknown { path: String },
}

/// One row of the library, as relink needs it.
#[derive(Debug, Clone)]
pub struct Row {
    pub file_id: i64,
    pub audio_md5: String,
    pub path: String,
}

/// What the walk found: a hash for every audio file under the root.
#[derive(Debug, Clone)]
pub struct Found {
    pub path: String,
    pub audio_md5: String,
}

/// The decisions, and the updates they imply.
#[derive(Debug, Default)]
pub struct Plan {
    pub outcomes: Vec<(i64, Outcome)>,
    /// `(file_id, new_path)` — the only thing relink ever writes
    /// `[SPEC-RLK-100]`.
    pub updates: Vec<(i64, String)>,
    pub unknown: Vec<String>,
    /// Two files, one hash `[SPEC-RLK-120]`. The row binds to the first in
    /// walk order — which is sorted, so the choice is reproducible rather than
    /// whichever the filesystem happened to hand over first.
    pub duplicates: Vec<(String, String)>,
}

impl Plan {
    pub fn count(&self, want: fn(&Outcome) -> bool) -> usize {
        self.outcomes.iter().filter(|(_, o)| want(o)).count()
    }
}

/// Classify every row and every file found.
///
/// Pure: the walk and the database are the caller's business, so the decisions
/// can be tested without either. `on_disk` answers "is there a file here",
/// which is what separates a row that is `Missing` from one that is `Corrupt`.
pub fn plan(rows: &[Row], found: &[Found], on_disk: &dyn Fn(&str) -> bool) -> Plan {
    let by_md5: HashMap<&str, &Row> = rows.iter().map(|r| (r.audio_md5.as_str(), r)).collect();
    let mut seen: HashMap<i64, ()> = HashMap::new();
    let mut plan = Plan::default();

    for f in found {
        match by_md5.get(f.audio_md5.as_str()) {
            Some(row) => {
                // A second file hashing to a row already bound is a duplicate
                // on disk, not a second opinion about where the row lives.
                if seen.contains_key(&row.file_id) {
                    plan.duplicates.push((row.path.clone(), f.path.clone()));
                    continue;
                }
                seen.insert(row.file_id, ());
                if row.path == f.path {
                    plan.outcomes.push((row.file_id, Outcome::Matched));
                } else {
                    plan.outcomes.push((
                        row.file_id,
                        Outcome::Moved { was: row.path.clone(), found: f.path.clone() },
                    ));
                    plan.updates.push((row.file_id, f.path.clone()));
                }
            }
            // Nothing claims this audio. It may be new music, or it may be a
            // corrupted copy of something known -- which is decided below, from
            // the row's side, because only a row can say where it expected to
            // find itself.
            None => plan.unknown.push(f.path.clone()),
        }
    }

    for r in rows {
        if seen.contains_key(&r.file_id) {
            continue;
        }
        // The row went unhashed. If bytes sit where it says they should, they
        // are the wrong bytes -- a truncated or damaged copy. Reporting that as
        // `Unknown` would file a failed transfer as a library discovery.
        if on_disk(&r.path) {
            plan.outcomes.push((
                r.file_id,
                Outcome::Corrupt { path: r.path.clone(), expected: r.audio_md5.clone() },
            ));
        } else {
            plan.outcomes.push((r.file_id, Outcome::Missing { path: r.path.clone() }));
        }
    }
    plan
}

/// Extensions worth opening. Anything else under the root is not audio and is
/// not reported as unknown music -- cover art and stray text files are not
/// findings.
const AUDIO: &[&str] = &["mp3", "flac", "m4a", "mp4", "aac", "ogg", "oga", "opus", "wav"];

pub fn is_audio(p: &Path) -> bool {
    p.extension()
        .and_then(|e| e.to_str())
        .map(|e| AUDIO.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// Every audio file under `root`, depth first.
pub fn walk(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else { continue };
        for e in entries.flatten() {
            let p = e.path();
            match e.file_type() {
                Ok(t) if t.is_dir() => stack.push(p),
                Ok(t) if t.is_file() && is_audio(&p) => out.push(p),
                _ => {}
            }
        }
    }
    out.sort();
    out
}

/// The MD5 of a file's encoded audio stream, container and tags excluded.
///
/// **Via ffmpeg, because ffmpeg wrote the values we hold `[SPEC-RLK-080]`.**
/// Not because it is more correct: `audio_md5` is Essentia's `md5_encoded`,
/// and Essentia's audio I/O is FFmpeg/libav, so the stored corpus is an
/// ffmpeg artefact and agreeing with it is close to a tautology.
///
/// Symphonia is equally deterministic and equally self-consistent; it simply
/// stops at the last complete decodable frame where ffmpeg carries on to the
/// end of the file `[SPEC-RLK-085]`. On ~60 of 5,705 files here that trailing
/// remnant exists and the two part company. Had Symphonia written the
/// references, ffmpeg would be the one failing.
///
/// So this is not a free choice. It must stay the tool that produced the
/// incumbent values, and changing it means regenerating every hash at once
/// `[SPEC-RLK-086]`.
pub fn hash_encoded(path: &Path) -> Result<String, String> {
    let out = std::process::Command::new("ffmpeg")
        .args(["-v", "error", "-i"])
        .arg(path)
        .args(["-vn", "-c:a", "copy", "-f", "md5", "-"])
        .output()
        .map_err(|e| format!("ffmpeg not available: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout);
    // `MD5=<hex>`; anything else means ffmpeg could not read the stream, and
    // the stderr it produced is the only useful thing to say about it.
    match text.trim().rsplit_once('=') {
        Some((_, hex)) if hex.len() == 32 => Ok(hex.to_string()),
        _ => Err(String::from_utf8_lossy(&out.stderr).trim().to_string()),
    }
}

/// `ffmpeg -version`'s first line, asked once per process.
///
/// Cached because both questions below want it and neither wants to pay for a
/// second subprocess. The one consequence worth stating: ffmpeg installed
/// *during* a run is not noticed. Nothing here runs long enough for that to
/// matter -- a relink walk is one shot -- and the alternative is spawning a
/// process per file to learn something that cannot change usefully mid-walk.
fn version_line() -> Option<&'static str> {
    static LINE: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();
    LINE.get_or_init(|| {
        let out = std::process::Command::new("ffmpeg").arg("-version").output().ok()?;
        if !out.status.success() {
            return None;
        }
        Some(String::from_utf8_lossy(&out.stdout).lines().next()?.trim().to_string())
    })
    .as_deref()
}

/// Is the hasher present? Checked once, so a missing ffmpeg is one clear line
/// rather than 5,705 identical failures.
pub fn hasher_available() -> bool {
    version_line().is_some()
}

/// **Which hasher produced a value, as `name@version`** `[SPEC-RLK-086]`,
/// `[SPEC-RLK-150]`'s third precondition.
///
/// `audio_md5` is not a standard. It is "whatever this demuxer did", and
/// `[SPEC-RLK-080]` establishes that the incumbent 5,705 values are an
/// Essentia/libav artefact rather than a specified quantity. That makes an
/// ffmpeg upgrade able, in principle, to orphan rows -- and nothing downstream
/// would report it as anything but missing music, which is the failure this
/// records against.
///
/// **The whole version token, not a truncated one.** `8.0-full_build-www.gyan.dev`
/// and a distribution's `5.1.9-0+deb12u1` differ in exactly the build detail
/// that would explain a disagreement, so trimming to `8.0` would discard the
/// evidence this column exists to keep. `[SPEC-RLK-088]` compared 5.1.9 against
/// 8.0 across architectures and found none -- recording the string is what lets
/// the *next* such comparison be made from the database instead of from memory.
///
/// `tools/ingest_folder.py::ffmpeg_generator` computes the identical string on
/// Vipunen's side; the two are separate because Lempi and Vipunen share a
/// schema and no code `[GDE-ARC-018]`. Verified equal on this machine
/// 2026-09-22: both `ffmpeg@8.0-full_build-www.gyan.dev`.
///
/// `None` where ffmpeg cannot be asked, and a `None` is written as `NULL`
/// rather than as a guess.
pub fn hasher_generator() -> Option<String> {
    // "ffmpeg version 8.0-full_build-www.gyan.dev Copyright (c) 2000-2025 ..."
    let v = version_line()?.strip_prefix("ffmpeg version ")?.split_whitespace().next()?;
    Some(format!("ffmpeg@{v}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: i64, md5: &str, path: &str) -> Row {
        Row { file_id: id, audio_md5: md5.into(), path: path.into() }
    }
    fn found(md5: &str, path: &str) -> Found {
        Found { path: path.into(), audio_md5: md5.into() }
    }
    fn nothing_on_disk(_: &str) -> bool {
        false
    }

    #[test]
    fn a_file_where_the_row_says_it_is_needs_no_change() {
        let p = plan(&[row(1, "aa", "/audio/a.mp3")], &[found("aa", "/audio/a.mp3")],
                     &nothing_on_disk);
        assert_eq!(p.outcomes, vec![(1, Outcome::Matched)]);
        assert!(p.updates.is_empty(), "a matched row must not be written");
    }

    /// The whole point: the same audio at a different path is the same music.
    #[test]
    fn the_same_audio_elsewhere_is_a_move_not_a_loss() {
        let p = plan(
            &[row(1, "aa", r"C:\Users\x\Music\a.mp3")],
            &[found("aa", "/srv/library/audio/a.mp3")],
            &nothing_on_disk,
        );
        assert_eq!(p.updates, vec![(1, "/srv/library/audio/a.mp3".to_string())]);
        assert!(matches!(p.outcomes[0].1, Outcome::Moved { .. }));
    }

    /// A Pi holding part of a library is a deployment, not a fault
    /// `[SPEC-RLK-060]`.
    #[test]
    fn audio_that_did_not_travel_is_missing_not_corrupt() {
        let p = plan(&[row(1, "aa", "/audio/a.mp3"), row(2, "bb", "/audio/b.mp3")],
                     &[found("aa", "/audio/a.mp3")], &nothing_on_disk);
        assert_eq!(p.count(|o| matches!(o, Outcome::Missing { .. })), 1);
        assert_eq!(p.count(|o| matches!(o, Outcome::Corrupt { .. })), 0);
    }

    /// The distinction that earns relink its second job `[SPEC-RLK-055]`. A
    /// truncated copy hashes to nothing known; read naively that is "new
    /// music", which files a failed transfer as a discovery.
    #[test]
    fn bytes_that_do_not_hash_to_their_row_are_corrupt_not_unknown() {
        let here = |p: &str| p == "/audio/a.mp3";
        let p = plan(
            &[row(1, "aa", "/audio/a.mp3")],
            &[found("truncated-hash", "/audio/a.mp3")],
            &here,
        );
        assert_eq!(
            p.outcomes,
            vec![(1, Outcome::Corrupt { path: "/audio/a.mp3".into(), expected: "aa".into() })]
        );
        // It is still listed as unclaimed audio, because it is -- but the row's
        // verdict is what a reader acts on.
        assert_eq!(p.unknown, vec!["/audio/a.mp3".to_string()]);
        assert!(p.updates.is_empty(), "corrupt audio must never be bound");
    }

    #[test]
    fn audio_no_row_claims_is_unknown_and_binds_nothing() {
        let p = plan(&[], &[found("zz", "/audio/new.mp3")], &nothing_on_disk);
        assert_eq!(p.unknown, vec!["/audio/new.mp3".to_string()]);
        assert!(p.updates.is_empty());
    }

    /// Running it twice must change nothing the second time `[SPEC-RLK-110]`.
    #[test]
    fn a_second_run_over_a_bound_library_writes_nothing() {
        let rows = vec![row(1, "aa", "/audio/a.mp3"), row(2, "bb", "/audio/b.mp3")];
        let disk = vec![found("aa", "/audio/a.mp3"), found("bb", "/audio/b.mp3")];
        let first = plan(&rows, &disk, &nothing_on_disk);
        assert!(first.updates.is_empty());
        let second = plan(&rows, &disk, &nothing_on_disk);
        assert_eq!(first.outcomes, second.outcomes);
    }

    /// Two copies of one recording bind the row once, and say so
    /// `[SPEC-RLK-120]`.
    #[test]
    fn a_second_file_with_the_same_hash_is_a_duplicate_not_a_rebind() {
        let p = plan(
            &[row(1, "aa", "/old/a.mp3")],
            &[found("aa", "/audio/first.mp3"), found("aa", "/audio/second.mp3")],
            &nothing_on_disk,
        );
        assert_eq!(p.updates, vec![(1, "/audio/first.mp3".to_string())],
                   "the row binds once, to the first in walk order");
        assert_eq!(p.duplicates, vec![("/old/a.mp3".to_string(), "/audio/second.mp3".to_string())]);
        assert_eq!(p.count(|o| matches!(o, Outcome::Moved { .. })), 1);
        assert_eq!(p.count(|o| matches!(o, Outcome::Missing { .. })), 0,
                   "the row is bound, so it is not also missing");
    }

    #[test]
    fn only_audio_is_walked() {
        assert!(is_audio(Path::new("/x/a.mp3")));
        assert!(is_audio(Path::new("/x/a.FLAC")), "extension case must not matter");
        // Cover art and strays are not unclaimed music.
        assert!(!is_audio(Path::new("/x/folder.jpg")));
        assert!(!is_audio(Path::new("/x/Thumbs.db")));
        assert!(!is_audio(Path::new("/x/notes")));
    }

    /// `[SPEC-RLK-150]` precondition 3. Two claims, and the second is the one
    /// that matters: the string must *agree with* `hasher_available`, so a
    /// machine that can hash always records what hashed, and one that cannot
    /// records nothing rather than a guess.
    ///
    /// Deliberately not asserting a version. This runs on Windows 8.0, on the
    /// Pi's 5.1.9 and in a Debian container, and pinning any of those would be
    /// testing the build machine `[SPEC-RLK-088]`.
    #[test]
    fn the_hasher_names_itself_and_its_version_or_says_nothing() {
        let g = hasher_generator();
        assert_eq!(g.is_some(), hasher_available(),
                   "a hasher that can run must be nameable, and one that cannot must not be named");
        if let Some(g) = g {
            let (name, version) = g.split_once('@').expect("the form is name@version");
            assert_eq!(name, "ffmpeg");
            assert!(!version.is_empty(), "a version of nothing is not a version");
            // The whole token, build suffix and all: it is the part that would
            // explain a disagreement, so it must not be trimmed away.
            assert!(!version.contains(char::is_whitespace), "one token, got {version:?}");
        }
    }
}
