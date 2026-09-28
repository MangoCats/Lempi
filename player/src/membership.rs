//! A player joining a mesh, and being a member of one `[SPEC-MTR-130]`,
//! `[SPEC050]`.
//!
//! An appliance has no screen of its own and no HTTPS client, so the hub
//! *invites* it over its ordinary web port, and a person compares the code on
//! the hub's console with the one in the player's Settings. Plain HTTP is safe
//! for this only because of what the code covers -- Bluetooth's numeric
//! comparison runs over an unauthenticated radio for the same reason:
//! - each side derives the code from both keys *as it received them*, and the
//!   hub commits to its nonce before it sees the player's, so a device in the
//!   middle, which must substitute keys of its own, makes the two codes differ;
//! - the invitation carries the mesh's key signed by the hub's own key, which
//!   the code covers, so the mesh key -- and every roster it signs after -- is
//!   authenticated by the same comparison.
//!
//! Membership is kept as `mesh-member.json` beside the listener database: the
//! signed roster, and the mesh and hub fingerprints pinned at joining.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use p256::ecdsa::signature::{Signer, Verifier};
use p256::ecdsa::{DerSignature, Signature, SigningKey, VerifyingKey};
use p256::pkcs8::{DecodePrivateKey, DecodePublicKey, EncodePublicKey, LineEnding};
use serde_json::{json, Value};
use sha2::Digest;

/// The invitation this player is answering, if any: one at a time.
static INVITE: Mutex<Option<Invite>> = Mutex::new(None);

#[derive(Debug, Clone)]
struct Invite {
    id: String,
    mesh: String,
    mesh_fp: String,
    hub_fp: String,
    hub_address: String,
    commit: String,
    nonce: String,
    code: Option<String>,
    /// `invited` -> `comparing` -> `confirmed` -> `joined`, or `rejected`.
    state: &'static str,
    confirmation: Option<String>,
    /// The pairing window this invitation began in (`crate::pairing::window_id`),
    /// or 0 on a host that needs none. An unconfirmed invitation does not
    /// outlive its window `[SPEC-NSH-140]`.
    window: u64,
}

fn state_dir(listener: &Path) -> PathBuf {
    listener.parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from("."))
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

fn sha256_hex(b: &[u8]) -> String {
    hex(&sha2::Sha256::digest(b))
}

fn random_hex(n: usize) -> String {
    use rand_core::RngCore;
    let mut b = vec![0u8; n];
    rand_core::OsRng.fill_bytes(&mut b);
    hex(&b)
}

fn signing_key(listener: &Path) -> Result<SigningKey, String> {
    let me = crate::discovery::identity(listener)?;
    let pem = std::fs::read_to_string(&me.key).map_err(|e| e.to_string())?;
    SigningKey::from_pkcs8_pem(&pem).map_err(|e| e.to_string())
}

/// A signature by this node's key, base64 DER, as the hub's Python verifies it.
fn sign(listener: &Path, text: &str) -> Result<String, String> {
    let sig: DerSignature = signing_key(listener)?.sign(text.as_bytes());
    Ok(b64(sig.as_bytes()))
}

fn verify(key: &VerifyingKey, text: &str, sig_b64: &str) -> bool {
    let Ok(der) = unb64(sig_b64) else { return false };
    Signature::from_der(&der).is_ok_and(|s| key.verify(text.as_bytes(), &s).is_ok())
}

fn b64(b: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.encode(b)
}

fn unb64(s: &str) -> Result<Vec<u8>, String> {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.decode(s).map_err(|e| e.to_string())
}

