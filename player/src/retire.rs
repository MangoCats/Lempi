//! The files the hub retired, moved to this node's own `_Retired` `[SPEC-RTF-020..035]`.
//!
//! A merge on the hub retires the files it no longer plays `[SPEC-AGN-030]`,
//! and the next star sync removes their rows here. The files themselves stayed
//! in the album's folder, beside the disc that replaced them, catalogued by
//! nothing -- on `bose`, *Highway 61*'s six from 2026-10-30. The hub now sends,
//! with its commit, the list of what it retired and the node still has a path
//! for (`retire`), and this moves each one, after the catalogue has landed:
//!
//! * only when no row here names its audio any more -- a file moved while still
//!   catalogued reads as *missing*, and *Send what's missing* sends it again
//!   `[GDE-INT-150]`;
//! * only when it is where it was said to be, with the hub's bytes: the same
//!   size, then the same SHA-256;
//! * only by a rename on the same file system -- all of it or none, never a copy.
//!
//! It goes to `<music folder>/_Retired/<album folder>/<retired_at>/<name>`, the
//! hub's own layout, never over another file, and is never deleted. Anything
//! else leaves the file where it is, and says why `[SPEC-RTF-030]`. A move is
//! read back -- the source gone, the destination there, its hash again -- so
//! `moved` is a measurement and not an intention `[GDE-DEP-070]`.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::path::{Path, PathBuf};

use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Value};

use crate::bundle::sha256_file;
use crate::relink::RETIRED;

/// `[SPEC-RTF-060]` What the snapshot says this build can do: 1 moves the files
/// the hub retired. A node that does not say so is sent no list.
pub const RETIRE_FILES: i64 = 1;

/// What MPD is told inside a node's `_Retired`: ignore everything there. Its
/// player plays by catalogue, so this is tidiness, not a fix `[SPEC-RTF-040]`.
const MPDIGNORE: &str = "*\n";

/// Whether the catalogue still names `md5`. `None` when it cannot be read --
/// which is not "no", and moves nothing.
fn catalogued(library: &Path, md5: &str) -> Option<bool> {
    let c = Connection::open_with_flags(library, OpenFlags::SQLITE_OPEN_READ_ONLY).ok()?;
    c.query_row("SELECT EXISTS (SELECT 1 FROM files WHERE audio_md5 = ?1)", [md5], |r| r.get::<_, bool>(0)).ok()
}

/// The music folder this file sits in, by the hub's `rel_path` taken off the end
/// of its path; `None` when the path does not end in it.
fn music_root(path: &str, rel: &str) -> Option<String> {
    let p = path.replace('\\', "/");
    let rel = rel.trim_start_matches('/');
    p.strip_suffix(rel).filter(|r| r.ends_with('/')).map(|r| r.trim_end_matches('/').to_string())
}

/// Where it goes: never a name already taken, `name (2).ext` and on.
fn destination(root: &str, rel: &str, date: &str) -> PathBuf {
    let rel = Path::new(rel);
    let dir = Path::new(root).join(RETIRED).join(rel.parent().unwrap_or(Path::new(""))).join(date);
    let name = rel.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let mut out = dir.join(&name);
    let (stem, ext) = match name.rsplit_once('.') {
        Some((s, e)) => (s.to_string(), format!(".{e}")),
        None => (name.clone(), String::new()),
    };
    let mut k = 2;
    while out.exists() {
        out = dir.join(format!("{stem} ({k}){ext}"));
        k += 1;
    }
    out
}

/// A date the hub sent, made safe as one folder name: digits and dashes only.
fn date_of(e: &Value) -> String {
    let d: String = e["retired_at"].as_str().unwrap_or("").chars().take(10)
        .filter(|c| c.is_ascii_digit() || *c == '-').collect();
    if d.is_empty() { "undated".into() } else { d }
}

/// `.mpdignore` in the music folder's `_Retired`, once.
fn ignore_for_mpd(root: &str) {
    let f = Path::new(root).join(RETIRED).join(".mpdignore");
    if !f.exists() {
        let _ = std::fs::write(f, MPDIGNORE);
    }
}

