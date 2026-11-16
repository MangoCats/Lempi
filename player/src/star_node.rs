//! The player's half of the signed star sync `[SPEC-NSH-020..090]`.
//!
//! The hub reaches a member player the way it already sends rosters: over the
//! player's ordinary web port, each request signed by the mesh key the player
//! pinned when it joined, each answer signed by this node's own key. No ssh,
//! no stop, and no `sqlite3` run as another user -- the player applies its own
//! patch, through `lempi_core::mesh_sync`.
//!
//! Three steps, each a `POST /mesh/sync/<op>` carrying
//! `{"request": "<json>", "signature": "<mesh key, base64 DER>"}`:
//! - `snapshot` -- this node's shared tables and its catalogue summary
//!   `[SPEC-NSH-050]`, `[SPEC-NSH-060]`;
//! - `rows` -- its own catalogue rows about the files the hub names, read
//!   only, so a patch is made against what is here `[SPEC-STAR-945]`;
//! - `rehearse` -- both patches checked against the live files, nothing kept;
//! - `commit` -- rehearsed again, then applied: the catalogue first under the
//!   strict rule, then the listener under the member rule, and the catalogue
//!   put back if the listener fails, so a node never keeps half a switch
//!   `[SPEC-NSH-080]`. What was applied, reversed, is kept three deep.
//!
//! A request names the operation, the run, this node, the mesh, a nonce and
//! the time it was made. It is refused unless every one is right, the time is
//! within `FRESH_MS` of this node's clock, and -- for a commit -- the run is
//! newer than the last one committed here, so a captured commit cannot be
//! replayed to roll the node back `[SPEC-NSH-030]`. The one exception writes
//! nothing: the same run's commit asked again, with the same patch, is given
//! the answer the first one gave, since that answer can be lost on the way
//! back `[SPEC-NSH-032]`.
//!
//! One step at a time: each takes `STEPS` first, so a commit asked again while
//! the first is still applying waits for it, and is then answered. And before
//! any step, a commit this node began and never finished -- the player stopped
//! inside it -- is finished or put back, whichever its two halves show
//! `[SPEC-NSH-082]`.

use std::path::{Path, PathBuf};

use lempi_core::mesh_sync::{self, Rule};

#[cfg(test)]
use crate::catalogue_window::{wal_mode, WINDOW_CALLS};
use crate::catalogue_window::{journal_mode, writable, Window};
use serde_json::{json, Value};

/// How far a request's own time may be from this node's. Appliances keep
/// time by chrony against the hub's source; ten minutes is a clock that is
/// plainly wrong, not one that has drifted.
const FRESH_MS: i64 = 10 * 60 * 1000;
/// Commits whose inverse is kept `[SPEC-NSH-080]`.
const KEEP: usize = 3;
/// `[SPEC-NKP-920..935]`: a patch in parts. Most parts one catalogue patch may be cut
/// into, the most text one `stage` takes, the room a node keeps free beyond twice what
/// is staged, and how long a part nobody committed is kept.
const MAX_PARTS: u64 = 400;
const PART_LIMIT: usize = 32 << 20;
const STAGE_MARGIN: u64 = 256 << 20;
const STAGED_TTL: std::time::Duration = std::time::Duration::from_secs(24 * 3600);

/// `[SPEC-NSH-032]`: one sync step at a time on this node. Until 2026-11-16 the
/// route ran each request on its own blocking thread with nothing between them,
/// so a commit asked again while the first was still applying passed the
/// last-run check beside it -- and, finding every row already there, kept an
/// empty inverse over the first one's.
static STEPS: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// What a handled step returns: the signed answer, and whether the player
/// should rebuild what it loads once at start (the catalogue or the
/// household's preferences changed). Not for words alone `[SPEC-LYR-204]`.
pub struct Handled {
    pub answer: Value,
    pub reload: bool,
}

pub fn handle(listener: &Path, library: &Path, op: &str, body: &Value) -> Result<Handled, String> {
    if !matches!(op, "snapshot" | "rows" | "rehearse" | "commit" | "stage" | "staged") {
        return Err(format!("no such step: {op}"));
    }
    // Dormant on a network this node does not trust `[SPEC-NSH-180]`.
    crate::trust::participating(listener, library).map_err(|why| format!("this node is dormant: {why}"))?;
    let text = body["request"].as_str().ok_or("no request")?;
    let held = crate::membership::mesh_signed(listener, text, body["signature"].as_str().unwrap_or(""))?;
    let req: Value = serde_json::from_str(text).map_err(|e| format!("the request: {e}"))?;
    let me = crate::discovery::identity(listener)?.fingerprint;
    check(&req, op, &me, held["mesh_fp"].as_str().unwrap_or(""), now_ms())?;
    let run = req["run"].as_str().unwrap_or_default().to_string();
    // A step that panicked leaves the lock poisoned; what it guards is on disk,
    // and `settle` reads that, so the lock is taken all the same.
    let _turn = STEPS.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    let settled = settle(listener, library);
    if let Err(why) = &settled {
        if matches!(op, "rehearse" | "commit" | "stage") {
            return Err(why.clone());
        }
    }
    let (result, reload) = match op {
        "snapshot" => {
            let mut s = snapshot(listener, library)?;
            // `[SPEC-NSH-082]`: what was settled before this answer, or why it could not be.
            match settled {
                Ok(Some(line)) => s["settled"] = json!(line),
                Err(why) => s["unsettled"] = json!(why),
                Ok(None) => {}
            }
            (s, false)
        }
        // `[SPEC-STAR-945]`: read only, like the snapshot it completes.
        "rows" => (mesh_sync::catalogue_rows(library, &req["want"])?, false),
        "rehearse" => (rehearse(listener, library, &req)?, false),
        "stage" => (stage(listener, &req, &run)?, false),
        "staged" => (staged(listener, &run), false),
        _ => {
            let digest = digest_of(&req);
            let last = last_run(listener);
            if last.as_deref() == Some(run.as_str()) {
                (answered_again(listener, &run, &digest)?, false)
            } else if last.as_deref().is_some_and(|l| run.as_str() < l) {
                return Err(format!("run {run} is not newer than {} -- already committed here", last.unwrap_or_default()));
            } else if journal(listener, &run).is_some_and(|j| j["phase"] == "committed") {
                // Its answer kept, and the run left unrecorded -- the one write after it
                // failed. Applied again it would find every row in, and keep an empty
                // inverse over the real one: recorded now instead, and answered.
                set_last_run(listener, &run)?;
                (answered_again(listener, &run, &digest)?, false)
            } else {
                commit(listener, library, &req, &run, &digest, &member_apply)?
            }
        }
    };
    let answer = json!({"op": op, "run": run, "nonce": req["nonce"], "node": me, "result": result}).to_string();
    let signature = crate::membership::sign_as_node(listener, &answer)?;
    Ok(Handled { answer: json!({"answer": answer, "signature": signature}), reload })
}

/// Everything about a request but its signature, which is checked first.
fn check(req: &Value, op: &str, me: &str, mesh_fp: &str, now: i64) -> Result<(), String> {
    addressed(req, op, me, mesh_fp, now)?;
    if !req["run"].as_str().is_some_and(is_run) {
        return Err(format!("not a run: {}", req["run"]));
    }
    Ok(())
}

/// A signed request from the hub is for this step, this node and this mesh,
/// carries a nonce, and was made within `FRESH_MS` of this node's clock
/// `[SPEC-NSH-030]`. Shared with the programme's hand-over `[SPEC-PGM-310]`.
pub(crate) fn addressed(req: &Value, op: &str, me: &str, mesh_fp: &str, now: i64) -> Result<(), String> {
    if req["op"].as_str() != Some(op) {
        return Err(format!("the request is for {}, not {op}", req["op"]));
    }
    if req["node"].as_str() != Some(me) {
        return Err("the request is for another node".into());
    }
    if req["mesh"].as_str() != Some(mesh_fp) {
        return Err("the request names another mesh".into());
    }
    if req["nonce"].as_str().is_none_or(|n| n.len() < 16) {
        return Err("the request has no nonce".into());
    }
    let at = req["at_ms"].as_i64().ok_or("the request has no time")?;
    if (now - at).abs() > FRESH_MS {
        return Err(format!("the request was made {}s from this node's clock: stale, or a clock is wrong", (now - at) / 1000));
    }
    Ok(())
}

/// A run's stamp, `star_sync.stamp`'s `YYYYMMDDTHHMMZ`: fixed width, so later
/// runs sort later as text.
fn is_run(s: &str) -> bool {
    let b = s.as_bytes();
    b.len() == 14
        && b[..8].iter().all(u8::is_ascii_digit)
        && b[8] == b'T'
        && b[9..13].iter().all(u8::is_ascii_digit)
        && b[13] == b'Z'
}

pub(crate) fn now_ms() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_millis() as i64)
}

/// What this build can apply, so the hub never hands a patch to a player that would
/// misread it `[SPEC-NKP-075]`. 1 is the catalogue patch by natural key.
const NATURAL_KEYS: i64 = 1;
/// `[IMPL-UID-052]`: this build resolves a natural patch's passage reference by
/// `passage_uid` as well as by span, and stores the uid a bundle carries. A node
/// that does not report it is never sent a reference by uid.
const PASSAGE_UIDS: i64 = 1;
/// `[SPEC-STAR-104]`: this build opens a catalogue on a read-only partition itself, for the
/// rehearsal as well as the commit, and says whether the catalogue is on one. A node that
/// does not report it is sent no catalogue patch to a read-only partition: it would refuse it.
const CATALOGUE_WINDOW: i64 = 1;
/// `[SPEC-STAR-945]`: this build answers `rows`, its own catalogue rows about some files.
/// A node that does not report it is patched against what the hub last sent, and the hub
/// says so. 2 since 2026-10-31: asked by recording, it lists the files that credit one
/// too `[SPEC-LNK-050]`. 3, the same day: asked for tracklists, a release's own
/// tracklist `[SPEC-LNK-072]`.
const CATALOGUE_ROWS: i64 = 3;
/// `[SPEC-NSH-032]`: this build answers a commit asked again with the answer it gave,
/// and takes one step at a time. A hub asks a node's commit again only when it says so:
/// asked again, an older build refuses it, or, still applying the first, runs it twice.
const COMMIT_AGAIN: i64 = 1;

fn snapshot(listener: &Path, library: &Path) -> Result<Value, String> {
    Ok(json!({
        "listener": mesh_sync::export(listener, &mesh_sync::MERGED)?,
        "catalogue": mesh_sync::catalogue_summary(library)?,
        "last_run": last_run(listener),
        "commit_again": COMMIT_AGAIN,
        "natural_keys": NATURAL_KEYS,
        "passage_uids": PASSAGE_UIDS,
        // `[IMPL-UID-056]`: the indexes its catalogue holds. Reported only by a
        // build that applies a patch's `add_indexes`, so the report is the
        // capability: a hub sends an index to no node that did not give one.
        "catalogue_indexes": mesh_sync::catalogue_indexes(library)?,
        "catalogue_window": CATALOGUE_WINDOW,
        "catalogue_rows": CATALOGUE_ROWS,
        "retire_files": crate::retire::RETIRE_FILES,
        "library_readonly": library.exists() && !writable(library),
        // [SPEC-STAR-107]: each half's journal mode, as SQLite says it; null
        // for one that could not be asked. The hub names a listener that is
        // not WAL -- there one long read refuses every write -- and until
        // 2026-11-13 nothing read a node's mode at all.
        "journal_mode": {"listener": journal_mode(listener), "library": journal_mode(library)},
        // [GDE-INT-145]: what this player found reading its own files back,
        // for the hub to name. A report that cannot be read says so, as
        // `{"error": ...}`: null is what a build without the check sends, and
        // a node holding damaged files must not pass for one [GDE-INT-020].
        "file_checks": crate::scrub::summary(listener, library)
            .unwrap_or_else(|e| json!({ "error": e })),
    }))
}

fn has_rows(p: &Value) -> bool {
    p["tables"].as_array().is_some_and(|t| !t.is_empty())
}

