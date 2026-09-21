//! What each binary's command line *is*, as data.
//!
//! No parsing lives here and none lives in a binary either: [`super::Cli`] is
//! the only implementation in the repository, and each program below
//! contributes nothing but a table `[GDE-ARC-033]`. Adding an option is one
//! edit, to one table; the parser, the `--help` text, the unknown-option
//! refusal and the `--version` line all follow from it and are not written
//! anywhere twice.
//!
//! **Declared in the library, not in the binaries.** `mpd_*`, `fbui` and
//! `echoprobe` are behind `required-features`, so a spec living beside each
//! binary would be compiled only when that feature was on -- and
//! `[GDE-ECHO-384]` is this project's own account of what that costs: three
//! faults survived in `echo_client.rs` because no ordinary test run compiled
//! it. Every table here is compiled and checked by every `cargo test`,
//! whatever features are selected.
//!
//! The shared bases in [`common`] fix each recurring option's spelling and
//! kind once. A binary adapts one with `.needed()`, `.or(..)`, `.was(..)` or
//! `.saying(..)`; what a caller types is therefore identical across all
//! seventeen, which is the property the fleet's unit files depend on.

use super::{Cli, Opt};

/// Options that recur. The spelling and the kind are written here and nowhere
/// else, so `--library` means the same thing, spelled the same way, to every
/// program that takes one.
pub mod common {
    use crate::cli::Opt;

    /// The listener half: plays, preferences, programmes -- the one thing in
    /// the library nothing can rebuild `[REQ-LIB-160]`.
    pub const LISTENER: Opt = Opt::text(
        "--listener",
        "PATH",
        "the listener database: plays, preferences, programmes",
    ).short("lis");
    /// The catalogue half. Equal to the listener half on any installation
    /// that has not split `[IMPL-DBSPLIT-025]`.
    pub const LIBRARY: Opt = Opt::text(
        "--library",
        "PATH",
        "the catalogue: files, passages, flavor, cover art",
    ).short("l");
    pub const PORT: Opt = Opt::int("--port", "N", "TCP port to serve the web UI on").short("p");
    pub const DEPTH: Opt = Opt::int("--depth", "N", "how many passages to keep queued ahead").short("d");
    pub const DEVICE: Opt = Opt::text(
        "--device",
        "NAME",
        "output device, matched as a case-insensitive substring",
    ).short("dev");
    pub const ROOT: Opt = Opt::text("--root", "DIR", "MPD's music_directory").short("r");
    pub const ADDR: Opt = Opt::text("--addr", "HOST:PORT", "the MPD instance to talk to").short("a");
    pub const AUDIO_ROOT: Opt = Opt::text("--audio-root", "DIR", "where the audio files are").short("r");
    pub const URL: Opt = Opt::text("--url", "WS-URL", "the WebSocket to read snapshots from").short("u");
    pub const FILE: Opt = Opt::text("--file", "PATH", "the audio file to read").short("f");
    pub const START_MS: Opt = Opt::int("--start-ms", "MS", "where the passage starts in the file").short("s");
    pub const END_MS: Opt =
        Opt::int("--end-ms", "MS", "where it ends; absent means the end of the file").short("e");
    pub const APPLY: Opt = Opt::flag("--apply", "make the changes; without it nothing is written")
        .short("a")
        .not_from_env();
    pub const WRITE: Opt =
        Opt::flag("--write", "record the results; without it nothing is written")
            .short("w")
            .not_from_env();
    pub const RUN_FOR: Opt = Opt::real(
        "--for",
        "SECONDS",
        "stop after this long; absent means run until interrupted",
    ).short("f");
    /// Seconds, as a real number: `mpd_fill`, `mpd_direct` and `mpd_watch`.
    /// **The default is derived, not retyped** `[GDE-ARC-033]`. Its sibling
    /// `INTERVAL_MS` already took `default_sample_interval_ms!()`; this one
    /// carried no default at all, so `must_real` panicked on startup for
    /// every binary built on it -- `mpd_watch` always, `mpd_fill` and
    /// `mpd_direct` whenever the listener database had no saved settings
    /// `[GDE-CLI-115]`. The old code said `unwrap_or(5.0)`.
    ///
    /// Seconds here, milliseconds there, one source: the macro is the
    /// millisecond figure, so the seconds default is that divided by a
    /// thousand and cannot drift from it.
    pub const INTERVAL_S: Opt = Opt::real(
        "--interval",
        "SECONDS",
        "how often to sample what is playing, in SECONDS",
    )
        .short("i")
        .or(crate::default_sample_interval_seconds!());
    /// **A different name, because it is a different unit** `[GDE-CLI-110]`.
    /// `mpd_session` has always taken milliseconds where the other three take
    /// seconds. A long name means one thing across the project, and the
    /// environment variable is derived from it, so the two cannot share
    /// `--interval`: this one says which unit it is and derives cleanly to
    /// `LEMPI_INTERVAL_MS`.
    pub const INTERVAL_MS: Opt = Opt::int(
        "--interval-ms",
        "MS",
        "how often to sample what is playing, in MILLISECONDS",
    )
    .short("i");
    pub const SEED: Opt =
        Opt::int("--seed", "N", "fix the random seed so a run can be repeated").short("s");
}

