//! Route 1 of `[GDE-ECHO-540]`: find out why the presentation-offset term
//! reads zero.
//!
//! The player records `delay=0` on every callback `[GDE-ECHO-530]`. The kernel,
//! on the same raw `hw:` device at the same moment, does not — `/proc` reports
//! a live delay swinging 2760..4396 frames with `delay + avail` pinned at 4412.
//! The quantity exists and the player cannot see it, and until that is fixed
//! Phase 1's offset column stays empty on every node and `[GDE-ECHO-290]`'s
//! eligibility rule disqualifies `bose` itself `[GDE-ECHO-535]`.
//!
//! This probe reads the same number five ways, from one process, as close to
//! the same instant as a process can manage:
//!
//!   1. `snd_pcm_status_get_delay()` — the STATUS ioctl's `delay` field. This
//!      is the one cpal uses, and therefore the one the player sees.
//!   2. `snd_pcm_delay()` — a different library call into a different kernel
//!      path for the same quantity. If (1) is zero and (2) is not, the fix is
//!      a one-line change of source.
//!   3. `snd_pcm_avail_delay()` — what cpal switched to after 0.15.3. Asking
//!      it here turns "upgrading probably fixes this" into a measurement on
//!      the hardware in question, before anyone pays for an 0.15 -> 0.18 API
//!      migration on a working audio path.
//!   4. `avail` from the same Status, with `buffer - avail` as the arithmetic
//!      cross-check that needs no delay API at all.
//!   5. `/proc/asound/.../status`, re-read per iteration — the instrument that
//!      disagreed in the first place, kept in the comparison so that the
//!      disagreement is reproduced here rather than taken on trust
//!      `[GOV-SRC-020]`.
//!
//! Then it opens the device a second time **through cpal**, which is the layer
//! actually under suspicion, and reports what `playback - callback` yields.
//! Between the two phases the question is decided: if raw ALSA reports a delay
//! and cpal reports none, the loss is in cpal's conversion and this repository
//! can fix it. If both report none while `/proc` does not, the loss is below
//! both and the answer is route 2 — read `/proc` directly, visibly, as a
//! ranked fallback `[GOV-SRC-040]`.
//!
//! **The device is exclusive.** `lempi` holds it, so this cannot run beside the
//! player: stop the service, probe, start it again. It plays true digital
//! silence, so it is inaudible even though the DAC is running.
//!
//! The command line is `cli::specs::delayprobe`; `delayprobe --help` prints
//! it. The device default lives in that table `[GDE-ARC-033]`.

#[cfg(not(target_os = "linux"))]
fn main() {
    // Still answers --help and --version, so the command line is discoverable
    // from the machine someone is most likely typing it on.
    let _ = lempi_player::cli::specs::delayprobe::SPEC.parse();
    eprintln!("delayprobe: ALSA only; there is nothing to probe on this platform");
    std::process::exit(2);
}

#[cfg(target_os = "linux")]
fn main() {
    linux::run();
}

#[cfg(target_os = "linux")]
mod linux {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::sync::Arc;

    use alsa::pcm::{Access, Format, HwParams, State, TstampType, PCM};
    use alsa::Direction;

    /// Match the player, which pins its period rather than taking the device's
    /// own `[LOG-CPAL-040]`. Without this the probe opens with whatever
    /// maximum the hardware offers -- 32768 frames on `teacherslounge`, buffer
    /// a megabyte -- and its delay column is a picture of that buffer filling,
    /// not of the offset the player would actually see. An instrument that
    /// does not reproduce the configuration under test measures something
    /// else, however carefully.
    const PERIOD_FRAMES: alsa::pcm::Frames = 2048;
    const RATE: u32 = 44100;
    const CHANNELS: u32 = 2;