/// This node's certificate, made beside its key if it has none: bare,
/// self-signed, the key and nothing else -- as a phone's Keystore makes one.
/// A member is trusted for its key being in the roster, never for what a
/// certificate says.
pub fn certificate(listener: &Path) -> Result<String, String> {
    let me = crate::discovery::identity(listener)?;
    let path = me.key.with_file_name("node.pem");
    if let Ok(pem) = std::fs::read_to_string(&path) {
        return Ok(pem);
    }
    use std::str::FromStr;
    use x509_cert::builder::{Builder, CertificateBuilder, Profile};
    use x509_cert::der::EncodePem;
    let key = signing_key(listener)?;
    let spki = x509_cert::spki::SubjectPublicKeyInfoOwned::from_key(*key.verifying_key()).map_err(|e| e.to_string())?;
    let serial = x509_cert::serial_number::SerialNumber::from(u32::from_str_radix(&random_hex(3), 16).unwrap_or(1));
    let validity = x509_cert::time::Validity::from_now(std::time::Duration::from_secs(100 * 365 * 86_400))
        .map_err(|e| e.to_string())?;
    let subject = x509_cert::name::Name::from_str("CN=lempi-node").map_err(|e| e.to_string())?;
    let builder = CertificateBuilder::new(Profile::Manual { issuer: None }, serial, validity, subject, spki, &key)
        .map_err(|e| e.to_string())?;
    let cert = builder.build::<DerSignature>().map_err(|e| e.to_string())?;
    let pem = cert.to_pem(LineEnding::LF).map_err(|e| e.to_string())?;
    std::fs::write(&path, &pem).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(pem)
}

/// The fingerprint and key of a certificate given as PEM.
fn cert_key(pem: &str) -> Result<(String, VerifyingKey), String> {
    use x509_cert::der::{DecodePem, Encode};
    let cert = x509_cert::Certificate::from_pem(pem).map_err(|e| format!("not a certificate: {e}"))?;
    let spki = cert.tbs_certificate.subject_public_key_info.to_der().map_err(|e| e.to_string())?;
    let key = VerifyingKey::from_public_key_der(&spki).map_err(|e| format!("not a P-256 key: {e}"))?;
    Ok((sha256_hex(&spki), key))
}

/// The six digits both sides show: the same derivation as `mesh.code()`.
pub fn code(hub_fp: &str, candidate_fp: &str, nonce_h: &str, nonce_c: &str) -> String {
    let d = sha2::Sha256::digest(format!("lempi-sas|{hub_fp}|{candidate_fp}|{nonce_h}|{nonce_c}").as_bytes());
    let n = u64::from_be_bytes(d[..8].try_into().unwrap_or([0; 8])) % 1_000_000;
    format!("{:03} {:03}", n / 1000, n % 1000)
}

/// The membership this player holds, if it has joined a mesh.
pub fn membership(listener: &Path) -> Option<Value> {
    let v: Value = serde_json::from_str(&std::fs::read_to_string(state_dir(listener).join("mesh-member.json")).ok()?).ok()?;
    let roster: Value = serde_json::from_str(v["roster"]["roster"].as_str()?).ok()?;
    Some(json!({"hub_fp": v["hub_fp"], "mesh_fp": v["mesh_fp"], "hub_address": v["hub_address"], "roster": roster}))
}

