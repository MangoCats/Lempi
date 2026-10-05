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
//! replayed to roll the node back `[SPEC-NSH-030]`.

use std::path::{Path, PathBuf};

use lempi_core::mesh_sync::{self, Rule};
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

/// What a handled step returns: the signed answer, and whether the player
/// should rebuild what it loads once at start (the catalogue or the
/// household's preferences changed).
pub struct Handled {
    pub answer: Value,
    pub reload: bool,
}

pub fn handle(listener: &Path, library: &Path, op: &str, body: &Value) -> Result<Handled, String> {
    if !matches!(op, "snapshot" | "rehearse" | "commit" | "stage" | "staged") {
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
    let (result, reload) = match op {
        "snapshot" => (snapshot(listener, library)?, false),
        "rehearse" => (rehearse(listener, library, &req)?, false),
        "stage" => (stage(listener, &req, &run)?, false),
        "staged" => (staged(listener, &run), false),
        _ => {
            let last = last_run(listener);
            if last.as_deref().is_some_and(|l| run.as_str() <= l) {
                return Err(format!("run {run} is not newer than {} -- already committed here", last.unwrap_or_default()));
            }
            commit(listener, library, &req, &run, &|l, p| {
                mesh_sync::apply_with(l, p, Some(&mesh_sync::MERGED), Rule::Member, true)
            })?
        }
    };
    let answer = json!({"op": op, "run": run, "nonce": req["nonce"], "node": me, "result": result}).to_string();
    let signature = crate::membership::sign_as_node(listener, &answer)?;
    Ok(Handled { answer: json!({"answer": answer, "signature": signature}), reload })
}

/// Everything about a request but its signature, which is checked first.
fn check(req: &Value, op: &str, me: &str, mesh_fp: &str, now: i64) -> Result<(), String> {
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
    if !req["run"].as_str().is_some_and(is_run) {
        return Err(format!("not a run: {}", req["run"]));
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

fn now_ms() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_millis() as i64)
}

/// What this build can apply, so the hub never hands a patch to a player that would
/// misread it `[SPEC-NKP-075]`. 1 is the catalogue patch by natural key.
const NATURAL_KEYS: i64 = 1;

fn snapshot(listener: &Path, library: &Path) -> Result<Value, String> {
    Ok(json!({
        "listener": mesh_sync::export(listener, &mesh_sync::MERGED)?,
        "catalogue": mesh_sync::catalogue_summary(library)?,
        "last_run": last_run(listener),
        "natural_keys": NATURAL_KEYS,
    }))
}

fn has_rows(p: &Value) -> bool {
    p["tables"].as_array().is_some_and(|t| !t.is_empty())
}

fn counts(c: &mesh_sync::Applied) -> Value {
    json!({"applied": c.applied, "already": c.already, "kept": c.kept})
}

/// Both patches against the live files, nothing kept. The catalogue under the
/// strict rule refuses on its first mismatch; the listener under the member
/// rule counts what it would keep.
fn rehearse(listener: &Path, library: &Path, req: &Value) -> Result<Value, String> {
    let (cat, lis) = (&req["catalogue_patch"], &req["listener_patch"]);
    let mut out = json!({});
    if let Some(spec) = req.get("catalogue_parts").filter(|v| !v.is_null()) {
        // `[SPEC-NKP-920]`: what was staged, every part against its digest again, all in
        // one transaction that is rolled back.
        let paths = part_paths(listener, req["run"].as_str().unwrap_or(""), spec)?;
        let mut it = paths.iter().map(|p| load_part(p));
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
    Ok(out)
}

/// The listener's half of a commit, apart so a test can make it fail after
/// the catalogue has landed -- the one failure a rehearsal cannot foresee.
type ListenerApply<'a> = &'a dyn Fn(&Path, &Value) -> Result<mesh_sync::Outcome, String>;

/// What a catalogue apply left to undo it with: the inverse itself, or how many parts'
/// inverses were written to disk `[SPEC-NKP-940]`.
enum CatInverse {
    Mem(Value),
    Parts(usize),
}

fn commit(listener: &Path, library: &Path, req: &Value, run: &str, apply_listener: ListenerApply)
    -> Result<(Value, bool), String> {
    let (cat, lis) = (&req["catalogue_patch"], &req["listener_patch"]);
    let parts = req.get("catalogue_parts").filter(|v| !v.is_null());
    let has_cat = parts.is_some() || has_rows(cat);
    // Rehearsed first, here, whatever the hub rehearsed before: the files may
    // have moved since.
    rehearse(listener, library, req)?;
    if !has_cat && !has_rows(lis) {
        set_last_run(listener, run)?;
        return Ok((json!({"catalogue": null, "listener": null}), false));
    }
    // The root verb only when the file cannot be written where it is -- a
    // read-only partition, as on bose. A catalogue already writable (on the
    // root filesystem, or anywhere a test puts one) never reaches it.
    let opened = if has_cat && !writable(library) { library_rw(true)? } else { false };
    let applied = (|| -> Result<(Option<(mesh_sync::Applied, CatInverse)>, Option<mesh_sync::Outcome>), String> {
        let c = if let Some(spec) = parts {
            // One transaction over every part; each part's inverse goes to disk as it is made.
            let paths = part_paths(listener, run, spec)?;
            let n = paths.len();
            let dir = sync_dir(listener);
            std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
            let mut it = paths.iter().map(|p| load_part(p));
            let done = mesh_sync::apply_parts(library, &mut it, None, Rule::Strict, true, &mut |i, inv| {
                write_atomic(&dir.join(format!("inverse-{run}-c{}.json", i + 1)), &inv.to_string())
            });
            match done {
                Ok(counts) => Some((counts, CatInverse::Parts(n))),
                Err(e) => {
                    remove_inverse_parts(listener, run, n);
                    return Err(format!("catalogue: {e}"));
                }
            }
        } else if has_rows(cat) {
            let o = mesh_sync::apply_with(library, cat, None, Rule::Strict, true).map_err(|e| format!("catalogue: {e}"))?;
            Some((o.counts, CatInverse::Mem(o.inverse)))
        } else {
            None
        };
        let l = if has_rows(lis) {
            match apply_listener(listener, lis) {
                Ok(l) => Some(l),
                Err(e) => {
                    let put_back = c.as_ref().map(|(_, inv)| match inv {
                        CatInverse::Mem(i) => mesh_sync::undo(library, i),
                        CatInverse::Parts(n) => {
                            let dir = sync_dir(listener);
                            let mut back = (1..=*n).rev().map(|i| {
                                std::fs::read_to_string(dir.join(format!("inverse-{run}-c{i}.json")))
                                    .map_err(|e| e.to_string())
                                    .and_then(|t| serde_json::from_str::<Value>(&t).map_err(|e| e.to_string()))
                            });
                            let r = mesh_sync::undo_parts(library, &mut back);
                            remove_inverse_parts(listener, run, *n);
                            r
                        }
                    });
                    return Err(match put_back {
                        Some(Err(u)) => format!("listener: {e}; and the catalogue could NOT be put back: {u}"),
                        Some(Ok(_)) => format!("listener: {e}; the catalogue was put back"),
                        None => format!("listener: {e}"),
                    });
                }
            }
        } else {
            None
        };
        Ok((c, l))
    })();
    // `[SPEC-STAR-104]`: a catalogue on read-only media must be in rollback-
    // journal mode, because a WAL file there has no sidecar to open and the
    // player crash-loops `[BOS-RUN-092]`. Checked before the partition goes back,
    // and left read-write -- harmless to the player -- rather than closed over
    // a file that would not open. A check that cannot run says so, too.
    let mut warning: Option<String> = None;
    if opened {
        match wal_mode(library) {
            Some(false) => {
                if let Err(e) = library_rw(false) {
                    tracing::warn!("star sync: the catalogue's partition could not be returned to read-only: {e}");
                    warning = Some(format!("the catalogue's partition could not be returned to read-only: {e}"));
                }
            }
            Some(true) => {
                warning = Some("the catalogue is in WAL mode, which read-only media cannot open: its partition was left read-write [BOS-RUN-092]".into());
            }
            None => {
                warning = Some("the catalogue's journal mode could not be read: its partition was left read-write".into());
            }
        }
        if let Some(w) = &warning {
            tracing::warn!("star sync: {w}");
        }
    }
    let (c, l) = applied?;
    let (mem, n_parts) = match c.as_ref().map(|(_, inv)| inv) {
        Some(CatInverse::Mem(i)) => (Some(i), 0),
        Some(CatInverse::Parts(n)) => (None, *n),
        None => (None, 0),
    };
    keep_inverse(listener, run, mem, n_parts, l.as_ref().map(|o| &o.inverse))?;
    set_last_run(listener, run)?;
    if n_parts > 0 {
        let _ = std::fs::remove_dir_all(staged_dir(listener, run));       // consumed
    }
    let mut result = json!({"catalogue": c.as_ref().map(|(counts_, _)| counts(counts_)),
                            "listener": l.as_ref().map(|o| counts(&o.counts))});
    if n_parts > 0 {
        result["catalogue_parts"] = json!(n_parts);
    }
    if let Some(w) = warning {
        result["warning"] = json!(w);
    }
    tracing::info!("star sync: run {run} committed here: {result}");
    Ok((result, true))
}

/// Whether a SQLite file is in WAL mode, from its own header: bytes 18 and 19
/// are the write and read format versions, 1 for a rollback journal and 2 for
/// WAL. Read from the file rather than asked of a connection, so it needs no
/// lock the player's own connection holds. `None` when the header cannot be read.
fn wal_mode(p: &Path) -> Option<bool> {
    use std::io::Read;
    let mut h = [0u8; 20];
    std::fs::File::open(p).and_then(|mut f| f.read_exact(&mut h)).ok()?;
    Some(h[18] == 2 || h[19] == 2)
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
    let tmp = d.join("state.json.part");
    std::fs::write(&tmp, json!({"last_run": run}).to_string()).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, d.join("state.json")).map_err(|e| e.to_string())
}

/// What a commit wrote, reversed, as `inverse-<run>.json`; the newest `KEEP` runs
/// are kept. `mesh_sync::undo` with either half puts that half back. A catalogue sent
/// in parts has each part's inverse in `inverse-<run>-c<n>.json`, which this file
/// lists, and which `undo_parts` puts back together `[SPEC-NKP-940]`.
fn keep_inverse(listener: &Path, run: &str, cat: Option<&Value>, cat_parts: usize, lis: Option<&Value>) -> Result<(), String> {
    let d = sync_dir(listener);
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    let names: Vec<String> = (1..=cat_parts).map(|i| format!("inverse-{run}-c{i}.json")).collect();
    std::fs::write(
        d.join(format!("inverse-{run}.json")),
        json!({"run": run, "catalogue": cat, "catalogue_parts": names, "listener": lis}).to_string(),
    )
    .map_err(|e| e.to_string())?;
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
fn write_atomic(path: &Path, text: &str) -> Result<(), String> {
    let tmp = path.with_extension("part");
    std::fs::write(&tmp, text).map_err(|e| format!("{}: {e}", tmp.display()))?;
    std::fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))
}

