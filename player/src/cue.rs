//! Cue sheets, so a guest can name a passage inside a capture `[SPEC-MPD-056]`.
//!
//! A DAO capture carries one set of album-level tags, so MPD names every
//! passage inside it after the file — 34.1% of this library `[SPEC-MPD-052]`.
//! MPD *does* read cue sheets, and exposes each cue track as its own song with
//! its own title. Writing one beside a capture names every passage in it, for
//! every client, with no sticker support required.
//!
//! **This writes into the listener's music folder**, which is why it is off by
//! default and asked for explicitly `[REQ-VIS-205]`. Nothing else in Lempi puts
//! a file there.
//!
//! Two things measured before choosing this shape:
//!
//! * MPD applies a cue track's boundaries **as a range** and reports the right
//!   title, so `rangeid` is neither needed nor safe — it overwrites the range
//!   with offsets into the *file*.
//! * MPD **ignores `INDEX 00`**, so a track ends where the next one begins.
//!   Against radio spans that is a median 4.8 s of the next track's lead-in.
//!   The backend already ends an unhonoured span itself `[SPEC-MPD-096]`, and
//!   that machinery bounds this the same way.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use rusqlite::Connection;

use crate::report::Written;

/// One line of a sheet: where the passage starts, what it is called, and who
/// performs it. Named because four functions here take a slice of them.
type Track = (i64, String, String);

/// Why a capture might get no sheet.
const ONE_PASSAGE: &str = "needed no sheet";
const NOT_OURS: &str = "left as they were";

/// `MM:SS:FF`, 75 frames to the second, as the format requires.
fn cue_time(ms: i64) -> String {
    let ms = ms.max(0);
    let secs = ms / 1000;
    let frames = (((ms % 1000) * 75 + 500) / 1000).min(74);
    format!("{:02}:{:02}:{:02}", secs / 60, secs % 60, frames)
}

/// Cue strings are double-quoted with no escape, so a quote inside one ends it.
fn cue_str(s: &str) -> String {
    s.replace('"', "'").replace(['\r', '\n'], " ")
}

/// What the standard name for a record by more than one artist is
/// `[SPEC-MPD-058]`.
///
/// Matched literally by the client — Cantata compares against
/// `QLatin1String("Various Artists")` — and saying it is what puts a compilation
/// on the path that looks up each *track's* artist rather than the album's.
const VARIOUS: &str = "Various Artists";

/// How much of a record one artist must perform to be *its* artist
/// `[SPEC-MPD-058]`.
///
/// **Measured, because both obvious rules failed.** Requiring unanimity called
/// *Goodbye Yellow Brick Road* a compilation, on the strength of a few passages
/// linked to the wrong recording. Requiring a simple majority called *Moana* a
/// Mark Mancina record, because his score cues outnumber the songs.
///
/// Across all 191 captures the dominant artist's share is almost never in
/// doubt — **182 of them are unanimous** — and only five sit in between:
///
/// | share | artists | capture | is really |
/// | ---: | ---: | :--- | :--- |
/// | 44% | 3 | Caddyshack | a compilation |
/// | 61% | 14 | Moana | a compilation |
/// | 83% | 9 | Goodbye Yellow Brick Road | one artist's |
/// | 88% | 3 | Exodus 40 | one artist's |
/// | 93% | 2 | Frampton Comes Alive | one artist's |
///
/// Any threshold above 61% and at or below 83% separates them. Three quarters
/// sits in the middle of that gap with room on both sides.
const MOSTLY: (usize, usize) = (3, 4);

