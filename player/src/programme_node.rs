//! The hub's programme, handed to this node signed `[SPEC-PGM-310]`.
//!
//! A phone fetches the programme assigned to it over the members' channel and
//! hands it to its own player (`/programme/arrival`). An appliance has no
//! client for that channel -- no HTTPS stack is built into it -- so the hub
//! hands the programme over the player's own port instead, as it hands over
//! the roster: `POST /mesh/programme`, carrying
//! `{"request": "<json>", "signature": "<mesh key, base64 DER>"}`.
//!
//! The request names its step, this node, the mesh, a nonce and the time it
//! was made, checked as a star sync step is (`star_node::addressed`). What it
//! carries is only held, never applied: the answer is a person's, on this
//! node `[SPEC-PGM-050]`. A request with no programme only asks what this
//! node runs. Either way the answer, signed by this node's key, says what it
//! runs and what waits, which is how the hub's Programmes page knows it
//! `[SPEC-PGM-440]`.

use std::path::Path;

use serde_json::{json, Value};

use crate::db::{Arrival, PlayerStore};

const OP: &str = "programme";

pub fn take(listener: &Path, library: &Path, body: &Value) -> Result<Value, String> {
    let text = body["request"].as_str().ok_or("no request")?;
    let held = crate::membership::mesh_signed(listener, text, body["signature"].as_str().unwrap_or(""))?;
    let req: Value = serde_json::from_str(text).map_err(|e| format!("the request: {e}"))?;
    let me = crate::discovery::identity(listener)?.fingerprint;
    crate::star_node::addressed(&req, OP, &me, held["mesh_fp"].as_str().unwrap_or(""), crate::star_node::now_ms())?;
    let store = PlayerStore::open_split(listener, library).map_err(|e| e.message().to_string())?;
    let result = arrive(&store, &req["programme"])?;
    let answer = json!({"op": OP, "nonce": req["nonce"], "node": me, "result": result}).to_string();
    let signature = crate::membership::sign_as_node(listener, &answer)?;
    Ok(json!({"answer": answer, "signature": signature}))
}