/// Step one, from the hub: an invitation. Answered with this player's
/// certificate and nonce, the nonce signed to prove the key is held here.
pub fn invite(listener: &Path, body: &Value) -> Result<Value, String> {
    if membership(listener).is_some() {
        return Err("already a member of a mesh: leave it first".into());
    }
    // Refuse a new invitation while one is mid-comparison `[SecurityReview3 R3]`:
    // the single `INVITE` slot would otherwise be silently overwritten, so a
    // second invite could replace the one a person is part-way through
    // confirming. A stale one that never completed is cleared with reject.
    if let Ok(g) = INVITE.lock() {
        if let Some(existing) = g.as_ref() {
            if existing.state != "rejected" {
                return Err("an invitation is already in progress: confirm or reject it first".into());
            }
        }
    }
    let s = |k: &str| body[k].as_str().map(str::to_string).ok_or(format!("the invitation has no {k}"));
    let hub_cert = s("hub_certificate")?;
    let (hub_fp, hub_key) = cert_key(&hub_cert)?;
    let mesh_pub = p256::PublicKey::from_public_key_pem(&s("mesh_public_key")?).map_err(|e| e.to_string())?;
    let mesh_fp = sha256_hex(mesh_pub.to_public_key_der().map_err(|e| e.to_string())?.as_bytes());
    let commit = s("commit")?;
    // The mesh key, vouched for by the hub's key -- which the code covers.
    if !verify(&hub_key, &format!("lempi-invite|{mesh_fp}|{commit}"), &s("signature")?) {
        return Err("the invitation is not signed by the hub key it carries".into());
    }
    let inv = Invite {
        id: random_hex(16), mesh: s("mesh")?, mesh_fp, hub_fp, hub_address: s("hub_address").unwrap_or_default(),
        commit, nonce: random_hex(16), code: None, state: "invited", confirmation: None, window: 0,
    };
    let answer = json!({
        "invite": inv.id, "certificate": certificate(listener)?, "nonce": inv.nonce,
        "signature": sign(listener, &format!("lempi-enrol|{}|{}|{}", inv.id, inv.commit, inv.nonce))?,
        "name": crate::discovery::host_name(),
    });
    tracing::info!("mesh: invited to join '{}' by hub {}", inv.mesh, &inv.hub_fp[..8]);
    *INVITE.lock().map_err(|_| "lock")? = Some(inv);
    Ok(answer)
}

fn with_invite<T>(id: &str, f: impl FnOnce(&mut Invite) -> Result<T, String>) -> Result<T, String> {
    let mut g = INVITE.lock().map_err(|_| "lock")?;
    match g.as_mut() {
        Some(inv) if inv.id == id => f(inv),
        _ => Err("no such invitation".into()),
    }
}

/// Step two, from the hub: its nonce, checked against its commitment. Both
/// sides can now show the code.
pub fn reveal(listener: &Path, id: &str, nonce_h: &str) -> Result<Value, String> {
    let me = crate::discovery::identity(listener)?;
    with_invite(id, |inv| {
        if inv.state != "invited" {
            return Err(format!("the invitation is {}", inv.state));
        }
        if sha256_hex(nonce_h.as_bytes()) != inv.commit {
            inv.state = "rejected";
            return Err("the hub's nonce does not match what it committed to".into());
        }
        inv.code = Some(code(&inv.hub_fp, &me.fingerprint, nonce_h, &inv.nonce));
        inv.state = "comparing";
        Ok(json!({"state": inv.state}))
    })
}

/// A person here says the codes match: signed, for the hub to verify.
pub fn confirm(listener: &Path, id: &str, code_in: &str) -> Result<Value, String> {
    let (code, state) = with_invite(id, |inv| Ok((inv.code.clone(), inv.state)))?;
    if state != "comparing" {
        return Err(format!("the invitation is {state}"));
    }
    // The confirming request must carry the code this player itself shows
    // `[SecurityReview2 R3]`. The player's own Settings page has it from
    // `status`, so a person still just clicks "the codes match" -- but a blind
    // POST from another LAN host, which never saw the code, cannot complete an
    // enrolment on the human's behalf. This does not defend against an attacker
    // who ran the whole invitation (they can derive the code); it stops the
    // unattended automated case, which is what the LAN UI leaves open.
    let code = code.unwrap_or_default();
    if code.is_empty() || code_in != code {
        return Err("the confirmation code does not match".into());
    }
    let sig = sign(listener, &format!("lempi-confirm|{id}|{code}"))?;
    with_invite(id, |inv| {
        inv.state = "confirmed";
        inv.confirmation = Some(sig);
        Ok(json!({"state": inv.state}))
    })
}

pub fn reject(id: &str) -> Result<Value, String> {
    with_invite(id, |inv| {
        inv.state = "rejected";
        Ok(json!({"state": inv.state}))
    })
}

