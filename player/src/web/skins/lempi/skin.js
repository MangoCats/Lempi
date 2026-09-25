// The reference skin. Whatever a new skin needs from `core.js`, this one uses
// first -- so if something is missing from the contract, it shows up here.
(() => {
  const $ = id => document.getElementById(id);
  const { clock, overlap } = Lempi.fmt;

  // Volume, programme and the queue are the same behaviour in every skin, so
  // they come from core; this skin says only where they live and how a row
  // should read `[REQ-VIS-160]`.
  const showVolume = Lempi.bindVolume($('volume'), $('volnum'));
  const showProgram = Lempi.bindProgram($('prog'));
  // Which track the explanation panel is describing `[REQ-VIS-100]`.
  //
  // `null` means "whatever is playing", and it follows the track changes --
  // which is the behaviour this skin had before any of this, so doing nothing
  // still works the way it used to. Picking a queued row pins the panel to
  // that passage until something else is picked or it starts playing.
  let picked = null;
  let pickedWhy = null;          // fetched once; an explanation never changes

  const showQueue = Lempi.bindQueue(
    $('queue'), q => (q.artist ? `${q.title} — ${q.artist}` : q.title), 'li',
    'nothing queued',
    {
      // One control set beside the picked row instead of a set on every row.
      controls: false,
      selected: () => picked,
      onPick: (qid, item) => { pick(qid, item.passage_id); },
      // Title/artist become their own clickable spans, reaching the
      // preference panel `[REQ-VIS-285]` -- they stop the click from also
      // firing `onPick` above, so the two features never compete.
      linkable: true,
    });

  // `qid` identifies the QUEUE ENTRY; `passageId` the recording it plays. The
  // selection follows the entry -- two copies of one passage are separately
  // pickable -- while the explanation is fetched for the passage, because an
  // explanation is about the recording and is the same for both copies.
  function pick(qid, passageId) {
    picked = qid;
    pickedWhy = null;
    if (qid != null && passageId != null) {
      // 404 is a real answer -- a resumed passage, or one queued before the
      // log began -- and renderWhy already says so for null.
      fetch(`/why/${passageId}`)
        .then(r => (r.ok ? r.json() : null))
        .then(w => { if (picked === qid) { pickedWhy = w; draw(); } })
        .catch(() => { if (picked === qid) draw(); });
    }
    draw();
  }
  $('nowrow').onclick = () => pick(null, null);

  // The settings screen `[REQ-VIS-160]`. The skip shape and the programme are
  // set once and then left alone, so they no longer sit in the reading path
  // between the transport and the queue.
  //
  // A panel swap rather than a second page: the settings are driven by the
  // same snapshot as everything else, and a separate page would need its own
  // socket to show the programme currently in force. Both panels stay in the
  // DOM, so the bindings below attach once and keep working either way.
  //
  // History joins them as a third panel `[REQ-VIS-250]` rather than a fourth
  // shape: exactly one of the three is ever open, so opening one closes
  // whichever of the other two was showing instead of layering a screen on
  // top of it.
  const gear = $('gear'), histBtn = $('histbtn');
  function showPanel(which) {
    const settings = which === 'settings', history = which === 'history';
    $('panel-main').hidden = settings || history;
    $('panel-settings').hidden = !settings;
    $('panel-history').hidden = !history;
    gear.setAttribute('aria-expanded', String(settings));
    histBtn.setAttribute('aria-expanded', String(history));
  }
  gear.onclick = () => showPanel(gear.getAttribute('aria-expanded') === 'true' ? 'main' : 'settings');
  histBtn.onclick = () => {
    const opening = histBtn.getAttribute('aria-expanded') !== 'true';
    showPanel(opening ? 'history' : 'main');
    // Freshest first every time it is opened: "history" that opened on
    // whatever page you last left is not what "recently played" promises.
    if (opening) { histPage = 1; loadHistory(); }
  };

  // Seconds here, milliseconds on the wire: seconds are what the listener is
  // choosing, milliseconds are what the mixer counts in.
  const skipFade = $('skipfade'), skipLead = $('skiplead');
  skipFade.onchange = () => Lempi.skipFade(skipFade.value * 1000);
  skipLead.onchange = () => Lempi.skipLead(skipLead.value * 1000);
  // This node's place in a fleet [SPEC-DLY-120], [SPEC-ECHO-010].
  const echoTrim = $('echotrim'), echoFollow = $('echofollow');
  echoTrim.onchange = () => Lempi.echoTrim(echoTrim.value || 0);
  $('echotrimreset').onclick = () => Lempi.echoTrimReset();
  // On change, not on every keystroke: a half-typed hostname is not a node
  // anyone meant to follow, and sending one would make the panel flicker
  // through states nobody chose.
  echoFollow.onchange = () => Lempi.echoFollow(echoFollow.value);
  const echoJoin = $('echojoin');
  echoJoin.onchange = () => Lempi.echoJoinNow(echoJoin.value === '1');

  const resumeSave = $('resumesave');
  resumeSave.onchange = () => Lempi.resumeSave(resumeSave.value * 1000);
  // A skip suppresses; it does not count as played `[SPEC-PLAY-050]`.
  const skipSuppress = $('skipsuppress');
  skipSuppress.onchange = () => Lempi.skipSuppress(skipSuppress.value);
  const dequeueSuppress = $('dequeuesuppress');
  dequeueSuppress.onchange = () => Lempi.dequeueSuppress(dequeueSuppress.value);
  const queueDepth = $('queuedepth');
  queueDepth.onchange = () => Lempi.queueDepth(queueDepth.value);
  const sampleInterval = $('sampleinterval');
  sampleInterval.onchange = () => Lempi.sampleInterval(sampleInterval.value * 1000);
  // Which backend is sounding `[SPEC-BK-030]`. Hidden entirely when no guest is
  // attached: a control that can only be refused is worse than no control.
  const cueSheets = $('cuesheets');
  cueSheets.onchange = () => Lempi.cueSheets(cueSheets.checked);

  const coversBox = $('covers');
  coversBox.onchange = () => Lempi.covers(coversBox.checked);

  const lyricsBox = $('lyricscache');
  lyricsBox.onchange = () => Lempi.lyricsCache(lyricsBox.checked);

  const sidecarBox = $('lyricssidecar');
  sidecarBox.onchange = () => Lempi.lyricsSidecar(sidecarBox.checked);

  $('underreset').onclick = () => Lempi.restartUnderruns();

  // Where the sound is coming from, in the file's own terms [REQ-VIS-235].
  // Hidden entirely when nothing is playing, rather than showing empty rows.
  function showSource(s) {
    const box = $('source');
    if (!s.file_path) { box.hidden = true; return; }
    box.hidden = false;
    $('srcpath').textContent = s.file_path;
    const t = ms => Lempi.fmt.clock(ms);
    // The file's length is unknown where it was never probed, which is not
    // the same as a file of no length.
    const whole = s.file_ms ? t(s.file_ms) : 'unknown length';
    $('srcspan').textContent =
      `${t(s.file_start_ms)} – ${t(s.file_end_ms)} of ${whole}`;
  }

  const backendSel = $('backend');
  backendSel.onchange = () => Lempi.switchBackend(backendSel.value);

  // Copy the build to the clipboard `[REQ-VIS-200]`. The point of showing it is
  // that someone can quote it somewhere else, and retyping a twelve-character
  // hash is how the wrong one gets quoted. Confirms in place, because a copy
  // that says nothing is indistinguishable from a button that does nothing.
  const buildCopy = $('buildcopy');
  buildCopy.onclick = async () => {
    const text = $('buildtext').textContent;
    const said = t => { buildCopy.textContent = t;
                        setTimeout(() => { buildCopy.textContent = '⧉'; }, 1200); };
    // The async Clipboard API is restricted to secure contexts (https, or
    // localhost) -- this page reached over plain http on the appliance's
    // own LAN or AP address is neither, so this throws *every* time there,
    // not just when a user denies it. Confirmed live: on Windows/Chrome
    // against a plain http:// address, this always falls through below.
    try {
      await navigator.clipboard.writeText(text);
      said('✓');
      return;
    } catch { /* fall through */ }
    // `execCommand('copy')` is not gated by secure-context the way the
    // async API is above -- confirmed live to actually place text on the
    // clipboard over plain http on Windows/Chrome, which is exactly the
    // case the try above always hits there. A detached, off-screen textarea
    // avoids disturbing anything the user already had selected on the page.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch { /* unsupported */ }
    document.body.removeChild(ta);
    if (copied) { said('✓'); return; }
    // Both copy paths failed outright -- select the visible text in place
    // so a manual copy is one keystroke (Ctrl/Cmd-C) rather than a careful
    // drag across a small monospace hash.
    const r = document.createRange();
    r.selectNodeContents($('buildtext'));
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(r);
    said('✗');
  };

  // Each term is shown separately, never just the product: a single number
  // cannot be argued with, and arguing with it is the point [SPEC-DIR-190].
  // Terms sitting at 1.0 did nothing, and are dimmed rather than hidden --
  // "this did not apply" is itself part of the answer.
  const TERMS = [
    ['Artist recovery',     w => w.artist_weight],
    ['Recording restraint', w => w.recording_restraint],
    ['Recording recovery',  w => w.recording_ramp],
    ['Related damping',     w => w.related_damping],
    ['Length bonus',        w => w.length_bonus],
    ['Occasion',            w => w.occasion],
  ];


  // The <option> list is rebuilt only when it actually changes; replacing it on
  // every push would close the dropdown in the user's hand.
  let progSig = '';

  // Limits come from the engine, which is what actually enforces them.
  // Fields being edited are left alone, as the volume slider is while held.
  // **Fed the whole snapshot, not `s.skip`.** These three live at the top
  // level while the skip shape is a nested object, and reading them off the
  // nested one silently yielded `undefined` -- which hid the control rather
  // than erroring, so the page looked finished and offered nothing.
  // What drew this page `[REQ-VIS-200]`. Shown always, guest or no guest: the
  // question "which build am I looking at" is asked most often when something
  // expected is missing, which is exactly when the answer is hardest to get by
  // any other means.
  // What this host offers beyond playing `[GDE-HST-360]`, measured by the
  // server at start. A control the host cannot honour is hidden rather than
  // drawn to fail -- bose and lp3-wifi have no lempi-btctl, and their Wi-Fi,
  // speaker, LED and radio rows had never worked. Hidden only on an explicit
  // `false`: a snapshot without the field says nothing, and nothing is not
  // "absent".
  let caps = {};
  const CAP_SECTIONS = {
    restart: 'restart', power_off: 'power', wifi: 'wifi-row',
    bluetooth: 'speakers', led: 'led-row', radios: 'radios',
  };
  function renderCapabilities(c) {
    caps = c || {};
    for (const [cap, id] of Object.entries(CAP_SECTIONS)) $(id).hidden = caps[cap] === false;
  }

  function renderBuild(s) {
    if (s.build) $('buildtext').textContent = s.build;
    if (s.branch) $('branchtext').textContent = s.branch;
    if (s.commit_date) {
      $('commitdatetext').textContent = s.commit_subject
        ? `${s.commit_date} — ${s.commit_subject}`
        : s.commit_date;
    }
    if (typeof s.dirty_files === 'number') {
      $('workingtreetext').textContent = s.dirty_files === 0
        ? 'clean'
        : `${s.dirty_files} file${s.dirty_files === 1 ? '' : 's'} uncommitted`;
    }
  }

  function renderCue(s) {
    if (document.activeElement !== cueSheets) cueSheets.checked = !!s.cue_sheets;
    $('cuestatus').textContent = s.cue_status || '';
    if (document.activeElement !== coversBox) coversBox.checked = !!s.covers;
    $('coversstatus').textContent = s.covers_status || '';
    if (document.activeElement !== lyricsBox) lyricsBox.checked = !!s.lyrics_cache;
    $('lyricsstatus').textContent = s.lyrics_status || '';
    if (document.activeElement !== sidecarBox) sidecarBox.checked = !!s.lyrics_sidecar;
    $('sidecarstatus').textContent = s.sidecar_status || '';
  }

  function renderBackend(s) {
    // The whole block, not the control alone: each setting now carries its
    // own label and explanation, and hiding only the `<span>` would leave a
    // heading and a paragraph describing a control that is not there.
    const block = backendSel.closest('.setting');
    if (block) block.hidden = !s.guest_available;
    // Name the guest rather than its category: "MPD at 127.0.0.1:6600" says
    // whether the thing behind the option is the one being looked at.
    const guestOpt = backendSel.querySelector('option[value=mpd]');
    if (guestOpt && s.guest_name) guestOpt.textContent = `${s.guest_name} (spans only)`;
    if (s.backend && document.activeElement !== backendSel) backendSel.value = s.backend;
    $('switchstatus').textContent = s.switch_status || '';
  }

  // This node's place in a fleet [SPEC-DLY-050], [SPEC-ECHO-020].
  //
  // The note under the delay box is the whole point of the control. Three
  // different zeroes mean three different things -- measured, restored, and
  // never configured -- and they are indistinguishable as numbers, so the
  // provenance is said in words beside the value [GOV-SRC-040].
  function renderEchoNode(n) {
    if (!n) return;
    // The engine's own limit, not a copy of it kept here [GDE-ARC-033].
    if (n.trim_limit_ms) {
      echoTrim.min = -n.trim_limit_ms;
      echoTrim.max = n.trim_limit_ms;
    }
    if (document.activeElement !== echoTrim) echoTrim.value = n.trim_ms;
    if (document.activeElement !== echoFollow) echoFollow.value = n.follow_host || '';
    if (document.activeElement !== echoJoin) echoJoin.value = n.join_now ? '1' : '0';
    // The node's own rate, not an assumed 44.1 kHz.
    const ms = f => (f * 1000 / (n.rate || 44100)).toFixed(1);
    let note;
    if (n.measured_frames == null) {
      // Not "0 ms": this device reports no usable delay at all, so the whole
      // offset is whatever a listener set by ear [GDE-ECHO-290].
      note = n.trim_ms
        ? `Nothing here measures this device's delay, so this ${n.trim_ms} ms is the whole of it — set by ear.`
        : "Nothing here measures this device's delay, and none has been set. This speaker is using no delay at all.";
    } else if (n.trim_ms === 0) {
      note = `Measured at ${ms(n.measured_frames)} ms, with nothing added.`;
    } else {
      note = `Measured at ${ms(n.measured_frames)} ms, ${n.trim_ms > 0 ? 'plus' : 'minus'} `
           + `${Math.abs(n.trim_ms)} ms set here — ${ms(n.offset_frames)} ms in all.`;
    }
    // A clamp is reported, never silently applied: a node cannot sound before
    // it submits [SPEC-DLY-030].
    if (n.clamped) note += ' That is further back than the speaker can go; using none.';
    $('echotrimnote').textContent = note;

    const st = $('echofollownote');
    if (!n.follow_host) {
      st.textContent = 'Playing this node’s own programme. '
        + 'Type the name of another player to follow it.';
    } else {
      // "Following" is a setting, not a state. Saying which of the two is
      // true is the difference between a working fleet and a silent failure.
      st.textContent = n.follow_status || `Set to follow ${n.follow_host}; not connected yet.`;
    }
  }

  function renderSkip(k) {
    if (!k) return;
    if (document.activeElement !== skipFade) {
      skipFade.min = 0;
      skipFade.max = k.fade_max_ms / 1000;
      skipFade.value = (k.fade_ms / 1000).toFixed(1);
    }
    if (document.activeElement !== skipLead) {
      skipLead.min = k.lead_min_ms / 1000;
      skipLead.max = k.lead_max_ms / 1000;
      skipLead.value = (k.lead_ms / 1000).toFixed(1);
    }
    $('skipoverlap').textContent = overlap(k);
    if (k.resume_save_ms != null && document.activeElement !== resumeSave) {
      resumeSave.min = k.resume_save_min_ms / 1000;
      resumeSave.max = k.resume_save_max_ms / 1000;
      resumeSave.value = (k.resume_save_ms / 1000).toFixed(1);
    }
    if (k.skip_suppress_h != null && document.activeElement !== skipSuppress) {
      skipSuppress.min = k.skip_suppress_min_h;
      skipSuppress.max = k.skip_suppress_max_h;
      skipSuppress.value = k.skip_suppress_h;
    }
    if (k.dequeue_suppress_h != null && document.activeElement !== dequeueSuppress) {
      dequeueSuppress.min = k.dequeue_suppress_min_h;
      dequeueSuppress.max = k.dequeue_suppress_max_h;
      dequeueSuppress.value = k.dequeue_suppress_h;
    }
    if (k.queue_depth != null && document.activeElement !== queueDepth) {
      queueDepth.min = k.queue_depth_min;
      queueDepth.max = k.queue_depth_max;
      queueDepth.value = k.queue_depth;
    }
    if (k.sample_interval_ms != null && document.activeElement !== sampleInterval) {
      sampleInterval.min = k.sample_interval_min_ms / 1000;
      sampleInterval.max = k.sample_interval_max_ms / 1000;
      sampleInterval.value = (k.sample_interval_ms / 1000).toFixed(1);
    }
  }

  function renderWhy(w) {
    const terms = $('terms'), losers = $('losers');
    if (!w) {
      $('why').textContent =
        'This passage was not chosen by the Program Director — it was resumed, ' +
        'or queued before the log began.';
      terms.hidden = losers.hidden = true;
      $('stages').textContent = '';
      return;
    }
    const prog = w.program ? `${w.program}: ` : '';
    $('why').textContent =
      `${prog}weight ${w.decayed_weight.toFixed(3)} of ${w.pool_weight.toFixed(1)} across ` +
      `${w.pool_size.toLocaleString()} candidates — a ` +
      `${w.share_pct.toFixed(2)}% chance of being picked.`;

    const body = terms.querySelector('tbody');
    body.textContent = '';
    for (const [label, get] of TERMS) {
      const v = get(w);
      const tr = body.insertRow();
      if (Math.abs(v - 1) < 1e-9) tr.className = 'inert';
      tr.insertCell().textContent = label;
      tr.insertCell().textContent = '×' + v.toFixed(4);
    }
    const tot = body.insertRow();
    tot.className = 'total';
    tot.insertCell().textContent = 'Frequency weight';
    tot.insertCell().textContent = w.weight.toFixed(4);

    // Stage B/C terms: how it FIT, kept visually apart from how OFTEN it may
    // play. The two stories stay separate all the way to the screen.
    const add = (label, text) => {
      const tr = body.insertRow();
      tr.insertCell().textContent = label;
      tr.insertCell().textContent = text;
    };
    if (w.shaping && !w.shaping.bypassed) {
      add('Pool shaping', `${w.shaping.eligible_in.toLocaleString()} eligible → ` +
                         `${w.shaping.gathered} gathered, ${w.shaping.seeds_used} seeds`);
      if (w.shaping.disliked_out) add('Disliked out', String(w.shaping.disliked_out));
    }
    if (w.seed_distances && w.seed_distances.length) {
      add('Distance to seeds', w.seed_distances.map(d => d.toFixed(2)).join(', '));
    }
    if (w.flow_distance != null) add('Follows previous by', w.flow_distance.toFixed(3));
    add('Flow rank', `#${w.rank + 1}`);
    const decayed = body.insertRow();
    decayed.className = 'total';
    decayed.insertCell().textContent = 'Roulette weight';
    decayed.insertCell().textContent = w.decayed_weight.toFixed(4);
    terms.hidden = false;

    const ol = losers.querySelector('ol');
    ol.textContent = '';
    for (const r of w.runners_up ?? []) {
      const li = document.createElement('li');
      // Title and artist, clickable through to the preference panel the
      // same way a queue row already is [REQ-VIS-285] -- one function,
      // not a second rendering of the same link.
      Lempi.linkableTrack(li, r);
      const span = document.createElement('span');
      span.className = 'w';
      span.textContent = `(${r.weight.toFixed(3)})`;
      li.appendChild(span);
      ol.appendChild(li);
    }
    losers.hidden = ol.children.length === 0;
    $('stages').textContent = w.stages ?? '';
  }

  // The byline is two independent claims that happen to share a line, so each
  // half carries its own provenance: a MusicBrainz artist beside an album that
  // is only a file tag is the common case here, and averaging the two into one
  // marker would describe neither.
  function showByline(s) {
    const el = $('byline');
    el.textContent = '';
    // `link` is only set for the artist -- an album has no mbid to open a
    // preference panel on `[REQ-VIS-285]`.
    const part = (text, source, what, link) => {
      if (!text) return;
      if (el.firstChild) el.appendChild(document.createTextNode(' — '));
      const span = document.createElement('span');
      Lempi.named(span, text, source, what);
      if (link) {
        span.classList.add('pref-link');
        span.onclick = e => {
          e.stopPropagation();
          Lempi.editPreference('artist', link, text);
        };
      }
      el.appendChild(span);
    };
    part(s.artist, s.artist_source, 'artist', s.artist_mbid);
    part(s.album, s.album_source, 'album', null);
  }

  // Each message is a complete snapshot, so rendering is a pure function of
  // the last one received. No accumulated client state means no drift.
  // The last snapshot, so picking a row can redraw immediately instead of
  // waiting up to half a second for the next push.
  let latest = null;
  const draw = () => { if (latest) render(latest); };

  // The bar reads the same snapshot the display does, so a click can only
  // ever act on the track actually shown [REQ-VIS-225].
  Lempi.seekable($('bar'), () => latest);

  Lempi.subscribe(s => { latest = s; render(s); });

  function render(s) {
    Lempi.named($('title'), s.title ?? '—', s.title_source, 'title');
    // The title reaches a recording preference panel when a recording is
    // actually behind it `[REQ-VIS-285]` -- `stopPropagation` so this does
    // not also fire `#nowrow`'s own click (the "explain this pick" panel,
    // below): the two features coexist rather than compete for one click.
    $('title').classList.toggle('pref-link', !!s.mbid);
    $('title').onclick = s.mbid
      ? e => { e.stopPropagation(); Lempi.editPreference('recording', s.mbid, s.title); }
      : null;
    showByline(s);
    $('plays').textContent = Lempi.fmt.plays(s.plays, s.last_played);
    Lempi.showArt($('art'), s.passage_id);
    $('time').textContent = `${clock(s.position_ms)} / ${clock(s.duration_ms)}`;
    $('fill').style.width =
      s.duration_ms ? `${(s.position_ms / s.duration_ms) * 100}%` : '0';
    // Offered only where the live backend can honour it [SPEC-BK-040].
    $('bar').classList.toggle('seekable', Boolean(s.can_seek && s.duration_ms));
    $('state').textContent = s.playing ? 'playing' : 'paused';
    // The ground shifts with it, so the state is legible without reading.
    $('devmode').hidden = !s.dev_mode;
    document.body.classList.toggle('dev', Boolean(s.dev_mode));
    $('under').textContent = s.underruns_since_reset ?? s.underrun_samples;
    const since = Lempi.since(s.underruns_since);
    $('undersince').textContent = since ? `since ${since}` : '';
    showSource(s);
    showQueue(s);
    showProgram(s);
    showVolume(s);
    $('progmode').textContent = s.program
      ? (s.program_manual ? `${s.program}, chosen` : `${s.program}, by the clock`)
      : '';
    renderSkip(s.skip);
    renderEchoNode(s.echo_node);
    renderBackend(s);
    renderCue(s);
    renderBuild(s);
    renderCapabilities(s.capabilities);
    renderPick(s);
    $('link').textContent =
      Lempi.status === 'connected' ? 'Connected' : 'Reconnecting…';
  }

  // The explanation panel, and the one control set that goes with it.
  //
  // `qpickSig` is the same guard `bindQueue` keeps, for the same reason: this
  // box was emptied and refilled on every snapshot, so its buttons were
  // replaced twice a second, and a press that spanned a replacement was never
  // delivered as a click at all.
  let qpickSig = null;
  function renderPick(s) {
    // A pinned passage that has since started playing, or left the queue
    // entirely, falls back to following the current passage rather than
    // explaining something no longer there.
    const queued = (s.queue || []).some(q => q.qid === picked);
    if (picked != null && !queued) picked = null;

    $('nowrow').classList.toggle('picked', picked == null);
    const on = picked != null ? (s.queue || []).find(q => q.qid === picked) : null;
    $('whotitle').textContent = on
      ? (on.artist ? `${on.title} — ${on.artist}` : on.title)
      : 'this passage';
    renderWhy(picked == null ? s.why : pickedWhy);

    // Controls belong to a queued row; the playing one cannot be moved or
    // dropped, which is why picking it hides them rather than disabling them.
    const box = $('qpick');
    box.hidden = !on;
    if (!on) {
      qpickSig = null;
      return;
    }
    // Which shift verbs can do anything from where this entry sits, so the
    // ones that cannot come up disabled rather than answering silently.
    const items = s.queue || [];
    const at = items.indexOf(on);
    const ends = { first: at === 0, last: at === items.length - 1 };
    const sig = JSON.stringify([on.qid, on.editable, ends]);
    if (sig === qpickSig) return;
    qpickSig = sig;
    box.textContent = '';
    box.appendChild(Lempi.queueControls(on.qid, on.editable, ends));
  }

  // ------------------------------------------------------------- speakers
  // Choosing a speaker `[PI3-UI-010]`. The states shown are the ones the
  // system reports, not the ones we hoped for: 'playing' and 'connected' are
  // different rows because a speaker can be linked while the audio goes
  // somewhere else entirely, and showing a tick for that is the lie that hid
  // this fault for two days `[PI3-WHY-010]`.
  const STATE_TEXT = {
    playing:   ['Playing here', null],
    connected: ['Connected, but silent', 'Use this one'],
    paired:    ['Known', 'Connect'],
    found:     ['In range', 'Pair'],
    stale:     ['Needs re-pairing', 'Repair'],
  };
  const VERB = { connected: 'use', paired: 'use', found: 'pair', stale: 'repair' };

  let previous = null;   // the speaker to fall back to, for confirm-or-revert
  let countdown = null;

  function speakerRow(d, output) {
    const li = document.createElement('li');
    // A device is only 'playing' if the audio is demonstrably reaching it.
    const playing = d.state === 'connected' && output &&
                    output.sink && output.sink === d.name;
    const state = playing ? 'playing' : d.state;
    const [label, action] = STATE_TEXT[state] || [state, null];
    li.className = 'bt ' + state;

    const name = document.createElement('span');
    name.className = 'btname';
    name.textContent = d.name || d.address;
    const said = document.createElement('span');
    said.className = 'btstate';
    said.textContent = label;
    li.append(name, said);

    if (action) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = action;
      b.onclick = () => choose(d, VERB[state] || 'use');
      li.appendChild(b);
    }
    if (state !== 'found') {
      const f = document.createElement('button');
      f.type = 'button';
      f.className = 'btforget';
      f.textContent = 'Forget';
      f.onclick = () => act('forget', d.address);
      li.appendChild(f);
    }
    return li;
  }

  async function act(verb, address) {
    hint(verb === 'pair' ? 'Pairing — this takes about half a minute…'
                         : 'Working…');
    try {
      const r = await fetch(`/audio/speakers/${verb}/${address}`, { method: 'POST' });
      const body = await r.json().catch(() => null);
      if (!r.ok) { hint(body || 'That did not work.'); return null; }
      return body;
    } catch (e) {
      hint('Could not reach the player.');
      return null;
    }
  }

  // Confirm or revert `[PI3-UI-030]`. Switching speakers can destroy the very
  // means of hearing whether the switch worked, so the change is provisional
  // until someone says they can hear it, and an unanswered question puts the
  // old one back rather than leaving a silent appliance.
  async function choose(device, verb) {
    const before = await sinkNow();
    const body = await act(verb, device.address);
    if (!body) { refresh(); return; }
    if (body.audible === false) {
      hint('Connected, but the sound is not reaching it yet.');
      refresh();
      return;
    }
    previous = before;
    askConfirm(device);
    refresh();
  }

  function askConfirm(device) {
    $('bt-confirm').hidden = false;
    let left = 30;
    const tick = () => {
      $('bt-countdown').textContent =
        `Going back to what worked in ${left}s if you do not answer.`;
      if (left-- <= 0) revert();
    };
    clearInterval(countdown);
    tick();
    countdown = setInterval(tick, 1000);
    $('bt-yes').onclick = () => { settle(); hint(`Playing on ${device.name}.`); };
    $('bt-no').onclick = () => revert();
  }

  function settle() {
    clearInterval(countdown);
    $('bt-confirm').hidden = true;
    previous = null;
  }

  async function revert() {
    const back = previous;
    settle();
    hint('Putting the old speaker back…');
    // Nothing to go back TO is a real case: the previous sink may have been
    // the dummy, which is to say silence. Reopening is still right -- it is
    // how the player is told to look again.
    if (back && back.address) await act('use', back.address);
    else await fetch('/command/reopen-output', { method: 'POST' });
    refresh();
  }

  async function sinkNow() {
    try {
      const r = await fetch('/audio/sink');
      const s = await r.json();
      const list = await (await fetch('/audio/speakers')).json();
      const match = (list.devices || []).find(d => d.name === s.sink);
      return match || null;
    } catch (e) { return null; }
  }

  function hint(text) { $('bt-hint').textContent = text; }

  async function refresh(scan) {
    const list = $('bt-list');
    if (scan) hint('Looking for speakers — about twenty seconds…');
    try {
      const r = await fetch(scan ? '/audio/speakers/scan' : '/audio/speakers',
                            { method: scan ? 'POST' : 'GET' });
      const body = await r.json();
      list.textContent = '';
      const devices = body.devices || [];
      for (const d of devices) list.appendChild(speakerRow(d, body.output));
      // **`[PI3-FOUND-740]` A scan that did not happen must not read as one
      // that found nothing.** The helper still sends the remembered speakers
      // when discovery never started -- they are still selectable -- so the
      // rows above are right either way, and it is the hint that has to
      // carry the difference. Checked before the empty case, because "the
      // adapter refused" is the true answer even when the list is also
      // empty, and "put yours in pairing mode" is the sentence that sent the
      // listener after the wrong problem for an afternoon.
      if (body.error) { hint(body.error); return; }
      if (!devices.length) {
        hint('No speakers known yet. Put yours in pairing mode, then look.');
        return;
      }
      if (!scan) {
        const out = body.output;
        hint(out && out.dummy
             ? 'Nothing can hear the music right now.'
             : out && out.sink ? `Playing on ${out.sink}.` : 'Where the music is playing.');
      } else {
        hint('Pick yours from the list.');
      }
    } catch (e) {
      hint('Could not reach the player.');
    }
  }

  // ----------------------------------------------------------------- power
  // Two steps, because there is no third. An appliance reached only through
  // this page cannot be switched back on from it, so a misclick costs a walk
  // to the plug -- the same reason the speaker change asks "can you hear it?"
  // rather than trusting one press `[PI3-UI-030]`.
  // The same ask-then-confirm shape as shutting down: both stop the music,
  // and neither should happen on one stray click.
  $('rs-ask').onclick = () => {
    $('rs-confirm').hidden = false;
    $('rs-ask').disabled = true;
  };
  $('rs-no').onclick = () => {
    $('rs-confirm').hidden = true;
    $('rs-ask').disabled = false;
  };
  $('rs-yes').onclick = async () => {
    $('rs-yes').disabled = true;
    $('rs-no').disabled = true;
    $('rs-hint').textContent = 'Saving where you were, then restarting…';
    try {
      const r = await fetch('/power/restart', { method: 'POST' });
      $('rs-hint').textContent = r.ok
        ? 'Restarting. This page reconnects on its own in a few seconds.'
        : `Could not restart: ${await r.text()}`;
    } catch (e) {
      // The socket dying is the expected ending, not a failure.
      $('rs-hint').textContent =
        'Restarting. This page reconnects on its own in a few seconds.';
    }
    $('rs-confirm').hidden = true;
    $('rs-yes').disabled = false;
    $('rs-no').disabled = false;
    $('rs-ask').disabled = false;
  };

  $('pw-ask').onclick = () => {
    $('pw-confirm').hidden = false;
    $('pw-ask').disabled = true;
  };
  $('pw-no').onclick = () => {
    $('pw-confirm').hidden = true;
    $('pw-ask').disabled = false;
  };
  $('pw-yes').onclick = async () => {
    $('pw-yes').disabled = true;
    $('pw-no').disabled = true;
    $('pw-hint').textContent = 'Saving where you were, then shutting down…';
    try {
      const r = await fetch('/power/off', { method: 'POST' });
      // 202, not 204: accepted. The process answering is about to be stopped,
      // so it cannot honestly report that the machine finished the job.
      $('pw-hint').textContent = r.ok
        ? 'Shutting down. Wait for the light to go out before pulling power.'
        : `Could not shut down: ${await r.text()}`;
    } catch (e) {
      // The socket dying IS the expected ending here, so it is not an error.
      $('pw-hint').textContent =
        'Shutting down. Wait for the light to go out before pulling power.';
    }
    $('pw-confirm').hidden = true;
  };

  // ---------------------------------------------------------------- radios
  // A blocked radio looks exactly like a broken button `[PI3-FOUND-050]`: the
  // speaker was paired, bonded, trusted and flashing, and Connect did nothing
  // because hci0 was soft-blocked. Say so here, where the hand already is.
  const RADIO_NAME = { bluetooth: 'Bluetooth', wlan: 'Wi-Fi', wwan: 'Mobile' };

  async function radios() {
    const box = $('radio-controls');
    try {
      const body = await (await fetch('/audio/radios')).json();
      const list = body.radios || [];
      box.textContent = '';
      let blocked = 0;
      for (const r of list) {
        const label = RADIO_NAME[r.kind] || r.kind;
        const off = r.soft === 1 || r.hard === 1;
        if (off) blocked++;
        const b = document.createElement('button');
        b.type = 'button';
        b.textContent = `${label}: ${off ? 'off' : 'on'}`;
        // Two reasons a switch is refused, and they are not the same thing.
        // A hardware switch cannot be overridden from here at all. A radio
        // carrying the default route could be switched and must not be: it is
        // the way this page is reached, and on the appliance there is no other
        // `[PI3-RF-030]`. The player would answer with the same refusal; this
        // just does not offer the trap.
        if (r.hard === 1) {
          b.disabled = true;
          b.title = 'Held off by a hardware switch.';
        } else if (r.carries_route) {
          b.disabled = true;
          b.title = `${label} carries this connection. Switching it off here `
                  + 'would cut the link to this page, and nothing short of '
                  + 'physical access would bring it back.';
        } else {
          b.onclick = async () => {
            b.disabled = true;
            await fetch(`/audio/radio/${r.kind}/${off ? 'on' : 'off'}`,
                        { method: 'POST' });
            // The speaker cannot come back before its radio does.
            setTimeout(() => { radios(); refresh(); }, 1200);
          };
        }
        box.appendChild(b);
      }
      $('radio-hint').textContent = blocked
        ? 'A radio is switched off. Nothing on it can connect until it is on.'
        : 'Aerials are on.';
    } catch (e) {
      box.textContent = '';
      $('radio-hint').textContent = 'Could not ask about the radios.';
    }
  }

  $('bt-scan').onclick = () => refresh(true);

  // ------------------------------------------------------------------- led
  // Four modes, not a checkbox `[PI3-LED-010]`: solid on (with a
  // brightness), the appliance's existing live Wi-Fi indicator, solid off,
  // or hands-off. Fetched when the panel opens, the same reason `radios()`
  // is: it costs a round trip to the database, and most sessions never
  // open Settings.
  const ledMode = $('led-mode');
  const ledBrightness = $('led-brightness');
  const ledBrightnessLabel = $('led-brightness-label');

  function showBrightness(show) {
    ledBrightness.hidden = !show;
    ledBrightnessLabel.hidden = !show;
  }

  async function led() {
    try {
      const body = await (await fetch('/led')).json();
      ledMode.value = body.mode ?? 'on';
      ledBrightness.value = body.brightness ?? 100;
      ledBrightnessLabel.textContent = `${ledBrightness.value}%`;
      showBrightness(ledMode.value === 'on');
    } catch { /* leave whatever it last showed */ }
  }

  async function postLed(mode, pct) {
    const q = mode === 'on' ? `?pct=${pct}` : '';
    await fetch(`/led/${mode}${q}`, { method: 'POST' });
  }

  ledMode.onchange = async () => {
    showBrightness(ledMode.value === 'on');
    ledMode.disabled = true;
    try {
      await postLed(ledMode.value, ledBrightness.value);
    } finally {
      ledMode.disabled = false;
    }
  };
  // Only posted on release, the same reasoning `Lempi.bindVolume` already
  // gives for the volume slider -- dragging must not spam the appliance
  // with a request (and a subprocess) per pixel.
  ledBrightness.onchange = () => {
    ledBrightnessLabel.textContent = `${ledBrightness.value}%`;
    postLed('on', ledBrightness.value);
  };
  ledBrightness.oninput = () => {
    ledBrightnessLabel.textContent = `${ledBrightness.value}%`;
  };

  // ------------------------------------------------------------------ wifi
  // Moving the appliance to a new Wi-Fi network, or serving its own
  // `[SPEC034]`. Every action that can disconnect this very page has
  // already scheduled a hard, server-side revert before it ever touches
  // anything -- this banner and its countdown are the confirm half of
  // that, a convenience for cancelling it early, never the safety net
  // itself. If this page is closed or this browser never gets back here
  // at all, the revert still fires on its own.
  const wifiKnownList = $('wifi-known-list');
  const wifiScanList = $('wifi-scan-list');
  const wifiScanBtn = $('wifi-scan-btn');
  const wifiConnectForm = $('wifi-connect-form');
  const wifiConnectSsid = $('wifi-connect-ssid');
  const wifiConnectPassword = $('wifi-connect-password');
  const apSsid = $('ap-ssid');
  const apPassword = $('ap-password');
  const apStartBtn = $('ap-start-btn');
  const apStopBtn = $('ap-stop-btn');

  // A password field with no way to check what you actually typed means the
  // only way to catch a mistake is to retype it and hope -- worse than usual
  // here, since a wrong Wi-Fi password is exactly the kind of mistake that
  // can only be found out once the radio has already switched away from
  // whatever was reachable a moment ago.
  function wirePasswordToggle(input, button) {
    button.onclick = () => {
      const shown = input.type === 'text';
      input.type = shown ? 'password' : 'text';
      button.textContent = shown ? '👁' : '🙈';
      button.title = shown ? 'Show password' : 'Hide password';
      button.setAttribute('aria-pressed', String(!shown));
    };
  }
  wirePasswordToggle(wifiConnectPassword, $('wifi-connect-password-toggle'));
  wirePasswordToggle(apPassword, $('ap-password-toggle'));

  const wifiConfirmBox = $('wifi-confirm');
  const wifiConfirmYes = $('wifi-confirm-yes');
  const wifiConfirmCountdown = $('wifi-confirm-countdown');

  let confirmTimer = null;

  function showWifiConfirm(changeId, minutes) {
    clearInterval(confirmTimer);
    let remaining = minutes * 60;
    wifiConfirmBox.hidden = false;
    const tick = () => {
      const m = Math.floor(remaining / 60), s = remaining % 60;
      wifiConfirmCountdown.textContent =
        `Reverts automatically in ${m}:${String(s).padStart(2, '0')} unless confirmed.`;
      if (remaining <= 0) { clearInterval(confirmTimer); wifiConfirmBox.hidden = true; }
      remaining--;
    };
    tick();
    confirmTimer = setInterval(tick, 1000);
    wifiConfirmYes.onclick = async () => {
      wifiConfirmYes.disabled = true;
      try {
        await fetch(`/wifi/confirm/${changeId}`, { method: 'POST' });
      } finally {
        clearInterval(confirmTimer);
        wifiConfirmBox.hidden = true;
        wifiConfirmYes.disabled = false;
      }
    };
  }

  // One fetch feeds both the known-networks list and "what's active right
  // now" -- there is nothing about NetworkManager's own connection state
  // that changes between reading it twice a moment apart.
  async function wifiKnown() {
    let rows = [];
    try {
      rows = await (await fetch('/wifi/known')).json();
    } catch { /* leave whatever it last showed */ return; }

    wifiKnownList.textContent = '';
    for (const r of rows) {
      const li = document.createElement('li');
      const active = r.active === 'yes';
      li.append(`${r.name}${active ? ' (active)' : ''} `);
      const auto = document.createElement('button');
      auto.type = 'button';
      auto.textContent = r.autoconnect === 'yes' ? 'auto: on' : 'auto: off';
      auto.onclick = async () => {
        auto.disabled = true;
        const want = r.autoconnect === 'yes' ? 'off' : 'on';
        await fetch(`/wifi/autoconnect/${encodeURIComponent(r.name)}/${want}`, { method: 'POST' });
        wifiKnown();
      };
      li.appendChild(auto);
      if (!active) {
        const forget = document.createElement('button');
        forget.type = 'button';
        forget.textContent = 'forget';
        forget.onclick = async () => {
          forget.disabled = true;
          await fetch(`/wifi/forget/${encodeURIComponent(r.name)}`, { method: 'POST' });
          wifiKnown();
        };
        li.appendChild(forget);
      }
      wifiKnownList.appendChild(li);
    }

    const activeRow = rows.find(r => r.active === 'yes');
    $('wifi-current').textContent = activeRow ? activeRow.name : 'not connected';
    const onOwnAp = activeRow && activeRow.name === 'lempi-ap';
    apStartBtn.hidden = !!onOwnAp;
    apStopBtn.hidden = !onOwnAp;
  }

  async function wifiScan() {
    wifiScanList.textContent = '';
    let rows = [];
    try {
      rows = await (await fetch('/wifi/scan')).json();
    } catch { return; }
    const seen = new Set(); // the same network answers on more than one channel
    for (const r of rows) {
      if (seen.has(r.ssid) || !r.ssid) continue;
      seen.add(r.ssid);
      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = `${r.ssid} (${r.security || 'open'}, ${r.signal}%)`;
      btn.onclick = () => {
        wifiConnectSsid.textContent = r.ssid;
        wifiConnectForm.dataset.ssid = r.ssid;
        wifiConnectForm.hidden = false;
        wifiConnectPassword.value = '';
        wifiConnectPassword.focus();
      };
      li.appendChild(btn);
      wifiScanList.appendChild(li);
    }
  }

  wifiScanBtn.onclick = () => {
    wifiScanBtn.disabled = true;
    wifiScanBtn.textContent = 'scanning…';
    wifiScan().finally(() => {
      wifiScanBtn.disabled = false;
      wifiScanBtn.textContent = 'Scan for networks';
    });
  };
  $('wifi-connect-cancel').onclick = () => { wifiConnectForm.hidden = true; };
  $('wifi-connect-go').onclick = async () => {
    const ssid = wifiConnectForm.dataset.ssid;
    const btn = $('wifi-connect-go');
    btn.disabled = true;
    try {
      const q = new URLSearchParams({ ssid, password: wifiConnectPassword.value });
      const body = await (await fetch(`/wifi/connect?${q}`, { method: 'POST' })).json();
      if (body.error) throw new Error(body.error);
      wifiConnectForm.hidden = true;
      showWifiConfirm(body.change_id, body.minutes);
    } catch (e) {
      alert(`Could not connect: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  };

  apStartBtn.onclick = async () => {
    apStartBtn.disabled = true;
    try {
      const q = new URLSearchParams();
      if (apSsid.value) q.set('ssid', apSsid.value);
      if (apPassword.value) q.set('password', apPassword.value);
      const body = await (await fetch(`/wifi/ap/start?${q}`, { method: 'POST' })).json();
      if (body.error) throw new Error(body.error);
      showWifiConfirm(body.change_id, body.minutes);
    } catch (e) {
      alert(`Could not start the access point: ${e.message}`);
    } finally {
      apStartBtn.disabled = false;
    }
  };
  apStopBtn.onclick = async () => {
    apStopBtn.disabled = true;
    try {
      const body = await (await fetch('/wifi/ap/stop', { method: 'POST' })).json();
      if (body.error) throw new Error(body.error);
      showWifiConfirm(body.change_id, body.minutes);
    } catch (e) {
      alert(`Could not stop the access point: ${e.message}`);
    } finally {
      apStopBtn.disabled = false;
    }
  };

  // Populated when the panel is opened rather than at load: it costs a
  // subprocess on the appliance, and most sessions never open the settings.
  gear.addEventListener('click', () => {
    // Not asked where the host cannot answer: each would only come back as an
    // error written into a row that is hidden anyway.
    if (!$('panel-settings').hidden) {
      if (caps.radios !== false) radios();
      if (caps.bluetooth !== false) refresh();
      if (caps.led !== false) led();
      if (caps.wifi !== false) wifiKnown();
    }
  });

  // --------------------------------------------------------------- history
  // Its own fetch rather than the socket's snapshot `[REQ-VIS-250]`: a page
  // of what has already happened is not "what is true right now", and
  // teaching the wire format to page would serve nowhere else it is used.
  let histPage = 1;

  const HIST_COLS = 7;

  function histRow(cells) {
    const tr = document.createElement('tr');
    for (const cell of cells) {
      const td = document.createElement('td');
      // A cell is plain text, or a node this function no longer has to
      // understand -- `histCell` below builds the clickable ones.
      if (cell instanceof Node) td.appendChild(cell);
      else td.textContent = cell;
      tr.appendChild(td);
    }
    return tr;
  }

  // One history cell's content: clickable when a mbid is known, reaching
  // the same preference panel every other skin and view does
  // `[REQ-VIS-285]`, plain text otherwise (unidentified audio, an
  // uncredited artist, or nothing played at all).
  function histCell(text, kind, id) {
    if (!id) return document.createTextNode(text || (kind === 'recording' ? '(unknown)' : ''));
    const span = document.createElement('span');
    span.className = 'pref-link';
    span.textContent = text;
    span.onclick = () => Lempi.editPreference(kind, id, text);
    return span;
  }

  // "Flag this for review" `[REQ-VIS-265]`. Keyed by recording when the row
  // has one, by passage when it does not -- the same fallback the server
  // resolves, so an unidentified track is exactly as flaggable as a named one.
  // Absent entirely (row.flag_kind is null) only for a play whose passage was
  // relinked away with no recording ever attached to it, which has nothing
  // left to flag `[SPEC-DF-035]`.
  function flagCell(row) {
    const td = document.createElement('td');
    if (!row.flag_kind) {
      td.title = 'nothing stable enough to flag survives for this row';
      return td;
    }
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!row.flagged;
    box.title = 'flag this track for review in Vipunen';
    box.onchange = async () => {
      const want = box.checked;
      box.disabled = true;
      try {
        await Lempi.setFlag(row.flag_kind, row.flag_id, want);
      } catch (e) {
        box.checked = !want;   // put it back; the server never saw the change
      }
      box.disabled = false;
    };
    td.appendChild(box);
    return td;
  }

  function histMessage(text) {
    const tr = document.createElement('tr');
    tr.className = 'empty';
    const td = document.createElement('td');
    td.colSpan = HIST_COLS;
    td.textContent = text;
    tr.appendChild(td);
    return tr;
  }

  async function loadHistory() {
    const size = Number($('histsize').value);
    const body = $('histtable').querySelector('tbody');
    let data;
    try {
      data = await Lempi.history(histPage, size);
    } catch (e) {
      body.textContent = '';
      body.appendChild(histMessage('Could not reach the player.'));
      return;
    }
    // The server clamps an unrecognised size rather than refusing it, so the
    // control is put back in step with what was actually asked for.
    if (data.size !== size) $('histsize').value = String(data.size);
    const pages = Math.max(1, Math.ceil(data.total / data.size));
    // A page that emptied out from under us -- the listener paged forward,
    // then something aged out, or they picked a bigger size -- settles on
    // the new last page rather than showing nothing.
    if (histPage > pages) { histPage = pages; return loadHistory(); }

    body.textContent = '';
    if (!data.entries.length) {
      body.appendChild(histMessage('Nothing played yet.'));
    }
    for (const row of data.entries) {
      const pct = row.played_pct == null ? '—' : `${Math.round(row.played_pct)}%`;
      const tr = histRow([
        Lempi.since(row.at),
        histCell(row.title || '(unknown)', 'recording', row.mbid),
        histCell(row.artist || '', 'artist', row.artist_mbid),
        row.album || '',
        pct,
        row.kind === 'play' ? 'Played' : 'Skipped',
      ]);
      tr.className = row.kind === 'play' ? 'hit' : 'miss';
      tr.appendChild(flagCell(row));
      body.appendChild(tr);
    }
    $('histpage').textContent = `Page ${histPage} of ${pages}`;
    $('histprev').disabled = histPage <= 1;
    $('histnext').disabled = histPage >= pages;
  }

  $('histsize').onchange = () => { histPage = 1; loadHistory(); };
  $('histprev').onclick = () => { if (histPage > 1) { histPage--; loadHistory(); } };
  $('histnext').onclick = () => { histPage++; loadHistory(); };

})();
