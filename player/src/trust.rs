//! Trusted networks, on an appliance `[SPEC051]`, `[SPEC-NSH-180..190]`.
//!
//! A node takes part in the mesh only on networks its operator trusts. On any
//! other it is **dormant**: it answers no discovery, refuses the star sync's
//! routes, and refuses every membership step -- and it keeps playing, and its
//! own page keeps working `[SPEC-TN-020]`. A network is named by its
//! NetworkManager connection UUID, never its SSID, which anyone can copy
//! `[SPEC-TN-030]`.
//!
//! **The rule.** The node takes part when every connection it is on -- active,
//! and not loopback -- is trusted. One untrusted connection is enough to go
//! quiet: the discovery socket is bound to every interface, so it would answer
//! there too. Unknown is untrusted `[SPEC-TN-040]`, and so is "cannot tell": a
//! node that cannot read its networks says so and stays dormant.
//!
//! **Upgrade.** The first start that has no record trusts the connections
//! active then, so no node already in service goes quiet `[SPEC-NSH-185]`.
//!
//! **Granting and revoking.** Revoking trust only narrows exposure and is
//! open to the page. *Granting* it needs the pairing window, the same physical
//! act as pairing: otherwise any host on a foreign network could mark that
//! network trusted and wake the node on it.
//!
//! A build without the root helper -- a desktop, a hub -- has no
//! NetworkManager to key on and is not gated: the hub is stationary and
//! trusted `[SPEC-NSH-190]`.

use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde_json::{json, Value};

/// One connection, as NetworkManager lists it -- read without root
/// (`bluetooth::connections_unprivileged`).
#[derive(Clone, Debug, PartialEq)]
pub struct Conn {
    pub uuid: String,
    pub name: String,
    pub kind: String,
    pub active: bool,
}

/// How long a reading of the connections is reused. Discovery asks on every
/// query; a network changes rarely, and a change is seen within this long
/// without a poll of its own `[SPEC-TN-900]`.
const FRESH: Duration = Duration::from_secs(30);

type Reading = (Instant, Result<Vec<Conn>, String>);
static CACHE: Mutex<Option<Reading>> = Mutex::new(None);

/// The connections that say which network the node is on.
fn on_now(conns: &[Conn]) -> Vec<&Conn> {
    conns.iter().filter(|c| c.active && c.kind != "loopback").collect()
}

/// Whether a node on `conns`, trusting `trusted`, takes part; if not, why.
pub fn standing(conns: &[Conn], trusted: &[String]) -> Result<(), String> {
    let on = on_now(conns);
    if on.is_empty() {
        return Err("it is on no network".into());
    }
    let strangers: Vec<&str> = on.iter().filter(|c| !trusted.contains(&c.uuid)).map(|c| c.name.as_str()).collect();
    match strangers.as_slice() {
        [] => Ok(()),
        [one] => Err(format!("it is on {one}, which is not trusted")),
        many => Err(format!("it is on {}, which are not trusted", many.join(", "))),
    }
}

/// What the first start trusts: the connections active then.
pub fn seed(conns: &[Conn]) -> Vec<String> {
    on_now(conns).iter().map(|c| c.uuid.clone()).collect()
}

/// A connection UUID as NetworkManager writes one, and nothing that could be
/// anything else.
fn is_uuid(s: &str) -> bool {
    !s.is_empty() && s.len() <= 64 && s.chars().all(|c| c.is_ascii_hexdigit() || c == '-')
}

#[cfg(all(feature = "appliance", not(test)))]
fn read_connections() -> Result<Vec<Conn>, String> {
    let rows = crate::bluetooth::connections_unprivileged()?;
    Ok(rows
        .iter()
        .map(|r| Conn {
            uuid: r["uuid"].as_str().unwrap_or_default().to_string(),
            name: r["name"].as_str().unwrap_or_default().to_string(),
            kind: r["type"].as_str().unwrap_or_default().to_string(),
            active: r["active"] == "yes",
        })
        .filter(|c| is_uuid(&c.uuid))
        .collect())
}

/// Whether trust applies on this build at all.
pub const APPLIES: bool = cfg!(feature = "appliance");

// A test's own reading of the networks, per thread: tests run in parallel and
// must not see each other's, and a test that sets none -- every test not
// about trust -- finds trust not applying, so no test reaches the real helper.
#[cfg(test)]
thread_local! {
    pub(crate) static FAKE: std::cell::RefCell<Option<Result<Vec<Conn>, String>>> =
        const { std::cell::RefCell::new(None) };
}

/// Whether trust applies here: the build's, or under test the fake's.
fn applies() -> bool {
    #[cfg(test)]
    {
        FAKE.with(|f| f.borrow().is_some())
    }
    #[cfg(not(test))]
    {
        APPLIES
    }
}