/// The player `[REQ-VIS-140]`.
pub mod lempi {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LISTENER: Opt = common::LISTENER.needed().was(0).bootstrap();
    pub const LIBRARY: Opt =
        common::LIBRARY.saying("the catalogue half, where the database has been split")
        .short("lib").bootstrap();
    pub const PORT: Opt =
        // No retired name to answer to any more: the one this carried was
        // dropped once no deploy script or unit in the fleet exported it.
        // `Opt::env_formerly` stays as API for the next one `[GDE-CLI-040]`,
        // and so does its rule -- the string it holds is the name being
        // RETIRED, so a rename pass must never rewrite it. Doing so makes the
        // option declare itself as formerly itself, which silently drops the
        // compatibility the shim exists to provide.
        common::PORT.or(crate::default_port!());
    pub const DEPTH: Opt =
        common::DEPTH.or(crate::default_queue_depth!()).setting("queue_depth");
    pub const DEVICE: Opt = common::DEVICE;
    pub const ECHO_OFFSET: Opt = Opt::int(
        "--echo-offset-frames",
        "N",
        "this node's calibrated presentation offset; absent means fill the ring",
    )
    .or("0")
    .short("eo");
    pub const ECHO_FLEET_MIN: Opt = Opt::int(
        "--echo-fleet-min-frames",
        "N",
        "the smallest presentation offset in the fleet",
    )
    .or("0")
        .short("efm");
    pub const ECHO_RATE: Opt =
        Opt::int("--echo-rate", "HZ", "this node's output sample rate").or("44100")
        .short("er");
    /// The one option whose layer-2 relationship is the other way round: the
    /// flag *seeds* the stored setting rather than overriding it
    /// `[SPEC-ECHO-010]`, so it is deliberately NOT declared `.setting(..)`.
    /// A node told to follow by a listener must not be un-told by a unit file
    /// that still carries the flag from its first setup.
    pub const FOLLOW: Opt = Opt::text(
        "--follow",
        "HOST",
        "seed the stored 'follow this node' setting; absent means it plays its own",
    )
    .short("f")
        .not_from_env();
    pub const MPD: Opt = Opt::text("--mpd", "HOST:PORT", "attach an MPD instance as a guest backend")
        .short("m");
    pub const MPD_ROOT: Opt =
        Opt::text("--mpd-root", "DIR", "that MPD's music_directory")
        .short("mr");
    /// **Promised by `[IMPL-AUD-010]` and never implemented.** That document
    /// has told every appliance builder since to "identify what exists
    /// first: `aplay -l`, and after setup `lempi --list-devices`", and no
    /// binary ever read the flag -- found 2026-09-20 by the guard that
    /// checks the repository's own invocations against these tables. The
    /// library call it needed, `output::list_devices`, already existed.
    pub const LIST_DEVICES: Opt = Opt::flag(
        "--list-devices",
        "print the output devices this machine offers, and stop",
    )
        .short("ld")
        .stops()
        .not_from_env();

    pub const SPEC: Cli = Cli {
        program: "lempi",
        summary: "continuous radio with a web UI",
        opts: &[
            LISTENER, LIBRARY, PORT, DEPTH, DEVICE, LIST_DEVICES, ECHO_OFFSET,
            ECHO_FLEET_MIN, ECHO_RATE, FOLLOW, MPD, MPD_ROOT,
        ],
        notes: &[],
    };
}

/// The headless sibling of `lempi` `[SPEC009]`.
pub mod station {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LISTENER: Opt = common::LISTENER.needed().was(0).bootstrap();
    pub const COUNT: Opt =
        Opt::int("--count", "N", "how many passages to select").or("5").was(1)
        .short("c");
    pub const LIST: Opt = Opt::flag("--list", "print the programme and play nothing")
        .short("l")
        .not_from_env();