/// `[SPEC-RTF-020]` The files the hub retired that this commit is to move, as it sent them.
fn retire_list(req: &Value) -> &[Value] {
    req["retire"].as_array().map(Vec::as_slice).unwrap_or(&[])
}

fn counts(c: &mesh_sync::Applied) -> Value {
    json!({"applied": c.applied, "already": c.already, "kept": c.kept})
}

/// Both patches against the live files, nothing kept. The catalogue under the
/// strict rule refuses on its first mismatch; the listener under the member
/// rule counts what it would keep.
/// The `rehearse` step. A rehearsal applies the patch and rolls it back, which needs the
/// catalogue open for writing, so on a read-only partition it too is inside the window.
fn rehearse(listener: &Path, library: &Path, req: &Value) -> Result<Value, String> {
    let has_cat = req.get("catalogue_parts").is_some_and(|v| !v.is_null()) || has_rows(&req["catalogue_patch"]);
    // A file to retire is on the music partition, which on bose is the catalogue's.
    let win = Window::open(library, has_cat || !retire_list(req).is_empty())?;
    let out = rehearse_in(listener, library, req);
    if let Some(w) = win.close(library) {
        tracing::warn!("star sync: after a rehearsal: {w}");
    }
    out
}

fn rehearse_in(listener: &Path, library: &Path, req: &Value) -> Result<Value, String> {
    let (cat, lis) = (&req["catalogue_patch"], &req["listener_patch"]);
    let mut out = json!({});
    if let Some(spec) = req.get("catalogue_parts").filter(|v| !v.is_null()) {
        // `[SPEC-NKP-920]`: what was staged, every part against its digest again, all in
        // one transaction that is rolled back.
        let paths = part_paths(listener, req["run"].as_str().unwrap_or(""), spec)?;
        let mut it = paths.iter().map(load_part);
        out["catalogue"] = counts(&mesh_sync::apply_parts(library, &mut it, None, Rule::Strict, false, &mut |_, _| Ok(()))
            .map_err(|e| format!("catalogue: {e}"))?);
    } else if has_rows(cat) {
        out["catalogue"] = counts(&mesh_sync::apply_with(library, cat, None, Rule::Strict, false)
            .map_err(|e| format!("catalogue: {e}"))?.counts);
    }
    if has_rows(lis) {
        out["listener"] = counts(&mesh_sync::apply_with(listener, lis, Some(&mesh_sync::MERGED), Rule::Member, false)
            .map_err(|e| format!("listener: {e}"))?.counts);
    }
    if !retire_list(req).is_empty() {
        out["retire"] = crate::retire::retire(library, retire_list(req), false);
    }
    Ok(out)
}

/// The listener's half of a commit, apart so a test can make it fail after
/// the catalogue has landed -- the one failure a rehearsal cannot foresee. It is
/// handed the sink its inverse goes to, before its transaction commits.
type Sink<'a> = &'a mut dyn FnMut(usize, &Value) -> Result<(), String>;
type ListenerApply<'a> = &'a dyn Fn(&Path, &Value, Sink) -> Result<mesh_sync::Applied, String>;

/// What a commit applied: the catalogue's counts and how many inverse parts it
/// left on disk, if it had a catalogue half, and the listener's counts and
/// inverse, if it had that half.
type Committed = (Option<(mesh_sync::Applied, usize)>, Option<(mesh_sync::Applied, Value)>);

/// A failed commit's reason, and whether this node may hold part of the run
/// still -- a half it could not put back -- so its record is kept for `settle`.
type Failed = (String, bool);

/// The listener's half as a commit applies it: the shared tables under the member
/// rule, its inverse handed to `sink` before the transaction commits.
fn member_apply(listener: &Path, patch: &Value, sink: Sink) -> Result<mesh_sync::Applied, String> {
    mesh_sync::apply_parts(listener, &mut std::iter::once(Ok(patch.clone())), Some(&mesh_sync::MERGED), Rule::Member, true, sink)
}

fn commit(listener: &Path, library: &Path, req: &Value, run: &str, digest: &str, apply_listener: ListenerApply)
    -> Result<(Value, bool), String> {
    let (cat, lis) = (&req["catalogue_patch"], &req["listener_patch"]);
    let parts = req.get("catalogue_parts").filter(|v| !v.is_null());
    let has_cat = parts.is_some() || has_rows(cat);
    let to_retire = retire_list(req);
    if !has_cat && !has_rows(lis) && to_retire.is_empty() {
        let result = json!({"catalogue": null, "listener": null});
        record(listener, run, digest, "committed", Some(&result))?;
        set_last_run(listener, run)?;
        return Ok((result, false));
    }
    // `[SPEC-NSH-082]`: on disk before anything is written, so a player stopped
    // inside the commit finds, at its next step, that it was.
    std::fs::create_dir_all(sync_dir(listener)).map_err(|e| e.to_string())?;
    record(listener, run, digest, "applying", None)?;
    // The root verb only when the file cannot be written where it is -- a
    // read-only partition, as on bose. A catalogue already writable (on the
    // root filesystem, or anywhere a test puts one) never reaches it. The window spans
    // the rehearsal as well as the apply: both write, the first to roll back.
    let win = match Window::open(library, has_cat || !to_retire.is_empty()) {
        Ok(w) => w,
        Err(e) => {
            forget(listener, run);
            return Err(e);
        }
    };
    let applied = (|| -> Result<Committed, Failed> {
        // Rehearsed first, here, whatever the hub rehearsed before: the files may
        // have moved since.
        rehearse_in(listener, library, req).map_err(|e| (e, false))?;
        let dir = sync_dir(listener);
        // One transaction over every part, a patch sent whole being one part; each
        // part's inverse goes to disk as it is made, before the transaction commits.
        // Until 2026-11-16 a whole patch's inverse was kept in memory until both
        // halves had landed, so a power cut between them left the catalogue switched
        // with nothing on disk to put it back, or even to say so.
        let c = if has_cat {
            let done = (|| {
                let mut sink = |i: usize, inv: &Value| write_atomic(&dir.join(cat_inverse(run, i + 1)), &inv.to_string());
                match parts {
                    Some(spec) => {
                        let paths = part_paths(listener, run, spec)?;
                        let mut it = paths.iter().map(load_part);
                        mesh_sync::apply_parts(library, &mut it, None, Rule::Strict, true, &mut sink).map(|c| (c, paths.len()))
                    }
                    None => mesh_sync::apply_parts(library, &mut std::iter::once(Ok(cat.clone())), None, Rule::Strict, true, &mut sink)
                        .map(|c| (c, 1)),
                }
            })();
            match done {
                Ok(c) => Some(c),
                Err(e) => {
                    remove_inverse_parts(listener, run);
                    return Err((format!("catalogue: {e}"), false));
                }
            }
        } else {
            None
        };
        let n = c.as_ref().map_or(0, |(_, n)| *n);
        let unwind = |why: String| -> Failed {
            match put_back(library, listener, run, n) {
                Ok(()) if n > 0 => (format!("{why}; the catalogue was put back"), false),
                Ok(()) => (why, false),
                Err(u) => (format!("{why}; and the catalogue could NOT be put back: {u}"), true),
            }
        };
        // The catalogue's half is settled: in, or there was none.
        record(listener, run, digest, "catalogue", None).map_err(|e| unwind(format!("the commit's record: {e}")))?;
        let l = if has_rows(lis) {
            let path = dir.join(lis_inverse(run));
            let mut inverse = None;
            let out = apply_listener(listener, lis, &mut |_, inv| {
                write_atomic(&path, &inv.to_string())?;
                inverse = Some(inv.clone());
                Ok(())
            });
            match out {
                Ok(counts) => Some((counts, inverse.unwrap_or_else(|| json!({"tables": []})))),
                Err(e) => {
                    let _ = std::fs::remove_file(&path);
                    return Err(unwind(format!("listener: {e}")));
                }
            }
        } else {
            None
        };
        // Both halves are in. A record that cannot be written now leaves the one
        // before it, which `settle` finishes from at the next step.
        record(listener, run, digest, "landed", None)
            .map_err(|e| (format!("both halves landed and the commit's record could not say so ({e}): the next step finishes it"), true))?;
        Ok((c, l))
    })();
    // `[SPEC-RTF-025]`: the files the hub retired, moved only once both halves have
    // landed and inside the same window -- a move left undone is said, never a failed
    // commit: the catalogue is in, and a stray is something to tidy, not something lost.
    let retired = (applied.is_ok() && !to_retire.is_empty()).then(|| crate::retire::retire(library, to_retire, true));
    // The tags of any file the catalogue gained, read while it is open for
    // writing -- on a read-only catalogue the one moment it is `[REQ-VIS-180]`.
    // Never fatal to the commit; the scan says what it could not do.
    if has_cat && applied.is_ok() {
        let _ = crate::tags::backfill(library, false);
    }
    let warning = win.close(library);
    if let Some(w) = &warning {
        tracing::warn!("star sync: {w}");
    }
    let (c, l) = match applied {
        Ok(done) => done,
        Err((why, keep)) => {
            if !keep {
                forget(listener, run);
            }
            return Err(why);
        }
    };
    let n_parts = c.as_ref().map_or(0, |(_, n)| *n);
    let moved = retired.as_ref().map(crate::retire::moves);
    keep_inverse(listener, run, n_parts, l.as_ref().map(|(_, inv)| inv), moved.as_ref())?;
    if parts.is_some() {
        let _ = std::fs::remove_dir_all(staged_dir(listener, run));       // consumed
    }
    let mut result = json!({"catalogue": c.as_ref().map(|(counts_, _)| counts(counts_)),
                            "listener": l.as_ref().map(|(counts_, _)| counts(counts_))});
    if parts.is_some() {
        result["catalogue_parts"] = json!(n_parts);
    }
    if let Some(r) = retired {
        result["retire"] = r;
    }
    if let Some(w) = warning {
        result["warning"] = json!(w);
    }
    // The answer kept before the run is recorded, so a run recorded here always
    // has one to give again `[SPEC-NSH-032]`.
    record(listener, run, digest, "committed", Some(&result))?;
    set_last_run(listener, run)?;
    tracing::info!("star sync: run {run} committed here: {result}");
    Ok((result, has_cat || !to_retire.is_empty() || changes_selection(lis)))
}

// ---- a commit asked again, and one a stopped player left `[SPEC-NSH-032]`, `[SPEC-NSH-082]` --

/// What a commit was asked to write, by digest: the same run asked again with the
/// same digest is the same commit. The patches as this node parsed them, so the
/// digest does not depend on how the hub spaced its text.
fn digest_of(req: &Value) -> String {
    let what = json!({"catalogue_patch": req["catalogue_patch"], "listener_patch": req["listener_patch"],
                      "catalogue_parts": req["catalogue_parts"], "retire": req["retire"]});
    sha_hex(what.to_string().as_bytes())
}

fn cat_inverse(run: &str, n: usize) -> String {
    format!("inverse-{run}-c{n}.json")
}

fn lis_inverse(run: &str) -> String {
    format!("inverse-{run}-l.json")
}

fn journal_path(listener: &Path, run: &str) -> PathBuf {
    sync_dir(listener).join(format!("commit-{run}.json"))
}

fn journal(listener: &Path, run: &str) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(journal_path(listener, run)).ok()?).ok()
}

/// How far a commit has got, on disk before the next write: `applying` before
/// anything, `catalogue` once that half is in or there is none, `landed` once
/// both are, and `committed` with the answer it gave.
fn record(listener: &Path, run: &str, digest: &str, phase: &str, result: Option<&Value>) -> Result<(), String> {
    let mut j = json!({"run": run, "digest": digest, "phase": phase, "at_ms": now_ms()});
    if let Some(r) = result {
        j["result"] = r.clone();
    }
    write_atomic(&journal_path(listener, run), &j.to_string())
}

/// A run this node holds nothing of: its record and any inverse it left.
fn forget(listener: &Path, run: &str) {
    remove_inverse_parts(listener, run);
    let _ = std::fs::remove_file(sync_dir(listener).join(lis_inverse(run)));
    let _ = std::fs::remove_file(journal_path(listener, run));
}