/// Who the **album** is by, which is not simply who the first track is by
/// `[SPEC-MPD-058]`.
///
/// The disc-level `PERFORMER` becomes `AlbumArtist` for every track in the
/// sheet. Taking it from track one gave a 22-artist compilation one album artist
/// — the first singer on it — for all 80 of its tracks, and a client keyed on
/// that shows one artist's picture and biography for the whole record while the
/// titles underneath keep changing.
///
/// The album is by whoever performs most of it, by [`MOSTLY`], and is a
/// compilation when nobody does. Tracks naming nobody are not counted either
/// way — missing data is not evidence.
///
/// `None` where no track names an artist: an absent field beats an invented one.
fn album_performer(tracks: &[Track]) -> Option<String> {
    let mut count: HashMap<&str, usize> = HashMap::new();
    for artist in tracks.iter().map(|t| t.2.trim()).filter(|a| !a.is_empty()) {
        *count.entry(artist).or_insert(0) += 1;
    }
    let named: usize = count.values().sum();
    if named == 0 {
        return None;
    }
    // `max_by_key` breaks ties arbitrarily, which is the bug this replaced — but
    // a tie cannot reach three quarters, so it can only land on `VARIOUS`.
    let (who, most) = count.iter().max_by_key(|(_, n)| **n)?;
    let (num, den) = MOSTLY;
    Some(if most * den >= named * num { (*who).to_string() } else { VARIOUS.to_string() })
}

/// One capture's sheet, or `None` when there is nothing worth naming.
fn sheet(file: &Path, album: &str, tracks: &[Track]) -> Option<String> {
    // A file with one passage is already named by its own tags; a sheet would
    // add a layer and say nothing new.
    if tracks.len() < 2 {
        return None;
    }
    let name = file.file_name()?.to_string_lossy().to_string();
    let kind = match file.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_uppercase()) {
        Some(e) if e == "MP3" => "MP3",
        Some(e) if e == "FLAC" || e == "OGG" => "WAVE",
        _ => "WAVE",
    };
    let mut out = String::new();
    out.push_str("REM Written by Lempi. Tracks are its radio passages.\n");
    if let Some(performer) = album_performer(tracks) {
        out.push_str(&format!("PERFORMER \"{}\"\n", cue_str(&performer)));
    }
    out.push_str(&format!("TITLE \"{}\"\n", cue_str(album)));
    out.push_str(&format!("FILE \"{}\" {}\n", cue_str(&name), kind));
    for (i, (start_ms, title, artist)) in tracks.iter().enumerate() {
        out.push_str(&format!("  TRACK {:02} AUDIO\n", i + 1));
        let t = if title.is_empty() { format!("Track {}", i + 1) } else { title.clone() };
        out.push_str(&format!("    TITLE \"{}\"\n", cue_str(&t)));
        if !artist.is_empty() {
            out.push_str(&format!("    PERFORMER \"{}\"\n", cue_str(artist)));
        }
        out.push_str(&format!("    INDEX 01 {}\n", cue_time(*start_ms)));
    }
    Some(out)
}

