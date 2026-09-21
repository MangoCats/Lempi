//! Which sink is the audio *actually* reaching `[PI3-API-020]`?
//!
//! The player cannot answer this itself. It opens `default` through ALSA and
//! that is the only name it ever learns, while the routing decision happens a
//! layer further out in PipeWire. So the honest answer has to be read from
//! there, and the interface has to be willing to report `Dummy Output` --
//! PipeWire's stand-in when no hardware is present, and a sink that accepts
//! audio perfectly forever while nobody hears a thing `[PI3-WHY-010]`.
//!
//! It costs a subprocess, so **it is never called from the audio path**. The
//! settings panel calls `current()` on demand, and the path supervisor calls it
//! from its own thread `[SPEC-APS-060]`.
//!
//! It once had a `SinkWatch` companion that polled on a background thread and
//! published an atomic, because the engine needed the answer and could not
//! afford to ask. That is gone: the engine no longer asks at all, so the
//! poller it needed is one fewer thread and one fewer copy of the question.

use std::process::Command;

/// Where the player's stream is linked, as PipeWire sees it.
#[derive(Debug, Clone, PartialEq, Default, serde::Serialize)]
pub struct SinkStatus {
    /// The node the stream is linked to, if it could be determined.
    pub sink: Option<String>,
    /// True when that node is PipeWire's placeholder. Reported rather than
    /// hidden: it is the difference between "connected" and "playing", and
    /// concealing it is how this fault stayed invisible for two days.
    pub dummy: bool,
    /// False when the query itself could not run -- no `wpctl`, no session
    /// bus, not Linux. Distinguished from "ran and found nothing", because
    /// the remedies differ.
    pub known: bool,
}

/// The stream name `cpal` registers with PipeWire.
const STREAM: &str = "[lempi]";
const DUMMY: &str = "Dummy Output";

/// Pull the linked sink out of `wpctl status` output.
///
/// Separated from running the command so the parsing is testable without a
/// sound server, which is the only part with any real chance of being wrong.
///
/// The shape being read:
///
/// ```text
///  └─ Streams:
///         50. PipeWire ALSA [lempi]
///              45. output_FL       > MIDDLETON:playback_FL   [active]
/// ```
pub fn parse(text: &str) -> Option<String> {
    let mut in_stream = false;
    for line in text.lines() {
        if line.contains(STREAM) {
            in_stream = true;
            continue;
        }
        if in_stream {
            // A port line links with '>'. Anything else ends our block: the
            // next stream's header, or the end of the section.
            if let Some((_, target)) = line.split_once('>') {
                let name = target.trim().split(':').next()?.trim();
                if !name.is_empty() {
                    return Some(name.to_string());
                }
            } else if line.contains('.') && !line.trim().is_empty() {
                in_stream = false;
            }
        }
    }
    None
}

/// Ask PipeWire where the audio is going.
pub fn current() -> SinkStatus {
    let out = Command::new("wpctl").arg("status").output();
    let Ok(out) = out else {
        return SinkStatus::default(); // known: false
    };
    let text = String::from_utf8_lossy(&out.stdout);
    match parse(&text) {
        Some(sink) => SinkStatus { dummy: sink == DUMMY, sink: Some(sink), known: true },
        None => SinkStatus { sink: None, dummy: false, known: true },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Real output, captured from the Pi while playing to the speaker.
    const PLAYING: &str = "\
 └─ Streams:
        50. PipeWire ALSA [lempi]
             45. output_FL       > MIDDLETON:playback_FL\t[active]
             53. output_FR       > MIDDLETON:playback_FR\t[active]
";

    /// The failure this whole module exists to make visible: same shape, and
    /// nothing in it looks wrong unless you read the target.
    const SILENT: &str = "\
 └─ Streams:
        50. PipeWire ALSA [lempi]
             45. output_FR       > Dummy Output:playback_FR\t[active]
             47. output_FL       > Dummy Output:playback_FL\t[active]
";

    #[test]
    fn reads_a_real_sink() {
        assert_eq!(parse(PLAYING).as_deref(), Some("MIDDLETON"));
    }

    #[test]
    fn reports_the_dummy_rather_than_hiding_it() {
        assert_eq!(parse(SILENT).as_deref(), Some("Dummy Output"));
        let s = SinkStatus {
            dummy: parse(SILENT).as_deref() == Some(DUMMY),
            sink: parse(SILENT),
            known: true,
        };
        assert!(s.dummy, "a dummy-bound stream must be distinguishable");
    }

    #[test]
    fn other_streams_are_not_mistaken_for_ours() {
        let mixed = "\
 └─ Streams:
        12. Firefox [firefox]
             13. output_FL       > MIDDLETON:playback_FL\t[active]
";
        assert_eq!(parse(mixed), None, "only our own stream counts");
    }

    #[test]
    fn no_stream_is_not_an_error() {
        assert_eq!(parse(" └─ Streams:\n"), None);
        assert_eq!(parse(""), None);
    }
}