fn connections() -> Result<Vec<Conn>, String> {
    #[cfg(test)]
    {
        let _ = (&CACHE, FRESH);
        FAKE.with(|f| f.borrow().clone()).unwrap_or_else(|| Err("no fake networks".into()))
    }
    #[cfg(all(feature = "appliance", not(test)))]
    {
        let mut g = CACHE.lock().map_err(|_| "the network cache is poisoned".to_string())?;
        if let Some((at, r)) = g.as_ref() {
            if at.elapsed() < FRESH {
                return r.clone();
            }
        }
        let r = read_connections();
        *g = Some((Instant::now(), r.clone()));
        r
    }
    #[cfg(all(not(feature = "appliance"), not(test)))]
    {
        let _ = (&CACHE, FRESH);
        Err("this build reads no networks".into())
    }
}

/// Forget the cached reading: after a trust change, or a Wi-Fi change.
pub fn invalidate() {
    if let Ok(mut g) = CACHE.lock() {
        *g = None;
    }
}

fn trusted(listener: &Path, library: &Path) -> Vec<String> {
    crate::db::PlayerStore::open_split(listener, library)
        .ok()
        .and_then(|s| s.load_trusted_networks())
        .unwrap_or_default()
}

/// Whether this node takes part in the mesh here; if not, why
/// `[SPEC-NSH-180]`. Always yes on a build trust does not apply to.
pub fn participating(listener: &Path, library: &Path) -> Result<(), String> {
    if !applies() {
        return Ok(());
    }
    let conns = connections().map_err(|e| format!("its networks cannot be read ({e})"))?;
    standing(&conns, &trusted(listener, library))
}

/// At start: record the trusted networks if none are, from the connections
/// active now `[SPEC-NSH-185]`. Returns the line to log, which says what was
/// assumed either way `[GDE-DEP-060]`.
pub fn seed_at_start(listener: &Path, library: &Path) -> String {
    if !applies() {
        return "trusted networks: not applied on this build; a hub or desktop is stationary [SPEC-NSH-190]".into();
    }
    let Ok(store) = crate::db::PlayerStore::open_split(listener, library) else {
        return "trusted networks: the settings could not be opened; dormant until they can".into();
    };
    if let Some(t) = store.load_trusted_networks() {
        return format!("trusted networks: {} recorded", t.len());
    }
    match connections() {
        Ok(c) => {
            let s = seed(&c);
            if s.is_empty() {
                return "trusted networks: none recorded and no network active; the next start with one \
                        trusts it [SPEC-NSH-185]".into();
            }
            let names: Vec<&str> = on_now(&c).iter().map(|c| c.name.as_str()).collect();
            match store.save_trusted_networks(&s) {
                Ok(()) => format!("trusted networks: none recorded; trusting what this node is on now -- {} \
                                   [SPEC-NSH-185]", names.join(", ")),
                Err(e) => format!("trusted networks: could not record {} ({e}); dormant", names.join(", ")),
            }
        }
        Err(e) => format!("trusted networks: none recorded, and the networks cannot be read ({e}); dormant \
                           until they can"),
    }
}

/// What the Settings page shows: every connection but loopback, which of
/// them the node is on and trusts, and whether it takes part -- with why not
/// `[SPEC-TN-060]`.
pub fn status(listener: &Path, library: &Path) -> Value {
    if !applies() {
        return json!({"applies": false, "participating": true, "why": null, "networks": []});
    }
    let t = trusted(listener, library);
    let (networks, standing_now) = match connections() {
        Ok(c) => (
            c.iter()
                .filter(|c| c.kind != "loopback")
                .map(|c| json!({"uuid": c.uuid, "name": c.name, "type": c.kind, "active": c.active,
                                "trusted": t.contains(&c.uuid)}))
                .collect(),
            standing(&c, &t),
        ),
        Err(e) => (Vec::new(), Err(format!("its networks cannot be read ({e})"))),
    };
    json!({"applies": true, "participating": standing_now.is_ok(), "why": standing_now.err(), "networks": networks})
}

