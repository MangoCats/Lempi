//! What a generation run did, said the same way every time `[PI3-API-030]`.
//!
//! Four modules write files into folders Lempi does not own — [`crate::cue`],
//! [`crate::covers`], [`crate::lyrics_cache`], [`crate::lyrics_sidecar`] — and
//! each grew its own report. The shape was identical and the **words were not**:
//! one called an untouched file `skipped`, another `kept`, a third
//! `left as they were`, all meaning the same thing, while `covers` used
//! `had_cover` for it and `skipped` for something else entirely.
//!
//! A listener reads those sentences one after another on the settings page. They
//! should differ where the runs differ and nowhere else, so the sentence is
//! built once here and each module supplies only its own nouns and reasons.
//!
//! The four modules also share a tail: once a module has decided *this*
//! candidate should be written, every one of them dry-runs, writes and reports
//! the same way. [`write_or_report`] is that tail, factored out so it can only
//! drift once instead of four times. The decision above it — *which*
//! candidates, and why the others were passed over — stays in each module,
//! because that part genuinely differs.

use std::path::Path;

/// A tally of one run over a folder.
#[derive(Debug, Default, PartialEq)]
pub struct Written {
    /// Files created.
    pub written: usize,
    /// Files already holding exactly what this would have written. The measure
    /// of idempotence: a second run should report everything here.
    pub unchanged: usize,
    /// Candidates deliberately not written, counted by reason, in the order the
    /// reasons were first given.
    ///
    /// **Reasons, not a single number.** "473 skipped" tells a listener nothing
    /// about whether their library is fine or their settings are wrong.
    pub passed: Vec<(&'static str, usize)>,
    /// What went wrong, named. Failures are rare and specific, so they are
    /// listed rather than counted.
    pub failed: Vec<String>,
}

impl Written {
    pub fn wrote(&mut self) {
        self.written += 1;
    }

    pub fn already_current(&mut self) {
        self.unchanged += 1;
    }

    /// Count one candidate against a reason it was not written.
    ///
    /// `why` completes the phrase "*N* …", so it reads as
    /// `"75 shared a folder"`.
    pub fn passed_over(&mut self, why: &'static str) {
        match self.passed.iter_mut().find(|(w, _)| *w == why) {
            Some((_, n)) => *n += 1,
            None => self.passed.push((why, 1)),
        }
    }

    pub fn failed(&mut self, what: String) {
        self.failed.push(what);
    }

    /// How many of `why` were passed over. For tests, which should assert on
    /// the reason rather than on a position in a list.
    pub fn count_of(&self, why: &str) -> usize {
        self.passed.iter().find(|(w, _)| *w == why).map_or(0, |(_, n)| *n)
    }

    /// One sentence, for the person who asked for the run.
    ///
    /// `noun` is what was written, singular — "cue sheet", "cover", "song".
    /// Silent about failures when there were none, because a trailing
    /// "0 failed" reads as an invitation to worry.
    pub fn summary(&self, noun: &str) -> String {
        let mut s = format!("{} {noun}(s) written, {} already current", self.written, self.unchanged);
        for (why, n) in &self.passed {
            s.push_str(&format!(", {n} {why}"));
        }
        if !self.failed.is_empty() {
            s.push_str(&format!(", {} failed", self.failed.len()));
        }
        s
    }
}

/// Write `bytes` to `path` and record the outcome — or, for a dry run, count
/// what would have been written without touching the filesystem at all.
///
/// Shared by [`crate::cue`], [`crate::covers`], [`crate::lyrics_cache`] and
/// [`crate::lyrics_sidecar`]: the one part of "write this candidate" that was
/// identical in all four already, ahead of this being pulled out.
pub fn write_or_report(path: &Path, bytes: &[u8], dry_run: bool, rep: &mut Written) {
    if dry_run {
        rep.wrote();
        return;
    }
    match std::fs::write(path, bytes) {
        Ok(()) => rep.wrote(),
        Err(e) => rep.failed(format!("{}: {e}", path.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The reasons keep the order they were first given, so a summary does not
    /// reshuffle itself between runs.
    #[test]
    fn reasons_are_counted_and_keep_their_order() {
        let mut r = Written::default();
        r.passed_over("shared a folder");
        r.passed_over("without art");
        r.passed_over("shared a folder");

        assert_eq!(r.passed, vec![("shared a folder", 2), ("without art", 1)]);
        assert_eq!(r.count_of("shared a folder"), 2);
        assert_eq!(r.count_of("never mentioned"), 0);
    }

    #[test]
    fn the_sentence_names_every_reason() {
        let mut r = Written::default();
        for _ in 0..3 {
            r.wrote();
        }
        r.already_current();
        r.passed_over("shared a folder");

        assert_eq!(
            r.summary("cover"),
            "3 cover(s) written, 1 already current, 1 shared a folder"
        );
    }

    /// Nothing to say about failures is said by not saying it.
    #[test]
    fn failures_appear_only_when_there_are_some() {
        let mut r = Written::default();
        r.wrote();
        assert!(!r.summary("file").contains("failed"));
        r.failed("C:/x/y.cue: access denied".into());
        assert!(r.summary("file").ends_with(", 1 failed"));
    }

    fn tmp() -> std::path::PathBuf {
        let p = std::env::temp_dir().join(format!(
            "lempi_report_{}_{}",
            std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()
        ));
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    /// A dry run counts as written but never touches the filesystem.
    #[test]
    fn a_dry_run_counts_without_writing() {
        let dir = tmp();
        let path = dir.join("x.txt");
        let mut r = Written::default();
        write_or_report(&path, b"hello", true, &mut r);
        assert_eq!(r.written, 1);
        assert!(!path.exists(), "dry run must not create the file");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A real run writes the bytes and reports one write.
    #[test]
    fn a_real_run_writes_and_reports() {
        let dir = tmp();
        let path = dir.join("x.txt");
        let mut r = Written::default();
        write_or_report(&path, b"hello", false, &mut r);
        assert_eq!(r.written, 1);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "hello");
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// A write that cannot land — no such directory — is reported as a named
    /// failure, not silently dropped.
    #[test]
    fn a_failed_write_is_reported_by_name() {
        let dir = tmp();
        let path = dir.join("nowhere").join("x.txt"); // parent does not exist
        let mut r = Written::default();
        write_or_report(&path, b"hello", false, &mut r);
        assert_eq!(r.written, 0);
        assert_eq!(r.failed.len(), 1);
        assert!(r.failed[0].contains("x.txt"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
