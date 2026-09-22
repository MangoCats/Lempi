//! Lempi's selection engine.
//!
//! **What belongs here is decided by one question: does it help choose the next
//! passage, or does it help make a sound?** Choosing lives here; sounding lives
//! in `lempi-player`. The two have never been entangled -- this crate is where
//! that stops being a fact someone re-measures and starts being one cargo
//! enforces `[GDE-AND-045]`.
//!
//! The dependency list in `Cargo.toml` carries the reasoning. In short: three
//! crates (`rusqlite`, `serde`, `serde_json`) against the player's 135, and no
//! path from here to `cpal`, `symphonia`, `rubato`, `axum` or `tokio`.
//!
//! **Everything here is re-exported from `lempi_player` under its original
//! name**, so `crate::db::Library` and `crate::director::library::Director`
//! still resolve exactly as they did when these modules were files in that
//! crate. The split was deliberately arranged to cost the 99 existing call
//! sites nothing.
//!
//! ## What is *not* portable yet
//!
//! Two things in here reach outside the process, and a phone would find both:
//!
//! * [`relink::hash_encoded`] shells out to **ffmpeg**, and must keep doing so
//!   `[SPEC-RLK-080]` -- the stored `audio_md5` corpus is an ffmpeg artefact,
//!   so agreeing with it is the requirement, not a preference. There is already
//!   a [`relink::hasher_available`] guard, so a host without ffmpeg says so once
//!   rather than failing 5,705 times. A phone needs either an in-process
//!   ffmpeg-compatible hasher or bundles verified before they arrive.
//! * [`director::program`]'s UTC-offset sync calls `libc::localtime_r` on Unix
//!   and `powershell` on Windows `[SPEC-DIR-180]`, falling back to `None`
//!   elsewhere. Android and iOS are both Unix with a working `tm_gmtoff`, so
//!   this one is already portable; it is named here only because it is the
//!   other place this crate leaves its own process.

/// The tuning constants selection is decided by, re-exported at the root
/// because that is where the forty-odd `crate::SKIP_SUPPRESS_H` references in
/// this crate and the next one expect to find them.
mod settings;
pub use settings::*;

pub mod bundle;
pub mod db;
pub mod director;
pub mod fade;
pub mod queue;
pub mod relink;
/// What a file says about itself -- **the data half only**.
///
/// `Tags` and `Artwork` are what the library stores and the queue carries;
/// reading them out of an audio file is symphonia's job and stays in
/// `lempi_player::tags`, which re-exports these types so every existing
/// `crate::tags::Tags` still resolves. Splitting the module this way is what
/// keeps `symphonia` out of a crate whose business is selection.
pub mod tags;
