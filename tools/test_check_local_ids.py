#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for `check_local_ids.py` `[IMPL-UID-082]`: each of its four rules finds
what it is for, in a source written as the tree writes it, and lets pass what
it is not -- a skipped field, a test module, a helper. And against the real
tree: a number put back where Stage C took it out is found.

    python tools/test_check_local_ids.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import check_local_ids as cli  # noqa: E402

FAILED = []


def check(cond, msg):
    if not cond:
        FAILED.append(msg)
        print(f"  FAIL  {msg}")


def found(path, src):
    return [what for _, what in cli.findings(path, src)]


def test_a_serialized_number():
    print("rule 1: a serialized passage_id or file_id, and nothing skipped")
    src = '''
#[derive(Debug, serde::Serialize)]
pub struct Row {
    pub passage_id: i64,
    pub title: String,
}
'''
    check(len(found("player/src/web/x.rs", src)) == 1, f"a serialized number is found: {found('player/src/web/x.rs', src)}")
    skipped = src.replace("    pub passage_id: i64,", "    /// The engine's number.\n    #[serde(skip)]\n    pub passage_id: i64,")
    check(found("player/src/web/x.rs", skipped) == [], "one kept with #[serde(skip)] is not")
    cond = src.replace("    pub passage_id: i64,", '    #[serde(skip_serializing_if = "Option::is_none")]\n    pub passage_id: Option<i64>,')
    check(len(found("player/src/web/x.rs", cond)) == 1, "skip_serializing_if still sends it, and is found")
    plain = src.replace("serde::Serialize", "Clone")
    check(found("player/src/web/x.rs", plain) == [], "a struct that does not serialize is not")
    fid = src.replace("passage_id", "file_id")
    check(len(found("player/core/src/db/x.rs", fid)) == 1, "a file number in lempi-core is found too")
    j = 'fn f() -> Json { serde_json::json!({"passage_id": n, "title": t}) }\n'
    check(len(found("player/src/web/x.rs", j)) == 1, "a json! value keyed by one is found")
    cols = 'fn f() -> Json { serde_json::json!({"columns": ["file_id", "audio_md5"]}) }\n'
    check(found("player/src/star_node.rs", cols) == [], "a column's name in a list is not a key")


def test_test_modules_and_files():
    print("test code is outside it, and a brace in a string does not end a test module")
    src = '''
fn real() {}

#[cfg(test)]
mod tests {
    const S: &str = "}";
    #[derive(serde::Serialize)]
    struct Old { passage_id: i64 }
    fn f() { let _ = "INSERT INTO passages VALUES (1)"; }
}
'''
    check(found("player/src/web/x.rs", src) == [], f"nothing inside a test module: {found('player/src/web/x.rs', src)}")
    after = src + '''
#[derive(serde::Serialize)]
struct New { passage_id: i64 }
'''
    check(len(found("player/src/web/x.rs", after)) == 1, "but what follows it is read")
    check(found("tools/test_x.py", 'c.execute("INSERT INTO passages VALUES (1)")') == [], "a test file is not read")
    check(found("player/tests/x.rs", 'const Q: &str = "INSERT INTO passages VALUES (1)";') == [], "nor a tests/ folder")


def test_a_listener_table_by_number():
    print("rule 2: a listener table declared with a passage_id column")
    src = 'PLAY = """CREATE TABLE IF NOT EXISTS listener_play_history (play_id INTEGER PRIMARY KEY,\n    passage_id INTEGER, mbid TEXT)"""\n'
    check(len(found("tools/x.py", src)) == 1, f"found in a tool: {found('tools/x.py', src)}")
    by_uid = src.replace("passage_id INTEGER", "passage_uid TEXT")
    check(found("tools/x.py", by_uid) == [], "by uid it is not")
    cat = 'CREATE TABLE passage_recordings (passage_id INTEGER NOT NULL, mbid TEXT);\n'
    check(found("sql/schema.sql", cat) == [], "a catalogue table joins by number inside its own database, and is not")
    rs = 'const T: &str = "CREATE TABLE id_reviews (passage_id INTEGER PRIMARY KEY, decision TEXT)";\n'
    check(len(found("player/core/src/db/x.rs", rs)) == 1, "found in Rust too")


def test_an_integer_passage_route():
    print("rule 3: a web handler taking a passage as an integer")
    src = '''pub(super) async fn h(
    State(ui): State<Ui>,
    axum::extract::Path(passage_id): axum::extract::Path<i64>,
) {}
'''
    check(len(found("player/src/web/x.rs", src)) == 1, f"found: {found('player/src/web/x.rs', src)}")
    pair = src.replace("Path(passage_id): axum::extract::Path<i64>", "Path((passage, ms)): axum::extract::Path<(i64, u64)>")
    check(len(found("player/src/web/x.rs", pair)) == 1, "in a tuple too")
    ms = src.replace("Path(passage_id): axum::extract::Path<i64>", "Path(ms): axum::extract::Path<u64>")
    check(found("player/src/web/x.rs", ms) == [], "a number that is no passage is not")
    uid = src.replace("axum::extract::Path(passage_id): axum::extract::Path<i64>",
                      "passage: super::passage_ref::Passage")
    check(found("player/src/web/x.rs", uid) == [], "the uid extractor is not")


def test_a_passage_written_by_hand():
    print("rule 4: a passage row written outside the helpers")
    src = 'c.execute("INSERT OR IGNORE INTO passages (file_id, kind) VALUES (?, ?)", (f, k))\n'
    check(len(found("tools/x.py", src)) == 1, "found in a tool")
    check(found("tools/passage_uid.py", src) == [], "not in the helper")
    check(found("fixtures/natural_patch/x.py", src) == [], "nor in a fixture")


def test_the_real_tree():
    print("against the real tree: clean, and a number put back where Stage C took it out is found")
    with open(os.path.join(REPO, "player", "src", "web", "mod.rs"), encoding="utf-8") as fh:
        web = fh.read()
    check(found("player/src/web/mod.rs", web) == [], f"web/mod.rs is clean: {found('player/src/web/mod.rs', web)[:3]}")
    marker = "    /// The passage on air, by this node's number, never sent.\n    #[serde(skip)]\n    pub passage_id: Option<i64>,"
    check(marker in web, "the fixture line this test edits is still there")
    back = web.replace(marker, "    pub passage_id: Option<i64>,")
    got = found("player/src/web/mod.rs", back)
    check(len(got) == 1 and "serialized passage_id" in got[0], f"the snapshot's number, unskipped, is found: {got}")
    with open(os.path.join(REPO, "player", "src", "web", "browse.rs"), encoding="utf-8") as fh:
        browse = fh.read()
    taken = "passage_info(passage: super::passage_ref::Passage)"
    check(taken in browse, "the handler line this test edits is still there")
    back = browse.replace(taken, "passage_info(axum::extract::Path(passage_id): axum::extract::Path<i64>)", 1)
    check(len(found("player/src/web/browse.rs", back)) == 1, "a passage route by number again is found")


def main() -> int:
    test_a_serialized_number()
    test_test_modules_and_files()
    test_a_listener_table_by_number()
    test_an_integer_passage_route()
    test_a_passage_written_by_hand()
    test_the_real_tree()
    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        return 1
    print("check_local_ids: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