/// One file: its outcome, and where it went when it moved.
fn one(library: &Path, e: &Value, move_now: bool) -> Value {
    let md5 = e["audio_md5"].as_str().unwrap_or("");
    let from = e["path"].as_str().unwrap_or("");
    let said = |outcome: &str, why: Option<String>, to: Option<&Path>| {
        json!({"audio_md5": md5, "outcome": outcome, "from": from,
               "to": to.map(|p| p.to_string_lossy().into_owned()), "why": why})
    };
    let (Some(rel), false) = (e["rel_path"].as_str(), md5.is_empty() || from.is_empty()) else {
        return said("refused", Some("the entry names no audio, path or place".into()), None);
    };
    // A rehearsal comes before the catalogue patch removes the row, so only a
    // commit -- after it has landed -- can ask.
    if move_now {
        match catalogued(library, md5) {
            Some(false) => {}
            Some(true) => return said("still catalogued", None, None),
            None => return said("refused", Some("the catalogue could not be read".into()), None),
        }
    }
    let src = Path::new(from);
    let meta = match std::fs::metadata(src) {
        Ok(m) if m.is_file() => m,
        _ => return said("not there", None, None),
    };
    if e["size_bytes"].as_u64().is_some_and(|n| n != meta.len()) {
        return said("bytes differ", Some(format!("{} bytes, the hub's {}", meta.len(), e["size_bytes"])), None);
    }
    let Some(root) = music_root(from, rel) else {
        return said("elsewhere", Some(format!("the path does not end in {rel}")), None);
    };
    let to = destination(&root, rel, &date_of(e));
    if !move_now {
        return said("to move", None, Some(&to));
    }
    // The hub's bytes, or nothing is touched: a file of the same name and size
    // is not therefore the same file.
    let want = e["sha256"].as_str().unwrap_or("");
    match sha256_file(src) {
        Ok(h) if !want.is_empty() && h == want => {}
        Ok(_) => return said("bytes differ", Some("its SHA-256 is not the hub's".into()), None),
        Err(err) => return said("refused", Some(err), None),
    }
    if let Some(dir) = to.parent() {
        if let Err(err) = std::fs::create_dir_all(dir) {
            return said("refused", Some(format!("{}: {err}", dir.display())), None);
        }
    }
    ignore_for_mpd(&root);
    // A rename, never a copy: across file systems it fails, and the file stays.
    if let Err(err) = std::fs::rename(src, &to) {
        return said("refused", Some(format!("not moved: {err}")), None);
    }
    // Read back.
    let back = !src.exists() && sha256_file(&to).is_ok_and(|h| h == want);
    if !back {
        return said("refused", Some("moved, but it does not read back as the hub's bytes".into()), Some(&to));
    }
    tracing::info!("retired: {from} -> {} [SPEC-RTF-020]", to.display());
    said("moved", None, Some(&to))
}

/// Every file in `entries`, as the hub sent them. `move_now` false is a
/// rehearsal: what would happen, nothing moved, nothing hashed. Returns
/// `{"files": [{audio_md5, outcome, from, to, why}]}`.
pub fn retire(library: &Path, entries: &[Value], move_now: bool) -> Value {
    json!({"files": entries.iter().map(|e| one(library, e, move_now)).collect::<Vec<_>>()})
}

/// `[SPEC-RTF-035]` The moves a commit made, as its inverse keeps them: each
/// `{from, to}`, so an undo puts the files back where the rows it restores
/// say they are.
pub fn moves(result: &Value) -> Value {
    json!(result["files"].as_array().into_iter().flatten()
        .filter(|f| f["outcome"] == "moved")
        .map(|f| json!({"from": f["from"], "to": f["to"]}))
        .collect::<Vec<_>>())
}

