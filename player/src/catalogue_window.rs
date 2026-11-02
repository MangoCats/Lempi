//! The catalogue opened for writing where it is kept read-only
//! `[SPEC-STAR-104]`.
//!
//! On `bose` the catalogue's partition is mounted read-only, and anything that
//! must write it opens it through the one root verb for that, `lempi-btctl
//! library-rw`, and closes it again. The star sync's commit did this alone
//! until 2026-10-26; the tag scan does it too now `[REQ-VIS-180]`, after 340
//! files had arrived there with no tags, the scan failing on each at every
//! start. One window, so both open and close it the same way.

use std::path::Path;

#[cfg(all(feature = "appliance", not(test)))]
use serde_json::Value;

/// Whether `p` can be written: false on a read-only mount (EROFS).
///
/// **Asked of the kernel, never by opening the file** `[SPEC-STAR-104]`. A
/// POSIX advisory lock belongs to the process, and closing *any* descriptor
/// on a file releases every lock the process holds on it -- SQLite's
/// included. This was `OpenOptions::new().write(true).open(p)` until
/// 2026-11-01, called by the tag scan at every start and by every sync's
/// snapshot, with the player's own connections open on the catalogue. Each
/// call silently dropped their shared lock, so the next writer to close --
/// `import_bundle`, a commit -- took itself for the last connection and
/// deleted the catalogue's `-wal` and `-shm` beneath them. Every connection
/// this process held, and every one it opened after, then read the
/// catalogue through the index of a WAL that no longer existed: "database
/// disk image is malformed", a rebuild refused, the hourly integrity check
/// unable to run, until a restart. Seen on lempi02w and lp3-wifi that day;
/// reproduced on lempi02w with two processes and one `open`/`close`, and not
/// without it.
#[cfg(unix)]
pub(crate) fn writable(p: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;
    let Ok(path) = std::ffi::CString::new(p.as_os_str().as_bytes()) else { return false };
    // SAFETY: `path` is a valid NUL-terminated string that outlives the
    // call, and access(2) only reads it. It opens nothing.
    unsafe { libc::access(path.as_ptr(), libc::W_OK) == 0 }
}

/// Windows locks belong to the handle that took them, so opening and closing
/// another handle costs SQLite nothing there.
#[cfg(not(unix))]
pub(crate) fn writable(p: &Path) -> bool {
    std::fs::OpenOptions::new().write(true).open(p).is_ok()
}

/// The catalogue's partition opened for writing, where it is read-only `[SPEC-STAR-104]`.
pub(crate) struct Window {
    opened: bool,
}

impl Window {
    /// Opened only when a catalogue is to be written and cannot be where it is.
    pub(crate) fn open(library: &Path, needed: bool) -> Result<Window, String> {
        let opened = if needed && !writable(library) { library_rw(library, true)? } else { false };
        Ok(Window { opened })
    }

    /// `[SPEC-STAR-104]`: a catalogue on read-only media must be in rollback-
    /// journal mode, because a WAL file there has no sidecar to open and the
    /// player crash-loops `[BOS-RUN-092]`. Checked before the partition goes back,
    /// and left read-write -- harmless to the player -- rather than closed over
    /// a file that would not open. A check that cannot run says so, too.
    pub(crate) fn close(self, library: &Path) -> Option<String> {
        if !self.opened {
            return None;
        }
        match wal_mode(library) {
            Some(false) => library_rw(library, false)
                .err()
                .map(|e| format!("the catalogue's partition could not be returned to read-only: {e}")),
            Some(true) => Some("the catalogue is in WAL mode, which read-only media cannot open: its partition was left read-write [BOS-RUN-092]".into()),
            None => Some("the catalogue's journal mode could not be read: its partition was left read-write".into()),
        }
    }
}

/// Make the catalogue's partition writable through the one root verb for it;
/// true when it was read-only and so must be put back.
#[cfg(all(feature = "appliance", not(test)))]
fn library_rw(_library: &Path, on: bool) -> Result<bool, String> {
    let out = std::process::Command::new("sudo")
        .args(["-n", crate::bluetooth::HELPER, "library-rw", if on { "on" } else { "off" }])
        .output()
        .map_err(|e| format!("{}: {e}", crate::bluetooth::HELPER))?;
    let v: Value = serde_json::from_slice(&out.stdout)
        .map_err(|_| format!("library-rw answered {:?}", String::from_utf8_lossy(&out.stdout).trim()))?;
    if v["ok"] != true {
        return Err(format!("library-rw: {}", v["error"].as_str().unwrap_or("failed")));
    }
    Ok(v["was"] == "ro")
}

