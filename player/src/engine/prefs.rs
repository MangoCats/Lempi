//! The listener's settings the engine holds without mixing by them: how often
//! the position is saved, the suppression windows, the guest's sampling
//! interval, and what may be written beside the audio.
//!
//! Kept apart from `Engine`'s audio state so the two can be read separately.
//! They are loaded and saved in `persist.rs` and published with the snapshot.

pub(crate) struct Prefs {
    /// How often the resume point is written `[REQ-VIS-155]`. Configurable
    /// because every one of these writes lands on the appliance's most
    /// volatile partition `[PI-C-010]`.
    pub(crate) resume_save_ms: u64,
    /// How long a skipped passage stays out of selection `[SPEC-PLAY-050]`.
    pub(crate) skip_suppress_h: u64,
    /// How long a passage removed from the queue unheard stays out
    /// `[SPEC-PLAY-055]`.
    pub(crate) dequeue_suppress_h: u64,
    /// How often a guest backend should sample `status` `[SPEC-MPD-105]`. The
    /// local engine does not poll; it holds the value so one row owns every
    /// listener setting and the settings page has one place to read.
    pub(crate) sample_interval_ms: u64,
    /// Whether Lempi may write cue sheets into the music folder
    /// `[REQ-VIS-205]`. Held here for the same reason: one row, one page.
    pub(crate) cue_sheets: bool,
    /// `[REQ-VIS-210]`
    pub(crate) covers: bool,
    /// `[REQ-VIS-215]`
    pub(crate) lyrics_cache: bool,
    /// `[REQ-VIS-220]`
    pub(crate) lyrics_sidecar: bool,
}

impl Default for Prefs {
    fn default() -> Self {
        Self {
            resume_save_ms: crate::RESUME_SAVE_MS,
            skip_suppress_h: crate::SKIP_SUPPRESS_H,
            dequeue_suppress_h: crate::DEQUEUE_SUPPRESS_H,
            sample_interval_ms: crate::SAMPLE_INTERVAL_MS,
            cue_sheets: false,
            covers: false,
            lyrics_cache: false,
            lyrics_sidecar: false,
        }
    }
}
