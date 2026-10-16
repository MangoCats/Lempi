// Waveform boundary editing [REQ-LIB-175], [SPEC021].
//
// Fetch the passage's current boundaries and its raw audio, decode it
// client-side, and draw a peak waveform with six draggable markers and a
// gain field over it. A drag previews on release, not on every pixel of
// motion `[SPEC021 §4]` -- decoding audio on every frame of a drag is where
// a "real-time" editor stops feeling real-time. "Save edit" posts the draft
// to `boundary_reviews`; nothing here ever touches `passages` itself
// `[SPEC-SUI-140]`.
//
// A zoomable/pannable viewport, precise numeric fields, undo, and pausing
// the main transport were added `[SPEC-SUI-217..220]` after using this
// editor against a real multi-minute file: a fixed-width canvas mapping the
// whole file to one screen made a lead-in a few pixels wide, the only way to
// grab a marker was a blind 10px guess on a bare line, there was no way back
// from a mistake, and the server's own playback kept running underneath the
// browser's preview with no way to silence it from here.
//
// Fade-in/fade-out markers and curve pickers were added `[SPEC-SUI-226]`:
// this passage's own volume envelope, independent of lead -- lead only times
// when a crossfade with a neighbour is *permitted*, and was never itself a
// gain ramp during ordinary playback, so the preview now applies
// `fade_in_ms`/`fade_out_ms` (with their own selected curve), not
// `lead_in_ms`/`lead_out_ms`, matching what real playback actually does.
//
// Automatic detection was added `[SPEC-SUI-230]`: the parameters the
// algorithm runs with, for radio and album, editable here and run against
// this passage's own decoded audio, with where every point would land drawn
// as dashed markers. Built because the spans in the library are mostly not
// Lempi's own -- passage 7439's end, MuLibPlay's, cut 15 s of audible fade --
// and tuning an algorithm needs a place to watch it work on real audio.
(() => {
  const $ = id => document.getElementById(id);
  const note = (text, cls = '') => {
    const n = $('note');
    n.textContent = text;
    n.className = 'note' + (cls ? ' ' + cls : '');
  };

  Lempi.startBare();

  const passageId = (() => {
    const m = location.pathname.match(/\/edit\/(\d+)/);
    return m ? Number(m[1]) : null;
  })();

  const fmt = ms => {
    if (ms == null) return '—';
    const s = ms / 1000;
    const m = Math.floor(s / 60);
    const r = (s - m * 60).toFixed(3);
    return `${m}:${r.padStart(6, '0')}`;
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  let base = null;    // the last-loaded-or-saved values, for the dirty check
  let draft = null;   // the values being edited -- same nine fields
  let buffer = null;  // the decoded AudioBuffer
  let audioCtx = null;
  let source = null;  // the currently-playing AudioBufferSourceNode, or null
  let playedAt = 0;   // audioCtx.currentTime when the current playback began
  let playedFromMs = 0; // draft-relative ms the current playback began at
  let dragging = null; // 'start' | 'end' | 'leadIn' | 'leadOut' | 'fadeIn' | 'fadeOut' | null
  let fileMs = 0;      // the file's own duration, for the facts line only
  // The decoded `buffer`'s own sample 0 is this many ms into the file, not
  // necessarily ms 0 `[SPEC-SUI-224]` -- a DAO capture is only ever decoded
  // in a bounded window around the passage being edited, never the whole
  // file. Every place converting an absolute ms (draft/view, both still
  // file-absolute throughout, matching what the server stores and what the
  // facts line shows) into a *sample index* has to subtract this first;
  // everywhere else -- view bounds, drag math, zoom -- stays in the same
  // absolute space it already used and needs no change.
  let windowFromMs = 0;
  // How much context to decode on each side of the passage's own span
  // `[SPEC-SUI-224]` -- generous for any real edit, small next to what an
  // hours-long capture was costing before this existed.
  const WINDOW_PAD_MS = 60_000;
  // The boundary as loaded, captured once and never overwritten by a save --
  // unlike `base`, which resets to `draft` on every commit `[SPEC-SUI-140]`.
  // The best available stand-in, without a new server read, for "the span
  // flavor was last computed against": correct for the ordinary case of one
  // edit, one save, since nothing else moves `passages.end_ms` in between.
  let loadedBoundary = null;
  let info = null;      // `/info` as loaded -- kind and neighbours, for detection
  let mono = null;      // the decoded window, channels averaged, for detection

  const canvas = $('wave');
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

  const totalMs = () => (buffer ? buffer.duration * 1000 : 0);
  // The decoded window's own end, in whole ms `[SPEC-SUI-228]` -- wherever
  // `totalMs()` becomes a *boundary* for `start_ms`/`end_ms` rather than
  // just a view extent, it has to be rounded first: `buffer.duration` comes
  // from a sample count over a sample rate and is essentially never an
  // integer, so a drag or a typed value landing exactly on this ceiling
  // otherwise saves as a float, which the server's `u64` fields reject
  // outright rather than truncating.
  const windowEndMs = () => Math.round(windowFromMs + totalMs());

  // The viewport: what span of the file the canvas currently shows. Distinct
  // from the passage's own start/end -- "what you can see" and "what the
  // passage is" are different questions, and conflating them is why the old
  // single fixed-width canvas made a 40ms lead-in impossible to see, let
  // alone drag, on a four-minute file `[SPEC-SUI-218]`.
  let view = { fromMs: 0, toMs: 0 };
  const MIN_SPAN_MS = 200;

  function clampView(fromMs, toMs) {
    const total = totalMs();
    const cap = total || MIN_SPAN_MS;
    let span = clamp(toMs - fromMs, Math.min(MIN_SPAN_MS, cap), cap);
    // Bounded by the decoded window, not by ms 0 -- `windowFromMs` is 0 for
    // the ordinary case (decoding starts at the file's own beginning) and
    // only ever nonzero for a bounded window into a large DAO capture
    // `[SPEC-SUI-224]`.
    const from = clamp(fromMs, windowFromMs, Math.max(windowFromMs, windowFromMs + total - span));
    return { fromMs: from, toMs: from + span };
  }
  function setView(fromMs, toMs) {
    view = clampView(fromMs, toMs);
    // Against `fileMs` (the file's own true duration, from `/info`), not
    // `totalMs()` -- the latter is only the *decoded window's* own
    // duration, which reads as nonsense beside two absolute positions once
    // that window does not start at ms 0 `[SPEC-SUI-224]`.
    $('viewrange').textContent =
      `showing ${fmt(view.fromMs)} – ${fmt(view.toMs)} of ${fmt(fileMs || null)}`;
    draw();
  }
  function zoomStep(factor) {
    const mid = (view.fromMs + view.toMs) / 2;
    const span = (view.toMs - view.fromMs) * factor;
    setView(mid - span / 2, mid + span / 2);
  }

  // A marker outside the current view is not "hard to see," it is not drawn
  // at all -- `px(ms)` maps it off the edge of the canvas, and nothing about
  // that looks different from the marker simply not existing. A lead-in a
  // few hundred ms wide and a lead-out four minutes away on the same file
  // are rarely both in frame at once at any zoom worth looking at either one
  // closely with, so touching a field for one re-centres the view on it,
  // rather than requiring a person to already know this page has separate
  // zoom controls at all.
  //
  // `minSpanMs`, when given, also re-zooms even when `ms` is technically
  // already in view: a 4.5s lead-out on a five-minute file is ~1.5% of a
  // whole-passage view -- a handful of pixels, present but indistinguishable
  // from the end marker beside it, which reads exactly like "not there." The
  // same is true of a 300ms nudge to `end_ms` on a wide "Zoom out" view --
  // technically still on screen, but sub-pixel, which reported live as
  // "changes in end time are not reflected" even though `draw()` genuinely
  // ran every time `[SPEC-SUI-225]`. Bringing a point on screen is not the
  // same claim as making it legible.
  //
  // Given a magnitude -- a lead's own length, or how far a start/end edit
  // just moved -- six times that much breathing room reads as an actual
  // change; the same distance inside a five-minute whole-passage view reads
  // as a stray pixel. The floor keeps a near-zero magnitude (most edits are
  // small nudges, not big moves) from zooming in absurdly far.
  const revealSpan = magnitudeMs => Math.max(magnitudeMs * 6, 1500);

  function ensureVisible(ms, minSpanMs) {
    if (!buffer) return;
    const spanNow = view.toMs - view.fromMs;
    const inView = ms >= view.fromMs && ms <= view.toMs;
    if (inView && (!minSpanMs || spanNow <= minSpanMs)) return;
    const span = minSpanMs ? Math.max(minSpanMs, MIN_SPAN_MS) : spanNow;
    setView(ms - span / 2, ms + span / 2);
  }

  // CSS-pixel space, shared by drawing, hit-testing and click/pan math, so
  // all three agree about where a millisecond actually sits.
  //
  // `EDGE_PAD_CSS` reserves a margin at each side so a marker exactly at the
  // view's own boundary is never clipped in half against the canvas edge
  // `[SPEC-SUI-222]` -- real, not hypothetical: `start_ms` is 0 and `end_ms`
  // is the file's own duration for most passages in this library (one
  // track, one file), so "Whole passage" and "Zoom out" routinely put
  // both blue markers *exactly* on the canvas boundary, where a knob drawn
  // centred on the edge is half off-canvas and a line there reads as the
  // canvas's own border, not as a marker at all.
  const EDGE_PAD_CSS = 2;
  const msToX = (ms, widthCss) => {
    const span = Math.max(1, view.toMs - view.fromMs);
    const usable = Math.max(1, widthCss - 2 * EDGE_PAD_CSS);
    return EDGE_PAD_CSS + ((ms - view.fromMs) / span) * usable;
  };
  const xToMs = (xCss, widthCss) => {
    const span = Math.max(1, view.toMs - view.fromMs);
    const usable = Math.max(1, widthCss - 2 * EDGE_PAD_CSS);
    return view.fromMs + ((xCss - EDGE_PAD_CSS) / usable) * span;
  };
  const msOf = x => xToMs(x, canvas.getBoundingClientRect().width || canvas.width);

  const dirty = () =>
    !base ||
    draft.start_ms !== base.start_ms ||
    draft.end_ms !== base.end_ms ||
    draft.lead_in_ms !== base.lead_in_ms ||
    draft.lead_out_ms !== base.lead_out_ms ||
    Math.abs(draft.gain_db - base.gain_db) > 1e-9 ||
    draft.fade_in_ms !== base.fade_in_ms ||
    draft.fade_out_ms !== base.fade_out_ms ||
    draft.fade_in_curve !== base.fade_in_curve ||
    draft.fade_out_curve !== base.fade_out_curve;

  // Every completed change to the draft passes through here, which makes it
  // the one place the detection table's "now" column follows the draft --
  // and, with "keep my start/end", the place detection re-runs from.
  const refreshCommitState = () => {
    $('commit').disabled = !dirty();
    renderDetectTable();
    if ($('detspan').checked) scheduleDetect();
  };

  // Undo: one stack of prior drafts, pushed once per *completed* edit -- a
  // drag's own motion, or a field's own keystrokes, never touch it, matching
  // the "preview on release, not per-pixel" rule this file already followed
  // for playback `[SPEC-SUI-220]`.
  let undoStack = [];
  const UNDO_CAP = 50;
  function pushUndo() {
    if (!draft) return;
    undoStack.push({ ...draft });
    if (undoStack.length > UNDO_CAP) undoStack.shift();
    $('undo').disabled = false;
  }
  function undo() {
    if (!undoStack.length) return;
    stopPreview();
    draft = undoStack.pop();
    $('undo').disabled = undoStack.length === 0;
    updateFacts();
    updatePreciseFields();
    $('gain').value = draft.gain_db.toFixed(1);
    refreshCommitState();
    draw();
  }
  $('undo').addEventListener('click', undo);

  async function load() {
    if (!passageId) {
      note('No passage id in the URL.', 'bad');
      return;
    }

    // Two audio engines can otherwise play the same passage at once: the
    // server's own transport, which runs regardless of any browser tab, and
    // this page's own preview. Silencing the server side once, on entry, is
    // simpler and more reliable than trying to keep both in sync
    // `[SPEC-SUI-217]`. Best-effort -- a failed pause is not worth blocking
    // the editor over.
    fetch('/command/pause', { method: 'POST' }).catch(() => {});
    $('pausenote').textContent =
      'The main player was paused so only this preview plays. Its own Play ' +
      'button on the player page resumes it when you are done here.';

    // Asked for alongside `/info`, not after it -- neither depends on the other.
    const defaultsWanted = fetch('/boundary-params.json')
      .then(r => (r && r.ok ? r.json() : null))
      .catch(() => null);

    const infoResp = await fetch(`/edit/${passageId}/info`).catch(() => null);
    if (!infoResp || !infoResp.ok) {
      note(`Passage ${passageId} does not exist, or its info could not be read.`, 'bad');
      return;
    }
    info = await infoResp.json();
    base = {
      start_ms: info.start_ms, end_ms: info.end_ms,
      lead_in_ms: info.lead_in_ms, lead_out_ms: info.lead_out_ms,
      gain_db: info.gain_db,
      fade_in_ms: info.fade_in_ms, fade_out_ms: info.fade_out_ms,
      fade_in_curve: info.fade_in_curve, fade_out_curve: info.fade_out_curve,
    };
    draft = { ...base };
    loadedBoundary = { start_ms: base.start_ms, end_ms: base.end_ms };
    fileMs = info.file_ms;
    $('gain').value = draft.gain_db.toFixed(1);
    $('gain').disabled = false;
    updateFacts();
    updatePreciseFields();
    for (const id of ['startms', 'endms', 'leadinms', 'leadoutms',
                       'fadeinms', 'fadeoutms', 'fadeincurve', 'fadeoutcurve']) {
      $(id).disabled = false;
    }
    setUpDetection(await defaultsWanted);

    note('Loading audio…');
    // A bounded window around the passage, never the whole file
    // `[SPEC-SUI-224]` -- decoding a real 4h05m, 324.7 MB DAO capture
    // client-side left the page with no waveform and an unresponsive Play,
    // hanging on ~10 GB of interleaved f32 PCM the browser was asked to
    // produce for one waveform. The server now decodes only this span,
    // through the same seek-accurate decoder the real player uses, and
    // returns it as a WAV. For the near-totality of the library --
    // single-track files where `start_ms=0`/`end_ms=file_ms` already --
    // this window already equals the whole file, so nothing about the
    // ordinary case changes beyond "now decoded server-side."
    windowFromMs = Math.max(0, base.start_ms - WINDOW_PAD_MS);
    const windowToMs = Math.min(fileMs || Infinity, base.end_ms + WINDOW_PAD_MS);
    const audioResp = await fetch(
      `/edit/${passageId}/audio?from_ms=${windowFromMs}&to_ms=${windowToMs}`,
    ).catch(() => null);
    if (!audioResp || !audioResp.ok) {
      note('Could not read the audio for this passage.', 'bad');
      return;
    }
    const bytes = await audioResp.arrayBuffer();

    const Ctx = window.AudioContext || window.webkitAudioContext;
    audioCtx = new Ctx();
    try {
      buffer = await audioCtx.decodeAudioData(bytes);
    } catch (e) {
      note('The audio could not be decoded by this browser.', 'bad');
      return;
    }

    $('play').disabled = false;
    for (const id of ['zoomout', 'zoomin', 'zoompassage', 'zoomfile']) $(id).disabled = false;
    // One channel for detection, the average of all of them -- what
    // `analyze_amplitude.py` asks ffmpeg for (`-ac 1`), so both measure the
    // same overall level rather than one side of a stereo image.
    mono = new Float32Array(buffer.length);
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      const ch = buffer.getChannelData(c);
      for (let i = 0; i < ch.length; i++) mono[i] += ch[i] / buffer.numberOfChannels;
    }
    setView(windowFromMs, windowFromMs + totalMs());
    updateFacts(); // the partial-window note depends on `windowFromMs`/`totalMs()`, both just set
    runDetect();

    // The server clamps to `EDIT_AUDIO_MAX_MS` regardless of what was asked,
    // and a library not yet repaired can still store an `end_ms` past what
    // `PassageDecoder` actually finds -- caught here against the *requested*
    // window rather than the file's own stored duration, since decoding
    // always goes through real seeking now, never trusting stored duration
    // at all `[SPEC-SUI-224]`.
    const decodedMs = totalMs();
    const requestedMs = windowToMs - windowFromMs;
    const overrunMs = requestedMs - decodedMs;
    if (overrunMs > 1000) {
      note(
        `This browser decoded ${fmt(decodedMs)} of real audio, but ` +
        `${fmt(requestedMs)} was requested around this passage -- ` +
        `${fmt(overrunMs)} short. The end and any lead-out past ${fmt(decodedMs)} ` +
        `cannot be shown or reached at any zoom until the library's own stored ` +
        `boundary is corrected (see tools/repair_durations.py); not a fault in ` +
        `this page.`,
        'bad',
      );
      return;
    }

    note(info.edited ? 'Showing a saved-but-not-yet-applied edit.' : '');
  }

  function updateFacts() {
    $('facts').innerHTML =
      `start <b>${fmt(draft.start_ms)}</b> · end <b>${fmt(draft.end_ms)}</b> · ` +
      `lead-in <b>${draft.lead_in_ms} ms</b> · lead-out <b>${draft.lead_out_ms} ms</b> · ` +
      `fade-in <b>${draft.fade_in_ms} ms ${draft.fade_in_curve}</b> · ` +
      `fade-out <b>${draft.fade_out_ms} ms ${draft.fade_out_curve}</b> · ` +
      `gain <b>${draft.gain_db.toFixed(2)} dB</b> · file <b>${fmt(fileMs || null)}</b>` +
      // Which cut this is and where its stored span came from -- most of the
      // library's spans are MuLibPlay's (`inherited:mulib`), not anything
      // Lempi computed, which is worth knowing before trusting one.
      (info && info.kind
        ? ` · <b>${esc(info.kind)}</b> cut, span from <b>${esc(info.boundary_src || 'unknown')}</b>`
        : '') +
      // Only a bounded window around the passage is ever decoded for a
      // large DAO capture `[SPEC-SUI-224]` -- said plainly here, since
      // "Zoom out" quietly stopping short of the file's own edges would
      // otherwise look like a bug rather than the deliberate limit it is.
      // Expanding the window on request is real future work, not solved
      // here, the same honest-deferral shape `[SPEC021 §6]` already uses
      // for the neighbour-passage question.
      (buffer && totalMs() < (fileMs || 0) - 1000
        ? ` · <b class="win">showing ±${Math.round(WINDOW_PAD_MS / 1000)}s around ` +
          `this passage, not the whole file</b>`
        : '');
    updateFlavorNote();
  }

  // A small trim -- a few hundred ms off a click or a pop -- does not make
  // flavor meaningfully wrong, and offering a re-analysis for every such
  // edit would train a person to ignore the offer. A boundary that has
  // actually moved a long way is a different question, worth surfacing
  // before the save that makes it easy to forget about `[SPEC-SUI-223]`.
  // 5000ms, the same number `WHOLE_FILE_SLACK_MS` uses elsewhere in this
  // codebase for an unrelated reason -- coincidence, not a shared constant.
  const FLAVOR_SUGGEST_MS = 5000;
  function updateFlavorNote() {
    const el = $('flavornote');
    if (!draft || !loadedBoundary) { el.textContent = ''; return; }
    const movedStart = Math.abs(draft.start_ms - loadedBoundary.start_ms);
    const movedEnd = Math.abs(draft.end_ms - loadedBoundary.end_ms);
    if (Math.max(movedStart, movedEnd) > FLAVOR_SUGGEST_MS) {
      el.textContent =
        'This moves the boundary well past where flavor was last analysed. ' +
        'Consider re-analyzing flavor for this passage from its profile page ' +
        'in Vipunen after saving.';
      el.className = 'note warn';
    } else {
      el.textContent = '';
      el.className = 'note';
    }
  }

  function updatePreciseFields() {
    $('startms').value = draft.start_ms;
    $('endms').value = draft.end_ms;
    $('leadinms').value = draft.lead_in_ms;
    $('leadoutms').value = draft.lead_out_ms;
    $('fadeinms').value = draft.fade_in_ms;
    $('fadeoutms').value = draft.fade_out_ms;
    $('fadeincurve').value = draft.fade_in_curve;
    $('fadeoutcurve').value = draft.fade_out_curve;
  }

  // One min/max pair per horizontal pixel `[SPEC021 §4]`, over whatever span
  // the viewport currently shows rather than always the whole buffer -- the
  // same cost per redraw either way, since it is still one pass over `w`
  // columns; only which samples fall in each column changes.
  function draw() {
    // Nothing to draw before the audio decodes, and nowhere to draw it in an
    // environment with no real canvas (this file's own jsdom check among
    // them) -- both are "there is nothing here yet," not an error.
    if (!buffer) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round((rect.width || 1200) * dpr));
    canvas.height = Math.max(1, Math.round(220 * dpr));
    const g = canvas.getContext('2d');
    if (!g) return;
    const w = canvas.width, h = canvas.height;
    g.clearRect(0, 0, w, h);

    const data = buffer.getChannelData(0);
    const sr = buffer.sampleRate;
    const span = Math.max(1, view.toMs - view.fromMs);
    const mid = h / 2;
    // The same padded coordinate space `msOf`/`handleAt` use, scaled from
    // CSS to canvas pixels -- the waveform, its shading and its markers all
    // have to agree about where a millisecond sits, edge padding included,
    // or a peak drawn flush to the raw canvas edge would visibly disagree
    // with a start/end line now deliberately inset from it.
    const padPx = EDGE_PAD_CSS * dpr;
    const usable = Math.max(1, w - 2 * padPx);
    const px = ms => padPx + ((ms - view.fromMs) / span) * usable;

    g.strokeStyle = '#9ad';
    g.lineWidth = Math.max(1, dpr);
    g.beginPath();
    for (let x = Math.floor(padPx); x < w - padPx; x++) {
      const msLo = view.fromMs + ((x - padPx) / usable) * span;
      const msHi = view.fromMs + ((x + 1 - padPx) / usable) * span;
      const lo = Math.max(0, Math.floor(((msLo - windowFromMs) / 1000) * sr));
      const hi = Math.min(data.length, Math.max(lo + 1, Math.ceil(((msHi - windowFromMs) / 1000) * sr)));
      let min = 1, max = -1;
      for (let i = lo; i < hi; i++) {
        const v = data[i];
        if (v < min) min = v;
        if (v > max) max = v;
      }
      if (min > max) { min = 0; max = 0; }
      g.moveTo(x, mid + min * mid);
      g.lineTo(x, mid + max * mid);
    }
    g.stroke();
    // Two independent semi-transparent layers, not one hand-blended region --
    // lead and fade legitimately overlap either way `[XFD-ORTH-010]`, and the
    // canvas's own alpha compositing shows that honestly wherever they do.
    const shade = (fromMs, toMs, color) => {
      if (toMs <= fromMs) return;
      g.fillStyle = color;
      g.fillRect(px(fromMs), 0, px(toMs) - px(fromMs), h);
    };
    shade(draft.start_ms, draft.start_ms + draft.lead_in_ms, 'rgba(255,187,102,.18)');
    shade(draft.end_ms - draft.lead_out_ms, draft.end_ms, 'rgba(255,187,102,.18)');
    shade(draft.start_ms, draft.start_ms + draft.fade_in_ms, 'rgba(167,139,250,.22)');
    shade(draft.end_ms - draft.fade_out_ms, draft.end_ms, 'rgba(167,139,250,.22)');

    const line = (ms, color, width) => {
      const x = px(ms);
      g.strokeStyle = color;
      g.lineWidth = width * dpr;
      g.beginPath();
      g.moveTo(x, 0);
      g.lineTo(x, h);
      g.stroke();
    };
    line(draft.start_ms, '#6cf', 2);
    line(draft.end_ms, '#6cf', 2);
    line(draft.start_ms + draft.lead_in_ms, '#fb6', 1.5);
    line(draft.end_ms - draft.lead_out_ms, '#fb6', 1.5);
    line(draft.start_ms + draft.fade_in_ms, '#a78bfa', 1.5);
    line(draft.end_ms - draft.fade_out_ms, '#a78bfa', 1.5);

    if ($('dettrace').checked && mono && params) drawTrace(g, w, h, dpr, padPx, usable, span, px);

    // Where detection would put each point `[SPEC-SUI-230]`: dashed, in the
    // same colour as the marker it suggests a value for, with a small
    // triangle on the bottom edge -- so a suggestion that coincides with the
    // real marker is still visibly there, and the knob rail at the top stays
    // reserved for things that can be dragged.
    if (suggestion) {
      const s = suggestion;
      const marks = [
        [s.start_ms, '#6cf'], [s.end_ms, '#6cf'],
        [s.start_ms + s.lead_in_ms, '#fb6'], [s.end_ms - s.lead_out_ms, '#fb6'],
        [s.start_ms + s.fade_in_ms, '#a78bfa'], [s.end_ms - s.fade_out_ms, '#a78bfa'],
      ];
      g.setLineDash([5 * dpr, 4 * dpr]);
      for (const [ms, color] of marks) line(ms, color, 1.25);
      g.setLineDash([]);
      for (const [ms, color] of marks) {
        const x = px(ms);
        g.fillStyle = color;
        g.beginPath();
        g.moveTo(x, h - 9 * dpr);
        g.lineTo(x - 5 * dpr, h);
        g.lineTo(x + 5 * dpr, h);
        g.fill();
      }
    }

    // A knob near the top of each line -- an actual grabbable shape, not
    // just a place on a line that happens to respond to a drag
    // `[SPEC-SUI-219]`. `handleAt` only recognises a pointer this close to
    // the top of the canvas as "on a handle" at all, so the rest of the
    // canvas is unambiguously pan/click territory.
    const knob = (ms, color) => {
      const x = px(ms);
      g.fillStyle = color;
      g.beginPath();
      g.arc(x, HANDLE_BAND_PX * 0.55 * dpr, 5 * dpr, 0, Math.PI * 2);
      g.fill();
    };
    knob(draft.start_ms, '#6cf');
    knob(draft.end_ms, '#6cf');
    knob(draft.start_ms + draft.lead_in_ms, '#fb6');
    knob(draft.end_ms - draft.lead_out_ms, '#fb6');
    knob(draft.start_ms + draft.fade_in_ms, '#a78bfa');
    knob(draft.end_ms - draft.fade_out_ms, '#a78bfa');

    if (source) {
      const elapsed = (audioCtx.currentTime - playedAt) * 1000;
      line(draft.start_ms + playedFromMs + elapsed, '#fff', 1.5);
    }
  }

  // Which marker a pointer is close enough to grab, and only near the top of
  // the canvas -- the knob rail. Below that band every pointer gesture is
  // pan-or-click, never a marker drag, so there is no blind guessing about
  // which one a click near the middle of the canvas meant `[SPEC-SUI-219]`.
  const HANDLE_BAND_PX = 28;
  function handleAt(x, y) {
    if (y > HANDLE_BAND_PX) return null;
    const w = canvas.getBoundingClientRect().width || canvas.width;
    const tolerancePx = 12;
    const candidates = [
      ['leadIn', draft.start_ms + draft.lead_in_ms],
      ['leadOut', draft.end_ms - draft.lead_out_ms],
      ['fadeIn', draft.start_ms + draft.fade_in_ms],
      ['fadeOut', draft.end_ms - draft.fade_out_ms],
      ['start', draft.start_ms],
      ['end', draft.end_ms],
    ];
    let best = null, bestDist = tolerancePx;
    for (const [name, ms] of candidates) {
      const d = Math.abs(msToX(ms, w) - x);
      if (d < bestDist) { bestDist = d; best = name; }
    }
    return best;
  }

  function applyDrag(which, ms) {
    // Bounded by the decoded window's own absolute span, not by ms 0 --
    // `windowFromMs` is 0 for the ordinary whole-file case `[SPEC-SUI-224]`.
    // Rounded, like the ceiling it bounds `[SPEC-SUI-228]`: `totalMs()` comes
    // from the buffer's sample count over its sample rate and is essentially
    // never a whole millisecond, so a drag that lands exactly on this edge
    // would otherwise hand `start`/`end` that same fraction.
    const windowToMs = windowEndMs();
    // `ms` itself is pixel-derived (`xToMs`), so it is fractional on every
    // ordinary drag, not just at a clamped edge -- rounded here for the same
    // reason `leadIn`/`leadOut`/`fadeIn`/`fadeOut` already round just below:
    // `start_ms`/`end_ms` are the server's `u64` fields `[SPEC-SUI-228]`,
    // and a fractional value that was never caught here reached `/review`'s
    // JSON body unrounded, which the server rejects outright rather than
    // truncating -- found live, dragging the end marker.
    if (which === 'start') draft.start_ms = Math.round(clamp(ms, windowFromMs, draft.end_ms - 1));
    else if (which === 'end') draft.end_ms = Math.round(clamp(ms, draft.start_ms + 1, windowToMs));
    else if (which === 'leadIn')
      draft.lead_in_ms = Math.round(clamp(ms - draft.start_ms, 0, draft.end_ms - draft.start_ms));
    else if (which === 'leadOut')
      draft.lead_out_ms = Math.round(clamp(draft.end_ms - ms, 0, draft.end_ms - draft.start_ms));
    // Independently constrained inside [start, end], not nested inside
    // lead's own span -- lead and fade are orthogonal `[XFD-ORTH-010]` and
    // legitimately overlap either way `[SPEC-SUI-226]`.
    else if (which === 'fadeIn')
      draft.fade_in_ms = Math.round(clamp(ms - draft.start_ms, 0, draft.end_ms - draft.start_ms));
    else if (which === 'fadeOut')
      draft.fade_out_ms = Math.round(clamp(draft.end_ms - ms, 0, draft.end_ms - draft.start_ms));
  }

  // Pan vs. click-to-jump, told apart by how far the pointer actually moved
  // `[SPEC-SUI-218]`: a gesture that never crosses the threshold is a click
  // (jump playback there); one that does is a pan (move the viewport, touch
  // nothing else). A gesture that starts on a knob is neither -- it drags
  // that marker, exactly as before.
  const PAN_THRESHOLD_PX = 4;
  let panStart = null; // { x, fromMs, toMs }
  let didPan = false;

  canvas.addEventListener('pointerdown', e => {
    if (!draft || !buffer) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    dragging = handleAt(x, y);
    canvas.setPointerCapture(e.pointerId);
    if (dragging) {
      pushUndo();
      return;
    }
    panStart = { x, fromMs: view.fromMs, toMs: view.toMs };
    didPan = false;
  });
  canvas.addEventListener('pointermove', e => {
    const rect = canvas.getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, 0, rect.width);
    const y = e.clientY - rect.top;
    if (dragging) {
      applyDrag(dragging, msOf(x));
      updateFacts();
      updatePreciseFields();
      draw();
      return;
    }
    if (panStart) {
      const dx = x - panStart.x;
      if (didPan || Math.abs(dx) >= PAN_THRESHOLD_PX) {
        didPan = true;
        canvas.classList.add('panning');
        const span = panStart.toMs - panStart.fromMs;
        const w = rect.width || canvas.width;
        const deltaMs = (dx / Math.max(1, w - 2 * EDGE_PAD_CSS)) * span;
        setView(panStart.fromMs - deltaMs, panStart.toMs - deltaMs);
      }
      return;
    }
    canvas.classList.toggle('onhandle', Boolean(handleAt(x, y)));
  });
  const releaseDrag = e => {
    if (dragging) {
      dragging = null;
      refreshCommitState();
      playFrom(0); // preview from the new start, matching what "release" means in SPEC021 §4
      return;
    }
    if (panStart) {
      if (!didPan && e) {
        const rect = canvas.getBoundingClientRect();
        const x = clamp(e.clientX - rect.left, 0, rect.width);
        playFrom(clamp(msOf(x) - draft.start_ms, 0, draft.end_ms - draft.start_ms));
      }
      panStart = null;
      didPan = false;
      canvas.classList.remove('panning');
    }
  };
  canvas.addEventListener('pointerup', releaseDrag);
  canvas.addEventListener('pointercancel', () => {
    dragging = null;
    panStart = null;
    didPan = false;
    canvas.classList.remove('panning');
  });

  // Wheel-to-zoom, centred on the cursor rather than the view's own middle --
  // the point someone is pointing at is the point that should stay put
  // `[SPEC-SUI-218]`.
  canvas.addEventListener('wheel', e => {
    if (!buffer) return;
    e.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const x = clamp(e.clientX - rect.left, 0, rect.width);
    const anchorMs = msOf(x);
    const factor = e.deltaY > 0 ? 1.25 : 0.8;
    const span = (view.toMs - view.fromMs) * factor;
    const usable = Math.max(1, (rect.width || canvas.width) - 2 * EDGE_PAD_CSS);
    const ratio = clamp((x - EDGE_PAD_CSS) / usable, 0, 1);
    setView(anchorMs - span * ratio, anchorMs + span * (1 - ratio));
  }, { passive: false });

  $('zoomin').addEventListener('click', () => zoomStep(0.5));
  $('zoomout').addEventListener('click', () => zoomStep(2));
  $('zoompassage').addEventListener('click', () => setView(draft.start_ms, draft.end_ms));
  // Labelled "Zoom out" in the page, not "Whole file" -- it reaches the
  // edge of what was actually decoded, which is the whole file for the
  // ordinary single-track case and a bounded window around the passage for
  // a large DAO capture `[SPEC-SUI-224]`; the id stays `zoomfile` since
  // nothing else needs to change for that. See the partial-window note
  // `updateFacts` adds when the two differ.
  $('zoomfile').addEventListener('click', () => setView(windowFromMs, windowFromMs + totalMs()));

  // The preview player: a fresh `AudioBuffer` sliced at the draft's
  // boundaries with the draft's fades and gain baked in sample-by-sample,
  // using the exact same formula `fade.rs` applies to real playback
  // `[SPEC021 §4]` -- so what changes on a drag is also what is heard.
  //
  // Applies `fade_in_ms`/`fade_out_ms`, not `lead_in_ms`/`lead_out_ms`
  // `[SPEC-SUI-226]`: lead only times when a crossfade with a neighbour is
  // *permitted*, and was never itself a gain ramp during ordinary playback
  // -- fade is the envelope real playback actually applies, so previewing
  // against lead was quietly claiming a fade-out real playback never
  // produced. Multiplied, not chosen, where the two overlap on a very short
  // passage, matching `Envelope::gain_at` on the Rust side.
  function renderPreview() {
    const sr = buffer.sampleRate;
    const startSample = Math.max(0, Math.floor(((draft.start_ms - windowFromMs) / 1000) * sr));
    const endSample = Math.min(buffer.length, Math.ceil(((draft.end_ms - windowFromMs) / 1000) * sr));
    const n = Math.max(1, endSample - startSample);
    const fadeIn = Math.round((draft.fade_in_ms / 1000) * sr);
    const fadeOut = Math.round((draft.fade_out_ms / 1000) * sr);
    const foStart = n - fadeOut;
    const linGain = Math.pow(10, draft.gain_db / 20);
    const out = audioCtx.createBuffer(buffer.numberOfChannels, n, sr);
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      const src = buffer.getChannelData(c);
      const dst = out.getChannelData(c);
      for (let i = 0; i < n; i++) {
        let g = linGain;
        if (fadeIn > 0 && i < fadeIn) g *= LempiFade.gainIn(draft.fade_in_curve, i / fadeIn);
        if (fadeOut > 0 && i >= foStart) g *= LempiFade.gainOut(draft.fade_out_curve, (i - foStart) / fadeOut);
        dst[i] = (src[startSample + i] || 0) * g;
      }
    }
    return out;
  }

  function stopPreview() {
    if (source) {
      // Detach `onended` before stopping it: the event still fires
      // asynchronously after `.stop()`, and by the time it does, `source`
      // may already have moved on to a newer node started in the meantime.
      // A callback still watching the old node would then see `source ===`
      // that stale node and wrongly null out the *current* one -- this is
      // the actual cause a click could seem to "start another stream" that
      // never got silenced: the reference to it was lost, so a later
      // `stopPreview()` had nothing to call `.stop()` on, even though the
      // node itself was still actually playing.
      source.onended = null;
      try { source.stop(); } catch (e) { /* already stopped */ }
      source.disconnect();
      source = null;
    }
    $('play').textContent = 'Play';
  }

  function playFrom(offsetMs) {
    stopPreview();
    const preview = renderPreview();
    const node = audioCtx.createBufferSource();
    node.buffer = preview;
    node.connect(audioCtx.destination);
    const offSec = clamp(offsetMs, 0, preview.duration * 1000) / 1000;
    playedAt = audioCtx.currentTime;
    playedFromMs = offSec * 1000;
    // Guarded by identity, not just detached above: a node that runs to its
    // own natural end (never stopped by hand) must still clear `source`, but
    // only if nothing newer has already taken its place.
    node.onended = () => {
      if (source === node) { source = null; $('play').textContent = 'Play'; }
    };
    node.start(0, offSec);
    source = node;
    $('play').textContent = 'Pause';
    requestAnimationFrame(tick);
  }

  function tick() {
    draw();
    if (source) requestAnimationFrame(tick);
  }

  $('play').addEventListener('click', () => {
    if (source) stopPreview();
    else playFrom(0);
  });

  $('gain').addEventListener('change', () => {
    const v = Number($('gain').value);
    if (Number.isFinite(v)) {
      pushUndo();
      draft.gain_db = v;
      updateFacts();
      refreshCommitState();
    }
  });

  // The precise fields: the same five values dragging a knob edits, typed
  // instead of dragged -- most of what "how do I place these" was really
  // asking for `[SPEC-SUI-219]`. Same clamps `applyDrag` already enforces,
  // so a typed value cannot reach a state a drag could not also reach.
  //
  // Each field also reveals its own marker `[SPEC-SUI-221]` -- on focus, so
  // simply clicking into "lead-out" answers "where is that" before a single
  // character is typed, and again after a change, in case the new value
  // moved somewhere the old view no longer reaches. A start near 0 and an
  // end four minutes later are rarely both worth looking at closely at the
  // same zoom, and nothing on screen tells a person that a marker off the
  // current edge is merely off-screen rather than absent.
  $('startms').addEventListener('focus', () => ensureVisible(draft.start_ms));
  $('startms').addEventListener('change', () => {
    const v = Number($('startms').value);
    if (!Number.isFinite(v)) return updatePreciseFields();
    pushUndo();
    const before = draft.start_ms;
    draft.start_ms = clamp(Math.round(v), windowFromMs, draft.end_ms - 1);
    updateFacts(); updatePreciseFields();
    // Reveal scaled to how far this edit actually moved it, not just
    // whether the new position is technically in view -- a small nudge on
    // a wide zoom is otherwise on screen but sub-pixel, indistinguishable
    // from nothing having happened.
    ensureVisible(draft.start_ms, revealSpan(Math.abs(draft.start_ms - before)));
    draw(); refreshCommitState();
  });
  $('endms').addEventListener('focus', () => ensureVisible(draft.end_ms));
  $('endms').addEventListener('change', () => {
    const v = Number($('endms').value);
    if (!Number.isFinite(v)) return updatePreciseFields();
    pushUndo();
    const before = draft.end_ms;
    draft.end_ms = clamp(Math.round(v), draft.start_ms + 1, windowEndMs());
    updateFacts(); updatePreciseFields();
    ensureVisible(draft.end_ms, revealSpan(Math.abs(draft.end_ms - before)));
    draw(); refreshCommitState();
  });
  $('leadinms').addEventListener('focus', () =>
    ensureVisible(draft.start_ms + draft.lead_in_ms, revealSpan(draft.lead_in_ms)));
  $('leadinms').addEventListener('change', () => {
    const v = Number($('leadinms').value);
    if (!Number.isFinite(v)) return updatePreciseFields();
    pushUndo();
    draft.lead_in_ms = Math.round(clamp(v, 0, draft.end_ms - draft.start_ms));
    updateFacts(); updatePreciseFields();
    ensureVisible(draft.start_ms + draft.lead_in_ms, revealSpan(draft.lead_in_ms));
    draw(); refreshCommitState();
  });
  $('leadoutms').addEventListener('focus', () =>
    ensureVisible(draft.end_ms - draft.lead_out_ms, revealSpan(draft.lead_out_ms)));
  $('leadoutms').addEventListener('change', () => {
    const v = Number($('leadoutms').value);
    if (!Number.isFinite(v)) return updatePreciseFields();
    pushUndo();
    draft.lead_out_ms = Math.round(clamp(v, 0, draft.end_ms - draft.start_ms));
    updateFacts(); updatePreciseFields();
    ensureVisible(draft.end_ms - draft.lead_out_ms, revealSpan(draft.lead_out_ms));
    draw(); refreshCommitState();
  });

  // Fade-in/fade-out: the same reveal-on-focus, clamp-on-change shape as
  // lead above, just against `fade_in_ms`/`fade_out_ms` `[SPEC-SUI-226]`.
  $('fadeinms').addEventListener('focus', () =>
    ensureVisible(draft.start_ms + draft.fade_in_ms, revealSpan(draft.fade_in_ms)));
  $('fadeinms').addEventListener('change', () => {
    const v = Number($('fadeinms').value);
    if (!Number.isFinite(v)) return updatePreciseFields();
    pushUndo();
    draft.fade_in_ms = Math.round(clamp(v, 0, draft.end_ms - draft.start_ms));
    updateFacts(); updatePreciseFields();
    ensureVisible(draft.start_ms + draft.fade_in_ms, revealSpan(draft.fade_in_ms));
    draw(); refreshCommitState();
  });
  $('fadeoutms').addEventListener('focus', () =>
    ensureVisible(draft.end_ms - draft.fade_out_ms, revealSpan(draft.fade_out_ms)));
  $('fadeoutms').addEventListener('change', () => {
    const v = Number($('fadeoutms').value);
    if (!Number.isFinite(v)) return updatePreciseFields();
    pushUndo();
    draft.fade_out_ms = Math.round(clamp(v, 0, draft.end_ms - draft.start_ms));
    updateFacts(); updatePreciseFields();
    ensureVisible(draft.end_ms - draft.fade_out_ms, revealSpan(draft.fade_out_ms));
    draw(); refreshCommitState();
  });
  $('fadeincurve').addEventListener('change', () => {
    pushUndo();
    draft.fade_in_curve = $('fadeincurve').value;
    updateFacts();
    refreshCommitState();
  });
  $('fadeoutcurve').addEventListener('change', () => {
    pushUndo();
    draft.fade_out_curve = $('fadeoutcurve').value;
    updateFacts();
    refreshCommitState();
  });

  $('commit').addEventListener('click', async () => {
    $('commit').disabled = true;
    // Belt-and-braces `[SPEC-SUI-228]`: whatever produced `draft`, its six
    // duration fields are rounded again here, at the wire -- the one place a
    // fractional value is *certain* to be caught, since the server's `u64`
    // columns reject one outright rather than truncating it. Mutates `draft`
    // itself, not just the outgoing body, so the precise fields and the
    // dirty check agree with what was actually saved rather than silently
    // disagreeing with it by a fraction of a millisecond.
    for (const f of ['start_ms', 'end_ms', 'lead_in_ms', 'lead_out_ms', 'fade_in_ms', 'fade_out_ms']) {
      draft[f] = Math.round(draft[f]);
    }
    updatePreciseFields();
    const resp = await fetch(`/edit/${passageId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(draft),
    }).catch(() => null);
    if (!resp || !resp.ok) {
      const text = resp ? await resp.text().catch(() => '') : '';
      note(`Could not save the edit${text ? `: ${text}` : ''}.`, 'bad');
      refreshCommitState();
      return;
    }
    base = { ...draft };
    // What has and has not happened: this writes `boundary_reviews` on THIS
    // machine's own database only `[SPEC021 §2]`. It is not yet folded into
    // `passages` here (that is `apply_boundary_reviews.py`, a deliberate
    // separate step), and it has not gone anywhere else at all -- carrying it
    // to another library is Vipunen's own sync `[SPEC-DF-107..118]`, not
    // something this page can reach or should try to trigger.
    note('Saved to this machine’s own database. Not yet applied to the ' +
         'library here, and not yet sent to any other library -- both are ' +
         'separate, deliberate steps.', 'good');
  });

  // ---------------------------------------------------------------------
  // Automatic detection `[SPEC-SUI-230]`. The algorithm is `boundary.js`
  // (`[SPEC-SUI-229]`); this is the panel around it: the parameters for both
  // kinds, editable, run against the decoded window on every change, and the
  // result offered -- never applied without asking -- as a suggestion.
  //
  // The edited parameters are kept in this browser only, so tuning carries
  // from one passage to the next. Making them the defaults everywhere is a
  // deliberate step: copy the JSON into `boundary-params.json`, which the
  // batch tool reads too.
  let defaults = null;     // boundary-params.json as shipped
  let params = null;       // { radio, album } as edited here
  let detKind = 'radio';
  let suggestion = null;   // the last detect() result, or null
  let detSteps = [];
  let detTimer = null;
  let traceCache = null;   // { windowMs, env } over the whole decoded window
  const KINDS = ['radio', 'album'];
  const PARAM_STORE = 'lempi.edit.detectParams.v1';
  // Column heads for the two-sided groups; a group not named here has one.
  const SIDES = { 'Start / end': ['start', 'end'],
                  'Lead-in / lead-out': ['in', 'out'],
                  'Fade-in / fade-out': ['in', 'out'] };

  // Only keys the shipped defaults know, and only of the same type -- a value
  // saved by an older or newer page cannot smuggle in a parameter the
  // algorithm does not read, or a string where a number belongs.
  function mergeParams(base, saved) {
    const out = { ...base };
    if (saved && typeof saved === 'object') {
      for (const k of Object.keys(base)) {
        if (k in saved && typeof saved[k] === typeof base[k]) out[k] = saved[k];
      }
    }
    return out;
  }
  function saveParams() {
    try { localStorage.setItem(PARAM_STORE, JSON.stringify(params)); } catch (e) { /* a convenience only */ }
  }

  function setUpDetection(shipped) {
    if (!window.LempiBoundary || !shipped || !shipped.radio || !shipped.album) {
      // Said, not hidden: a panel that silently is not there reads as a
      // page that never had one `[GDE-DEP-060]`.
      $('detect').hidden = false;
      $('detsteps').innerHTML =
        '<li>Detection is unavailable: its default parameters could not be read from this player.</li>';
      for (const id of ['detkind', 'detspan', 'dettrace', 'detreset', 'detjsonapply']) $(id).disabled = true;
      return;
    }
    defaults = shipped;
    let saved = null;
    try { saved = JSON.parse(localStorage.getItem(PARAM_STORE) || 'null'); } catch (e) { saved = null; }
    params = {};
    for (const k of KINDS) params[k] = mergeParams(defaults[k], saved && saved[k]);
    detKind = KINDS.includes(info.kind) ? info.kind : 'radio';
    $('detkind').value = detKind;
    $('detect').hidden = false;
    renderKindNote();
    renderParams();
    renderDetectTable();
    renderSteps(['Waiting for the audio to decode.']);
  }

  function renderKindNote() {
    const own = KINDS.includes(info.kind) ? info.kind : null;
    $('detkindnote').textContent = !own
      ? 'this passage\'s kind could not be read'
      : own === detKind ? `this is a ${own} cut`
        : `this is a ${own} cut -- showing what the ${detKind} parameters would do to it`;
  }

  // Where a start may be searched for and an end searched back from: the
  // file's own edges, a neighbouring passage of the same kind, or the edge
  // of what was decoded -- whichever is nearest, and said which.
  function searchContext() {
    if ($('detspan').checked) {
      return {
        ctx: { loMs: draft.start_ms, hiMs: draft.end_ms, loIsFileEdge: true, hiIsFileEdge: true },
        keep: true,
        why: `start and end kept where they are now (${fmt(draft.start_ms)} – ${fmt(draft.end_ms)}); ` +
             'lead and fade measured on that span',
      };
    }
    const winEnd = windowEndMs();
    const prev = info.prev_end_ms, next = info.next_start_ms;
    const loMs = Math.max(windowFromMs, prev == null ? 0 : prev);
    const hiMs = Math.min(winEnd, next == null ? Infinity : next);
    const loIsFileEdge = prev == null && windowFromMs === 0;
    // The decoded length and the stored file length differ by a sample's
    // rounding even when the whole file was decoded; a window that stopped
    // short of the file by design is short by far more than this.
    const hiIsFileEdge = next == null && winEnd >= (fileMs || winEnd) - 1000;
    const loWhy = loIsFileEdge ? 'the file\'s start'
      : prev != null && loMs === prev ? 'where the previous passage ends' : 'the edge of the decoded window';
    const hiWhy = hiIsFileEdge ? 'the file\'s end'
      : next != null && hiMs === next ? 'where the next passage starts' : 'the edge of the decoded window';
    return {
      ctx: { loMs, hiMs, loIsFileEdge, hiIsFileEdge },
      keep: false,
      why: `searched ${fmt(loMs)} – ${fmt(hiMs)}: from ${loWhy} to ${hiWhy}`,
    };
  }

  function scheduleDetect() {
    if (detTimer) clearTimeout(detTimer);
    detTimer = setTimeout(runDetect, 120);
  }

  function runDetect() {
    detTimer = null;
    if (!mono || !params || !draft) return;
    const { ctx, keep, why } = searchContext();
    if (!(ctx.hiMs > ctx.loMs)) {
      suggestion = null;
      renderSteps([`Nothing to search -- ${why}.`]);
    } else {
      const p = keep ? { ...params[detKind], trim_start: false, trim_end: false } : params[detKind];
      const t0 = performance.now();
      suggestion = LempiBoundary.detect(mono, buffer.sampleRate, windowFromMs, ctx, p);
      const took = Math.round(performance.now() - t0);
      renderSteps([why, ...suggestion.steps, `(computed in ${took} ms on ${fmt(totalMs())} of decoded audio)`]);
    }
    $('legendsuggest').hidden = !suggestion;
    renderDetectTable();
    draw();
  }

  // Suggested beside now, one row per point. A fade row carries its curve,
  // and "use" on it takes the curve too.
  const POINTS = [
    { label: 'start', field: 'start_ms', color: '#6cf', pos: true },
    { label: 'end', field: 'end_ms', color: '#6cf', pos: true },
    { label: 'lead-in', field: 'lead_in_ms', color: '#fb6' },
    { label: 'lead-out', field: 'lead_out_ms', color: '#fb6' },
    { label: 'fade-in', field: 'fade_in_ms', color: '#a78bfa', curve: 'fade_in_curve' },
    { label: 'fade-out', field: 'fade_out_ms', color: '#a78bfa', curve: 'fade_out_curve' },
  ];
  function renderDetectTable() {
    const t = $('detresult');
    if (!t || !params || !draft) return;
    t.textContent = '';
    const head = t.insertRow();
    for (const h of ['', 'suggested', 'now', 'difference', '']) {
      const th = document.createElement('th');
      th.textContent = h;
      head.append(th);
    }
    let differs = false;
    for (const pt of POINTS) {
      const r = t.insertRow();
      r.className = 'point';
      const c0 = r.insertCell();
      const sw = document.createElement('span');
      sw.className = 'sw';
      sw.style.background = pt.color;
      c0.append(sw, pt.label);
      const show = (v, curve) => (v == null ? '—' : pt.pos ? fmt(v) : `${v} ms` + (curve ? ` ${curve}` : ''));
      const sug = suggestion ? suggestion[pt.field] : null;
      const now = draft[pt.field];
      const sugCurve = suggestion && pt.curve ? suggestion[pt.curve] : null;
      const nowCurve = pt.curve ? draft[pt.curve] : null;
      const cell = (text, cls) => { const c = r.insertCell(); c.textContent = text; c.className = cls; };
      cell(show(sug, sugCurve), 'num');
      cell(show(now, nowCurve), 'num');
      const d = sug == null ? null : sug - now;
      const curveDiffers = Boolean(pt.curve && suggestion && sugCurve !== nowCurve);
      const text = d == null ? '—'
        : d === 0 ? (curveDiffers ? 'curve' : 'same')
          : `${d > 0 ? '+' : '−'}${Math.abs(d).toLocaleString()} ms`;
      cell(text, 'num' + (d || curveDiffers ? ' differs' : ''));
      const use = document.createElement('button');
      use.type = 'button';
      use.textContent = 'use';
      use.disabled = !suggestion || (d === 0 && !curveDiffers);
      use.title = `Put the suggested ${pt.label} into the draft`;
      use.addEventListener('click', () => applySuggestion([pt.field]));
      r.insertCell().append(use);
      if (d || curveDiffers) differs = true;
    }
    $('detuse').disabled = !suggestion || !differs;
  }

  function renderSteps(lines) {
    detSteps = lines;
    const ol = $('detsteps');
    ol.textContent = '';
    for (const s of lines) {
      const li = document.createElement('li');
      li.textContent = s;
      ol.append(li);
    }
  }

  // Into the draft, exactly as a drag would put it there -- one undo step,
  // the same clamps, and nothing saved until Save edit.
  function applySuggestion(fields) {
    if (!suggestion) return;
    pushUndo();
    stopPreview();
    const s = suggestion;
    if (fields.includes('start_ms') && fields.includes('end_ms')) {
      // Both at once: the pair is valid together even where either alone
      // would cross the other's current value.
      draft.start_ms = clamp(s.start_ms, windowFromMs, windowEndMs() - 1);
      draft.end_ms = clamp(s.end_ms, draft.start_ms + 1, windowEndMs());
    } else if (fields.includes('start_ms')) {
      draft.start_ms = clamp(s.start_ms, windowFromMs, draft.end_ms - 1);
    } else if (fields.includes('end_ms')) {
      draft.end_ms = clamp(s.end_ms, draft.start_ms + 1, windowEndMs());
    }
    const len = draft.end_ms - draft.start_ms;
    for (const f of ['lead_in_ms', 'lead_out_ms', 'fade_in_ms', 'fade_out_ms']) {
      if (fields.includes(f)) draft[f] = clamp(s[f], 0, len);
    }
    if (fields.includes('fade_in_ms')) draft.fade_in_curve = s.fade_in_curve;
    if (fields.includes('fade_out_ms')) draft.fade_out_curve = s.fade_out_curve;
    updateFacts();
    updatePreciseFields();
    refreshCommitState();
    draw();
  }
  $('detuse').addEventListener('click', () => applySuggestion(POINTS.map(p => p.field)));

  function renderParams() {
    const box = $('detparams');
    box.textContent = '';
    const rows = LempiBoundary.ROWS;
    for (const group of [...new Set(rows.map(r => r.group))]) {
      const mine = rows.filter(r => r.group === group);
      const sides = SIDES[group];
      const t = document.createElement('table');
      const head = t.insertRow();
      for (const h of [group, ...(sides || [])]) {
        const th = document.createElement('th');
        th.textContent = h;
        head.append(th);
      }
      for (const row of mine) {
        const tr = t.insertRow();
        const lab = tr.insertCell();
        lab.className = 'rowlabel';
        lab.textContent = row.label + (row.unit ? ` (${row.unit})` : '');
        lab.title = row.hint;
        for (const key of row.keys) {
          const td = tr.insertCell();
          if (sides && row.keys.length === 1) td.colSpan = 2;
          td.append(paramInput(row, key));
        }
      }
      box.append(t);
    }
    refreshJson();
  }

  function paramInput(row, key) {
    const p = params[detKind];
    let el;
    if (row.type === 'bool') {
      el = document.createElement('input');
      el.type = 'checkbox';
      el.checked = Boolean(p[key]);
    } else if (row.type === 'select') {
      el = document.createElement('select');
      for (const o of row.options) el.append(new Option(o, o));
      el.value = p[key];
    } else {
      el = document.createElement('input');
      el.type = 'number';
      el.min = key === 'window_ms' ? '1' : '0';
      el.step = row.unit === 'ms' ? '10' : '1';
      el.value = p[key];
    }
    el.dataset.key = key;
    el.title = row.hint;
    const mark = () => el.classList.toggle('changed', params[detKind][key] !== defaults[detKind][key]);
    mark();
    el.addEventListener(row.type === 'number' ? 'input' : 'change', () => {
      let v;
      if (row.type === 'bool') v = el.checked;
      else if (row.type === 'select') v = el.value;
      else {
        v = Number(el.value);
        // Half-typed is not a value: keep the last good one until it is.
        if (el.value === '' || !Number.isFinite(v) || v < Number(el.min)) return;
      }
      params[detKind][key] = v;
      mark();
      saveParams();
      refreshJson();
      scheduleDetect();
    });
    return el;
  }

  $('detkind').addEventListener('change', () => {
    detKind = $('detkind').value;
    renderKindNote();
    renderParams();
    runDetect();
  });
  $('detspan').addEventListener('change', runDetect);
  $('dettrace').addEventListener('change', () => {
    $('legendtrace').hidden = !$('dettrace').checked;
    draw();
  });
  $('detreset').addEventListener('click', () => {
    params[detKind] = { ...defaults[detKind] };
    saveParams();
    renderParams();
    runDetect();
  });

  function refreshJson() {
    if (!params || !$('detjsonbox').open) return;
    $('detjson').value = JSON.stringify({ version: defaults.version, ...params }, null, 2);
  }
  $('detjsonbox').addEventListener('toggle', refreshJson);
  $('detjsonapply').addEventListener('click', () => {
    let parsed;
    try { parsed = JSON.parse($('detjson').value); } catch (e) {
      $('detjsonnote').textContent = `Not JSON: ${e.message}`;
      return;
    }
    for (const k of KINDS) params[k] = mergeParams(defaults[k], parsed && parsed[k]);
    saveParams();
    renderParams();
    runDetect();
    $('detjsonnote').textContent = 'Applied. A key it did not name went back to its default.';
  });

  // Each window's level over the waveform, 0 dBFS at the top and -90 at the
  // bottom, with every threshold the current parameters use drawn across it
  // -- so "why did it put the end there" is answered by where the curve
  // crosses a line, not by reading numbers.
  const DB_FLOOR = -90;
  function drawTrace(g, w, h, dpr, padPx, usable, span, px) {
    const wms = params[detKind].window_ms;
    if (!traceCache || traceCache.windowMs !== wms) {
      traceCache = { windowMs: wms, env: LempiBoundary.envelope(mono, buffer.sampleRate, 0, mono.length, wms) };
    }
    const env = traceCache.env;
    const yOf = db => (Math.max(DB_FLOOR, Math.min(0, db)) / DB_FLOOR) * h;
    g.strokeStyle = '#5d8';
    g.lineWidth = 1.5 * dpr;
    g.beginPath();
    let started = false;
    for (let x = Math.floor(padPx); x < w - padPx; x++) {
      const msLo = view.fromMs + ((x - padPx) / usable) * span - windowFromMs;
      const msHi = view.fromMs + ((x + 1 - padPx) / usable) * span - windowFromMs;
      const i0 = Math.floor(msLo / wms), i1 = Math.max(i0, Math.floor(msHi / wms));
      let peak = -1;
      for (let i = Math.max(0, i0); i <= Math.min(env.length - 1, i1); i++) if (env[i] > peak) peak = env[i];
      if (peak < 0) continue;
      const y = yOf(LempiBoundary.dbOf(peak));
      if (started) g.lineTo(x, y); else { g.moveTo(x, y); started = true; }
    }
    g.stroke();
    if (!suggestion) return;
    // One line per distinct level, named for everything that uses it.
    const byLevel = new Map();
    for (const lv of suggestion.levels) {
      if (!lv.on) continue;
      const k = lv.dbfs.toFixed(1);
      if (!byLevel.has(k)) byLevel.set(k, { dbfs: lv.dbfs, names: [], color: /silence/.test(lv.name) ? '#6cf' : '#fb6' });
      byLevel.get(k).names.push(lv.name);
    }
    g.font = `${11 * dpr}px system-ui, sans-serif`;
    g.textAlign = 'right';
    for (const { dbfs, names, color } of byLevel.values()) {
      const y = yOf(dbfs);
      g.strokeStyle = color;
      g.lineWidth = 1 * dpr;
      g.setLineDash([2 * dpr, 3 * dpr]);
      g.beginPath();
      g.moveTo(padPx, y);
      g.lineTo(w - padPx, y);
      g.stroke();
      g.setLineDash([]);
      g.fillStyle = color;
      g.fillText(`${dbfs.toFixed(0)} dBFS  ${names.join(', ')}`, w - padPx - 4 * dpr, y - 3 * dpr);
    }
    g.textAlign = 'start';
  }

  load().catch(() => note('Something went wrong loading this passage.', 'bad'));
})();
