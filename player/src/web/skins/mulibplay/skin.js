// MuLibPlay's arrangement, driven by Lempi's socket.
//
// Two of its ideas map straight onto Lempi concepts and are kept as they were:
// the stacked station buttons with the live one in gold are the programme list,
// and "Autoselect by clock time" is the manual-programme override [SPEC-DIR-185].
//
// The original "Title by Artist from Album", its 200px cover and its play count
// are all back, now that the engine sends them [REQ-VIS-170]. Its
// browse-by-artist pages are still missing, needing endpoints that do not exist.
//
// The connecting words hide with what they introduce: a bare "by" with nothing
// after it reads as a fault, and about a third of this library has no embedded
// cover at all.
(() => {
  const $ = id => document.getElementById(id);
  const { clock } = Lempi.fmt;

  // The original submitted volume on mouseup and touchend for the same reason
  // core sends on release: a level per pixel of travel would be a request per
  // pixel of travel.
  const showVolume = Lempi.bindVolume($('volume'), $('volnum'));
  const showQueue = Lempi.bindQueue(
    $('queue'), q => (q.artist ? `${q.title} by ${q.artist}` : q.title), 'p',
    'nothing queued',
    // Title/artist become their own clickable spans, reaching the
    // preference panel `[REQ-VIS-285]`.
    { linkable: true, artistJoin: ' by ' });

  // Checked means the clock is choosing, which is Lempi's "no manual override".
  // Unchecking has to name something to switch TO -- "manual" is a programme
  // id, not a bare flag -- so it freezes on whatever is engaged right now
  // rather than doing nothing and leaving the box to spring back on the next
  // snapshot, which is what a `false` branch here used to be `[SPEC-DIR-185]`.
  let activeProgramId = null;
  $('autoclock').onchange = e => {
    if (e.target.checked) { Lempi.program('auto'); }
    else if (activeProgramId != null) { Lempi.program(activeProgramId); }
  };

  // Gold is "this is what is on" throughout the original -- transport and
  // stations alike -- so it is applied from one place here.
  const lit = (el, on) => { if (el) el.className = on ? 'buttonOn' : 'button'; };

  let stationSig = '';
  function renderStations(s) {
    const host = $('stations');
    // By engagement time, not by name -- the order the list is actually
    // used in, since that is the question "what comes on when" asks.
    const sorted = [...(s.programs || [])].sort((a, b) => a.start.localeCompare(b.start));
    const active = sorted.find(p => p.name === s.program);
    activeProgramId = active ? active.id : null;
    // Nothing to show, and two different reasons for it. The Director is built
    // after the web server binds, so for those seconds the list is empty
    // because nothing is KNOWN yet -- which used to render as a silently blank
    // panel and read as the feature having broken. `Lempi.directorReady` tells
    // the two apart from the snapshot rather than from a timer.
    const waiting = sorted.length === 0;
    const sig = waiting ? (Lempi.directorReady(s) ? 'empty:none' : 'empty:starting')
                        : sorted.map(p => p.id + p.name + p.start).join('|');
    if (sig !== stationSig) {
      stationSig = sig;
      host.textContent = '';
      if (waiting) {
        const note = document.createElement('p');
        note.className = 'stationnote';
        note.textContent = Lempi.directorReady(s)
          ? 'No programmes configured.'
          : 'Program Director starting…';
        host.appendChild(note);
      }
      for (const p of sorted) {
        const row = document.createElement('div');
        row.className = 'stationrow';
        const time = document.createElement('span');
        time.className = 'engagetime';
        time.textContent = p.start;
        const b = document.createElement('button');
        b.className = 'button';
        b.textContent = p.name;
        b.title = `from ${p.start}`;
        b.onclick = () => Lempi.program(p.id);
        row.append(time, b);
        host.appendChild(row);
      }
    }
    for (const b of host.querySelectorAll('.button, .buttonOn')) lit(b, b.textContent === s.program);
    // Dimmed once a manual pick has made the schedule inert -- the same
    // signal the checkbox itself gives, read here for the times beside it.
    for (const t of host.querySelectorAll('.engagetime')) t.classList.toggle('dim', s.program_manual);
    // Unticking has to name a programme to switch TO, and while the Director
    // is starting there is none: `activeProgramId` is null, so the box would
    // spring back on the next snapshot having done nothing at all.
    $('autoclock').disabled = waiting;
    $('autoclock').checked = !s.program_manual;
  }


  // Names are shown bare in this skin `[REQ-VIS-122]`. The provenance marks
  // -- MB, tag, file -- are a Lempi idea the original never had, and this skin
  // is a reproduction of its face. The claim is not abandoned, only moved: the
  // Lempi and WinAmp skins still carry it on every name.
  const plain = (el, text) => { if (el) el.textContent = text ?? ''; };

  // Hide the connecting word along with the value it introduces.
  const pair = (wordId, valueId, text) => {
    plain($(valueId), text);
    $(wordId).hidden = !text;
  };

  // Title/artist reach the preference panel when their own mbid is known
  // `[REQ-VIS-285]`, plain text otherwise -- unidentified audio, or an
  // uncredited artist, the same case that already leaves `artist` unset.
  const prefLink = (el, kind, id, text) => {
    if (!el) return;
    el.classList.toggle('pref-link', !!id);
    el.onclick = id ? () => Lempi.editPreference(kind, id, text) : null;
  };

  // Reads the same snapshot the display does, so a click can only act on
  // the track actually shown [REQ-VIS-225].
  let latest = null;
  Lempi.seekable($('bar'), () => latest);

  // Auto-scroll the lyrics window across the passage's run time -- a rough
  // guess, not real synchronisation. No timed data exists for these words
  // and none is fetched `[SPEC-LYR-060]`; this only assumes the text's
  // lines land roughly evenly between the 20% and 80% marks of the
  // passage -- most songs are still on their first verse a fifth of the
  // way in, and the window reaches its last line a little before the song
  // ends rather than exactly when the last line is sung. Driven by
  // `position_ms`/`duration_ms`, which every tick already carries, rather
  // than a separate timer -- so it freezes correctly on pause and jumps
  // correctly on seek for free. A repeated chorus is the usual reason the
  // guess drifts, and it is also why drifting there costs little: the text
  // the window shows late is still the right words.
  //
  // The scrollbar handle remains a full manual override, not just while
  // held: compared against the position this code itself last wrote rather
  // than a 'scroll' event, so there is no race with the browser's own event
  // timing to get wrong. Once the listener's own scroll disagrees with that,
  // this stops touching the box until the passage changes.
  const LYRICS_START_FRACTION = 0.2;
  const LYRICS_FINISH_FRACTION = 0.8;
  let lyricsPassage = null;
  let lyricsUserScrolled = false;
  let lyricsAutoTop = null;
  function autoScrollLyrics(s) {
    const box = $('lyrics');
    if (!box || box.hidden) return;
    if (s.passage_id !== lyricsPassage) {
      lyricsPassage = s.passage_id;
      lyricsUserScrolled = false;
      lyricsAutoTop = null;
    }
    if (lyricsAutoTop !== null && Math.abs(box.scrollTop - lyricsAutoTop) > 2) {
      lyricsUserScrolled = true;
    }
    if (lyricsUserScrolled || !s.duration_ms) return;
    const played = s.position_ms / s.duration_ms;
    const fraction = Math.min(1, Math.max(0,
      (played - LYRICS_START_FRACTION) / (LYRICS_FINISH_FRACTION - LYRICS_START_FRACTION)));
    const target = Math.max(0, Math.round((box.scrollHeight - box.clientHeight) * fraction));
    box.scrollTop = target;
    lyricsAutoTop = target;
  }

  Lempi.subscribe(s => {
    latest = s;
    plain($('title'), s.title ?? '—');
    prefLink($('title'), 'recording', s.mbid, s.title);
    pair('byword', 'artist', s.artist);
    prefLink($('artist'), 'artist', s.artist_mbid, s.artist);
    pair('fromword', 'album', s.album);
    $('plays').textContent = Lempi.fmt.plays(s.plays, s.last_played);
    Lempi.showArt($('art'), s.passage_id);
    // The original showed the back of the sleeve beside the front when it
    // had one -- 559 of its 675 albums did. Hidden by itself when absent.
    Lempi.showBackArt($('artback'), s.passage_id);
    Lempi.showLyrics($('lyrics'), s.passage_id);
    autoScrollLyrics(s);
    $('time').textContent = `${clock(s.position_ms)} / ${clock(s.duration_ms)}`;
    $('fill').style.width =
      s.duration_ms ? `${(s.position_ms / s.duration_ms) * 100}%` : '0';
    $('bar').classList.toggle('seekable', Boolean(s.can_seek && s.duration_ms));
    lit($('b-play'), s.playing);
    lit($('b-pause'), !s.playing);
    showVolume(s);
    showQueue(s);
    renderStations(s);
    $('why').textContent = s.why
      ? `${s.why.program ? s.why.program + ': ' : ''}weight ` +
        `${s.why.decayed_weight.toFixed(3)} of ${s.why.pool_weight.toFixed(1)} across ` +
        `${s.why.pool_size.toLocaleString()} candidates — ` +
        `${s.why.share_pct.toFixed(2)}% chance of being picked.`
      : '';
  });
})();
