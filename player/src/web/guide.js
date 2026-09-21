// The in-app listener's guide `[REQ-VIS-310]` -- fetches one language's
// whole guide in one request, then switches tiers locally with no further
// fetches. A language change is the only thing that re-fetches.
(() => {
  const TIERS = [
    ['quickstart', 'Quick Start'],
    ['preferences', 'Preference Tuning'],
    ['importing', 'Importing & Refining Music'],
    ['empty_system', 'Starting From Nothing'],
    ['advanced', 'Advanced Features'],
    ['appendix', 'Appendix: How It Decides'],
  ];

  // Remembered per browser, not per player -- the exact reason `core.js`
  // keeps the skin choice in localStorage rather than anywhere server-side.
  const KEY = 'lempi.guide.lang';
  function remember(code) {
    try { localStorage.setItem(KEY, code); } catch { /* unremembered, not broken */ }
  }
  function remembered() {
    try { return localStorage.getItem(KEY); } catch { return null; }
  }

  // Best-effort match against what the browser already asks for, falling
  // back to the first registered language (always "en") -- never a hard
  // failure over a language nobody has translated yet.
  function pickLanguage(available) {
    const saved = remembered();
    if (saved && available.some(l => l.code === saved)) return saved;
    const nav = (navigator.language || '').toLowerCase();
    const short = nav.split('-')[0];
    const byShort = available.find(l => l.code.toLowerCase() === short);
    if (byShort) return byShort.code;
    return available[0]?.code ?? 'en';
  }

  const tiersEl = document.getElementById('tiers');
  const contentEl = document.getElementById('content');
  const langpick = document.getElementById('langpick');
  const errorEl = document.getElementById('loaderror');

  let content = null; // the currently-loaded language's {tier: html, ...}
  let active = null;

  function showTier(id) {
    if (!content || !(id in content)) return;
    active = id;
    contentEl.innerHTML = content[id];
    for (const btn of tiersEl.querySelectorAll('button')) {
      btn.setAttribute('aria-selected', btn.dataset.tier === id ? 'true' : 'false');
    }
    contentEl.scrollIntoView({ block: 'start' });
  }

  function buildNav() {
    tiersEl.innerHTML = '';
    for (const [id, label] of TIERS) {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      b.dataset.tier = id;
      b.onclick = () => {
        location.hash = id;
        showTier(id);
      };
      tiersEl.appendChild(b);
    }
  }

  async function loadContent(code) {
    const r = await fetch(`/guide/content/${encodeURIComponent(code)}`);
    if (!r.ok) throw new Error(`the server answered ${r.status}`);
    content = await r.json();
    remember(code);
    const wanted = location.hash.replace('#', '');
    showTier(TIERS.some(([id]) => id === wanted) ? wanted : (active ?? 'quickstart'));
  }

  async function start() {
    await Lempi.startBare();
    buildNav();
    const r = await fetch('/guide/langs');
    if (!r.ok) throw new Error(`the server answered ${r.status}`);
    const langs = await r.json();
    if (langs.length > 1) {
      langpick.hidden = false;
      langpick.innerHTML = langs
        .map(l => `<option value="${l.code}">${l.label}</option>`)
        .join('');
      langpick.onchange = () => loadContent(langpick.value);
    }
    const chosen = pickLanguage(langs);
    if (langpick) langpick.value = chosen;
    await loadContent(chosen);
  }

  start().catch(() => { errorEl.style.display = 'block'; });
})();