    pub const SPEC: Cli = Cli {
        program: "station",
        summary: "play passages from the library on the terminal",
        opts: &[LISTENER, COUNT, LIST],
        notes: &["Set LEMPI_NULL_OUTPUT=1 for a discard sink (headless/CI)."],
    };
}

/// Play arbitrary files, for audio not in the library.
pub mod play {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const FILE: Opt = common::FILE.needed().was(0);
    pub const START_MS: Opt = common::START_MS.or("0").was(1);
    pub const END_MS: Opt = common::END_MS.was(2);
    pub const FILE2: Opt =
        Opt::text("--file2", "PATH", "a second file, to hear the crossfade between them").was(3)
        .short("f2");
    pub const START2_MS: Opt =
        Opt::int("--start2-ms", "MS", "where the second passage starts").or("0").was(4)
        .short("s2");
    pub const END2_MS: Opt = Opt::int("--end2-ms", "MS", "where the second passage ends").was(5)
        .short("e2");
    pub const LEAD_S: Opt = Opt::real(
        "--lead-s",
        "SECONDS",
        "overlap between the two; needed on both sides, so one number sets both",
    )
    .or("0")
    .was(6)
    .short("l");

    pub const SPEC: Cli = Cli {
        program: "play",
        summary: "play one file, or two with a crossfade between them",
        opts: &[FILE, START_MS, END_MS, FILE2, START2_MS, END2_MS, LEAD_S],
        notes: &["Set LEMPI_NULL_OUTPUT=1 for a discard sink (headless/CI)."],
    };
}

/// The acceptance gate for bounded decode `[REQ-AUD-110]`.
pub mod memcheck {
    use super::common;
    use crate::cli::Cli;

    pub const FILE: crate::cli::Opt = common::FILE.needed().was(0);
    pub const START_MS: crate::cli::Opt = common::START_MS.or("0").was(1);
    pub const END_MS: crate::cli::Opt = common::END_MS.was(2);

    pub const SPEC: Cli = Cli {
        program: "memcheck",
        summary: "decode one passage through a fixed buffer and report peak RSS",
        opts: &[FILE, START_MS, END_MS],
        notes: &[],
    };
}

/// What it costs to rebuild the Program Director in place `[SPEC009]`.
pub mod dircheck {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LISTENER: Opt = common::LISTENER.needed().was(0).bootstrap();
    pub const LIBRARY: Opt = common::LIBRARY.was(1)
        .short("lib").bootstrap();

    pub const SPEC: Cli = Cli {
        program: "dircheck",
        summary: "measure a Director load: how long it takes and what two cost at once",
        opts: &[LISTENER, LIBRARY],
        notes: &[],
    };
}

/// What flavor distance does on a real library `[SPEC-FD-070]`.
pub mod flavorcheck {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LIBRARY: Opt = common::LIBRARY
        .saying("the catalogue, which is where `flavor` lives")
        .needed()
        .was(0).bootstrap();
    pub const SAMPLES: Opt =
        Opt::int("--samples", "N", "how many pairs to sample for the distribution")
            .or("20000")
            .was(1)
        .short("s");

    pub const SPEC: Cli = Cli {
        program: "flavorcheck",
        summary: "report the flavor schema, distance distribution and nearest neighbours",
        opts: &[LIBRARY, SAMPLES],
        notes: &[],
    };
}

/// Read every file's own tags into the library `[REQ-VIS-180]`.
pub mod tagscan {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LIBRARY: Opt = common::LIBRARY.needed().was(0).bootstrap();
    pub const ALL: Opt =
        Opt::flag("--all", "forget what is known and read every file again")
        .short("a")
        .not_from_env();

    pub const SPEC: Cli = Cli {
        program: "tagscan",
        summary: "read file tags into the library; the player also does this at startup",
        opts: &[LIBRARY, ALL],
        notes: &[],
    };
}

