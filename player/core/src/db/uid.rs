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

/// How a passage just written came by its identity `[IMPL-UID-038]`.
pub enum Birth<'a> {
    /// The hub sent it, with its uid and birth key: stored as they are, once
    /// checked against each other. A node never computes a uid for a passage the
    /// hub already has `[IMPL-UID-052]`.
    Sent { uid: &'a str, audio_md5: &'a str, kind: &'a str, start_ms: i64, end_ms: i64, salt: u32 },
    /// Born here, of a file no one else has: computed now, with the lowest salt
    /// not already born on its key `[SPEC-UID-054]`.
    Here,
    /// Sent by a hub that gave it none: left empty, for the hub's patch to fill.
    Unknown,
}

/// Whether this catalogue's passages carry a uid yet.
pub fn has_uids(conn: &rusqlite::Connection) -> rusqlite::Result<bool> {
    let mut q = conn.prepare("SELECT 1 FROM pragma_table_info('passages') WHERE name = 'passage_uid'")?;
    q.exists([])
}

/// Give passage `passage_id`, just written, its identity as `birth` says. Nothing
/// where the catalogue's passages carry no uid. `Err` for a sent uid that is not
/// its own birth key's, which is refused by name, never stored.
pub fn stamp_birth(conn: &rusqlite::Connection, passage_id: i64, birth: &Birth) -> Result<(), String> {
    if !has_uids(conn).map_err(|e| e.to_string())? {
        return Ok(());
    }
    let (uid, md5, kind, start, end, salt) = match birth {
        Birth::Unknown => return Ok(()),
        Birth::Sent { uid, audio_md5, kind, start_ms, end_ms, salt } => {
            let want = passage_uid(audio_md5, kind, *start_ms, *end_ms, *salt);
            if want.as_deref() != Some(*uid) {
                return Err(format!("passage uid {uid} is not its birth key's ({audio_md5}, {kind}, {start_ms}, {end_ms}, \
                                    salt {salt}): refused, never stored"));
            }
            (uid.to_string(), audio_md5.to_string(), kind.to_string(), *start_ms, *end_ms, *salt)
        }
        Birth::Here => {
            let (md5, kind, start, end): (String, String, i64, i64) = conn
                .query_row("SELECT f.audio_md5, p.kind, p.start_ms, p.end_ms FROM passages p JOIN files f USING (file_id) \
                            WHERE p.passage_id = ?1", [passage_id], |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)))
                .map_err(|e| format!("passage {passage_id}: {e}"))?;
            let mut salt = 0u32;
            while conn
                .prepare_cached("SELECT 1 FROM passages WHERE birth_md5 = ?1 AND birth_kind = ?2 AND birth_start_ms = ?3 \
                                 AND birth_end_ms = ?4 AND birth_salt = ?5 AND passage_id <> ?6")
                .and_then(|mut q| q.exists(rusqlite::params![md5, kind, start, end, salt, passage_id]))
                .map_err(|e| e.to_string())?
            {
                salt += 1;
            }
            let uid = passage_uid(&md5, &kind, start, end, salt).ok_or(format!("passage {passage_id}: no birth key"))?;
            (uid, md5, kind, start, end, salt)
        }
    };
    conn.execute(
        "UPDATE passages SET passage_uid = ?2, birth_md5 = ?3, birth_kind = ?4, birth_start_ms = ?5, \
         birth_end_ms = ?6, birth_salt = ?7 WHERE passage_id = ?1",
        rusqlite::params![passage_id, uid, md5, kind, start, end, salt],
    )
    .map(|_| ())
    .map_err(|e| format!("passage {passage_id}: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn catalogue(uids: bool) -> rusqlite::Connection {
        let c = rusqlite::Connection::open_in_memory().unwrap();
        c.execute_batch("CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT);
                         CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT,
                             start_ms INTEGER, end_ms INTEGER);
                         INSERT INTO files VALUES (1, 'abc');
                         INSERT INTO passages VALUES (10, 1, 'radio', 0, 1000);").unwrap();
        if uids {
            c.execute_batch("ALTER TABLE passages ADD COLUMN passage_uid TEXT; ALTER TABLE passages ADD COLUMN birth_md5 TEXT;
                             ALTER TABLE passages ADD COLUMN birth_kind TEXT; ALTER TABLE passages ADD COLUMN birth_start_ms INTEGER;
                             ALTER TABLE passages ADD COLUMN birth_end_ms INTEGER; ALTER TABLE passages ADD COLUMN birth_salt INTEGER;").unwrap();
        }
        c
    }

    fn uid_of(c: &rusqlite::Connection, id: i64) -> Option<String> {
        c.query_row("SELECT passage_uid FROM passages WHERE passage_id = ?1", [id], |r| r.get(0)).unwrap()
    }

    #[test]
    fn a_catalogue_without_uids_is_left_as_it_is() {
        let c = catalogue(false);
        assert!(!has_uids(&c).unwrap());
        stamp_birth(&c, 10, &Birth::Here).unwrap();
    }

    #[test]
    fn a_passage_born_here_is_given_its_uid_and_a_second_birth_is_salted() {
        let c = catalogue(true);
        stamp_birth(&c, 10, &Birth::Here).unwrap();
        assert_eq!(uid_of(&c, 10), passage_uid("abc", "radio", 0, 1000, 0));
        c.execute_batch("UPDATE passages SET end_ms = 900 WHERE passage_id = 10;
                         INSERT INTO passages (passage_id, file_id, kind, start_ms, end_ms) VALUES (11, 1, 'radio', 0, 1000);").unwrap();
        stamp_birth(&c, 11, &Birth::Here).unwrap();
        assert_eq!(uid_of(&c, 11), passage_uid("abc", "radio", 0, 1000, 1), "born on a key already born on [SPEC-UID-054]");
    }

    #[test]
    fn a_sent_uid_is_stored_as_sent_and_one_not_its_keys_is_refused() {
        let c = catalogue(true);
        let good = passage_uid("hubmd5", "radio", 5, 900, 0).unwrap();
        stamp_birth(&c, 10, &Birth::Sent { uid: &good, audio_md5: "hubmd5", kind: "radio", start_ms: 5, end_ms: 900, salt: 0 }).unwrap();
        assert_eq!(uid_of(&c, 10).as_deref(), Some(good.as_str()), "the hub's uid, born of the hub's key, not this row's span");
        let err = stamp_birth(&c, 10, &Birth::Sent { uid: "0000", audio_md5: "hubmd5", kind: "radio", start_ms: 5, end_ms: 900, salt: 0 });
        assert!(err.is_err() && uid_of(&c, 10).as_deref() == Some(good.as_str()), "refused, and nothing changed");
        let c = catalogue(true);
        stamp_birth(&c, 10, &Birth::Unknown).unwrap();
        assert_eq!(uid_of(&c, 10), None, "a hub that sent none leaves it for its patch");
    }

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
