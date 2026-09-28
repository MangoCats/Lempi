//! Being found on the network `[SPEC050]`, `[SPEC-MTR-300..330]`.
//!
//! A node's identity, and its answer to the queries on UDP 13492.
//!
//! **One responder per machine, and it is the player.** The hub's machine
//! also runs Vipunen's intake, and two processes cannot share a UDP port
//! reliably on Windows. So the player that runs beside a hub answers for it
//! too, reading the hub's roster from `mesh/` beside its own listener
//! database and checking that the members' port is open before it says so.
//!
//! **Identity** `[SPEC-MTR-100]`: an ECDSA P-256 key, PKCS#8 PEM -- the
//! format the hub's own Python writes -- made at first start beside the
//! listener database and never moved. On the hub it is the hub's own
//! `mesh/node.key`, so the desktop is one node, not two. On an appliance it
//! lands in `/var/lempi`, which is not the overlay root on any of the three
//! `[IMPL-BOS-185]`.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::time::Duration;

use p256::pkcs8::{DecodePrivateKey, EncodePrivateKey, EncodePublicKey, LineEnding};
use sha2::Digest;

/// The discovery port `[GDE-NDS-010]`.
pub const PORT: u16 = 13492;

/// Set once this player is answering on [`PORT`]: the Settings row shows only
/// where that is so -- a phone's player never does.
pub static ANSWERING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
/// The members' port the hub's intake serves `[SPEC-MTR-200]`.
const MEMBER_PORT: u16 = 5732;

/// This node's identity: where its key is, and the key's fingerprint.
#[derive(Debug, Clone)]
pub struct Identity {
    pub key: PathBuf,
    /// SHA-256 of the public key's DER (SubjectPublicKeyInfo), lower-case hex:
    /// the same bytes the hub and the phone fingerprint.
    pub fingerprint: String,
}

/// Where this node keeps its state: beside its listener database.
fn state_dir(listener: &Path) -> PathBuf {
    listener.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from("."))
}

/// The node's key: the hub's, where this is the hub; else its own, made now
/// if it has none.
pub fn identity(listener: &Path) -> Result<Identity, String> {
    let dir = state_dir(listener);
    let hub = dir.join("mesh").join("node.key");
    let key = if hub.is_file() { hub } else { dir.join("node.key") };
    let secret = if key.is_file() {
        let pem = std::fs::read_to_string(&key).map_err(|e| format!("{}: {e}", key.display()))?;
        p256::SecretKey::from_pkcs8_pem(&pem).map_err(|e| format!("{}: {e}", key.display()))?
    } else {
        let s = p256::SecretKey::random(&mut rand_core::OsRng);
        let pem = s.to_pkcs8_pem(LineEnding::LF).map_err(|e| e.to_string())?;
        write_private(&key, pem.as_bytes())?;
        tracing::info!("node identity made: {}", key.display());
        s
    };
    let spki = secret.public_key().to_public_key_der().map_err(|e| e.to_string())?;
    let fingerprint = sha2::Sha256::digest(spki.as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
    Ok(Identity { key, fingerprint })
}

fn write_private(path: &Path, data: &[u8]) -> Result<(), String> {
    use std::io::Write;
    let mut o = std::fs::OpenOptions::new();
    o.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        o.mode(0o600);
    }
    let mut f = o.open(path).map_err(|e| format!("{}: {e}", path.display()))?;
    f.write_all(data).map_err(|e| e.to_string())
}

/// The machine's own name, as the network is likely to know it.
pub fn host_name() -> String {
    std::fs::read_to_string("/proc/sys/kernel/hostname")
        .ok()
        .or_else(|| std::env::var("COMPUTERNAME").ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "unknown".into())
}

/// The hub this machine is, if it is one and its members' port answers.
fn hub_here(listener: &Path) -> Option<serde_json::Value> {
    let roster = state_dir(listener).join("mesh").join("roster.json");
    let signed: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(roster).ok()?).ok()?;
    let r: serde_json::Value = serde_json::from_str(signed["roster"].as_str()?).ok()?;
    let open = std::net::TcpStream::connect_timeout(
        &SocketAddr::from(([127, 0, 0, 1], MEMBER_PORT)),
        Duration::from_millis(300),
    )
    .is_ok();
    open.then(|| {
        serde_json::json!({
            "mesh": r["mesh"]["name"], "mesh_fingerprint": r["mesh"]["fingerprint"],
            "members_port": MEMBER_PORT,
        })
    })
}

