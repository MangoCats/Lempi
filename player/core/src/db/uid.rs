//! A passage's permanent identity, the same on every node `[SPEC-UID-030]`.
//!
//! The first 128 bits of SHA-256 over the passage's birth key, as 32
//! lowercase hex characters `[IMPL-UID-030]`. Derived from what the passage
//! is, so two nodes making the same cut get the same identity without
//! talking; stored when the passage is created and never recalculated, so it
//! survives every boundary edit afterwards, which the span that named a
//! passage until now could not.
//!
//! `tools/passage_uid.py` is this function's twin, and
//! `fixtures/passage_uid/vectors.json` holds the two equal: the test below
//! reads it, and `tools/test_passage_uid.py` writes and checks it.

use sha2::{Digest, Sha256};

/// Names the encoding, so a change to it is a new name rather than a silent
/// difference between two builds.
pub const DOMAIN: &str = "lempi-passage-uid/1";
/// The unit separator: in no md5, kind or decimal.
const SEP: char = '\u{1f}';

/// The uid of a passage born as `(audio_md5, kind, start_ms, end_ms)`, with
/// `salt` 0 unless a collision rule of `[SPEC-UID-050]` gave it another.
/// `None` for a key that could make two births one text: an empty or
/// separator-bearing md5 or kind.
pub fn passage_uid(audio_md5: &str, kind: &str, start_ms: i64, end_ms: i64, salt: u32) -> Option<String> {
    if audio_md5.is_empty() || kind.is_empty() || audio_md5.contains(SEP) || kind.contains(SEP) {
        return None;
    }
    let text = format!("{DOMAIN}{SEP}{audio_md5}{SEP}{kind}{SEP}{start_ms}{SEP}{end_ms}{SEP}{salt}");
    let digest = Sha256::digest(text.as_bytes());
    Some(digest[..16].iter().map(|b| format!("{b:02x}")).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn the_shared_vectors_hold_in_rust_as_in_python() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../fixtures/passage_uid/vectors.json");
        let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
        let doc: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(doc["encoding"], DOMAIN, "the fixture is for this encoding");
        let vectors = doc["vectors"].as_array().unwrap();
        assert!(vectors.len() >= 8, "every case the Python test writes is here");
        for v in vectors {
            let got = passage_uid(
                v["audio_md5"].as_str().unwrap(),
                v["kind"].as_str().unwrap(),
                v["start_ms"].as_i64().unwrap(),
                v["end_ms"].as_i64().unwrap(),
                v["salt"].as_u64().unwrap() as u32,
            );
            assert_eq!(got.as_deref(), v["uid"].as_str(), "{}", v["case"]);
        }
    }

    #[test]
    fn a_key_that_could_be_two_births_is_refused() {
        assert!(passage_uid("", "radio", 0, 1, 0).is_none());
        assert!(passage_uid("ab\u{1f}c", "radio", 0, 1, 0).is_none());
        assert!(passage_uid("ab", "", 0, 1, 0).is_none());
        assert!(passage_uid("ab", "ra\u{1f}dio", 0, 1, 0).is_none());
        let u = passage_uid("ab", "radio", 0, 1, 0).unwrap();
        assert_eq!(u.len(), 32);
        assert_ne!(Some(u), passage_uid("ab", "radio", 0, 1, 1), "a salt is another identity");
    }
}
