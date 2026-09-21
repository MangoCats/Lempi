// Reviewing questionable recording ids [REQ-LIB-165].
//
// The queue here is only the CONTRADICTED passages: audio that AcoustID
// matched confidently to something other than the id the passage carries.
// Everything else is deliberately absent. `unmatched` in particular is not a
// finding -- it means AcoustID has no entry for the audio, which says nothing
// about whether the stored id is right -- and there are thousands of those.
// Putting them in front of a person would bury the real cases.
//
// Nothing here rewrites the library. A decision is recorded against the
// passage and `tools/apply_reviews.py` folds accepted ones in as a separate,
// deliberate step: reassigning an id changes what a passage *is*, and play
// history is keyed by recording, so doing it silently from a web click would
// re-attribute every past play of it.
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  Lempi.startBare();

  // A handoff lands on one passage, not the whole queue `[SPEC-SUI-150]`.
  // `passage_id` is a Vipunen-issued local sequence number, valid only for the
  // co-resident player that named it `[SPEC-DF-035]` -- this is the handoff
  // contract, not a general-purpose bookmark, so it is read once and never
  // written back to the URL.
  const wantPassage = (() => {
    const n = Number(new URLSearchParams(location.search).get('passage'));
    return Number.isInteger(n) && n > 0 ? n : null;
  })();

  const pct = s => (s == null ? '' : `${(s * 100).toFixed(1)}%`);

  // Every mbid on a card is a link to what it actually names `[SPEC-SUI-195]`
  // -- a person cannot judge a candidate by its id, only by the MusicBrainz
  // page it opens onto. No proxying: this is the browser's own operator
  // following the same link it could type by hand.
  const mbidLink = (mbid, kind = 'recording') => {
    const a = document.createElement('a');
    a.className = 'mbid';
    a.href = `https://musicbrainz.org/${kind}/${encodeURIComponent(mbid)}`;
    a.target = '_blank';
    a.rel = 'noopener';
    a.textContent = mbid;
    return a;
  };

  // The grades, worst first. `on` is whether it is shown to begin with:
  // `different-id` is 93% of the findings on this library and is a tidiness
  // problem, and `unverified` is not evidence of anything at all -- leaving
  // either on by default would bury the cases actually worth judging under
  // the ones that are not. Both stay one tap away.
  const GRADE = {
    'no-mbid':      { label: 'no MBID',       on: true,
                      why: 'no MusicBrainz id at all — a migration placeholder' },
    'wrong-song':   { label: 'wrong song',    on: true,
                      why: 'neither the title nor the performer matches' },
    'wrong-artist': { label: 'wrong artist',  on: true,
                      why: 'same title, different performer' },
    'wrong-title':  { label: 'wrong title',   on: true,
                      why: 'same performer, different title' },
    'different-id': { label: 'different id',  on: false,
                      why: 'the same recording under another MBID' },
    'unverified':   { label: 'unverified',    on: false,
                      why: 'AcoustID does not know this audio; not evidence either way' },
    // Reached only by a deep link `[SPEC-SUI-199]`, never by the queue itself
    // — `showFilters()` counts it, but nothing ever puts it `on` by default
    // since it can only appear one card at a time anyway.
    'on-demand':    { label: 'set by hand',   on: true,
                      why: 'opened directly, not flagged by any automatic check' },
  };
  const ORDER = Object.keys(GRADE);
  const showing = new Set(ORDER.filter(k => GRADE[k].on));
  // Decided cards are their own chip rather than a grade: a judgement can be
  // any severity, and what you want when looking for one is "the ones I have
  // answered", not "the wrong-artist ones I have answered".
  let showDecided = false;
  // How a recorded decision reads when the card is met again.
  const SAID = { kept: 'kept as it was', reassigned: 'reassigned',
                 deferred: 'left for later' };

  // One card: the two competing claims, then what can be done about it.
  function card(item) {
    const box = el('div', 'card');
    box.dataset.passage = item.passage_id;

    const head = el('div', 'head');
    // Very different findings arrive in this queue and they do not deserve the
    // same attention. Most of the time the audio matches a DIFFERENT RECORDING
    // OF THE SAME SONG -- a remaster, a 5.1 mix, a compilation's own entry --
    // which is tidiness. Occasionally the names disagree too, and that is a
    // passage playing under the wrong name. Saying which is which, and letting
    // one be looked at without the other, is most of the value of the page.
    const kind = el('span', `kind ${item.severity}`, GRADE[item.severity].label);
    kind.title = GRADE[item.severity].why;
    head.appendChild(kind);
    head.appendChild(el('span', 'score',
      `passage ${item.passage_id} · fingerprint match ${pct(item.score)}`));
    box.appendChild(head);

    const claims = el('div', 'claims');

    const mine = el('div', 'claim');
    mine.appendChild(el('h2', null, 'The library says'));
    mine.appendChild(el('div', 'name', item.title || '(untitled)'));
    mine.appendChild(el('div', 'sub',
      [item.artist, item.album].filter(Boolean).join(' — ') || '—'));
    mine.appendChild(mbidLink(item.stored_mbid));
    claims.appendChild(mine);

    const theirs = el('div', 'claim');
    theirs.appendChild(el('h2', null, 'The audio says'));
    // What kind of check this actually is, and when it ran -- found needed
    // live: a passage identified by a *different* method afterward (a
    // release-tracklist match, a hand pick) still shows its old AcoustID
    // verdict here unless that pass is re-run, and without a date on it
    // that reads as a live, current answer rather than the dated one it is.
    theirs.appendChild(el('div', 'sub',
      item.checked_at === 'never'
        ? 'AcoustID has not been run against this audio — this card was ' +
          'opened by hand, not because any automatic check found something ' +
          'to say. Searching below reassigns the recording directly; ' +
          'nothing here needs a fingerprint match first.'
        : `AcoustID's own audio fingerprint match, checked ${item.checked_at} — ` +
          'a separate, automatic check, not the same thing as a manual pick or ' +
          'a release-tracklist match made elsewhere. Nothing here re-verifies ' +
          'itself if the stored id changes by some other means after this date; ' +
          'only re-running the fingerprint pass does.'));
    const opts = el('ul', 'opts');
    // Radio rather than a button per candidate: picking is not deciding, and
    // a row of "use this one" buttons makes an irreversible-feeling choice out
    // of what should be a glance. A search result and a fingerprint
    // suggestion render through this one function, so choosing either is the
    // same action from the reviewer's side of the page `[SPEC-SUI-196]`.
    const seen = new Set();
    const addOption = s => {
      if (seen.has(s.mbid)) return;
      seen.add(s.mbid);
      const li = el('li');
      const input = document.createElement('input');
      input.type = 'radio';
      input.name = `pick-${item.passage_id}`;
      input.value = s.mbid;
      input.id = `pick-${item.passage_id}-${s.mbid}`;
      const label = document.createElement('label');
      label.htmlFor = input.id;
      label.appendChild(el('div', 'name', s.title || '(untitled)'));
      label.appendChild(el('div', 'sub', s.artist || '—'));
      label.appendChild(mbidLink(s.mbid));
      li.appendChild(input);
      li.appendChild(label);
      li.appendChild(el('span', 'pct', pct(s.score)));
      opts.appendChild(li);
    };
    for (const s of item.suggested || []) addOption(s);
    // Nothing to choose between. On this library that is 23 of the 44 `no-mbid`
    // cards and every `unverified` one -- and `no-mbid` leads the queue, so it
    // is most likely the first thing anyone meets. Say what the card is for
    // instead of leaving a dead control to be poked at -- removed the moment a
    // search actually finds something, since it stops being true then.
    let none = null;
    if (!(item.suggested || []).length) {
      none = el('li', 'none',
        item.severity === 'no-mbid'
          ? 'This passage carries no MusicBrainz id, and AcoustID does not ' +
            'recognise the audio either — so there is nothing here to reassign ' +
            'it to. It needs identifying by hand, or a better fingerprint.'
          : 'AcoustID has no entry for this audio. That is not evidence for or ' +
            'against the stored id — there is simply nothing to compare.');
      opts.appendChild(none);
    }
    theirs.appendChild(opts);

    // Searching MusicBrainz directly `[SPEC-SUI-196]`, `[REQ-LIB-180]` -- for
    // the cases the fingerprint queue cannot reach at all: self-released
    // audio with no AcoustID entry, or a remaster it has never indexed.
    // Song/Album kind selector `[SPEC-RIP-074]`: searching by *album* --
    // what a CD ripped by hand actually has printed on it -- finds the
    // release, then its own tracklist (fetched on pick) feeds the exact
    // same `addOption` every recording search and fingerprint suggestion
    // already does. Not a second commit path, only a second way in.
    const search = el('div', 'search');
    const kindSel = document.createElement('select');
    for (const [v, label] of [['recording', 'Song'], ['release', 'Album']]) {
      const o = document.createElement('option');
      o.value = v;
      o.textContent = label;
      kindSel.appendChild(o);
    }
    const box2 = document.createElement('input');
    box2.type = 'search';
    box2.placeholder = 'search MusicBrainz by title…';
    const go = el('button', null, 'Search');
    const status = el('span', 'sub');
    search.append(kindSel, box2, go, status);
    theirs.appendChild(search);

    const releaseResults = el('ul', 'opts release-results');
    releaseResults.hidden = true;
    theirs.appendChild(releaseResults);

    const pickRelease = async (mbid, title) => {
      status.textContent = `fetching ${title || mbid}'s tracklist…`;
      try {
        const r = await fetch(`/review/release-tracks/${encodeURIComponent(mbid)}`);
        const tracks = r.ok ? await r.json() : [];
        for (const t of tracks) {
          addOption({ mbid: t.mbid, title: t.title, artist: null, score: 1.0 });
        }
        if (tracks.length && none) { none.remove(); none = null; }
        if (tracks.length) { use.hidden = false; use.title = 'choose one of the matches above first'; }
        status.textContent = tracks.length
          ? `${tracks.length} track(s) from “${title || mbid}” added above`
          : 'that release has no readable tracklist';
      } catch {
        status.textContent = 'could not fetch that release’s tracklist';
      }
    };

    const runSearch = async () => {
      const text = box2.value.trim();
      if (!text) return;
      const kind = kindSel.value;
      go.disabled = true;
      status.textContent = 'searching…';
      releaseResults.hidden = true;
      releaseResults.textContent = '';
      try {
        const r = await fetch(`/api/musicbrainz/search?kind=${kind}&q=${encodeURIComponent(text)}`);
        const found = r.ok ? await r.json() : [];
        if (kind === 'release') {
          // A release is not itself a candidate to reassign to -- picking
          // one is a second step (its tracklist), not a direct pick.
          releaseResults.hidden = found.length === 0;
          for (const rel of found) {
            const li = el('li');
            const btn = el('button', 'pick-release', rel.title || '(untitled)');
            btn.type = 'button';
            btn.appendChild(el('span', 'sub', rel.artist || '—'));
            btn.onclick = () => pickRelease(rel.mbid, rel.title);
            li.appendChild(btn);
            releaseResults.appendChild(li);
          }
          status.textContent = found.length ? `${found.length} album(s) — pick one for its tracklist`
                                            : 'no results';
        } else {
          for (const s of found) addOption(s);
          if (found.length && none) { none.remove(); none = null; }
          if (found.length) { use.hidden = false; use.title = 'choose one of the matches above first'; }
          status.textContent = found.length ? `${found.length} result(s) added above`
                                            : 'no results';
        }
      } catch {
        status.textContent = 'search failed';
      }
      go.disabled = false;
    };
    go.onclick = runSearch;
    box2.addEventListener('keydown', e => { if (e.key === 'Enter') runSearch(); });

    claims.appendChild(theirs);
    box.appendChild(claims);

    // Which album to call it. A recording is on many releases -- the album,
    // the remaster, three compilations -- and without an answer the name is
    // picked by release date, which is a guess. Fetched only when a candidate
    // is chosen, because most cards are never reassigned.
    const albums = el('div', 'albums');
    albums.hidden = true;
    box.appendChild(albums);

    let releaseChoice = null;
    async function offerAlbums(mbid) {
      albums.textContent = '';
      albums.hidden = false;
      releaseChoice = null;
      albums.appendChild(el('h2', null, 'Call the album'));
      let list = [];
      try {
        const r = await fetch(`/review/releases/${encodeURIComponent(mbid)}`);
        if (r.ok) list = await r.json();
      } catch { /* offered, not required: the album can stay as it is */ }
      if (!list.length) {
        albums.appendChild(el('p', 'sub',
          'No releases known for this recording yet — the album will keep ' +
          'coming from the file’s own tag.'));
        return;
      }
      const sel = document.createElement('select');
      const none = document.createElement('option');
      none.value = '';
      none.textContent = 'leave the album as it is';
      sel.appendChild(none);
      for (const rel of list) {
        const o = document.createElement('option');
        o.value = rel.mbid;
        o.textContent = [rel.title, rel.date && rel.date.slice(0, 4),
                         rel.status, rel.track_count && `${rel.track_count} tracks`]
                          .filter(Boolean).join(' · ');
        if (rel.chosen) o.selected = true;
        sel.appendChild(o);
      }
      releaseChoice = () => sel.value;
      albums.appendChild(sel);
    }

    // A recording can be exactly right while MusicBrainz's own credit is
    // wrong `[SPEC-SUI-197]` -- a correction independent of whatever else
    // this card is about, so it is offered whether or not the recording
    // itself was ever reassigned. Only offered against a real MusicBrainz
    // id: a placeholder has no `recording_artists` row to correct yet.
    const isMbid = s => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);
    if (isMbid(item.stored_mbid)) {
      const fix = el('div', 'artistfix');
      fix.appendChild(el('h2', null, 'Fix the artist credit'));
      box.appendChild(fix);

      const renderFix = () => {
        fix.querySelectorAll(':scope > :not(h2)').forEach(n => n.remove());
        if (item.artist_review) {
          fix.appendChild(el('p', 'sub',
            `Corrected to “${item.artist_review}”` +
            (item.artist_review_applied ? ' — applied to the library.' : ' — saved, not yet applied.')));
          if (!item.artist_review_applied) {
            const undo = el('button', null, 'Undo the correction');
            undo.onclick = async () => {
              undo.disabled = true;
              const r = await fetch(`/review/${item.passage_id}/artist/reopen`, { method: 'POST' });
              if (r.ok) { delete item.artist_review; renderFix(); }
              else { undo.disabled = false; fix.appendChild(el('p', 'sub', await r.text())); }
            };
            fix.appendChild(undo);
          }
          return;
        }
        const abox = document.createElement('input');
        abox.type = 'search';
        abox.placeholder = 'search MusicBrainz for the right artist…';
        const ago = el('button', null, 'Search');
        const astatus = el('span', 'sub');
        const results = el('ul', 'opts');
        const row = el('div', 'search');
        row.append(abox, ago, astatus);
        fix.append(row, results);

        const confirmArtist = (mbid, name) => {
          const c = el('button', null, `Confirm “${name}”`);
          c.onclick = async () => {
            c.disabled = true;
            const q = new URLSearchParams({ mbid, name });
            const r = await fetch(`/review/${item.passage_id}/artist/correct?${q}`, { method: 'POST' });
            if (r.ok) { item.artist_review = name; item.artist_review_applied = false; renderFix(); }
            else { c.disabled = false; astatus.textContent = await r.text(); }
          };
          return c;
        };

        const runArtistSearch = async () => {
          const text = abox.value.trim();
          if (!text) return;
          ago.disabled = true;
          astatus.textContent = 'searching…';
          results.textContent = '';
          try {
            const r = await fetch(`/api/musicbrainz/search?kind=artist&q=${encodeURIComponent(text)}`);
            const found = r.ok ? await r.json() : [];
            for (const s of found) {
              const li = el('li');
              li.appendChild(el('div', 'name', s.title || '(unnamed)'));
              li.appendChild(mbidLink(s.mbid, 'artist'));
              li.appendChild(confirmArtist(s.mbid, s.title || s.mbid));
              results.appendChild(li);
            }
            astatus.textContent = found.length ? '' : 'no results';
          } catch {
            astatus.textContent = 'search failed';
          }
          ago.disabled = false;
        };
        ago.onclick = runArtistSearch;
        abox.addEventListener('keydown', e => { if (e.key === 'Enter') runArtistSearch(); });
      };
      renderFix();
    }

    const acts = el('div', 'acts');
    const said = el('span', 'said');

    // Hearing it is the only thing that settles a case the names cannot, and
    // the player is already there to do it. Reuses the queue verb rather than
    // growing an audio endpoint of its own [REQ-VIS-185].
    const play = el('button', null, 'Play now');
    play.title = 'send this passage to the player, to hear which it is';
    play.onclick = () => {
      Lempi.queue(item.passage_id, 'now');
      said.textContent = 'sent to the player';
    };

    const use = el('button', null, 'Use the match');
    use.disabled = true;
    const keep = el('button', null, 'Keep ours');
    const later = el('button', null, 'Decide later');

    const hasCandidates = (item.suggested || []).length > 0;
    // A control that can never work should not be offered at all. Presenting it
    // greyed says "not yet"; the truth is "not ever, on this card".
    if (!hasCandidates) use.hidden = true;

    opts.addEventListener('change', () => {
      const p = picked();
      use.disabled = !p;
      // Why it is disabled, where the disabled thing is. Without this the
      // precondition is invisible: the radios read as decoration, and the
      // button reads as broken.
      use.title = p ? 'record this as the right recording'
                    : 'choose one of the matches above first';
      if (p) offerAlbums(p.value);
    });
    use.title = hasCandidates ? 'choose one of the matches above first'
                              : 'nothing to reassign this to';
    const picked = () =>
      box.querySelector(`input[name="pick-${item.passage_id}"]:checked`);

    const settle = async (decision, mbid, phrase) => {
      for (const b of [use, keep, later]) b.disabled = true;
      const q = new URLSearchParams();
      if (mbid) q.set('mbid', mbid);
      const rel = releaseChoice && releaseChoice();
      if (rel) q.set('release', rel);
      const qs = q.toString() ? `?${q}` : '';
      const r = await fetch(`/review/${item.passage_id}/${decision}${qs}`,
                            { method: 'POST' });
      if (r.ok) {
        // Remembered on the item, not just on the element: toggling a filter
        // re-renders every card, and a settled one coming back armed would
        // invite deciding it twice.
        item.decided = phrase;
        box.classList.add('done');
        said.textContent = phrase;
        reopen.hidden = false;
        tally.decided++;
        showTally();
        showFilters();
      } else {
        // Re-arm rather than leaving a dead card: a failed write must not look
        // like a recorded decision. The server sends the reason as text, and
        // "already applied to the library" is exactly what has to be read.
        const why = await r.text().catch(() => '');
        said.textContent = why || `could not record that (${r.status})`;
        keep.disabled = later.disabled = false;
        use.disabled = !picked();
      }
    };

    // Undo. Withdrawing a judgement that has only been recorded is a delete;
    // one already written into the library is not, and the server refuses it
    // with the reason, which lands in `said` above.
    const reopen = el('button', null, 'Undo');
    reopen.title = 'withdraw this decision and put the card back in the queue';
    reopen.onclick = async () => {
      reopen.disabled = true;
      const r = await fetch(`/review/${item.passage_id}/reopen`, { method: 'POST' });
      if (r.ok) {
        delete item.decision;
        delete item.decided;
        tally.decided = Math.max(0, tally.decided - 1);
        showTally();
        showFilters();
        showCards();
      } else {
        said.textContent = (await r.text().catch(() => '')) ||
                           `could not undo that (${r.status})`;
        reopen.disabled = false;
      }
    };

    use.onclick = () => {
      const p = picked();
      if (p) settle('reassigned', p.value, 'reassigned');
    };
    keep.onclick = () => settle('kept', null, 'kept as it was');
    later.onclick = () => settle('deferred', null, 'left for later');

    acts.append(play, el('span', 'spacer'), said, use, keep, later, reopen);
    box.appendChild(acts);

    // A judgement already on record -- from this session or a previous one --
    // renders settled, with undo as the only live control.
    const already = item.decided || (item.decision && SAID[item.decision]);
    if (already) {
      box.classList.add('done');
      said.textContent = item.applied ? `${already}, applied to the library` : already;
      for (const b of [use, keep, later]) b.disabled = true;
    } else {
      reopen.hidden = true;
    }
    return box;
  }

  let tally = {};
  function showTally() {
    const t = tally;
    if (!t.ran) {
      $('tally').textContent =
        'The fingerprint pass has not been run against this library yet.';
      return;
    }
    const left = t.contradicted - t.decided;
    $('tally').innerHTML =
      `<b>${t.checked.toLocaleString()}</b> passages checked against their audio · ` +
      `<b>${t.confirmed.toLocaleString()}</b> confirmed · ` +
      `<b>${t.contradicted.toLocaleString()}</b> contradicted · ` +
      `<b>${Math.max(0, left).toLocaleString()}</b> still to review`;
  }

  async function load() {
    $('note').className = 'note';
    $('note').textContent = 'loading…';
    let data;
    try {
      const r = await fetch('/review/queue');
      if (!r.ok) throw new Error(`the server answered ${r.status}`);
      data = await r.json();
    } catch (e) {
      // A failed query must never render as "nothing to review" -- that is the
      // mistake that made the browse page look empty when it was broken.
      $('note').className = 'note bad';
      $('note').textContent = `Could not read the review queue: ${e.message}`;
      return;
    }
    tally = data.progress || {};
    showTally();
    items = data.items || [];
    // A handoff passage the queue itself would never surface -- never
    // fingerprinted, or simply not what someone wants it to say
    // `[SPEC-SUI-199]`. One extra request, only for the deep-link case, and
    // only when the queue didn't already carry it.
    if (wantPassage != null && !items.some(i => i.passage_id === wantPassage)) {
      try {
        const r = await fetch(`/review/passage/${wantPassage}`);
        if (r.ok) items.push(await r.json());
      } catch { /* falls through to showCards()'s own "nothing to do" message */ }
    }
    showFilters();
    showCards();
  }

  // One chip per grade, each carrying its own count, so the size of each kind
  // of problem is visible before deciding which to work through.
  const isDecided = i => Boolean(i.decision || i.decided);

  function showFilters() {
    const host = $('filters');
    host.textContent = '';
    const counts = {};
    for (const i of items) if (!isDecided(i)) counts[i.severity] = (counts[i.severity] || 0) + 1;
    for (const key of ORDER) {
      const n = counts[key] || 0;
      const b = el('button', `chip ${key}`, `${GRADE[key].label} ${n}`);
      b.title = GRADE[key].why;
      b.disabled = n === 0;
      b.setAttribute('aria-pressed', String(showing.has(key)));
      b.onclick = () => {
        if (showing.has(key)) showing.delete(key); else showing.add(key);
        b.setAttribute('aria-pressed', String(showing.has(key)));
        showCards();
      };
      host.appendChild(b);
    }
    // Decided, so a judgement can be found again and undone. Off by default:
    // the queue's job is to shorten as it is worked through.
    const n = items.filter(isDecided).length;
    const d = el('button', 'chip decided', `decided ${n}`);
    d.title = 'judgements already recorded — open one to undo it';
    d.disabled = n === 0;
    d.setAttribute('aria-pressed', String(showDecided));
    d.onclick = () => {
      showDecided = !showDecided;
      d.setAttribute('aria-pressed', String(showDecided));
      showCards();
    };
    host.appendChild(d);
  }

  function showCards() {
    const cards = $('cards');
    cards.textContent = '';
    $('note').textContent = '';

    // The handoff case: one card, or a plain explanation of why there is
    // none, never the whole queue's worth of unrelated findings.
    if (wantPassage != null) {
      $('filters').hidden = true;
      const item = items.find(i => i.passage_id === wantPassage);
      const note = $('note');
      if (item) {
        cards.appendChild(card(item));
        note.append(`Showing passage ${wantPassage} only — `);
        const all = el('a', null, 'show the whole queue');
        all.href = location.pathname;
        note.appendChild(all);
      } else {
        note.className = 'note';
        note.textContent = `Passage ${wantPassage} does not exist in this ` +
          'library, or the fingerprint-review tables have not been created yet.';
      }
      return;
    }

    const shown = items.filter(i =>
      isDecided(i) ? showDecided : showing.has(i.severity));
    for (const item of shown) cards.appendChild(card(item));

    $('note').textContent =
      !tally.ran
        ? 'Run tools/fingerprint_ids.py, then --merge, to populate this.'
        : !items.length
          ? 'Nothing left to review.'
          : !shown.length
            ? 'Nothing shown — every grade is switched off.'
            : `${shown.length} of ${items.length} shown. Worst first. ` +
              '"Unverified" means AcoustID has no entry for the audio, which ' +
              'is not evidence either way.';
  }

  let items = [];
  load();
})();