/// Whether this node answers at all: the switch in its Settings, on unless
/// someone has turned it off `[SPEC-MTR-020]`.
fn announcing(listener: &Path, library: &Path) -> bool {
    crate::db::PlayerStore::open_split(listener, library).map(|s| s.load_announce()).unwrap_or(true)
}

/// The answer to one query, or `None` to stay silent `[SPEC050]`.
pub fn answer(q: &serde_json::Value, me: &Identity, listener: &Path, library: &Path, web_port: u16)
    -> Option<serde_json::Value> {
    if q["lempi"] != 1 {
        return None;
    }
    let nonce = q["nonce"].as_str().filter(|n| n.len() <= 64)?;
    let kind = q["q"].as_str()?;
    // A member answers its own mesh, and only one that proves it knows the
    // discovery key its roster carries `[SPEC-MTR-310]`. Everyone else, other
    // meshes included, learns nothing -- not even that a member is here.
    if let Some((mesh_fp, key)) = crate::membership::discovery_key(listener) {
        if kind != "members" || q["mesh"].as_str() != Some(mesh_fp.as_str()) {
            return None;
        }
        let proof = hex_decode(q["proof"].as_str()?)?;
        if !mac_ok(&key, &format!("lempi-members|{mesh_fp}|{nonce}"), &proof) {
            return None;
        }
        return Some(serde_json::json!({
            "lempi": 1, "a": "member", "nonce": nonce, "name": host_name(), "fingerprint": me.fingerprint,
            "web_port": web_port, "version": crate::GIT,
            "proof": mac_hex(&key, &format!("lempi-member|{nonce}|{}", me.fingerprint)),
        }));
    }
    if !matches!(kind, "candidates" | "hubs") || !announcing(listener, library) {
        return None;
    }
    // Being a hub is having the roster; *answering* as one also needs the
    // members' port open. Until 2026-09-28 both hung on the port, so a hub
    // whose intake was stopped answered as an ordinary candidate.
    let is_hub = state_dir(listener).join("mesh").join("roster.json").is_file();
    let hub = if kind == "hubs" && is_hub { hub_here(listener) } else { None };
    if kind == "candidates" && is_hub {
        return None;
    }
    let mut a = serde_json::json!({
        "lempi": 1, "nonce": nonce, "name": host_name(), "fingerprint": me.fingerprint,
        "web_port": web_port, "version": crate::GIT,
    });
    match (kind, hub) {
        // A hub is already its mesh's; it is no one's candidate.
        ("candidates", None) => a["a"] = "candidate".into(),
        ("hubs", Some(h)) => {
            a["a"] = "hub".into();
            for (k, v) in h.as_object()? {
                a[k] = v.clone();
            }
        }
        _ => return None,
    }
    Some(a)
}

type Mac = hmac::Hmac<sha2::Sha256>;

fn mac_hex(key: &[u8], text: &str) -> String {
    use hmac::Mac as _;
    let mut m = <Mac as hmac::Mac>::new_from_slice(key).expect("any key length");
    m.update(text.as_bytes());
    m.finalize().into_bytes().iter().map(|b| format!("{b:02x}")).collect()
}

/// In constant time, as a proof should be checked.
fn mac_ok(key: &[u8], text: &str, proof: &[u8]) -> bool {
    use hmac::Mac as _;
    let mut m = <Mac as hmac::Mac>::new_from_slice(key).expect("any key length");
    m.update(text.as_bytes());
    m.verify_slice(proof).is_ok()
}

fn hex_decode(s: &str) -> Option<Vec<u8>> {
    (s.len().is_multiple_of(2) && s.len() <= 128)
        .then(|| (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok()).collect())
        .flatten()
}