/// Write a cue sheet beside every capture in the library.
///
/// Idempotent: a sheet whose content already matches is left alone, so running
/// this twice touches nothing and a listener toggling the setting does not
/// rewrite their folder.
pub fn generate(conn: &Connection, dry_run: bool) -> Result<Written, String> {
    let mut by_file: HashMap<String, (String, Vec<Track>)> = HashMap::new();
    let mut q = conn
        .prepare(
            "SELECT f.path, \
                COALESCE(t.album, ''), \
                p.start_ms, \
                COALESCE((SELECT r.title FROM passage_recordings pr \
                          JOIN recordings r ON r.mbid = pr.mbid \
                          WHERE pr.passage_id = p.passage_id LIMIT 1), ''), \
                COALESCE((SELECT a.name FROM passage_recordings pr \
                          JOIN recording_artists ra ON ra.mbid = pr.mbid \
                          JOIN artists a ON a.mbid = ra.artist_mbid \
                          WHERE pr.passage_id = p.passage_id \
                          ORDER BY ra.weight DESC LIMIT 1), '') \
             FROM passages p JOIN files f USING(file_id) \
             LEFT JOIN file_tags t ON t.file_id = f.file_id \
             WHERE p.kind = 'radio' ORDER BY f.path, p.start_ms",
        )
        .map_err(|e| e.to_string())?;
    let rows = q
        .query_map([], |r| {
            Ok((
                r.get::<_, String>(0)?,
                r.get::<_, String>(1)?,
                r.get::<_, i64>(2)?,
                r.get::<_, String>(3)?,
                r.get::<_, String>(4)?,
            ))
        })
        .map_err(|e| e.to_string())?;
    for (path, album, start, title, artist) in rows.flatten() {
        let e = by_file.entry(path).or_insert_with(|| (album, Vec::new()));
        e.1.push((start, title, artist));
    }

    let mut rep = Written::default();
    for (path, (album, tracks)) in by_file {
        let audio = PathBuf::from(&path);
        let Some(text) = sheet(&audio, &album, &tracks) else {
            rep.passed_over(ONE_PASSAGE);
            continue;
        };
        let cue = audio.with_extension("cue");
        // Never overwrite someone else's sheet. A cue file that Lempi did not
        // write may be the reason the library is arranged as it is.
        if let Ok(existing) = std::fs::read_to_string(&cue) {
            if existing == text {
                rep.already_current();
                continue;
            }
            if !existing.starts_with("REM Written by Lempi") {
                rep.passed_over(NOT_OURS);
                continue;
            }
        }
        crate::report::write_or_report(&cue, text.as_bytes(), dry_run, &mut rep);
    }
    Ok(rep)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tracks() -> Vec<Track> {
        vec![
            (5_030, "Test for Echo".into(), "Rush".into()),
            (361_586, "Driven".into(), "Rush".into()),
        ]
    }

    /// 75 frames to the second, and never 75 — which is the next second.
    #[test]
    fn cue_times_are_minutes_seconds_frames() {
        assert_eq!(cue_time(0), "00:00:00");
        assert_eq!(cue_time(5_030), "00:05:02");
        assert_eq!(cue_time(361_586), "06:01:44");
        assert_eq!(cue_time(999), "00:00:74", "never rolls to frame 75");
        assert_eq!(cue_time(-5), "00:00:00", "and never goes backwards");
    }

    /// A quote inside a title would end the field early; the format has no
    /// escape for it.
    #[test]
    fn a_quote_in_a_title_cannot_break_the_field() {
        let t: Vec<Track> = vec![
            (0, "He said \"go\"".into(), "X".into()),
            (1_000, "b".into(), "X".into()),
        ];
        let out = sheet(Path::new("a.mp3"), "Al\"bum", &t).unwrap();
        assert_eq!(out.matches('"').count() % 2, 0, "quotes stay balanced");
        assert!(!out.contains("said \"go\""));
    }

    /// **The album artist is not simply the first track's artist**
    /// `[SPEC-MPD-058]`.
    ///
    /// Measured on the live library before the fix: `Various/Moana.cue` carries
    /// 22 distinct artists over 80 tracks, and MPD reported `AlbumArtist:
    /// Olivia Foaʻi` — track one's singer — for every one of them.
    #[test]
    fn a_record_by_several_artists_is_by_various_artists() {
        let mixed = vec![
            (0, "Tulou Tagaloa".into(), "Olivia Foaʻi".into()),
            (1_000, "Where You Are".into(), "Nicole Scherzinger".into()),
            (2_000, "Shiny".into(), "Jemaine Clement".into()),
        ];
        assert_eq!(album_performer(&mixed).as_deref(), Some(VARIOUS));

        let one_artist = vec![
            (0, "Candle in the Wind".into(), "Elton John".into()),
            (1_000, "Bennie and the Jets".into(), "Elton John".into()),
        ];
        assert_eq!(
            album_performer(&one_artist).as_deref(),
            Some("Elton John"),
            "a record by one artist still says so"
        );
    }

    /// A track naming nobody is missing data, not evidence of a compilation.
    #[test]
    fn unnamed_tracks_do_not_make_a_record_various() {
        let partial = vec![
            (0, "One".into(), "Heather Nova".into()),
            (1_000, "Two".into(), String::new()),
            (2_000, "Three".into(), "Heather Nova".into()),
        ];
        assert_eq!(album_performer(&partial).as_deref(), Some("Heather Nova"));

        let nameless =
            vec![(0, "One".into(), String::new()), (1_000, "Two".into(), String::new())];
        assert_eq!(album_performer(&nameless), None, "absent beats invented");
    }

    /// **One stray credit must not reclassify a solo record** — the reason
    /// unanimity was abandoned. Measured: `GoodbyeYellowBrickRoad.cue` has a few
    /// of its 49 passages linked to the wrong recording, and a strict rule
    /// called the album a compilation on the strength of them.
    #[test]
    fn a_few_wrong_credits_do_not_make_a_solo_record_a_compilation() {
        let mut tracks: Vec<Track> = (0..20)
            .map(|i| (i * 1_000, format!("Song {i}"), "Elton John".to_string()))
            .collect();
        tracks[7].2 = "The Band Perry".into();
        tracks[13].2 = "Someone Else".into();
        assert_eq!(
            album_performer(&tracks).as_deref(),
            Some("Elton John"),
            "18 of 20 is still his record"
        );
    }

    /// **A record most of one artist is still a compilation** if the rest is
    /// spread widely enough — the case a simple majority got wrong. Measured:
    /// *Moana* is 61% Mark Mancina over 14 artists, and is a soundtrack.
    #[test]
    fn a_dominant_composer_does_not_own_a_soundtrack() {
        // 61 of 100, as measured, with the rest spread over other names.
        let mut tracks: Vec<Track> = (0..61)
            .map(|i| (i * 1_000, format!("Cue {i}"), "Mark Mancina".to_string()))
            .collect();
        for i in 0..39 {
            tracks.push((100_000 + i * 1_000, format!("Song {i}"), format!("Singer {i}")));
        }
        assert_eq!(
            album_performer(&tracks).as_deref(),
            Some(VARIOUS),
            "a majority is not most of it"
        );
    }

    /// And a record where nobody performs most of it is a compilation, however
    /// the tracks are distributed.
    #[test]
    fn a_record_nobody_dominates_is_a_compilation() {
        let split = vec![
            (0, "A".into(), "One".into()),
            (1_000, "B".into(), "One".into()),
            (2_000, "C".into(), "Two".into()),
            (3_000, "D".into(), "Two".into()),
        ];
        assert_eq!(
            album_performer(&split).as_deref(),
            Some(VARIOUS),
            "a tie cannot reach three quarters"
        );
    }

    /// The disc-level line is what a client reads as `AlbumArtist`, so the
    /// compilation case must reach the sheet itself and not stop at the helper.
    #[test]
    fn the_sheet_carries_various_artists_for_a_compilation() {
        let mixed = vec![
            (0, "A".into(), "One Singer".into()),
            (1_000, "B".into(), "Another Singer".into()),
        ];
        let out = sheet(Path::new("/m/Various/Moana.mp3"), "Moana", &mixed).unwrap();
        assert!(out.contains("PERFORMER \"Various Artists\"\n"), "the album's own line");
        assert!(out.contains("    PERFORMER \"One Singer\""), "and each track keeps its own");
        assert!(out.contains("    PERFORMER \"Another Singer\""));
    }

    /// A single-passage file is already named by its own tags.
    #[test]
    fn a_file_with_one_passage_gets_no_sheet() {
        assert!(sheet(Path::new("a.mp3"), "Album", &tracks()[..1]).is_none());
        assert!(sheet(Path::new("a.mp3"), "Album", &tracks()).is_some());
    }

    #[test]
    fn a_sheet_names_every_track_and_points_at_the_audio() {
        let out = sheet(Path::new("/m/Rush/TestForEcho.mp3"), "Test For Echo", &tracks()).unwrap();
        assert!(out.contains("FILE \"TestForEcho.mp3\" MP3"), "the audio, not the path");
        assert!(out.contains("TRACK 01 AUDIO") && out.contains("TRACK 02 AUDIO"));
        assert!(out.contains("TITLE \"Driven\""));
        assert!(out.contains("INDEX 01 06:01:44"));
        assert!(out.starts_with("REM Written by Lempi"), "so it is recognisable later");
    }
}