    /// `/proc`'s own view, found by walking the card tree rather than by
    /// guessing a card number -- `[BOS-PWR-050]`'s rule, for the same reason:
    /// card ordering is not stable across boots.
    fn proc_status(owner_pid: u32) -> Option<(i64, i64)> {
        let cards = std::fs::read_dir("/proc/asound").ok()?;
        for card in cards.flatten() {
            let mut p = card.path();
            p.push("pcm0p/sub0/status");
            let Ok(text) = std::fs::read_to_string(&p) else {
                continue;
            };
            if !text.contains("state: RUNNING") {
                continue;
            }
            // More than one card can be RUNNING; take the one this process
            // owns, so the comparison is against our own stream.
            let mine = text
                .lines()
                .find_map(|l| l.strip_prefix("owner_pid"))
                .and_then(|l| l.split(':').nth(1))
                .and_then(|v| v.trim().parse::<u32>().ok())
                .map(|pid| pid == owner_pid)
                .unwrap_or(false);
            if !mine {
                continue;
            }
            let field = |name: &str| -> Option<i64> {
                text.lines()
                    .find_map(|l| l.strip_prefix(name))?
                    .split(':')
                    .nth(1)?
                    .trim()
                    .parse()
                    .ok()
            };
            return Some((field("delay")?, field("avail")?));
        }
        None
    }

    /// Open exactly as cpal does `[GDE-ECHO-160]`, including the timestamp
    /// configuration, because "exactly as cpal does" is the hypothesis under
    /// test. A probe that configured the device differently could not tell a
    /// cpal bug from a configuration difference.
    fn open_like_cpal(device: &str) -> Result<(PCM, i64, i64), alsa::Error> {
        let pcm = PCM::new(device, Direction::Playback, false)?;
        {
            let hw = HwParams::any(&pcm)?;
            hw.set_channels(CHANNELS)?;
            hw.set_rate(RATE, alsa::ValueOr::Nearest)?;
            hw.set_format(Format::s16())?;
            hw.set_access(Access::RWInterleaved)?;
            pcm.hw_params(&hw)?;
        }
        {
            // period = PERIOD_FRAMES, buffer = 2x, which is what
            // `BufferSize::Fixed` means to cpal's ALSA backend.
            let hw = HwParams::any(&pcm)?;
            hw.set_channels(CHANNELS)?;
            hw.set_rate(RATE, alsa::ValueOr::Nearest)?;
            hw.set_format(Format::s16())?;
            hw.set_access(Access::RWInterleaved)?;
            hw.set_period_size_near(PERIOD_FRAMES, alsa::ValueOr::Nearest)?;
            hw.set_buffer_size_near(2 * PERIOD_FRAMES)?;
            pcm.hw_params(&hw)?;
        }
        let (buffer, period) = pcm.get_params()?;
        {
            let sw = pcm.sw_params_current()?;
            sw.set_avail_min(period as alsa::pcm::Frames)?;
            sw.set_start_threshold((buffer - period) as alsa::pcm::Frames)?;
            sw.set_tstamp_mode(true)?;
            // cpal asks for MonotonicRaw and silently accepts Monotonic when
            // the driver refuses `[GDE-ECHO-150]`. Mirror that, and say which
            // one we ended up with -- a silent fallback is the thing this
            // whole investigation exists to stop being silent.
            sw.set_tstamp_type(TstampType::MonotonicRaw)?;
            if pcm.sw_params(&sw).is_err() {
                sw.set_tstamp_type(TstampType::Monotonic)?;
                pcm.sw_params(&sw)?;
                println!("  tstamp_type : Monotonic (MonotonicRaw refused, as cpal would)");
            } else {
                println!("  tstamp_type : MonotonicRaw");
            }
        }
        Ok((pcm, buffer as i64, period as i64))
    }

