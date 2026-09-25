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
//!
//! **`[GDE-HST-350]` One observer per platform, behind one interface.** How the
//! question is asked differs by host -- `wpctl` on a PipeWire Linux, D-Bus once
//! `[SPEC-APS-090]` lands, stream state on a phone, nothing at all on Windows or
//! on a node that goes straight to ALSA -- and the answer must not pretend
//! otherwise. So callers hold a [`SinkObserver`] and read a [`Tristate`]: where
//! nothing can be observed, the answer is `Unknown`, said as such, rather than
//! the `true` a missing `wpctl` used to turn into `[SPEC-APS-030]`. SPEC011's
//! D-Bus observer then replaces an implementation here, not its callers.

use std::process::Command;

/// Whether anything could hear us -- the shape of SPEC011's
/// `PathState.audible` `[SPEC-APS-060]`. `Unknown` is an answer, not a
/// failure: it is what a host that cannot see its own output must say.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, serde::Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Tristate {
    Yes,
    No,
    #[default]
    Unknown,
}

/// Where the player's stream is linked, as the host's observer sees it.
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
    /// The answer the three fields above add up to. See [`SinkStatus::judge`].
    pub audible: Tristate,
    /// Which observer answered: `wpctl`, or `none`.
    pub observer: &'static str,
    /// Why `audible` is `Unknown`, when it is.
    pub note: Option<String>,
}

impl SinkStatus {
    /// Fill in `audible` from what was observed.
    ///
    /// Only a dummy is `No`, and only a real linked sink is `Yes`. A query that
    /// could not run is `Unknown`, and so is one that ran and found our stream
    /// linked to nothing: the stream may not be open yet, and "not linked" is
    /// not "linked to the placeholder".
    fn judge(mut self) -> Self {
        self.audible = match (self.known, self.dummy, &self.sink) {
            (false, _, _) => Tristate::Unknown,
            (true, true, _) => Tristate::No,
            (true, false, Some(_)) => Tristate::Yes,
            (true, false, None) => Tristate::Unknown,
        };
        if self.audible == Tristate::Unknown && self.note.is_none() && self.known {
            self.note = Some("the player's stream is linked to nothing".into());
        }
        self
    }
}

/// Something that can say where the audio is going on this host.
///
/// `observe` may block -- a subprocess today, a D-Bus call later -- so, like
/// [`current`], it is never called from the audio path `[SPEC-APS-070]`.
pub trait SinkObserver: Send + Sync {
    /// The mechanism, for the log and the panel.
    fn name(&self) -> &'static str;
    fn observe(&self) -> SinkStatus;
}

/// PipeWire, asked through `wpctl status` -- the Linux observer today.
pub struct Wpctl;

impl SinkObserver for Wpctl {
    fn name(&self) -> &'static str {
        "wpctl"
    }

    fn observe(&self) -> SinkStatus {
        let base = SinkStatus { observer: self.name(), ..Default::default() };
        let out = match Command::new("wpctl").arg("status").output() {
            Ok(o) => o,
            Err(e) => {
                // No `wpctl`: a node that goes straight to ALSA, like `bose`,
                // or one whose PipeWire is not installed.
                return SinkStatus { note: Some(format!("wpctl could not run: {e}")), ..base }.judge();
            }
        };
        if !out.status.success() {
            // Ran, but could not reach a PipeWire -- no session bus, say. The
            // output is not an answer about any sink.
            return SinkStatus {
                note: Some(format!("wpctl status exited {}", out.status)),
                ..base
            }
            .judge();
        }
        let text = String::from_utf8_lossy(&out.stdout);
        match parse(&text) {
            Some(sink) => SinkStatus { dummy: sink == DUMMY, sink: Some(sink), known: true, ..base },
            None => SinkStatus { known: true, ..base },
        }
        .judge()
    }
}

/// A host with no way to observe its output. Answers `Unknown`, always, and
/// says why.
pub struct Unobservable(pub &'static str);

impl SinkObserver for Unobservable {
    fn name(&self) -> &'static str {
        "none"
    }

    fn observe(&self) -> SinkStatus {
        SinkStatus { observer: self.name(), note: Some(self.0.into()), ..Default::default() }.judge()
    }
}

/// This platform's observer.
///
/// By `target_os`, and `android` is not `linux`: a phone has no `wpctl`, and
/// its stream-state observer is Android work `[GDE-HST-350]`.
pub fn observer() -> &'static dyn SinkObserver {
    #[cfg(target_os = "linux")]
    {
        &Wpctl
    }
    #[cfg(not(target_os = "linux"))]
    {
        &Unobservable("no audibility observer exists for this platform yet")
    }
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

/// Ask this host's observer where the audio is going.
pub fn current() -> SinkStatus {
    observer().observe()
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
            ..Default::default()
        }
        .judge();
        assert!(s.dummy, "a dummy-bound stream must be distinguishable");
        assert_eq!(s.audible, Tristate::No);
    }

    /// Only an observation answers Yes or No; everything else is Unknown, and
    /// says why `[SPEC-APS-030]`.
    #[test]
    fn audibility_is_judged_from_what_was_seen() {
        let seen = |sink: Option<&str>, known| {
            SinkStatus {
                dummy: sink == Some(DUMMY),
                sink: sink.map(String::from),
                known,
                ..Default::default()
            }
            .judge()
        };
        assert_eq!(seen(Some("MIDDLETON"), true).audible, Tristate::Yes);
        assert_eq!(seen(Some(DUMMY), true).audible, Tristate::No);
        let unlinked = seen(None, true);
        assert_eq!(unlinked.audible, Tristate::Unknown, "not linked is not the dummy");
        assert!(unlinked.note.is_some(), "and it says why");
        // The case that used to read as audible: the question never ran.
        assert_eq!(seen(None, false).audible, Tristate::Unknown);
    }

    #[test]
    fn a_host_that_cannot_observe_says_so() {
        let s = Unobservable("no way to look").observe();
        assert_eq!(s.audible, Tristate::Unknown);
        assert!(!s.known);
        assert_eq!(s.observer, "none");
        assert_eq!(s.note.as_deref(), Some("no way to look"));
        assert_eq!(
            serde_json::to_value(&s).unwrap()["audible"],
            serde_json::json!("unknown"),
            "the wire form is a word, not a boolean a reader could take as true"
        );
    }

    /// The platform choice itself. On Linux the answer is whatever `wpctl`
    /// can see -- `Unknown` in a container with no PipeWire -- but the
    /// observer is named; elsewhere it is `none`, and never `Yes`.
    #[test]
    fn this_platform_has_the_observer_it_should() {
        let s = current();
        if cfg!(target_os = "linux") {
            assert_eq!(s.observer, "wpctl");
        } else {
            assert_eq!(s.observer, "none");
            assert_eq!(s.audible, Tristate::Unknown);
        }
        if !s.known {
            assert_eq!(s.audible, Tristate::Unknown, "an unrun query is never an answer");
            assert!(s.note.is_some());
        }
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
