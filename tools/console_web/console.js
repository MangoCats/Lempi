// Shared bits of the Vipunen console [SPEC013].
//
// Small on purpose. The console has no live state to push -- a library does not
// change while you look at it unless a job is running, and jobs are stage 3 --
// so there is no socket here and no snapshot dispatch. When stage 3 adds jobs
// it adds an EventSource, which is the smaller mechanism that fits a
// one-directional progress stream [SPEC-SUI-030].

const S = {
  async get(path) {
    const r = await fetch(path);
    const d = await r.json();
    // A failed query reports rather than rendering as an empty library. That
    // exact mistake blanked the player's browse page twice [REQ-LIB-165].
    if (d && d.error) throw new Error(d.error);
    return d;
  },

  n(x) { return (x ?? 0).toLocaleString(); },

  clock(ms) {
    const t = Math.round((ms || 0) / 1000);
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
  },

  // `ingest_decisions.decided_at` is a real Unix epoch -- seconds since
  // 1970 UTC, written that way by every stage that fills the table
  // (`analyze_amplitude.py`, `choose_release.py`, `suggest_release.py`,
  // `ingest_folder.py`) -- unlike the naive-local strings the review
  // tables use elsewhere in this console, an epoch is unambiguous, so
  // converting it to this browser's own local wall clock is exactly
  // right here, not the mistake `system.html`'s own `agoText` was
  // written to avoid repeating.
  when(x) {
    const n = Number(x);
    if (!x || !Number.isFinite(n)) return x || '';
    const d = new Date(n * 1000);
    const p = v => String(v).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
           `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  },

  el(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') e.className = v;
      else if (k === 'text') e.textContent = v;
      else e.setAttribute(k, v);
    }
    for (const k of kids) if (k !== null && k !== undefined) e.append(k);
    return e;
  },

  // An id that is not an MBID is not a defect -- it means no MusicBrainz entry
  // exists for this audio [IMPL-SUI-025]. Say that, rather than flagging it red.
  isMbid(s) {
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s || '');
  },

  fail(where, e) {
    // `where` is a CSS selector everywhere this was first written, but a
    // caller that already holds the element (a per-candidate result box
    // with no id of its own, e.g.) has no selector to give it -- accepting
    // either avoids inventing one just to satisfy this signature.
    const box = typeof where === 'string' ? document.querySelector(where) : where;
    if (box) box.replaceChildren(S.el('p', { class: 'err', text: String(e.message || e) }));
  },

  // A live job's log while it runs, then whatever the caller wants once it
  // stops -- factored out of flags.html's own watchSync()/showResult(),
  // which a second job-launching page (profile.html's reanalyze button) was
  // about to duplicate rather than share [SPEC-SUI-214].
  //
  // The status line is jobs.html's heartbeat, brought here because its
  // absence cost a person a long wait on 2026-10-01: a send to one speaker
  // sat queued behind another's 16-minute copy -- the runner does one job
  // at a time, and a queued job emits nothing -- and the panel showed an
  // empty box. Now a queued job names what it waits behind, and a quiet
  // running one says how long it has been quiet.
  //
  // And a Stop beside it, on every page that watches a job [REQ-LIB-132]: a
  // send to a speaker ran for minutes with no way to end it but the Jobs
  // page. The runner decides how -- at once, at the step's next safe point,
  // or after a step that must not be left half-done -- and says which in the
  // log below.
  watchJob(id, box, onDone) {
    const status = S.el('p', { class: 'empty', style: 'font-size:.85rem;padding:.2rem 0', text: 'connecting…' });
    const stop = S.el('button', { class: 'chip', text: 'Stop', title: 'stop this job at its next safe point' });
    stop.onclick = async () => {
      if (!confirm('Stop this job? It stops at its next safe point: what it has finished stays done, '
        + 'and a step that must not be left half-done is let finish first. The log says what happens.')) return;
      stop.disabled = true;
      stop.textContent = 'stopping…';
      try {
        const r = await (await fetch(`/api/jobs/${id}/stop`, { method: 'POST' })).json();
        if (!r.stopped) { stop.textContent = 'it had already finished'; }
      } catch (e) { stop.disabled = false; stop.textContent = 'Stop'; alert(`Could not ask it to stop: ${e.message}`); }
    };
    const head = S.el('div', { style: 'display:flex;gap:.6rem;align-items:center;flex-wrap:wrap' }, status, stop);
    const log = S.el('div', { class: 'list' });
    box.replaceChildren(head, log);
    let last = Date.now(), stage = null, heard = false, ahead = null, polled = 0;
    const tick = async () => {
      if (!heard && Date.now() - polled > 3000) {
        polled = Date.now();
        try {
          const jobs = await S.get('/api/jobs');
          const me = jobs.find(j => j.job_id === id);
          const run = jobs.find(j => j.state === 'running' && j.job_id !== id);
          ahead = me && me.state === 'queued'
            ? (run ? `job ${run.job_id} (${run.kind}${S.peerOf(run)})` : 'the job before it') : null;
        } catch (e) { /* the next tick asks again */ }
      }
      const s = Math.round((Date.now() - last) / 1000);
      status.textContent = ahead && !heard ? `queued — waiting for ${ahead} to finish; one job runs at a time`
        : (stage ? `running: ${stage}` : 'waiting to start') + (s >= 3 ? ` — last output ${s} s ago, still working` : '');
    };
    const beat = setInterval(tick, 1000);
    tick();
    const es = new EventSource(`/api/jobs/${id}/stream`);
    es.onmessage = async m => {
      const e = JSON.parse(m.data);
      heard = true;
      last = Date.now();
      if (e.kind === 'stage') stage = e.text;
      if (e.kind === 'done') {
        clearInterval(beat);
        status.textContent = `finished: ${e.text}`;
        stop.hidden = true;
      }
      if (e.kind === 'counts') return;
      const line = e.kind === 'stage' ? `── ${e.text} ──`
        : e.kind === 'done' ? `── finished: ${e.text} ──` : e.text;
      log.append(S.el('div', {
        style: e.kind === 'error' ? 'color:var(--bad)'
          : (e.kind === 'stage' || e.kind === 'done') ? 'color:var(--accent)' : '',
        text: line,
      }));
      log.scrollTop = log.scrollHeight;
      if (e.kind === 'done') {
        es.close();
        const job = await S.get(`/api/jobs/${id}`);
        if (onDone) onDone(job, box);
      }
    };
  },

  // ", for lempi02w" when a job's target names a speaker -- the sends and
  // the exports do -- so "waiting for job 47" says which one.
  peerOf(job) {
    try {
      const p = JSON.parse(job.target).peer;
      return p ? `, for ${p}` : '';
    } catch (e) {
      return '';
    }
  },

  // The default `onDone`: flat numeric tiles -- what most job results
  // already are (flags pull/push's matched/already/unmatched, and so on).
  // A caller whose `result` is structured differently passes its own
  // `onDone` to `watchJob` instead of using this.
  tileResult(job, box) {
    if (!job.result) return;
    box.append(S.el('div', { class: 'tiles' },
      ...Object.entries(job.result).map(([k, v]) =>
        S.el('div', { class: 'tile' }, S.el('b', { text: S.n(v) }), S.el('span', { text: k })))));
  },

  // The handoff `[SPEC-SUI-140]`, `[SPEC-SUI-150]`, `[SPEC-SUI-170]`. Vipunen asks
  // the operating system to ensure a co-resident player is there -- never Lempi
  // itself `[SPEC-SUI-025]` -- then embeds its own page for this exact passage.
  // `[SPEC-SUI-155]`: embed it where it embeds, link it where it does not.
  // Detecting a blocked frame from script is unreliable across browsers, so the
  // new-tab link is offered unconditionally alongside the frame rather than
  // only after a failure is inferred -- cheap, and correct either way.
  //
  // One function, two callers: id review and the waveform editor are the same
  // handoff shape aimed at a different Lempi route `[SPEC-SUI-135]` -- a
  // second mechanism would just be this one, copied.
  //
  // Shared since 2026-10-18: the flags page re-opens a pending edit with it
  // too, so it lives here rather than in one page `[REQ-VIS-275]`.
  handoffBox(passageId, route, label) {
    const box = S.el('div', { class: 'handoff' },
      S.el('p', { class: 'empty', text: 'Starting the local player…' }));

    (async () => {
      // Only a request that never got an answer is "could not reach". An answer
      // carrying an error is the server's own reason, said as itself -- found
      // 2026-10-18: a stale-build refusal was prefixed "Could not reach
      // Vipunen's own server", which it had just reached [SPEC-SUI-227].
      let r;
      try {
        r = await (await fetch('/api/handoff/ensure', { method: 'POST' })).json();
      } catch (e) {
        box.replaceChildren(S.el('p', { class: 'err' },
          `Could not reach Vipunen's own server to ask: ${e.message}`));
        return;
      }
      if (!r || !r.ok) {
        box.replaceChildren(S.el('p', { class: 'err' },
          (r && r.error) || 'Vipunen answered without a local Lempi, and gave no reason.'));
        return;
      }
      const url = `http://localhost:${r.port}${route}`;
      box.replaceChildren(
        S.el('p', {},
          S.el('a', { href: url, target: '_blank', rel: 'noopener' }, 'Open in a new tab ↗'),
          r.started ? S.el('span', { class: 'pill ok', style: 'margin-left:.6rem',
                                      text: 'started a local Lempi' }) : null),
        S.el('iframe', {
          src: url, class: 'handoff-frame', title: `Lempi — ${label}`,
          // Cross-origin, deliberately [SPEC-SUI-145]: Vipunen cannot see inside
          // this frame, and does not ask to. The round trip closes through the
          // shared database on the next scan, not through the browser.
        }));
    })();

    return box;
  },
};