/// Hold what arrived, if anything did, then say where this node stands.
fn arrive(store: &PlayerStore, programme: &Value) -> Result<Value, String> {
    let outcome = if programme.is_null() {
        None
    } else {
        let a: Arrival = serde_json::from_value(programme.clone()).map_err(|e| format!("the programme: {e}"))?;
        let o = store.programme_arrived(&a).map_err(|e| e.message().to_string())?;
        tracing::info!("programme: {} version {} handed over by the hub: {o:?}", a.name, a.version);
        Some(o)
    };
    let current = store.programme().map_err(|e| e.message().to_string())?;
    let waiting = store.programme_pending().map_err(|e| e.message().to_string())?;
    Ok(json!({
        "outcome": outcome,
        "current": {"name": current.name, "source": current.source,
                    "hub_version": current.hub_version, "fingerprint": current.fingerprint},
        "waiting": waiting.map(|w| json!({"name": w.name, "hub_version": w.hub_version,
                                          "fingerprint": w.fingerprint, "kept": w.kept})),
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(tag: &str) -> (std::path::PathBuf, PlayerStore) {
        let dir = std::env::temp_dir().join(format!("lempi-programme-node-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // The catalogue is attached read-only, so it must already be there.
        rusqlite::Connection::open(dir.join("library.db")).unwrap().execute_batch("CREATE TABLE t (a);").unwrap();
        let s = PlayerStore::open_split(&dir.join("listener.db"), &dir.join("library.db")).unwrap();
        (dir, s)
    }

    fn wkmp(version: i64) -> Value {
        json!({"name": "WKMP", "version": version,
               "slots": [{"name": "Soft", "start": "4:00", "seeds": ["a", "b"]},
                         {"name": "Bright", "start": "9:00", "seeds": ["c"]}]})
    }

    /// `[SPEC-PGM-050]`: what the hub hands over is held, and the answer says
    /// so with what this node runs; nothing is applied until a person answers.
    #[test]
    fn a_handed_programme_is_held_and_the_answer_says_where_the_node_stands() {
        let (dir, s) = store("held");
        let before = s.programme().unwrap().fingerprint;
        let r = arrive(&s, &wkmp(1)).unwrap();
        assert_eq!(r["outcome"], "asking");
        assert_eq!(r["current"]["fingerprint"], before.as_str(), "nothing replaced");
        assert_eq!((r["waiting"]["name"].as_str(), r["waiting"]["hub_version"].as_i64()), (Some("WKMP"), Some(1)));
        assert_eq!(r["waiting"]["kept"], false);
        let _ = std::fs::remove_dir_all(dir);
    }

    /// `[SPEC-PGM-310]`, end to end through `take`: held when signed by the
    /// pinned mesh key and addressed to this node, answered signed by this
    /// node's key; refused, with nothing held, when signed by another key, for
    /// another node or another step, or stale.
    #[test]
    fn the_signed_hand_over_end_to_end() {
        use crate::star_node::tests::{node, signed};
        use p256::ecdsa::signature::Verifier;
        use p256::pkcs8::DecodePrivateKey;
        let n = node("programme");
        let req = |op: &str, who: &str, at_ms: i64| {
            json!({"op": op, "node": who, "mesh": n.mesh_fp, "nonce": "00112233445566778899aabbccddeeff",
                   "at_ms": at_ms, "programme": wkmp(1)})
        };
        let now = crate::star_node::now_ms();
        let stranger = p256::ecdsa::SigningKey::random(&mut rand_core::OsRng);
        for (key, r, why) in [
            (&stranger, req(OP, &n.me, now), "not signed by this player's mesh"),
            (&n.mesh, req(OP, "someone-else", now), "another node"),
            (&n.mesh, req("commit", &n.me, now), "not programme"),
            (&n.mesh, req(OP, &n.me, now - 11 * 60 * 1000), "stale"),
        ] {
            let err = take(&n.l, &n.lib, &signed(key, &r)).unwrap_err();
            assert!(err.contains(why), "{why}: {err}");
        }
        let s = PlayerStore::open_split(&n.l, &n.lib).unwrap();
        assert!(s.programme_pending().unwrap().is_none(), "nothing refused was held");

        let got = take(&n.l, &n.lib, &signed(&n.mesh, &req(OP, &n.me, now))).unwrap();
        let answer = got["answer"].as_str().unwrap();
        let pem = std::fs::read_to_string(crate::discovery::identity(&n.l).unwrap().key).unwrap();
        let node_key = p256::ecdsa::SigningKey::from_pkcs8_pem(&pem).unwrap();
        let sig = { use base64::Engine; base64::engine::general_purpose::STANDARD.decode(got["signature"].as_str().unwrap()).unwrap() };
        assert!(node_key.verifying_key().verify(answer.as_bytes(), &p256::ecdsa::Signature::from_der(&sig).unwrap()).is_ok(),
                "the answer is signed by this node's key");
        let a: Value = serde_json::from_str(answer).unwrap();
        assert_eq!((a["op"].as_str(), a["node"].as_str()), (Some(OP), Some(n.me.as_str())));
        assert_eq!(a["nonce"], "00112233445566778899aabbccddeeff", "the answer is to this request");
        assert_eq!(a["result"]["outcome"], "asking");
        assert_eq!(s.programme_pending().unwrap().map(|p| p.hub_version), Some(1), "and it is held");
        let _ = std::fs::remove_dir_all(&n.dir);
    }

    /// A request with no programme only asks; a malformed one is refused.
    #[test]
    fn no_programme_only_asks_and_a_malformed_one_is_refused() {
        let (dir, s) = store("ask");
        let r = arrive(&s, &Value::Null).unwrap();
        assert!(r["outcome"].is_null());
        assert!(r["waiting"].is_null(), "nothing arrived, nothing waits");
        assert!(arrive(&s, &json!({"name": "WKMP"})).unwrap_err().contains("the programme"));
        let _ = std::fs::remove_dir_all(dir);
    }
}
