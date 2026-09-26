// The player, without a single opinion about how it looks [REQ-VIS-160].
//
// Everything a skin needs and nothing it should have to reinvent: the socket,
// the snapshot, the commands, and the two pieces of arithmetic that are
// specified rather than decorative. A skin supplies markup, a stylesheet, and a
// render function; it never opens a socket, never builds a URL, and never
// carries a copy of the fader curve.
//
// The split is not new -- it is the one the server already draws. `/ws` pushes
// complete snapshots and the POST endpoints take commands; that contract is the
// real interface, and the DOM was only ever one rendering of it.
const Lempi = (() => {
  const listeners = [];
  let last = null;

  // ---- commands ----------------------------------------------------------
  // Every one is fire-and-forget. The truth comes back in the next snapshot,
  // so nothing here updates the display optimistically and then has to explain
  // itself when the engine disagrees.
  const post = path => fetch(path, { method: 'POST' });

  // ---- the fader ---------------------------------------------------------
  // Quadratic in travel, flat where it meets full scale [REQ-AUD-156]. This
  // lives here, not in a skin: it is a specified control law, and three skins
  // each carrying their own copy is three chances to disagree with the engine.
  //
  // The floor comes from the engine, so the number exists in exactly one place.
  let faderFloor = -72;
  const fader = {
    // Travel (0 at the left, 1 at the right) to dB.
    db: x => faderFloor * (1 - x) ** 2,
    // dB back to travel, for putting the knob where the engine says it is.
    travel: db => 1 - Math.sqrt(db / faderFloor),
    get floor() { return faderFloor; },
  };

  // ---- formatting --------------------------------------------------------
  const fmt = {
    clock(ms) {
      const t = Math.max(0, Math.round(ms / 1000));
      return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
    },
    // Displayed to a tenth of a dB, and SENT as the displayed figure, so the
    // caption cannot differ from the level in force.
    round1: v => Math.round(v * 10) / 10,
    db: db => `${db <= -0.05 ? '−' + Math.abs(db).toFixed(1) : '0.0'} dB`,
    // "12 plays", and when. Play counts are per RECORDING, so the phrasing
    // must not imply this file: the same recording reached through two files
    // is the same thing heard twice.
    plays(n, at) {
      if (!n) return 'never played';
      const times = n === 1 ? 'once' : `${n} times`;
      return at ? `played ${times}, last ${new Date(at * 1000).toLocaleDateString()}`
                : `played ${times}`;
    },
    // "1.5 s of overlap" / "0.5 s of silence between" / "back to back".
    // A lead longer than the fade is legal and leaves a gap; say so plainly
    // rather than letting silence come as a surprise.
    overlap(k) {
      const s = (k.fade_ms - k.lead_ms) / 1000;
      return s > 0 ? `${s.toFixed(1)} s of overlap`
           : s < 0 ? `${(-s).toFixed(1)} s of silence between`
           : 'back to back';
    },
  };

  // ---- provenance --------------------------------------------------------
  // Where a displayed name came from `[REQ-VIS-120]`.
  //
  // "The MusicBrainz Recording title" and "whatever this file's ID3 tag says"
  // are different claims, and nothing in the name itself betrays which one you
  // are reading: both arrive as ordinary text, and one of them is a stem of a
  // filename with the underscores taken out. This library is a migration whose
  // every recording id came from one source, so the difference is not academic.
  //
  // Visible rather than on hover, which is where it used to be: a tooltip is
  // no use on the phone this interface is mostly read from, and a claim you
  // have to go looking for is one nobody checks.
  const SOURCE = {
    musicbrainz: { mark: 'MB', label: 'MusicBrainz' },
    tags: { mark: 'tag', label: 'the file tags' },
    filename: { mark: 'file', label: 'the filename' },
    unknown: { mark: '?', label: 'nowhere' },
  };

  function badge(source, what) {
    const s = SOURCE[source] || SOURCE.unknown;
    const b = document.createElement('span');
    b.className = 'src';
    b.dataset.src = source || 'unknown';
    b.textContent = s.mark;
    // The long form stays on hover for anyone who wants the sentence.
    b.title = `${what} from ${s.label}`;
    return b;
  }

  // Put `text` in `el` and hang its provenance off the end. No badge on an
  // empty name: there is no claim to qualify, and "unknown from nowhere" is
  // noise rather than information.
  function named(el, text, source, what) {
    if (!el) return;
    el.textContent = text ?? '';
    if (text) el.appendChild(badge(source, what));
  }

  // ---- cover art ---------------------------------------------------------
  // A URL and the load/error dance around it. The URL because core owns every
  // route; the dance because all three skins would otherwise carry the same
  // eight lines, and art is missing often enough -- roughly a third of this
  // library -- that getting the failure case wrong would be conspicuous.
  //
  // Nothing is asked of the server until a skin asks: a 404 is the normal
  // answer for a file with no embedded picture, not an error to report.
  // The back of the sleeve, for skins that show it. Separate route, same
  // dance: a passage with no back cover is the normal case, not an error, so
  // the element hides on 404 exactly as the front does.
  // The kantele `[REQ-VIS-128]`, shown where a passage has no cover.
  //
  // The kantele is the instrument of the Kalevala, the same source the
  // project already borrows from in Vipunen. Drawn from the instrument
  // rather than from a stock illustration of one, which is why the strings
  // terminate ON the varras -- the bar at the NARROW end that they are knotted
  // around -- and run to tuning pins at the wide end. A traditional five-string
  // kantele has no sound hole; the body is hollowed from beneath.
  //
  // Inline markup rather than a data URI, because a data URI is an isolated
  // document and cannot see `currentColor`. Inlined, one mark takes each skin's
  // own text colour: gold in MuLibPlay, LCD green in WinAmp, dim grey in Lempi.
  const KANTELE =
    '<svg viewBox="0 0 96 96" fill="none" stroke="currentColor" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
    '<path d="M16 54 C31 48 48 40 79 32 L79 50 C53 54 33 58 16 54 Z" stroke-width="1.3" opacity=".34"/>' +
    '<path d="M22.1 51.3 C40 46.0 58 39.8 75 35.5" stroke-width=".95" opacity=".6"/>' +
    '<path d="M22.2 52.3 C40 47.5 58 41.8 75 38.5" stroke-width=".95" opacity=".55"/>' +
    '<path d="M22.3 53.3 C40 49.0 58 44.3 75 41.5" stroke-width=".95" opacity=".5"/>' +
    '<path d="M22.4 54.3 C40 50.6 58 46.8 75 44.5" stroke-width=".95" opacity=".45"/>' +
    '<path d="M22.5 55.3 C40 52.2 58 49.3 75 47.5" stroke-width=".95" opacity=".4"/>' +
    '<path d="M21.9 50.1 L22.6 56.5" stroke-width="1.6" opacity=".55"/>' +
    '<g fill="currentColor" stroke="none" opacity=".5">' +
    '<circle cx="76.4" cy="35.5" r="1.15"/><circle cx="76.4" cy="38.5" r="1.15"/>' +
    '<circle cx="76.4" cy="41.5" r="1.15"/><circle cx="76.4" cy="44.5" r="1.15"/>' +
    '<circle cx="76.4" cy="47.5" r="1.15"/></g></svg>';

  // Structure, not decoration, so it belongs to core rather than to each skin:
  // two stacked layers is the only way to fade BETWEEN two covers, since
  // swapping one element's `src` is instantaneous. Skins keep the sizing and
  // the object-fit; they do not have to know how the crossfade is built.
  const ART_CSS =
    '.artbox { position: relative; overflow: hidden; }' +
    '.artbox > img, .artbox > .ph { position: absolute; inset: 0;' +
    '  width: 100%; height: 100%; }' +
    '.artbox > img { opacity: 0; transition: opacity 1s ease; }' +
    '.artbox > img.on { opacity: 1; }' +
    '.artbox > .ph { display: grid; place-items: center;' +
    '  transition: opacity 1s ease; }' +
    '.artbox.has-art > .ph { opacity: 0; }' +
    '.artbox > .ph svg { width: 78%; height: 78%; }' +
    '@media (prefers-reduced-motion: reduce) {' +
    '  .artbox > img, .artbox > .ph { transition: none; } }';

  function artStyle() {
    if (document.getElementById('lempi-art-css')) return;
    const st = document.createElement('style');
    st.id = 'lempi-art-css';
    st.textContent = ART_CSS;
    document.head.appendChild(st);
  }

  // Build the two layers and the mark beneath them, once per box.
  function artBox(box) {
    if (box.dataset.art) return;
    artStyle();
    box.dataset.art = '1';
    box.classList.add('artbox');
    box.textContent = '';
    const ph = document.createElement('span');
    ph.className = 'ph';
    ph.innerHTML = KANTELE;
    const a = document.createElement('img');
    const b = document.createElement('img');
    a.alt = b.alt = '';
    box.append(ph, a, b);
  }

  function showBackArt(box, passageId) {
    return showArt(box, passageId, true);
  }

  // The words for `passageId`, fetched once per passage `[SPEC-LYR-040]`.
  //
  // Fetched rather than pushed: the snapshot goes out on every tick to every
  // skin, and a song's words change once a song. `lastLyrics` is what stops one
  // request per tick — the passage id is the whole cache key, because two
  // passages of one recording share their words anyway.
  //
  // A 404 is the ordinary answer for most of the library and empties the box
  // rather than showing an error: a panel saying "not found" for three tracks
  // out of four is worse than a panel that is simply not there.
  let lastLyrics = null;
  function showLyrics(box, passageId) {
    if (!box) return;
    if (passageId == null) { box.textContent = ''; box.hidden = true; lastLyrics = null; return; }
    if (passageId === lastLyrics) return;
    lastLyrics = passageId;
    fetch(`/lyrics/${passageId}`)
      .then(r => (r.ok ? r.text() : null))
      .then(t => {
        if (passageId !== lastLyrics) return;   // a later track won the race
        box.textContent = t || '';
        box.hidden = !t;
      })
      .catch(() => { box.textContent = ''; box.hidden = true; });
  }

  // Fade from whatever is showing to the cover for `passageId`.
  //
  // The box never empties: the mark sits under both layers, so the element
  // keeps its size whatever happens. That is what stopped the controls beneath
  // it jumping up and down as tracks changed -- the old version toggled
  // `hidden`, which is `display: none`, so the art left the layout entirely
  // while the next one loaded and the page reflowed twice per track
  // `[REQ-VIS-127]`.
  function showArt(box, passageId, back) {
    if (!box) return;
    artBox(box);
    const want = passageId == null ? ''
               : back ? `/art/${passageId}/back` : `/art/${passageId}`;
    if (box.dataset.for === want) return;   // already showing it
    box.dataset.for = want;

    const imgs = box.querySelectorAll('img');
    const clear = () => imgs.forEach(i => i.classList.remove('on'));
    if (!want) {                            // nothing playing: back to the mark
      clear();
      box.classList.remove('has-art');
      return;
    }
    const live = box.querySelector('img.on');
    const idle = live === imgs[0] ? imgs[1] : imgs[0];
    // Swap only once the incoming cover has decoded. Fading toward an image
    // that has not arrived shows an empty box for the length of the fade,
    // which is the artefact this exists to remove.
    idle.onload = () => {
      if (box.dataset.for !== want) return; // superseded while loading
      clear();
      idle.classList.add('on');
      box.classList.add('has-art');
    };
    // A 404 is the ordinary answer for a passage with no embedded picture --
    // roughly a third of this library -- so it fades to the mark rather than
    // being reported.
    idle.onerror = () => {
      if (box.dataset.for !== want) return;
      clear();
      box.classList.remove('has-art');
    };
    idle.src = want;
  }

  // The edit controls for one queued passage, wired and ready to append.
  //
  // Here rather than in each skin because all three want the same three
  // verbs on the same object, and three copies would drift. A skin styles
  // them through `.qedit` and decides where they go; it does not decide
  // what they do.
  // Takes the ENTRY's id, not the passage's `[REQ-VIS-186]`. A passage may be
  // queued twice; those are two entries naming one recording, and addressing
  // them by passage meant removing one removed both and moving one moved
  // whichever happened to come first.
  // `ends` marks a row that is already first or last. The engine clamps a
  // shift at both ends and answers 204 either way `[REQ-VIS-185]`, so the
  // button that cannot move anything looked exactly like one that could and
  // was indistinguishable from a control that had failed. Disabled instead.
  function queueControls(qid, editable = true, ends = {}) {
      const box = document.createElement('span');
      box.className = 'qedit';
      delegateQueueEdits();
      // Remove first and furthest left, then sooner, then later. Fixed
      // order and fixed widths so the controls line up as columns down the
      // queue: a column of identical buttons is one target to learn, where
      // buttons that shift with the length of a title are three.
      for (const [label, action, title] of Lempi.VERBS.edit) {
          const b = document.createElement('button');
          b.type = 'button';
          b.textContent = label;
          b.title = title;
          // What the delegated listener reads, since it has no closure to
          // read from. The entry, not the passage `[REQ-VIS-186]`.
          b.dataset.qid = qid;
          b.dataset.action = action;
          if (editable === false) {
              // Already in the mixer, so its audio is partly in the ring:
              // the control would report success and change nothing.
              b.disabled = true;
              b.title = 'already playing into the buffer';
          } else if (action === 'sooner' && ends.first) {
              b.disabled = true;
              b.title = 'already first in the queue';
          } else if (action === 'later' && ends.last) {
              b.disabled = true;
              b.title = 'already last in the queue';
          }
          box.appendChild(b);
      }
      return box;
  }

  // One listener, on the document, in the capture phase -- not one per button.
  //
  // A handler bound to a button dies with the button, and these buttons are
  // rebuilt whenever the queue changes underneath them. Worse, a press whose
  // mousedown and mouseup land either side of a rebuild never becomes a click
  // at all: the browser dispatches one only when both resolve to the same live
  // element, so the press registered visually and then did nothing. That is
  // what made these controls feel intermittent. A listener on a node that is
  // never replaced cannot be lost this way.
  //
  // Capture rather than bubble because a skin may also have a click handler on
  // the row -- Lempi picks the passage -- and capture runs from the document
  // down, before the row's own handler, which is what lets a control's click be
  // stopped from counting as a pick as well.
  let editsDelegated = false;
  function delegateQueueEdits() {
    if (editsDelegated) return;
    editsDelegated = true;
    // The receipt goes up when the pointer lands, not when the round trip
    // finishes: it is the half a listener needs to see at once, and the half
    // that must not wait on anything to appear.
    document.addEventListener('pointerdown', e => {
      const b = editButton(e.target);
      if (b) ack(b);
    }, true);
    document.addEventListener('click', e => {
      const b = editButton(e.target);
      if (!b) return;
      // The row itself may do something else entirely.
      e.stopPropagation();
      // Keyboard activation sends no pointerdown, so the receipt is raised
      // here as well rather than only there.
      ack(b);
      post(`/queue/${b.dataset.qid}/${b.dataset.action}`)
        .then(r => { if (!r || r.ok === false) ackFailed(b); })
        .catch(() => ackFailed(b));
    }, true);
  }

  const editButton = t => {
    const b = t && t.closest ? t.closest('.qedit button[data-qid]') : null;
    return b && !b.disabled ? b : null;
  };

  // ---- acknowledging a press ---------------------------------------------
  // A queue edit is fire-and-forget and its result arrives with the next
  // snapshot, so between the press and the confirmation there was nothing at
  // all to see -- and nothing to tell a press that had been taken from one
  // that had been dropped.
  //
  // A class, not a style: each skin says what "registered" looks like in its
  // own palette, the way it already says what every other state looks like.
  //
  // Confirmation needs no timer in the ordinary case. The snapshot carrying
  // the change rebuilds the row and the mark goes with it, which is why this
  // is honest rather than decorative: the flash ends when the engine agrees,
  // not on a fixed schedule. `ACK_HOLD_MS` is only the floor, for a press that
  // changes nothing the listener can see.
  const ACK_HOLD_MS = 700;
  const ACK_FAIL_MS = 1200;

  // Commands that DO something once, rather than moving the player into a state
  // the next snapshot will report. Only these are acknowledged: Play and Pause
  // come back as `playing` and every skin already shows that, so flashing them
  // as well would be a second answer to a question already answered.
  //
  // The class is all core contributes. Each skin decides whether to show it and
  // in what colours -- Lempi and MuLibPlay flash Skip, WinAmp leaves it to the
  // bevel its own idiom already presses.
  const MOMENTARY = new Set(['skip']);

  function ack(b) {
    b.classList.remove('failed');
    b.classList.add('acked');
    clearTimeout(b.ackTimer);
    b.ackTimer = setTimeout(() => b.classList.remove('acked'), ACK_HOLD_MS);
  }

  // Refused, or never answered. In practice the button is still on screen to
  // wear this: the row is rebuilt only when the queue actually changes, which
  // is the case where the request did NOT fail.
  function ackFailed(b) {
    clearTimeout(b.ackTimer);
    b.classList.remove('acked');
    b.classList.add('failed');
    b.ackTimer = setTimeout(() => b.classList.remove('failed'), ACK_FAIL_MS);
  }

  // ---- binders -----------------------------------------------------------
  // Behaviour every skin needs and none should own. A skin marks up a slider,
  // a select and a list; these make them work, and the skin keeps only what it
  // is for -- where things go and what they look like.
  //
  // Each returns a render function for the snapshot, which `subscribe` calls.
  // They are opt-in: a skin that wants to do something unusual simply does not
  // call them, and nothing here is load-bearing for the rest.

  // The volume slider. While dragging, the slider is the truth -- adopting the
  // pushed value mid-drag would fight the user's thumb twice a second -- and
  // the level is sent on release, not per pixel of travel.
  function bindVolume(slider, label) {
    let holding = false;
    let pending = 0;
    // Written only when it changes: every skin shows this, twice a second
    // `[LOG-SPK-900]`.
    const show = db => {
      const t = fmt.db(db);
      if (label && label.textContent !== t) label.textContent = t;
    };
    slider.oninput = () => {
      holding = true;
      pending = fmt.round1(fader.db(Number(slider.value)));
      show(pending);
    };
    slider.onchange = () => { holding = false; post(`/volume/${pending}`); };
    return s => {
      if (holding) return;
      const db = fmt.round1(s.volume_db ?? 0);
      slider.value = fader.travel(db);
      show(db);
    };
  }

  // Is there a Director yet?
  //
  // `pool` is absent exactly when there is none `[REQ-VIS-150]`, which makes it
  // the one honest way to read an empty programme list. The web server binds
  // before the Director is built -- deliberately, since the page is the only
  // way back into an appliance -- so for those seconds the snapshot reports no
  // programmes because none are *known*, not because none exist. A Director
  // that is there and still reports none means none are configured, and saying
  // "starting" for ever would be a lie on a system that simply has none.
  const directorReady = s => s.pool != null;

  // The programme picker. The option list is rebuilt only when it actually
  // changes; replacing it on every push would close the dropdown in the hand
  // of whoever is reading it.
  //
  // With nothing to offer the control is disabled and says why. Disabled
  // rather than merely empty because `/program/{id}` validates the id against
  // this same list: a choice made during the startup window is answered 404
  // and dropped without a word, so a picker that looked usable would be one
  // more control that takes a press and does nothing `[REQ-VIS-185]`.
  function bindProgram(select, autoLabel = 'Automatic (by time of day)', labels = {}) {
    const starting = labels.starting ?? 'Program Director starting…';
    const none = labels.none ?? 'no programmes configured';
    let signature = '';
    select.onchange = e => post(`/program/${e.target.value}`);
    return s => {
      const programs = s.programs || [];
      const waiting = programs.length === 0;
      // A real signature is `id + name` joined, so it always starts with a
      // digit and can never collide with either of these.
      const sig = waiting ? (directorReady(s) ? 'empty:none' : 'empty:starting')
                          : programs.map(p => p.id + p.name).join('|');
      if (sig !== signature) {
        signature = sig;
        select.textContent = '';
        if (waiting) {
          const o = document.createElement('option');
          o.textContent = directorReady(s) ? none : starting;
          select.appendChild(o);
        } else {
          const auto = document.createElement('option');
          auto.value = 'auto';
          auto.textContent = autoLabel;
          select.appendChild(auto);
          for (const p of programs) {
            const o = document.createElement('option');
            o.value = p.id;
            o.textContent = `${p.name} — from ${p.start}`;
            select.appendChild(o);
          }
        }
      }
      select.disabled = waiting;
      if (waiting) return;
      if (!s.program_manual) {
        select.value = 'auto';
      } else {
        const m = programs.find(p => p.name === s.program);
        if (m) select.value = String(m.id);
      }
    };
  }

  // One queue row: edit controls first, then the title, then the duration.
  // The skin supplies the element it wants and how the row should read; the
  // order and the controls are the same everywhere because they are the same
  // idea everywhere `[REQ-VIS-185]`.
  function queueRow(item, label, tag = 'li', opts = {}) {
    const row = document.createElement(tag);
    row.dataset.passage = item.passage_id;
    row.dataset.qid = item.qid;
    if (item.editable === false) row.dataset.locked = '1';
    // A skin may put ONE set of controls beside a selected row instead of a
    // set on every row. Both are honest arrangements; which suits depends on
    // whether the skin has a notion of a selected track.
    if (opts.controls !== false) {
      row.appendChild(queueControls(item.qid, item.editable, opts.ends));
    }
    const title = document.createElement('span');
    title.className = 'qtitle';
    // `opts.linkable`: title/artist become their own clickable spans,
    // reaching the preference panel `[REQ-VIS-285]`, instead of one plain
    // text node -- a skin opts in because its own `label()` string is no
    // longer what gets shown. Skins that don't ask stay byte-for-byte
    // unchanged (WinAmp's marquee has no separate artist node to link).
    if (opts.linkable) {
      linkableTrack(title, item);
    } else {
      title.appendChild(document.createTextNode(label(item) + ' '));
    }
    const dur = document.createElement('span');
    dur.className = 'dur';
    dur.textContent = fmt.clock(item.duration_ms);
    title.appendChild(dur);
    row.appendChild(title);
    return row;
  }

  // The whole list, including the empty case, which every skin got wrong in
  // its own way before this.
  //
  // Rebuilt only when the list it draws has actually changed. A snapshot
  // arrives twice a second and the queue does not change twice a second, so
  // wiping and rebuilding on every push meant the edit buttons were replaced
  // under the listener's own finger -- and a press that spans a rebuild is
  // never delivered as a click at all. The same guard `bindProgram` above
  // already keeps, for the same shape of reason: a render that throws away
  // live DOM costs more than the comparison that avoids it.
  //
  // `QueueItem` carries nothing that moves with the clock -- no position, no
  // countdown -- so the whole list serialises to a stable signature, and one
  // that cannot go stale when a field is added to it later.
  function bindQueue(container, label, tag = 'li', empty = 'nothing queued', opts = {}) {
    let signature = null;
    return s => {
      const items = s.queue || [];
      // Read per render, not per bind: the rows are rebuilt on every snapshot
      // and the selection changes between them, so a value captured when the
      // binder was made would mark the wrong row for ever. A function is
      // accepted for exactly that reason.
      const sel = typeof opts.selected === 'function' ? opts.selected() : opts.selected;
      const sig = JSON.stringify([sel, items]);
      if (sig === signature) return;
      signature = sig;
      container.textContent = '';
      if (!items.length) {
        const none = document.createElement(tag);
        none.className = 'empty';
        none.textContent = empty;
        container.appendChild(none);
        return;
      }
      for (let i = 0; i < items.length; i++) {
        const q = items[i];
        // Which shift verbs can do anything from here, so the ones that
        // cannot say so rather than answering silently.
        const ends = { first: i === 0, last: i === items.length - 1 };
        const row = queueRow(q, label, tag, { ...opts, ends });
        // Selection is the skin's idea; the row only reports the click and
        // wears the mark the skin asks for.
        // The entry, so a skin can tell two copies of one passage apart; the
        // whole item too, because what a skin wants next is usually the
        // passage behind it -- an explanation is about the recording, not
        // about which copy of it is queued.
        if (opts.onPick) row.onclick = () => opts.onPick(q.qid, q);
        if (sel != null && sel === q.qid) row.classList.add('picked');
        container.appendChild(row);
      }
    };
  }

  // ---- preference panel [REQ-VIS-285], [REQ-VIS-290] ---------------------
  // MuLibPlay let a listener hand-tune an artist's or a recording's own
  // rotation/recovery/restraint directly. Lempi's schema carries those
  // exact values forward (`listener_preferences`) but nothing reached them
  // until this. An in-page panel, not a dialog: each skin carries its own
  // `<div id="pref-panel" hidden>` at a spot in its own markup, filled in
  // and toggled from here -- no overlay, no injected stylesheet, so the
  // panel is simply styled by whichever skin's CSS it lives in rather than
  // needing to fake that skin's colors from JS.

  // Builds `<title-span>[ — <artist-span>]`, each a clickable link when its
  // own mbid is known, plain text otherwise (unidentified audio, or a
  // recording with no linked artist -- the same case that already leaves
  // `artist` unset). Shared by every skin that opts into `queueRow`'s
  // `linkable` mode, each skin's own now-playing render, and (exported as
  // `Lempi.linkableTrack`) the Lempi skin's own "It beat" runner-up list --
  // the same link, wherever a track is named.
  function linkableTrack(container, item, opts = {}) {
    const open = (kind, id, label, e) => {
      e.stopPropagation();
      e.preventDefault();
      Lempi.editPreference(kind, id, label);
    };
    const titleEl = document.createElement(item.mbid ? 'a' : 'span');
    titleEl.className = 'pref-link pref-link-title';
    titleEl.textContent = item.title ?? '';
    if (item.mbid) {
      titleEl.href = '#';
      titleEl.onclick = e => open('recording', item.mbid, item.title, e);
    }
    container.appendChild(titleEl);
    if (item.artist) {
      container.appendChild(document.createTextNode(opts.artistJoin ?? ' — '));
      const artistEl = document.createElement(item.artist_mbid ? 'a' : 'span');
      artistEl.className = 'pref-link pref-link-artist';
      artistEl.textContent = item.artist;
      if (item.artist_mbid) {
        artistEl.href = '#';
        artistEl.onclick = e => open('artist', item.artist_mbid, item.artist, e);
      }
      container.appendChild(artistEl);
    }
    container.appendChild(document.createTextNode(' '));
  }

  // No injected stylesheet, no backdrop, no `position: fixed` -- found live
  // to be the actual cause of "Cancel and Save do nothing": a full-viewport
  // overlay this codebase's own convention never uses anywhere else, whose
  // click-to-close-on-backdrop listener and z-index stacking were exactly
  // the kind of thing this project's own "no modal convention" note in
  // `SPEC029` already flagged as a risk worth avoiding. Each skin instead
  // carries its own empty `<div id="pref-panel" class="pref-panel" hidden>`
  // at a spot in its own markup, styled entirely by that skin's own CSS --
  // "the same color and control shape scheme as the parent interface" is
  // then simply true, not something this file has to fake with inline
  // colors. A skin that doesn't provide the slot (WinAmp) silently gets no
  // preference panel, the same posture every other opt-in skin feature here
  // already takes.

  // Fills the skin-provided slot with the field markup, once per skin load
  // -- `loadSkin()` replaces `#app`'s entire subtree on every skin (re)load,
  // so a cached reference from a previous load would be a detached node;
  // this always looks the slot up fresh and only builds into it if it is
  // still empty.
  function prefPanelSlot() {
    const panel = document.getElementById('pref-panel');
    if (!panel || panel.dataset.built) return panel;
    // Each field's own label/readout/reset share a row; the slider itself
    // gets a row of its own, full width -- the range of a control read at
    // a glance, not squeezed beside three other things fighting it for
    // space.
    const field = (name, label) => `
      <div class="pref-field" data-field="${name}">
        <div class="pref-field-head">
          <label>${label}</label>
          <span class="pref-readout"></span>
          <button type="button" class="pref-reset" title="use the default">Reset</button>
        </div>
        <input type="range" min="-0.998" max="0.998" step="0.001">
      </div>`;
    // `.pref-subject` carries "by <artist> from <album>" on its own line
    // rather than being run into the heading `[SPEC-PREF-090]` -- three
    // names concatenated into one uppercase, letter-spaced heading (which
    // is what the Lempi skin makes of `.pref-heading`) is a wall, and the
    // title is the thing being edited while the other two only say which
    // one it is. `.pref-specials` is filled per subject in
    // `editPreference` below, since which specials exist is a property of
    // the library rather than of this file `[SPEC-PREF-080]`.
    panel.innerHTML = `
      <h2 class="pref-heading"></h2>
      <p class="pref-subject" hidden></p>
      <p class="pref-error" hidden></p>
      ${field('rotation', 'Cooldown')}
      ${field('recovery', 'Recovery')}
      ${field('restraint', 'Preference')}
      <div class="pref-specials" hidden></div>
      <div class="pref-actions">
        <button type="button" class="pref-cancel">Cancel</button>
        <button type="button" class="pref-save">Save</button>
      </div>`;
    // Wired once, here, unconditionally -- Cancel must work even if a later
    // `editPreference` call fails before reaching its own wiring below.
    // Closes the play-frequency panel along with the preference panel
    // itself `[REQ-VIS-300]`: "close the history when the preference
    // closes" -- there is no reading of frequency data for a subject
    // whose preference panel is no longer open.
    panel.querySelector('.pref-cancel').onclick = () => {
      panel.hidden = true;
      closeFreqPanel();
    };
    panel.dataset.built = '1';
    return panel;
  }

  // The play-frequency panel is its own slot, `#freq-panel`, built once the
  // same way `prefPanelSlot()` builds `#pref-panel` -- a skin that carries
  // no slot (WinAmp) silently gets no frequency table, same posture as the
  // preference panel it sits under. Table, not a list: five windows are
  // columns, "All"/"User"/each program are rows, so a grid reads far more
  // directly than repeating five labelled numbers per row.
  function freqPanelSlot() {
    const panel = document.getElementById('freq-panel');
    if (!panel || panel.dataset.built) return panel;
    panel.innerHTML = `
      <h3 class="freq-heading"></h3>
      <table class="freq-table">
        <thead><tr><th></th><th>24h</th><th>7d</th><th>30d</th><th>365d</th><th>All time</th></tr></thead>
        <tbody></tbody>
      </table>`;
    panel.dataset.built = '1';
    return panel;
  }

  function closeFreqPanel() {
    const panel = document.getElementById('freq-panel');
    if (panel) panel.hidden = true;
  }

  // Guards a slow `/play-frequency` response against landing after the
  // subject has changed, or the preference panel (and this one with it)
  // has already closed -- the same problem a fast double-click on two
  // different links already risks. Each call to `loadFreqPanel` bumps this
  // and captures its own value; the response is applied only if it is
  // still current when it arrives.
  let freqToken = 0;

  // Fetched and shown independently of the preference panel's own fetch
  // above -- "do not delay opening of the preference panel while play
  // frequency is prepared, open play frequency panel when it is ready"
  // `[REQ-VIS-300]`. Never awaited by its caller.
  async function loadFreqPanel(kind, id) {
    const panel = freqPanelSlot();
    if (!panel) return; // this skin carries no #freq-panel slot
    const token = ++freqToken;
    panel.hidden = true; // clear whatever the last subject showed
    let rows;
    try {
      const r = await fetch(`/play-frequency/${kind}/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`the server answered ${r.status}`);
      rows = await r.json();
    } catch {
      return; // silent, like a failed preference fetch leaving Save inert
    }
    if (token !== freqToken) return; // subject changed, or panel closed, meanwhile
    panel.querySelector('.freq-heading').textContent =
      `Play frequency — ${kind === 'artist' ? 'artist' : 'recording'}`;
    const body = panel.querySelector('tbody');
    body.innerHTML = '';
    for (const row of rows) {
      const tr = document.createElement('tr');
      [row.label, ...row.counts.map(String)].forEach((text, i) => {
        const cell = document.createElement(i === 0 ? 'th' : 'td');
        cell.textContent = text;
        tr.appendChild(cell);
      });
      body.appendChild(tr);
    }
    panel.hidden = false;
  }

  // Rotation/recovery are log-scale hours `[SPEC-DIR-110]`: `10^v` hours.
  // Restraint is a log-scale multiplier: `10^-v`. Both formulas are
  // `frequency.rs`'s own -- this is a *readout*, not a second definition;
  // the server is still the one place a value actually takes effect.
  function prefDuration(v) {
    const hours = 10 ** v;
    if (hours < 1) return `≈ ${Math.round(hours * 60)} min`;
    if (hours < 48) return `≈ ${hours.toFixed(1)} hrs`;
    return `≈ ${(hours / 24).toFixed(1)} days`;
  }
  function prefMultiplier(v) {
    const m = 10 ** -v;
    return m >= 1 ? `≈ ${m.toFixed(1)}× as often` : `≈ ${m.toFixed(2)}× as often`;
  }
  const READOUT = { rotation: prefDuration, recovery: prefDuration, restraint: prefMultiplier };

  // None of the three sliders move linearly against their own stored
  // value -- a plain `10^v`/`10^-v` range read as "0 through most of the
  // right hand side," found live: everywhere near the useful, commonly-
  // wanted values was squeezed into a sliver next to "no change at all,"
  // and everywhere else read as indistinguishable from the extreme. `t`
  // (each slider's own raw value, `-1..1`, never quite reaching either
  // end) is a *position*, not a preference; each step toward an edge
  // halves the remaining distance to it while doubling (or halving) the
  // duration/multiplier it represents, *centered on that field's own
  // default* -- 1× the default at the center (`t=0`), 2× a quarter of the
  // way toward "more" (`t=-0.5`), 0.5× a quarter toward "less" (`t=0.5`),
  // 4× an eighth of the way (`t=-0.75`), 0.25× an eighth the other way,
  // 8× a sixteenth, and so on -- exactly the geometric spacing asked for,
  // verified by hand against each of those points before this was
  // trusted. Centering on the default rather than a fixed number matters
  // here specifically because rotation/recovery's own default is not the
  // same number for an artist as for a recording (`Tuning::artist_defaults`/
  // `recording_defaults`, `[SPEC-DIR-120]`) -- the slider has to center on
  // whichever one actually applies to what is open, not a value baked in
  // once. `LOG2_10` converts the doubling scale (`u`) to and from the
  // stored value itself, which is decade-scaled either way (hours = `10^v`,
  // multiplier = `10^-restraint`, `[SPEC-DIR-115]`) -- the transform
  // changes what a slider *feels* like, never what gets sent to or stored
  // by the server, which still only ever sees the real `rotation`/
  // `recovery`/`restraint` value. One formula for all three fields, `center`
  // the only thing that differs (restraint's own default is always 0, so
  // this reduces to exactly the multiplier transform restraint already
  // had): "lower `v` is left" is what every field's own slider already
  // meant before this (shorter cooldown/recovery, or a bigger boost, both
  // sat left of center), so nothing about the left/right sense changes.
  const LOG2_10 = Math.log2(10);
  function tToV(t, center) {
    if (t === 0) return center;
    const u = Math.sign(t) * Math.log2(1 - Math.abs(t));
    return center - u / LOG2_10;
  }
  function vToT(v, center) {
    const u = -(v - center) * LOG2_10;
    if (u === 0) return 0;
    return -Math.sign(u) * (1 - 2 ** -Math.abs(u));
  }

  // Builds one row per special the library registers `[SPEC-PREF-080]` --
  // MuLibPlay's Christmas/Winter/Summer/Children's occasions and its profanity
  // slider, which were editable there and, until this, were carried by Lempi
  // and settable by nothing. Returns the per-row state `editPreference`'s
  // own Save reads.
  //
  // Which specials exist is never hardcoded here: the server reports
  // whatever `listener_occasions` registers, so adding one stays "rows in
  // two tables and no edit to the engine" `[SPEC-DIR-130]` -- and no edit to
  // this file either.
  //
  // Two values per special, and the distinction is the whole point.
  // `inherited` is what `flavor` holds (six years of MuLibPlay tagging, or
  // whatever Vipunen derived); `own` is what this listener set by hand, `null`
  // when they never did. Reset drops `own` and goes back to `inherited` --
  // it does not mean zero, which would be the different and permanent claim
  // "this is definitely not a Christmas song."
  function renderSpecials(box, rows) {
    box.textContent = '';
    box.hidden = rows.length === 0;
    if (!rows.length) return [];
    const heading = document.createElement('h3');
    heading.className = 'pref-section';
    heading.textContent = 'Specials';
    box.appendChild(heading);

    return rows.map(row => {
      const inherited = row.inherited ?? 0;
      const state = {
        key: `special:${row.characteristic}:${row.class}`,
        own: row.value ?? null,
        cleared: false,
      };
      const field = document.createElement('div');
      field.className = 'pref-field';
      // The same `.pref-field` shape the three tuning sliders use, so every
      // skin's existing CSS dresses these without a line of new styling per
      // skin. Structure from markup, every name through `textContent`:
      // `row.label` is a database value, and a label is not a place to start
      // trusting one with `innerHTML`.
      field.innerHTML = `
        <div class="pref-field-head">
          <label></label>
          <span class="pref-readout"></span>
          <button type="button" class="pref-reset" title="use the inherited value">Reset</button>
        </div>
        <input type="range" min="0" max="1" step="0.01">`;
      field.querySelector('label').textContent = row.label;
      const input = field.querySelector('input');
      const readout = field.querySelector('.pref-readout');
      const refresh = () => {
        const v = state.own ?? inherited;
        readout.textContent = `${Math.round(v * 100)}%${state.own == null ? ' (inherited)' : ''}`;
      };
      input.value = state.own ?? inherited;
      refresh();
      input.oninput = () => {
        state.own = Number(input.value);
        state.cleared = false;
        refresh();
      };
      field.querySelector('.pref-reset').onclick = () => {
        state.own = null;
        state.cleared = true;
        input.value = inherited;
        refresh();
      };
      box.appendChild(field);
      return state;
    });
  }

  // Opens the panel for one subject, fetches its current tuning, and wires
  // Save/Reset. `kind` is `'recording'` or `'artist'`; `id` its mbid.
  // A subject with no mbid (unidentified audio, an uncredited artist) has
  // nothing to open -- callers only invoke this when an id is present.
  async function editPreference(kind, id, label) {
    const panel = prefPanelSlot();
    if (!panel) return; // this skin carries no #pref-panel slot (WinAmp)
    const err = panel.querySelector('.pref-error');
    const save = panel.querySelector('.pref-save');
    const subject = panel.querySelector('.pref-subject');
    const specialsBox = panel.querySelector('.pref-specials');
    err.hidden = true;
    // Cleared, not left showing the last subject's: the fetch below may
    // fail, and a panel headed by one recording while naming another's
    // artist and album is worse than one naming neither.
    subject.hidden = true;
    subject.textContent = '';
    specialsBox.hidden = true;
    specialsBox.textContent = '';
    // The caller's own label until the fetch lands, then the library's own
    // naming `[SPEC-PREF-090]` -- which is the same for a given recording
    // wherever the panel was opened from, unlike what each surface happens
    // to have on hand.
    panel.querySelector('.pref-heading').textContent =
      `${kind === 'artist' ? 'Artist' : 'Recording'} preferences — ${label ?? id}`;
    // Disabled until the fetch below actually succeeds -- there is nothing
    // to save before then, and this is what stayed a dead button in the
    // earlier version whenever that fetch failed: Save was only ever wired
    // *after* it, so a failure left it silently inert. Cancel needs none of
    // this -- wired once, unconditionally, in `prefPanelSlot()`.
    save.disabled = true;
    save.onclick = null;
    panel.hidden = false;
    // Its own independent fetch, deliberately not awaited -- the
    // preference panel above is already open and must not wait on this
    // `[REQ-VIS-300]`.
    loadFreqPanel(kind, id);

    let current = {};
    let defaults = {};
    try {
      const r = await fetch(`/preference/${kind}/${encodeURIComponent(id)}`);
      if (!r.ok) throw new Error(`the server answered ${r.status}`);
      const doc = await r.json();
      current = doc;
      defaults = doc.defaults;
    } catch (e) {
      err.textContent = `could not load current preferences: ${e.message}`;
      err.hidden = false;
      return;
    }

    const named = current.subject ?? {};
    panel.querySelector('.pref-heading').textContent =
      `${kind === 'artist' ? 'Artist' : 'Recording'} preferences — ${named.title ?? label ?? id}`;
    // MuLibPlay's own track editor headed this page "<name> *by* <artist>
    // *from* <album>", and that is the phrasing kept here. Either half is
    // dropped when the library doesn't know it rather than shown empty --
    // an uncredited artist and a recording on no release are both ordinary
    // `[REQ-VIS-120]`, not a gap to paper over.
    const provenance = [];
    if (named.artist) provenance.push(`by ${named.artist}`);
    if (named.album) provenance.push(`from ${named.album}`);
    subject.textContent = provenance.join(' · ');
    subject.hidden = provenance.length === 0;
    const specials = renderSpecials(specialsBox, current.specials ?? []);

    // Per-field state: `null` means "at the default", a number means
    // "explicitly set to this" -- the same three-way shape the server
    // itself keeps, so Save only ever sends what actually changed. Every
    // field's slider is read through the same position transform, each
    // centered on that field's own default -- see `tToV`/`vToT` above.
    const state = {};
    const cleared = new Set();
    for (const field of ['rotation', 'recovery', 'restraint']) {
      const center = defaults[field];
      state[field] = current[field] ?? center;
      const box = panel.querySelector(`.pref-field[data-field="${field}"]`);
      const input = box.querySelector('input');
      const readout = box.querySelector('.pref-readout');
      const resetBtn = box.querySelector('.pref-reset');
      const refresh = () => {
        readout.textContent = READOUT[field](state[field])
          + (current[field] == null ? ' (default)' : '');
      };
      input.value = vToT(state[field], center);
      refresh();
      input.oninput = () => {
        state[field] = tToV(Number(input.value), center);
        cleared.delete(field);
        current[field] = state[field]; // no longer "at the default" once dragged
        refresh();
      };
      resetBtn.onclick = () => {
        state[field] = center;
        current[field] = null;
        cleared.add(field);
        input.value = vToT(state[field], center);
        refresh();
      };
    }

    save.disabled = false;
    save.onclick = async () => {
      const q = new URLSearchParams();
      for (const field of ['rotation', 'recovery', 'restraint']) {
        if (cleared.has(field)) q.set(field, '');
        else if (current[field] != null) q.set(field, String(state[field]));
      }
      // Same three-way query the three fields above use `[SPEC-PREF-087]`:
      // a special nobody touched is simply absent, so a save never rewrites
      // tagging it did not look at.
      for (const sp of specials) {
        if (sp.cleared) q.set(sp.key, '');
        else if (sp.own != null) q.set(sp.key, String(sp.own));
      }
      try {
        const r = await fetch(`/preference/${kind}/${encodeURIComponent(id)}?${q}`,
          { method: 'POST' });
        if (!r.ok) throw new Error(`the server answered ${r.status}`);
        panel.hidden = true;
        closeFreqPanel();
      } catch (e) {
        err.textContent = `could not save: ${e.message}`;
        err.hidden = false;
      }
    };
  }

  // ---- skins -------------------------------------------------------------
  // The choice is per browser, not per player: two people on two phones may
  // want different skins of the same radio, and neither should be able to
  // restyle the other. That is why it lives in localStorage and not the engine.
  const KEY = 'lempi.skin';
  let catalogue = [];

  // MuLibPlay is what a browser that has never chosen gets `[REQ-VIS-124]`:
  // the face six years of listening happened in front of. A browser that HAS
  // chosen keeps its choice, which is the whole reason this is stored.
  const DEFAULT_SKIN = 'mulibplay';

  // localStorage rather than a cookie, deliberately. The server never needs to
  // know which skin a browser wears -- the shell fetches it -- so a cookie
  // would ride on every request, including the WebSocket upgrade and every art
  // fetch, to tell the server something it does not use. This also survives
  // where a session cookie would not.
  //
  // Wrapped because storage THROWS rather than returning null when a browser
  // is in a private mode or has site data blocked. Unwrapped, that exception
  // lands before any skin loads, and the page is blank rather than merely
  // unable to remember.
  function remember(name) {
    try { localStorage.setItem(KEY, name); } catch { /* unremembered, not broken */ }
  }
  function remembered() {
    try { return localStorage.getItem(KEY); } catch { return null; }
  }

  function chosen() {
    const q = new URLSearchParams(location.search).get('skin');
    if (q) remember(q);
    return q || remembered() || DEFAULT_SKIN;
  }

  function setSkin(name) {
    remember(name);
    location.href = location.pathname; // drop ?skin= so it is not sticky twice
  }

  // A skin is markup, a stylesheet and a script, in that order: the script may
  // assume its own DOM is present, which is the whole reason it loads last.
  async function loadSkin(name) {
    const base = `/skin/${name}`;
    const [html] = await Promise.all([
      fetch(`${base}/skin.html`).then(r => r.text()),
      new Promise((ok, no) => {
        const l = document.createElement('link');
        l.rel = 'stylesheet';
        l.href = `${base}/skin.css`;
        l.onload = ok;
        l.onerror = no;
        document.head.appendChild(l);
      }),
    ]);
    document.getElementById('app').innerHTML = html;
    // Buttons are wired centrally so no skin has to know a URL to be playable.
    for (const b of document.querySelectorAll('[data-cmd]')) {
      b.onclick = () => {
        // Momentary commands get the receipt the queue verbs get, and for the
        // same reason: nothing in the snapshot says "a skip happened", so
        // unlike Play and Pause -- whose effect comes back as `playing`, which
        // every skin already draws one way or another -- Skip has no state of
        // its own to show that the press landed.
        //
        // On click rather than on pointerdown, which is where the queue verbs
        // raise theirs. There the early signal buys something: those buttons
        // are rebuilt from snapshots and a press can outlive the element. This
        // one is markup that is never replaced, so the click is both reliable
        // and the exact moment the command is sent -- and a receipt that fires
        // when the command does is the more honest of the two.
        const momentary = MOMENTARY.has(b.dataset.cmd);
        if (momentary) ack(b);
        const sent = post(`/command/${b.dataset.cmd}`);
        if (momentary) {
          sent.then(r => { if (!r || r.ok === false) ackFailed(b); })
              .catch(() => ackFailed(b));
        }
      };
    }
    for (const b of document.querySelectorAll('[data-skin]')) {
      b.onclick = () => setSkin(b.dataset.skin);
    }
    // A skin marks up an empty <select data-skins> and gets a working picker;
    // the catalogue comes from the server, so adding a skin never means editing
    // three others to list it.
    for (const sel of document.querySelectorAll('[data-skins]')) {
      sel.textContent = '';
      for (const s of catalogue) {
        const o = document.createElement('option');
        o.value = s.name;
        o.textContent = s.label;
        sel.appendChild(o);
      }
      sel.value = chosen();
      sel.onchange = () => setSkin(sel.value);
    }
    await new Promise((ok, no) => {
      const s = document.createElement('script');
      s.src = `${base}/skin.js`;
      s.onload = ok;
      s.onerror = no;
      document.body.appendChild(s);
    });
    // A skin that loads after the first snapshot must not sit blank waiting for
    // the next one, which may be half a second away.
    if (last) dispatch(last);
  }

  function dispatch(s) {
    last = s;
    if (s.fader_min_db) faderFloor = s.fader_min_db;
    for (const fn of listeners) {
      // One skin's mistake must not silence the rest of the page, and a render
      // that throws every 500 ms would otherwise fill the console and stop.
      try { fn(s); } catch (e) { console.error('skin render failed', e); }
    }
  }

  // ---- the socket --------------------------------------------------------
  // Reconnect rather than go stale: the player outlives any one page load, and
  // a silently dead socket looks exactly like paused playback.
  let status = 'connecting';
  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = e => dispatch(JSON.parse(e.data));
    ws.onopen = () => { status = 'connected'; };
    ws.onclose = () => {
      status = 'reconnecting';
      if (last) dispatch(last); // let skins show the disconnection
      setTimeout(connect, 1000);
    };
  }

  return {
    subscribe(fn) { listeners.push(fn); if (last) fn(last); },
    get snapshot() { return last; },
    get status() { return status; },
    get skins() { return catalogue; },
    get skin() { return chosen(); },
    setSkin,
    fader,
    fmt,
    bindVolume,
    bindProgram,
    bindQueue,
    queueRow,
    showArt,
    showLyrics,
    artUrl: id => `/art/${id}`,
    command: name => post(`/command/${name}`),
    volume: db => post(`/volume/${db}`),
    seek: ms => post(`/seek/${Math.max(0, Math.round(ms))}`),
    restartUnderruns: () => post('/underruns/restart'),
    // A unix second to something a person reads. Local time, because the
    // listener is standing in it.
    since(unix) {
      if (!unix) return '';
      const d = new Date(unix * 1000);
      const today = new Date();
      const sameDay = d.toDateString() === today.toDateString();
      const clock = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      return sameDay ? clock : `${d.toLocaleDateString()} ${clock}`;
    },
    // Wire a progress bar up as a control. Shared because both skins want
    // the same arithmetic, and a second copy of it would drift.
    seekable(bar, state) {
      bar.addEventListener('click', e => {
        const s = state();
        if (!s || !s.can_seek || !s.duration_ms) return;
        const box = bar.getBoundingClientRect();
        if (box.width <= 0) return;
        // Clamped: a click on the very edge of a padded hit area can land
        // fractionally outside the bar itself.
        const at = Math.min(Math.max((e.clientX - box.left) / box.width, 0), 1);
        Lempi.seek(at * s.duration_ms);
      });
    },
    program: id => post(`/program/${id}`),
    skipFade: ms => post(`/skip/fade/${Math.round(ms)}`),
    skipLead: ms => post(`/skip/lead/${Math.round(ms)}`),
    resumeSave: ms => post(`/resume/save/${Math.round(ms)}`),
    // This node's place in a fleet [SPEC-DLY-010], [SPEC-ECHO-010]. The host
    // goes in a body rather than a path: a hostname is text a person typed,
    // and path-escaping it to satisfy a router is a way to mangle it.
    echoTrim: ms => post(`/echo/trim/${Math.round(ms)}`),
    echoTrimReset: () => post('/echo/trim/reset'),
    echoFollow: host => fetch('/echo/follow', { method: 'POST', body: String(host ?? '') }),
    echoJoinNow: now => post(`/echo/join/${now ? 1 : 0}`),
    skipSuppress: h => post(`/skip/suppress/${Math.round(h)}`),
    dequeueSuppress: h => post(`/dequeue/suppress/${Math.round(h)}`),
    queueDepth: n => post(`/queue/depth/${Math.round(n)}`),
    sampleInterval: ms => post(`/sample/interval/${Math.round(ms)}`),
    switchBackend: which => post(`/backend/${which}`),
    cueSheets: on => post(`/cue/${on ? 'on' : 'off'}`),
    covers: on => post(`/covers/${on ? 'on' : 'off'}`),
    lyricsCache: on => post(`/lyricscache/${on ? 'on' : 'off'}`),
    lyricsSidecar: on => post(`/lyricssidecar/${on ? 'on' : 'off'}`),
    // The player page: load the chosen skin, then follow the socket.
    async start() {
      catalogue = await fetch('/skins').then(r => r.json()).catch(() => []);
      await loadSkin(chosen());
      connect();
    },
    // The browse page: it wants the skin's LOOK and the command helpers, but
    // not the player's markup and not a socket -- a library listing does not
    // change twice a second.
    async startBare() {
      catalogue = await fetch('/skins').then(r => r.json()).catch(() => []);
      const l = document.createElement('link');
      l.rel = 'stylesheet';
      l.href = `/skin/${chosen()}/skin.css`;
      document.head.appendChild(l);
    },
    // Throws rather than resolving to nothing when the query fails, so a
    // broken listing cannot be mistaken for an empty library.
    browse: (kind, filter = {}) => {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(filter)) if (v) q.set(k, v);
      return fetch(`/browse/${kind}?${q}`).then(r => {
        if (!r.ok) throw new Error(`the server answered ${r.status}`);
        return r.json();
      });
    },
    // A page of what has actually sounded `[REQ-VIS-250]`. Same shape of
    // failure as `browse`: thrown, not silently empty.
    history: (page, size) =>
      fetch(`/history?page=${page}&size=${size}`).then(r => {
        if (!r.ok) throw new Error(`the server answered ${r.status}`);
        return r.json();
      }),
    // "Flag this for review" `[REQ-VIS-265]` -- on at any time, off at any
    // time, the same call either way with the new state carried as the query.
    setFlag: (kind, id, flagged) =>
      fetch(`/history/flag/${kind}/${encodeURIComponent(id)}?flagged=${flagged}`,
            { method: 'POST' }).then(r => {
        if (!r.ok) throw new Error(`the server answered ${r.status}`);
      }),
    // The verbs, in one place `[REQ-VIS-185]`. The engine validates them and
    // is the authority; this is the list the pages build their controls from,
    // so adding one means touching the match in `web.rs` and this array,
    // rather than three separate button-building loops.
    VERBS: {
        place: [
            ['Now', 'now', 'playing now'],
            ['Next', 'next', 'playing next'],
            ['Last', 'last', 'added to the end'],
        ],
        edit: [
            ['\u00d7', 'remove', 'remove from the queue'],
            ['\u2191', 'sooner', 'play sooner'],
            ['\u2193', 'later', 'play later'],
        ],
    },
    queue: (id, action) => post(`/queue/${id}/${action}`),

    queueControls,
    // Exported because a skin that draws its own programme list -- MuLibPlay
    // draws stacked buttons rather than a picker -- needs the same reading of
    // an empty one that `bindProgram` makes.
    directorReady,
    named,
    badge,
    showBackArt,
    editPreference,
    linkableTrack,
  };
})();
