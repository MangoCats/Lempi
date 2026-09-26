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
use lempi_player::host::{Config, Player, StartError};

fn main() {
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
    // **Bootstrap, and then everything else** `[GDE-CLI-095]`. These two are
    // declared `.bootstrap()`, so they resolve from the command line, the
    // environment and their defaults only -- they name the database the
    // stored settings live in, and cannot be answered by it.
    let db = PathBuf::from(args.need(&opt::LISTENER));
    // The catalog-side file. Equal to `db` on every installation that
    // hasn't split `[IMPL-DBSPLIT-025]`, `[IMPL-DBSPLIT-030]` -- lempi02w,
    // once split, is the first to ever pass a genuinely different one.
    let library = args.text(&opt::LIBRARY).map(PathBuf::from).unwrap_or_else(|| db.clone());

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
        backup: true,
        tag_scan: true,
    };

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
    player.wait();
}
