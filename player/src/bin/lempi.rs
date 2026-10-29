//! Lempi: continuous radio with a web UI.
//!
//! **The command line, and nothing else** `[GDE-HST-040]`, `[GDE-HST-320]`.
//! What the player *is* -- its engine thread, its tick loop, the MPD guest,
//! the echo client, the web server -- lives in [`lempi_player::host`] since
//! 2026-09-25, where a test or another host can reach it. This file resolves
//! the command line through its four layers into a [`Config`] and hands it
//! over; a phone would hand over the same struct from a settings screen.
//!
//! A plain `fn main`, not `#[tokio::main]`: the library builds and owns its
//! own runtime `[GDE-HST-300]`, and two runtimes nested is one tokio refuses.
//!
//! The command line is [`lempi_player::cli::specs::lempi`] and lives there
//! rather than here: one parser serves all seventeen binaries, and each
//! contributes only a table of options `[GDE-CLI-010]`. `lempi --help` prints
//! it. **Until 2026-09-20 it did not** -- nothing recognised `--help`, so the
//! string became the positional database path and the player started against
//! a file of that name `[GDE-CLI-050]`.
//!
//! `--device` takes a case-insensitive substring of the output device name.
//! It matters more than it looks: PipeWire offers a `Dummy Output` whenever no
//! real sink is present, and a Bluetooth speaker that is momentarily absent at
//! startup leaves the player attached to that dummy -- playing perfectly into
//! nothing, and reporting itself healthy `[IMPL-AUD-010]`.

use std::path::PathBuf;

use lempi_player::cli::specs::lempi as opt;
use lempi_player::host::{Config, Ended, Player, StartError};

/// Set in a player this one started because Windows refused its output, to
/// the time it did so, in Unix seconds `[GDE-HST-230]`.
const REFUSAL_RESTART: &str = "LEMPI_REFUSAL_RESTART";
/// A refusal sooner than this after a restart for one is not restarted for
/// again: a new process did not clear it, and another would only loop.
const RESTART_SPACING: u64 = 600;

fn main() {
    // The pairing button's handler, before anything else `[SPEC-NSH-160]`.
    // SIGUSR1's default action ends a process, so a press that arrived before
    // the handler was installed -- or on a host that never installed it --
    // would stop the player. Installed first, on every host, it never does.
    lempi_player::pairing::install_signal_handler();
    // Help, version and every refusal are handled here, identically for all
    // seventeen binaries. Nothing below this line inspects a raw argument.
    let mut args = opt::SPEC.parse();
    // **What the first three layers settled** `[GOV-SRC-040]`, before
    // anything is opened. Printed here rather than only after the database
    // is attached so that it is visible on the query path below too, where
    // there is no layer 2 to attach.
    for line in args.provenance() {
        println!("lempi: {line}");
    }
    // Before the database, the engine or the server: this asks the machine a
    // question and leaves `[IMPL-AUD-010]`. It is what that document has told
    // appliance builders to run since long before the flag existed.
    if args.has(&opt::LIST_DEVICES) {
        for d in lempi_player::output::Output::list_devices() {
            println!("{d}");
        }
        return;
    }
    println!("lempi {}", lempi_player::build_id());
    if std::env::var_os(REFUSAL_RESTART).is_some() {
        // Whether the output line below names the device is the measurement:
        // a new process opening what the old one was refused `[GDE-HST-230]`.
        println!("lempi: started again because Windows refused the last process its output [GDE-HST-230]");
    }
    // **Bootstrap, and then everything else** `[GDE-CLI-095]`. These two are
    // declared `.bootstrap()`, so they resolve from the command line, the
    // environment and their defaults only -- they name the database the
    // stored settings live in, and cannot be answered by it.
    let db = PathBuf::from(args.need(&opt::LISTENER));
    // The catalog-side file, required since 2026-10-02: it once defaulted to
    // `db`, the unsplit shape no installation still has, and so a unit that
    // lost its `--library` ran on quietly writing plays into the catalogue
    // `[IMPL-DBSPLIT-025]`.
    let library = PathBuf::from(args.need(&opt::LIBRARY));

    // Layer 2 is attached here and not before. Everything read after this
    // line resolves through all four layers; everything above it, through
    // three. That ordering is the reason the two are written apart.
    if let Some(saved) =
        lempi_player::db::PlayerStore::open_split(&db, &library).ok().and_then(|s| s.load_settings())
    {
        for complaint in args.with_settings(|k| saved.value_of(k)) {
            // A stored setting that could not be used: a warning, where
            // `journalctl -p warning` can see it `[GDE-HST-180]` step (4).
            tracing::warn!("lempi: {complaint}");
        }
    }
    // And what layer 2 has now changed, if anything. Only the stored ones:
    // the rest were reported above and repeating them would bury these.
    for line in args.provenance() {
        if line.contains("from the stored setting") {
            println!("lempi: {line}");
        }
    }

    // The node to follow `[GDE-ECHO-330]`. Absent means this node is nobody's
    // echo and plays its own programme, which is every node's default and the
    // behaviour it keeps if its master ever goes away `[GDE-ECHO-500]`.
    let follow = args.text(&opt::FOLLOW).map(str::to_string);
    #[cfg(not(feature = "echo-client"))]
    if follow.is_some() {
        // Refusing loudly beats ignoring a flag: a node asked to follow and
        // silently playing its own programme is the hardest kind of wrong to
        // notice `[GOV-SRC-040]`.
        tracing::warn!("{} needs a build with `--features echo-client`; not following", opt::FOLLOW.name);
    }

    let config = Config {
        listener: db,
        library,
        depth: args.must_size(&opt::DEPTH),
        device: args.text(&opt::DEVICE).map(str::to_string),
        // This node's calibrated presentation offset, and the smallest in the
        // fleet `[LOG-ECHO-030]`. Both in frames, both calibrated rather than
        // read live `[LOG-P4-100]`. Absent means "fill the ring to capacity",
        // which is correct for a node running alone and for whichever node
        // holds the fleet's minimum `[GOV-SRC-040]`.
        echo_offset_frames: args.must_int(&opt::ECHO_OFFSET),
        echo_fleet_min_frames: args.must_int(&opt::ECHO_FLEET_MIN),
        echo_rate: args.must_int(&opt::ECHO_RATE) as u32,
        follow,
        // A guest backend, offered rather than assumed `[SPEC-BK-020]`. Lempi
        // still plays; MPD is attached and idle until a switch asks for it.
        mpd_addr: args.text(&opt::MPD).map(str::to_string),
        mpd_root: args.text(&opt::MPD_ROOT).map(str::to_string),
        web_port: Some(args.must_size(&opt::PORT) as u16),
        also_port_80: true,
        // The appliance's UI is for its LAN, and asks for nothing: these two
        // are a phone host's `[REQ-AND-160]`.
        web_loopback_only: false,
        web_secret: None,
        // An appliance's library is its own to annotate `[REQ-VIS-220]`; a
        // phone's is not `[REQ-AND-200]`.
        writes_beside_audio: true,
        backup: true,
        tag_scan: true,
        integrity_check: true,
        paused: args.has(&opt::PAUSED),
    };

    let port = config.web_port;
    let player = match Player::start(config) {
        Ok(p) => p,
        Err(e) => {
            // The same lines, in the same order, as before this was a library:
            // the session's own error, then the verdict.
            // Errors, not plain stderr: this is the line a failed start is
            // diagnosed from, and it read as info in the journal.
            tracing::error!("{e}");
            if matches!(e, StartError::Engine(_)) {
                tracing::error!("engine failed to start");
            }
            std::process::exit(1);
        }
    };
    // Returns when the engine or the web server stops -- the web server
    // ending ends the process, as it always did, and systemd restarts it.
    // Or when Windows has refused the output long enough to want a new
    // process, which only this file can start: it has the command line.
    loop {
        match player.wait() {
            Ended::Refused => match restarted_for_refusal_ago() {
                Some(ago) if ago < RESTART_SPACING => {
                    // Loudly, and once: the supervisor asks only once per
                    // process, so this player now waits the refusal out.
                    tracing::error!("output refused again {ago}s after a restart for the same \
                                     refusal; not restarting a second time -- waiting it out, \
                                     and a restart by hand is the remedy left [GDE-HST-230]");
                }
                _ => restart_for_refusal(player, port),
            },
            Ended::Engine | Ended::Web => return,
        }
    }
}

