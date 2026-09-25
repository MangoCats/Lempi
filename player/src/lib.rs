//! Lempi audio player.
//!
//! The single hard rule this crate exists to enforce: **audio is never decoded
//! whole** `[GDE-FBD-010]`. Lempi v1 called `miniaudio.decode_file()` and pulled
//! entire files into memory; this library's largest file is 244.9 minutes, which
//! is ~2.6 GB decoded at int16 and ~5.2 GB at f32 `[GDE-V1-030]`. That is not
//! slow on a 512 MB Pi Zero 2W, it is impossible.
//!
//! Everything here is built around a fixed-capacity buffer per passage
//! (~15 s, ~5.3 MB at 44.1 kHz stereo f32) `[GDE-ARC-050]`, so memory is a
//! function of how many passages are open, never of how long they are.

/// What this build is, for anything that has to say so `[REQ-VIS-200]`.
///
/// Crate version and the commit it was built from. `+dirty` means the tree had
/// uncommitted changes, so the hash names where it started rather than what was
/// compiled — a distinction that matters exactly when someone is asking why a
/// change is not there.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const GIT: &str = env!("LEMPI_GIT");

/// The branch, commit date, and commit subject the build came from, and how
/// many files the tree had uncommitted at build time -- the same fields
/// Vipunen's own `/system` page already shows `[SPEC-SUI-210..213]`, so the
/// Settings page can say which build this is the same way from either side
/// of the handoff.
pub const BRANCH: &str = env!("LEMPI_BRANCH");
pub const COMMIT_DATE: &str = env!("LEMPI_COMMIT_DATE");
pub const COMMIT_SUBJECT: &str = env!("LEMPI_COMMIT_SUBJECT");
pub const DIRTY_FILES: &str = env!("LEMPI_DIRTY_FILES");

/// `0.1.0 (45b74a6952de)`, or with `+dirty` when the tree was not clean.
pub fn build_id() -> String {
    format!("{VERSION} ({GIT})")
}

/// **The selection engine, which is a separate crate** `[GDE-AND-045]`.
///
/// `bundle`, `db`, `director`, `fade`, `queue` and `relink` were files in this
/// crate until 2026-09-22 and are now `lempi-core`, re-exported here under
/// their original names. That is not cosmetic: every one of the 99 places in
/// this crate that says `crate::db::…` or `crate::director::…` still says it,
/// and the split cost them nothing.
///
/// What it does cost is the thing worth buying. `lempi-core` names three
/// dependencies, and `cpal`, `symphonia`, `rubato`, `axum` and `tokio` are not
/// reachable from any of them — so the Director's independence from the audio
/// path, previously a property re-established by reading imports on 2026-08-20,
/// 2026-09-02 and 2026-09-22, is now something cargo refuses to let anyone
/// break. See `player/core/Cargo.toml` for the full reasoning.
pub use lempi_core::{bundle, db, director, fade, queue, relink};

/// The listener-setting defaults and their bounds, which moved with the store
/// that reads them `[GDE-ARC-033]`.
///
/// Re-exported at this crate's root because that is where every existing
/// `crate::SKIP_SUPPRESS_H`, `crate::QUEUE_DEPTH` and `crate::RESUME_SAVE_MS`
/// expects them, in the web layer, the CLI specs and the engine alike. See
/// `lempi_core::settings` for why the two `DEFAULT_*` strings are strings.
pub use lempi_core::{
    as_number, BROWSE_LIMIT, DEFAULT_QUEUE_DEPTH, DEFAULT_SAMPLE_INTERVAL_MS,
    DEQUEUE_SUPPRESS_H, DEQUEUE_SUPPRESS_MAX_H, DEQUEUE_SUPPRESS_MIN_H, QUEUE_DEPTH,
    QUEUE_DEPTH_MAX, QUEUE_DEPTH_MIN, RESUME_SAVE_MAX_MS, RESUME_SAVE_MIN_MS,
    RESUME_SAVE_MS, SAMPLE_INTERVAL_MAX_MS, SAMPLE_INTERVAL_MIN_MS, SAMPLE_INTERVAL_MS,
    SKIP_FADE_MAX_MS, SKIP_FADE_MS, SKIP_LEAD_MAX_MS, SKIP_LEAD_MIN_MS, SKIP_LEAD_MS,
    SKIP_SUPPRESS_H, SKIP_SUPPRESS_MAX_H, SKIP_SUPPRESS_MIN_H,
};