/// The run's catalogue inverses, `inverse-<run>-c1.json` on, as many as there are.
fn cat_inverses_on_disk(listener: &Path, run: &str) -> usize {
    (1..).take_while(|i| sync_dir(listener).join(cat_inverse(run, *i)).is_file()).count()
}

fn read_inverse(path: &Path) -> Result<Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
}

/// The run's catalogue inverses, the last part's first, as an undo wants them.
fn cat_inverses_back(listener: &Path, run: &str, n: usize) -> impl Iterator<Item = Result<Value, String>> {
    let (dir, run) = (sync_dir(listener), run.to_string());
    (1..=n).rev().map(move |i| read_inverse(&dir.join(cat_inverse(&run, i))))
}

/// The catalogue's half of `run` put back from the inverses on disk, inside the
/// window a read-only partition needs; then those inverses go. `n` 0 is nothing to do.
fn put_back(library: &Path, listener: &Path, run: &str, n: usize) -> Result<(), String> {
    if n == 0 {
        return Ok(());
    }
    let win = Window::open(library, true)?;
    let r = mesh_sync::undo_parts(library, &mut cat_inverses_back(listener, run, n));
    if let Some(w) = win.close(library) {
        tracing::warn!("star sync: after putting a catalogue back: {w}");
    }
    r.map(|_| remove_inverse_parts(listener, run))
}

/// The same run's commit asked again: the answer it gave, if this is the same
/// commit, marked so; never anything written `[SPEC-NSH-032]`.
fn answered_again(listener: &Path, run: &str, digest: &str) -> Result<Value, String> {
    match journal(listener, run) {
        Some(j) if j["phase"] == "committed" && j["digest"] == digest => {
            let mut r = j["result"].clone();
            r["committed_earlier"] = json!(true);
            tracing::info!("star sync: run {run} asked again; answered as committed earlier");
            Ok(r)
        }
        Some(j) if j["phase"] == "committed" => {
            Err(format!("run {run} is already committed here, with another patch: nothing written"))
        }
        _ => Err(format!("run {run} is already committed here, by a build that kept no answer: nothing written")),
    }
}

/// `[SPEC-NSH-082]`: a commit this node began and never finished -- the player
/// stopped inside it -- settled before anything else is answered: finished where
/// both halves had landed, put back where only the catalogue had, forgotten where
/// nothing had. What was done, said; `Err` where it could not be told, and then
/// every step that writes is refused, saying why, until a person has looked.
fn settle(listener: &Path, library: &Path) -> Result<Option<String>, String> {
    let Ok(rd) = std::fs::read_dir(sync_dir(listener)) else { return Ok(None) };
    let mut open: Vec<(String, Value)> = rd
        .flatten()
        .filter_map(|e| e.file_name().into_string().ok())
        .filter_map(|n| n.strip_prefix("commit-").and_then(|r| r.strip_suffix(".json")).map(str::to_string))
        .filter_map(|run| journal(listener, &run).map(|j| (run, j)))
        .filter(|(_, j)| j["phase"] != "committed")
        .collect();
    open.sort_by(|a, b| a.0.cmp(&b.0));
    let mut said = Vec::new();
    for (run, j) in open {
        if let Some(why) = j["unsettled"].as_str() {
            return Err(format!("run {run}'s commit stopped part way, and {why}: nothing will be written here until \
                                a person has looked (`{}`) [SPEC-NSH-082]", journal_path(listener, &run).display()));
        }
        let n = cat_inverses_on_disk(listener, &run);
        let phase = j["phase"].as_str().unwrap_or("").to_string();
        let done = match phase.as_str() {
            "landed" => finish(listener, &run, &j, n),
            "catalogue" => match listener_landed(listener, &run) {
                Ok(true) => finish(listener, &run, &j, n),
                Ok(false) => put_back(library, listener, &run, n).map(|()| {
                    forget(listener, &run);
                    let what = if n > 0 {
                        "its catalogue had landed and its listener had not: the catalogue was put back"
                    } else {
                        "nothing of it had landed"
                    };
                    what.to_string()
                }),
                Err(e) => Err(e),
            },
            "applying" if n == 0 => {
                forget(listener, &run);
                Ok("nothing of it had landed".to_string())
            }
            "applying" => match catalogue_landed(library, listener, &run, n) {
                Ok(true) => put_back(library, listener, &run, n).map(|()| {
                    forget(listener, &run);
                    "its catalogue had landed and its listener had not: the catalogue was put back".to_string()
                }),
                Ok(false) => {
                    forget(listener, &run);
                    Ok("nothing of it had landed".to_string())
                }
                Err(e) => Err(e),
            },
            other => Err(format!("its record says `{other}`, which this build does not know")),
        };
        match done {
            Ok(what) => {
                tracing::warn!("star sync: run {run}'s commit had stopped part way: {what}");
                said.push(format!("run {run}'s commit had stopped part way: {what}"));
            }
            Err(why) => {
                let mut j = j.clone();
                j["unsettled"] = json!(why);
                let _ = write_atomic(&journal_path(listener, &run), &j.to_string());
                tracing::error!("star sync: run {run}'s commit stopped part way and cannot be settled: {why}");
                return Err(format!("run {run}'s commit stopped part way, and {why}: nothing will be written here \
                                    until a person has looked (`{}`) [SPEC-NSH-082]", journal_path(listener, &run).display()));
            }
        }
    }
    Ok((!said.is_empty()).then(|| said.join("; ")))
}

/// Both halves were in: what the commit would have kept, kept, and the run recorded.
/// It stopped before the files it was to retire were moved and the new files' tags
/// read; the answer says so, and the next run asks about those files again.
fn finish(listener: &Path, run: &str, j: &Value, n: usize) -> Result<String, String> {
    let lpath = sync_dir(listener).join(lis_inverse(run));
    let lis = if lpath.is_file() { Some(read_inverse(&lpath)?) } else { None };
    keep_inverse(listener, run, n, lis.as_ref(), None)?;
    let result = json!({"catalogue": null, "listener": null,
                        "finished": "both halves had landed when the player stopped; files to retire were not moved"});
    record(listener, run, j["digest"].as_str().unwrap_or(""), "committed", Some(&result))?;
    if last_run(listener).is_none_or(|l| run > l.as_str()) {
        set_last_run(listener, run)?;
    }
    Ok("both halves had landed: it was finished, and the run recorded".to_string())
}

/// Whether the listener's half of `run` had landed, read from the listener itself:
/// its inverse, rehearsed under the member rule, finds a row the patch wrote. None
/// on disk is a half not begun. A patch that changed no row, landed or not, leaves
/// the listener as it would be: finished.
fn listener_landed(listener: &Path, run: &str) -> Result<bool, String> {
    let path = sync_dir(listener).join(lis_inverse(run));
    if !path.is_file() {
        return Ok(false);
    }
    let inv = read_inverse(&path)?;
    let o = mesh_sync::apply_with(listener, &inv, None, Rule::Member, false)
        .map_err(|e| format!("whether its listener had landed could not be read: {e}"))?;
    let c = o.counts;
    if c.applied == 0 && c.already == 0 && c.kept > 0 {
        return Err(format!("every one of the {} listener row(s) it wrote has changed since, so whether they \
                            landed cannot be told", c.kept));
    }
    Ok(c.applied > 0 || c.already == 0)
}

/// Whether the catalogue's half of `run` had landed, read from the catalogue itself:
/// its inverse, rehearsed, finds the rows the patch wrote (landed) or the rows that
/// were there before it (not). A catalogue is not edited where it is patched, so
/// anything else is a catalogue this cannot judge, and is said.
fn catalogue_landed(library: &Path, listener: &Path, run: &str, n: usize) -> Result<bool, String> {
    let win = Window::open(library, true)?;
    let r = mesh_sync::rehearse_undo(library, &mut cat_inverses_back(listener, run, n));
    if let Some(w) = win.close(library) {
        tracing::warn!("star sync: after reading a catalogue: {w}");
    }
    let c = r.map_err(|e| format!("whether its catalogue had landed could not be read: {e}"))?;
    match (c.applied, c.already) {
        (0, 0) => Err("its catalogue inverse names no row, so whether it landed cannot be told".to_string()),
        (applied, _) => Ok(applied > 0),
    }
}

/// The shared tables that change what a page shows and never what plays, so a
/// commit of these alone asks for no rebuild `[SPEC-LYR-204]`. Words saved for
/// a passage are read from the file at each request; the Director holds none.
const SHOWN_ONLY: [&str; 1] = ["passage_lyrics"];

/// Whether a listener patch names a table the Director is built from. Until
/// 2026-11-13 every commit that wrote a row asked for a rebuild -- 57.9 s to
/// 73.9 s on `lempi02w` -- and every lyric row goes to every node
/// `[SPEC-LYR-200]`, so one corrected line rebuilt the fleet. A table this
/// does not know asks for the rebuild: the mistake that costs a minute, not
/// the one that leaves a node selecting from preferences it no longer holds.
fn changes_selection(lis: &Value) -> bool {
    lis["tables"]
        .as_array()
        .is_some_and(|tables| tables.iter().any(|t| !t["name"].as_str().is_some_and(|n| SHOWN_ONLY.contains(&n))))
}

fn sync_dir(listener: &Path) -> PathBuf {
    listener.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from(".")).join("mesh-sync")
}

fn last_run(listener: &Path) -> Option<String> {
    let v: Value = serde_json::from_str(&std::fs::read_to_string(sync_dir(listener).join("state.json")).ok()?).ok()?;
    v["last_run"].as_str().map(str::to_string)
}

fn set_last_run(listener: &Path, run: &str) -> Result<(), String> {
    let d = sync_dir(listener);
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    write_atomic(&d.join("state.json"), &json!({"last_run": run}).to_string())
}

/// What a commit wrote, reversed, as `inverse-<run>.json`; the newest `KEEP` runs
/// are kept. The catalogue's inverse is in `inverse-<run>-c<n>.json`, one a part --
/// a patch sent whole is one part -- which this file lists, and which `undo_parts`
/// puts back together `[SPEC-NKP-940]`. The listener's is in this file itself;
/// `mesh_sync::undo` with it puts that half back. A commit's record goes with its
/// inverses; one older than the run just recorded can answer nothing again.
fn keep_inverse(listener: &Path, run: &str, cat_parts: usize, lis: Option<&Value>,
                moved: Option<&Value>) -> Result<(), String> {
    let d = sync_dir(listener);
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    let names: Vec<String> = (1..=cat_parts).map(|i| cat_inverse(run, i)).collect();
    write_atomic(
        &d.join(format!("inverse-{run}.json")),
        // `moved`: the files retired here, `{from, to}`, for `retire::undo` [SPEC-RTF-035].
        &json!({"run": run, "catalogue": null, "catalogue_parts": names, "listener": lis, "moved": moved}).to_string(),
    )?;
    let _ = std::fs::remove_file(d.join(lis_inverse(run)));      // now in the file above
    let files: Vec<String> = std::fs::read_dir(&d)
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|n| n.starts_with("inverse-"))
        .collect();
    let mut runs: Vec<String> = files.iter().map(|n| n["inverse-".len()..].chars().take(14).collect()).collect();
    runs.sort();
    runs.dedup();
    let old: Vec<&String> = runs.iter().take(runs.len().saturating_sub(KEEP)).collect();
    for f in &files {
        if old.iter().any(|r| f["inverse-".len()..].starts_with(r.as_str())) {
            let _ = std::fs::remove_file(d.join(f));
        }
    }
    for f in std::fs::read_dir(&d).map_err(|e| e.to_string())?.flatten() {
        let name = f.file_name().into_string().unwrap_or_default();
        if name.strip_prefix("commit-").is_some_and(|r| r.len() >= 14 && &r[..14] < run) {
            let _ = std::fs::remove_file(f.path());
        }
    }
    Ok(())
}

// ---- a catalogue patch in parts `[SPEC-NKP-920..940]` -------------------------------------

fn sha_hex(bytes: &[u8]) -> String {
    use sha2::Digest;
    sha2::Sha256::digest(bytes).iter().map(|b| format!("{b:02x}")).collect()
}