/// Trust a connection, or stop trusting it. Granting needs `window_open` --
/// the pairing window, or a loopback caller; revoking never does.
pub fn set(listener: &Path, library: &Path, uuid: &str, on: bool, window_open: bool) -> Result<Value, String> {
    if !applies() {
        return Err("trusted networks do not apply on this build".into());
    }
    if !is_uuid(uuid) {
        return Err("not a connection".into());
    }
    if on && !window_open {
        return Err("trusting a network needs the pairing window: press the pair button on the device, \
                    then try again"
            .into());
    }
    let conns = connections()?;
    if on && !conns.iter().any(|c| c.uuid == uuid && c.kind != "loopback") {
        return Err("no such connection on this node".into());
    }
    let store = crate::db::PlayerStore::open_split(listener, library).map_err(|e| e.to_string())?;
    let mut t = store.load_trusted_networks().unwrap_or_default();
    t.retain(|u| u != uuid);
    if on {
        t.push(uuid.to_string());
    }
    store.save_trusted_networks(&t).map_err(|e| e.to_string())?;
    invalidate();
    let name = conns.iter().find(|c| c.uuid == uuid).map_or(uuid, |c| c.name.as_str());
    tracing::info!("trusted networks: {name} {}", if on { "trusted" } else { "no longer trusted" });
    Ok(status(listener, library))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn c(uuid: &str, name: &str, kind: &str, active: bool) -> Conn {
        Conn { uuid: uuid.into(), name: name.into(), kind: kind.into(), active }
    }

    /// bose's own connections, as `nmcli` listed them on 2026-09-28.
    fn bose() -> Vec<Conn> {
        vec![c("10e5d03a", "home-wifi", "wifi", true), c("2ed66f88", "lo", "loopback", true),
             c("75a1216a", "netplan-eth0", "ethernet", false)]
    }

    #[test]
    fn a_node_takes_part_only_where_every_network_it_is_on_is_trusted() {
        assert!(standing(&bose(), &["10e5d03a".into()]).is_ok(), "home trusted: takes part; loopback never counts");
        let why = standing(&bose(), &[]).unwrap_err();
        assert!(why.contains("home-wifi") && why.contains("not trusted"), "unknown is untrusted: {why}");
        let mut two = bose();
        two[2].active = true;
        let why = standing(&two, &["10e5d03a".into()]).unwrap_err();
        assert!(why.contains("netplan-eth0"), "one untrusted connection is enough to go quiet: {why}");
        assert!(standing(&[c("2ed66f88", "lo", "loopback", true)], &[]).unwrap_err().contains("no network"));
    }

    /// `[SPEC-NSH-185]`: the upgrade trusts exactly what the node is on.
    #[test]
    fn the_first_start_trusts_exactly_the_active_connections() {
        assert_eq!(seed(&bose()), ["10e5d03a"], "not loopback, not an inactive profile");
    }

    #[test]
    fn a_uuid_is_hex_and_dashes_only() {
        assert!(is_uuid("10e5d03a-95c6-34ba-b347-6aee8c4d1cf3"));
        assert!(!is_uuid("") && !is_uuid("x; rm") && !is_uuid(&"a".repeat(65)));
    }

    fn fake(r: Result<Vec<Conn>, String>) {
        FAKE.with(|f| *f.borrow_mut() = Some(r));
    }

    fn tmp(name: &str) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("lempi-trust-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d.join("listener.db")
    }

    /// `[SPEC-NSH-185]` and granting: the upgrade trusts what the node is on;
    /// revoking is open and makes the node dormant; granting needs the window.
    #[test]
    fn trust_is_seeded_revoked_freely_and_granted_physically() {
        let l = tmp("set");
        fake(Ok(bose()));
        assert!(participating(&l, &l).is_err(), "nothing recorded yet: unknown is untrusted");
        let line = seed_at_start(&l, &l);
        assert!(line.contains("home-wifi"), "{line}");
        assert!(participating(&l, &l).is_ok(), "the upgrade trusted what it was on");
        assert!(seed_at_start(&l, &l).contains("1 recorded"), "seeded once, never again");
        set(&l, &l, "10e5d03a", false, false).unwrap();
        assert!(participating(&l, &l).unwrap_err().contains("not trusted"), "revoked, without the window");
        let e = set(&l, &l, "10e5d03a", true, false).unwrap_err();
        assert!(e.contains("pairing window"), "granting needs the physical act: {e}");
        assert!(set(&l, &l, "deadbeef", true, true).unwrap_err().contains("no such connection"));
        let s = set(&l, &l, "10e5d03a", true, true).unwrap();
        assert_eq!(s["participating"], true, "{s}");
        assert_eq!(s["networks"].as_array().unwrap().len(), 2, "every connection but loopback is listed");
        FAKE.with(|f| *f.borrow_mut() = None);
    }

    /// `[SPEC-NSH-180]`: dormant means silent to discovery and closed to the
    /// sync -- and "cannot tell" is dormant too.
    #[test]
    fn a_dormant_node_answers_nothing_and_syncs_nothing() {
        let l = tmp("gates");
        let me = crate::discovery::identity(&l).unwrap();
        let q = json!({"lempi": 1, "nonce": "abc123", "q": "candidates"});
        assert!(crate::discovery::answer(&q, &me, &l, &l, 5720).is_some(), "trust not applying: it answers");
        fake(Err("nmcli is not running".into()));
        assert!(participating(&l, &l).unwrap_err().contains("cannot be read"), "cannot tell is dormant");
        assert!(crate::discovery::answer(&q, &me, &l, &l, 5720).is_none(), "dormant: silent");
        let e = crate::star_node::handle(&l, &l, "snapshot", &json!({"request": "{}", "signature": ""})).err().unwrap();
        assert!(e.contains("dormant"), "the sync is refused before anything else: {e}");
        fake(Ok(bose()));
        seed_at_start(&l, &l);
        assert!(crate::discovery::answer(&q, &me, &l, &l, 5720).is_some(), "trusted again: it answers");
        FAKE.with(|f| *f.borrow_mut() = None);
    }
}
