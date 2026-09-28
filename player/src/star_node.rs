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

/// What a handled step returns: the signed answer, and whether the player
/// should rebuild what it loads once at start (the catalogue or the
/// household's preferences changed).
pub struct Handled {
    pub answer: Value,
    pub reload: bool,
}

pub fn handle(listener: &Path, library: &Path, op: &str, body: &Value) -> Result<Handled, String> {
    if !matches!(op, "snapshot" | "rehearse" | "commit") {
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

fn snapshot(listener: &Path, library: &Path) -> Result<Value, String> {
    Ok(json!({
        "listener": mesh_sync::export(listener, &mesh_sync::MERGED)?,
        "catalogue": mesh_sync::catalogue_summary(library)?,
        "last_run": last_run(listener),
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
    if has_rows(cat) {
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

fn commit(listener: &Path, library: &Path, req: &Value, run: &str, apply_listener: ListenerApply)
    -> Result<(Value, bool), String> {
    let (cat, lis) = (&req["catalogue_patch"], &req["listener_patch"]);
    // Rehearsed first, here, whatever the hub rehearsed before: the files may
    // have moved since.
    rehearse(listener, library, req)?;
    if !has_rows(cat) && !has_rows(lis) {
        set_last_run(listener, run)?;
        return Ok((json!({"catalogue": null, "listener": null}), false));
    }
    // The root verb only when the file cannot be written where it is -- a
    // read-only partition, as on bose. A catalogue already writable (on the
    // root filesystem, or anywhere a test puts one) never reaches it.
    let opened = if has_rows(cat) && !writable(library) { library_rw(true)? } else { false };
    let applied = (|| -> Result<(Option<mesh_sync::Outcome>, Option<mesh_sync::Outcome>), String> {
        let c = if has_rows(cat) {
            Some(mesh_sync::apply_with(library, cat, None, Rule::Strict, true).map_err(|e| format!("catalogue: {e}"))?)
        } else {
            None
        };
        let l = if has_rows(lis) {
            match apply_listener(listener, lis) {
                Ok(l) => Some(l),
                Err(e) => {
                    let put_back = c.as_ref().map(|c| mesh_sync::undo(library, &c.inverse));
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
    if opened {
        if let Err(e) = library_rw(false) {
            tracing::warn!("star sync: the catalogue's partition could not be returned to read-only: {e}");
        }
    }
    let (c, l) = applied?;
    keep_inverse(listener, run, c.as_ref().map(|o| &o.inverse), l.as_ref().map(|o| &o.inverse))?;
    set_last_run(listener, run)?;
    let result = json!({"catalogue": c.as_ref().map(|o| counts(&o.counts)),
                        "listener": l.as_ref().map(|o| counts(&o.counts))});
    tracing::info!("star sync: run {run} committed here: {result}");
    Ok((result, true))
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

/// What a commit wrote, reversed, as `inverse-<run>.json`; the newest `KEEP`
/// are kept. `mesh_sync::undo` with either half puts that half back.
fn keep_inverse(listener: &Path, run: &str, cat: Option<&Value>, lis: Option<&Value>) -> Result<(), String> {
    let d = sync_dir(listener);
    std::fs::create_dir_all(&d).map_err(|e| e.to_string())?;
    std::fs::write(d.join(format!("inverse-{run}.json")), json!({"run": run, "catalogue": cat, "listener": lis}).to_string())
        .map_err(|e| e.to_string())?;
    let mut old: Vec<PathBuf> = std::fs::read_dir(&d)
        .map_err(|e| e.to_string())?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .filter(|p| p.file_name().and_then(|n| n.to_str()).is_some_and(|n| n.starts_with("inverse-")))
        .collect();
    old.sort();
    for p in old.iter().take(old.len().saturating_sub(KEEP)) {
        let _ = std::fs::remove_file(p);
    }
    Ok(())
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
            keep_inverse(&l, run, None, Some(&json!({"tables": []}))).unwrap();
            set_last_run(&l, run).unwrap();
        }
        let mut names: Vec<String> = std::fs::read_dir(sync_dir(&l)).unwrap()
            .filter_map(|e| e.ok()?.file_name().into_string().ok()).filter(|n| n.starts_with("inverse-")).collect();
        names.sort();
        assert_eq!(names, ["inverse-20260902T0000Z.json", "inverse-20260903T0000Z.json", "inverse-20260904T0000Z.json"]);
        assert_eq!(last_run(&l).as_deref(), Some("20260904T0000Z"));
        let _ = std::fs::remove_dir_all(&d);
    }
}