fn staged_dir(listener: &Path, run: &str) -> PathBuf {
    sync_dir(listener).join(format!("staged-{run}"))
}

fn part_file(dir: &Path, n: u64) -> PathBuf {
    dir.join(format!("catalogue-{n}.json"))
}

/// A file written whole or not at all: a half-written part is never named as a part.
/// On disk, not only in the page cache, before this returns `[SPEC-NSH-082]`: an
/// inverse must outlast a power cut that the catalogue's own commit, which SQLite
/// syncs, survives -- or the catalogue is switched with nothing to put it back.
fn write_atomic(path: &Path, text: &str) -> Result<(), String> {
    use std::io::Write;
    let tmp = path.with_extension("part");
    let mut f = std::fs::File::create(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(text.as_bytes()).and_then(|()| f.sync_all()).map_err(|e| format!("{}: {e}", tmp.display()))?;
    drop(f);
    std::fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))?;
    // The rename itself is durable once its directory is synced. Windows cannot
    // open a directory as a file, and has no need to.
    #[cfg(unix)]
    if let Some(dir) = path.parent().and_then(|d| std::fs::File::open(d).ok()) {
        let _ = dir.sync_all();
    }
    Ok(())
}

/// The run's catalogue inverses, as many as were written: they are written in order,
/// so a commit stopped part way leaves the first few.
fn remove_inverse_parts(listener: &Path, run: &str) {
    for i in 1..=cat_inverses_on_disk(listener, run) {
        let _ = std::fs::remove_file(sync_dir(listener).join(cat_inverse(run, i)));
    }
}

fn load_part(path: &PathBuf) -> Result<Value, String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))
}

/// Free bytes on the partition `dir` is on.
#[cfg(unix)]
fn free_bytes(dir: &Path) -> Result<u64, String> {
    use std::os::unix::ffi::OsStrExt;
    let c = std::ffi::CString::new(dir.as_os_str().as_bytes()).map_err(|e| e.to_string())?;
    // SAFETY: `c` is a valid NUL-terminated path and `s` is a zeroed `statvfs` the call fills in.
    let (rc, s) = unsafe {
        let mut s: libc::statvfs = std::mem::zeroed();
        (libc::statvfs(c.as_ptr(), &mut s), s)
    };
    if rc != 0 {
        return Err(format!("cannot read the free space of {}", dir.display()));
    }
    // A no-op on the 64-bit targets built today, and not on a 32-bit one,
    // where `fsblkcnt_t` and the block size are `c_ulong`: kept, and said so.
    #[allow(clippy::unnecessary_cast)]
    let bytes = s.f_bavail as u64 * s.f_frsize as u64;
    Ok(bytes)
}

/// A host that is not unix has no state partition to fill: no limit is read.
#[cfg(not(unix))]
fn free_bytes(_dir: &Path) -> Result<u64, String> {
    Ok(u64::MAX)
}

/// One part of a catalogue patch, stored for the commit `[SPEC-NKP-925]`. The hash is of
/// the text exactly as sent, so nothing depends on how a number is spelled. A node with
/// too little room says so with the figures, before it takes a first part `[SPEC-NKP-935]`.
fn stage(listener: &Path, req: &Value, run: &str) -> Result<Value, String> {
    let last = last_run(listener);
    if last.as_deref().is_some_and(|l| run <= l) {
        return Err(format!("run {run} is not newer than {} -- already committed here", last.unwrap_or_default()));
    }
    let p = &req["part"];
    if p["half"].as_str() != Some("catalogue") {
        return Err("only the catalogue is staged".into());
    }
    let of = p["of"].as_u64().filter(|o| (1..=MAX_PARTS).contains(o)).ok_or("a part names how many there are")?;
    let n = p["n"].as_u64().filter(|n| (1..=of).contains(n)).ok_or("a part has its number")?;
    let want = p["sha256"].as_str().filter(|h| h.len() == 64 && h.bytes().all(|b| b.is_ascii_hexdigit())).ok_or("a part has its digest")?;
    let text = p["text"].as_str().ok_or("a part has its text")?;
    if text.len() > PART_LIMIT {
        return Err(format!("part {n} is {} MB, more than the {} MB a part may be", text.len() >> 20, PART_LIMIT >> 20));
    }
    if sha_hex(text.as_bytes()) != want {
        return Err(format!("part {n} of {of} did not arrive intact"));
    }
    let dir = staged_dir(listener, run);
    std::fs::create_dir_all(sync_dir(listener)).map_err(|e| e.to_string())?;
    if !dir.exists() {
        let total = p["total_bytes"].as_u64().unwrap_or(text.len() as u64);
        let need = total.saturating_mul(2).saturating_add(STAGE_MARGIN);
        let free = free_bytes(&sync_dir(listener))?;
        if free < need {
            return Err(format!(
                "not enough room to stage: {} MB free on this node's state partition, {} MB needed \
                 (twice the {} MB staged, and {} MB to spare)",
                free >> 20, need >> 20, total >> 20, STAGE_MARGIN >> 20));
        }
    }
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    write_atomic(&part_file(&dir, n), text)?;
    gc_staged(listener, run);
    Ok(json!({"staged": n, "of": of, "bytes": text.len()}))
}

/// Which parts of a run are here, by digest, so what a rehearsal staged is not sent again.
fn staged(listener: &Path, run: &str) -> Value {
    let mut have = serde_json::Map::new();
    if let Ok(rd) = std::fs::read_dir(staged_dir(listener, run)) {
        for e in rd.flatten() {
            let name = e.file_name().into_string().unwrap_or_default();
            if let Some(n) = name.strip_prefix("catalogue-").and_then(|r| r.strip_suffix(".json")) {
                if let Ok(bytes) = std::fs::read(e.path()) {
                    have.insert(n.to_string(), json!(sha_hex(&bytes)));
                }
            }
        }
    }
    // `[SPEC-NSH-032]`: a commit consumes what it staged, so a hub trying the same
    // run's commit again is told it is in, and stages nothing.
    json!({"catalogue": have, "committed": last_run(listener).as_deref() == Some(run)})
}

/// Staged parts for a run no newer than the last committed, or a day old, are removed.
fn gc_staged(listener: &Path, keep_run: &str) {
    let last = last_run(listener);
    let Ok(rd) = std::fs::read_dir(sync_dir(listener)) else { return };
    for e in rd.flatten() {
        let name = e.file_name().into_string().unwrap_or_default();
        let Some(r) = name.strip_prefix("staged-") else { continue };
        if r == keep_run {
            continue;
        }
        let old = e.metadata().and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok()).is_some_and(|age| age > STAGED_TTL);
        if old || last.as_deref().is_some_and(|l| r <= l) {
            let _ = std::fs::remove_dir_all(e.path());
        }
    }
}

