//! What each reader of the `Snapshot` needs from it `[GDE-HST-060]`.
//!
//! The snapshot has three readers that are not the server: `fbui`, an echo
//! follower, and the skins. Each keeps its own idea of the snapshot's shape
//! (`fbui` a hand-copied struct, a follower a two-field one, a skin whatever its
//! JavaScript reads), and nothing compared those ideas with the server's.
//! `fixtures/snapshot/consumers.json` names, per reader, the fields it reads.
//! The server's side checks it carries every one of them; each reader's side
//! checks the list is what it actually reads. A field dropped or renamed on
//! either side then fails a test instead of a render.
//!
//! **Compatibility is presence, not a number** -- the rule `[SPEC-PL-065]` set
//! for the payload. A field whose value is `null` is present.
//!
//! Read at run time, not with `include_str!`, so building the player never
//! depends on the fixture being there; only a test that asks for it does.

use std::collections::BTreeMap;

use serde::de::{self, Deserialize, Visitor};

/// The fixture, as the repository has it.
pub const FIXTURE: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../fixtures/snapshot/consumers.json");

/// The fields `consumer` is recorded as reading, sorted, as paths: `title`,
/// `skip.fade_ms`, `queue[].qid` -- the last meaning *that field of every
/// element*.
///
/// # Panics
///
/// When the fixture is missing, malformed, or does not name `consumer`. It is
/// a test's helper; a test that cannot read its fixture has nothing to check,
/// and must say so rather than pass.
pub fn required(consumer: &str) -> Vec<String> {
    let text = std::fs::read_to_string(FIXTURE)
        .unwrap_or_else(|e| panic!("cannot read {FIXTURE}: {e}"));
    let mut all: BTreeMap<String, Vec<String>> =
        serde_json::from_str(&text).unwrap_or_else(|e| panic!("{FIXTURE} is not valid: {e}"));
    let mut paths = all
        .remove(consumer)
        .unwrap_or_else(|| panic!("{FIXTURE} names no consumer `{consumer}`"));
    // Sorted, as `paths` is, so a list edited out of order compares as a set.
    paths.sort();
    paths
}

/// Every consumer the fixture names.
pub fn consumers() -> Vec<String> {
    let text = std::fs::read_to_string(FIXTURE)
        .unwrap_or_else(|e| panic!("cannot read {FIXTURE}: {e}"));
    let all: BTreeMap<String, Vec<String>> =
        serde_json::from_str(&text).unwrap_or_else(|e| panic!("{FIXTURE} is not valid: {e}"));
    all.into_keys().collect()
}

/// The field names a `#[derive(Deserialize)]` struct asks for, as serde itself
/// knows them -- renames applied, `#[serde(default)]` fields included.
///
/// This is how a reader's side of the contract is taken from the reader's own
/// type rather than from a second list typed beside it. It asks the struct to
/// deserialize from a deserializer that records the names serde passes to
/// `deserialize_struct` and then refuses. A struct using `#[serde(flatten)]`
/// asks for a map instead and yields nothing -- which the caller's comparison
/// then reports, rather than passing.
pub fn fields_of<T: for<'de> Deserialize<'de>>() -> &'static [&'static str] {
    let mut seen: &'static [&'static str] = &[];
    let _ = T::deserialize(Recorder(&mut seen));
    seen
}

struct Recorder<'a>(&'a mut &'static [&'static str]);

impl<'de> de::Deserializer<'de> for Recorder<'_> {
    type Error = de::value::Error;

    fn deserialize_any<V: Visitor<'de>>(self, _: V) -> Result<V::Value, Self::Error> {
        Err(de::Error::custom("not a struct"))
    }

    fn deserialize_struct<V: Visitor<'de>>(
        self,
        _: &'static str,
        fields: &'static [&'static str],
        _: V,
    ) -> Result<V::Value, Self::Error> {
        *self.0 = fields;
        Err(de::Error::custom("recorded"))
    }

    serde::forward_to_deserialize_any! {
        bool i8 i16 i32 i64 i128 u8 u16 u32 u64 u128 f32 f64 char str string
        bytes byte_buf option unit unit_struct newtype_struct seq tuple
        tuple_struct map enum identifier ignored_any
    }
}

/// A reader's paths, built from its structs: the top-level fields, then each
/// nested struct's under its prefix (`queue[]`, `skip`). Sorted, so two lists
/// compare as sets and a difference prints readably.
pub fn paths(top: &[&str], nested: &[(&str, &[&str])]) -> Vec<String> {
    let mut out: Vec<String> = top.iter().map(|f| f.to_string()).collect();
    for (prefix, fields) in nested {
        out.extend(fields.iter().map(|f| format!("{prefix}.{f}")));
    }
    out.sort();
    out
}

/// Whether `path` is present in a serialized snapshot. An array segment
/// (`queue[]`) needs at least one element to look inside, and the element's
/// absence is reported as the test data's failing, not the server's.
pub fn present(snapshot: &serde_json::Value, path: &str) -> Result<(), String> {
    let mut here = snapshot;
    for seg in path.split('.') {
        let (name, each) = match seg.strip_suffix("[]") {
            Some(n) => (n, true),
            None => (seg, false),
        };
        here = here
            .get(name)
            .ok_or_else(|| format!("`{path}`: the snapshot has no `{name}`"))?;
        if each {
            here = here
                .get(0)
                .ok_or_else(|| format!("`{path}`: `{name}` is empty in the test data, so nothing was checked"))?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(serde::Deserialize)]
    #[allow(dead_code)]
    struct Probe {
        a: u32,
        #[serde(rename = "b_renamed")]
        b: Option<String>,
        #[serde(default)]
        c: Vec<u8>,
    }

    /// The recorder reports what serde asks for, renames and defaults included
    /// -- or the reader-side checks built on it compare against nothing.
    #[test]
    fn the_recorder_reads_the_names_serde_uses() {
        assert_eq!(fields_of::<Probe>(), ["a", "b_renamed", "c"]);
    }

    #[test]
    fn a_path_is_found_through_objects_and_arrays() {
        let v = serde_json::json!({ "a": null, "q": [{ "id": 1 }], "e": [], "s": { "x": 0 } });
        assert!(present(&v, "a").is_ok(), "null is present");
        assert!(present(&v, "q[].id").is_ok());
        assert!(present(&v, "s.x").is_ok());
        assert!(present(&v, "q[].missing").is_err());
        assert!(present(&v, "e[].id").unwrap_err().contains("empty in the test data"));
        assert!(present(&v, "gone").is_err());
    }
}
