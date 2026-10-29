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
  // { artist, album } -- the narrowing, not the search. With an album, the
  // artist is only the way in: the trail, and what opens ticked.
  let filter = {};
  let timer = null;
  // An opened album's tracks, where a tick queues the album passage
  // [SPEC-ALB-010]. Everywhere else a tick queues the radio one, as before.
  let asAlbum = false;
  // Set by tapping an album, and spent by the listing it opens: an album
  // opens ticked [SPEC-ALB-025], and a search typed inside it does not
  // tick it all again.
  let opening = false;

  // The heading must be derived the same way the server sorts, or the letters
  // stop being monotonic: strip a leading "The" here while the ORDER BY does
  // not, and "The Beatles" emits a stray B in the middle of the Ts. So this
  // takes the first character of the very key the server sorted by -- an
  // artist's sort name, "Beatles, The", where it has one [REQ-VIS-182] --
  // as it stands. Everything outside A-Z lands under '#', which is where
  // "10,000 Maniacs" and "'Til Tuesday" actually sort.
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
    // Back the way it was reached: an album opened from the Album tab has no
    // artist in its trail, and its way out is the albums it was picked from.
    const via = filter.artist ? 'artists' : 'albums';
    const all = document.createElement('a');
    all.textContent = via === 'artists' ? 'All artists' : 'All albums';
    all.onclick = () => narrow(via, {});
    c.appendChild(all);
    if (filter.artist) {
      c.append(' › ');
      const a = document.createElement('a');
      a.textContent = filter.artist;
      a.onclick = () => narrow('albums', { artist: filter.artist });
      c.appendChild(a);
    }
    if (filter.album) c.append(' › ' + filter.album);
  }

  // Every narrowing goes through here: a row tapped, a link followed, a
  // crumb. The search is cleared on the way, since it was typed for the
  // listing being left. Kept, "beat" found the Beatles and then narrowed
  // their albums to those named like it, and an album followed from a
  // searched track opened as the one track that matched. Switching tabs
  // keeps it: that is the same search, asked of another axis.
  function narrow(next, f, open = false) {
    filter = f;
    opening = open;
    clearTimeout(timer);
    $('q').value = '';
    show(next);
  }

  // A link inside a row's second line, to somewhere else in Browse: its own
  // tap target, as the passage-details link is, so following it does not
  // tick the row it sits on.
  function away(text, cls, go) {
    const a = document.createElement('a');
    a.className = cls;
    a.href = '#';
    a.textContent = text;
    a.onclick = e => { e.preventDefault(); e.stopPropagation(); go(); };
    return a;
  }

  // One row shape for all three listings: a name, an optional second line, some
  // numbers, and a tap. Only the contents differ. The second line is text, or
  // a list of text and elements -- a track's artist or album is a link.
  function row(title, sub, nums, onTap) {
    const li = document.createElement('li');
    const name = document.createElement('div');
    name.className = 'name';
    const b = document.createElement('b');
    b.textContent = title;
    name.appendChild(b);
    const parts = [].concat(sub || []).filter(Boolean);
    if (parts.length) {
      const s = document.createElement('span');
      s.append(...parts);
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
        b.onclick = () => jump(target);
      } else {
        b.disabled = true;
      }
      bar.appendChild(b);
    }
    stickUnderBar();
  }

  // Where a letter's heading sits in the list, worked out from the first row
  // under it -- not from the heading, which is sticky. `scrollIntoView` on a
  // sticky heading measured where it was stuck, so every heading already
  // scrolled past read as in view: the first tap worked and a tap back up the
  // list did nothing. Reproduced in Chrome at phone width on lempi02w,
  // 2026-10-01: M, then C, stayed in the Ms.
  function jump(head) {
    const first = head.nextElementSibling || head;
    const y = first.getBoundingClientRect().top + window.scrollY
      - $('az').offsetHeight - head.offsetHeight;
    window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
  }

  // A heading sticks just under the bar, however tall the bar is: on a phone
  // its 27 letters wrap to two or three rows -- 107 px measured at 400 px
  // wide -- and a fixed offset left the heading hidden behind it.
  function stickUnderBar() {
    document.documentElement.style.setProperty('--az-h', `${$('az').offsetHeight}px`);
  }
  window.addEventListener('resize', stickUnderBar);

  const plural = (n, one) => `${n.toLocaleString()} ${one}${n === 1 ? '' : 's'}`;

  const picked = () =>
    [...document.querySelectorAll('.pick:checked')].map(b => b.value);

  // A box carries two answers: `own`, what a tick by hand queues, and
  // `whole`, what Select all queues -- empty where it leaves the track out.
  // They differ only for a held track [SPEC-ALB-050]: chosen by hand it is
  // the held passage itself, in a whole-album play its stand-in or nothing.
  const tick = (box, on, as = box.dataset.own) => { box.checked = on; box.value = as; };
  const whole = () => [...document.querySelectorAll('.pick')].filter(b => b.dataset.whole);
  const wholeTicked = () => {
    const w = whole();
    return w.length > 0 && w.every(b => b.checked && b.value === b.dataset.whole);
  };

  // The verbs are useless without a selection, and a button that does nothing
  // when pressed teaches nothing. Disabled until there is something to act on.
  function armed() {
    const n = picked().length;
    for (const b of document.querySelectorAll('#verbs button:not(#v-all)')) b.disabled = !n;
    let said = n ? `${plural(n, 'track')} selected` : '';
    if (asAlbum) {
      const all = wholeTicked();
      $('v-all').disabled = all || !whole().length;
      // What the whole album leaves out is said, not silently missing.
      const out = document.querySelectorAll('.pick:not([data-whole])').length;
      if (all && out) said += ` · ${out} left out`;
    }
    $('picked').textContent = said;
  }

  // The whole album, in its running order [SPEC-ALB-020] -- the whole-album
  // play [REQ-PD-127]. It replaces the selection rather than adding to it.
  $('v-all').onclick = () => {
    for (const b of document.querySelectorAll('.pick')) {
      if (b.dataset.whole) tick(b, true, b.dataset.whole);
      else tick(b, false);
    }
    armed();
  };
  // Its opposite, as a button of its own: an album opens ticked
  // [SPEC-ALB-025], so starting from nothing is one tap rather than one per
  // track. Disabled, like the verbs, while nothing is ticked.
  $('v-none').onclick = () => {
    for (const b of document.querySelectorAll('.pick')) tick(b, false);
    armed();
  };

  for (const [label, action, said] of Lempi.VERBS.place) {
    $('v-' + action).onclick = () => {
      const ids = picked();
      if (!ids.length) return;
      // One request carrying the list, in the order it is displayed. Sent as
      // separate requests they would arrive interleaved, and inserting each at
      // the same place would reverse them.
      Lempi.queue(ids.join(','), action).then(() => {
        $('note').textContent =
          `${plural(ids.length, 'track')}${asAlbum ? ' as on the album' : ''} — ${said}.`;
        for (const b of document.querySelectorAll('.pick:checked')) tick(b, false);
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
    asAlbum = kind === 'tracks' && !!filter.album;
    $('v-all').hidden = $('v-none').hidden = !asAlbum;
    const fresh = opening;
    opening = false;
    const body = $('rows');
    const note = $('note');

    // An opened album is asked for whole. The artist it was reached through
    // stays in the trail and decides what opens ticked [SPEC-ALB-025], but
    // does not narrow the listing: a compilation's tracks are each credited
    // to their own artist, so narrowing by one showed a third of the album
    // from the artist, and nothing at all from the Album tab, where the
    // artist was the release's "Various Artists" [REQ-VIS-183].
    const ask = filter.album ? { album: filter.album } : filter;
    let rows;
    try {
      rows = await Lempi.browse(kind, { ...ask, q: $('q').value.trim() });
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

    // An album opened shows its sleeve, front and back, above its first track
    // [REQ-VIS-184] -- at the MuLibPlay player's 200px, side by side, stacked
    // on a phone too narrow for two. Through the first track's passage: the
    // cover lookup resolves it through the release that names the album.
    // A side the catalogue lacks is left out, and with neither the row goes.
    if (kind === 'tracks' && filter.album && rows.length) {
      const sleeves = document.createElement('li');
      sleeves.className = 'sleeves';
      for (const [side, alt] of [['', 'front cover'], ['/back', 'back cover']]) {
        const img = document.createElement('img');
        img.alt = alt;
        img.src = `/art/${rows[0].passage_id}${side}`;
        img.onerror = () => { img.remove(); if (!sleeves.querySelector('img')) sleeves.remove(); };
        sleeves.appendChild(img);
      }
      body.appendChild(sleeves);
    }

    const anchors = new Map();
    let last = null;
    for (const r of rows) {
      const label = kind === 'tracks' ? r.title : (r.sort || r.name);
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
        // In an opened album a tick queues the album passage, which keeps
        // the record's own gaps and plays straight into the next, rather
        // than the radio one rotation plays [SPEC-ALB-010]. A track without
        // one falls back to its radio passage and says so [SPEC-ALB-040];
        // Select all leaves it out, since a hidden intro joined to its track
        // is one, and its audio is already in the album passages around it.
        let own = r.passage_id, all = null, says = '';
        if (asAlbum) {
          if (r.album_passage == null) {
            says = 'radio cut — not in Select all';
          } else {
            own = all = r.album_passage;
            // Held: Select all plays an equal passage from another file in
            // its place, or leaves it out [SPEC-HOLD-070]. By hand, the
            // held passage itself, which a person may still choose.
            if (r.album_hold) {
              all = r.stand_in ? r.stand_in.passage_id : null;
              says = r.stand_in
                ? `held — Select all plays it from ${r.stand_in.album || 'another file'}`
                : 'held — not in Select all';
            }
          }
        }
        // A track's artist is a way out to that artist's own page, the one
        // the Artist tab leads to: a compilation names five acts, and any
        // of them may be the one worth following [REQ-VIS-197].
        const by = r.artist && away(r.artist, 'artist',
                                    () => narrow('albums', { artist: r.artist }));
        let sub;
        if (filter.album) {
          sub = [by, by && says ? ' · ' : '', says];
        } else {
          // Outside an opened album its album is the way to it, opened as
          // an album tapped from where this listing stands [REQ-VIS-198]:
          // an artist's tracks keep the artist, for the trail and what
          // opens ticked [SPEC-ALB-025]; all tracks or a search open it
          // whole.
          const on = r.album && away(r.album, 'album',
                                     () => narrow('tracks', { artist: filter.artist, album: r.album }, true));
          sub = [by, by && on ? ' — ' : '', on];
        }
        const li = row(numbered, sub, r.plays ? `${r.plays}×` : '');
        if (r.album_hold) li.title = `held: ${r.album_hold}`;
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.className = 'pick';
        box.dataset.own = own;
        if (all != null) box.dataset.whole = all;
        box.value = own;
        box.onclick = e => e.stopPropagation();
        box.onchange = () => { box.value = box.dataset.own; armed(); };
        // Opened, it is ticked as Select all would tick it, from the Album
        // tab; from an artist, only that artist's own tracks [SPEC-ALB-025],
        // by the same displayed credit that put the album in their list.
        if (fresh && all != null && (!filter.artist || r.artist === filter.artist)) {
          tick(box, true, all);
        }
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
        // Make this recording a seed of a time slot [SPEC-PGM-420]: the
        // shared preference panel, opened beneath this row. An unidentified
        // passage names no recording, and cannot be one.
        if (r.mbid) {
          const seed = document.createElement('a');
          seed.className = 'info seed';
          seed.href = '#';
          seed.textContent = '☆';
          seed.title = 'seed of a time slot, and preferences';
          seed.onclick = e => {
            e.preventDefault();
            e.stopPropagation();
            const panel = document.getElementById('pref-panel');
            if (panel) li.after(panel);
            Lempi.editPreference('recording', r.mbid, r.title);
          };
          li.appendChild(seed);
        }
        // Tapping anywhere on the row ticks it: a 16-pixel checkbox is not a
        // phone target, and the whole row is.
        li.onclick = () => { tick(box, !box.checked); armed(); };
        body.appendChild(li);
      } else if (kind === 'albums') {
        const li = row(
          r.name, r.artist ?? '',
          `${plural(r.passages, 'track')}\n${r.plays}×`,
          // The artist this album was reached through, not its credit: that
          // is a label, and as a filter it matched none of a compilation's
          // tracks [REQ-VIS-183].
          () => narrow('tracks', { artist: filter.artist, album: r.name }, true));
        // An artist's albums, each with its front [REQ-VIS-184]. Lazy, so a
        // long discography fetches only what scrolls into view; a missing
        // cover keeps its square, so the names stay in one column.
        if (filter.artist && r.passage) {
          const img = document.createElement('img');
          img.className = 'thumb';
          img.alt = '';
          img.loading = 'lazy';
          img.src = `/art/${r.passage}`;
          img.onerror = () => img.classList.add('none');
          li.prepend(img);
        }
        body.appendChild(li);
      } else {
        body.appendChild(row(
          r.name, '',
          `${plural(r.passages, 'track')}\n${r.plays}×`,
          () => narrow('albums', { artist: r.name })));
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