pub mod backup;
/// One command line, seventeen binaries `[GDE-CLI-010]`. The only argument
/// parser in the repository; each binary contributes a table of options and
/// no parsing code of its own.
pub mod cli;
/// Cue sheets, so a guest can name a passage inside a capture [SPEC-MPD-056].
pub mod covers;
pub mod cue;
pub mod decoder;
pub mod echo;
/// Following a master `[GDE-ECHO-330]`. Behind `echo-client` because it names
/// `tokio-tungstenite` directly; the crate is already in the graph via axum's
/// own `ws` feature, so the gate costs a build nothing it was not already
/// paying.
#[cfg(feature = "echo-client")]
pub mod echo_client;
pub mod engine;
/// Where diagnostics go, and how they are written `[GDE-HST-070]`. The
/// library emits through `tracing`; this is the one place a line is written.
pub mod logging;
/// Per-song lyrics where a client will find them `[SPEC-LYR-070]`.
pub mod lyrics_cache;
/// Lyrics beside the audio, for a client that reads the music folder
/// `[SPEC-LYR-080]`.
pub mod lyrics_sidecar;
pub mod mixer;
/// The MPD protocol client `[SPEC-MPD-070]`. `std::net` and nothing else, and
/// absent entirely from a build that did not ask for it.
#[cfg(feature = "mpd")]
pub mod mpd;

/// MPD driven through the [`playback::Playback`] seam `[SPEC-BK-020]`.
#[cfg(feature = "mpd")]
pub mod mpd_backend;
pub mod output;
pub mod path;
pub mod playback;
/// One shape for what a folder-writing run did `[PI3-API-030]`.
pub mod report;
pub mod bluetooth;
pub mod sink;
pub mod session;

/// Holding two backends and exchanging which one sounds [SPEC-BK-030].
pub mod switch;
pub mod tags;
pub mod web;
pub mod resample;
pub mod scrobble;

/// Frames of audio buffered per passage. 15 s at 44.1 kHz.
///
/// Sized from McRhythm's measured design `[GDE-MCR-020]`: 44100 * 2ch * 4 bytes
/// * 15 s = 5.29 MB, against a =<150 MB total-process target `[REQ-HW-100]`.
pub const BUFFER_FRAMES: usize = 44_100 * 15;

/// How much of the queue a display is sent.
///
/// Not how much is queued -- the Director keeps whatever depth it was given.
/// This is how far ahead anyone can usefully read, and it bounds the size of a
/// snapshot that goes out twice a second to every connected browser.
pub const QUEUE_SHOWN: usize = 12;

/// Free space below which a passage's decoder is topped up again, in frames.
///
/// Small enough that the check is cheap and the buffer never sits nearly empty,
/// large enough that a decode yields more than it costs to ask for.
pub const DECODE_TOPUP_FRAMES: usize = 4096;

/// How many decode attempts a skip will make to fill the incoming passage
/// before it cuts the ring `[PI-CHR-075]`.
///
/// One attempt yields `DECODE_TOPUP_FRAMES`, so this covers roughly two
/// seconds at 44.1 kHz — comfortably more than the 1.5 s overlay a default
/// fade asks for, and bounded because the listener is waiting on it.
pub const TOPUP_TRIES_BEFORE_CUT: usize = 24;

/// Peak resident memory of this process, in bytes.
///
/// Deliberately dependency-free: the memory bound is the property under test,
/// so measuring it should not itself pull in allocations or crates.
pub fn peak_rss_bytes() -> Option<u64> {
    #[cfg(target_os = "linux")]
    {
        let s = std::fs::read_to_string("/proc/self/status").ok()?;
        for line in s.lines() {
            if let Some(rest) = line.strip_prefix("VmHWM:") {
                let kb: u64 = rest.split_whitespace().next()?.parse().ok()?;
                return Some(kb * 1024);
            }
        }
        None
    }
    #[cfg(windows)]
    {
        // PROCESS_MEMORY_COUNTERS.PeakWorkingSetSize via psapi, declared inline
        // rather than taking a windows-sys dependency for one struct.
        #[repr(C)]
        #[derive(Default)]
        struct Pmc {
            cb: u32,
            page_fault_count: u32,
            peak_working_set_size: usize,
            working_set_size: usize,
            quota_peak_paged_pool_usage: usize,
            quota_paged_pool_usage: usize,
            quota_peak_non_paged_pool_usage: usize,
            quota_non_paged_pool_usage: usize,
            pagefile_usage: usize,
            peak_pagefile_usage: usize,
        }
        // GetProcessMemoryInfo lives in psapi; kernel32 alone does not export it.
        #[link(name = "kernel32")]
        extern "system" {
            fn GetCurrentProcess() -> isize;
        }
        #[link(name = "psapi")]
        extern "system" {
            fn GetProcessMemoryInfo(h: isize, c: *mut Pmc, cb: u32) -> i32;
        }
        let mut pmc = Pmc { cb: std::mem::size_of::<Pmc>() as u32, ..Default::default() };
        let ok = unsafe {
            GetProcessMemoryInfo(GetCurrentProcess(), &mut pmc, pmc.cb)
        };
        if ok != 0 { Some(pmc.peak_working_set_size as u64) } else { None }
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    {
        None
    }
}
