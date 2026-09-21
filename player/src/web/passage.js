// One passage's own facts [REQ-VIS-270] -- the appliance-side sibling of
// Vipunen's profile page. Read-only, no editing affordance here: this is
// "what is this", not "fix this" -- the boundary editor and id review stay
// where they already are, behind vipunen-support, on the desktop's own
// co-resident Lempi [SPEC-SUI-138].
(() => {
  const $ = id => document.getElementById(id);
  Lempi.startBare();

  const passageId = (() => {
    const m = location.pathname.match(/\/passage\/(\d+)/);
    return m ? Number(m[1]) : null;
  })();

  // A real MusicBrainz id, or one of this project's own local placeholders
  // (`local:audio:<md5>`, `local:audio:<md5>:<start_ms>`, the migration's
  // `local:track:N`) -- shown as plain text, not linked, since there is
  // nowhere on musicbrainz.org for any of those to resolve to.
  const isMbid = s => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s || '');

  const fmt = ms => {
    if (ms == null) return '—';
    const s = ms / 1000;
    const m = Math.floor(s / 60);
    const r = (s - m * 60).toFixed(3);
    return `${m}:${r.padStart(6, '0')}`;
  };

  const dl = (box, pairs) => {
    box.replaceChildren();
    for (const [k, v] of pairs) {
      if (v == null) continue;
      const dt = document.createElement('dt');
      dt.textContent = k;
      const dd = document.createElement('dd');
      dd.textContent = v;
      box.append(dt, dd);
    }
  };

  async function load() {
    if (!passageId) {
      $('title').textContent = 'No passage id in the URL.';
      return;
    }
    const resp = await fetch(`/passage/${passageId}/info`).catch(() => null);
    if (!resp || !resp.ok) {
      $('title').textContent = `Passage ${passageId}`;
      $('note').textContent = 'Not found here — it may belong to a different '
        + 'installation, or a rescan has since renumbered it.';
      return;
    }
    const p = await resp.json();

    const title = (p.recordings[0] && p.recordings[0].title) || p.tag_title || '(untitled)';
    const artist = (p.recordings[0] && p.recordings[0].artists[0]
                     && p.recordings[0].artists[0].name) || p.tag_artist;
    $('title').textContent = title;
    $('subtitle').textContent = [artist, p.tag_album].filter(Boolean).join(' — ');
    document.title = `Lempi — ${title}`;

    if (p.sibling) {
      const a = document.createElement('a');
      a.href = `/passage/${p.sibling.passage_id}`;
      a.textContent = `→ this file's own ${p.sibling.kind} cut`;
      $('sibling').replaceChildren(a);
      $('sibling').hidden = false;
    }

    dl($('span'), [
      ['kind', p.kind],
      ['span', `${fmt(p.start_ms)} – ${fmt(p.end_ms)}`],
      ['lead in / out', p.lead_in_ms == null && p.lead_out_ms == null
        ? 'not yet analysed' : `${p.lead_in_ms ?? 0} / ${p.lead_out_ms ?? 0} ms`],
      // Directly under lead in/out, not after gain -- lead and fade are the
      // two edges of a passage's own boundary, and reading side by side
      // against Vipunen's own profile page [REQ-VIS-270] means matching its
      // row order here, not just its wording.
      ['fade in / out', `${p.fade_in_ms} ms ${p.fade_in_curve} / ${p.fade_out_ms} ms ${p.fade_out_curve}`],
      ['gain', p.gain_db == null ? null : `${p.gain_db.toFixed(2)} dB`],
      ['boundary source', p.boundary_src],
    ]);
    // A real link, appended as its own dt/dd rather than folded into `dl()`'s
    // plain key/value loop, which only ever writes text -- one more fact
    // about the passage, not a separate action, so it stays in this list.
    const whyDt = document.createElement('dt');
    whyDt.textContent = 'why this passage?';
    const whyDd = document.createElement('dd');
    const why = document.createElement('a');
    why.href = `/why/${p.passage_id}`;
    why.textContent = 'see the selection reasoning →';
    whyDd.appendChild(why);
    $('span').append(whyDt, whyDd);

    dl($('file'), [
      ['path', p.path],
      ['format / duration', `${p.format || '?'}, ${fmt(p.duration_ms)} (${p.duration_ms} ms)`],
      ['audio md5', p.audio_md5],
    ]);

    const box = $('recordings');
    box.replaceChildren();
    if (!p.recordings.length) {
      box.append(Object.assign(document.createElement('p'), {
        className: 'empty', textContent: 'No recording linked to this passage yet.',
      }));
    }
    for (const r of p.recordings) {
      const card = document.createElement('div');
      card.className = 'card';
      const head = document.createElement('div');
      const mbidEl = isMbid(r.mbid)
        ? Object.assign(document.createElement('a'), {
            href: `https://musicbrainz.org/recording/${r.mbid}`,
            target: '_blank', rel: 'noopener', textContent: r.mbid,
          })
        : Object.assign(document.createElement('span'), { textContent: r.mbid });
      head.append(
        Object.assign(document.createElement('b'), { textContent: r.title || '(untitled)' }),
        document.createTextNode(' — '), mbidEl,
        document.createTextNode(' '),
        Object.assign(document.createElement('span'), {
          className: 'weight', textContent: `weight ${r.weight}, via ${r.source}`,
        }),
      );
      card.appendChild(head);
      if (r.artists.length) {
        const artists = document.createElement('div');
        artists.className = 'artists';
        artists.textContent = r.artists.map(a => a.name).join(', ');
        card.appendChild(artists);
      }
      box.appendChild(card);
    }
  }

  load().catch(() => { $('note').textContent = 'Something went wrong loading this passage.'; });
})();
