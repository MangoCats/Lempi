//! What it costs to rebuild the Program Director in place `[SPEC009]`.
//!
//! `Director::load` is requested once by `Session::open` and built on its own
//! thread rather than blocking startup on it -- a resumed passage needs no
//! selection to start playing, and this tool's own numbers below are exactly
//! why that block was worth removing. Imported music is browsable at once and
//! unselectable until that first build lands, or the player restarts
//! `[IMPL-SUI-070]`. Rebuilding it live instead is attractive because
//! **the Director is off the audio path entirely** — it chooses what plays next
//! and never touches decode, mix or output, so a rebuild cannot glitch a note.
//!
//! Whether it is affordable turns on two numbers, and this measures them rather
//! than reasoning about them — the same shape as `memcheck` for `[REQ-AUD-110]`:
//!
//!   1. **How long** a load takes, against the queue depth that would cover it.
//!   2. **Peak memory while two exist at once**, which is what a build-then-swap
//!      costs on a 512 MB appliance holding to ≤150 MB `[GDE-ARC-050]`.
//!
//! The command line is `cli::specs::dircheck`; `dircheck --help` prints it.

use std::path::PathBuf;
use std::time::Instant;

use lempi_player::cli::specs::dircheck as opt;
use lempi_player::{db::Library, peak_rss_bytes};

fn mb(b: u64) -> f64 {
    b as f64 / 1_048_576.0
}

fn rss() -> u64 {
    peak_rss_bytes().unwrap_or(0)
}

fn main() {
    let args = opt::SPEC.parse();
    let db = PathBuf::from(args.need(&opt::LISTENER));
    // A second, optional path exercises Director::load across a genuinely
    // split pair -- exactly the shape [PI-DB-020]/[IMPL-DBSPLIT-025]
    // describe, and the one this tool otherwise never sees since its own
    // single-path default (`open_split(db, db)`) never attaches anything.
    let library = args.text(&opt::LIBRARY).map(PathBuf::from).unwrap_or_else(|| db.clone());
    let lib = match Library::open_split(&db, &library) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("cannot open {}: {e:?}", db.display());
            std::process::exit(1);
        }
    };
    let radio = lib.count_radio().unwrap_or(0);
    let base = rss();
    println!("library: {radio} radio passages");
    println!("baseline peak RSS: {:.1} MB\n", mb(base));

    // One, cold.
    let t0 = Instant::now();
    let first = match lib.director() {
        Ok(d) => d,
        Err(e) => {
            eprintln!("director load failed: {e:?}");
            std::process::exit(1);
        }
    };
    let cold = t0.elapsed();
    // What the pool looks like once loaded `[SPEC-DIR-190]`. Cheap here, and
    // the only place the three identity tiers `[GDE-WRK-035]` can be seen
    // apart against a real library rather than a fixture.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let cen = first.census(now);
    println!(
        "pool: {} eligible of {} | blocked: {} artist, {} recording, {} same song, {} related\n\
         \x20     {} under min weight, {} filtered, {} suppressed\n",
        cen.eligible,
        cen.total(),
        cen.artist_blocked,
        cen.recording_blocked,
        cen.work_blocked,
        cen.related_blocked,
        cen.below_min_weight,
        cen.filtered,
        cen.suppressed
    );
    let after_one = rss();
    println!("first load     {:>8.0} ms   peak RSS {:>7.1} MB  (+{:.1})",
             cold.as_secs_f64() * 1000.0, mb(after_one), mb(after_one - base));

    // Hold several at once. Peak RSS only ever rises, so the FIRST load
    // measures the Director plus every one-time cost behind it -- SQLite's page
    // cache, query scratch, the allocator's first reach for pages. The MARGINAL
    // cost of one more is what a build-then-swap actually pays, and only
    // holding two at a time reveals it.
    let mut held = vec![first];
    let mut prev = after_one;
    let mut warm = std::time::Duration::ZERO;
    for n in 2..=4 {
        let t1 = Instant::now();
        held.push(lib.director().expect("another load"));
        let took = t1.elapsed();
        if n == 2 {
            warm = took;
        }
        let now = rss();
        println!("holding {n}      {:>8.0} ms   peak RSS {:>7.1} MB  (+{:.1} for this one)",
                 took.as_secs_f64() * 1000.0, mb(now), mb(now.saturating_sub(prev)));
        prev = now;
    }
    let marginal = prev.saturating_sub(after_one) / 3;
    drop(held);

    println!();
    println!("first load     ~{:.1} MB   Director + SQLite cache + query scratch",
             mb(after_one.saturating_sub(base)));
    println!("each further   ~{:.1} MB   <- what a live swap transiently costs", mb(marginal));
    println!();
    // The queue is the buffer. A rebuild is affordable when the audio already
    // queued outlasts it by a wide margin -- the cost of being wrong is a late
    // refill, never a gap, because selection is not the audio path.
    let secs = cold.as_secs_f64().max(warm.as_secs_f64());
    println!("a {:.2} s load sits inside a 180 s queue {:.0}x over -- inside one track.",
             secs, 180.0 / secs.max(0.001));
    if mb(marginal) > 30.0 {
        println!("NOTE: {:.0} MB marginal against the 150 MB budget [GDE-ARC-050] argues \
                  for drop-then-load rather than build-then-swap.", mb(marginal));
    }
}

#[cfg(test)]
mod tests {
    /// A live rebuild builds the new Director on its own thread, off the
    /// selection path, and hands it over when it is ready. That requires it to
    /// be `Send`; if it ever gains a `Connection` or an `Rc` this fails to
    /// compile, which is the point of asserting it here rather than finding out
    /// in the middle of writing the reload.
    #[test]
    fn director_can_cross_a_thread() {
        fn assert_send<T: Send>() {}
        assert_send::<lempi_player::director::library::Director>();
    }
}