// A saved-but-not-yet-applied edit `[REQ-VIS-275]` looked identical to a
// pushed one from this very console until it wasn't -- Lempi's editor
// commits a draft and changes nothing else, and nothing anywhere said so
// unless a person already knew to look. Every page loads this file, so this
// is the one place a badge actually reaches all of them, not just the page
// someone happened to be on when they went looking.
(async () => {
  const nav = document.querySelector('header nav');
  if (!nav) return;
  let d;
  try {
    d = await S.get('/api/pending');
  } catch (e) {
    return;   // a console with no library open yet has nothing to count
  }
  if (!d.total) return;
  // Points at the button that does it, not at a command to type: the flags
  // page grew one `[REQ-LIB-175]`, and a tooltip still naming the CLI sends
  // a person to a terminal for something two clicks away. The tools are
  // still what runs -- the page says so, and says it first.
  const pill = S.el('a', {
    class: 'pill warn', href: '/flags#apply',
    title: 'Saved but not yet applied to the library -- open the flags page '
      + 'to review them and apply, or run tools/apply_boundary_reviews.py / '
      + 'tools/apply_reviews.py --commit by hand',
  }, `⚠ ${S.n(d.total)} pending edit${d.total === 1 ? '' : 's'}`);
  nav.appendChild(pill);
})();