/// The staged files a commit or a rehearsal is to apply, each checked against the digest
/// the hub named: a part missing or not as named refuses the step, writing nothing.
fn part_paths(listener: &Path, run: &str, spec: &Value) -> Result<Vec<PathBuf>, String> {
    let of = spec["of"].as_u64().filter(|o| (1..=MAX_PARTS).contains(o)).ok_or("catalogue_parts names how many there are")?;
    let digests = spec["sha256"].as_array().filter(|d| d.len() as u64 == of).ok_or("catalogue_parts has one digest for each part")?;
    let dir = staged_dir(listener, run);
    let mut paths = Vec::new();
    for n in 1..=of {
        let path = part_file(&dir, n);
        let bytes = std::fs::read(&path).map_err(|_| format!("catalogue part {n} of {of} is not staged here"))?;
        if digests[(n - 1) as usize].as_str() != Some(sha_hex(&bytes).as_str()) {
            return Err(format!("staged part {n} of {of} is not the one the hub named; nothing written"));
        }
        paths.push(path);
    }
    Ok(paths)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    fn req(op: &str) -> Value {
        json!({"op": op, "node": "me", "mesh": "m", "nonce": "0123456789abcdef", "run": "20260928T1830Z", "at_ms": 1_000_000})
    }

    /// `[SPEC-NSH-030]`: every field of a request is checked, and a request
    /// from too far away in time is refused.
    #[test]
    fn a_request_must_be_for_this_step_node_and_mesh_and_fresh() {
        let now = 1_000_000;
        assert!(check(&req("commit"), "commit", "me", "m", now).is_ok());
        assert!(check(&req("rehearse"), "commit", "me", "m", now).unwrap_err().contains("not commit"));
        assert!(check(&req("commit"), "commit", "you", "m", now).unwrap_err().contains("another node"));
        assert!(check(&req("commit"), "commit", "me", "other", now).unwrap_err().contains("another mesh"));
        let mut r = req("commit");
        r["nonce"] = json!("short");
        assert!(check(&r, "commit", "me", "m", now).unwrap_err().contains("nonce"));
        let mut r = req("commit");
        r["run"] = json!("../../etc");
        assert!(check(&r, "commit", "me", "m", now).unwrap_err().contains("not a run"));
        assert!(check(&req("commit"), "commit", "me", "m", now + FRESH_MS + 1).unwrap_err().contains("stale"));
        assert!(check(&req("commit"), "commit", "me", "m", now - FRESH_MS - 1).unwrap_err().contains("stale"));
    }

    /// `[SPEC-STAR-104]`: a catalogue in WAL mode is told from one that is not
    /// -- asked of SQLite, never by opening the file `[SPEC-STAR-105]`.
    #[test]
    fn the_journal_mode_is_told_apart() {
        let dir = std::env::temp_dir().join(format!("lempi-star-wal-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let (plain, wal) = (dir.join("plain.db"), dir.join("wal.db"));
        let c = rusqlite::Connection::open(&plain).unwrap();
        c.execute_batch("PRAGMA journal_mode=DELETE; CREATE TABLE t (a);").unwrap();
        drop(c);
        let c = rusqlite::Connection::open(&wal).unwrap();
        c.execute_batch("PRAGMA journal_mode=WAL; CREATE TABLE t (a);").unwrap();
        drop(c);
        assert_eq!(wal_mode(&plain), Some(false), "a rollback journal");
        assert_eq!(wal_mode(&wal), Some(true), "WAL");
        assert_eq!(wal_mode(&dir.join("absent.db")), None, "a file that cannot be read says so");
        std::fs::write(dir.join("short.db"), b"SQLite").unwrap();
        assert_eq!(wal_mode(&dir.join("short.db")), None, "and so does one too short to have a header");
    }

    #[test]
    fn a_run_is_star_syncs_stamp_and_sorts_by_time() {
        assert!(is_run("20260928T1830Z"));
        assert!(!is_run("20260928T1830") && !is_run("2026-09-28T18:30Z") && !is_run(""));
        assert!("20260928T1830Z" > "20260927T2359Z" && "20261001T0000Z" > "20260930T2359Z");
    }

    /// A member node with a real mesh key and identity, and the two files.
    pub(crate) struct Node {
        pub(crate) dir: PathBuf,
        pub(crate) l: PathBuf,
        pub(crate) lib: PathBuf,
        pub(crate) mesh: p256::ecdsa::SigningKey,
        pub(crate) mesh_fp: String,
        pub(crate) me: String,
    }

    pub(crate) fn node(name: &str) -> Node {
        use p256::pkcs8::{EncodePublicKey, LineEnding};
        use sha2::Digest;
        let dir = std::env::temp_dir().join(format!("lempi-star-node-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let (l, lib) = (dir.join("listener.db"), dir.join("library.db"));
        rusqlite::Connection::open(&l).unwrap().execute_batch(
            "CREATE TABLE listener_preferences (subject_kind TEXT, subject_id TEXT, rotation REAL, updated_at TEXT,
                 PRIMARY KEY (subject_kind, subject_id));
             INSERT INTO listener_preferences VALUES ('artist','a1',1.0,'t1');").unwrap();
        rusqlite::Connection::open(&lib).unwrap().execute_batch(
            "CREATE TABLE files (file_id INTEGER PRIMARY KEY, audio_md5 TEXT, format TEXT, path TEXT);
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER, end_ms INTEGER,
                 passage_uid TEXT GENERATED ALWAYS AS (printf('%032x', passage_id)) VIRTUAL);
             INSERT INTO files VALUES (1,'m1','flac','/music/a.flac');
             INSERT INTO passages VALUES (10,1,'track',0,1000);").unwrap();
        let mesh = p256::ecdsa::SigningKey::random(&mut rand_core::OsRng);
        let der = mesh.verifying_key().to_public_key_der().unwrap();
        let mesh_fp: String = sha2::Sha256::digest(der.as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
        let pem = mesh.verifying_key().to_public_key_pem(LineEnding::LF).unwrap();
        let me = crate::discovery::identity(&l).unwrap().fingerprint;
        let roster = json!({"mesh": {"name": "Home", "public_key": pem}, "version": 1,
                            "members": [{"fingerprint": me, "role": "player"}]}).to_string();
        std::fs::write(dir.join("mesh-member.json"), json!({"roster": {"roster": roster, "signature": "x"},
            "mesh_fp": mesh_fp, "hub_fp": "h", "hub_address": ""}).to_string()).unwrap();
        Node { dir, l, lib, mesh, mesh_fp, me }
    }

    pub(crate) fn signed(key: &p256::ecdsa::SigningKey, req: &Value) -> Value {
        use base64::Engine;
        use p256::ecdsa::signature::Signer;
        let text = req.to_string();
        let sig: p256::ecdsa::DerSignature = key.sign(text.as_bytes());
        json!({"request": text, "signature": base64::engine::general_purpose::STANDARD.encode(sig.as_bytes())})
    }

    fn request(n: &Node, op: &str, run: &str, cat: Value, lis: Value) -> Value {
        json!({"op": op, "run": run, "node": n.me, "mesh": n.mesh_fp, "nonce": "00112233445566778899aabbccddeeff",
               "at_ms": now_ms(), "catalogue_patch": cat, "listener_patch": lis})
    }

    fn cat_patch() -> Value {
        json!({"tables": [{"name": "files", "columns": ["file_id","audio_md5","format"], "key": ["file_id"],
               "rows": [{"key": [1], "was": [1,"m1","flac"], "now": [1,"m1","opus"]}]}]})
    }

    fn lis_patch() -> Value {
        json!({"tables": [{"name": "listener_preferences", "columns": ["subject_kind","subject_id","rotation","updated_at"],
               "key": ["subject_kind","subject_id"],
               "rows": [{"key": ["artist","a1"], "was": ["artist","a1",1.0,"t1"], "now": ["artist","a1",2.0,"t2"]}]}]})
    }

    /// A listener patch of one lyric row, new here.
    fn lyric_patch(text: &str, was: Value) -> Value {
        json!({"tables": [{"name": "passage_lyrics",
               "columns": ["audio_md5","kind","start_ms","end_ms","mbid","text","no_words","updated_at","origin","passage_uid"],
               "key": ["audio_md5","kind","start_ms"],
               "rows": [{"key": ["m1","track",0], "was": was,
                         "now": ["m1","track",0,1000,"rec-1",text,0,"2026-11-13 09:00:00.250","desktop",null]}]}]})
    }

    /// `[SPEC-LYR-200]`, `[SPEC-LYR-204]`, through `handle` and the player's
    /// own `apply_with` under `MERGED` -- the hub's tests prove the patch with
    /// their own applier, which is not what a node runs. A row of words lands
    /// in the listener the player made, is read back by the passage it is for,
    /// and asks for no rebuild; the same row beside a preference does.
    #[test]
    fn words_land_through_the_players_own_apply_and_rebuild_nothing() {
        let n = node("lyrics");
        // The table as the player makes it, on the listener `node` made; and
        // the one catalogue table more that naming a passage's recording reads.
        drop(lempi_core::db::PlayerStore::open_split(&n.l, &n.lib).unwrap());
        rusqlite::Connection::open(&n.lib).unwrap().execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL);
             INSERT INTO passage_recordings VALUES (10,'rec-1',1.0);").unwrap();
        let words = |n: &Node| -> Option<String> {
            lempi_core::db::Library::open_split(&n.l, &n.lib).unwrap().lyrics(10).unwrap()
        };
        assert_eq!(words(&n), None);

        let first = lyric_patch("typed on the desktop", json!(null));
        let r = request(&n, "rehearse", "20261113T0900Z", json!(null), first.clone());
        let got = handle(&n.l, &n.lib, "rehearse", &signed(&n.mesh, &r)).unwrap();
        assert!(!got.reload);
        assert_eq!(words(&n), None, "a rehearsal keeps nothing");

        let r = request(&n, "commit", "20261113T0900Z", json!(null), first);
        let done = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap();
        assert_eq!(answer_of(&done)["listener"]["applied"], 1, "{}", done.answer);
        assert_eq!(words(&n).as_deref(), Some("typed on the desktop"), "read back by the passage it is for");
        assert!(!done.reload, "words change what a page shows, not what plays: no rebuild");

        // Corrected at the hub, and sent beside a preference: that one does.
        let was = json!(["m1","track",0,1000,"rec-1","typed on the desktop",0,"2026-11-13 09:00:00.250","desktop",null]);
        let mut both = lyric_patch("corrected", was);
        both["tables"].as_array_mut().unwrap().extend(lis_patch()["tables"].as_array().unwrap().iter().cloned());
        let r = request(&n, "commit", "20261113T0905Z", json!(null), both);
        let done = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap();
        assert_eq!(answer_of(&done)["listener"]["applied"], 2, "{}", done.answer);
        assert_eq!(words(&n).as_deref(), Some("corrected"));
        assert!(done.reload, "a preference changes what plays");

        assert!(changes_selection(&json!({"tables": [{"name": "a_table_from_the_future", "rows": []}]})),
                "a table this build does not know asks for the rebuild");
        assert!(!changes_selection(&json!(null)) && !changes_selection(&json!({"tables": []})));
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-STAR-107]`: the snapshot says each half's journal mode, as
    /// SQLite reads it, so the hub can name a listener that is not WAL.
    #[test]
    fn the_snapshot_says_each_halfs_journal_mode() {
        let n = node("journal");
        let ask = |n: &Node, run: &str| -> Value {
            let r = request(n, "snapshot", run, json!(null), json!(null));
            answer_of(&handle(&n.l, &n.lib, "snapshot", &signed(&n.mesh, &r)).unwrap())["journal_mode"].clone()
        };
        assert_eq!(ask(&n, "20261113T0900Z"), json!({"listener": "delete", "library": "delete"}),
                   "as the fixture made them");
        assert_eq!(lempi_core::db::ensure_listener_wal(&n.l).unwrap(), Some(("delete".into(), "wal".into())));
        assert_eq!(ask(&n, "20261113T0901Z"), json!({"listener": "wal", "library": "delete"}),
                   "the listener set at a start, the catalogue left alone");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    fn format_of(lib: &Path) -> String {
        rusqlite::Connection::open(lib).unwrap().query_row("SELECT format FROM files WHERE file_id=1", [], |r| r.get(0)).unwrap()
    }

    /// `[SPEC-NSH-030]`, `[SPEC-NSH-080]`, end to end through `handle`: an
    /// answer signed by this node; a request not signed by the pinned mesh key
    /// refused; a commit applied, then refused when replayed or older.
    #[test]
    fn the_signed_sync_end_to_end() {
        use p256::ecdsa::signature::Verifier;
        use p256::pkcs8::DecodePrivateKey;
        let n = node("e2e");
        let snap = handle(&n.l, &n.lib, "snapshot", &signed(&n.mesh, &request(&n, "snapshot", "20260928T1830Z", json!(null), json!(null)))).unwrap();
        let answer = snap.answer["answer"].as_str().unwrap();
        let pem = std::fs::read_to_string(crate::discovery::identity(&n.l).unwrap().key).unwrap();
        let node_key = p256::ecdsa::SigningKey::from_pkcs8_pem(&pem).unwrap();
        let sig = { use base64::Engine; base64::engine::general_purpose::STANDARD.decode(snap.answer["signature"].as_str().unwrap()).unwrap() };
        assert!(node_key.verifying_key().verify(answer.as_bytes(), &p256::ecdsa::Signature::from_der(&sig).unwrap()).is_ok(),
                "the answer is signed by this node's key");
        let a: Value = serde_json::from_str(answer).unwrap();
        assert_eq!(a["result"]["natural_keys"], 1, "the build says what it can apply [SPEC-NKP-075]");
        assert_eq!(a["result"]["listener"]["tables"][0]["name"], "listener_preferences");
        assert_eq!(a["result"]["catalogue"]["tables"][1]["rows"][0], json!([10, 1, "track", 0, 1000]));

        let stranger = p256::ecdsa::SigningKey::random(&mut rand_core::OsRng);
        let r = request(&n, "commit", "20260928T1830Z", cat_patch(), lis_patch());
        let err = handle(&n.l, &n.lib, "commit", &signed(&stranger, &r)).err().unwrap();
        assert!(err.contains("not signed by this player's mesh"), "{err}");
        assert_eq!(format_of(&n.lib), "flac", "and nothing was written");

        let done = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap();
        assert!(done.reload, "a commit that changed something asks for a reload");
        assert_eq!(format_of(&n.lib), "opus");
        assert_eq!(last_run(&n.l).as_deref(), Some("20260928T1830Z"));
        assert!(sync_dir(&n.l).join("inverse-20260928T1830Z.json").is_file(), "the inverse is kept");
        // `[SPEC-NSH-032]`: replayed, the same commit is answered as it was, and nothing is written.
        let first = answer_of(&done);
        let kept = std::fs::read_to_string(sync_dir(&n.l).join("inverse-20260928T1830Z.json")).unwrap();
        let again = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap();
        let a = answer_of(&again);
        assert_eq!((a["committed_earlier"].clone(), a["catalogue"].clone(), a["listener"].clone()),
                   (json!(true), first["catalogue"].clone(), first["listener"].clone()), "the first answer, again: {a}");
        assert!(!again.reload, "and nothing changed, so nothing to reload");
        assert_eq!(std::fs::read_to_string(sync_dir(&n.l).join("inverse-20260928T1830Z.json")).unwrap(), kept,
                   "the inverse kept is the first commit's");
        let other = request(&n, "commit", "20260928T1830Z", cat_patch(), json!(null));
        let err = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &other)).err().unwrap();
        assert!(err.contains("another patch"), "the same run with another patch is refused: {err}");
        // Its answer kept and the run not recorded -- the last write failed: answered, never applied again.
        set_last_run(&n.l, "20260927T0000Z").unwrap();
        let again = answer_of(&handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap());
        assert_eq!(again["committed_earlier"], true, "{again}");
        assert_eq!(last_run(&n.l).as_deref(), Some("20260928T1830Z"), "and the run is recorded now");
        assert_eq!(std::fs::read_to_string(sync_dir(&n.l).join("inverse-20260928T1830Z.json")).unwrap(), kept,
                   "with the first commit's inverse, not an empty one");
        let older = request(&n, "commit", "20260927T0000Z", cat_patch(), lis_patch());
        assert!(handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &older)).err().unwrap().contains("not newer"));
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-STAR-945]`: a node's own rows about the files the hub names -- the file,
    /// its passages and credits, and what those credits reach -- and nothing about any
    /// other file; the scope widened by what this catalogue links; a cover as its hash.
    #[test]
    fn the_rows_step_answers_with_this_catalogue_about_those_files() {
        let n = node("rows");
        rusqlite::Connection::open(&n.lib).unwrap().execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT,
                 PRIMARY KEY (passage_id, mbid));
             CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE recording_artists (mbid TEXT, artist_mbid TEXT, PRIMARY KEY (mbid, artist_mbid));
             CREATE TABLE artists (mbid TEXT PRIMARY KEY, name TEXT);
             CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, chosen INTEGER, PRIMARY KEY (release_mbid, mbid));
             CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE cover_art (release_mbid TEXT PRIMARY KEY, front BLOB);
             CREATE TABLE file_releases (audio_md5 TEXT PRIMARY KEY, release_mbid TEXT, decided_at TEXT);
             INSERT INTO files VALUES (2,'m2','mp3','/music/b.mp3');
             INSERT INTO passages VALUES (20,2,'radio',0,500);
             INSERT INTO passage_recordings VALUES (10,'rec-wrong',1.0,'musicbrainz'), (20,'rec-other',1.0,'musicbrainz');
             INSERT INTO recordings VALUES ('rec-wrong','I Had a Dream'), ('rec-other','Elsewhere');
             INSERT INTO recording_artists VALUES ('rec-wrong','art-1');
             INSERT INTO artists VALUES ('art-1','John Sebastian'), ('art-2','Nobody here');
             INSERT INTO release_recordings VALUES ('rel-w','rec-wrong',1), ('rel-x','rec-wrong',0);
             INSERT INTO releases VALUES ('rel-w','Woodstock'), ('rel-x','Another');
             INSERT INTO cover_art VALUES ('rel-w', x'0102');
             INSERT INTO file_releases VALUES ('m1','rel-w','2026-10-06T22:07:39');").unwrap();
        let mut r = request(&n, "rows", "20261014T1434Z", json!(null), json!(null));
        r["want"] = json!({"files": ["m1"], "recordings": ["rec-right"]});
        let got = handle(&n.l, &n.lib, "rows", &signed(&n.mesh, &r)).unwrap();
        assert!(!got.reload, "reading is not a change");
        let a: Value = serde_json::from_str(got.answer["answer"].as_str().unwrap()).unwrap();
        let res = &a["result"];
        assert_eq!(res["scope"]["recordings"], json!(["rec-right", "rec-wrong"]),
                   "the hub's recording, and the one this catalogue credits -- the one the hub must remove");
        assert_eq!(res["scope"]["releases"], json!(["rel-w"]), "the file's release, and where its credit is chosen");
        assert_eq!(res["scope"]["artists"], json!(["art-1"]));
        let table = |t: &str| res["tables"].as_array().unwrap().iter().find(|x| x["name"] == t).cloned();
        assert_eq!(table("files").unwrap()["rows"], json!([[1, "m1", "flac", "/music/a.flac"]]), "only the file asked about");
        assert_eq!(table("passage_recordings").unwrap()["rows"], json!([[10, "rec-wrong", 1.0, "musicbrainz"]]));
        assert_eq!(table("release_recordings").unwrap()["rows"].as_array().unwrap().len(), 2,
                   "every release of a credit in scope, chosen or not: an import moves `chosen`");
        let front = &table("cover_art").unwrap()["rows"][0][1];
        assert_eq!(front["sha256"], "a12871fee210fb8619291eaea194581cbd2531e4b23759d225f6806923f63222", "a blob is its hash");
        assert!(table("id_checks").is_none() && table("file_art").is_none(), "a table this catalogue lacks is left out");
        let stranger = p256::ecdsa::SigningKey::random(&mut rand_core::OsRng);
        assert!(handle(&n.l, &n.lib, "rows", &signed(&stranger, &r)).is_err(), "and only the mesh may ask");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-LNK-050]`: asked by recording, with no file named, the node answers with
    /// the files whose passages credit it -- and says that it did.
    #[test]
    fn asked_by_recording_the_rows_step_names_the_files_that_credit_it() {
        let n = node("rows-by-recording");
        rusqlite::Connection::open(&n.lib).unwrap().execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, weight REAL, source TEXT,
                 PRIMARY KEY (passage_id, mbid));
             CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
             INSERT INTO files VALUES (2,'m2','mp3','/music/b.mp3');
             INSERT INTO passages VALUES (20,2,'radio',0,500);
             INSERT INTO passage_recordings VALUES (10,'rec-mine',1.0,'musicbrainz'), (20,'rec-other',1.0,'musicbrainz');
             INSERT INTO recordings VALUES ('rec-mine','Ticking'), ('rec-other','Elsewhere');").unwrap();
        let ask = |by: bool| {
            let mut r = request(&n, "rows", "20261031T1500Z", json!(null), json!(null));
            r["want"] = json!({"files": [], "recordings": ["rec-mine"], "by_recording": by});
            let got = handle(&n.l, &n.lib, "rows", &signed(&n.mesh, &r)).unwrap();
            let a: Value = serde_json::from_str(got.answer["answer"].as_str().unwrap()).unwrap();
            a["result"].clone()
        };
        let res = ask(true);
        assert_eq!(res["by_recording"], json!(true), "and says it was asked so");
        assert_eq!(res["scope"]["files"], json!(["m1"]), "the file crediting the recording, not the other");
        let table = |t: &str| res["tables"].as_array().unwrap().iter().find(|x| x["name"] == t).cloned();
        assert_eq!(table("files").unwrap()["rows"], json!([[1, "m1", "flac", "/music/a.flac"]]));
        assert_eq!(table("recordings").unwrap()["rows"], json!([["rec-mine", "Ticking"]]));
        let plain = ask(false);
        assert_eq!(plain["by_recording"], json!(false));
        assert_eq!(plain["scope"]["files"], json!([]), "not asked by recording, no file is in scope");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-LNK-072]`: asked for tracklists, the node answers a release's own tracklist
    /// and the recordings it names -- and says that it did.
    #[test]
    fn asked_for_tracklists_the_rows_step_names_a_releases_own_tracks() {
        let n = node("rows-tracklists");
        rusqlite::Connection::open(&n.lib).unwrap().execute_batch(
            "CREATE TABLE passage_recordings (passage_id INTEGER, mbid TEXT, PRIMARY KEY (passage_id, mbid));
             CREATE TABLE recordings (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE releases (mbid TEXT PRIMARY KEY, title TEXT);
             CREATE TABLE release_recordings (release_mbid TEXT, mbid TEXT, position INTEGER, chosen INTEGER,
                 PRIMARY KEY (release_mbid, mbid));
             INSERT INTO recordings VALUES ('rec-o1','One'), ('rec-x','Elsewhere');
             INSERT INTO releases VALUES ('rel-org','Org'), ('rel-x','Other');
             INSERT INTO release_recordings VALUES ('rel-org','rec-o1',1,0), ('rel-x','rec-x',1,0);").unwrap();
        let ask = |tl: bool| {
            let mut r = request(&n, "rows", "20261031T2000Z", json!(null), json!(null));
            r["want"] = json!({"files": [], "releases": ["rel-org"], "tracklists": tl});
            let got = handle(&n.l, &n.lib, "rows", &signed(&n.mesh, &r)).unwrap();
            let a: Value = serde_json::from_str(got.answer["answer"].as_str().unwrap()).unwrap();
            a["result"].clone()
        };
        let res = ask(true);
        assert_eq!(res["tracklists"], json!(true), "and says it was asked so");
        let table = |r: &Value, t: &str| r["tables"].as_array().unwrap().iter().find(|x| x["name"] == t).cloned();
        assert_eq!(table(&res, "release_recordings").unwrap()["rows"], json!([["rel-org", "rec-o1", 1, 0]]),
                   "the release's own tracklist, not another's");
        assert_eq!(table(&res, "recordings").unwrap()["rows"], json!([["rec-o1", "One"]]), "and what it names");
        let plain = ask(false);
        assert_eq!(plain["tracklists"], json!(false));
        assert_eq!(table(&plain, "release_recordings").unwrap()["rows"], json!([]), "not asked, no tracklist");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-NSH-080]`: when the listener fails after the catalogue landed,
    /// the catalogue is put back and the run is not recorded as committed.
    #[test]
    fn a_failed_listener_puts_the_catalogue_back() {
        let n = node("restore");
        let r = request(&n, "commit", "20260928T1830Z", cat_patch(), lis_patch());
        let err = commit(&n.l, &n.lib, &r, "20260928T1830Z", "d", &|_, _, _| Err("disk full".into())).err().unwrap();
        assert!(err.contains("disk full") && err.contains("put back"), "{err}");
        assert_eq!(format_of(&n.lib), "flac", "the catalogue is as it was");
        assert_eq!(last_run(&n.l), None, "and the run can be tried again");
        assert!(journal(&n.l, "20260928T1830Z").is_none() && cat_inverses_on_disk(&n.l, "20260928T1830Z") == 0,
                "with no record of it, and no inverse, left behind");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// A commit the player stops inside: `apply` stands in for its listener's half, and panics.
    fn stopped(n: &Node, run: &str, apply: ListenerApply) {
        let r = request(n, "commit", run, cat_patch(), lis_patch());
        let d = digest_of(&r);
        let out = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| commit(&n.l, &n.lib, &r, run, &d, apply)));
        assert!(out.is_err(), "the player stopped inside the commit");
    }

    fn rotation_of(l: &Path) -> f64 {
        rusqlite::Connection::open(l).unwrap()
            .query_row("SELECT rotation FROM listener_preferences WHERE subject_id = 'a1'", [], |r| r.get(0)).unwrap()
    }

    fn snapshot_of(n: &Node, run: &str) -> Value {
        answer_of(&handle(&n.l, &n.lib, "snapshot", &signed(&n.mesh, &request(n, "snapshot", run, json!(null), json!(null)))).unwrap())
    }

    /// `[SPEC-NSH-082]`: a player that stops inside a commit is settled at its next step,
    /// before it answers it: put back where only the catalogue had landed, finished where
    /// both halves had. Each half's inverse was on disk before that half landed.
    #[test]
    fn a_commit_stopped_part_way_is_settled_at_the_next_step() {
        let n = node("settle");
        let run = "20261116T2300Z";

        // Stopped before the listener began: the catalogue had landed, alone.
        stopped(&n, run, &|_, _, _| panic!("the power is cut"));
        assert_eq!(format_of(&n.lib), "opus", "the catalogue had landed");
        assert_eq!(journal(&n.l, run).unwrap()["phase"], "catalogue");
        assert!(sync_dir(&n.l).join(cat_inverse(run, 1)).is_file(), "a whole patch's inverse is on disk, as a part's is");
        let s = snapshot_of(&n, run);
        assert!(s["settled"].as_str().unwrap_or("").contains("the catalogue was put back"), "{s}");
        assert_eq!(format_of(&n.lib), "flac", "put back before the snapshot describes the node");
        assert!(journal(&n.l, run).is_none() && last_run(&n.l).is_none() && cat_inverses_on_disk(&n.l, run) == 0,
                "and the run can be committed afresh");

        // Stopped inside the listener's transaction: its inverse on disk, and nothing of it kept.
        stopped(&n, run, &|l, p, sink| {
            let _ = mesh_sync::apply_parts(l, &mut std::iter::once(Ok(p.clone())), Some(&mesh_sync::MERGED), Rule::Member, false, sink);
            panic!("the power is cut")
        });
        assert!(sync_dir(&n.l).join(lis_inverse(run)).is_file());
        assert_eq!((format_of(&n.lib).as_str(), rotation_of(&n.l)), ("opus", 1.0), "the catalogue in, the listener not");
        let s = snapshot_of(&n, run);
        assert!(s["settled"].as_str().unwrap_or("").contains("the catalogue was put back"), "the listener says it had not: {s}");
        assert_eq!(format_of(&n.lib), "flac");

        // Stopped after both halves had landed, before the run was recorded.
        stopped(&n, run, &|l, p, sink| {
            let c = member_apply(l, p, sink);
            panic!("the power is cut after {c:?}")
        });
        assert_eq!((format_of(&n.lib).as_str(), rotation_of(&n.l), last_run(&n.l)), ("opus", 2.0, None), "both had landed");
        let s = snapshot_of(&n, run);
        assert!(s["settled"].as_str().unwrap_or("").contains("finished"), "{s}");
        assert_eq!(last_run(&n.l).as_deref(), Some(run), "the run is recorded");
        let r = request(&n, "commit", run, cat_patch(), lis_patch());
        let again = answer_of(&handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap());
        assert!(again["committed_earlier"] == true && again["finished"].is_string(), "the hub asking again is told: {again}");
        let kept: Value = serde_json::from_str(&std::fs::read_to_string(sync_dir(&n.l).join(format!("inverse-{run}.json"))).unwrap()).unwrap();
        assert_eq!(kept["catalogue_parts"], json!([cat_inverse(run, 1)]));
        assert!(!sync_dir(&n.l).join(lis_inverse(run)).exists(), "the listener's inverse is in the run's file now");
        mesh_sync::undo_parts(&n.lib, &mut cat_inverses_back(&n.l, run, 1)).unwrap();
        mesh_sync::undo(&n.l, &kept["listener"]).unwrap();
        assert_eq!((format_of(&n.lib).as_str(), rotation_of(&n.l)), ("flac", 1.0), "and what was kept puts both back");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-NSH-082]`: stopped between the catalogue's commit and its record -- the record
    /// still `applying` -- the catalogue itself says whether it had landed. One whose rows hold
    /// neither what was there nor what the patch wrote is not guessed at: every step that writes
    /// is refused, saying why, and the snapshot says so, until a person has looked.
    #[test]
    fn the_catalogue_says_whether_it_landed_and_one_it_cannot_judge_is_refused() {
        let n = node("settle-applying");
        let run = "20261116T2305Z";
        let applying = |n: &Node| {
            let mut j = journal(&n.l, run).unwrap();
            j["phase"] = json!("applying");
            write_atomic(&journal_path(&n.l, run), &j.to_string()).unwrap();
        };
        stopped(&n, run, &|_, _, _| panic!("the power is cut"));
        applying(&n);
        let s = snapshot_of(&n, run);
        assert!(s["settled"].as_str().unwrap_or("").contains("put back"), "it had landed: {s}");
        assert_eq!(format_of(&n.lib), "flac");

        // Its inverse written inside the transaction, which never committed.
        let rehearsed = |n: &Node| {
            let dir = sync_dir(&n.l);
            mesh_sync::apply_parts(&n.lib, &mut std::iter::once(Ok(cat_patch())), None, Rule::Strict, false,
                                   &mut |i, inv| write_atomic(&dir.join(cat_inverse(run, i + 1)), &inv.to_string())).unwrap();
            record(&n.l, run, "d", "applying", None).unwrap();
        };
        rehearsed(&n);
        let s = snapshot_of(&n, run);
        assert!(s["settled"].as_str().unwrap_or("").contains("nothing of it had landed"), "{s}");
        assert!(cat_inverses_on_disk(&n.l, run) == 0 && journal(&n.l, run).is_none(), "and nothing of it is left");

        // A catalogue changed since, under the stopped commit: neither.
        rehearsed(&n);
        rusqlite::Connection::open(&n.lib).unwrap().execute("UPDATE files SET format = 'wav' WHERE file_id = 1", []).unwrap();
        let s = snapshot_of(&n, run);
        assert!(s["unsettled"].as_str().unwrap_or("").contains("could not be read"), "the snapshot says why: {s}");
        let r = request(&n, "commit", "20261116T2310Z", cat_patch(), lis_patch());
        let err = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).err().unwrap();
        assert!(err.contains("until a person has looked"), "{err}");
        assert_eq!(format_of(&n.lib), "wav", "and nothing was written");
        let _ = std::fs::remove_file(journal_path(&n.l, run));     // the person, having looked
        rusqlite::Connection::open(&n.lib).unwrap().execute("UPDATE files SET format = 'flac' WHERE file_id = 1", []).unwrap();
        assert!(handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).is_ok(), "then the node takes a commit again");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-NSH-032]`: one step at a time. A commit asked while another step holds the
    /// node waits for it, rather than running beside it.
    #[test]
    fn a_step_waits_for_the_one_before_it() {
        let n = node("one-at-a-time");
        let r = signed(&n.mesh, &request(&n, "commit", "20261116T2315Z", cat_patch(), lis_patch()));
        let held = STEPS.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        let (l, lib) = (n.l.clone(), n.lib.clone());
        let t = std::thread::spawn(move || handle(&l, &lib, "commit", &r).map(|h| answer_of(&h)));
        std::thread::sleep(std::time::Duration::from_millis(300));
        assert!(!t.is_finished(), "the commit waits while another step holds the node");
        assert_eq!(format_of(&n.lib), "flac", "and has written nothing");
        drop(held);
        let a = t.join().unwrap().unwrap();
        assert_eq!(a["catalogue"]["applied"], 1, "{a}");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-NSH-080]`: three inverses kept, the oldest dropped.
    #[test]
    fn three_inverses_are_kept() {
        let d = std::env::temp_dir().join(format!("lempi-star-node-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let l = d.join("listener.db");
        for run in ["20260901T0000Z", "20260902T0000Z", "20260903T0000Z", "20260904T0000Z"] {
            keep_inverse(&l, run, 0, Some(&json!({"tables": []})), None).unwrap();
            set_last_run(&l, run).unwrap();
        }
        let mut names: Vec<String> = std::fs::read_dir(sync_dir(&l)).unwrap()
            .filter_map(|e| e.ok()?.file_name().into_string().ok()).filter(|n| n.starts_with("inverse-")).collect();
        names.sort();
        assert_eq!(names, ["inverse-20260902T0000Z.json", "inverse-20260903T0000Z.json", "inverse-20260904T0000Z.json"]);
        assert_eq!(last_run(&l).as_deref(), Some("20260904T0000Z"));
        let _ = std::fs::remove_dir_all(&d);
    }

    // ---- a catalogue patch in parts [SPEC-NKP-920..940] --------------------

    /// Three parts: change a file, add a file, add its passage.
    fn calls() -> Vec<&'static str> {
        WINDOW_CALLS.with(|c| c.borrow().clone())
    }

    fn make_read_only(lib: &Path) -> bool {
        let mut perm = std::fs::metadata(lib).unwrap().permissions();
        perm.set_readonly(true);
        std::fs::set_permissions(lib, perm).unwrap();
        !writable(lib)           // false when the tests run as root, which a permission bit does not stop
    }

    /// `[SPEC-STAR-104]`: a catalogue on a read-only partition is opened for the rehearsal as
    /// well as the commit -- both write, the first to roll back -- and closed again after
    /// each, whether the step succeeded or not. The snapshot says it is read-only.
    #[test]
    fn a_read_only_catalogue_is_opened_for_the_check_and_the_commit_and_closed_after() {
        let n = node("ro");
        if !make_read_only(&n.lib) {
            eprintln!("SKIPPED: a permission bit does not make a file read-only to this user (root?)");
            let _ = std::fs::remove_dir_all(&n.dir);
            return;
        }
        WINDOW_CALLS.with(|c| c.borrow_mut().clear());
        let snap = handle(&n.l, &n.lib, "snapshot", &signed(&n.mesh, &request(&n, "snapshot", "20260929T0000Z", json!(null), json!(null)))).unwrap();
        let a: Value = serde_json::from_str(snap.answer["answer"].as_str().unwrap()).unwrap();
        assert_eq!(a["result"]["library_readonly"], true, "the snapshot says the catalogue is on a read-only partition");
        assert_eq!(a["result"]["catalogue_window"], 1, "and that this build can open it");
        assert!(calls().is_empty(), "a snapshot only reads: the partition is never opened for it");

        let r = request(&n, "rehearse", "20260929T0000Z", cat_patch(), json!(null));
        let done = handle(&n.l, &n.lib, "rehearse", &signed(&n.mesh, &r)).unwrap();
        assert_eq!(done.answer["answer"].as_str().map(|t| serde_json::from_str::<Value>(t).unwrap()).unwrap()["result"]["catalogue"]["applied"], 1);
        assert_eq!(calls(), ["on", "off"], "a rehearsal opens the partition and closes it");
        assert!(!writable(&n.lib), "and it is read-only again");
        assert_eq!(format_of(&n.lib), "flac", "nothing was kept");

        let bad = json!({"tables": [{"name": "files", "columns": ["file_id","audio_md5","format"], "key": ["file_id"],
                         "rows": [{"key": [1], "was": [1,"m1","mp3"], "now": [1,"m1","opus"]}]}]});
        let r = request(&n, "rehearse", "20260929T0000Z", bad, json!(null));
        assert!(handle(&n.l, &n.lib, "rehearse", &signed(&n.mesh, &r)).is_err(), "a patch the node does not expect is refused");
        assert_eq!(calls(), ["on", "off", "on", "off"], "and the partition is closed after a refusal too");
        assert!(!writable(&n.lib));

        let r = request(&n, "commit", "20260929T0000Z", cat_patch(), lis_patch());
        let done = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap();
        assert!(done.reload);
        assert_eq!(format_of(&n.lib), "opus", "the commit landed through the window");
        assert_eq!(calls(), ["on", "off", "on", "off", "on", "off"], "one window, around the rehearsal inside it and the apply");
        assert!(!writable(&n.lib), "and the partition is back to read-only");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-RTF-020..035]`, end to end through `handle`, on a read-only catalogue as bose's:
    /// the snapshot says this build retires files; a rehearsal says what would move and moves
    /// nothing; a commit whose patch removes a file's row moves that file to the node's own
    /// `_Retired` inside the window, leaves one still catalogued where it is, answers both,
    /// and keeps the move in its inverse.
    #[test]
    fn a_commit_retires_the_files_its_patch_uncatalogues() {
        let n = node("retire");
        let music = n.dir.join("Music");
        let (old, kept) = (music.join("Artist/Album/old.mp3"), music.join("Artist/Album/kept.mp3"));
        std::fs::create_dir_all(old.parent().unwrap()).unwrap();
        std::fs::write(&old, b"the old rip").unwrap();
        std::fs::write(&kept, b"still played").unwrap();
        rusqlite::Connection::open(&n.lib).unwrap().execute_batch(&format!(
            "INSERT INTO files VALUES (2,'old','mp3','{}'); INSERT INTO files VALUES (3,'kept','mp3','{}');",
            old.display(), kept.display())).unwrap();
        let entry = |md5: &str, p: &Path, rel: &str| json!({"audio_md5": md5, "path": p.to_string_lossy(), "rel_path": rel,
            "sha256": crate::bundle::sha256_file(p).unwrap(), "size_bytes": std::fs::metadata(p).unwrap().len(),
            "retired_at": "2026-10-30"});
        let list = json!([entry("old", &old, "Artist/Album/old.mp3"), entry("kept", &kept, "Artist/Album/kept.mp3")]);
        let drop_old = json!({"tables": [{"name": "files", "columns": ["file_id","audio_md5","format","path"], "key": ["file_id"],
            "rows": [{"key": [2], "was": [2,"old","mp3",old.to_string_lossy()], "now": null}]}]});
        let ro = make_read_only(&n.lib);
        WINDOW_CALLS.with(|c| c.borrow_mut().clear());
        let result = |h: Handled| -> Value {
            serde_json::from_str::<Value>(h.answer["answer"].as_str().unwrap()).unwrap()["result"].clone()
        };
        let snap = result(handle(&n.l, &n.lib, "snapshot", &signed(&n.mesh, &request(&n, "snapshot", "20261101T0000Z", json!(null), json!(null)))).unwrap());
        assert_eq!(snap["retire_files"], 1, "the build says it retires files [SPEC-RTF-060]");

        let mut r = request(&n, "rehearse", "20261101T0000Z", drop_old.clone(), json!(null));
        r["retire"] = list.clone();
        let rehearsed = result(handle(&n.l, &n.lib, "rehearse", &signed(&n.mesh, &r)).unwrap());
        assert_eq!(rehearsed["retire"]["files"][0]["outcome"], "to move", "{rehearsed}");
        assert!(old.is_file(), "a rehearsal moves nothing");

        let mut r = request(&n, "commit", "20261101T0000Z", drop_old, json!(null));
        r["retire"] = list;
        let done = result(handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap());
        let outcomes: Vec<&str> = done["retire"]["files"].as_array().unwrap().iter().map(|f| f["outcome"].as_str().unwrap()).collect();
        assert_eq!(outcomes, ["moved", "still catalogued"], "{done}");
        let to = music.join("_Retired/Artist/Album/2026-10-30/old.mp3");
        assert!(to.is_file() && !old.exists(), "moved to the node's own _Retired");
        assert!(kept.is_file(), "a file still catalogued is left where it is");
        if ro {
            assert_eq!(calls(), ["on", "off", "on", "off"], "the rehearsal's window, then the commit's, around the move");
            assert!(!writable(&n.lib), "and the partition is read-only again");
        }
        let inv: Value = serde_json::from_str(&std::fs::read_to_string(sync_dir(&n.l).join("inverse-20261101T0000Z.json")).unwrap()).unwrap();
        assert_eq!(inv["moved"][0]["from"].as_str().unwrap(), old.to_string_lossy(), "the move kept with the commit's inverse");
        assert_eq!(crate::retire::undo(&inv["moved"]), Ok(1));
        assert!(old.is_file() && !to.exists(), "and an undo puts it back [SPEC-RTF-035]");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-STAR-104]`, `[BOS-RUN-092]`: a catalogue in WAL mode cannot be opened on read-only
    /// media, so after the commit the partition is left read-write, and the answer says so.
    #[test]
    fn a_wal_catalogue_leaves_the_partition_open_and_says_so() {
        let n = node("rowal");
        {
            let c = rusqlite::Connection::open(&n.lib).unwrap();
            c.query_row("PRAGMA journal_mode=WAL", [], |r| r.get::<_, String>(0)).unwrap();
        }
        let _ = std::fs::remove_file(n.lib.with_extension("db-wal"));
        let _ = std::fs::remove_file(n.lib.with_extension("db-shm"));
        if !make_read_only(&n.lib) {
            eprintln!("SKIPPED: a permission bit does not make a file read-only to this user (root?)");
            let _ = std::fs::remove_dir_all(&n.dir);
            return;
        }
        WINDOW_CALLS.with(|c| c.borrow_mut().clear());
        let r = request(&n, "commit", "20260929T0001Z", cat_patch(), json!(null));
        let done = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).unwrap();
        let a: Value = serde_json::from_str(done.answer["answer"].as_str().unwrap()).unwrap();
        assert!(a["result"]["warning"].as_str().unwrap_or("").contains("WAL"), "the answer says why: {a}");
        assert_eq!(calls(), ["on"], "the partition was not closed over a WAL file");
        assert!(writable(&n.lib));
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    fn part_texts() -> Vec<String> {
        vec![
            json!({"tables": [{"name": "files", "columns": ["file_id","audio_md5","format"], "key": ["file_id"],
                   "rows": [{"key": [1], "was": [1,"m1","flac"], "now": [1,"m1","opus"]}]}]}).to_string(),
            json!({"tables": [{"name": "files", "columns": ["file_id","audio_md5","format"], "key": ["file_id"],
                   "rows": [{"key": [2], "was": null, "now": [2,"m2","wav"]}]}]}).to_string(),
            json!({"tables": [{"name": "passages", "columns": ["passage_id","file_id","kind","start_ms","end_ms"], "key": ["passage_id"],
                   "rows": [{"key": [11], "was": null, "now": [11,2,"track",0,500]}]}]}).to_string(),
        ]
    }

    fn part_request(n: &Node, run: &str, i: u64, of: u64, text: &str, total: u64) -> Value {
        let mut r = request(n, "stage", run, json!(null), json!(null));
        r["part"] = json!({"half": "catalogue", "n": i, "of": of, "sha256": sha_hex(text.as_bytes()), "total_bytes": total, "text": text});
        r
    }

    fn spec_of(texts: &[String]) -> Value {
        json!({"of": texts.len(), "sha256": texts.iter().map(|t| sha_hex(t.as_bytes())).collect::<Vec<_>>()})
    }

    fn answer_of(h: &Handled) -> Value {
        let a: Value = serde_json::from_str(h.answer["answer"].as_str().unwrap()).unwrap();
        a["result"].clone()
    }

    fn stage_all(n: &Node, run: &str, texts: &[String]) {
        let total: u64 = texts.iter().map(|t| t.len() as u64).sum();
        for (i, t) in texts.iter().enumerate() {
            let r = part_request(n, run, i as u64 + 1, texts.len() as u64, t, total);
            let h = handle(&n.l, &n.lib, "stage", &signed(&n.mesh, &r)).unwrap_or_else(|e| panic!("stage {}: {e}", i + 1));
            assert_eq!(answer_of(&h)["staged"], i as u64 + 1);
        }
    }

    fn with_parts(n: &Node, op: &str, run: &str, texts: &[String]) -> Value {
        let mut r = request(n, op, run, json!(null), json!(null));
        r["catalogue_parts"] = spec_of(texts);
        signed(&n.mesh, &r)
    }

    fn files_in(lib: &Path) -> i64 {
        rusqlite::Connection::open(lib).unwrap().query_row("SELECT count(*) FROM files", [], |r| r.get(0)).unwrap()
    }

    /// Staged, then rehearsed (which keeps nothing), then committed in one transaction: the inverse of each part
    /// is on disk, the staged parts are gone, and the undo puts it all back at once.
    #[test]
    fn parts_are_staged_then_committed_in_one_transaction() {
        let n = node("parts");
        let run = "20260928T1830Z";
        let texts = part_texts();
        stage_all(&n, run, &texts);
        let have = answer_of(&handle(&n.l, &n.lib, "staged", &signed(&n.mesh, &request(&n, "staged", run, json!(null), json!(null)))).unwrap());
        assert_eq!(have["catalogue"]["2"], sha_hex(texts[1].as_bytes()), "the node says which parts it holds, by digest");
        let reh = answer_of(&handle(&n.l, &n.lib, "rehearse", &with_parts(&n, "rehearse", run, &texts)).unwrap());
        assert_eq!(reh["catalogue"]["applied"], 3);
        assert_eq!((format_of(&n.lib).as_str(), files_in(&n.lib)), ("flac", 1), "a rehearsal keeps nothing");
        assert!(staged_dir(&n.l, run).is_dir(), "and leaves the parts staged for the commit");

        let done = handle(&n.l, &n.lib, "commit", &with_parts(&n, "commit", run, &texts)).unwrap();
        assert!(done.reload);
        let res = answer_of(&done);
        assert_eq!((res["catalogue"]["applied"].as_u64(), res["catalogue_parts"].as_u64()), (Some(3), Some(3)));
        assert_eq!((format_of(&n.lib).as_str(), files_in(&n.lib)), ("opus", 2), "all three parts landed");
        assert!(!staged_dir(&n.l, run).exists(), "the staged parts were consumed");
        let d = sync_dir(&n.l);
        for i in 1..=3 {
            assert!(d.join(format!("inverse-{run}-c{i}.json")).is_file(), "part {i}'s inverse is on disk");
        }
        let kept: Value = serde_json::from_str(&std::fs::read_to_string(d.join(format!("inverse-{run}.json"))).unwrap()).unwrap();
        assert_eq!(kept["catalogue_parts"].as_array().map(Vec::len), Some(3), "and listed by the run's inverse");

        let mut back = (1..=3).rev().map(|i| {
            serde_json::from_str::<Value>(&std::fs::read_to_string(d.join(format!("inverse-{run}-c{i}.json"))).unwrap()).map_err(|e| e.to_string())
        });
        // `[SPEC-NSH-032]`: asked again, the node says the run is in, and answers the commit
        // as it did -- with its parts consumed, and nothing staged or written.
        let have = answer_of(&handle(&n.l, &n.lib, "staged", &signed(&n.mesh, &request(&n, "staged", run, json!(null), json!(null)))).unwrap());
        assert_eq!(have, json!({"catalogue": {}, "committed": true}), "staged says the run is committed here");
        let again = answer_of(&handle(&n.l, &n.lib, "commit", &with_parts(&n, "commit", run, &texts)).unwrap());
        assert_eq!((again["committed_earlier"].clone(), again["catalogue"]["applied"].clone()), (json!(true), json!(3)), "{again}");
        mesh_sync::undo_parts(&n.lib, &mut back).unwrap();
        assert_eq!((format_of(&n.lib).as_str(), files_in(&n.lib)), ("flac", 1), "the undo is whole");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// A part that did not arrive intact, a run no newer than the last, a part that is missing, a staged part
    /// that is not the one named, and a bad row in the last part: each refused, and nothing written.
    #[test]
    fn a_part_in_doubt_refuses_the_commit_and_writes_nothing() {
        let n = node("parts-refuse");
        let run = "20260928T1830Z";
        let texts = part_texts();
        let mut bad = part_request(&n, run, 1, 3, &texts[0], 100);
        bad["part"]["text"] = json!(texts[1]);
        assert!(handle(&n.l, &n.lib, "stage", &signed(&n.mesh, &bad)).err().unwrap().contains("did not arrive intact"));
        assert!(!staged_dir(&n.l, run).exists(), "and nothing was staged");

        stage_all(&n, run, &texts[..2]);                       // the third is missing
        let mut r = request(&n, "commit", run, json!(null), json!(null));
        r["catalogue_parts"] = spec_of(&texts);
        let err = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).err().unwrap();
        assert!(err.contains("part 3 of 3 is not staged here"), "{err}");
        assert_eq!((format_of(&n.lib).as_str(), files_in(&n.lib)), ("flac", 1));

        stage_all(&n, run, &texts);                            // now all three
        std::fs::write(part_file(&staged_dir(&n.l, run), 2), "{\"tables\": []}").unwrap();   // tampered with on disk
        let err = handle(&n.l, &n.lib, "commit", &with_parts(&n, "commit", run, &texts)).err().unwrap();
        assert!(err.contains("not the one the hub named"), "{err}");
        assert_eq!(files_in(&n.lib), 1, "nothing written");

        // A bad row in the last part rolls back the first two, leaves no inverse behind, and the run can be tried again.
        let mut texts2 = part_texts();
        texts2[2] = json!({"tables": [{"name": "passages", "columns": ["passage_id","file_id","kind","start_ms","end_ms"], "key": ["passage_id"],
                           "rows": [{"key": [10], "was": [10,1,"track",0,999], "now": [10,1,"track",0,5]}]}]}).to_string();
        stage_all(&n, run, &texts2);
        let err = handle(&n.l, &n.lib, "commit", &with_parts(&n, "commit", run, &texts2)).err().unwrap();
        assert!(err.contains("part 3"), "the part is named: {err}");
        assert_eq!((format_of(&n.lib).as_str(), files_in(&n.lib)), ("flac", 1), "the first two parts were rolled back with it");
        assert!(!sync_dir(&n.l).join(format!("inverse-{run}-c1.json")).exists(), "no inverse left over");
        assert_eq!(last_run(&n.l), None, "and the run is not recorded as committed");

        set_last_run(&n.l, "20260929T0000Z").unwrap();
        let old = part_request(&n, run, 1, 3, &texts[0], 100);
        assert!(handle(&n.l, &n.lib, "stage", &signed(&n.mesh, &old)).err().unwrap().contains("not newer"), "a stale run is not staged");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-NKP-935]`: a node with too little room says so, with the figures, before it takes a first part;
    /// and parts for a run that can no longer commit are cleared at the next step.
    #[test]
    fn staging_is_bounded_and_clears_what_can_no_longer_commit() {
        let n = node("parts-room");
        let texts = part_texts();
        if cfg!(unix) {
            let huge = part_request(&n, "20260928T1830Z", 1, 3, &texts[0], u64::MAX / 4);
            let err = handle(&n.l, &n.lib, "stage", &signed(&n.mesh, &huge)).err().unwrap();
            assert!(err.contains("not enough room to stage") && err.contains("MB free"), "{err}");
            assert!(!staged_dir(&n.l, "20260928T1830Z").exists(), "nothing was staged");
        }
        let d = sync_dir(&n.l);
        std::fs::create_dir_all(d.join("staged-20260929T0000Z")).unwrap();
        set_last_run(&n.l, "20260929T0000Z").unwrap();
        stage_all(&n, "20261001T0000Z", &texts);
        assert!(!d.join("staged-20260929T0000Z").exists(), "a run no newer than the last committed is cleared");
        assert!(staged_dir(&n.l, "20261001T0000Z").is_dir(), "while the run being staged is kept");
        let _ = std::fs::remove_dir_all(&n.dir);
    }
}
