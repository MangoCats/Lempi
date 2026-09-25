//! Speaker selection, fronting the privileged helper `[PI3-API-050]`.
//!
//! Every BlueZ operation goes through `sudo lempi-btctl`, which holds the
//! privilege and enforces its own argument checks `[PI-SET-030]`. This module
//! is the other half of that boundary: it decides which verbs exist, and it
//! validates the address again before spending a subprocess on it.
//!
//! Checking twice is deliberate. The helper's check is the one that must not
//! be bypassed; this one exists so a malformed address from a browser is a 400
//! with an explanation rather than a non-zero exit nobody reads, and so the
//! rule survives someone later calling this module from somewhere new.

use std::process::Command;

/// Public so the start-up capability check asks about the same path this
/// module runs `[GDE-HST-360]`.
pub const HELPER: &str = "/usr/local/bin/lempi-btctl";

/// The verbs the web surface may invoke. An enum rather than a string passed
/// through, so an unknown verb cannot reach the helper at all.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Verb {
    List,
    Scan,
    Pair,
    Repair,
    Use,
    Forget,
    Status,
    /// Every radio and whether it is blocked `[PI3-RF-010]`.
    Radios,
}

impl Verb {
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "list" => Verb::List,
            "scan" => Verb::Scan,
            "pair" => Verb::Pair,
            "repair" => Verb::Repair,
            "use" => Verb::Use,
            "forget" => Verb::Forget,
            "status" => Verb::Status,
            "radios" => Verb::Radios,
            _ => return None,
        })
    }

    fn as_str(self) -> &'static str {
        match self {
            Verb::List => "list",
            Verb::Scan => "scan",
            Verb::Pair => "pair",
            Verb::Repair => "repair",
            Verb::Use => "use",
            Verb::Forget => "forget",
            Verb::Status => "status",
            Verb::Radios => "radios",
        }
    }

    /// Does this verb name a device?
    pub fn needs_address(self) -> bool {
        !matches!(self, Verb::List | Verb::Scan | Verb::Radios)
    }
}

/// A Bluetooth device address, in the one form the helper accepts.
///
/// Anchored, fixed length, uppercase hex. Deliberately not a lenient parse
/// that normalises what it is given: the value is about to become an argument
/// to a privileged program, and the useful property is that anything not
/// already exactly right is refused rather than repaired into something
/// plausible.
pub fn is_address(s: &str) -> bool {
    let b = s.as_bytes();
    if b.len() != 17 {
        return false;
    }
    b.iter().enumerate().all(|(i, c)| {
        if i % 3 == 2 {
            *c == b':'
        } else {
            c.is_ascii_digit() || (b'A'..=b'F').contains(c)
        }
    })
}

