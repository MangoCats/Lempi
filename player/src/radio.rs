//! Whether this node's Wi-Fi shares a radio with the speaker it plays to
//! `[PI3-AIM-170]`, `[GDE-INT-137]`.
//!
//! The Pi Zero W, Zero 2 W and 3 B carry Wi-Fi and Bluetooth on one chip and
//! one antenna, so anything else the node does on the radio, or that keeps it
//! busy, is time the speaker's audio does not get
//! [PI031](../../LempiPi/PI031-settling-the-shared-radio.md). Two things here
//! give way to it: a bulk transfer pauses the music (`web/transfer.rs`), and
//! the integrity check waits for the music to pause (`scrub.rs`). Both ask
//! this, so the rule is written once.
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::path::Path;

use crate::sink::Tristate;

/// The rule: the node has the Bluetooth helper, and its output is a
/// Bluetooth speaker -- a stored `bt:` output, or a stored `dac` on a node
/// PipeWire shows has none, which is not believed `[PI3-FOUND-820]`. A node
/// that cannot tell is not counted: `bose` has no PipeWire to ask, plays to
/// its DAC, and was paused for every upload of 2026-10-25 by a first version
/// that read "unknown" as shared.
pub fn radio_shared(bluetooth: bool, active_output: &str, local: Tristate) -> bool {
    bluetooth && (active_output.starts_with("bt:") || (active_output == "dac" && local == Tristate::No))
}

/// The rule, asked of this node: its stored output from the listener's file,
/// and PipeWire only when that output is `dac`. Blocks on a database read and
/// sometimes a `pw-cli`, so a caller asks it once in a while, not per sample.
pub fn shares_radio(bluetooth: bool, listener: &Path, library: &Path) -> bool {
    if !bluetooth {
        return false;
    }
    let active = crate::db::PlayerStore::open_split(listener, library)
        .map(|s| s.get_active_output())
        .unwrap_or_default();
    let local = if active == "dac" { crate::sink::local_output() } else { Tristate::Unknown };
    radio_shared(true, &active, local)
}

#[cfg(test)]
mod tests {
    use super::radio_shared;
    use crate::sink::Tristate;

    #[test]
    fn only_a_bluetooth_output_shares_the_radio() {
        assert!(radio_shared(true, "bt:AA:BB:CC:DD:EE:FF", Tristate::Unknown), "a speaker");
        assert!(radio_shared(true, "dac", Tristate::No), "a stored DAC on a node with none [PI3-FOUND-820]");
        assert!(!radio_shared(true, "dac", Tristate::Yes), "a real DAC");
        assert!(!radio_shared(true, "dac", Tristate::Unknown), "a node that cannot tell -- bose -- is not counted");
        assert!(!radio_shared(false, "bt:AA:BB:CC:DD:EE:FF", Tristate::Unknown), "no Bluetooth helper, no speaker");
    }
}
