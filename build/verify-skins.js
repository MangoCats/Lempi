// Render every skin, in a real DOM, against snapshots the server could send.
//
// A skin is HTML, CSS and JavaScript, so `cargo test` cannot reach it: the Rust
// suite proves the SHELL serves the right bytes and stops there. This proves the
// bytes work -- that each skin loads through `core.js`'s own loader, survives a
// snapshot with everything in it and one with almost nothing, wires its
// transport, and drives volume through the shared fader curve rather than a
// private copy of it `[REQ-VIS-160]`.
//
// Optional by design: it needs node and jsdom, which the player itself does not.
// `verify-targets.sh` skips it when they are absent rather than failing, because
// a Pi has no business installing a JavaScript test runner to play music.
//
//   npm install jsdom && node build/verify-skins.js [snapshot.json]

const fs = require('fs');
const path = require('path');
// Exit 2, not 0, when the test cannot run. A harness that reports success
// because it did nothing is worse than no harness: this project has already
// been caught once committing on a verification that had quietly skipped.
let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch {
  console.log('SKIPPED: jsdom is not installed (npm install jsdom)');
  process.exit(2);
}

const ROOT = path.join(__dirname, '..', 'player', 'src', 'web');

// An optional live capture, for checking a skin against real library data:
//   node build/verify-skins.js snapshot.json
// The built-in fixtures below are what actually gates the check, so that it
// needs no running player and no library.
const LIVE = process.argv[2]
  ? JSON.parse(fs.readFileSync(process.argv[2], 'utf8'))
  : null;

// A second snapshot exercising branches a healthy player may not reach:
// nothing playing, nothing queued, no programme, no explanation, fade of zero.
const SPARSE = {
  playing: false, title: null, position_ms: 0, duration_ms: 0,
  // Nothing named, nothing counted: the branch where every connecting word and
  // the cover must disappear rather than dangle [REQ-VIS-170].
  passage_id: null, artist: null, album: null, plays: 0, last_played: null,
  title_source: 'unknown', artist_source: 'unknown', album_source: 'unknown',
  queue_len: 0, queue: [], volume_db: -72.0, fader_min_db: -72.0,
  program: null, program_manual: false, programs: [],
  skip: { fade_ms: 0, lead_ms: 500, fade_max_ms: 10000, lead_min_ms: 100, lead_max_ms: 2000,
          resume_save_ms: 5000, resume_save_min_ms: 1000, resume_save_max_ms: 300000 },
  underrun_samples: 0, why: null, dev_mode: false,
};

// A snapshot carrying a full Program Director explanation -- the richest
// branch, and the one a live capture may not happen to include.
const RICH = {
  ...SPARSE,
  playing: true, title: 'A Passage With Reasons', position_ms: 61000, duration_ms: 244000,
  // MusicBrainz for the recording and the artist, the file's tag for the album
  // -- which is exactly the mixed provenance the live library produces today.
  passage_id: 4242, artist: 'Some Performer', album: 'Some Release',
  plays: 12, last_played: 1735689600,
  title_source: 'musicbrainz', artist_source: 'musicbrainz', album_source: 'tags',
  queue_len: 3,
  queue: [{ qid: 101, passage_id: 1, title: 'Next One', artist: 'Another', duration_ms: 180000 },
          { qid: 102, passage_id: 2, title: 'The One After', artist: null, duration_ms: 205000 }],
  volume_db: -12.5,
  program: 'Mellow', program_manual: true,
  programs: [{ id: 1, name: 'Mellow', start: '20:00' }, { id: 2, name: 'Prog', start: '09:00' }],
  skip: { fade_ms: 2000, lead_ms: 500, fade_max_ms: 10000, lead_min_ms: 100, lead_max_ms: 2000,
          resume_save_ms: 5000, resume_save_min_ms: 1000, resume_save_max_ms: 300000 },
  why: {
    program: 'Mellow', weight: 0.8123, decayed_weight: 0.4211, pool_weight: 3120.5,
    pool_size: 8078, share_pct: 0.0135,
    artist_weight: 1.0, recording_restraint: 0.75, recording_ramp: 1.0,
    related_damping: 0.9, length_bonus: 1.08, occasion: 1.0,
    shaping: { bypassed: false, eligible_in: 8078, gathered: 400, seeds_used: 3, disliked_out: 12 },
    seed_distances: [0.21, 0.33, 0.41], flow_distance: 0.187, rank: 4,
    runners_up: [{ title: 'Nearly', weight: 0.41 }, { title: 'Also Nearly', weight: 0.39 }],
    stages: 'frequency → shaping → flow',
  },
};

// A page of play history `[REQ-VIS-250]`: one play, one skip, and a play
// written before the percentage columns existed -- the three shapes a row
// can actually be in.
const HISTORY = {
  entries: [
    { at: 1735689600, kind: 'play', title: 'A Song', artist: 'A Band',
      album: 'An Album', played_pct: 91.4,
      flag_kind: 'recording', flag_id: 'aaaaaaaa-0000-0000-0000-000000000001', flagged: false },
    { at: 1735689500, kind: 'skip', title: 'Another Song', artist: 'A Band',
      album: null, played_pct: 4.2,
      flag_kind: 'passage', flag_id: '42', flagged: true },
    // A row nothing survives to key a flag by `[REQ-VIS-265]`: the passage
    // was relinked away and no recording was ever attached to it.
    { at: 1735689400, kind: 'play', title: null, artist: null, album: null,
      played_pct: null, flag_kind: null, flag_id: null, flagged: false },
  ],
  total: 3, page: 1, size: 100,
};

const skins = fs.readdirSync(path.join(ROOT, 'skins'));
let failures = 0;