/// Run a verb. `Err` carries a message fit to show a listener.
pub fn run(verb: Verb, address: Option<&str>) -> Result<serde_json::Value, String> {
    if verb.needs_address() {
        match address {
            Some(a) if is_address(a) => {}
            _ => return Err("not a device address".into()),
        }
    }
    let mut cmd = Command::new("sudo");
    cmd.arg("-n").arg(HELPER).arg(verb.as_str());
    if let Some(a) = address.filter(|_| verb.needs_address()) {
        cmd.arg(a);
    }
    let out = cmd.output().map_err(|e| format!("helper not available: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout);
    // The helper reports its own failures as JSON with ok:false, so a parse
    // failure here means something else went wrong -- most likely the sudoers
    // rule is missing, which is worth saying plainly rather than as a blank.
    serde_json::from_str(text.trim()).map_err(|_| {
        let err = String::from_utf8_lossy(&out.stderr);
        if err.contains("password") || err.contains("not allowed") {
            "helper is not permitted to run; the sudoers rule is missing".into()
        } else {
            format!("helper gave no usable answer: {}", err.trim())
        }
    })
}

/// Switch one radio `[PI3-RF-020]`.
///
/// Separate from [`run`] because its two arguments are a kind and a state
/// rather than a device address, and widening `run` to carry arbitrary strings
/// would give up the property that makes it safe: every argument it passes is
/// checked against a closed shape first. Both of these are checked here too,
/// and again in the helper, which is the side that actually holds privilege.
///
/// **The helper, not this, decides what may be switched off.** It refuses to
/// block the radio carrying the default route -- on a Pi Zero 2 W that is Wi-Fi
/// and there is no way back, while on a machine with wired ethernet it may cost
/// nothing `[PI3-RF-030]`. Putting the rule there keeps it true for every
/// caller, including a person at a terminal.
pub fn set_radio(kind: &str, on: bool) -> Result<serde_json::Value, String> {
    if !matches!(kind, "bluetooth" | "wlan" | "wwan") {
        return Err("not a radio kind".into());
    }
    let out = Command::new("sudo")
        .arg("-n")
        .arg(HELPER)
        .arg("radio")
        .arg(kind)
        .arg(if on { "on" } else { "off" })
        .output()
        .map_err(|e| format!("helper not available: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim())
        .map_err(|_| format!("helper gave no usable answer: {}",
                             String::from_utf8_lossy(&out.stderr).trim()))
}

/// Switch the appliance's status LED `[PI3-LED-010]`. Not a `Verb` -- it
/// names no device, and takes a mode the same shape `set_radio` already
/// takes a radio kind, so it gets the same small wrapper rather than
/// forcing itself into `run`'s device-address shape.
///
/// `mode` is one of `on`/`wifi`/`off`/`default` -- checked here, again in
/// the helper, and again in the web route before that, the same
/// check-it-more-than-once posture `is_address` explains for a device
/// address. `pct` is only meaningful (and only sent) for `on`.
///
/// Applies immediately, live. Persisting the choice so it survives a
/// reboot is a separate concern, done by the caller writing
/// `player_settings` and by a boot-time script re-reading it -- this
/// function only ever touches the hardware.
pub fn set_led(mode: &str, pct: Option<u8>) -> Result<serde_json::Value, String> {
    if !matches!(mode, "on" | "wifi" | "off" | "default") {
        return Err("not a valid led mode".into());
    }
    let mut cmd = Command::new("sudo");
    cmd.arg("-n").arg(HELPER).arg("led").arg(mode);
    if mode == "on" {
        cmd.arg(pct.unwrap_or(100).clamp(1, 100).to_string());
    }
    let out = cmd.output().map_err(|e| format!("helper not available: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim())
        .map_err(|_| format!("helper gave no usable answer: {}",
                             String::from_utf8_lossy(&out.stderr).trim()))
}

// ------------------------------------------------------------------- wifi
// Moving the appliance into a new Wi-Fi network, or serving its own
// `[SPEC034]`. `wifi_scan`/`wifi_known` are the one place this module
// does not simply relay the helper's own JSON: an SSID is arbitrary bytes
// chosen by whoever runs a nearby network, not this project, and the
// helper deliberately does not attempt to hand-escape that into JSON
// itself (see `lempi-btctl`'s own `wifi-scan` comment) -- it relays
// `nmcli -m multiline`'s framed-by-field-name output verbatim instead,
// and `parse_multiline` below turns that into real, correctly-escaped
// JSON using this process's own encoder, the one place that can actually
// promise it.

fn run_helper(args: &[&str]) -> Result<std::process::Output, String> {
    Command::new("sudo")
        .arg("-n")
        .arg(HELPER)
        .args(args)
        .output()
        .map_err(|e| format!("helper not available: {e}"))
}

/// The helper's own `{"ok":false,"error":"..."}` shape, read off `stdout`
/// -- `die()` in `lempi-btctl` never writes to `stderr`, so a failure is
/// always valid JSON on the same stream a success would have used.
fn helper_error(out: &std::process::Output) -> String {
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str::<serde_json::Value>(text.trim())
        .ok()
        .and_then(|v| v.get("error").and_then(|e| e.as_str()).map(String::from))
        .unwrap_or_else(|| {
            let stderr = String::from_utf8_lossy(&out.stderr);
            format!("helper gave no usable answer: {}", stderr.trim())
        })
}

/// Turn `nmcli -m multiline` output into one JSON object per record.
/// Multiline mode frames by field name and by line, one requested field
/// per line, cycling back to the first field at each new record -- unlike
/// `-t` (terse) mode's single delimited line per record, there is no
/// inline separator here that arbitrary content (an SSID with a literal
/// `:` in it) could ever collide with, so this never needs an unescaping
/// parser at all. `fields` must be given in the exact order they were
/// requested from `nmcli`; a record left short at the very end (a
/// truncated final read) is dropped rather than emitted half-filled.
fn parse_multiline(text: &str, fields: &[&str]) -> Vec<serde_json::Value> {
    let mut out = Vec::new();
    let mut lines = text.lines();
    'records: loop {
        let mut obj = serde_json::Map::new();
        for &field in fields {
            let Some(line) = lines.next() else { break 'records };
            let value = line.split_once(':').map_or("", |(_, v)| v).trim();
            obj.insert(field.to_lowercase(), serde_json::Value::String(value.to_string()));
        }
        out.push(serde_json::Value::Object(obj));
    }
    out
}

/// Networks currently in radio range `[SPEC034]`.
pub fn wifi_scan() -> Result<Vec<serde_json::Value>, String> {
    let out = run_helper(&["wifi-scan"])?;
    if !out.status.success() {
        return Err(helper_error(&out));
    }
    Ok(parse_multiline(&String::from_utf8_lossy(&out.stdout), &["SSID", "SIGNAL", "SECURITY"]))
}

/// Every Wi-Fi connection profile NetworkManager already remembers --
/// this project's own "known networks" list is exactly this, not a
/// second copy of it `[SPEC034]`.
pub fn wifi_known() -> Result<Vec<serde_json::Value>, String> {
    let out = run_helper(&["wifi-known"])?;
    if !out.status.success() {
        return Err(helper_error(&out));
    }
    let all = parse_multiline(
        &String::from_utf8_lossy(&out.stdout),
        &["NAME", "TYPE", "AUTOCONNECT", "ACTIVE"],
    );
    Ok(all
        .into_iter()
        .filter(|v| v.get("type").and_then(|t| t.as_str()) == Some("wifi"))
        .collect())
}

/// Switch the client connection to `ssid`, schedule the hard revert, and
/// return the pending change's id and its own timeout `[SPEC034]`. An
/// apparent success here is not proof of reachability -- see
/// `wifi_confirm`.
pub fn wifi_connect(ssid: &str, password: &str) -> Result<serde_json::Value, String> {
    if ssid.is_empty() {
        return Err("ssid required".into());
    }
    let out = run_helper(&["wifi-connect", ssid, password])?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim()).map_err(|_| helper_error(&out))
}

/// Cancel the pending hard revert -- the browser's own proof that it can
/// still reach this device on whatever network `wifi_connect`/`ap_start`/
/// `ap_stop` just switched to `[SPEC034]`.
pub fn wifi_confirm(change_id: &str) -> Result<serde_json::Value, String> {
    if !change_id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_') {
        return Err("not a valid change id".into());
    }
    let out = run_helper(&["wifi-confirm", change_id])?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim()).map_err(|_| helper_error(&out))
}

/// Delete a known-network profile -- refused by the helper for whichever
/// one is currently active `[SPEC034]`.
pub fn wifi_forget(name: &str) -> Result<serde_json::Value, String> {
    if name.is_empty() {
        return Err("connection name required".into());
    }
    let out = run_helper(&["wifi-forget", name])?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim()).map_err(|_| helper_error(&out))
}

/// Whether a known network is offered automatically at boot `[SPEC034]`.
pub fn wifi_autoconnect(name: &str, on: bool) -> Result<serde_json::Value, String> {
    if name.is_empty() {
        return Err("connection name required".into());
    }
    let out = run_helper(&["wifi-autoconnect", name, if on { "on" } else { "off" }])?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim()).map_err(|_| helper_error(&out))
}

/// Start the appliance's own access point, schedule the hard revert, and
/// return the pending change's id `[SPEC034]`. `ssid`/`password` default
/// to the same published, not-a-secret credential `[PI-SET-030]` already
/// named -- passing `None` for either asks the helper to use it.
pub fn ap_start(ssid: Option<&str>, password: Option<&str>) -> Result<serde_json::Value, String> {
    let mut args = vec!["ap-start"];
    if let Some(s) = ssid {
        args.push(s);
        // The helper's own positional parsing needs a password argument
        // once an ssid is given at all, even to fall back to its default.
        args.push(password.unwrap_or("Lempi321"));
    } else if let Some(p) = password {
        args.push("Lempi");
        args.push(p);
    }
    let out = run_helper(&args)?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim()).map_err(|_| helper_error(&out))
}

