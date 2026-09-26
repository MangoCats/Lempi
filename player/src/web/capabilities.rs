//! What this host can do beyond playing music `[GDE-HST-360]`.
//!
//! The lempi skin offers restart, shut down, Wi-Fi, speakers, the LED and the
//! radios. Each reaches past the player -- `sudo systemctl`, or `sudo
//! lempi-btctl` -- and on a host without that, the button was still drawn and
//! could only fail: measured 2026-09-25, `bose` and `lp3-wifi` have no
//! `lempi-btctl` at all, so their Wi-Fi, speaker, LED and radio controls had
//! never worked. The snapshot now says which of these exist, the way
//! `Playback::Capabilities` says what a backend can do `[SPEC-BK-040]`, and a
//! skin hides what is absent.
//!
//! **Measured, not assumed** `[GDE-DEP-060]`: once, at start, by asking `sudo`
//! whether each command would be allowed without a password (`sudo -n -l`,
//! which lists and runs nothing) and whether the helper is there at all. A
//! build without the `appliance` feature has none of this code to reach, and
//! answers all false without asking.
#![deny(clippy::print_stdout, clippy::print_stderr)]

/// Each field: the control exists on this host, and would be let through.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize)]
pub struct Capabilities {
    /// `sudo systemctl restart lempi` -- restart the player.
    pub restart: bool,
    /// `sudo systemctl poweroff` -- shut the machine down.
    pub power_off: bool,
    /// The rest go through `lempi-btctl` `[PI3-API-010]`.
    pub wifi: bool,
    pub bluetooth: bool,
    pub led: bool,
    pub radios: bool,
    /// Taking part in an echo fleet -- following, and the delay and join
    /// settings that only mean something in one. A build without
    /// `echo-client` cannot follow, and a phone is built without it
    /// `[REQ-AND-170]`, `[REQ-AND-180]`. Not a host fact but a build fact, so
    /// it needs no measuring.
    pub follow: bool,
}

impl Capabilities {
    /// Ask this host. Blocks on a few `sudo -n -l` calls, so it runs once at
    /// start on the host's own thread, never per request.
    #[cfg(feature = "appliance")]
    pub fn detect() -> (Self, String) {
        let allowed = |args: &[&str]| {
            std::process::Command::new("sudo")
                .arg("-n")
                .arg("-l")
                .args(args)
                .output()
                .is_ok_and(|o| o.status.success())
        };
        let helper = crate::bluetooth::HELPER;
        let has_helper = std::path::Path::new(helper).exists();
        let via_helper = has_helper && allowed(&[helper]);
        let caps = Capabilities {
            restart: allowed(&["systemctl", "restart", "lempi"]),
            power_off: allowed(&["systemctl", "poweroff"]),
            wifi: via_helper,
            bluetooth: via_helper,
            led: via_helper,
            radios: via_helper,
            follow: cfg!(feature = "echo-client"),
        };
        let why = if !has_helper {
            format!(" (no {helper})")
        } else if !via_helper {
            format!(" ({helper} is not allowed through sudo -n)")
        } else {
            String::new()
        };
        (caps, why)
    }

    /// Not an appliance build: nothing to ask, and nothing offered.
    #[cfg(not(feature = "appliance"))]
    pub fn detect() -> (Self, String) {
        let caps = Capabilities { follow: cfg!(feature = "echo-client"), ..Default::default() };
        (caps, " (built without the appliance feature)".into())
    }

    /// The start line: what is offered and what is not, by name.
    pub fn describe(&self, why: &str) -> String {
        let all = [
            ("restart", self.restart),
            ("power-off", self.power_off),
            ("wifi", self.wifi),
            ("bluetooth", self.bluetooth),
            ("led", self.led),
            ("radios", self.radios),
            ("follow", self.follow),
        ];
        let on: Vec<&str> = all.iter().filter(|(_, v)| *v).map(|(n, _)| *n).collect();
        let off: Vec<&str> = all.iter().filter(|(_, v)| !*v).map(|(n, _)| *n).collect();
        match (on.is_empty(), off.is_empty()) {
            (_, true) => format!("host controls: all offered ({})", on.join(", ")),
            (true, _) => format!("host controls: none offered{why}"),
            _ => format!("host controls: {}; not offered: {}{why}", on.join(", "), off.join(", ")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_start_line_names_what_is_missing_and_why() {
        let caps = Capabilities { restart: true, power_off: true, ..Default::default() };
        assert_eq!(
            caps.describe(" (no /usr/local/bin/lempi-btctl)"),
            "host controls: restart, power-off; not offered: wifi, bluetooth, led, radios, follow \
             (no /usr/local/bin/lempi-btctl)"
        );
        assert_eq!(
            Capabilities::default().describe(" (built without the appliance feature)"),
            "host controls: none offered (built without the appliance feature)"
        );
    }

    /// The wire form a skin reads: every field present, as a boolean, so a
    /// skin can tell "absent" (`false`) from "an older server" (`undefined`).
    #[test]
    fn every_capability_is_on_the_wire() {
        let v = serde_json::to_value(Capabilities::default()).unwrap();
        for k in ["restart", "power_off", "wifi", "bluetooth", "led", "radios", "follow"] {
            assert_eq!(v[k], serde_json::json!(false), "{k}");
        }
    }

    /// Without the feature, no host control is asked about or offered; the
    /// one capability left is following, and only where `echo-client` is
    /// compiled in -- a phone's build has neither `[REQ-AND-170]`.
    #[cfg(not(feature = "appliance"))]
    #[test]
    fn a_build_without_the_feature_offers_nothing() {
        let expect = Capabilities { follow: cfg!(feature = "echo-client"), ..Default::default() };
        assert_eq!(Capabilities::detect().0, expect);
    }

    /// Following is a build fact: offered exactly when `echo-client` is in.
    #[test]
    fn following_is_offered_exactly_when_the_echo_client_is_built() {
        assert_eq!(Capabilities::detect().0.follow, cfg!(feature = "echo-client"));
    }
}