async function run(skin) {
  const dir = path.join(ROOT, 'skins', skin);
  // The shell starts core with an inline script. Here core is injected by hand
  // (jsdom fetches no subresources), so that call would fire before core exists
  // and print a ReferenceError that means nothing. Drop it; we call start below.
  const shell = fs.readFileSync(path.join(ROOT, 'shell.html'), 'utf8')
    .replace('<script>Lempi.start();</script>', '');
  const dom = new JSDOM(shell,
                        { runScripts: 'dangerously', url: 'http://localhost/?skin=' + skin });
  const { window } = dom;
  const errors = [];
  window.console.error = (...a) => errors.push(a.join(' '));
  const check = (cond, msg) => { if (!cond) errors.push(msg); };

  // jsdom does not fetch subresources, so <link>/<script> onload would never
  // fire and core's loader would wait forever. Fire them; we run skin.js below.
  const create = window.document.createElement.bind(window.document);
  window.document.createElement = tag => {
    const el = create(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };

  const posted = [];
  let refuse = false;                    // make the POST stub answer as refused
  window.fetch = (url, opts) => {
    if (opts && opts.method === 'POST') {
      posted.push(url);
      return Promise.resolve({ ok: !refuse, status: refuse ? 404 : 204 });
    }
    if (url === '/skins') {
      return Promise.resolve({ json: () => Promise.resolve(skins.map(n => ({ name: n, label: n }))) });
    }
    const m = /^\/skin\/([^/]+)\/(.+)$/.exec(url);
    if (m) return Promise.resolve({ text: () => Promise.resolve(fs.readFileSync(path.join(ROOT, 'skins', m[1], m[2]), 'utf8')) });
    if (/^\/art\//.test(url)) return Promise.reject(new Error('no art in a test DOM'));
    // An explanation for a queued passage, so the panel's fetch path is
    // exercised rather than only its failure path.
    if (/^\/why\/\d+$/.test(url)) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        { ...RICH.why, program: 'Queued Reasons' }) });
    }
    if (/^\/history\?/.test(url)) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(HISTORY) });
    }
    return Promise.reject(new Error('unexpected fetch ' + url));
  };

  let sock = null;
  window.WebSocket = function () { sock = this; this.close = () => {}; };

  // As a <script>, not eval: a classic script's top-level `const` lands in the
  // shared script scope where later scripts can see it, and eval's does not.
  const runScript = src => {
    const el = create('script');
    el.textContent = src;
    window.document.body.appendChild(el);
  };
  runScript(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  // core.js is a library now; the page starts it, exactly as shell.html does.
  runScript('Lempi.start();');

  // Wait for core to have loaded the skin and opened its socket.
  for (let i = 0; i < 200 && !sock; i++) await new Promise(r => setTimeout(r, 5));
  if (!sock) { console.log(`${skin.padEnd(11)} FAIL  core never finished loading the skin`); failures++; return; }

  // core appended a <script> jsdom will not execute; run it in the same window.
  try {
    runScript(fs.readFileSync(path.join(dir, 'skin.js'), 'utf8'));
  } catch (e) {
    console.log(`${skin.padEnd(11)} FAIL  skin.js threw on load: ${e.message}`);
    failures++;
    return;
  }

  const cases = [['sparse', SPARSE], ['rich', RICH]];
  if (LIVE) cases.push(['live', LIVE]);
  for (const [label, snap] of cases) {
    sock.onopen && sock.onopen();
    try {
      sock.onmessage({ data: JSON.stringify(snap) });
    } catch (e) {
      errors.push(`${label} snapshot threw: ${e.message}`);
    }
  }

  // Cover art: jsdom never loads images, so the browser's load event has to be
  // faked. Without this the check would pass on a skin that can never show a
  // cover -- which is exactly the failure being guarded against.
  const art = window.document.getElementById('art');
  const layers = box => [...box.querySelectorAll('img')];
  const requested = box => layers(box).find(i => i.getAttribute('src'));
  let artOk = 'no #art element';
  if (art) {
    if (!art.classList.contains('artbox')) {
      artOk = 'not built as a crossfade box';
    } else if (layers(art).length < 2) {
      // One layer cannot fade to another: swapping a src is instantaneous.
      artOk = 'only one layer, so nothing can cross-fade';
    } else if (!art.querySelector('.ph svg')) {
      // The mark is what keeps the box occupied. Without it the element
      // collapses between covers and everything below jumps `[REQ-VIS-127]`.
      artOk = 'no placeholder mark beneath the layers';
    } else if (!requested(art)) {
      artOk = 'src never set';
    } else if (art.querySelector('img.on')) {
      artOk = 'shown before it loaded';
    } else {
      const first = requested(art);
      first.dispatchEvent(new window.Event('load'));
      artOk = art.querySelector('img.on') === first ? null : 'not shown after load';
      if (!artOk) {
        check(art.classList.contains('has-art'),
              'the mark must retire once a cover is actually up');
        // A passage whose file has no picture is the ordinary case here --
        // about a third of the library -- and must fall back to the mark
        // rather than to an empty box. Driven by pushing a snapshot, so what
        // is under test is the path the skin really takes.
        sock.onmessage({ data: JSON.stringify({ ...RICH, passage_id: 999 }) });
        const second = layers(art).find(i => i !== first);
        check(second && second.getAttribute('src'),
              'the incoming cover must load on the OTHER layer, or there is no fade');
        if (second) second.dispatchEvent(new window.Event('error'));
        artOk = art.classList.contains('has-art')
              ? 'a 404 must fall back to the mark' : null;
      }
    }
    // Whatever happens above, the box itself must never be taken out of the
    // layout -- that is the entire point of the change.
    check(!art.hidden, 'the art box must never be hidden; it reserves the space');
    const svg = art.querySelector('.ph svg');
    check(svg && svg.getAttribute('stroke') === 'currentColor',
          'the mark must be drawn in currentColor so each skin colours it');
  }
  if (artOk) errors.push('cover art: ' + artOk);

  // A skin that shows the back of the sleeve must ask the back route for it.
  // MuLibPlay put front and back side by side and 559 of its 675 albums had a
  // back; pointing both boxes at the same URL would show the front twice and
  // look deliberate.
  const back = window.document.getElementById('artback');
  if (back) {
    const front = art && requested(art) && requested(art).getAttribute('src');
    const bimg = requested(back);
    const bsrc = bimg && bimg.getAttribute('src');
    check(/\/art\/\d+\/back$/.test(bsrc || ''),
          `back cover should come from the back route, got ${bsrc}`);
    check(bsrc !== front, 'front and back must not be the same image');
    // Absent is the common case, so it must fall back to the mark rather than
    // render broken beside a good cover.
    if (bimg) bimg.dispatchEvent(new window.Event('error'));
    check(!back.classList.contains('has-art'),
          'a missing back cover must fall back to the mark, not render broken');
    check(!back.hidden, 'the back box must also keep its space');
  }

  // The transport must be wired, and the picker populated from the catalogue.
  const skipBtn = window.document.querySelector('[data-cmd="skip"]');
  skipBtn.onclick();
  // Skip is momentary -- no snapshot field reports that one happened -- so it
  // carries the same receipt the queue verbs do, and for the same 700 ms. The
  // class comes from core in every skin; whether it is drawn is each skin's
  // own business, which is why this asserts the class and not a colour.
  check(skipBtn.classList.contains('acked'),
        'Skip must acknowledge the press: nothing else reports that it landed');
  // Volume must round-trip through the shared fader curve, not a per-skin copy.
  const vol = window.document.getElementById('volume');
  vol.value = 0.5;
  vol.oninput();
  vol.onchange();
  // After the volume post, deliberately: the ordering check below reads
  // `posted[1]` as the volume, and these presses would displace it.
  //
  // Play and Pause must NOT flash. `playing` already comes back in the snapshot
  // and every skin draws it one way or another, so a second signal over the top
  // would be answering a question already answered.
  for (const cmd of ['play', 'pause']) {
    const b = window.document.querySelector(`[data-cmd="${cmd}"]`);
    if (!b) continue;
    b.onclick();
    check(!b.classList.contains('acked'),
          `${cmd} reports itself through the snapshot and must not also flash`);
  }
  const picker = window.document.querySelector('[data-skins]');
  const opts = picker ? picker.options.length : 0;

  const title = window.document.getElementById('title');
  const queue = window.document.getElementById('queue');

  // Provenance must be VISIBLE, not a tooltip `[REQ-VIS-120]` -- that is the
  // whole point of the change, and a badge that renders nowhere would still
  // leave every other assertion here passing. The fixture names a MusicBrainz
  // title against a tag-sourced album, so a skin that hard-codes one marker
  // for the whole snapshot fails: at least two different sources must show.
  //
  // MuLibPlay is exempt by decision `[REQ-VIS-122]`: it reproduces the face of
  // the original, which had no such marks. The requirement is met by the other
  // skins, and the exemption is named here rather than implied by a skin that
  // quietly renders none -- so that a skin which is SUPPOSED to mark names and
  // stops doing so still fails.
  const marksProvenance = skin !== 'mulibplay';
  const badges = [...window.document.querySelectorAll('.src')];
  if (marksProvenance) {
    check(badges.length > 0, 'no provenance badge rendered anywhere');
    check(badges.some(b => b.dataset.src === 'musicbrainz'),
          'the MusicBrainz-sourced name is not marked as one');
  } else {
    check(badges.length === 0,
          `${skin} is exempt from provenance marks but rendered ${badges.length}`);
  }
  check(badges.every(b => b.textContent.trim()),
        'a provenance badge rendered empty');
  check(badges.every(b => b.title && /from /.test(b.title)),
        'a provenance badge carries no explanation on hover');
  // The badge must not be mistakeable for part of the name: it is a separate
  // element, so anything wanting the bare name can still get it.
  if (title) {
    check([...title.childNodes].some(n => n.nodeType === 3 &&
          n.textContent.includes('A Passage With Reasons')),
          'the title text is not a text node of its own');
  }

  // A skin that moves settings behind a gear must be able to open it again.
  // The controls stay in the DOM either way -- the bindings attach once at
  // load -- so the check is that the panels actually swap.
  const gear = window.document.getElementById('gear');
  if (gear) {
    const main = window.document.getElementById('panel-main');
    const set = window.document.getElementById('panel-settings');
    check(main && set, 'a gear needs both panels to switch between');
    check(!main.hidden && set.hidden, 'settings must start closed');
    gear.onclick();
    check(main.hidden && !set.hidden, 'the gear must open the settings screen');
    check(gear.getAttribute('aria-expanded') === 'true', 'aria-expanded must follow');
    // The controls moved there must still be wired to the engine.
    const fade = window.document.getElementById('skipfade');
    check(fade && typeof fade.onchange === 'function',
          'the skip control must still be bound after the move');
    fade.value = 3;
    fade.onchange();
    // The resume interval is bound in the same panel and must reach the engine.
    const rs = window.document.getElementById('resumesave');
    if (rs) {
      check(rs.value === '5.0', `resume interval should show 5.0 s, got ${rs.value}`);
      rs.value = 30; rs.onchange();
      check(posted.includes('/resume/save/30000'),
            `resume interval must post, got ${JSON.stringify(posted)}`);
    }
    check(posted.includes('/skip/fade/3000'),
          `a moved control must still reach the engine, posted ${JSON.stringify(posted)}`);
    gear.onclick();
    check(!main.hidden && set.hidden, 'the gear must close it again');
  }

  // A skin offering play history must be able to open it, fetch a page, and
  // close again without disturbing the other two panels `[REQ-VIS-250]`.
  const histBtn = window.document.getElementById('histbtn');
  if (histBtn) {
    const main = window.document.getElementById('panel-main');
    const hist = window.document.getElementById('panel-history');
    check(main && hist, 'a history button needs both panels to switch between');
    check(!main.hidden && hist.hidden, 'history must start closed');
    histBtn.onclick();
    await new Promise(r => setTimeout(r, 20));
    check(main.hidden && !hist.hidden, 'the history button must open the history screen');
    check(histBtn.getAttribute('aria-expanded') === 'true', 'aria-expanded must follow');
    const rows = () => [...window.document.querySelectorAll('#histtable tbody tr')];
    check(rows().length === HISTORY.entries.length,
          `history should render ${HISTORY.entries.length} rows, got ${rows().length}`);
    check(rows().some(r => /Played/.test(r.textContent)), 'a play must read as Played');
    check(rows().some(r => /Skipped/.test(r.textContent)), 'a skip must read as Skipped');
    check(rows().some(r => /91%|91\.4%/.test(r.textContent)), 'a known percentage must be shown');
    check(rows().some(r => /—/.test(r.textContent)),
          'a row with no percentage must read as absent, not 0%');

    // "Flag this for review" `[REQ-VIS-265]`: a checkbox where a stable
    // subject exists, nothing where it does not, and toggling it posts the
    // new state -- checking and unchecking are the same call either way.
    const boxes = () => rows().map(r => r.querySelector('input[type=checkbox]'));
    check(boxes().filter(Boolean).length === 2,
          `expected 2 flaggable rows, got ${boxes().filter(Boolean).length}`);
    check(boxes()[2] === null, 'a row with no stable subject must offer no checkbox at all');
    check(boxes()[0].checked === false && boxes()[1].checked === true,
          'the checkbox must start in the state the server reported');
    boxes()[0].checked = true;
    boxes()[0].onchange();
    await new Promise(r => setTimeout(r, 20));
    check(posted.some(u => u === '/history/flag/recording/' +
                            'aaaaaaaa-0000-0000-0000-000000000001?flagged=true'),
          `expected a flag POST, got ${JSON.stringify(posted)}`);

    histBtn.onclick();
    check(!main.hidden && hist.hidden, 'the history button must close it again');
  }

  // The programme list orders by engagement time, not by name, and the
  // times beside each button carry whether the schedule is actually the
  // thing choosing right now.
  const stations = window.document.getElementById('stations');
  if (stations) {
    const rows = () => [...stations.querySelectorAll('.stationrow')];
    check(rows().length === RICH.programs.length,
          `stations should render ${RICH.programs.length} rows, got ${rows().length}`);
    const starts = rows().map(r => r.querySelector('.engagetime').textContent);
    const sorted = [...starts].sort();
    check(JSON.stringify(starts) === JSON.stringify(sorted),
          `programmes should be ordered by start time, got ${JSON.stringify(starts)}`);
    // RICH sets program_manual: true -- the clock is not the one choosing.
    check(rows().every(r => r.querySelector('.engagetime').classList.contains('dim')),
          'engagement times must read as inert while a manual pick is in force');
    sock.onmessage({ data: JSON.stringify({ ...RICH, program_manual: false }) });
    check(rows().every(r => !r.querySelector('.engagetime').classList.contains('dim')),
          'engagement times must read as live once the clock is choosing again');
    // Restore the state later checks in this run expect.
    sock.onmessage({ data: JSON.stringify(RICH) });

    // Unchecking must freeze on whatever is engaged right now, not do
    // nothing and let the next snapshot spring the box back on -- the
    // control being a dead end in that direction is the bug this guards.
    sock.onmessage({ data: JSON.stringify({ ...RICH, program_manual: false }) });
    const autoclock = window.document.getElementById('autoclock');
    autoclock.checked = false;
    autoclock.onchange({ target: autoclock });
    const active = RICH.programs.find(p => p.name === RICH.program);
    check(posted.includes(`/program/${active.id}`),
          `unchecking must freeze on the active programme, posted ${JSON.stringify(posted)}`);
  }

  // Picking which passage the explanation panel describes `[REQ-VIS-100]`.
  // The skin that does this shows ONE control set beside the picked row, so
  // the rows must not carry their own -- two ways to move a passage is how they
  // drift apart.
  const nowrow = window.document.getElementById('nowrow');
  if (nowrow) {
    const rows = () => [...window.document.querySelectorAll('#queue li')];
    check(nowrow.classList.contains('picked'),
          'the playing passage must be picked to begin with');
    check(rows().every(r => !r.querySelector('.qedit')),
          'rows must not carry controls when the skin uses one shared set');
    check(window.document.getElementById('qpick').hidden,
          'the shared controls belong to a queued row, not the playing one');

    rows()[0].onclick();
    await new Promise(r => setTimeout(r, 20));
    check(!nowrow.classList.contains('picked'), 'picking a queued row unpicks the playing one');
    check(rows()[0].classList.contains('picked'), 'the picked row must be marked');
    const qp = window.document.getElementById('qpick');
    check(!qp.hidden, 'picking a queued row must reveal the controls');
    check(qp.querySelector('.qedit'), 'the shared set must hold the edit controls');
    // They ride the heading rather than taking a line of their own, so the
    // queue does not shift as rows are picked.
    const head = qp.closest('.qhead');
    check(head && head.querySelector('h2'),
          'the shared controls must sit on the section heading row');
    // The picked row is the one highlighted, so the controls must not name it
    // again: that repetition was removed and should not come back.
    check(!/Next One/.test(qp.textContent),
          `the controls must not repeat the picked title, got ${JSON.stringify(qp.textContent)}`);
    // The panel must now describe the PICKED passage, not the playing one.
    check(/Queued Reasons/.test(window.document.getElementById('why').textContent),
          'the explanation must follow the pick');
    check(/Next One/.test(window.document.getElementById('whotitle').textContent),
          'the heading must name the passage being explained');
    // And they must act on the picked passage, not on whatever was first.
    //
    // A real dispatched click, not a direct `onclick()` call: the handler is
    // delegated to the document in the capture phase precisely so it survives
    // the buttons being rebuilt, and reaching for a property no button carries
    // any more would test nothing at all. This is the path a listener uses.
    const fire = b => b.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));

    // The press is acknowledged the moment the pointer lands, before the
    // request goes anywhere and before any snapshot could come back. That is
    // the point of raising it there: it is the one signal that cannot be lost
    // to a rebuild or held up by a round trip.
    const first = qp.querySelectorAll('button')[0];
    first.dispatchEvent(new window.MouseEvent('pointerdown', { bubbles: true }));
    check(first.classList.contains('acked'),
          'a pointer landing on a queue verb must acknowledge it at once');

    fire(qp.querySelectorAll('button')[0]);
    // The ENTRY's id, not the passage's `[REQ-VIS-186]`. The fixture gives them
    // different values precisely so a control still addressing the passage
    // fails here rather than passing by coincidence.
    check(posted.some(u => u === '/queue/101/remove'),
          `the shared controls must act on the picked entry, posted ${JSON.stringify(posted)}`);

    // The fault all of this exists to close: the control set was emptied and
    // refilled on every snapshot, twice a second, so a press whose mousedown
    // and mouseup fell either side of one was never delivered as a click at
    // all -- which is what made these controls feel intermittent. Push the
    // same snapshot twice: the queue has not changed, so the buttons must be
    // the same objects afterwards, and must still act.
    sock.onmessage({ data: JSON.stringify(RICH) });
    await new Promise(r => setTimeout(r, 20));
    const held = qp.querySelectorAll('button')[0];
    sock.onmessage({ data: JSON.stringify(RICH) });
    await new Promise(r => setTimeout(r, 20));
    check(qp.querySelectorAll('button')[0] === held,
          'a snapshot that does not change the queue must not replace the controls');
    fire(qp.querySelectorAll('button')[0]);
    check(posted.filter(u => u === '/queue/101/remove').length === 2,
          `a control must still act after a snapshot, posted ${JSON.stringify(posted)}`);

    // A shift that the engine would clamp answers 204 like any other, so a
    // live-looking button at the end of the queue is one that can never do
    // anything `[REQ-VIS-185]`. The picked entry is first, so `sooner` is the
    // one with nowhere to go.
    const verbs = [...qp.querySelectorAll('button')];
    check(verbs[1] && verbs[1].disabled,
          'the first queued entry cannot be moved sooner, and must say so');
    check(verbs[2] && !verbs[2].disabled,
          'it can still be moved later, so that verb must stay live');

    // A refused request must not look like a taken one. The engine answers 404
    // for an entry that has already left the queue, which is exactly the race a
    // listener runs into by pressing twice.
    refuse = true;
    fire(verbs[2]);
    await new Promise(r => setTimeout(r, 20));
    check(!verbs[2].classList.contains('acked') && verbs[2].classList.contains('failed'),
          'a refused queue edit must say so rather than fading out like a success');
    refuse = false;

    nowrow.onclick();
    await new Promise(r => setTimeout(r, 20));
    check(nowrow.classList.contains('picked'), 'the playing passage must be pickable again');
    // And the controls go away with the selection. Only the appearing half was
    // asserted before, so a skin that revealed them and never hid them again
    // would have passed.
    check(window.document.getElementById('qpick').hidden,
          'unpicking the queued row must hide the controls again');
  }

  // Development mode must be visible, not remembered `[PI-SET-016]`: a
  // notation that names it and a ground that shifts, both driven by the
  // snapshot so a mode left on cannot look like a mode switched off.
  const dev = window.document.getElementById('devmode');
  if (dev) {
    check(dev.hidden, 'diagnostics notation must be hidden when dev_mode is false');
    check(!window.document.body.classList.contains('dev'),
          'the wine ground must be off when dev_mode is false');
    sock.onmessage({ data: JSON.stringify({ ...RICH, dev_mode: true }) });
    check(!dev.hidden, 'diagnostics notation must appear when dev_mode is true');
    check(window.document.body.classList.contains('dev'),
          'the ground must shift when dev_mode is true');
    sock.onmessage({ data: JSON.stringify(RICH) });
    check(dev.hidden, 'and clear again when it goes off');
  }

  // Startup: the web server binds before the Program Director is built, so for
  // those seconds the snapshot reports no programmes because none are KNOWN
  // yet. Rendering that as a silently blank panel read as the feature having
  // broken, and keeping the previous rows on screen instead would be worse --
  // `/program/{id}` validates against this very list, so a press in that
  // window is answered 404 and dropped without a word.
  //
  // `pool` is absent exactly while there is no Director, which is what tells
  // "not yet" apart from "none configured" without resorting to a timer.
  {
    const prog = window.document.getElementById('prog');
    const starting = { ...RICH, programs: [], program: null, pool: null };
    sock.onmessage({ data: JSON.stringify(starting) });
    await new Promise(r => setTimeout(r, 20));
    if (stations) {
      check(/starting/i.test(stations.textContent),
            `an empty list must say the Director is starting, got ${JSON.stringify(stations.textContent)}`);
      check(window.document.getElementById('autoclock').disabled,
            'the autoselect box must not accept a choice it cannot name a programme for');
    }
    if (prog) {
      check(prog.disabled, 'the picker must not look usable while it can offer nothing');
      check(/starting/i.test(prog.textContent),
            `the picker must say why it is empty, got ${JSON.stringify(prog.textContent)}`);
    }

    // A Director that IS there and still reports none means none are
    // configured. Saying "starting" for ever would be a lie on such a system.
    sock.onmessage({ data: JSON.stringify({ ...starting, pool: [0, 0] }) });
    await new Promise(r => setTimeout(r, 20));
    const shown = stations ? stations.textContent : (prog ? prog.textContent : '');
    check(/no programmes/i.test(shown) && !/starting/i.test(shown),
          `a Director reporting none must not read as starting up, got ${JSON.stringify(shown)}`);

    // And it all comes back when the programmes do.
    sock.onmessage({ data: JSON.stringify(RICH) });
    await new Promise(r => setTimeout(r, 20));
    if (stations) {
      check(stations.querySelectorAll('.button, .buttonOn').length === RICH.programs.length,
            'the station buttons must return when the Director does');
      check(!window.document.getElementById('autoclock').disabled,
            'and the autoselect box must be usable again');
    }
    if (prog) check(!prog.disabled, 'the picker must be usable again');
  }

  // The same guarantee for the skins that put a control set on every row
  // rather than one beside the picked one. A queue that has not changed must
  // come through a snapshot as the very same rows: rebuilding them regardless
  // is what dropped presses that spanned a push.
  const qlist = window.document.getElementById('queue');
  if (qlist && qlist.firstElementChild) {
    sock.onmessage({ data: JSON.stringify(RICH) });
    await new Promise(r => setTimeout(r, 20));
    const firstRow = qlist.firstElementChild;
    sock.onmessage({ data: JSON.stringify(RICH) });
    await new Promise(r => setTimeout(r, 20));
    check(qlist.firstElementChild === firstRow,
          'a snapshot that does not change the queue must not rebuild its rows');
    // And a queue that HAS changed must still be redrawn -- a guard that never
    // lets go is the same bug wearing the opposite coat.
    sock.onmessage({ data: JSON.stringify({ ...RICH, queue: RICH.queue.slice(1) }) });
    await new Promise(r => setTimeout(r, 20));
    check(qlist.children.length === 1,
          `a changed queue must redraw, got ${qlist.children.length} rows`);
    sock.onmessage({ data: JSON.stringify(RICH) });
    await new Promise(r => setTimeout(r, 20));
  }

  // `+1` when a history panel exists: the flag-checkbox toggle above posts
  // once, on top of whatever this skin's other controls already send
  // `[REQ-VIS-265]`.
  // The shared-control skin sends three: a remove, the same remove again to
  // prove the buttons still work after a snapshot, and a shift the stub
  // refuses so the failed state has something to report.
  // `+2` for the Play and Pause presses above, which exist to prove they do
  // NOT flash; they still reach the engine like any other press.
  const expectedPosts = 2 + (gear ? (nowrow ? 7 : 4) : 2) + (stations ? 1 : 0) + (histBtn ? 1 : 0);
  const ok = errors.length === 0 && posted.length === expectedPosts
             && opts === skins.length && posted[1] === '/volume/-18';
  if (!ok) failures++;
  console.log(
    `${skin.padEnd(11)} ${ok ? 'OK  ' : 'FAIL'}  ` +
    `title=${JSON.stringify((title && title.textContent) || '').slice(0, 30).padEnd(32)} ` +
    `badges=${badges.length} ` +
    `queue=${queue ? queue.children.length : '-'} rows  posted=${JSON.stringify(posted)}  ` +
    `picker=${opts}`);
  for (const e of errors) console.log('    ! ' + e);
}


