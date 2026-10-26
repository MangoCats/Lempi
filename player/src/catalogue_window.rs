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

/// Whether `p` can be opened for writing: false on a read-only mount (EROFS).
/// Opening for write changes nothing; the commit's own connection writes.
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

/// Whether a SQLite file is in WAL mode, from its own header: bytes 18 and 19
/// are the write and read format versions, 1 for a rollback journal and 2 for
/// WAL. Read from the file rather than asked of a connection, so it needs no
/// lock the player's own connection holds. `None` when the header cannot be read.
pub(crate) fn wal_mode(p: &Path) -> Option<bool> {
    use std::io::Read;
    let mut h = [0u8; 20];
    std::fs::File::open(p).and_then(|mut f| f.read_exact(&mut h)).ok()?;
    Some(h[18] == 2 || h[19] == 2)
}