/// Put back what `moves` lists, newest first. Never over a file already there.
pub fn undo(moved: &Value) -> Result<usize, String> {
    let mut n = 0;
    for m in moved.as_array().into_iter().flatten().rev() {
        let (Some(from), Some(to)) = (m["from"].as_str(), m["to"].as_str()) else { continue };
        if Path::new(from).exists() {
            return Err(format!("{from} is there again: not put back over it"));
        }
        std::fs::rename(to, from).map_err(|e| format!("{to} -> {from}: {e}"))?;
        n += 1;
    }
    Ok(n)
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Fx {
        dir: PathBuf,
        lib: PathBuf,
        music: PathBuf,
    }

    fn fx(name: &str) -> Fx {
        let dir = std::env::temp_dir().join(format!("lempi-retire-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let music = dir.join("Music");
        std::fs::create_dir_all(music.join("Artist/Album")).unwrap();
        let lib = dir.join("library.db");
        Connection::open(&lib).unwrap()
            .execute_batch("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, path TEXT);").unwrap();
        Fx { dir, lib, music }
    }

    impl Fx {
        fn file(&self, rel: &str, bytes: &[u8]) -> (String, String, u64) {
            let p = self.music.join(rel);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(&p, bytes).unwrap();
            (p.to_string_lossy().into_owned(), sha256_file(&p).unwrap(), bytes.len() as u64)
        }
        fn entry(&self, md5: &str, rel: &str, bytes: &[u8]) -> Value {
            let (path, sha, size) = self.file(rel, bytes);
            json!({"audio_md5": md5, "sha256": sha, "size_bytes": size, "path": path, "rel_path": rel,
                   "retired_at": "2026-10-30"})
        }
    }

    impl Drop for Fx {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    fn outcomes(v: &Value) -> Vec<String> {
        v["files"].as_array().unwrap().iter().map(|f| f["outcome"].as_str().unwrap().to_string()).collect()
    }

    /// The file the hub retired, uncatalogued here, with its bytes: moved to the
    /// node's own `_Retired` in the hub's layout, read back, MPD told to leave it.
    #[test]
    fn a_retired_file_is_moved_to_retired_and_read_back() {
        let f = fx("moved");
        let e = f.entry("old", "Artist/Album/01. Song.mp3", b"the old rip");
        let r = retire(&f.lib, &[e.clone()], true);
        assert_eq!(outcomes(&r), ["moved"], "{r}");
        let to = f.music.join("_Retired/Artist/Album/2026-10-30/01. Song.mp3");
        assert!(to.is_file() && !Path::new(e["path"].as_str().unwrap()).exists(), "moved, not copied");
        assert_eq!(r["files"][0]["to"].as_str().unwrap().replace('\\', "/"), to.to_string_lossy().replace('\\', "/"));
        assert_eq!(std::fs::read_to_string(f.music.join("_Retired/.mpdignore")).unwrap(), MPDIGNORE);
        // And an undo puts it back.
        assert_eq!(undo(&moves(&r)), Ok(1));
        assert!(Path::new(e["path"].as_str().unwrap()).is_file() && !to.exists(), "put back");
    }

    /// Each reason to leave a file where it is, said: still catalogued, not there,
    /// other bytes of the same size, a path that is not where the hub files it --
    /// and a rehearsal moves and hashes nothing.
    #[test]
    fn anything_else_leaves_the_file_and_says_why() {
        let f = fx("left");
        let kept = f.entry("kept", "Artist/Album/02. Kept.mp3", b"still played here");
        Connection::open(&f.lib).unwrap()
            .execute("INSERT INTO files (audio_md5, path) VALUES ('kept', ?1)", [kept["path"].as_str().unwrap()]).unwrap();
        let gone = json!({"audio_md5": "gone", "sha256": "x", "size_bytes": 3, "rel_path": "Artist/Album/03. Gone.mp3",
                          "path": f.music.join("Artist/Album/03. Gone.mp3").to_string_lossy(), "retired_at": "2026-10-30"});
        let mut other = f.entry("other", "Artist/Album/04. Other.mp3", b"same size!");
        other["sha256"] = json!("not these bytes");
        let mut elsewhere = f.entry("away", "Artist/Album/05. Away.mp3", b"filed elsewhere");
        elsewhere["rel_path"] = json!("Someone Else/Album/05. Away.mp3");
        let all = [kept.clone(), gone, other.clone(), elsewhere];
        let rehearsed = retire(&f.lib, &all, false);
        assert_eq!(outcomes(&rehearsed), ["to move", "not there", "to move", "elsewhere"], "{rehearsed}");
        assert!(!f.music.join(RETIRED).exists(), "a rehearsal moves nothing");
        let r = retire(&f.lib, &all, true);
        assert_eq!(outcomes(&r), ["still catalogued", "not there", "bytes differ", "elsewhere"], "{r}");
        assert!(Path::new(kept["path"].as_str().unwrap()).is_file() && Path::new(other["path"].as_str().unwrap()).is_file(),
                "every file left where it was");
        assert_eq!(moves(&r), json!([]));
    }

    /// A name already taken in `_Retired` -- the same day's second retirement -- is
    /// never written over; and the hub's date cannot climb out of the folder.
    #[test]
    fn nothing_is_written_over_and_the_date_stays_a_folder_name() {
        let f = fx("over");
        f.file("_Retired/Artist/Album/2026-10-30/01. Song.mp3", b"already retired");
        let e = f.entry("old", "Artist/Album/01. Song.mp3", b"the old rip");
        let r = retire(&f.lib, &[e], true);
        assert_eq!(outcomes(&r), ["moved"], "{r}");
        assert_eq!(std::fs::read(f.music.join("_Retired/Artist/Album/2026-10-30/01. Song.mp3")).unwrap(), b"already retired");
        assert!(f.music.join("_Retired/Artist/Album/2026-10-30/01. Song (2).mp3").is_file());
        assert_eq!(date_of(&json!({"retired_at": "../../etc"})), "undated", "dots and slashes dropped");
        assert_eq!(date_of(&json!({"retired_at": "2026-10-30T22:19"})), "2026-10-30", "the day alone");
        assert_eq!(date_of(&json!({})), "undated");
    }
}