fn remove_inverse_parts(listener: &Path, run: &str, n: usize) {
    for i in 1..=n {
        let _ = std::fs::remove_file(sync_dir(listener).join(format!("inverse-{run}-c{i}.json")));
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
    Ok(s.f_bavail as u64 * s.f_frsize as u64)
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
                "not enough room to stage: {} MB free on this node's state partition, {} MB needed                  (twice the {} MB staged, and {} MB to spare)",
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
    json!({"catalogue": have})
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

/// Whether `p` can be opened for writing: false on a read-only mount (EROFS).
/// Opening for write changes nothing; the commit's own connection writes.
fn writable(p: &Path) -> bool {
    std::fs::OpenOptions::new().write(true).open(p).is_ok()
}

/// Make the catalogue's partition writable for the commit, through the one
/// root verb for it; true when it was read-only and so must be put back.
#[cfg(feature = "appliance")]
fn library_rw(on: bool) -> Result<bool, String> {
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
#[cfg(not(feature = "appliance"))]
fn library_rw(_on: bool) -> Result<bool, String> {
    Ok(false)
}

#[cfg(test)]
mod tests {
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

    /// `[SPEC-STAR-104]`: the journal mode is read from the header, so a catalogue
    /// in WAL mode is told from one that is not without opening it.
    #[test]
    fn the_journal_mode_is_read_from_the_header() {
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
    struct Node {
        dir: PathBuf,
        l: PathBuf,
        lib: PathBuf,
        mesh: p256::ecdsa::SigningKey,
        mesh_fp: String,
        me: String,
    }

    fn node(name: &str) -> Node {
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
             CREATE TABLE passages (passage_id INTEGER PRIMARY KEY, file_id INTEGER, kind TEXT, start_ms INTEGER, end_ms INTEGER);
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

    fn signed(key: &p256::ecdsa::SigningKey, req: &Value) -> Value {
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
        let replay = handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &r)).err().unwrap();
        assert!(replay.contains("not newer"), "a replayed commit is refused: {replay}");
        let older = request(&n, "commit", "20260927T0000Z", cat_patch(), lis_patch());
        assert!(handle(&n.l, &n.lib, "commit", &signed(&n.mesh, &older)).err().unwrap().contains("not newer"));
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// `[SPEC-NSH-080]`: when the listener fails after the catalogue landed,
    /// the catalogue is put back and the run is not recorded as committed.
    #[test]
    fn a_failed_listener_puts_the_catalogue_back() {
        let n = node("restore");
        let r = request(&n, "commit", "20260928T1830Z", cat_patch(), lis_patch());
        let err = commit(&n.l, &n.lib, &r, "20260928T1830Z", &|_, _| Err("disk full".into())).err().unwrap();
        assert!(err.contains("disk full") && err.contains("put back"), "{err}");
        assert_eq!(format_of(&n.lib), "flac", "the catalogue is as it was");
        assert_eq!(last_run(&n.l), None, "and the run can be tried again");
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
            keep_inverse(&l, run, None, 0, Some(&json!({"tables": []}))).unwrap();
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
        mesh_sync::undo_parts(&n.lib, &mut back).unwrap();
        assert_eq!((format_of(&n.lib).as_str(), files_in(&n.lib)), ("flac", 1), "the undo is whole");
        let replay = handle(&n.l, &n.lib, "commit", &with_parts(&n, "commit", run, &texts)).err().unwrap();
        assert!(replay.contains("not newer"), "a replayed commit is refused: {replay}");
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