/// How long ago this process was started by [`restart_for_refusal`], if it was.
fn restarted_for_refusal_ago() -> Option<u64> {
    let at: u64 = std::env::var(REFUSAL_RESTART).ok()?.parse().ok()?;
    Some(unix_now().saturating_sub(at))
}

fn unix_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

/// Start this player again, with the same command line, and end this one
/// `[GDE-HST-230]`.
///
/// The resume point is written first, as the settings page's Restart writes it
/// `[REQ-VIS-250]`, and the new process resumes the play state with it
/// `[PI5-PWR-030]`. This one stops before the new one starts, because both
/// cannot hold the port; the port is confirmed free rather than assumed so.
/// Under systemd nothing reaches here -- the refusal is WASAPI's, and the
/// appliances are Linux.
fn restart_for_refusal(player: Player, port: Option<u16>) -> ! {
    let exe = match std::env::current_exe() {
        Ok(e) => e,
        Err(e) => {
            // Before anything is stopped: a player that cannot find itself
            // keeps playing what it can rather than exiting into nothing.
            tracing::error!("cannot restart for the refused output -- this executable cannot be found ({e}); \
                             waiting the refusal out [GDE-HST-230]");
            loop {
                if !matches!(player.wait(), Ended::Refused) {
                    std::process::exit(0);
                }
            }
        }
    };
    tracing::warn!("restarting: Windows refused this process its output [GDE-HST-230]");
    player.persist();
    if !player.shutdown() {
        tracing::warn!("the engine did not stop within 5s; restarting regardless");
    }
    if let Some(p) = port {
        let until = std::time::Instant::now() + std::time::Duration::from_secs(5);
        while std::net::TcpListener::bind(("0.0.0.0", p)).is_err() {
            if std::time::Instant::now() > until {
                tracing::warn!("port {p} is still held 5s after stopping; starting anyway, \
                                and the new process will say if it cannot listen");
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
    }
    match std::process::Command::new(&exe)
        .args(std::env::args_os().skip(1))
        .env(REFUSAL_RESTART, unix_now().to_string())
        .spawn()
    {
        Ok(child) => {
            tracing::warn!("restarted as process {} [GDE-HST-230]", child.id());
            std::process::exit(0);
        }
        Err(e) => {
            tracing::error!("could not start {} again: {e} -- the player has stopped [GDE-HST-230]",
                            exe.display());
            std::process::exit(1);
        }
    }
}
