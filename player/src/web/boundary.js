// Automatic boundary detection, parameterised, for the editor's own audio
// `[SPEC-SUI-229]`. Where a passage's start, end, lead-in, lead-out and fades
// *would* land, given a set of parameters -- so the parameters can be tuned
// against real audio and the editor can offer the result as a suggestion.
//
// The same algorithm is `detect()` in `tools/analyze_amplitude.py`, and both
// are checked against `fixtures/boundary/vectors.json` -- one algorithm,
// written twice, kept from drifting apart silently, the same arrangement
// `fade.js` has with `fade.rs` `[SPEC021 §4]`. The defaults are not written
// here at all: both read `boundary-params.json`, so a value tuned in the
// editor and copied into that file is what the batch tool uses next.
//
// With the radio defaults, lead-in/lead-out are exactly the inherited
// McRhythm rule `analyze_amplitude.py` has always applied `[SPEC-SA-075]`:
// absolute thresholds (a "full_scale" reference), a quick-ramp shortcut, a
// 10 s cap. Start/end trimming is new; nothing in Lempi computed a passage's
// span from its audio before this, and the spans in the library today are
// MuLibPlay's or an ingest path's, not this.
//
// Its own file, like `fade.js`, so the check that verifies it can load
// exactly this from plain Node, with no DOM and no Web Audio.
(function (root) {
  const lin = db => Math.pow(10, db / 20);
  const dbOf = v => (v > 0 ? 20 * Math.log10(v) : -Infinity);

  // RMS over non-overlapping windows of `mono[fromSample, toSample)`. A
  // trailing partial window is dropped, not padded -- padding would only
  // ever pull an edge value toward zero. `analyze_amplitude.py`'s
  // `rms_envelope` is the same arithmetic.
  function envelope(mono, sr, fromSample, toSample, windowMs) {
    const win = Math.max(1, Math.floor((sr * windowMs) / 1000));
    const from = Math.max(0, fromSample);
    const to = Math.min(mono.length, toSample);
    const n = Math.max(0, Math.floor((to - from) / win));
    const out = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      const base = from + i * win;
      let s = 0;
      for (let j = 0; j < win; j++) {
        const v = mono[base + j];
        s += v * v;
      }
      out[i] = Math.sqrt(s / win);
    }
    return out;
  }

  // The level every "N dB below" parameter is measured from. `full_scale`
  // is 0 dBFS, which is what the inherited thresholds always meant; `peak`
  // is the loudest window; `percentile` is the loud-but-typical level, so a
  // quietly mastered track is not judged against a loud one's yardstick.
  // Digital silence is left out of the percentile -- a long silent tail
  // would otherwise drag "typical" toward nothing. Index, not interpolation,
  // so Python computes the identical number.
  function referenceDb(env, p) {
    if (p.reference === 'full_scale') return 0;
    const vals = Array.from(env).filter(v => v > 0).sort((a, b) => a - b);
    if (!vals.length) return 0;
    if (p.reference === 'peak') return dbOf(vals[vals.length - 1]);
    const q = Math.min(100, Math.max(0, p.reference_percentile)) / 100;
    return dbOf(vals[Math.floor(q * (vals.length - 1))]);
  }

  // The first index at which `k` consecutive windows all reach `thr`, or -1.
  function firstRun(env, thr, k) {
    let run = 0;
    for (let i = 0; i < env.length; i++) {
      run = env[i] >= thr ? run + 1 : 0;
      if (run >= k) return i - k + 1;
    }
    return -1;
  }
  // The last index ending a run of `k` consecutive windows at `thr`, or -1.
  function lastRun(env, thr, k) {
    let run = 0;
    for (let i = env.length - 1; i >= 0; i--) {
      run = env[i] >= thr ? run + 1 : 0;
      if (run >= k) return i + k - 1;
    }
    return -1;
  }
  function firstAt(env, thr) {
    for (let i = 0; i < env.length; i++) if (env[i] >= thr) return i;
    return -1;
  }
  function lastAt(env, thr) {
    for (let i = env.length - 1; i >= 0; i--) if (env[i] >= thr) return i;
    return -1;
  }

  // Lead-in, `[AMP-LEADIN-020]`: reaching full content quickly means no lead
  // at all; otherwise the lead runs to the first window at the lead level,
  // capped. Times are window *starts*, as the inherited rule measured them.
  function leadIn(env, w, ref, p) {
    if (!p.lead_in) return { ms: 0, why: 'off for this kind' };
    const content = firstAt(env, lin(ref - p.lead_in_content_db));
    if (content < 0) return { ms: 0, why: 'never reaches the content level' };
    if (content * w < p.lead_in_quick_ramp_ms)
      return { ms: 0, why: `content level within ${content * w} ms: a quick ramp, no lead` };
    const at = firstAt(env, lin(ref - p.lead_in_db));
    if (at < 0) return { ms: 0, why: 'never reaches the lead level' };
    const ms = Math.min(at * w, p.lead_in_max_ms);
    return { ms, why: `lead level first reached ${at * w} ms in` +
                      (ms < at * w ? `, capped at ${ms} ms` : '') };
  }

  // Lead-out, `[AMP-LEADOUT-020]`, mirrored: measured back from the end of
  // the envelope, `(n - index) * w`, matching the inherited spec's own worked
  // examples rather than `(n - 1 - index)`.
  function leadOut(env, w, ref, p) {
    if (!p.lead_out) return { ms: 0, why: 'off for this kind' };
    const n = env.length;
    const content = lastAt(env, lin(ref - p.lead_out_content_db));
    if (content < 0) return { ms: 0, why: 'never reaches the content level' };
    if ((n - content) * w < p.lead_out_quick_ramp_ms)
      return { ms: 0, why: `content level until ${(n - content) * w} ms from the end: a quick ramp, no lead` };
    const at = lastAt(env, lin(ref - p.lead_out_db));
    if (at < 0) return { ms: 0, why: 'never reaches the lead level' };
    const ms = Math.min((n - at) * w, p.lead_out_max_ms);
    return { ms, why: `lead level last held ${(n - at) * w} ms before the end` +
                      (ms < (n - at) * w ? `, capped at ${ms} ms` : '') };
  }

  // `fixed` is the de-click ramp every passage gets `[SPEC-SC-046]`; `lead`
  // fades across the lead window, so an admitted overlap is also heard as a
  // blend `[SPEC-SC-043]` -- never shorter than the fixed ramp.
  const fadeOf = (mode, fixedMs, leadMs) => (mode === 'lead' ? Math.max(leadMs, fixedMs) : fixedMs);

  // Where every point would land.
  //
  //   mono   the decoded window, one channel (the caller averages channels)
  //   sr     its sample rate
  //   t0Ms   the file position of `mono[0]`, in ms
  //   ctx    { loMs, hiMs, loIsFileEdge, hiIsFileEdge } -- where a start may
  //          be searched for and an end may be searched back from: the file's
  //          own edges, or a neighbouring passage's, or the decoded window's
  //   p      one kind's parameter set, as in `boundary-params.json`
  //
  // Returns absolute positions and durations in whole ms, the reference
  // level, the thresholds as dBFS for drawing, and one line per decision.
  function detect(mono, sr, t0Ms, ctx, p) {
    const w = p.window_ms;
    const steps = [];
    const toSample = ms => Math.round(((ms - t0Ms) / 1000) * sr);
    const lo = ctx.loMs, hi = ctx.hiMs;

    const region = envelope(mono, sr, toSample(lo), toSample(hi), w);
    const regionRef = referenceDb(region, p);
    steps.push(p.reference === 'full_scale'
      ? 'levels are measured below full scale (0 dBFS)'
      : `reference level ${regionRef.toFixed(1)} dBFS (${p.reference === 'peak'
          ? 'the loudest window' : `the ${p.reference_percentile}th percentile window`})`);

    let start = lo;
    if (!p.trim_start && ctx.loIsFileEdge) {
      steps.push('start: kept at the file\'s own start (trim at the file\'s start is off)');
    } else {
      const k = Math.max(1, Math.ceil(p.start_min_sound_ms / w));
      const i = firstRun(region, lin(regionRef - p.start_silence_db), k);
      if (i < 0) {
        steps.push(`start: no ${k * w} ms of sound above ${(regionRef - p.start_silence_db).toFixed(1)} dBFS; left at the search edge`);
      } else {
        start = Math.max(lo, lo + i * w - p.start_pad_ms);
        steps.push(`start: sound above ${(regionRef - p.start_silence_db).toFixed(1)} dBFS from ` +
                   `${((lo + i * w) / 1000).toFixed(1)} s` + (p.start_pad_ms ? `, less ${p.start_pad_ms} ms` : ''));
      }
    }

    let end = hi;
    if (!p.trim_end && ctx.hiIsFileEdge) {
      steps.push('end: kept at the file\'s own end (trim at the file\'s end is off)');
    } else {
      const k = Math.max(1, Math.ceil(p.end_min_sound_ms / w));
      const j = lastRun(region, lin(regionRef - p.end_silence_db), k);
      if (j < 0) {
        steps.push(`end: no ${k * w} ms of sound above ${(regionRef - p.end_silence_db).toFixed(1)} dBFS; left at the search edge`);
      } else {
        end = Math.min(hi, lo + (j + 1) * w + p.end_pad_ms);
        steps.push(`end: sound above ${(regionRef - p.end_silence_db).toFixed(1)} dBFS until ` +
                   `${((lo + (j + 1) * w) / 1000).toFixed(1)} s` + (p.end_pad_ms ? `, plus ${p.end_pad_ms} ms` : ''));
      }
    }
    if (end <= start) {
      steps.push('start and end crossed; both left at the search edges');
      start = lo;
      end = hi;
    }

    // Leads are measured on the passage as detected, aligned to its own
    // start -- exactly what the batch tool measures on a stored passage.
    const span = envelope(mono, sr, toSample(start), toSample(end), w);
    const spanRef = referenceDb(span, p);
    const li = leadIn(span, w, spanRef, p);
    const lo2 = leadOut(span, w, spanRef, p);
    const len = end - start;
    const leadInMs = Math.min(li.ms, len), leadOutMs = Math.min(lo2.ms, len);
    steps.push(`lead-in ${leadInMs} ms: ${li.why}`);
    steps.push(`lead-out ${leadOutMs} ms: ${lo2.why}`);

    const fadeInMs = Math.min(fadeOf(p.fade_in_mode, p.fade_in_ms, leadInMs), len);
    const fadeOutMs = Math.min(fadeOf(p.fade_out_mode, p.fade_out_ms, leadOutMs), len);
    steps.push(`fade-in ${fadeInMs} ms ${p.fade_in_curve}` +
               (p.fade_in_mode === 'lead' ? ' (across the lead-in)' : ''));
    steps.push(`fade-out ${fadeOutMs} ms ${p.fade_out_curve}` +
               (p.fade_out_mode === 'lead' ? ' (across the lead-out)' : ''));

    return {
      start_ms: Math.round(start),
      end_ms: Math.round(end),
      lead_in_ms: Math.round(leadInMs),
      lead_out_ms: Math.round(leadOutMs),
      fade_in_ms: Math.round(fadeInMs),
      fade_out_ms: Math.round(fadeOutMs),
      fade_in_curve: p.fade_in_curve,
      fade_out_curve: p.fade_out_curve,
      reference_db: spanRef,
      levels: [
        { name: 'start silence', dbfs: regionRef - p.start_silence_db, on: p.trim_start || !ctx.loIsFileEdge },
        { name: 'end silence', dbfs: regionRef - p.end_silence_db, on: p.trim_end || !ctx.hiIsFileEdge },
        { name: 'lead-in content', dbfs: spanRef - p.lead_in_content_db, on: p.lead_in },
        { name: 'lead-in level', dbfs: spanRef - p.lead_in_db, on: p.lead_in },
        { name: 'lead-out content', dbfs: spanRef - p.lead_out_content_db, on: p.lead_out },
        { name: 'lead-out level', dbfs: spanRef - p.lead_out_db, on: p.lead_out },
      ],
      steps,
    };
  }

  // What the editor's parameter panel shows: one row per idea, a column
  // per side where the idea has two. `hint` is the row's tooltip.
  const ROWS = [
    { group: 'Level', label: 'window', keys: ['window_ms'], unit: 'ms', type: 'number',
      hint: 'RMS window length. Every time below is a whole number of windows.' },
    { group: 'Level', label: 'measured from', keys: ['reference'], type: 'select',
      options: ['full_scale', 'peak', 'percentile'],
      hint: 'What "dB below" means: 0 dBFS, the loudest window, or a percentile of the non-silent windows.' },
    { group: 'Level', label: 'percentile', keys: ['reference_percentile'], unit: '%', type: 'number',
      hint: 'Used only when measured from "percentile".' },

    { group: 'Start / end', label: 'trim at the file edge', keys: ['trim_start', 'trim_end'], type: 'bool',
      hint: 'Off keeps the file\'s own edge. Against a neighbouring passage the edge is always found from the audio.' },
    { group: 'Start / end', label: 'silence below', keys: ['start_silence_db', 'end_silence_db'], unit: 'dB', type: 'number',
      hint: 'Quieter than this many dB below the reference counts as silence.' },
    { group: 'Start / end', label: 'sustained sound', keys: ['start_min_sound_ms', 'end_min_sound_ms'], unit: 'ms', type: 'number',
      hint: 'How long sound must last to count, so a click is not the start.' },
    { group: 'Start / end', label: 'keep beyond', keys: ['start_pad_ms', 'end_pad_ms'], unit: 'ms', type: 'number',
      hint: 'Margin kept outside the first/last sound.' },

    { group: 'Lead-in / lead-out', label: 'detect', keys: ['lead_in', 'lead_out'], type: 'bool',
      hint: 'Off gives a lead of 0, as an album cut has.' },
    { group: 'Lead-in / lead-out', label: 'content level', keys: ['lead_in_content_db', 'lead_out_content_db'], unit: 'dB', type: 'number',
      hint: 'dB below the reference that counts as the music proper. Reached within the quick-ramp time means no lead.' },
    { group: 'Lead-in / lead-out', label: 'lead level', keys: ['lead_in_db', 'lead_out_db'], unit: 'dB', type: 'number',
      hint: 'dB below the reference where the lead ends (in) or begins (out).' },
    { group: 'Lead-in / lead-out', label: 'quick ramp', keys: ['lead_in_quick_ramp_ms', 'lead_out_quick_ramp_ms'], unit: 'ms', type: 'number',
      hint: 'Content level this close to the edge means the passage starts/ends at full volume: lead 0.' },
    { group: 'Lead-in / lead-out', label: 'at most', keys: ['lead_in_max_ms', 'lead_out_max_ms'], unit: 'ms', type: 'number',
      hint: 'Cap on the lead.' },

    { group: 'Fade-in / fade-out', label: 'mode', keys: ['fade_in_mode', 'fade_out_mode'], type: 'select',
      options: ['fixed', 'lead'],
      hint: '"fixed" is the de-click ramp; "lead" fades across the lead window, never shorter than the fixed length.' },
    { group: 'Fade-in / fade-out', label: 'length', keys: ['fade_in_ms', 'fade_out_ms'], unit: 'ms', type: 'number',
      hint: 'The fixed ramp length.' },
    { group: 'Fade-in / fade-out', label: 'curve', keys: ['fade_in_curve', 'fade_out_curve'], type: 'select',
      options: ['linear', 'cosine', 'exponential'], hint: 'The ramp shape, as fade.rs plays it.' },
  ];

  const api = { envelope, referenceDb, detect, ROWS, dbOf };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.LempiBoundary = api;
})(typeof window !== 'undefined' ? window : undefined);