/// Bind a library to this machine's paths `[SPEC012]`.
pub mod relink {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LIBRARY: Opt = common::LIBRARY.needed().was(0).bootstrap();
    pub const AUDIO_ROOT: Opt = common::AUDIO_ROOT.needed().was(1);
    pub const APPLY: Opt = common::APPLY;
    /// `-Q`, not `-q`. `-q` is conventionally *quiet* everywhere in Unix,
    /// and giving it to `--quick` here would be the one surprise a reader
    /// already knows the answer to `[GDE-CLI-020]`. Reserved unused
    /// alongside `-v`/`--verbose`, so that when any of these tools grows a
    /// quiet mode the obvious letter is free for it.
    pub const QUICK: Opt = Opt::flag(
        "--quick",
        "do not hash files already bound -- THIS VERIFIES NOTHING",
    )
        .short("Q")
        .not_from_env();
    /// Undocumented until 2026-09-20: the binary read `--report` and the
    /// usage string it printed did not mention it, which is the second copy
    /// going stale `[GDE-ARC-033]`. Declared now, so it is in `--help` by
    /// construction.
    pub const REPORT: Opt = Opt::text(
        "--report",
        "FILE",
        "write the whole outcome list here; the console output is truncated",
    )
        .short("o");

    pub const SPEC: Cli = Cli {
        program: "relink",
        summary: "rebind a library to the audio under this machine's paths, and verify it",
        opts: &[LIBRARY, AUDIO_ROOT, APPLY, QUICK, REPORT],
        notes: &["Needs ffmpeg on PATH: it hashes each file's encoded audio stream."],
    };
}

/// Receive a bundle from Vipunen `[SPEC014]`.
pub mod import_bundle {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LIBRARY: Opt = common::LIBRARY.needed().was(0).bootstrap();
    pub const BUNDLE: Opt =
        Opt::text("--bundle", "DIR", "the bundle directory to import").was(1)
        .short("b");
    pub const AUDIO_ROOT: Opt = common::AUDIO_ROOT
        .saying("where the audio is, if not the bundle's own audio/");
    pub const APPLY: Opt = common::APPLY;
    pub const INVENTORY: Opt = Opt::flag(
        "--inventory",
        "print the audio_md5 of everything this library holds, and stop",
    )
        .short("i")
        .not_from_env();

    pub const SPEC: Cli = Cli {
        program: "import_bundle",
        summary: "import a Vipunen bundle, in one transaction or not at all",
        opts: &[LIBRARY, BUNDLE, AUDIO_ROOT, APPLY, INVENTORY],
        notes: &["--bundle is required unless --inventory is given."],
    };
}

/// An echo node's side of the wire, observing only `[GDE-ECHO-310]`.
pub mod echoprobe {
    use crate::cli::{Cli, Opt};

    /// **`--master-url`, not `--url`.** `fbui`'s `--url` is the local
    /// player's own socket; this is a *different node's*. Pointing a
    /// framebuffer UI at a master, or a probe at itself, are both quietly
    /// wrong, so the two do not share a name `[GDE-CLI-110]`.
    pub const URL: Opt =
        Opt::text("--master-url", "WS-URL", "the master's snapshot WebSocket to follow")
            .needed()
            .was(0)
            .short("u");
    pub const OFFSET_FRAMES: Opt = Opt::int(
        "--offset-frames",
        "N",
        "this node's measured presentation offset (bose 2043, lempipi 15676)",
    )
    .or("0")
    .was(1)
        .short("o");
    pub const RATE: Opt =
        Opt::int("--rate", "HZ", "this node's output sample rate").or("44100").was(2)
        .short("r");

    pub const SPEC: Cli = Cli {
        program: "echoprobe",
        summary: "follow a master and print what an echo node would do; starts nothing",
        opts: &[URL, OFFSET_FRAMES, RATE],
        notes: &[],
    };
}

/// Is the presentation offset readable on this node? `[GDE-ECHO-540]`
pub mod delayprobe {
    use crate::cli::{Cli, Opt};

    /// The same string `delayprobe`'s own `DEFAULT_DEVICE` used to hold. It is
    /// declared here now, so the value and the sentence describing it are one
    /// thing `[GDE-ARC-033]`.
    /// **`--alsa-device`, not `--device`.** `lempi`'s `--device` is a
    /// case-insensitive substring of a cpal device name; this is a raw ALSA
    /// device string, and neither value works as the other. One long name,
    /// one meaning `[GDE-CLI-110]`.
    pub const DEVICE: Opt = Opt::text(
        "--alsa-device",
        "NAME",
        "the raw ALSA device to open",
    )
    .or("hw:CARD=sndrpihifiberry,DEV=0")
    .was(0)
    .short("d");
    pub const SECONDS: Opt =
        Opt::int("--seconds", "N", "how long each phase plays").or("6").was(1)
        .short("s");

    pub const SPEC: Cli = Cli {
        program: "delayprobe",
        summary: "read the ALSA presentation delay five ways, raw and through cpal",
        opts: &[DEVICE, SECONDS],
        notes: &["The device is exclusive: stop lempi before running this."],
    };
}