// ---------------------------------------------------------------------------
// The browse page. Its own check, because it is not a skin: it has no socket,
// it drives three listings and a selection, and it is where the last several
// faults landed -- an empty listing that was really a failed query, a dead end
// on an artist, and an order that came out backwards.
async function runBrowse() {
  const html = fs.readFileSync(path.join(ROOT, 'browse.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/browse' });
  const { window } = dom;
  const errors = [];
  window.console.error = (...a) => errors.push(a.join(' '));

  const create = window.document.createElement.bind(window.document);
  window.document.createElement = tag => {
    const el = create(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };

  const ARTISTS = [{ name: 'ABBA', artist: null, passages: 3, plays: 9 },
                   { name: 'Steely Dan', artist: null, passages: 8, plays: 40 }];
  const ALBUMS = [{ name: 'Aja', artist: 'Steely Dan', passages: 7, plays: 30 }];
  const TRACKS = [
    { passage_id: 11, title: 'Black Cow', artist: 'Steely Dan', album: 'Aja', plays: 4, track_no: 1, disc_no: 1 },
    { passage_id: 12, title: 'Aja', artist: 'Steely Dan', album: 'Aja', plays: 2, track_no: 2, disc_no: 1 },
  ];

  const posted = [];
  let fail = null;                       // set to make /browse/* answer 500
  window.fetch = (url, opts) => {
    if (opts && opts.method === 'POST') { posted.push(url); return Promise.resolve({ ok: true }); }
    if (url === '/skins') return Promise.resolve({ json: () => Promise.resolve([]) });
    const m = /^\/browse\/(\w+)/.exec(url);
    if (m) {
      if (fail) return Promise.resolve({ ok: false, status: fail });
      const rows = { artists: ARTISTS, albums: ALBUMS, tracks: TRACKS }[m[1]] || [];
      return Promise.resolve({ ok: true, json: () => Promise.resolve(rows) });
    }
    return Promise.reject(new Error('unexpected fetch ' + url));
  };

  const runScript = src => {
    const el = create('script');
    el.textContent = src;
    window.document.body.appendChild(el);
  };
  runScript(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript(fs.readFileSync(path.join(ROOT, 'browse.js'), 'utf8'));

  const settle = () => new Promise(r => setTimeout(r, 30));
  await settle(); await settle();

  const $ = id => window.document.getElementById(id);
  const rows = () => [...window.document.querySelectorAll('#rows li')]
    .filter(li => !li.classList.contains('letter'));
  const check = (cond, msg) => { if (!cond) errors.push(msg); };

  // Artists list, with the alphabet built from what is actually there.
  check(rows().length === 2, `artists: ${rows().length} rows, want 2`);
  const az = [...window.document.querySelectorAll('#az button')];
  check(az.length === 27, `alphabet: ${az.length} letters, want 27`);
  check(az.some(b => b.textContent === 'A' && !b.disabled), 'A should be live');
  check(az.some(b => b.textContent === 'Q' && b.disabled), 'Q should be disabled');
  check($('verbs').hidden, 'the verbs belong to the track listing only');

  // An artist narrows to albums; an album narrows to its tracks.
  rows()[1].onclick();
  await settle();
  check($('crumbs').textContent.includes('Steely Dan'), 'crumb trail should name the artist');
  rows()[0].onclick();
  await settle();
  check(!$('verbs').hidden, 'the verbs appear on tracks');
  check(rows().length === 2, `album tracks: ${rows().length}, want 2`);
  // Album order, so the number leads and the alphabet is gone.
  check(rows()[0].textContent.includes('1. Black Cow'), 'track number should lead in album order');
  check(window.document.querySelectorAll('#az button').length === 0,
        'no alphabet over a running order');

  // A passage's own facts [REQ-VIS-270] are one tap away, without also
  // ticking the row's checkbox underneath it.
  const info = rows()[0].querySelector('a.info');
  check(!!info, 'each track row must carry an info link');
  check(info && info.getAttribute('href') === '/passage/11',
        `the info link must name this row's own passage, got ${info && info.getAttribute('href')}`);
  // Not a real dispatched click -- jsdom attempts actual navigation for an
  // `<a href>`'s default action, which it does not implement and would log
  // as a spurious error here. Checking that the handler itself calls
  // `stopPropagation` is what actually matters: that call is the only thing
  // standing between a real tap and also ticking the row underneath it.
  let stopped = false;
  info.onclick({ stopPropagation: () => { stopped = true; } });
  check(stopped, "the info link's own click handler must call stopPropagation");

  // Nothing selected: the verbs must refuse rather than act.
  check($('v-now').disabled && $('v-next').disabled && $('v-last').disabled,
        'verbs must start disabled');
  $('v-next').onclick();
  check(posted.length === 0, 'a disabled verb must not post');

  // Select both, in listing order, and queue them as one request.
  for (const box of window.document.querySelectorAll('.pick')) { box.checked = true; }
  window.document.querySelector('.pick').onchange();
  check(!$('v-next').disabled, 'a selection must arm the verbs');
  $('v-next').onclick();
  await settle();
  check(posted.length === 1, `one request, got ${posted.length}`);
  check(posted[0] === '/queue/11,12/next',
        `must send the list in listing order, got ${posted[0]}`);

  // A failed query must say so, not render as an empty library.
  fail = 500;
  $('q').value = 'zzz';
  $('q').oninput();
  await new Promise(r => setTimeout(r, 260));
  check(/could not read/i.test($('note').textContent),
        `a failed query must be reported, got "${$('note').textContent}"`);

  console.log(`${'browse'.padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `${rows().length} rows rendered, posted=${JSON.stringify(posted)}`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

// ---------------------------------------------------------------------------
// The review page. It records judgements that later change what a passage IS,
// so the things worth pinning are the refusals: only contradictions listed, no
// reassignment without a chosen candidate, and a failed write that does not
// leave a card looking decided.
async function runReview() {
  const html = fs.readFileSync(path.join(ROOT, 'review.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/review' });
  const { window } = dom;
  const errors = [];
  window.console.error = (...a) => errors.push(a.join(' '));
  const create = window.document.createElement.bind(window.document);
  window.document.createElement = tag => {
    const el = create(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };

  const QUEUE = {
    progress: { ran: true, checked: 8078, confirmed: 6500, contradicted: 2, decided: 0 },
    items: [
      { passage_id: 21, stored_mbid: 'rec-wrong', title: 'Wrong Song',
        artist: 'Some Band', album: 'Some Record', score: 0.97,
        severity: 'wrong-song', rank: 0,
        suggested: [{ mbid: 'rec-real', title: 'Right Song', artist: 'A Band', score: 0.97 }] },
      // A real MusicBrainz id -- the artist-fix panel is only offered
      // against one of these, never a placeholder [SPEC-SUI-197].
      { passage_id: 22, stored_mbid: 'aaaaaaaa-0000-0000-0000-000000000022',
        title: 'Another', artist: null,
        album: null, score: 0.93, severity: 'wrong-song', rank: 0, suggested: [] },
      // The bulk of a real library, and off by default: if these render
      // without being asked for, the serious cases are buried again.
      { passage_id: 23, stored_mbid: 'rec-press', title: 'Why Worry',
        artist: 'Dire Straits', album: 'Brothers in Arms', score: 0.99,
        severity: 'different-id', rank: 3,
        suggested: [{ mbid: 'rec-51', title: 'Why Worry (5.1 mix)',
                      artist: 'Dire Straits', score: 0.99 }] },
      { passage_id: 24, stored_mbid: 'rec-unk', title: 'Obscure', artist: null,
        album: null, score: null, severity: 'unverified', rank: 5, suggested: [] },
      // Already judged in an earlier session, and already written into the
      // library -- the case undo must refuse rather than silently drop.
      { passage_id: 25, stored_mbid: 'rec-done', title: 'Settled', artist: 'Someone',
        album: null, score: 0.95, severity: 'wrong-song', rank: 1, suggested: [],
        decision: 'reassigned', chosen_mbid: 'rec-new', applied: true },
    ],
  };
  const RELEASES = [
    { mbid: 'rel-1', title: 'The Album', date: '1985-01-01', status: 'Official',
      track_count: 12, chosen: false },
    { mbid: 'rel-2', title: 'A Later Compilation', date: '2004-01-01',
      status: 'Official', track_count: 20, chosen: false },
  ];
  const posted = [];
  let writeFails = false;
  window.fetch = (url, opts) => {
    if (opts && opts.method === 'POST') {
      posted.push(url);
      if (/\/25\/reopen$/.test(url)) {
        // What the server says about withdrawing an applied decision.
        return Promise.resolve({ ok: false, status: 409,
          text: () => Promise.resolve('already applied to the library; use --revert') });
      }
      return Promise.resolve({ ok: !writeFails, status: writeFails ? 500 : 204,
                               text: () => Promise.resolve('') });
    }
    if (url === '/skins') return Promise.resolve({ json: () => Promise.resolve([]) });
    if (url === '/review/queue')
      return Promise.resolve({ ok: true, json: () => Promise.resolve(QUEUE) });
    if (url.startsWith('/review/releases/'))
      return Promise.resolve({ ok: true, json: () => Promise.resolve(RELEASES) });
    if (url.startsWith('/api/musicbrainz/search'))
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        /q=nothing/.test(url) ? [] : [
          { mbid: 'rec-searched', title: 'Found By Search', artist: 'Some Artist', score: 0.8 },
        ]) });
    return Promise.resolve({ ok: false, status: 404 });
  };
  // jsdom does not fetch `<script src>`; the page's scripts are injected the
  // same way the other checks do it, as classic scripts so `Lempi` is visible.
  const runScript = src => {
    const el = create('script');
    el.textContent = src;
    window.document.body.appendChild(el);
  };
  runScript(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript(fs.readFileSync(path.join(ROOT, 'review.js'), 'utf8'));

  const $ = id => window.document.getElementById(id);
  const settle = () => new Promise(r => setTimeout(r, 30));
  await settle(); await settle();

  const check = (cond, msg) => { if (!cond) errors.push(msg); };
  const cards = () => [...window.document.querySelectorAll('.card')];
  const chip = name => [...window.document.querySelectorAll('.chip')]
                         .find(b => b.className.includes(name));

  // Severity triage is the point of the page: on this library the serious
  // cases are 41 against 526, so anything that renders the bulk by default
  // buries them.
  check(cards().length === 2, `${cards().length} cards shown, want the 2 serious ones`);
  check(!cards().some(c => c.dataset.passage === '23'),
        'a different-pressing case must not show until asked for');
  check(/8,?078/.test($('tally').textContent), 'the tally should report what was checked');

  // Every grade gets a chip carrying its own count, so the size of each kind
  // of problem is visible before choosing what to work through.
  check(chip('different-id'), 'no chip for different-id');
  check(/1\b/.test(chip('different-id').textContent),
        `the chip should carry its count, got "${chip('different-id').textContent}"`);
  check(chip('different-id').getAttribute('aria-pressed') === 'false',
        'different-id must start off');
  check(chip('wrong-song').getAttribute('aria-pressed') === 'true',
        'wrong-song must start on');

  chip('different-id').onclick();
  check(cards().length === 3, `after enabling, ${cards().length} cards, want 3`);
  chip('wrong-song').onclick();
  check(cards().length === 1, `after disabling wrong-song, ${cards().length}, want 1`);
  chip('wrong-song').onclick();          // back to the starting selection
  chip('different-id').onclick();
  check(cards().length === 2, 'toggling back must restore the original list');

  const first = cards()[0];
  const btn = label => [...first.querySelectorAll('button')]
                         .find(b => b.textContent === label);

  // A reassignment with nothing chosen must be impossible to send.
  check(btn('Use the match').disabled, '"Use the match" must start disabled');
  check(/choose one/i.test(btn('Use the match').title || ''),
        'a disabled button must say what would enable it');
  btn('Use the match').onclick();
  check(posted.length === 0, 'an unarmed reassignment must not post');

  // Passage 22 has no candidates at all -- 23 of the 44 no-mbid cards on the
  // real library are like this, and no-mbid leads the queue, so it is the
  // first thing a reviewer meets. A control that can never work must not be
  // offered: greyed says "not yet", and the truth is "not ever, here".
  const noCand = cards().find(c => c.dataset.passage === '22');
  const noCandUse = [...noCand.querySelectorAll('button')]
                      .find(b => b.textContent === 'Use the match');
  check(noCandUse && noCandUse.hidden,
        '"Use the match" must be hidden when there is nothing to match to');
  check(/nothing to compare|nothing here to reassign/i.test(noCand.textContent),
        'a card with no candidates must say why, not just show an empty list');

  // Searching MusicBrainz directly finds something the fingerprint queue
  // could not `[SPEC-SUI-196]` -- on the same no-candidate card.
  const searchIn = card => card.querySelector('.search input');
  const searchGo = card => [...card.querySelectorAll('.search button')][0];
  searchIn(noCand).value = 'nothing';
  searchGo(noCand).onclick();
  await settle();
  check(/no results/i.test(noCand.querySelector('.search').textContent),
        'a search with nothing found must say so');
  check(noCandUse.hidden, 'zero results must not un-hide "Use the match"');

  searchIn(noCand).value = 'a real title';
  searchGo(noCand).onclick();
  await settle();
  check(!noCandUse.hidden, 'a found result must un-hide "Use the match"');
  check(!noCand.querySelector('li.none'),
        '"nothing to compare" must be removed once something was found');
  const found = noCand.querySelector('input[value="rec-searched"]');
  check(found, 'the searched result must be selectable exactly like a suggestion');
  if (found) {
    found.click();
    check(!noCandUse.disabled, 'choosing a searched result must arm the button the same way');
  }

  // Every mbid on a card is a real MusicBrainz link, not plain text
  // `[SPEC-SUI-195]`.
  const mbidLinks = [...window.document.querySelectorAll('a.mbid')];
  check(mbidLinks.length > 0, 'no mbid links were rendered at all');
  check(mbidLinks.every(a => a.href.startsWith('https://musicbrainz.org/') && a.target === '_blank'),
        'every mbid must link to its own MusicBrainz page in a new tab');

  // Auditioning goes through the ordinary queue verb rather than a new route.
  btn('Play now').onclick();
  check(posted[0] === '/queue/21/now', `audition should queue the passage, got ${posted[0]}`);

  // A real click, not a synthesised change event: dispatching `change` by
  // hand proves the handler works, which is not the same as proving a person
  // clicking the control reaches it.
  const radio = first.querySelector('input[type=radio]');
  radio.click();
  check(!btn('Use the match').disabled, 'choosing a candidate must arm the button');

  // Choosing a candidate offers the albums it appears on. Without an answer
  // the album is picked by release date, which is a guess.
  await settle();
  const sel = first.querySelector('.albums select');
  check(sel, 'choosing a candidate should offer which album to call it');
  if (sel) {
    check(sel.options.length === RELEASES.length + 1,
          `${sel && sel.options.length} album options, want ${RELEASES.length + 1} with "leave as is"`);
    check(sel.options[0].value === '', 'leaving the album alone must be an option');
    sel.value = 'rel-2';
  }

  btn('Use the match').onclick();
  await settle();
  check(posted[1] === '/review/21/reassigned?mbid=rec-real&release=rel-2',
        `reassignment must carry recording and album, got ${posted[1]}`);
  check(first.classList.contains('done'), 'a settled card should read as settled');

  // Undo. It appears only once a decision exists, and puts the card back.
  const undo = () => btn('Undo');
  check(undo() && !undo().hidden, 'a settled card must offer an undo');
  undo().onclick();
  await settle();
  check(posted[2] === '/review/21/reopen', `undo should reopen, got ${posted[2]}`);
  const reopened = cards().find(c => c.dataset.passage === '21');
  check(reopened && !reopened.classList.contains('done'),
        'after undo the card must be answerable again');

  // Fixing the artist credit `[SPEC-SUI-197]` -- independent of the recording
  // decision above, offered because passage 22's stored id is now a real
  // MusicBrainz id rather than a placeholder. Placed after every posted[N]
  // index check above: this itself posts, and would shift them. Re-fetched
  // fresh rather than reusing the `noCand` captured earlier: passage 21's
  // own undo just called `showCards()`, which rebuilds every card's DOM.
  const freshNoCand = cards().find(c => c.dataset.passage === '22');
  const fixPanel = freshNoCand.querySelector('.artistfix');
  check(fixPanel, 'a card with a real MusicBrainz id must offer the artist-fix panel');
  const fixSearchIn = fixPanel.querySelector('.search input');
  const fixSearchGo = fixPanel.querySelector('.search button');
  fixSearchIn.value = 'the real artist';
  fixSearchGo.onclick();
  await settle();
  const confirmBtn = [...fixPanel.querySelectorAll('button')]
                       .find(b => b.textContent.startsWith('Confirm'));
  check(confirmBtn, 'a found artist must offer a way to confirm it');
  if (confirmBtn) {
    confirmBtn.onclick();
    await settle();
    check(posted.some(u => u.startsWith('/review/22/artist/correct')),
          `expected an artist correction to post, got ${JSON.stringify(posted)}`);
    check(/corrected to/i.test(fixPanel.textContent),
          'a confirmed correction must read back as corrected');
    const undoBtn = [...fixPanel.querySelectorAll('button')]
                      .find(b => b.textContent.includes('Undo'));
    check(undoBtn, 'an unapplied correction must offer an undo');
    if (undoBtn) {
      undoBtn.onclick();
      await settle();
      check(posted.some(u => u === '/review/22/artist/reopen'),
            `expected the correction to be withdrawn, got ${JSON.stringify(posted)}`);
      check(!/corrected to/i.test(fixPanel.textContent),
            'after undo the panel must offer the search again, not the corrected state');
    }
  }

  // Decided cards from earlier sessions are off by default and reachable.
  check(!cards().some(c => c.dataset.passage === '25'),
        'a previously-decided card must not clutter the working queue');
  const decidedChip = [...window.document.querySelectorAll('.chip')]
                        .find(b => b.className.includes('decided'));
  check(decidedChip, 'no chip for decided');
  decidedChip.onclick();
  await settle();
  const settled = cards().find(c => c.dataset.passage === '25');
  check(settled, 'the decided chip must reveal earlier judgements');
  if (settled) {
    check(/applied/.test(settled.textContent),
          'a decision already written to the library must say so');
    // Undo must refuse it, and the reason has to be readable.
    const u = [...settled.querySelectorAll('button')].find(b => b.textContent === 'Undo');
    u.onclick();
    await settle();
    check(/--revert/.test(settled.textContent),
          `a refused undo must explain itself, got "${settled.textContent.slice(-80)}"`);
  }

  // A card with no candidates can still be kept -- that is a real answer.
  const second = cards()[1];
  const keep = [...second.querySelectorAll('button')].find(b => b.textContent === 'Keep ours');
  writeFails = true;
  keep.onclick();
  await settle();
  check(!second.classList.contains('done'),
        'a FAILED write must not leave the card looking decided');
  check(!keep.disabled, 'a failed write must re-arm the controls');

  console.log(`${'review'.padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `${cards().length} cards, posted=${JSON.stringify(posted)}`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

// ---------------------------------------------------------------------------
// A handoff lands on one passage, not the whole queue `[SPEC-SUI-150]`. Its
// own small fixture and its own JSDOM instance, since the query string has to
// be present before the page's own script ever runs.
async function runReviewHandoff() {
  const html = fs.readFileSync(path.join(ROOT, 'review.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/review?passage=22' });
  const { window } = dom;
  const errors = [];
  window.console.error = (...a) => errors.push(a.join(' '));
  const create = window.document.createElement.bind(window.document);
  window.document.createElement = tag => {
    const el = create(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };

  const QUEUE = {
    progress: { ran: true, checked: 10, confirmed: 8, contradicted: 2, decided: 0 },
    items: [
      { passage_id: 22, stored_mbid: 'rec-other', title: 'Another', artist: null,
        album: null, score: 0.93, severity: 'wrong-song', rank: 0, suggested: [] },
      { passage_id: 23, stored_mbid: 'rec-press', title: 'Why Worry',
        artist: 'Dire Straits', album: 'Brothers in Arms', score: 0.99,
        severity: 'different-id', rank: 3, suggested: [] },
    ],
  };
  // A passage the queue never carries at all -- never fingerprinted -- but
  // that the on-demand route `[SPEC-SUI-199]` still has a real card for.
  const ON_DEMAND = { passage_id: 30, stored_mbid: 'aaaaaaaa-0000-0000-0000-000000000030',
    checked_at: 'never', title: 'Opened By Hand', artist: 'Some Artist', album: null,
    score: null, suggested: [], severity: 'on-demand', rank: 6, decision: null,
    chosen_mbid: null, chosen_release_mbid: null, applied: false,
    artist_review: null, artist_review_applied: false };
  window.fetch = url => {
    if (url === '/skins') return Promise.resolve({ json: () => Promise.resolve([]) });
    if (url === '/review/queue')
      return Promise.resolve({ ok: true, json: () => Promise.resolve(QUEUE) });
    if (url === '/review/passage/30')
      return Promise.resolve({ ok: true, json: () => Promise.resolve(ON_DEMAND) });
    return Promise.resolve({ ok: false, status: 404 });
  };
  const runScript = src => {
    const el = create('script');
    el.textContent = src;
    window.document.body.appendChild(el);
  };
  runScript(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript(fs.readFileSync(path.join(ROOT, 'review.js'), 'utf8'));
  await new Promise(r => setTimeout(r, 30));

  const check = (cond, msg) => { if (!cond) errors.push(msg); };
  const cards = () => [...window.document.querySelectorAll('.card')];

  check(cards().length === 1, `a handoff should show exactly one card, got ${cards().length}`);
  check(cards()[0] && cards()[0].dataset.passage === '22',
        `the one card shown must be the named passage, got ${cards()[0] && cards()[0].dataset.passage}`);
  check(window.document.getElementById('filters').hidden,
        'the grade filters belong to the whole-queue view, not a handoff');
  check(/show the whole queue/i.test(window.document.getElementById('note').textContent),
        'a way back to the full queue must be offered');

  // A passage the queue does not carry -- confirmed, unchecked, or simply not
  // this library's numbering -- must explain itself, not render as empty.
  const dom2 = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/review?passage=999' });
  dom2.window.console.error = (...a) => errors.push('(999) ' + a.join(' '));
  const create2 = dom2.window.document.createElement.bind(dom2.window.document);
  dom2.window.document.createElement = tag => {
    const el = create2(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };
  dom2.window.fetch = window.fetch;
  const runScript2 = src => {
    const el = create2('script');
    el.textContent = src;
    dom2.window.document.body.appendChild(el);
  };
  runScript2(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript2(fs.readFileSync(path.join(ROOT, 'review.js'), 'utf8'));
  await new Promise(r => setTimeout(r, 30));
  check([...dom2.window.document.querySelectorAll('.card')].length === 0,
        'a passage absent from the queue must render no card');
  check(/999/.test(dom2.window.document.getElementById('note').textContent),
        'the explanation must name the passage that was not found');

  // A passage never in the queue at all, but reachable on demand
  // `[SPEC-SUI-199]` -- the case `profile.html`'s "Review in Lempi" button
  // could not actually serve before this route existed.
  const dom3 = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/review?passage=30' });
  dom3.window.console.error = (...a) => errors.push('(30) ' + a.join(' '));
  const create3 = dom3.window.document.createElement.bind(dom3.window.document);
  dom3.window.document.createElement = tag => {
    const el = create3(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };
  dom3.window.fetch = window.fetch;
  const runScript3 = src => {
    const el = create3('script');
    el.textContent = src;
    dom3.window.document.body.appendChild(el);
  };
  runScript3(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript3(fs.readFileSync(path.join(ROOT, 'review.js'), 'utf8'));
  await new Promise(r => setTimeout(r, 30));
  const onDemandCards = [...dom3.window.document.querySelectorAll('.card')];
  check(onDemandCards.length === 1,
        `an on-demand passage must render its own card, got ${onDemandCards.length}`);
  check(onDemandCards[0] && /opened by hand/i.test(onDemandCards[0].textContent),
        'an on-demand card must say it was opened by hand, not read as a real finding');
  check(onDemandCards[0] && onDemandCards[0].querySelector('.search input'),
        'the search-and-reassign box must be offered on an on-demand card too');

  console.log(`${'handoff'.padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `deep-linked to passage 22, an absent 999, and an on-demand 30`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

// ---------------------------------------------------------------------------
// `fade.js`'s ramp formulas against the same fixtures `fade.rs`'s own test
// checks Rust against `[SPEC021 §4]`, `[SPEC-SUI-226]` -- the one part of
// the editor's math this harness can verify without a real browser's Web
// Audio, since it is pure arithmetic and `fade.js` is written to run under
// plain Node too. One fixture file per curve, `gainIn`/`gainOut` now taking
// the curve name as their first argument.
function runFadeFixture() {
  const errors = [];
  let total = 0;
  delete require.cache[require.resolve(path.join(ROOT, 'fade.js'))];
  const { gainIn, gainOut } = require(path.join(ROOT, 'fade.js'));
  for (const curve of ['linear', 'cosine', 'exponential']) {
    const rows = JSON.parse(
      fs.readFileSync(path.join(__dirname, '..', 'fixtures', 'fade', `${curve}.json`), 'utf8'));
    total += rows.length;
    for (const { t, gain_in, gain_out } of rows) {
      const gotIn = gainIn(curve, t), gotOut = gainOut(curve, t);
      if (Math.abs(gotIn - gain_in) > 1e-5)
        errors.push(`gainIn(${curve}, ${t}): got ${gotIn}, want ${gain_in}`);
      if (Math.abs(gotOut - gain_out) > 1e-5)
        errors.push(`gainOut(${curve}, ${t}): got ${gotOut}, want ${gain_out}`);
    }
  }
  console.log(`${'fade'.padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `${total} points checked against the Rust-side fixtures`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

// The skins' side of the snapshot contract `[GDE-HST-060]`. Every skin check
// above renders SPARSE and RICH; if those two describe a field the server does
// not send, every skin passes against a player that does not exist. So the
// fields they carry must be exactly the fixture's `skins` list, which Rust's
// `the_snapshot_carries_every_field_its_readers_read` checks the server sends.
// `why` is a free-form object the server builds elsewhere, so only its
// presence is part of the contract, not its insides.
function runSnapshotFixture() {
  const errors = [];
  const want = JSON.parse(fs.readFileSync(
    path.join(__dirname, '..', 'fixtures', 'snapshot', 'consumers.json'), 'utf8')).skins || [];
  const OPAQUE = new Set(['why']);
  const got = new Set();
  const walk = (obj, prefix) => {
    for (const [k, v] of Object.entries(obj)) {
      const p = prefix + k;
      got.add(p);
      if (OPAQUE.has(p) || v === null || typeof v !== 'object') continue;
      if (Array.isArray(v)) {
        for (const e of v) if (e && typeof e === 'object') walk(e, p + '[].');
      } else {
        walk(v, p + '.');
      }
    }
  };
  walk(SPARSE, '');
  walk(RICH, '');
  if (!want.length) errors.push('the fixture names no fields for skins');
  for (const p of want) if (!got.has(p)) errors.push(`the fixture records "${p}", which no test snapshot carries`);
  for (const p of got) if (!want.includes(p)) errors.push(`the test snapshots carry "${p}", which the fixture does not record`);
  console.log(`${'snapshot'.padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `${got.size} fields in the test snapshots, ${want.length} in the fixture`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

// ---------------------------------------------------------------------------
// The waveform editor's read-only stage `[REQ-LIB-175]`, `[SPEC021]`. jsdom
// has no Web Audio, so decoding and drawing the waveform itself is out of
// reach here -- that seam is left to a person with a real browser. What this
// checks is the seam jsdom *can* see: the facts line renders exactly what
// `/edit/:id/info` reported (the "two views of one row" claim `[IMPL004]`
// makes), and a passage the server does not have explains itself instead of
// sitting blank.
async function runEdit() {
  const html = fs.readFileSync(path.join(ROOT, 'edit.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/edit/22' });
  const { window } = dom;
  const errors = [];
  window.console.error = (...a) => errors.push(a.join(' '));
  const create = window.document.createElement.bind(window.document);
  window.document.createElement = tag => {
    const el = create(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };

  const INFO = { passage_id: 22, start_ms: 1000, end_ms: 181000, file_ms: 200000,
                 lead_in_ms: 5, lead_out_ms: 946, gain_db: -1.2,
                 fade_in_ms: 20, fade_out_ms: 20,
                 fade_in_curve: 'exponential', fade_out_curve: 'exponential' };
  const posted = [];
  let audioUrl = null;
  window.fetch = (url, opts) => {
    if (opts && opts.method === 'POST') { posted.push(url); return Promise.resolve({ ok: true }); }
    if (url === '/skins') return Promise.resolve({ json: () => Promise.resolve([]) });
    if (url === '/edit/22/info')
      return Promise.resolve({ ok: true, json: () => Promise.resolve(INFO) });
    // A windowed request now, not the bare route -- `from_ms`/`to_ms`
    // query params around the passage, never the whole file [SPEC-SUI-224].
    if (url.startsWith('/edit/22/audio?')) {
      audioUrl = url;
      return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(new ArrayBuffer(8)) });
    }
    return Promise.resolve({ ok: false, status: 404 });
  };
  const runScript = src => {
    const el = create('script');
    el.textContent = src;
    window.document.body.appendChild(el);
  };
  runScript(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript(fs.readFileSync(path.join(ROOT, 'edit.js'), 'utf8'));
  await new Promise(r => setTimeout(r, 30));

  const check = (cond, msg) => { if (!cond) errors.push(msg); };
  const facts = window.document.getElementById('facts').textContent;
  check(facts.includes('5 ms'), `facts missing lead-in, got "${facts}"`);
  check(facts.includes('946 ms'), `facts missing lead-out, got "${facts}"`);
  check(facts.includes('-1.20 dB'), `facts missing gain, got "${facts}"`);

  // A windowed request, padded around the passage `[SPEC-SUI-224]` -- not
  // the bare file. `start_ms=1000` padded 60s under clamps to 0; `end_ms=
  // 181000` padded 60s over would exceed `file_ms=200000`, so it clamps
  // there instead.
  check(audioUrl === '/edit/22/audio?from_ms=0&to_ms=200000',
        `expected a padded, clamped window, got ${audioUrl}`);

  // The precise ms fields `[SPEC-SUI-219]` populate from the same `/info`
  // response the facts line reads, and are usable before the audio itself
  // ever decodes -- neither depends on Web Audio, unlike the canvas below.
  const $ = id => window.document.getElementById(id);
  check($('startms').value === '1000', `start field should read 1000, got ${$('startms').value}`);
  check($('endms').value === '181000', `end field should read 181000, got ${$('endms').value}`);
  check($('leadinms').value === '5', `lead-in field should read 5, got ${$('leadinms').value}`);
  check($('leadoutms').value === '946', `lead-out field should read 946, got ${$('leadoutms').value}`);
  check(!$('leadoutms').disabled, 'the precise fields must be usable once /info has loaded');

  // Fade-in/fade-out `[SPEC-SUI-226]`: independent of lead, their own
  // precise fields and curve pickers, populated from the same `/info`.
  check($('fadeinms').value === '20', `fade-in field should read 20, got ${$('fadeinms').value}`);
  check($('fadeoutms').value === '20', `fade-out field should read 20, got ${$('fadeoutms').value}`);
  check($('fadeincurve').value === 'exponential',
        `fade-in curve should read exponential, got ${$('fadeincurve').value}`);
  check($('fadeoutcurve').value === 'exponential',
        `fade-out curve should read exponential, got ${$('fadeoutcurve').value}`);
  check(!$('fadeinms').disabled, 'the fade fields must be usable once /info has loaded');
  check(!$('fadeincurve').disabled, 'the fade curve pickers must be usable once /info has loaded');

  $('fadeoutms').value = '500';
  $('fadeoutms').dispatchEvent(new window.Event('change'));
  check($('facts').textContent.includes('500 ms'), 'a fade-out edit must reach the facts line');
  check(!$('undo').disabled, 'a completed fade edit must arm undo');
  $('undo').dispatchEvent(new window.Event('click'));
  check($('fadeoutms').value === '20', 'undo must restore the fade-out field along with the draft');

  $('fadeincurve').value = 'linear';
  $('fadeincurve').dispatchEvent(new window.Event('change'));
  check($('facts').textContent.includes('linear'), 'a fade-in curve edit must reach the facts line');
  $('undo').dispatchEvent(new window.Event('click'));
  check($('fadeincurve').value === 'exponential', 'undo must restore the fade-in curve too');

  // Undo `[SPEC-SUI-220]`: a completed field edit is undoable, and the undo
  // button reflects whether there is anything to undo.
  check($('undo').disabled, 'undo must start with nothing to undo');
  $('leadoutms').value = '2000';
  $('leadoutms').dispatchEvent(new window.Event('change'));
  check(!$('undo').disabled, 'a completed edit must arm undo');
  check($('facts').textContent.includes('2000 ms'), 'the edit must reach the facts line');
  $('undo').dispatchEvent(new window.Event('click'));
  check($('facts').textContent.includes('946 ms'), 'undo must restore the prior value');
  check($('leadoutms').value === '946', 'undo must restore the field along with the draft');
  check($('undo').disabled, 'undo must disable itself once the stack is empty again');

  // The flavor-refresh suggestion: quiet for a small move, present for a
  // substantial one, gone again once undone -- compared against the
  // boundary as loaded, not the last save.
  // `startms`, not `endms`: the end field clamps against `totalMs()`, which
  // is 0 with no decoded buffer here (jsdom has no Web Audio) and would
  // clamp any value down to almost nothing -- the start field's own clamp
  // does not depend on it.
  check($('flavornote').textContent === '', 'no suggestion before any edit');
  $('startms').value = '1400';   // 400ms off the loaded 1000 -- small
  $('startms').dispatchEvent(new window.Event('change'));
  check($('flavornote').textContent === '', 'a small move must not suggest a re-analysis');
  $('startms').value = '12000';  // 11000ms off the loaded 1000 -- substantial
  $('startms').dispatchEvent(new window.Event('change'));
  check(/re-analyz/i.test($('flavornote').textContent),
        `a substantial move must suggest one, got "${$('flavornote').textContent}"`);
  $('undo').dispatchEvent(new window.Event('click'));
  $('undo').dispatchEvent(new window.Event('click'));
  check($('flavornote').textContent === '',
        'undoing back to the loaded boundary must clear the suggestion');

  // jsdom has no `AudioContext`; decoding fails there the same way it would
  // in a browser without Web Audio, and that must read as an explanation, not
  // a crash the page swallows silently.
  const note = window.document.getElementById('note').textContent;
  check(note.length > 0, 'a page that cannot decode audio must say so, not sit blank');

  // The main transport is silenced on entry regardless of whether the audio
  // itself ever decodes `[SPEC-SUI-217]` -- it happens before the audio
  // fetch, not after.
  check(posted.includes('/command/pause'), `expected a pause command, got ${JSON.stringify(posted)}`);
  check(/paused/i.test($('pausenote').textContent), 'the page must say the main player was paused');

  // A second instance for the passage the server does not have -- its own
  // fixture, since the query differs before the page's own script ever runs.
  const dom2 = new JSDOM(fs.readFileSync(path.join(ROOT, 'edit.html'), 'utf8'),
    { runScripts: 'dangerously', url: 'http://localhost/edit/999' });
  dom2.window.console.error = (...a) => errors.push('(999) ' + a.join(' '));
  const create2 = dom2.window.document.createElement.bind(dom2.window.document);
  dom2.window.document.createElement = tag => {
    const el = create2(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };
  dom2.window.fetch = url => {
    if (url === '/skins') return Promise.resolve({ json: () => Promise.resolve([]) });
    return Promise.resolve({ ok: false, status: 404 });
  };
  const runScript2 = src => {
    const el = create2('script');
    el.textContent = src;
    dom2.window.document.body.appendChild(el);
  };
  runScript2(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript2(fs.readFileSync(path.join(ROOT, 'edit.js'), 'utf8'));
  await new Promise(r => setTimeout(r, 30));
  const note2 = dom2.window.document.getElementById('note').textContent;
  check(/999/.test(note2), `an unknown passage must name itself, got "${note2}"`);

  console.log(`${'edit'.padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `facts line matches /info for passage 22, and 999 explains itself`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

// ---------------------------------------------------------------------------
// The passage-info page [REQ-VIS-270] -- the appliance-side sibling of
// Vipunen's profile page, reached from a browse row's own info link. Unlike
// `runEdit`, this one page has no seam jsdom cannot see: no Web Audio, no
// canvas, nothing decoded -- a plain fetch and a plain render, checkable
// end to end.
async function runPassage() {
  const html = fs.readFileSync(path.join(ROOT, 'passage.html'), 'utf8');
  const dom = new JSDOM(html, { runScripts: 'dangerously', url: 'http://localhost/passage/22' });
  const { window } = dom;
  const errors = [];
  window.console.error = (...a) => errors.push(a.join(' '));
  const create = window.document.createElement.bind(window.document);
  window.document.createElement = tag => {
    const el = create(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };

  const INFO = {
    passage_id: 22, path: '/m/song.mp3', format: 'mp3', duration_ms: 240000,
    audio_md5: 'deadbeef', kind: 'radio', start_ms: 1000, end_ms: 200000,
    lead_in_ms: 250, lead_out_ms: 1800, gain_db: -1.2,
    fade_in_ms: 20, fade_out_ms: 20, fade_in_curve: 'exponential', fade_out_curve: 'exponential',
    boundary_src: 'inherited:mulib', tag_title: 'Fallback Title', tag_artist: null, tag_album: null,
    recordings: [{
      mbid: '68684e6b-37d2-487e-8ee2-d21e28fa1589', weight: 1.0, source: 'inherited:mulib',
      title: 'A Passage With Reasons', artists: [{ name: 'A Band', weight: 1.0 }],
    }],
    sibling: { passage_id: 23, kind: 'album' },
  };
  window.fetch = url => {
    if (url === '/skins') return Promise.resolve({ json: () => Promise.resolve([]) });
    if (url === '/passage/22/info') return Promise.resolve({ ok: true, json: () => Promise.resolve(INFO) });
    return Promise.resolve({ ok: false, status: 404 });
  };
  const runScript = src => {
    const el = create('script');
    el.textContent = src;
    window.document.body.appendChild(el);
  };
  runScript(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript(fs.readFileSync(path.join(ROOT, 'passage.js'), 'utf8'));
  await new Promise(r => setTimeout(r, 30));

  const $ = id => window.document.getElementById(id);
  const check = (cond, msg) => { if (!cond) errors.push(msg); };

  check($('title').textContent === 'A Passage With Reasons',
        `title should prefer the recording's own, got "${$('title').textContent}"`);
  check($('subtitle').textContent.includes('A Band'), `subtitle should name the artist, got "${$('subtitle').textContent}"`);
  check(!$('sibling').hidden && $('sibling').textContent.includes('album'),
        `the sibling cut must be offered, got "${$('sibling').textContent}"`);
  check($('sibling').querySelector('a').getAttribute('href') === '/passage/23',
        'the sibling link must point at its own passage id');
  const spanText = $('span').textContent;
  check(spanText.includes('radio'), 'facts must show the passage kind');
  check(spanText.includes('250') && spanText.includes('1800'), 'facts must show lead-in/out');
  check(spanText.includes('20 ms exponential'), 'facts must show fade-in/out');
  // Directly under lead in/out, before gain -- matching Vipunen's own profile
  // page row order [REQ-VIS-270], so the two read side by side comparable.
  check(spanText.indexOf('lead in') < spanText.indexOf('fade in')
        && spanText.indexOf('fade in') < spanText.indexOf('gain'),
        `fade in/out must sit directly under lead in/out, before gain, got:\n${spanText}`);
  check(/see the selection reasoning/.test(spanText), 'a link to /why must be offered');
  check($('span').querySelector('a').getAttribute('href') === '/why/22',
        'the why-link must name this passage');
  check($('file').textContent.includes('/m/song.mp3'), 'file facts must show the path');
  const cards = window.document.querySelectorAll('#recordings .card');
  check(cards.length === 1, `expected 1 recording card, got ${cards.length}`);
  check(cards[0] && cards[0].textContent.includes('A Band'),
        'the recording card must show its credited artist');
  // A real MusicBrainz id links out; this project's own local placeholders
  // (`local:audio:...`) must not, since nothing there would resolve.
  check(!!cards[0].querySelector('a[href*="musicbrainz.org"]'),
        'a real mbid must link to musicbrainz.org');

  // A second instance for the passage the server does not have -- its own
  // fixture, since the query differs before the page's own script ever runs.
  const dom2 = new JSDOM(fs.readFileSync(path.join(ROOT, 'passage.html'), 'utf8'),
    { runScripts: 'dangerously', url: 'http://localhost/passage/999' });
  dom2.window.console.error = (...a) => errors.push('(999) ' + a.join(' '));
  const create2 = dom2.window.document.createElement.bind(dom2.window.document);
  dom2.window.document.createElement = tag => {
    const el = create2(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };
  dom2.window.fetch = url => {
    if (url === '/skins') return Promise.resolve({ json: () => Promise.resolve([]) });
    return Promise.resolve({ ok: false, status: 404 });
  };
  const runScript2 = src => {
    const el = create2('script');
    el.textContent = src;
    dom2.window.document.body.appendChild(el);
  };
  runScript2(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript2(fs.readFileSync(path.join(ROOT, 'passage.js'), 'utf8'));
  await new Promise(r => setTimeout(r, 30));
  const note2 = dom2.window.document.getElementById('note').textContent;
  check(/not found/i.test(note2), `an unknown passage must say so, got "${note2}"`);

  console.log(`${'passage'.padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `facts and recording card match /info for passage 22, and 999 explains itself`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

// The preference panel `[REQ-VIS-290]`, `[SPEC-PREF-080]`, `[SPEC-PREF-090]`
// -- built in core.js and shared by every skin that carries the slot, so it is
// checked once against each skin rather than being a fourth thing `run()` has
// to know about. What is under test is what `cargo test` structurally cannot
// reach: that the server's own JSON becomes the right controls, and that Save
// turns those controls back into the three-way query `preference.rs` parses.
const PREFERENCE = {
  rotation: null, recovery: null, restraint: null,
  defaults: { rotation: 2.0, recovery: 2.6, restraint: 0.0 },
  subject: { title: 'Fairytale of New York', artist: 'The Pogues',
             album: 'If I Should Fall from Grace with God' },
  specials: [
    // Tagged by the MuLibPlay migration, never touched by this listener.
    { characteristic: 'user.christmas', class: 'christmasy', label: 'Christmas',
      inherited: 1.0, value: null },
    // Inherited nothing, set by hand. That is profanity's real shape today
    // only because `migrate_mulib.py` drops MuLibPlay's 69 values as a dead
    // field `[SPEC-PREF-082]`; what is under test is a special with no
    // inherited value, whichever special happens to be in that state.
    { characteristic: 'user.profanity', class: 'profane', label: 'Profanity',
      inherited: null, value: 0.5 },
  ],
};

async function runPreference(skin) {
  const dir = path.join(ROOT, 'skins', skin);
  const shell = fs.readFileSync(path.join(ROOT, 'shell.html'), 'utf8')
    .replace('<script>Lempi.start();</script>', '');
  const dom = new JSDOM(shell,
                        { runScripts: 'dangerously', url: 'http://localhost/?skin=' + skin });
  const { window } = dom;
  const errors = [];
  window.console.error = (...a) => errors.push(a.join(' '));
  const check = (cond, msg) => { if (!cond) errors.push(msg); };

  const create = window.document.createElement.bind(window.document);
  window.document.createElement = tag => {
    const el = create(tag);
    if (tag === 'link' || tag === 'script') setTimeout(() => el.onload && el.onload(), 0);
    return el;
  };

  const posted = [];
  window.fetch = (url, opts) => {
    if (opts && opts.method === 'POST') { posted.push(url); return Promise.resolve({ ok: true }); }
    if (url === '/skins') {
      return Promise.resolve({ json: () => Promise.resolve(skins.map(n => ({ name: n, label: n }))) });
    }
    const m = /^\/skin\/([^/]+)\/(.+)$/.exec(url);
    if (m) return Promise.resolve({ text: () => Promise.resolve(fs.readFileSync(path.join(ROOT, 'skins', m[1], m[2]), 'utf8')) });
    if (/^\/preference\//.test(url)) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(PREFERENCE) });
    }
    // Its own independent fetch, deliberately not awaited by the panel
    // `[REQ-VIS-300]` -- answered here so the panel's real path runs.
    if (/^\/play-frequency\//.test(url)) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(
        [{ label: 'All', counts: [0, 1, 2, 3, 4] }]) });
    }
    if (/^\/art\//.test(url)) return Promise.reject(new Error('no art in a test DOM'));
    return Promise.reject(new Error('unexpected fetch ' + url));
  };

  let sock = null;
  window.WebSocket = function () { sock = this; this.close = () => {}; };
  const runScript = src => {
    const el = create('script');
    el.textContent = src;
    window.document.body.appendChild(el);
  };
  runScript(fs.readFileSync(path.join(ROOT, 'core.js'), 'utf8'));
  runScript('Lempi.start();');
  for (let i = 0; i < 200 && !sock; i++) await new Promise(r => setTimeout(r, 5));
  if (!sock) { console.log(`${('pref/' + skin).padEnd(11)} FAIL  core never loaded the skin`); failures++; return; }
  try {
    runScript(fs.readFileSync(path.join(dir, 'skin.js'), 'utf8'));
  } catch (e) {
    console.log(`${('pref/' + skin).padEnd(11)} FAIL  skin.js threw: ${e.message}`);
    failures++;
    return;
  }
  sock.onopen && sock.onopen();
  sock.onmessage({ data: JSON.stringify(RICH) });

  const panel = window.document.getElementById('pref-panel');
  // WinAmp carries no slot, and that is designed rather than missed
  // `[SPEC-PREF-070]`: its marquee concatenates artist and title into one
  // scrolling string with no separate node to hang a link on. Opening the
  // panel there must be a silent no-op, not a crash.
  if (!panel) {
    try {
      runScript("Lempi.editPreference('recording', 'rec-1', 'Whatever');");
      await new Promise(r => setTimeout(r, 30));
    } catch (e) {
      errors.push(`a skin with no panel slot must not throw: ${e.message}`);
    }
    console.log(`${('pref/' + skin).padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
                `no panel slot, and opening one is a silent no-op`);
    for (const e of errors) console.log('    ! ' + e);
    if (errors.length) failures++;
    return;
  }

  // Through a <script>, not `window.Lempi`: core.js's own top-level `const`
  // lives in the shared script scope, which is exactly why this harness
  // already starts core with `runScript('Lempi.start();')` above.
  runScript("Lempi.editPreference('recording', 'rec-1', 'A Fallback Label');");
  await new Promise(r => setTimeout(r, 30));

  check(!panel.hidden, 'the panel must be open');
  const heading = panel.querySelector('.pref-heading').textContent;
  check(/Fairytale of New York/.test(heading),
        `the heading must name the recording the server named, got "${heading}"`);
  check(!/A Fallback Label/.test(heading),
        "the library's own title must replace the caller's label once it arrives");
  const subject = panel.querySelector('.pref-subject');
  check(!subject.hidden, 'the artist/album line must be shown when both are known');
  check(/by The Pogues/.test(subject.textContent) &&
        /from If I Should Fall from Grace with God/.test(subject.textContent),
        `the artist and album must both appear, got "${subject.textContent}"`);

  const specials = panel.querySelector('.pref-specials');
  check(!specials.hidden, 'the specials section must be shown when any are registered');
  const rows = [...specials.querySelectorAll('.pref-field')];
  check(rows.length === 2, `two specials were served, ${rows.length} rendered`);
  const labels = rows.map(r => r.querySelector('label').textContent);
  check(labels.join(',') === 'Christmas,Profanity',
        `the specials must be named as the server named them, got "${labels.join(',')}"`);
  // The whole point of two values rather than one: an inherited value must
  // not read as a choice this listener made.
  const christmasReadout = rows[0].querySelector('.pref-readout').textContent;
  check(christmasReadout === '100% (inherited)',
        `an untouched special must show the inherited value as inherited, got "${christmasReadout}"`);
  const profanityReadout = rows[1].querySelector('.pref-readout').textContent;
  check(profanityReadout === '50%',
        `a hand-set special must show as chosen, not inherited, got "${profanityReadout}"`);

  // Drag Christmas down, and Reset a special that was set -- the two edits that
  // must reach the server, beside the three fields left alone, which must not.
  const slider = rows[0].querySelector('input');
  slider.value = '0.25';
  slider.dispatchEvent(new window.Event('input'));
  check(rows[0].querySelector('.pref-readout').textContent === '25%',
        'dragging a special must stop calling it inherited');
  rows[1].querySelector('.pref-reset').click();
  check(rows[1].querySelector('.pref-readout').textContent === '0% (inherited)',
        'Reset must fall back to the inherited value, which here is nothing');

  panel.querySelector('.pref-save').click();
  await new Promise(r => setTimeout(r, 30));
  const save = posted.find(u => /^\/preference\//.test(u));
  check(save, 'Save must post to /preference');
  if (save) {
    const q = new window.URLSearchParams(save.split('?')[1] ?? '');
    check(q.get('special:user.christmas:christmasy') === '0.25',
          `the dragged special must be sent as a number, got "${q.get('special:user.christmas:christmasy')}"`);
    check(q.get('special:user.profanity:profane') === '',
          'a Reset special must be sent empty, which is what the server reads as "use the inherited value"');
    check(!q.has('rotation') && !q.has('recovery') && !q.has('restraint'),
          'a field nobody touched must be absent, not resent');
  }

  console.log(`${('pref/' + skin).padEnd(11)} ${errors.length ? 'FAIL' : 'OK  '}  ` +
              `heading="${heading.slice(-34)}"  specials=${rows.length}  ` +
              `saved=${save ? save.split('?')[1] : 'nothing'}`);
  for (const e of errors) console.log('    ! ' + e);
  if (errors.length) failures++;
}

(async () => {
  for (const s of skins) await run(s);
  for (const s of skins) await runPreference(s);
  await runBrowse();
  await runReview();
  await runReviewHandoff();
  runFadeFixture();
  runSnapshotFixture();
  await runEdit();
  await runPassage();
  console.log(failures ? `\n${failures} skin(s) failed` : '\nall skins rendered without error');
  process.exit(failures ? 1 : 0);
})();
