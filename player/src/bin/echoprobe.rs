//! An echo node's side of the wire, observing only.
//!
//! Connects to a master's snapshot WebSocket, reads the `echo` field
//! `[GDE-ECHO-310]`, drives a [`Follower`] with this node's timing, and prints
//! what it *would* do. It starts nothing, trims nothing and touches no audio.
//!
//! **This exists because "the engine produces it" and "a node receives it" are
//! different claims.** The master side was verified by reading its own log
//! lines on `bose`; that proves the arithmetic, not that `EchoState` survives
//! serialisation, crosses a socket and arrives usable. Nothing had ever read
//! one until this.
//!
//! Observing first is also the cheap half. Acting on a schedule means starting
//! a passage at a wall-clock instant, and acting on a trim means dropping a
//! frame in the mixer -- both are changes to a running player, and both are
//! easier to trust once the numbers arriving have been watched for a while.
//!
//! The command line is `cli::specs::echoprobe`; `echoprobe --help` prints it.
//!
//! `--offset-frames` is this node's presentation offset `[GDE-ECHO-430]` --
//! the measured figure, not a guess: `bose` 2043, `lempi02w` 15676
//! `[LOG-CPAL-060]`.

use std::time::Duration;

use futures_util::StreamExt;
use lempi_player::cli::specs::echoprobe as opt;
use lempi_player::echo::{EchoState, Follow, Follower, NodeTiming, Trim};

/// Only the field an echo node cares about. Serde ignores the rest of the
/// snapshot, which is how this rides a socket built for a browser without
/// knowing anything about what the browser wants.
#[derive(serde::Deserialize)]
struct Snapshot {
    #[serde(default)]
    echo: EchoState,
}

fn now_nanos() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0)
}

#[tokio::main(flavor = "current_thread")]
async fn main() {
    let args = opt::SPEC.parse();
    let url = args.need(&opt::URL).to_string();
    let offset: u64 = args.must_int(&opt::OFFSET_FRAMES);
    let rate: u32 = args.must_int(&opt::RATE) as u32;

    let timing = NodeTiming { presentation_offset_frames: offset, rate };
    println!("echoprobe: following {url}");
    println!("  this node's offset: {offset} frames ({} ms)", timing.offset().as_millis());
    println!("  observing only -- nothing is started, nothing is trimmed");

    let mut follower = Follower::new(timing, Duration::from_micros(500), Duration::from_secs(1));
    // A follower's own basis would come from its own frame clock. With no
    // audio here there is nothing to invalidate, so it is established once and
    // left alone -- and that is stated rather than hidden, because a real node
    // must NOT do this `[GDE-ECHO-360]`.
    follower.basis.establish();

    let mut seen = 0u64;
    let mut last_report = String::new();
    let mut last_sched: Option<i64> = None;

    loop {
        let ws = match tokio_tungstenite::connect_async(&url).await {
            Ok((ws, _)) => ws,
            Err(e) => {
                eprintln!("echoprobe: connect failed: {e}; retrying in 3s");
                tokio::time::sleep(Duration::from_secs(3)).await;
                continue;
            }
        };
        println!("echoprobe: connected");
        let (_, mut rx) = ws.split();

        while let Some(msg) = rx.next().await {
            let text = match msg {
                Ok(tokio_tungstenite::tungstenite::Message::Text(t)) => t,
                Ok(_) => continue,
                Err(e) => {
                    eprintln!("echoprobe: socket error: {e}");
                    break;
                }
            };
            let snap: Snapshot = match serde_json::from_str(&text) {
                Ok(s) => s,
                Err(e) => {
                    // A master too old to publish the field is a fact worth
                    // printing once, not a parse loop `[GOV-SRC-040]`.
                    eprintln!("echoprobe: snapshot did not parse: {e}");
                    continue;
                }
            };
            seen += 1;
            report(&mut follower, &snap.echo, seen, &mut last_report, &mut last_sched);
        }
        eprintln!("echoprobe: disconnected; retrying in 3s");
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
}

/// Print only when the answer changes. At the snapshot's own cadence this
/// would otherwise emit twice a second forever, and a log nobody can read is
/// the same as no log.
fn report(f: &mut Follower, st: &EchoState, seen: u64, last: &mut String,
          last_sched: &mut Option<i64>) {
    let now = now_nanos();
    let follow = f.on_state(st, now);
    let trim = f.trim_for(st, None, now);

    // How far ahead of the sound does the schedule actually arrive?
    //
    // `[GDE-ECHO-310]` sizes the lead against the *offset spread* and calls the
    // margin sixty-fold. That comparison leaves out the follower's own ring: if
    // this node also needs ~15 s to push sample 0 through to its device, and it
    // learns of the passage 15 s before the sound, the usable margin is not
    // sixty-fold, it is whatever is left after subtracting a ring. Measure it
    // rather than assume either way.
    if let Some(sched) = st.schedule {
        if Some(sched.passage_id) != *last_sched {
            *last_sched = Some(sched.passage_id);
            let lead_ms = (sched.sound_at as i64 - now as i64) as f64 / 1e6;
            let offset_ms = f.timing.offset().as_millis() as f64;
            // A non-zero start sample is a seek `[GDE-ECHO-325]`, not a
            // passage boundary, and the two are worth telling apart at a
            // glance: one holds sync straight through, the other buys it back.
            let from = if sched.start_sample == 0 {
                "from the start".to_string()
            } else {
                format!("SEEK to {:.1}s in",
                        sched.start_sample as f64 / sched.rate.max(1) as f64)
            };
            println!(
                "[{seen:>6}] SCHEDULE passage {} {from} -- sound in {lead_ms:.0} ms; this node needs {offset_ms:.0} ms for the device, leaving {:.0} ms for its own ring",
                sched.passage_id, lead_ms - offset_ms
            );
        }
    }

    let line = match (&follow, st.anchor) {
        (Follow::Hold(why), _) => format!("HOLD -- master reports {why:?}"),
        (Follow::StartAt { passage_id, start_sample, at }, _) => {
            let lead = (*at as i64 - now as i64) as f64 / 1e9;
            format!("START passage {passage_id} at sample {start_sample} in {lead:.3}s")
        }
        (Follow::Missed { passage_id }, _) => {
            format!("MISSED passage {passage_id} -- submission was already due")
        }
        (Follow::Idle, Some(a)) => format!(
            "following passage {} at sample {} ({:.1}s), master ppm {}",
            a.passage_id, a.sample, a.sample as f64 / a.rate.max(1) as f64,
            match a.ppm {
                Some(p) => format!("{p:+.2}"),
                None => "not measured".to_string(),
            }
        ),
        (Follow::Idle, None) => "idle -- master has published no anchor".to_string(),
    };

    if line != *last {
        println!("[{seen:>6}] {line}");
        *last = line;
    }
    // A trim decision is rarer and always worth a line.
    if matches!(trim, Some(Trim::DropFrame) | Some(Trim::DuplicateFrame)) {
        println!("[{seen:>6}] would {trim:?} (no local air position here, so this is not expected)");
    }
}
