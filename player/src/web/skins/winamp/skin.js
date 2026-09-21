// The awkward case, on purpose: a fixed-width appliance rather than a document,
// with its own type, geometry and a scrolling title. It needed nothing from
// core.js that the other two did not -- which is what makes the contract worth
// having rather than merely tidy.
(() => {
  const $ = id => document.getElementById(id);
  const { clock } = Lempi.fmt;

  const showVolume = Lempi.bindVolume($('volume'), $('volnum'));
  // Short forms: this panel is fixed-width at 10px monospace, and the long
  // labels core defaults to would simply not fit beside the "Prog" caption.
  const showProgram = Lempi.bindProgram($('prog'), 'auto (by clock)',
    { starting: 'director starting…', none: 'no programmes' });
  const showQueue = Lempi.bindQueue(
    $('queue'), q => (q.artist ? `${q.artist} - ${q.title}` : q.title));
  // Scroll only when it will not fit. A title that fits and scrolls anyway is
  // the habit that made these players tiring to watch.
  let shown = null;
  function renderTitle(text) {
    if (text === shown) return;
    shown = text;
    const m = $('marquee');
    m.classList.remove('roll');
    m.textContent = '';
    const span = document.createElement('span');
    span.id = 'title';
    span.textContent = text;
    m.appendChild(span);
    if (span.scrollWidth > m.clientWidth) {
      // Doubling the text is what makes the wrap seamless at -50%.
      span.textContent = text + '   ';
      m.appendChild(span.cloneNode(true));
      m.classList.add('roll');
    }
  }

  let progSig = '';


  Lempi.subscribe(s => {
    // "Artist - Title" is this idiom's own habit, and it happens to put the
    // more identifying half first when the line has to scroll.
    renderTitle(s.artist ? `${s.artist} - ${s.title}` : (s.title ?? '—'));
    Lempi.showArt($('art'), s.passage_id);
    $('time').textContent = clock(s.position_ms);
    $('stat').textContent =
      `${clock(s.duration_ms)} · ${s.queue_len ?? 0} queued` +
      (s.plays ? ` · ${s.plays} plays` : '') +
      (s.underrun_samples ? ` · ${s.underrun_samples} under` : '') + ' ';
    // Provenance rides in the stat row rather than the marquee: the marquee
    // scrolls and is cloned to make the wrap seamless, so a badge in there
    // would drift off the panel and then appear twice `[REQ-VIS-120]`. Only
    // the names this skin actually shows are qualified -- it has no album
    // line, so an album badge would be marking something invisible.
    $('stat').appendChild(Lempi.badge(s.title_source, 'title'));
    if (s.artist) $('stat').appendChild(Lempi.badge(s.artist_source, 'artist'));
    $('state').textContent =
      Lempi.status === 'connected' ? (s.playing ? '▶ playing' : '❚❚ paused')
                                   : '… reconnecting';
    $('fill').style.width =
      s.duration_ms ? `${(s.position_ms / s.duration_ms) * 100}%` : '0';
    showVolume(s);
    showProgram(s);
    showQueue(s);
    $('why').textContent = s.why
      ? `${s.why.program ? s.why.program + ': ' : ''}` +
        `${s.why.share_pct.toFixed(2)}% of ${s.why.pool_size.toLocaleString()} ` +
        `candidates, rank #${s.why.rank + 1}`
      : '';
  });
})();
