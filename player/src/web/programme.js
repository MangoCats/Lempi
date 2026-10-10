// The Programme page [SPEC-PGM-400]: edit the programme this node runs, and
// save it under its own name or as a new programme [SPEC-PGM-405].
//
// Words per SPEC023 [ENT-PROGRAMME-010]: the programme is the whole day, a
// time slot one timed part of it, a seed a recording that sets a slot's
// sound. The page holds one working copy and sends it whole; the player
// checks it and answers with the programme as saved, or a sentence saying
// why not.
(() => {
  const $ = id => document.getElementById(id);
  Lempi.startBare();

  let saved = null;    // the programme as the player last answered
  let work = null;     // the copy being edited

  const clone = v => JSON.parse(JSON.stringify(v));
  const changed = () => saved && JSON.stringify(payload(work)) !== JSON.stringify(payload(saved));
  // What is sent: the slots as edited, each seed by its recording id.
  const payload = (p, name) => ({
    name: name !== undefined ? name : (p.name || '').trim() || null,
    slots: p.slots.map(s => ({ id: s.id ?? null, name: s.name, start: s.start || null,
                               seeds: s.seeds.map(x => x.mbid) })),
  });

  const note = (text, kind) => { const n = $('note'); n.textContent = text; n.className = 'note ' + (kind || ''); };

  const status = p => {
    const named = p.name ? `${p.name}` : 'Not named yet';
    const where = p.source === 'hub' && p.hub_version != null
      ? `the household's version ${p.hub_version}`
      : 'this node\'s own';
    return `${named} · ${where} · ${p.slots.length} time slot${p.slots.length === 1 ? '' : 's'}`;
  };

  const seedText = s => {
    if (!s.title) return s.mbid;
    return [s.title, s.artist, s.album].filter(Boolean).join(' — ');
  };

  // The range a slot is on air for, from the times as edited now.
  const ranges = slots => {
    const starts = slots.map(s => s.start).filter(Boolean).sort();
    return s => {
      if (!s.start) return 'chosen by hand only';
      if (starts.length < 2) return `from ${s.start}, all day`;
      const i = starts.indexOf(s.start);
      return `${s.start} – ${starts[(i + 1) % starts.length]}`;
    };
  };

  const button = (label, title, fn) => {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = label; if (title) b.title = title;
    b.onclick = fn; return b;
  };

  function render() {
    $('pname').value = work.name || '';
    $('status').textContent = status(saved);
    const box = $('slots');
    box.replaceChildren();
    const range = ranges(work.slots);
    if (!work.slots.length) {
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = 'No time slots: the player chooses with no programme to steer it.';
      box.appendChild(p);
    }
    work.slots.forEach((s, i) => {
      const card = document.createElement('div');
      card.className = 'card';
      const head = document.createElement('div');
      head.className = 'slot-head';
      const name = document.createElement('input');
      name.className = 'slot-name'; name.value = s.name; name.placeholder = 'time slot name';
      name.oninput = () => { s.name = name.value; touched(); };
      // A 24-hour text field, not `type=time`: a browser's time picker
      // follows its locale and showed "04:00 AM" above a range reading
      // "04:00 – 05:30". The player tidies "5:30" into "05:30".
      const start = document.createElement('input');
      start.className = 'slot-start'; start.value = s.start || '';
      start.placeholder = 'HH:MM'; start.inputMode = 'numeric'; start.maxLength = 5;
      start.title = 'When it comes on air, 24-hour; empty for one chosen only by hand';
      start.onchange = () => { s.start = start.value.trim() || null; touched(true); };
      head.append(name, start, button('Remove', 'Remove this time slot', () => {
        work.slots.splice(i, 1); touched(true);
      }));
      const r = document.createElement('div');
      r.className = 'range';
      r.textContent = range(s);
      const ol = document.createElement('ol');
      ol.className = 'seeds';
      if (!s.seeds.length) {
        const li = document.createElement('li');
        li.className = 'empty'; li.textContent = 'no seeds';
        ol.appendChild(li);
      }
      s.seeds.forEach((seed, j) => {
        const li = document.createElement('li');
        const t = document.createElement('span');
        t.textContent = seedText(seed);
        if (seed.title) t.title = seed.mbid;
        li.append(t,
          button('↑', 'Earlier', () => { if (j > 0) { [s.seeds[j - 1], s.seeds[j]] = [s.seeds[j], s.seeds[j - 1]]; touched(true); } }),
          button('↓', 'Later', () => { if (j < s.seeds.length - 1) { [s.seeds[j + 1], s.seeds[j]] = [s.seeds[j], s.seeds[j + 1]]; touched(true); } }),
          button('✕', 'Remove this seed', () => { s.seeds.splice(j, 1); touched(true); }));
        ol.appendChild(li);
      });
      card.append(head, r, ol);
      box.appendChild(card);
    });
    buttons();
  }

  // `redraw`: a change that moves ranges or rows; typing in a name does not.
  function touched(redraw) {
    note('');
    if (redraw) render(); else buttons();
  }
  function buttons() {
    const c = changed();
    $('save').disabled = !c;
    $('discard').disabled = !c;
  }

  async function load() {
    const r = await fetch('/programme/current').catch(() => null);
    if (!r || !r.ok) { note('The player did not answer; reload to try again.', 'bad'); return; }
    saved = await r.json();
    work = clone(saved);
    render();
  }

  async function save(name) {
    const r = await fetch('/programme/current', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload(work, name)),
    }).catch(() => null);
    if (!r) { note('The player did not answer; nothing was saved.', 'bad'); return false; }
    if (!r.ok) { note(await r.text() || `Not saved (${r.status}).`, 'bad'); return false; }
    saved = await r.json();
    work = clone(saved);
    render();
    note('Saved. The player takes the new time slots at its next choice.', 'good');
    return true;
  }

  $('pname').oninput = () => { work.name = $('pname').value; touched(); };
  $('addslot').onclick = () => {
    work.slots.push({ id: null, name: '', start: null, seeds: [] });
    touched(true);
    const names = document.querySelectorAll('.slot-name');
    if (names.length) names[names.length - 1].focus();
  };
  $('save').onclick = () => save();
  $('discard').onclick = () => { work = clone(saved); note(''); render(); };

  // Saving as a new programme: the one this node ran stays as it was
  // wherever else it lives -- the household's copy above all -- and this
  // node now runs the new one [SPEC-PGM-405].
  $('saveas').onclick = () => {
    $('newname').hidden = false;
    $('newnameinput').value = '';
    $('newnameinput').focus();
  };
  $('newnamecancel').onclick = () => { $('newname').hidden = true; };
  $('newnamesave').onclick = async () => {
    const n = $('newnameinput').value.trim();
    if (!n) { note('A new programme needs a name.', 'bad'); return; }
    const was = (saved.name || '').trim().toLowerCase();
    if (was && n.toLowerCase() === was) {
      note(`That is the name it already has; use Save to keep it as ${saved.name}.`, 'bad');
      return;
    }
    if (await save(n)) $('newname').hidden = true;
  };

  load();
})();
