// The lyric editor [SPEC-LYR-182] -- MuLibPlay's two-column page: the words of
// one passage on the left, a web search for the song on the right.
//
// It edits and saves ONE passage [SPEC-LYR-174]. Words the passage shows by a
// lower tier -- the other cut of the same audio, its recording, its work --
// are offered under the editor to read and copy from, and are never written
// from here [SPEC-LYR-186].
(() => {
  const $ = id => document.getElementById(id);
  // The look, and the socket: the page says which passage is playing while it
  // stays on its own [SPEC-LYR-190].
  Lempi.startWatching();

  const passageId = (() => {
    const m = location.pathname.match(/\/lyrics\/(\d+)\/edit/);
    return m ? Number(m[1]) : null;
  })();

  // What the passage's own entry holds, as last read or saved: leaving with
  // anything else in the boxes asks first [SPEC-LYR-188].
  let savedText = '';
  let savedMark = false;
  let sources = [];

  const dirty = () => $('editor').value !== savedText || $('nowords').checked !== savedMark;

  const say = (text, bad = false) => {
    $('status').textContent = text;
    $('status').classList.toggle('bad', bad);
  };

  // "Title by Artist from Album", as MuLibPlay headed its own page
  // [SPEC-PREF-090]; whichever halves the library knows.
  const named = v => [v.title || `Passage ${passageId}`,
                      v.artist && `by ${v.artist}`,
                      v.album && `from ${v.album}`].filter(Boolean).join(' ');

  // One line of the drop-down: which tier, what it is of, and where it came
  // from [SPEC-LYR-172].
  const label = s => {
    const from = s.imported
      ? (s.imported === 'mulibplay' ? 'imported from MuLibPlay' : `imported from ${s.imported}`)
      : `saved ${(s.updated_at || '').slice(0, 10)}${s.origin ? ` on ${s.origin}` : ''}`;
    if (s.tier === 2) return `Same audio, the ${s.kind} cut — ${from}`;
    const of = [s.title, s.artist && `by ${s.artist}`, s.album && `from ${s.album}`]
      .filter(Boolean).join(' ');
    return `${s.tier === 3 ? 'Same recording' : 'Same work'}: ${of || 'unnamed'} — ${from}`;
  };

  // The drop-down's choice, shown. "None" hides the box, and the editor takes
  // its height [SPEC-LYR-186].
  function showSource() {
    const v = $('source').value;
    const s = v === '' ? null : sources[Number(v)];
    $('reference').hidden = !s;
    $('copyall').hidden = !s;
    $('reference').textContent = s ? s.text : '';
    if (s) $('reference').scrollTop = 0;
  }

  function showOwn(own) {
    savedText = own ? own.text : '';
    savedMark = !!(own && own.no_words);
    $('editor').value = savedText;
    $('nowords').checked = savedMark;
    $('editor').disabled = savedMark;
  }

  function render(view) {
    $('heading').textContent = named(view);
    document.title = `Lempi — Lyrics — ${view.title || `passage ${passageId}`}`;
    $('about').href = `/passage/${passageId}`;

    // Empty when the passage has none of its own, whatever the player shows
    // for it [SPEC-LYR-184].
    showOwn(view.own);

    sources = view.sources || [];
    const pick = $('source');
    pick.replaceChildren();
    sources.forEach((s, i) => pick.append(new Option(label(s), String(i))));
    // Offered even when sources exist [SPEC-LYR-186].
    pick.append(new Option('None — no reference shown', ''));
    $('refrow').hidden = sources.length === 0;
    pick.value = sources.length ? '0' : '';
    showSource();

    // The search, already made when the page opens [SPEC-LYR-192]: `lyrics`,
    // the title and the artist. `igu=1` is what lets it be framed; the link
    // above the frame goes to the ordinary page.
    const words = ['lyrics', view.title, view.artist].filter(Boolean).join(' ');
    const q = encodeURIComponent(words);
    $('searchlink').href = `https://www.google.com/search?q=${q}`;
    $('searchlink').textContent = `open this search in a new window: ${words}`;
    $('search').src = `https://www.google.com/search?igu=1&q=${q}`;
  }

  // A 503 is the library busy for a moment, as the save says it
  // [SPEC-LYR-174]: said so, and asked again after `Retry-After`, a few times.
  async function load(tries = 0) {
    if (passageId == null) {
      $('heading').textContent = 'No passage id in the URL.';
      return;
    }
    const resp = await fetch(`/lyrics/${passageId}/sources`).catch(() => null);
    if (!resp || !resp.ok) {
      $('heading').textContent = `Passage ${passageId}`;
      $('save').disabled = true;
      if (resp && resp.status === 503 && tries < 4) {   // five asks in all, as the skins
        const wait = Math.max(1, Number(resp.headers.get('Retry-After')) || 2);
        say(`The library is busy; asking again in ${wait} s.`, true);
        setTimeout(() => load(tries + 1), wait * 1000);
        return;
      }
      say(resp && resp.status === 404
        ? 'Not found here — it may belong to another installation, or have been renumbered.'
        : resp && resp.status === 503
          ? 'The library is still busy. Reload the page to try again.'
          : 'The lyrics could not be read.', true);
      return;
    }
    $('save').disabled = false;
    render(await resp.json());
  }

  $('source').onchange = showSource;

  // All of the reference into the editor; part of it is an ordinary select
  // and copy. Asks before replacing what is there [SPEC-LYR-186].
  $('copyall').onclick = () => {
    const s = sources[Number($('source').value)];
    if (!s || $('editor').disabled) return;
    if ($('editor').value.trim() && !confirm('Replace what is in the editor with this text?')) return;
    $('editor').value = s.text;
    $('editor').focus();
    say('Copied. Not saved yet.');
  };

  // The text stays in the box under the mark, greyed, so unticking brings it
  // back [SPEC-LYR-188].
  $('nowords').onchange = () => { $('editor').disabled = $('nowords').checked; };

  $('save').onclick = async () => {
    say('Saving…');
    const resp = await fetch(`/lyrics/${passageId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: $('editor').value, no_words: $('nowords').checked }),
    }).catch(() => null);
    if (!resp || !resp.ok) {
      const why = resp ? (await resp.text().catch(() => '')) || `the server answered ${resp.status}` : 'no answer';
      say(`Not saved: ${why}`, true);
      return;
    }
    const { own } = await resp.json();
    showOwn(own);
    say(!own ? 'Cleared: this passage has no lyrics of its own now.'
      : own.no_words ? 'Saved: marked as having no lyrics.'
      : 'Saved.');
  };

  window.addEventListener('beforeunload', e => {
    if (!dirty()) return;
    e.preventDefault();
    e.returnValue = '';
  });

  // Says which passage is playing when it is another one, with a link to its
  // own page. It never follows by itself: the editor may hold unsaved text.
  Lempi.subscribe(s => {
    const other = s && s.passage_id != null && s.passage_id !== passageId;
    if ($('now').hidden !== !other) $('now').hidden = !other;
    if (!other) return;
    const name = [s.title || 'something', s.artist && `by ${s.artist}`].filter(Boolean).join(' ');
    if ($('nowname').textContent !== name) $('nowname').textContent = name;
    Lempi.lyricsLink($('nowlink'), s.passage_id);
  });

  load();
})();