/// Leave access-point mode, returning to whichever known network is set
/// to connect automatically, with the same schedule-then-confirm safety
/// as every other verb here `[SPEC034]`.
pub fn ap_stop() -> Result<serde_json::Value, String> {
    let out = run_helper(&["ap-stop"])?;
    let text = String::from_utf8_lossy(&out.stdout);
    serde_json::from_str(text.trim()).map_err(|_| helper_error(&out))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The case this parser exists for: a value containing the same `:`
    /// `nmcli -t` (terse) mode would need to escape -- multiline mode
    /// frames by field name and line instead, so it never needs to
    /// `[SPEC034]`.
    #[test]
    fn parse_multiline_handles_a_colon_inside_a_value() {
        let text = "SSID:                    My:Weird:Network\nSIGNAL:                  70\nSECURITY:                WPA2\n";
        let rows = parse_multiline(text, &["SSID", "SIGNAL", "SECURITY"]);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["ssid"], "My:Weird:Network");
        assert_eq!(rows[0]["signal"], "70");
    }

    #[test]
    fn parse_multiline_reads_several_records_in_order() {
        let text = "SSID:  A\nSIGNAL:  10\nSECURITY:  WPA2\nSSID:  B\nSIGNAL:  20\nSECURITY:  none\n";
        let rows = parse_multiline(text, &["SSID", "SIGNAL", "SECURITY"]);
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["ssid"], "A");
        assert_eq!(rows[1]["ssid"], "B");
        assert_eq!(rows[1]["security"], "none");
    }

    #[test]
    fn parse_multiline_drops_a_truncated_trailing_record() {
        let text = "SSID:  A\nSIGNAL:  10\nSECURITY:  WPA2\nSSID:  B\n";
        let rows = parse_multiline(text, &["SSID", "SIGNAL", "SECURITY"]);
        assert_eq!(rows.len(), 1, "a record left short at the end must not be emitted half-filled");
    }

    #[test]
    fn parse_multiline_of_empty_text_is_no_records_not_one_empty_one() {
        assert_eq!(parse_multiline("", &["SSID", "SIGNAL", "SECURITY"]), Vec::<serde_json::Value>::new());
    }

    #[test]
    fn wifi_known_keeps_only_wifi_profiles() {
        let text = "NAME:  preconfigured\nTYPE:  wifi\nAUTOCONNECT:  yes\nACTIVE:  yes\n\
                     NAME:  lo\nTYPE:  loopback\nAUTOCONNECT:  no\nACTIVE:  yes\n";
        let all = parse_multiline(text, &["NAME", "TYPE", "AUTOCONNECT", "ACTIVE"]);
        let wifi: Vec<_> =
            all.into_iter().filter(|v| v.get("type").and_then(|t| t.as_str()) == Some("wifi")).collect();
        assert_eq!(wifi.len(), 1);
        assert_eq!(wifi[0]["name"], "preconfigured");
    }

    #[test]
    fn wifi_connect_rejects_an_empty_ssid_before_spawning() {
        assert!(wifi_connect("", "password").is_err());
    }

    #[test]
    fn wifi_confirm_rejects_a_change_id_with_shell_metacharacters() {
        assert!(wifi_confirm("bad;id").is_err());
        assert!(wifi_confirm("bad id").is_err());
    }

    #[test]
    fn accepts_the_speaker_we_use() {
        assert!(is_address("20:64:DE:CF:F3:AD"));
    }

    #[test]
    fn refuses_anything_that_is_not_exactly_an_address() {
        // The case that matters: an argument bound for a privileged program.
        assert!(!is_address("20:64:DE:CF:F3:AD; rm -rf /"));
        assert!(!is_address("20:64:DE:CF:F3"), "too short");
        assert!(!is_address("20-64-DE-CF-F3-AD"), "wrong separator");
        assert!(!is_address("20:64:de:cf:f3:ad"), "lowercase is not normalised");
        assert!(!is_address(""));
        assert!(!is_address("../../etc/passwd"));
    }

    #[test]
    fn unknown_verbs_do_not_exist() {
        assert_eq!(Verb::parse("destroy"), None);
        assert_eq!(Verb::parse("remove"), None);
        assert_eq!(Verb::parse("use"), Some(Verb::Use));
    }

    #[test]
    fn only_the_listing_verbs_go_without_a_device() {
        assert!(!Verb::List.needs_address());
        assert!(!Verb::Scan.needs_address());
        for v in [Verb::Pair, Verb::Repair, Verb::Use, Verb::Forget, Verb::Status] {
            assert!(v.needs_address(), "{v:?} must name a device");
        }
    }

    #[test]
    fn a_verb_needing_a_device_refuses_a_bad_one_before_spawning() {
        assert!(run(Verb::Use, Some("nonsense")).is_err());
        assert!(run(Verb::Use, None).is_err());
    }
}