/// Answer queries until the runtime ends. A port already taken is one line
/// in the log, never fatal: discovery offers, it is not needed to play.
pub async fn serve(me: Identity, listener: PathBuf, library: PathBuf, web_port: u16) {
    let sock = match tokio::net::UdpSocket::bind(SocketAddr::from(([0, 0, 0, 0], PORT))).await {
        Ok(s) => s,
        Err(e) => {
            tracing::warn!("discovery: not answering on UDP {PORT}: {e}");
            return;
        }
    };
    tracing::info!("discovery: answering on UDP {PORT} as {}", &me.fingerprint[..8]);
    ANSWERING.store(true, std::sync::atomic::Ordering::Relaxed);
    let mut buf = [0u8; 2048];
    loop {
        let Ok((n, from)) = sock.recv_from(&mut buf).await else { continue };
        let Ok(q) = serde_json::from_slice::<serde_json::Value>(&buf[..n]) else { continue };
        let (me2, l, lib) = (me.clone(), listener.clone(), library.clone());
        let a = tokio::task::spawn_blocking(move || answer(&q, &me2, &l, &lib, web_port)).await.ok().flatten();
        if let Some(a) = a {
            let _ = sock.send_to(a.to_string().as_bytes(), from).await;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("lempi-discovery-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    /// Made once, and the same thereafter; the hub's own key where there is one.
    #[test]
    fn a_node_keeps_one_identity() {
        let d = dir("id");
        let l = d.join("listener.db");
        let a = identity(&l).unwrap();
        assert!(a.key.ends_with("node.key") && a.fingerprint.len() == 64);
        assert_eq!(identity(&l).unwrap().fingerprint, a.fingerprint, "the same key, read back");
        std::fs::create_dir_all(d.join("mesh")).unwrap();
        std::fs::rename(&a.key, d.join("mesh").join("node.key")).unwrap();
        let hub = identity(&l).unwrap();
        assert!(hub.key.starts_with(d.join("mesh")) && hub.fingerprint == a.fingerprint, "the hub's key is used");
    }

    /// A candidate answers for candidates, with its nonce; nothing else.
    #[test]
    fn only_a_well_formed_query_is_answered() {
        let d = dir("answer");
        let (l, lib) = (d.join("listener.db"), d.join("library.db"));
        let me = identity(&l).unwrap();
        let q = |v: serde_json::Value| answer(&v, &me, &l, &lib, 5720);
        let a = q(serde_json::json!({"lempi": 1, "q": "candidates", "nonce": "n1"})).expect("a candidate answers");
        assert_eq!((a["a"].as_str(), a["nonce"].as_str()), (Some("candidate"), Some("n1")));
        assert_eq!(a["fingerprint"].as_str(), Some(me.fingerprint.as_str()));
        assert!(q(serde_json::json!({"lempi": 1, "q": "hubs", "nonce": "n2"})).is_none(), "not a hub");
        assert!(q(serde_json::json!({"q": "candidates", "nonce": "n3"})).is_none(), "not a Lempi query");
        assert!(q(serde_json::json!({"lempi": 1, "q": "candidates"})).is_none(), "no nonce, no answer");
        assert!(q(serde_json::json!({"lempi": 1, "q": "everything", "nonce": "n4"})).is_none());
    }

    /// A hub is no one's candidate, even while its members' port is closed --
    /// found 2026-09-28, the desktop listed as a candidate with its intake stopped.
    #[test]
    fn a_hub_is_never_a_candidate() {
        let d = dir("hub");
        let (l, lib) = (d.join("listener.db"), d.join("library.db"));
        let me = identity(&l).unwrap();
        std::fs::create_dir_all(d.join("mesh")).unwrap();
        std::fs::write(d.join("mesh").join("roster.json"),
            r#"{"roster": "{\"mesh\": {\"name\": \"Home\", \"fingerprint\": \"ff\"}}", "signature": ""}"#).unwrap();
        let q = |v: serde_json::Value| answer(&v, &me, &l, &lib, 5720);
        assert!(q(serde_json::json!({"lempi": 1, "q": "candidates", "nonce": "n"})).is_none(), "not a candidate");
    }
}
