//! The smallest MPD client that answers a question `[SPEC015]`.
//!
//! `std::net` and nothing else, which is the claim `[SPEC-MPD-070]` makes about
//! this whole direction: MPD's protocol is line-oriented text over TCP, so
//! speaking it costs a dependency-free module rather than an HTTP stack.
//!
//! Deliberately incomplete. This is what stage 0 needs — connect, send, read a
//! response, parse `key: value` records `[IMPL-MPD-010]` — and the commands
//! that change anything are not here because stage 0 writes to nothing.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpStream;

pub struct Mpd {
    out: TcpStream,
    inp: BufReader<TcpStream>,
    /// The `OK MPD <version>` greeting, kept because the protocol level is the
    /// thing to target rather than the newest release `[SPEC-MPD-080]`.
    pub version: String,
    /// The socket has failed and this connection is finished.
    ///
    /// **Which kind of failure it was is not recoverable from the message.**
    /// `cmd` reports an `ACK` and a broken pipe both as `Err(String)`, and a
    /// caller that wants to reconnect on one but not the other was left
    /// matching on the text of it. The distinction is known here, so it is
    /// recorded here `[SPEC-MPD-130]`.
    pub broken: bool,
}

impl Mpd {
    pub fn connect(addr: &str) -> Result<Self, String> {
        let out = TcpStream::connect(addr).map_err(|e| format!("connect {addr}: {e}"))?;
        let inp = BufReader::new(out.try_clone().map_err(|e| e.to_string())?);
        let mut m = Mpd { out, inp, version: String::new(), broken: false };
        let mut greeting = String::new();
        m.inp.read_line(&mut greeting).map_err(|e| e.to_string())?;
        if !greeting.starts_with("OK MPD ") {
            return Err(format!("not an MPD greeting: {}", greeting.trim()));
        }
        m.version = greeting.trim_start_matches("OK MPD ").trim().to_string();
        Ok(m)
    }

    /// Send one command and collect its response lines.
    ///
    /// MPD terminates a response with `OK` or `ACK …`; anything else is a
    /// `key: value` line. An `ACK` is returned as an error rather than as data,
    /// because a caller that treats a refusal as an empty result will report a
    /// library of nothing and call it success.
    pub fn cmd(&mut self, command: &str) -> Result<Vec<String>, String> {
        if let Err(e) = writeln!(self.out, "{command}").and_then(|()| self.out.flush()) {
            self.broken = true;
            return Err(e.to_string());
        }
        let mut lines = Vec::new();
        loop {
            let mut line = String::new();
            let n = match self.inp.read_line(&mut line) {
                Ok(n) => n,
                Err(e) => {
                    self.broken = true;
                    return Err(e.to_string());
                }
            };
            if n == 0 {
                self.broken = true;
                return Err("connection closed mid-response".into());
            }
            let line = line.trim_end_matches(['\n', '\r']).to_string();
            if line == "OK" {
                return Ok(lines);
            }
            if let Some(err) = line.strip_prefix("ACK ") {
                return Err(err.to_string());
            }
            lines.push(line);
        }
    }

    /// Every song MPD knows, with its tags.
    ///
    /// Records are delimited by the `file:` key, which the protocol guarantees
    /// comes first for each song.
    pub fn songs(&mut self) -> Result<Vec<Song>, String> {
        let lines = self.cmd("listallinfo")?;
        let mut out: Vec<Song> = Vec::new();
        for line in lines {
            let Some((key, value)) = line.split_once(": ") else { continue };
            if key == "file" {
                out.push(Song { uri: value.to_string(), ..Default::default() });
                continue;
            }
            let Some(song) = out.last_mut() else { continue };
            match key {
                "MUSICBRAINZ_TRACKID" => song.recording_mbid = Some(value.to_string()),
                "Title" => song.title = Some(value.to_string()),
                "Artist" => song.artist = Some(value.to_string()),
                "duration" => song.duration_s = value.parse().ok(),
                _ => {}
            }
        }
        Ok(out)
    }
}

/// Quote a value for the protocol: wrap in `"`, backslash-escape `"` and `\`.
pub fn quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        if c == '"' || c == '\\' {
            out.push('\\');
        }
        out.push(c);
    }
    out.push('"');
    out
}

/// `key: value` lines as a map. Later keys win, which is what `status` wants.
pub fn parse(lines: &[String]) -> std::collections::HashMap<String, String> {
    lines
        .iter()
        .filter_map(|l| l.split_once(": "))
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

/// What happened when a passage was offered to MPD.
#[derive(Debug, Clone)]
pub struct Added {
    /// MPD's song id, the handle for everything afterwards.
    pub id: String,
    /// Whether MPD honoured the span. `false` means it kept the add, dropped
    /// the end, and will play past the passage `[SPEC-MPD-096]`.
    pub span_honoured: bool,
}

impl Mpd {
    /// `status`, parsed.
    pub fn status(&mut self) -> Result<std::collections::HashMap<String, String>, String> {
        self.cmd("status").map(|l| parse(&l))
    }

    /// Add a passage and bound it to its span.
    ///
    /// **The span is read back rather than assumed** `[SPEC-MPD-096]`,
    /// `[GOV-SRC-030]`: `rangeid` returns `OK` even when it silently drops an
    /// end that exceeds MPD's own duration estimate, and that estimate is wrong
    /// on 36.9% of this library `[SPEC-MPD-092]`. An `OK` is not evidence.
    ///
    /// A refused `rangeid` withdraws the add: naming a file without its span
    /// plays the whole capture, which is forty songs where one was wanted.
    pub fn add_ranged(&mut self, uri: &str, start_ms: i64, end_ms: i64) -> Result<Added, String> {
        let id = self
            .cmd(&format!("addid {}", quote(uri)))
            .map(|l| parse(&l).get("Id").cloned())?
            .ok_or_else(|| format!("addid returned no Id for {uri}"))?;
        let range = format!(
            "rangeid {id} {:.3}:{:.3}",
            start_ms as f64 / 1000.0,
            end_ms as f64 / 1000.0
        );
        if let Err(e) = self.cmd(&range) {
            let _ = self.cmd(&format!("deleteid {id}"));
            return Err(format!("rangeid refused ({e}); add withdrawn"));
        }
        let want = (end_ms - start_ms) as f64 / 1000.0;
        let span_honoured = self
            .cmd(&format!("playlistid {id}"))
            .ok()
            .and_then(|l| parse(&l).get("Time").and_then(|t| t.parse::<f64>().ok()))
            .is_some_and(|t| (t - want).abs() <= 1.5);
        Ok(Added { id, span_honoured })
    }
}

/// One song as MPD describes it. Only the fields the mapping ladder consults.
#[derive(Debug, Default, Clone)]
pub struct Song {
    /// Relative to MPD's `music_directory`, forward slashes, and **the thing
    /// Lempi must learn to name** `[SPEC-MPD-060]`.
    pub uri: String,
    /// Picard writes the *recording* MBID here despite the name, which is what
    /// makes this the second rung rather than a dead end.
    pub recording_mbid: Option<String>,
    pub title: Option<String>,
    pub artist: Option<String>,
    pub duration_s: Option<f64>,
}
