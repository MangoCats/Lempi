// MuLibPlay's "Browse by: Artist / Album / Track", rebuilt on Lempi's data
// [REQ-VIS-180].
//
// One page for all three skins rather than three implementations: it borrows
// the chosen skin's stylesheet and supplies only the layout a listing needs, so
// a new skin gets browsing for free and can still restyle every part of it.
//
// Built for a phone, which is how these pages were actually used. That means an
// alphabet bar rather than a scrollbar -- 463 artists is a long way to drag with
// a thumb, and one tap to the letter is the whole difference.
//
// Artist and album are ways IN to tracks, not destinations: an artist narrows to
// their albums, an album to its tracks, a track queues itself. The crumb trail
// is what makes that reversible.
(() => {
  const $ = id => document.getElementById(id);

  let kind = 'artists';
  // Told to us rather than assumed; `startBare` has no socket to learn it from,
  // so it is read from the first listing request's sibling endpoint.
  let browseLimit = 0;
  let filter = {};          // { artist, album } -- the narrowing, not the search
  let timer = null;

  // The heading must be derived the same way the server sorts, or the letters
  // stop being monotonic: strip a leading "The" here while the ORDER BY does
  // not, and "The Beatles" emits a stray B in the middle of the Ts. So this
  // takes the first character as it stands. Everything outside A-Z lands under
  // '#', which is where "10,000 Maniacs" and "'Til Tuesday" actually sort.
  const initial = name => {
    const c = (name || '').trim().toUpperCase()[0] || '#';
    return c >= 'A' && c <= 'Z' ? c : '#';
  };

  function crumbs() {
    const c = $('crumbs');
    c.textContent = '';
    if (!filter.artist && !filter.album) {
      c.textContent = { artists: 'All artists', albums: 'All albums',
                        tracks: 'All tracks' }[kind];
      return;
    }
    const all = document.createElement('a');
    all.textContent = 'All artists';
    all.onclick = () => { filter = {}; show('artists'); };
    c.appendChild(all);
    if (filter.artist) {
      c.append(' › ');
      const a = document.createElement('a');
      a.textContent = filter.artist;
      a.onclick = () => { filter = { artist: filter.artist }; show('albums'); };
      c.appendChild(a);
    }
    if (filter.album) c.append(' › ' + filter.album);
  }

  // One row shape for all three listings: a name, an optional second line, some
  // numbers, and a tap. Only the contents differ.
  function row(title, sub, nums, onTap) {
    const li = document.createElement('li');
    const name = document.createElement('div');
    name.className = 'name';
    const b = document.createElement('b');
    b.textContent = title;
    name.appendChild(b);
    if (sub) {
      const s = document.createElement('span');
      s.textContent = sub;
      name.appendChild(s);
    }
    li.appendChild(name);
    if (nums) {
      const n = document.createElement('div');
      n.className = 'num';
      n.textContent = nums;
      li.appendChild(n);
    }
    if (onTap) li.onclick = onTap;
    return li;
  }

  // The alphabet bar. Letters with nothing behind them are shown disabled
  // rather than hidden, so the bar does not reflow under the thumb as the
  // listing changes.
  function alphabet(anchors) {
    const bar = $('az');
    bar.textContent = '';
    const letters = ['#', ...'ABCDEFGHIJKLMNOPQRSTUVWXYZ'];
    for (const L of letters) {
      const b = document.createElement('button');
      b.textContent = L;
      const target = anchors.get(L);
      if (target) {
        b.onclick = () => target.scrollIntoView({ block: 'start', behavior: 'smooth' });
      } else {
        b.disabled = true;
      }
      bar.appendChild(b);
    }
  }

  const plural = (n, one) => `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`;

  const picked = () =>
    [...document.querySelectorAll('.pick:checked')].map(b => b.value);

  // The verbs are useless without a selection, and a button that does nothing
  // when pressed teaches nothing. Disabled until there is something to act on.
  function armed() {
    const n = picked().length;
    for (const b of document.querySelectorAll('#verbs button')) b.disabled = !n;
    $('picked').textContent = n ? `${plural(n, 'track')} selected` : '';
  }

  for (const [label, action, said] of Lempi.VERBS.place) {
    $('v-' + action).onclick = () => {
      const ids = picked();
      if (!ids.length) return;
      // One request carrying the list, in the order it is displayed. Sent as
      // separate requests they would arrive interleaved, and inserting each at
      // the same place would reverse them.
      Lempi.queue(ids.join(','), action).then(() => {
        $('note').textContent = `${plural(ids.length, 'track')} — ${said}.`;
        for (const b of document.querySelectorAll('.pick:checked')) b.checked = false;
        armed();
      });
    };
  }

  async function show(next) {
    if (next) kind = next;
    for (const b of document.querySelectorAll('[data-kind]')) {
      b.setAttribute('aria-selected', String(b.dataset.kind === kind));
    }
    crumbs();
    $('verbs').hidden = kind !== 'tracks';
    const body = $('rows');
    const note = $('note');

    let rows;
    try {
      rows = await Lempi.browse(kind, { ...filter, q: $('q').value.trim() });
    } catch (e) {
      // Say so. The first version of this page rendered a failed query as an
      // empty list, which is indistinguishable from an empty library and sent
      // the fault-finding in entirely the wrong direction.
      body.textContent = '';
      $('az').textContent = '';
      note.className = 'note bad';
      note.textContent = `Could not read the library: ${e.message}`;
      return;
    }

    body.textContent = '';
    note.className = 'note';
    if (!rows.length) {
      // Never dead-end on an artist. Album names come from the files' own
      // tags, which the player reads in the background on first run, so an
      // artist can legitimately have none yet -- and "no albums" is a useless
      // answer to "show me this artist". Their tracks are what was wanted.
      if (kind === 'albums' && filter.artist) {
        note.textContent = 'No album names for this artist yet — showing tracks.';
        return show('tracks');
      }
      note.textContent = kind === 'albums'
        ? 'No album names yet. They are read from the files themselves, in the '
          + 'background, the first time the player runs — try again shortly.'
        : 'Nothing matches.';
      $('az').textContent = '';
      return;
    }

    // An album arrives in ITS OWN order, not alphabetical order [REQ-VIS-190],
    // so the letter headings would neither be monotonic nor mean anything --
    // and an A-Z bar over twelve tracks is furniture. Suppressed together.
    const byLetter = !(kind === 'tracks' && filter.album);
    $('az').textContent = '';

    const anchors = new Map();
    let last = null;
    for (const r of rows) {
      const label = kind === 'tracks' ? r.title : r.name;
      const L = initial(label);
      if (byLetter && L !== last) {
        last = L;
        const head = document.createElement('li');
        head.className = 'letter';
        head.textContent = L;
        body.appendChild(head);
        if (!anchors.has(L)) anchors.set(L, head);
      }

      if (kind === 'tracks') {
        // A checkbox rather than three buttons per row: one set of verbs serves
        // the whole list [REQ-VIS-195], which keeps a row narrow enough to read
        // on a phone and makes queueing an album a single action.
        // In album order the number leads, because that is how the record is
        // read. Elsewhere it would be noise attached to an arbitrary position.
        const numbered = filter.album && r.track_no
          ? `${r.disc_no && r.disc_no > 1 ? r.disc_no + '-' : ''}${r.track_no}. ${r.title}`
          : r.title;
        const li = row(numbered,
                       filter.album ? (r.artist ?? '')
                                    : [r.artist, r.album].filter(Boolean).join(' — '),
                       r.plays ? `${r.plays}×` : '');
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.className = 'pick';
        box.value = r.passage_id;
        box.onclick = e => e.stopPropagation();
        box.onchange = armed;
        li.prepend(box);
        // A passage's own facts [REQ-VIS-270], one tap away without leaving
        // the checkbox underneath it -- its own click target, the same
        // `stopPropagation` reasoning the checkbox above already needs.
        const info = document.createElement('a');
        info.className = 'info';
        info.href = `/passage/${r.passage_id}`;
        info.textContent = 'ⓘ';
        info.title = 'passage details';
        info.onclick = e => e.stopPropagation();
        li.appendChild(info);
        // Tapping anywhere on the row ticks it: a 16-pixel checkbox is not a
        // phone target, and the whole row is.
        li.onclick = () => { box.checked = !box.checked; armed(); };
        body.appendChild(li);
      } else if (kind === 'albums') {
        body.appendChild(row(
          r.name, r.artist ?? '',
          `${plural(r.passages, 'track')}\n${r.plays}×`,
          () => { filter = { artist: r.artist ?? undefined, album: r.name };
                  show('tracks'); }));
      } else {
        body.appendChild(row(
          r.name, '',
          `${plural(r.passages, 'track')}\n${r.plays}×`,
          () => { filter = { artist: r.name }; show('albums'); }));
      }
    }
    if (byLetter) alphabet(anchors);
    armed();
    // The cap comes from the engine, which is what actually applies it.
    const cap = browseLimit;
    note.textContent = `${rows.length.toLocaleString()} ${kind}`
      + (cap && rows.length >= cap ? ` (showing the first ${cap.toLocaleString()})` : '');
  }

  for (const b of document.querySelectorAll('[data-kind]')) {
    // Switching axis clears the narrowing: "all albums" after picking one
    // artist is otherwise impossible to ask for.
    b.onclick = () => { filter = {}; show(b.dataset.kind); };
  }
  // Debounced, because every keystroke is a query over the whole library.
  $('q').oninput = () => {
    clearTimeout(timer);
    timer = setTimeout(() => show(), 200);
  };

  // MuLibPlay's three buttons went straight to their own page, so a skin can
  // still ask for one axis by name.
  const asked = new URLSearchParams(location.search).get('kind');
  const start = ['artists', 'albums', 'tracks'].includes(asked) ? asked : 'artists';
  Lempi.startBare()
    .then(() => Lempi.browse('limit').then(n => { browseLimit = n; }).catch(() => {}))
    .then(() => show(start));

  // Vipunen link [REQ-VIS-320]: hidden until the server confirms it is
  // actually worth showing -- a plain 404 on a build without
  // --features vipunen-support (`/vipunen/available` does not exist there at
  // all) fails this fetch exactly the same as any other reason it might
  // not be available, so no separate check for the feature is needed here.
  const vipunenLink = $('vipunen-link');
  fetch('/vipunen/available')
    .then(r => r.ok ? r.json() : { available: false })
    .then(body => { vipunenLink.hidden = !body.available; })
    .catch(() => {}); // stays hidden
  vipunenLink.onclick = async e => {
    e.preventDefault();
    if (vipunenLink.dataset.busy) return;
    vipunenLink.dataset.busy = '1';
    const was = vipunenLink.textContent;
    vipunenLink.textContent = 'starting Vipunen…';
    try {
      const body = await (await fetch('/vipunen/ensure', { method: 'POST' })).json();
      if (body.ok) {
        window.open(`http://127.0.0.1:${body.port}/`, '_blank');
      } else {
        alert(`Could not reach Vipunen: ${body.error}`);
      }
    } catch {
      alert('Could not reach Vipunen.');
    } finally {
      vipunenLink.textContent = was;
      delete vipunenLink.dataset.busy;
    }
  };
})();