    /// `None` when the device could not be opened at all -- distinct from
    /// `Some(false)`, which means it opened and the delay really was zero.
    fn phase_a(device: &str, seconds: u64) -> Option<bool> {
        println!("\n=== Phase A: raw ALSA, opened as cpal opens it ===");
        let (pcm, buffer, period) = match open_like_cpal(device) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("  open failed: {e}");
                if e.errno() == libc::EBUSY {
                    eprintln!("  the device is in use -- stop lempi first:");
                    eprintln!("    sudo systemctl stop lempi && delayprobe && sudo systemctl start lempi");
                }
                return None;
            }
        };
        println!("  device      : {device}");
        println!("  buffer      : {buffer} frames    period: {period} frames");

        let pid = std::process::id();
        let io = pcm.io_i16().expect("s16 io");
        let silence = vec![0i16; (period * CHANNELS as i64) as usize];

        // Fill the ring before starting, so the first reading is taken against
        // a full buffer rather than a starting one -- the prefill transient is
        // exactly what made the first drift numbers wrong `[LOG-FIX-020]`.
        while pcm.state() != State::Running {
            if io.writei(&silence).is_err() {
                break;
            }
            if pcm.state() == State::Prepared && pcm.start().is_err() {
                break;
            }
        }

        println!();
        println!("  {:>7}  {:>9}  {:>9}  {:>9}  {:>9}  {:>9}  {:>9}",
                 "iter", "status", "pcm_delay", "availdly", "buf-avail", "procdly", "procavail");
        println!("  {:>7}  {:>9}  {:>9}  {:>9}  {:>9}  {:>9}  {:>9}",
                 "", "(0.15.3)", "", "(master)", "", "", "");

        let iters = (seconds * RATE as u64) / period.max(1) as u64;
        let mut status_nonzero = 0u32;
        let mut pcm_delay_nonzero = 0u32;
        let mut avail_delay_nonzero = 0u32;
        let mut proc_nonzero = 0u32;
        let mut shown = 0;
        for i in 0..iters {
            if io.writei(&silence).is_err() {
                let _ = pcm.prepare();
                continue;
            }
            let Ok(st) = pcm.status() else { continue };
            let s_delay = st.get_delay();
            let s_avail = st.get_avail();
            let p_delay = pcm.delay().unwrap_or(-1);
            // What cpal master calls. One call, both numbers, and on this
            // driver the one that decides whether an upgrade is the fix.
            let ad = pcm.avail_delay().map(|(_, d)| d).unwrap_or(-1);
            let (pr_delay, pr_avail) = proc_status(pid).unwrap_or((-1, -1));

            if s_delay != 0 {
                status_nonzero += 1;
            }
            if p_delay > 0 {
                pcm_delay_nonzero += 1;
            }
            if ad > 0 {
                avail_delay_nonzero += 1;
            }
            if pr_delay > 0 {
                proc_nonzero += 1;
            }
            // Every iteration is a period apart; printing them all buries the
            // answer. Ten samples spread across the run show both the value and
            // whether it moves, which is the part `[GDE-ECHO-290]` judges on.
            if i % (iters / 10).max(1) == 0 && shown < 12 {
                shown += 1;
                println!("  {:>7}  {:>9}  {:>9}  {:>9}  {:>9}  {:>9}  {:>9}",
                         i, s_delay, p_delay, ad, buffer - s_avail, pr_delay, pr_avail);
            }
        }

        println!();
        println!("  over {iters} periods, readings that were nonzero:");
        println!("    snd_pcm_status_get_delay() : {status_nonzero}  <- cpal 0.15.3");
        println!("    snd_pcm_delay()            : {pcm_delay_nonzero}");
        println!("    snd_pcm_avail_delay()      : {avail_delay_nonzero}  <- cpal master");
        println!("    /proc delay                : {proc_nonzero}");
        Some(status_nonzero > 0)
    }

    fn phase_b(device: &str, seconds: u64) {
        println!("\n=== Phase B: the same device through cpal ===");
        use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

        // cpal names devices its own way; match on the ALSA name the caller
        // gave rather than assuming the default device is the one under test.
        let host = cpal::default_host();
        let want = device.split('=').nth(1).and_then(|s| s.split(',').next());
        let mut chosen = None;
        if let Ok(devices) = host.output_devices() {
            for d in devices {
                let name = d.to_string();
                if want.map(|w| name.contains(w)).unwrap_or(false) || name == device {
                    chosen = Some(d);
                    break;
                }
            }
        }
        let dev = match chosen.or_else(|| host.default_output_device()) {
            Some(d) => d,
            None => {
                eprintln!("  no cpal output device");
                return;
            }
        };
        println!("  cpal device : {dev}");

        let config = cpal::StreamConfig {
            channels: CHANNELS as u16,
            sample_rate: RATE,
            buffer_size: cpal::BufferSize::Default,
        };
        let calls = Arc::new(AtomicU64::new(0));
        let nonzero = Arc::new(AtomicU64::new(0));
        let last = Arc::new(AtomicU64::new(0));
        let distinct = Arc::new(AtomicU64::new(0));
        let (c, n, l, d) = (calls.clone(), nonzero.clone(), last.clone(), distinct.clone());

        let stream = dev.build_output_stream(
            config,
            move |out: &mut [i16], info: &cpal::OutputCallbackInfo| {
                out.iter_mut().for_each(|v| *v = 0);
                let ts = info.timestamp();
                // The player's own arithmetic, character for character, so that
                // a difference here is a difference in the platform and not in
                // how the two were written `[GDE-ECHO-280]`.
                let delay =
                    (ts.playback.duration_since(ts.callback).as_secs_f64() * RATE as f64) as u64;
                c.fetch_add(1, Ordering::Relaxed);
                if delay != 0 {
                    n.fetch_add(1, Ordering::Relaxed);
                }
                let was = l.swap(delay, Ordering::Relaxed);
                if was != delay {
                    d.fetch_add(1, Ordering::Relaxed);
                }
            },
            |e| eprintln!("  cpal error: {e}"),
            None,
        );
        let stream = match stream {
            Ok(s) => s,
            Err(e) => {
                eprintln!("  build_output_stream failed: {e}");
                return;
            }
        };
        if let Err(e) = stream.play() {
            eprintln!("  play failed: {e}");
            return;
        }
        std::thread::sleep(std::time::Duration::from_secs(seconds));
        drop(stream);

        let c = calls.load(Ordering::Relaxed);
        let n = nonzero.load(Ordering::Relaxed);
        let d = distinct.load(Ordering::Relaxed);
        println!("  callbacks   : {c}");
        println!("  delay != 0  : {n}");
        println!("  delay changed: {d}   (the test `[GDE-ECHO-290]` actually applies)");
    }

    pub fn run() {
        use lempi_player::cli::specs::delayprobe as opt;
        let args = opt::SPEC.parse();
        let device = args.need(&opt::DEVICE).to_string();
        let seconds: u64 = args.must_int(&opt::SECONDS);

        println!("delayprobe: is the presentation offset readable on this node?");
        println!("  `[GDE-ECHO-540]` route 1. Playing silence; the device is exclusive,");
        println!("  so lempi must be stopped for this to open at all.");

        let raw_saw_delay = phase_a(&device, seconds);
        phase_b(&device, seconds);

        println!("\n=== Verdict ===");
        // `false` used to mean both "ran, saw zero" and "never got to look",
        // and the second printed as the first -- the conflation CLAUDE.md
        // section 5 exists to forbid, committed here in the one tool whose
        // whole job is to report an absence honestly. Caught by a transient
        // EBUSY on smartboardpc.
        let Some(raw_saw_delay) = raw_saw_delay else {
            println!("  Phase A never opened the device, so nothing above is a");
            println!("  measurement of anything. Free the device and run it again;");
            println!("  do not read this as a delay of zero `[GDE-DEP-060]`.");
            return;
        };
        if raw_saw_delay {
            println!("  Raw ALSA reports a delay through the same call cpal makes.");
            println!("  If Phase B still shows delay == 0, the loss is inside cpal's");
            println!("  conversion and is fixable here -- route 1 `[GDE-ECHO-540]`.");
        } else {
            println!("  snd_pcm_status_get_delay() returned zero throughout. Compare the");
            println!("  other three columns: if snd_pcm_delay() or /proc reported a live");
            println!("  value, the STATUS ioctl path is the wrong source on this driver");
            println!("  and route 2 -- reading /proc, visibly ranked as a fallback");
            println!("  `[GOV-SRC-040]` -- is the answer. If every column read zero, the");
            println!("  earlier /proc observation was taken while lempi held the device");
            println!("  and this probe has not reproduced it; say so rather than");
            println!("  concluding `[GOV-SRC-020]`.");
        }
    }
}