/// A host without the root helper keeps its catalogue on a writable disk.
#[cfg(all(not(feature = "appliance"), not(test)))]
fn library_rw(_library: &Path, _on: bool) -> Result<bool, String> {
    Ok(false)
}

// In a test, the "partition" is the file's own permission bit, and each call is recorded.
#[cfg(test)]
thread_local! {
    pub(crate) static WINDOW_CALLS: std::cell::RefCell<Vec<&'static str>> = const { std::cell::RefCell::new(Vec::new()) };
}

#[cfg(test)]
fn library_rw(library: &Path, on: bool) -> Result<bool, String> {
    WINDOW_CALLS.with(|c| c.borrow_mut().push(if on { "on" } else { "off" }));
    let mut perm = std::fs::metadata(library).map_err(|e| e.to_string())?.permissions();
    let was_ro = perm.readonly();
    perm.set_readonly(!on);
    std::fs::set_permissions(library, perm).map_err(|e| e.to_string())?;
    Ok(was_ro)
}

/// Whether a SQLite file is in WAL mode. `None` when it cannot be asked.
///
/// **Asked of SQLite, not read from the file's header** -- see [`writable`]:
/// reading the header opened a descriptor on a catalogue the player holds
/// open, and closing it dropped the player's locks. SQLite keeps a
/// descriptor of its own open until no lock in this process needs it, so a
/// connection opened and closed here costs the others nothing. Asked only
/// while the partition is writable, where a WAL catalogue opens.
pub(crate) fn wal_mode(p: &Path) -> Option<bool> {
    // A path that is not a file is answered without opening anything.
    if !p.is_file() {
        return None;
    }
    let c = rusqlite::Connection::open_with_flags(p, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).ok()?;
    let mode: String = c.query_row("PRAGMA journal_mode", [], |r| r.get(0)).ok()?;
    Some(mode.eq_ignore_ascii_case("wal"))
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;

    const WRITER: &str = "LEMPI_TEST_CATALOGUE_WRITER";

    /// The other process, as `import_bundle` is: writes the catalogue, and
    /// closes. Run by the test below as a child of this test binary; on its
    /// own it does nothing.
    #[test]
    fn writer_helper() {
        let Ok(lib) = std::env::var(WRITER) else { return };
        let c = rusqlite::Connection::open(&lib).unwrap();
        c.execute_batch("BEGIN IMMEDIATE; INSERT INTO t SELECT a + 1000, b FROM t; COMMIT;").unwrap();
        drop(c);
    }

    /// **Asking whether the catalogue can be written, or how it journals,
    /// costs the player's connections nothing** `[SPEC-STAR-104]`. With the
    /// player's shape -- a listener with the catalogue attached read-only --
    /// both questions are asked, then another process writes the catalogue
    /// and closes. Its `-wal` and `-shm` must still be there, and the
    /// player's view right. When `writable` opened the file, the other
    /// process's close deleted them under the player (reproduced on lempi02w,
    /// 2026-11-01), and the next write made its catalogue read malformed.
    #[test]
    fn asking_about_the_catalogue_leaves_the_players_locks_alone() {
        let dir = std::env::temp_dir().join(format!("lempi-window-locks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let (lib, lis) = (dir.join("library.db"), dir.join("listener.db"));
        let c = rusqlite::Connection::open(&lib).unwrap();
        c.execute_batch(
            "PRAGMA journal_mode=WAL; CREATE TABLE t (a INTEGER, b TEXT);
             WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 1000)
             INSERT INTO t SELECT i, printf('%0200d', i) FROM n;",
        )
        .unwrap();
        drop(c);
        let player = rusqlite::Connection::open(&lis).unwrap();
        crate::db::attach_library(&player, &lis, &lib).unwrap();
        let rows = |c: &rusqlite::Connection| c.query_row("SELECT COUNT(*) FROM lib.t", [], |r| r.get::<_, i64>(0)).unwrap();
        assert_eq!(rows(&player), 1000);

        assert!(writable(&lib), "a writable catalogue reads as one");
        assert_eq!(wal_mode(&lib), Some(true));

        let out = std::process::Command::new(std::env::current_exe().unwrap())
            .args(["--exact", "catalogue_window::tests::writer_helper", "--test-threads=1"])
            .env(WRITER, &lib)
            .output()
            .unwrap();
        assert!(out.status.success(), "the writer: {}", String::from_utf8_lossy(&out.stdout));
        let side = |s: &str| dir.join(format!("library.db-{s}")).exists();
        assert!(side("wal") && side("shm"), "the other process's close took the -wal and -shm from under the player");
        assert_eq!(rows(&player), 2000, "and the player reads what was written");
        drop(player);
        let _ = std::fs::remove_dir_all(&dir);
    }
}