/// The native framebuffer/touch UI `[SPEC036]`.
pub mod fbui {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const URL: Opt = common::URL
        .saying("the player's WebSocket")
        .env("LEMPI_FBUI_URL")
        .or(concat!("ws://127.0.0.1:", crate::default_port!(), "/ws"))
        .was(0);
    pub const CALIBRATE: Opt = Opt::flag(
        "--calibrate",
        "run touch calibration even if a saved one exists",
    )
        .short("c")
        .not_from_env();

    pub const SPEC: Cli = Cli {
        program: "fbui",
        summary: "the framebuffer and touch UI for a small SPI panel",
        opts: &[URL, CALIBRATE],
        notes: &[],
    };
}

/// Stage 0: can Lempi name a passage in MPD's terms? `[IMPL-MPD-010]`
pub mod mpd_map {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const LIBRARY: Opt = common::LIBRARY.needed().was(0).bootstrap();
    pub const ROOT: Opt = common::ROOT.needed().was(1);
    pub const ADDR: Opt = common::ADDR.or("127.0.0.1:6600").was(2);
    pub const URIS: Opt = Opt::text(
        "--uris",
        "FILE",
        "run the ladder against a captured URI list instead of a live MPD",
    )
        .short("u");

    pub const SPEC: Cli = Cli {
        program: "mpd_map",
        summary: "walk the resolution ladder and report how far each row got; writes nothing",
        opts: &[LIBRARY, ROOT, ADDR, URIS],
        notes: &[],
    };
}

/// What MPD reports about what is playing `[SPEC-MPD-105]`.
pub mod mpd_watch {
    use super::common;
    use crate::cli::Cli;

    pub const ADDR: crate::cli::Opt = common::ADDR.or("127.0.0.1:6600").was(0);
    pub const INTERVAL: crate::cli::Opt =
        common::INTERVAL_S.setting_scaled("sample_interval_ms", 0.001);
    pub const RUN_FOR: crate::cli::Opt = common::RUN_FOR;
    pub const LIBRARY: crate::cli::Opt = common::LIBRARY
        .saying("the catalogue, to take real passage durations from")
        .formerly(&["--db"]).bootstrap();
    pub const ROOT: crate::cli::Opt = common::ROOT;

    pub const SPEC: Cli = Cli {
        program: "mpd_watch",
        summary: "sample what MPD is playing and judge each song heard, skipped or removed",
        opts: &[ADDR, INTERVAL, RUN_FOR, LIBRARY, ROOT],
        notes: &[
            "--library and --root go together: without both, MPD's own duration estimate is used.",
        ],
    };
}

/// Keep MPD's queue full by uniform random selection `[IMPL-MPD-030]`.
pub mod mpd_fill {
    use super::common;
    use crate::cli::Cli;

    pub const ADDR: crate::cli::Opt = common::ADDR.or("127.0.0.1:6600").was(0);
    pub const LISTENER: crate::cli::Opt = common::LISTENER.needed().formerly(&["--db"])
        .short("l").bootstrap();
    pub const ROOT: crate::cli::Opt = common::ROOT.needed();
    pub const DEPTH: crate::cli::Opt =
        common::DEPTH.or(crate::default_queue_depth!()).setting("queue_depth");
    // Seconds on the command line, milliseconds in the store. The scale is
    // declared here rather than converted in the binary, so layer 2 still
    // reaches this option and the binary still implements no precedence
    // `[GDE-CLI-090]`.
    pub const INTERVAL: crate::cli::Opt = common::INTERVAL_S
        .setting_scaled("sample_interval_ms", 0.001);
    pub const RUN_FOR: crate::cli::Opt = common::RUN_FOR;
    pub const SEED: crate::cli::Opt = common::SEED.or("11400714819323198485");

    pub const SPEC: Cli = Cli {
        program: "mpd_fill",
        summary: "top MPD's queue up to depth with random picks; the shape without a Director",
        opts: &[ADDR, LISTENER, ROOT, DEPTH, INTERVAL, RUN_FOR, SEED],
        notes: &[],
    };
}

/// `Director::decide` driving MPD directly `[IMPL-MPD-050]`.
pub mod mpd_direct {
    use super::common;
    use crate::cli::Cli;