/// Record which pairing window an invitation began in `[SPEC-NSH-140]`.
pub fn stamp_invite_window(id: &str, window: u64) {
    let _ = with_invite(id, |inv| {
        inv.window = window;
        Ok(())
    });
}

/// Drop an invitation not yet confirmed whose pairing window has gone
/// `[SPEC-NSH-140]`: closed, or replaced by a later one. A confirmed invitation
/// is kept -- it waits for the hub's roster, which is authenticated by the mesh
/// key and needs no window. Called only on a host that uses the window.
pub fn drop_stale_invite(current_window: u64) {
    if let Ok(mut g) = INVITE.lock() {
        let stale = g.as_ref().is_some_and(|inv| {
            inv.state != "confirmed" && (current_window == 0 || inv.window != current_window)
        });
        if stale {
            *g = None;
        }
    }
}

/// Step three, from the hub: the signed roster, once both people confirmed.
/// Kept only if the mesh key pinned at the invitation signed it, and it names
/// this player.
pub fn take_roster(listener: &Path, id: &str, signed: &Value) -> Result<Value, String> {
    let me = crate::discovery::identity(listener)?;
    let inv = with_invite(id, |inv| Ok(inv.clone()))?;
    if inv.state != "confirmed" {
        return Err(format!("the invitation is {}", inv.state));
    }
    let body = signed["roster"].as_str().ok_or("no roster")?;
    let r: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
    let mesh_pub = p256::PublicKey::from_public_key_pem(r["mesh"]["public_key"].as_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let fp = sha256_hex(mesh_pub.to_public_key_der().map_err(|e| e.to_string())?.as_bytes());
    let key = VerifyingKey::from(&mesh_pub);
    if fp != inv.mesh_fp || !verify(&key, body, signed["signature"].as_str().unwrap_or("")) {
        return Err("the roster is not signed by the mesh this player was invited to".into());
    }
    let named = r["members"].as_array().is_some_and(|m| m.iter().any(|m| m["fingerprint"] == me.fingerprint));
    if !named {
        return Err("the roster does not name this player".into());
    }
    let keep = json!({"roster": signed, "mesh_fp": inv.mesh_fp, "hub_fp": inv.hub_fp, "hub_address": inv.hub_address});
    std::fs::write(state_dir(listener).join("mesh-member.json"), keep.to_string()).map_err(|e| e.to_string())?;
    with_invite(id, |i| {
        i.state = "joined";
        Ok(())
    })?;
    tracing::info!("mesh: joined '{}', roster version {}", inv.mesh, r["version"]);
    Ok(json!({"state": "joined", "version": r["version"]}))
}

/// A newer roster, pushed by the hub after a member joined, left, or was
/// renamed `[SPEC-MTR-120]`: kept only if the mesh key pinned at joining
/// signed it and it is newer than the one held. One that no longer names this
/// player means it was removed, and it leaves `[SPEC-MTR-140]`.
pub fn update_roster(listener: &Path, signed: &Value) -> Result<Value, String> {
    let me = crate::discovery::identity(listener)?;
    let held = membership(listener).ok_or("not a member of any mesh")?;
    let body = signed["roster"].as_str().ok_or("no roster")?;
    let r: Value = serde_json::from_str(body).map_err(|e| e.to_string())?;
    let mesh_pub = p256::PublicKey::from_public_key_pem(r["mesh"]["public_key"].as_str().unwrap_or(""))
        .map_err(|e| e.to_string())?;
    let fp = sha256_hex(mesh_pub.to_public_key_der().map_err(|e| e.to_string())?.as_bytes());
    if held["mesh_fp"].as_str() != Some(fp.as_str())
        || !verify(&VerifyingKey::from(&mesh_pub), body, signed["signature"].as_str().unwrap_or(""))
    {
        return Err("the roster is not signed by this player's mesh".into());
    }
    let (new, old) = (r["version"].as_i64().unwrap_or(0), held["roster"]["version"].as_i64().unwrap_or(0));
    if new <= old {
        return Err(format!("roster version {new} is not newer than the {old} held"));
    }
    if !r["members"].as_array().is_some_and(|m| m.iter().any(|m| m["fingerprint"] == me.fingerprint)) {
        forget_membership(listener)?;
        tracing::info!("mesh: removed from '{}' at roster version {new}; left", r["mesh"]["name"]);
        return Ok(json!({"state": "removed", "version": new}));
    }
    let keep = json!({"roster": signed, "mesh_fp": held["mesh_fp"], "hub_fp": held["hub_fp"],
                      "hub_address": held["hub_address"]});
    std::fs::write(state_dir(listener).join("mesh-member.json"), keep.to_string()).map_err(|e| e.to_string())?;
    Ok(json!({"state": "updated", "version": new}))
}

/// This player leaves its mesh here; the hub removes it from the roster there.
/// Delete the stored membership. The unguarded removal, for the two callers
/// that have already earned it: `leave` after its fingerprint check, and the
/// roster update that finds this player is no longer named (a change already
/// verified as signed by the pinned mesh key).
fn forget_membership(listener: &Path) -> Result<(), String> {
    let p = state_dir(listener).join("mesh-member.json");
    if p.exists() {
        std::fs::remove_file(&p).map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn leave(listener: &Path, mesh_fp_in: &str) -> Result<(), String> {
    // A leave requested through the LAN UI must name the mesh this player is
    // actually in `[SecurityReview2 R3]`. Its own page has the fingerprint
    // from `status`, so leaving stays one click; a blind POST that never read
    // the membership cannot detach the player from its mesh.
    let held = membership(listener).ok_or("this player is in no mesh")?;
    if held["mesh_fp"].as_str() != Some(mesh_fp_in) {
        return Err("that is not this player's mesh".into());
    }
    forget_membership(listener)
}

/// What the Settings page and the hub read: membership, and an invitation in
/// progress with its code.
pub fn status(listener: &Path) -> Value {
    let inv = INVITE.lock().ok().and_then(|g| g.clone());
    json!({
        "member": membership(listener).map(|m| json!({
            "mesh": m["roster"]["mesh"]["name"], "mesh_fp": m["mesh_fp"], "hub_fp": m["hub_fp"],
            "hub_address": m["hub_address"], "version": m["roster"]["version"],
            "members": m["roster"]["members"].as_array().map(|ms| ms.iter().map(|x| json!({
                "name": x["name"], "role": x["role"], "fingerprint": x["fingerprint"]})).collect::<Vec<_>>()),
        })),
        "invite": inv.map(|i| json!({
            "id": i.id, "mesh": i.mesh, "mesh_fp": i.mesh_fp, "hub_fp": i.hub_fp, "state": i.state,
            "code": i.code, "confirmation": i.confirmation,
        })),
    })
}

/// The discovery key of the mesh this player belongs to, for proving
/// membership to its own hub and to no one else `[SPEC-MTR-310]`.
pub fn discovery_key(listener: &Path) -> Option<(String, Vec<u8>)> {
    let m = membership(listener)?;
    let k = unb64(m["roster"]["discovery_key"].as_str()?).ok()?;
    Some((m["mesh_fp"].as_str()?.to_string(), k))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_code_is_the_hubs_code() {
        // mesh.py's `code()` gave these, 2026-09-28: the two sides must agree
        // to the digit, or every comparison fails.
        assert_eq!(code("aa", "bb", "cc", "dd"), "564 444");
        assert_eq!(code("ddc60c85", "3398fb55", "n1", "n2"), "347 511");
    }

    #[test]
    fn leaving_requires_this_players_own_mesh_fingerprint() {
        // [SecurityReview2 R3]: a blind POST /mesh/leave from another LAN host
        // cannot detach the player; the fingerprint the player's own page shows
        // is required, and a wrong or absent one is refused.
        let d = std::env::temp_dir().join(format!("lempi-leave-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let l = d.join("listener.db");
        let member = json!({
            "roster": {"roster": "{\"members\":[],\"mesh\":{\"name\":\"Home\"}}", "signature": "x"},
            "mesh_fp": "abcd1234", "hub_fp": "ffff", "hub_address": "192.0.2.10"});
        std::fs::write(state_dir(&l).join("mesh-member.json"), member.to_string()).unwrap();
        assert!(leave(&l, "").is_err(), "an empty fingerprint is refused");
        assert!(leave(&l, "wrongfp").is_err(), "a wrong fingerprint is refused");
        assert!(state_dir(&l).join("mesh-member.json").exists(), "the membership is untouched");
        assert!(leave(&l, "abcd1234").is_ok(), "the held fingerprint leaves");
        assert!(!state_dir(&l).join("mesh-member.json").exists(), "and the membership is gone");
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn confirming_requires_the_code_the_player_shows() {
        // [SecurityReview2 R3]: confirm needs the code this player displays, so
        // a blind confirm from elsewhere on the LAN cannot complete enrolment.
        let d = std::env::temp_dir().join(format!("lempi-confirm-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let l = d.join("listener.db");
        *INVITE.lock().unwrap() = Some(Invite {
            id: "inv1".into(), mesh: "Home".into(), mesh_fp: "m".into(), hub_fp: "h".into(),
            hub_address: String::new(), commit: String::new(), nonce: "n".into(),
            code: Some("123 456".into()), state: "comparing", confirmation: None, window: 0});
        assert!(confirm(&l, "inv1", "000 000").is_err(), "a wrong code is refused");
        assert!(confirm(&l, "inv1", "").is_err(), "an empty code is refused");
        let ok = confirm(&l, "inv1", "123 456").unwrap();
        assert_eq!(ok["state"], "confirmed", "the shown code confirms");
        // [SecurityReview3 R3, option 4]: a second invitation is refused while
        // one is in progress -- the single INVITE slot is not silently replaced.
        // (Checked here, where INVITE is already held, to stay off the global
        // that parallel tests would race on.)
        let err = invite(&l, &json!({})).unwrap_err();
        assert!(err.contains("already in progress"), "a second invite is refused: {err}");
        // [SPEC-NSH-140]: a confirmed invitation outlives its window -- it waits
        // for the hub's roster, which needs no window.
        drop_stale_invite(0);
        assert!(INVITE.lock().unwrap().is_some(), "a confirmed invitation is kept");
        // An unconfirmed one lives exactly as long as the window it began in.
        let unconfirmed = |window| Invite {
            id: "inv2".into(), mesh: "Home".into(), mesh_fp: "m".into(), hub_fp: "h".into(),
            hub_address: String::new(), commit: String::new(), nonce: "n".into(),
            code: None, state: "comparing", confirmation: None, window};
        *INVITE.lock().unwrap() = Some(unconfirmed(0));
        stamp_invite_window("inv2", 777);
        drop_stale_invite(777);
        assert_eq!(INVITE.lock().unwrap().as_ref().map(|i| i.window), Some(777), "kept in its own window");
        drop_stale_invite(888);
        assert!(INVITE.lock().unwrap().is_none(), "dropped when a later window replaces it");
        *INVITE.lock().unwrap() = Some(unconfirmed(777));
        drop_stale_invite(0);
        assert!(INVITE.lock().unwrap().is_none(), "dropped when the window closes");
        *INVITE.lock().unwrap() = None;
        let _ = std::fs::remove_dir_all(&d);
    }

    #[test]
    fn a_player_makes_one_certificate_for_its_own_key() {
        let d = std::env::temp_dir().join(format!("lempi-membership-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        let l = d.join("listener.db");
        let pem = certificate(&l).unwrap();
        let (fp, _) = cert_key(&pem).unwrap();
        assert_eq!(fp, crate::discovery::identity(&l).unwrap().fingerprint, "the certificate is for this node's key");
        assert_eq!(certificate(&l).unwrap(), pem, "made once");
    }
}
