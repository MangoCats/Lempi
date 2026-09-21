# IMPL008: Targeted Remote Reads at Edit-Open Time

**Implementation Guide — the build order for [SPEC022 §2](spec/SPEC022-flag-and-edit-sync.md#2-a-console-gui-for-1-its-measured-cost-and-a-design-to-keep-it-fast)'s `[SPEC-DF-116..118]`, built 2026-08-29 as specified below**

> **Related:** [SPEC022](spec/SPEC022-flag-and-edit-sync.md) — read this first, in full, it is short · `tools/apply_changes.py` (the anchors and `classify()` this reuses) · `tools/console.py`, `tools/console_web/profile.html` (where this lands) · commit `abb41c0` (the pull/push GUI this builds past)

---

## 1. What already exists — do not re-derive, read SPEC022 instead

A GUI for pulling lempipi's flags and pushing local edits back is already built and committed (`tools/jobs.py`'s `remote-pull`/`remote-push`, the "Sync with a remote" section in `tools/console_web/flags.html`). It works, but both directions cost a full `scp` of lempipi's ~1.16 GB database — **measured live at over an hour**, because lempipi's WiFi sustains roughly 270 KB/s. The write itself was never the expensive part (already a small SQL patch); the *read* was, because `apply_changes.py`'s three-way merge needs the remote's current value to classify a change as fast-forward, no-op, or conflict.

**Editing itself lives in Lempi, not Vipunen.** `console.py`'s `/profile/:id` route is, and stays, read-only — the actual id-review/boundary editing UI is Lempi's own web UI (port 5720), reached via a handoff link from Vipunen's profile page.

## 2. What this builds: one targeted read, at the right moment

**The read itself, per `[SPEC-DF-116]`:** lempipi already carries the `sqlite3` CLI (confirmed live: 3.40.1, `-json` output works — `ssh pi@lempipi "sqlite3 -json /var/lempi/listener.db '...'"` returns a JSON array directly — **path corrected 2026-09-12**: lempipi split its database on 2026-09-10 and `/srv/library/library.db` is now a 0-byte leftover, so the original path would have returned nothing and said nothing. The anchors named below resolve in the listener half; catalogue reads want `--library`'s `/srv/library/library.db` instead). Use it for a **per-item** query instead of a database copy: when Vipunen's profile page for one passage/recording is opened, fetch just that item's current remote value via the identical anchors `apply_changes.py` already resolves locally (`audio_md5`+kind+start/end for a boundary, `recording_mbid` for an artist/id review) — not a copy, a handful of bytes.

**Offering it, per `[SPEC-DF-117]`:** if the remote's value differs from what Vipunen's own library shows, offer it as the new starting point *before* the "Review in Lempi" handoff — not after, not only at push time. Accepting it is a small, targeted **local** write (the one deliberate exception to "the console never writes the library"), and it must follow the existing discipline: not `console.py`'s HTTP handler touching SQLite directly, but a dedicated subprocess tool doing exactly that one row's update — the same shape every other write in this console already takes (`jobs.py`'s `_spawn`).

**Degrading gracefully, per `[SPEC-DF-118]`:** if lempipi doesn't answer, show *"could not reach lempipi — editing against the local baseline only"* and proceed. Never block. Household WiFi to this hardware has already proven unreliable all through the Sonos investigation on this same branch history — a check that can't run must not stop someone from working.

## 3. Build order — built as specified

1. **`tools/remote_peek.py`** — the one new read primitive everything else depends on. Given a remote (`user@host:/path`) and one anchor (recording mbid, or `audio_md5`+kind+start/end), builds the right `SELECT` and runs it via `ssh <host> sqlite3 -json <path> "..."`, returning the parsed row or `None`. Mirrors `apply_changes.py`'s own `current_recording()`/`current_boundary()`/`current_artist()` for what "current value" means per kind — proven identical against a real fixture, not just argued, by `tools/test_remote_peek.py`. Unreachable/timeout is handled explicitly (`BatchMode=yes`, a connect timeout, and a subprocess timeout as backstop) — never a hang, per `[SPEC-DF-118]`.
2. **`tools/accept_remote_basis.py`** — given a local db, an anchor, and the remote's value just fetched (`--value`, the exact JSON `remote_peek.py` printed), writes it as the new local value for exactly that row. Small and boring on purpose; it exists only so `console.py` itself never opens the library for writing. Reuses `apply_changes.py`'s own `resolve_passage()` rather than reimplementing anchor resolution.
3. **`console.py` + `profile.html` wiring** — opening a profile calls `remote_peek.py` (as a subprocess) against the configured remote (`STATE["jobs"].get_remote()`) for the two identities this page can hand off (id review, boundary editing). Three outcomes render: unreachable (warning, proceed), no divergence (nothing shown), diverged (offered, with an "accept lempipi's value" button that POSTs to `/api/profile/:id/accept-remote`, which resolves the anchor server-side and runs `accept_remote_basis.py` through the job model — `jobs.py`'s new `accept-remote` kind — the same posture every other write in this console already takes).

A boundary whose *identity itself* moved on lempipi (its own `start_ms`/`end_ms` changed independently) is not discoverable by this mechanism — the anchor that would find it is exactly what moved. This is a known, accepted gap, not an oversight: the anchor is the one `apply_changes.py` already resolves locally, unchanged, and a passage that has not been re-trimmed (an id correction, or an amplitude re-analysis that leaves `start_ms`/`end_ms` alone) is unaffected. `[SPEC-DF-117]`'s own full-copy read at push time is still the backstop for whatever this narrower check misses.

## 4. What "done" looks like — a measurable claim, not a demo

- A profile page opened while lempipi is reachable and in agreement shows nothing extra. **Automated** — `tools/test_console_remote_status.py`.
- A profile page opened for an item deliberately diverged on lempipi (set a flag or edit directly there, or against a scratch copy first) shows the difference and correctly writes the accepted value locally when taken. **Automated** at the tool layer — `tools/test_accept_remote_basis.py`, `tools/test_console_remote_status.py`; the HTTP round trip itself still wants one live click-through against a real profile page.
- A profile page opened with lempipi unreachable (pull the network cable, or point the remote config at a bad host) shows the warning and does not block reaching Lempi's own editor. **Automated** the failure-handling half — `tools/test_remote_peek.py`'s faked `subprocess.run`; not yet run against a genuinely unplugged lempipi.
- After accepting a remote value and then making a local edit, a subsequent `remote-push` (already built) classifies it as fast-forward, not conflict — this is the actual point of the feature, worth checking end to end once, not assumed from the pieces working separately. **Automated end to end**, real SQLite fixtures standing in for lempipi and the desktop — `tools/test_accept_remote_basis.py::test_closes_the_loop`.

None of the above has been run against the real appliance yet (`ssh`/`sqlite3 -json` against actual lempipi hardware) — everything here is proven against fixtures standing in for it, the same gap `[SPEC-DF-114]` closed for the full-copy jobs by measuring live. That live pass is still owed.

---

**Traceability:** implements `[SPEC-DF-116..118]` · depends on `abb41c0` (already committed) · built 2026-08-29 (`tools/remote_peek.py`, `tools/accept_remote_basis.py`, `tools/jobs.py`, `tools/console.py`, `tools/console_web/profile.html`, `console.css`) · tested (`tools/test_remote_peek.py`, `tools/test_accept_remote_basis.py`, `tools/test_console_remote_status.py`) · live verification against real lempipi hardware still owed