    pub const ADDR: crate::cli::Opt = common::ADDR.or("127.0.0.1:6600").was(0);
    pub const LISTENER: crate::cli::Opt = common::LISTENER.needed().formerly(&["--db"]).bootstrap();
    pub const ROOT: crate::cli::Opt = common::ROOT.needed();
    pub const LIBRARY: crate::cli::Opt = common::LIBRARY
        .short("lib").bootstrap();
    pub const DEPTH: crate::cli::Opt =
        common::DEPTH.or(crate::default_queue_depth!()).setting("queue_depth");
    // Seconds on the command line, milliseconds in the store. The scale is
    // declared here rather than converted in the binary, so layer 2 still
    // reaches this option and the binary still implements no precedence
    // `[GDE-CLI-090]`.
    pub const INTERVAL: crate::cli::Opt = common::INTERVAL_S
        .setting_scaled("sample_interval_ms", 0.001);
    pub const RUN_FOR: crate::cli::Opt = common::RUN_FOR;
    pub const SEED: crate::cli::Opt = common::SEED;
    pub const WRITE: crate::cli::Opt =
        common::WRITE.saying("write plays, skips and dequeues back to the library");

    pub const SPEC: Cli = Cli {
        program: "mpd_direct",
        summary: "the Program Director choosing for MPD, with no refill loop reimplemented",
        opts: &[ADDR, LISTENER, ROOT, LIBRARY, DEPTH, INTERVAL, RUN_FOR, SEED, WRITE],
        notes: &[],
    };
}

/// Lempi's own `Session`, playing MPD `[SPEC018]`.
pub mod mpd_session {
    use super::common;
    use crate::cli::{Cli, Opt};

    pub const ADDR: Opt = common::ADDR.or("127.0.0.1:6600").was(0);
    pub const LISTENER: Opt = common::LISTENER.needed().formerly(&["--db"])
        .short("l").bootstrap();
    pub const ROOT: Opt = common::ROOT.needed();
    pub const DEPTH: Opt =
        common::DEPTH.or(crate::default_queue_depth!()).setting("queue_depth");
    /// **`--interval-ms`, not `--interval`.** Three other binaries take
    /// `--interval` in SECONDS; this one takes milliseconds, and a long name
    /// means one thing across the project `[GDE-CLI-110]`. The unit is in
    /// the name, where it cannot be missed, and the variable derives to
    /// `LEMPI_INTERVAL_MS` with no override needed.
    pub const INTERVAL: Opt = common::INTERVAL_MS
        .or(crate::default_sample_interval_ms!())
        .setting("sample_interval_ms");
    pub const RUN_FOR: Opt = common::RUN_FOR;
    pub const WRITE: Opt = common::WRITE;
    pub const THEN_HANDOFF: Opt = Opt::flag(
        "--then-handoff",
        "finish by handing playback back to the local engine, which ends the session",
    )
        .short("t")
        .not_from_env();

    pub const SPEC: Cli = Cli {
        program: "mpd_session",
        summary: "Lempi's own Session with an MPD backend instead of an Engine",
        opts: &[ADDR, LISTENER, ROOT, DEPTH, INTERVAL, RUN_FOR, WRITE, THEN_HANDOFF],
        notes: &[],
    };
}

/// Every command line in the crate.
///
/// Iterated by the tests, so a property asserted about one binary's grammar is
/// asserted about all seventeen. Pinned against `Cargo.toml`'s own `[[bin]]`
/// list and `src/bin/` by `every_binary_has_exactly_one_spec`, so a binary
/// added without a table here fails the build's tests rather than shipping the
/// old hand-rolled parsing `[GDE-ARC-031]`.
pub const ALL: &[&Cli] = &[
    &lempi::SPEC,
    &station::SPEC,
    &play::SPEC,
    &memcheck::SPEC,
    &dircheck::SPEC,
    &flavorcheck::SPEC,
    &tagscan::SPEC,
    &relink::SPEC,
    &import_bundle::SPEC,
    &echoprobe::SPEC,
    &delayprobe::SPEC,
    &fbui::SPEC,
    &mpd_map::SPEC,
    &mpd_watch::SPEC,
    &mpd_fill::SPEC,
    &mpd_direct::SPEC,
    &mpd_session::SPEC,
];

/// The spec a given program name has, for the guards that read a command line
/// out of a file and have only the program's name to go on.
pub fn by_program(name: &str) -> Option<&'static Cli> {
    ALL.iter().copied().find(|c| c.program == name)
}

/// Every option of every binary, for a guard that has a name and no context.
pub fn any_option(name: &str) -> Option<&'static Opt> {
    ALL.iter().find_map(|c| c.opts.iter().find(|o| o.name == name))
}
