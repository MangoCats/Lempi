# HOWTO: build and run Lempi and Vipunen locally

Quick, practical steps for a desktop dev machine — not the appliance build
(see `LempiPi/` for that) and not the full architecture (see `README.md`
and `docs/` for that). Two things get built and run:

- **Lempi** (`player/`) — the Rust player. Plays music, serves the web UI.
- **Vipunen** (`tools/`) — the Python library builder and its browser console.
  Reads and writes the same library Lempi plays from — a pair of SQLite
  files, `library.db` and `listener.db` (§3); the two never talk to each
  other directly except through those files, plus one narrow handoff
  described at the end.

Every command below is verified against this repository as it stands.

---

## 1. Prerequisites

- **Rust** (stable toolchain) for Lempi.
- **Python 3** for Vipunen. Nothing needs `pip install` for the steps below —
  `tools/requirements.txt` is only for feature-extraction work (Stage-B
  models, AcousticBrainz dump processing), not for building a library or
  browsing the console.
- **On Windows, unset `CC` before building.** If it's set globally to a
  MinGW compiler, the bundled SQLite is compiled with MinGW while `rustc`
  links with MSVC, and the build fails on an unresolved `___chkstk_ms`:

  ```
  env -u CC cargo build --release
  ```

  (`build/README.md` has the full explanation and the cross-compile story
  for the Pi, if you need that instead.)

---

## 2. Build Lempi

```
cd player
env -u CC cargo build --release
```

This gives you `player/target/release/lempi` (`lempi.exe` on Windows) — the
plain appliance-equivalent build.

**If you also want the review page, the waveform editor, and MusicBrainz
search reachable from Vipunen**, build with the feature that carries them —
off by default because an appliance that never runs Vipunen has no reason to
carry the extra **3.05 MB** (measured 2026-09-11, `[SPEC-SUI-190]`) or the
`reqwest`/`rustls` stack it pulls in:

```
env -u CC cargo build --release --features vipunen-support
```

Either binary plays music and serves the ordinary web UI identically; the
feature only adds routes Vipunen's console links to.

---

## 3. Get a library

A library is **two SQLite files**: `library.db`, the catalogue (files,
passages, recordings, flavor, cover art), and `listener.db`, everything the
listener created (play history, preferences, programmes). Every installation
has run this way since 2026-09 `[PI023]`; the player refuses to start unless
both are named.

The usual way to get one is a copy of the hub's catalogue: a new node is sent
`library.db`, and the player creates an empty `listener.db` on its first
start. To build one from nothing instead, `sql/schema.sql` makes a single
file holding both halves' tables, which is inducted and then split:

```
python3 -c "import sqlite3; sqlite3.connect('build.db').executescript(open('sql/schema.sql', encoding='utf-8').read())"
python tools/ingest_folder.py build.db "/path/to/some/album" --commit
python tools/extract_library.py build.db
python tools/fingerprint_ids.py build.db
python tools/fingerprint_ids.py build.db --merge
python tools/split_database.py build.db     --library-out library.db --listener-out listener.db --commit
```

- Drop `--commit` to see what a step *would* do first — every one of these
  tools rehearses by default except where `--commit` is given.
- `fingerprint_ids.py` needs network access (AcoustID) and can take a
  while over a large folder; skip it for a quick local test and Lempi will
  still play the music, just without identified recordings for it yet.
- `split_database.py` never modifies its source, refuses to overwrite an
  output, and verifies row counts table-for-table, `integrity_check` and
  every index before reporting success.
- All of this is also reachable from Vipunen's own browser console (§5,
  the "jobs" and "export" pages) once it's running, rather than the CLI.

**Vipunen's tools take either half and find the other one**, so long as the
two sit in the same directory under those names. Where they do not — the
appliances keep them on separate mounts — name the second one:

```
python tools/load_occasions.py listener.db --library /srv/library/library.db
LEMPI_LIBRARY=/srv/library/library.db python tools/export_flags.py listener.db -o flags.json
```

---

## 4. Run Lempi

```
player/target/release/lempi --listener listener.db --library library.db --port 5720
```

Open `http://127.0.0.1:5720/` for the player UI. **`lempi --help` lists
every option**, and so does `--help` on any of the other binaries in
`player/src/bin/` (a few build only with a feature: `mpd`, `fbui` or
`echo-client`); the list below is a summary, not the definition
`[GDE-CLI-010]`. `--port`
defaults to `5720` if omitted; `--device NAME` picks an output device by a
case-insensitive substring match if the default one isn't what you want, and
`--list-devices` prints what this machine offers. `--library` is the
catalogue half; the player attaches it read-only and writes only the
listener half.

Every option is named — there are no bare arguments `[GDE-CLI-020]`. The old
form, with the listener database as the first bare word, is still read and
prints the corrected line on stderr `[GDE-CLI-040]`.

**Putting the listening back.** The player snapshots the listener half at
startup and hourly into `listener-backups/` beside it `[REQ-LIB-160]`. To
put one back, stop the player, then:

```
player/target/release/restore_listener --listener listener.db --list
player/target/release/restore_listener --listener listener.db --library library.db \
    --snapshot listener-backups/listener-<stamp>.db
```

The second line rehearses: it reports how many plays would come back and how
many would be re-pointed to renumbered passages, and writes nothing. Add
`--apply` to do it; the current state is saved first as a `prerestore-`
snapshot, which rotation never removes, so a wrong choice can be undone the
same way.

---

## 5. Run Vipunen

Vipunen's console is a plain Python script, no build step:

```
python tools/console.py library.db --root "/path/to/your/Music"
```

It finds the listener half beside the catalogue, so there is no second path
to pass here.

Open `http://127.0.0.1:5730/`. `--port` defaults to `5730`. `--root` is
repeatable and points at the audio folder(s) the "folder" view compares
against what the database claims — omit it and everything else still
works, just with an empty folder view.

The console opens the database `mode=ro`: it cannot write to your library
by itself. Everything that writes runs as a separate job, subprocessing the
same CLI tools shown in §3. The one job that writes the *hub's own* databases
is the star sync's commit (§7): it pauses the local Lempi, takes the console's
write lock, applies the patch, and reloads the player `[SPEC-STAR-120]`.

---

## 6. How they meet: the handoff

A few pages Vipunen links to — reviewing a questionable recording id,
editing a passage's waveform boundaries — are actually served **by
Lempi**, not by the console. The first time you open one of those links,
Vipunen checks whether a Lempi is already listening on `127.0.0.1:5720` and,
if not, launches one itself — built with `--features vipunen-support` (§2),
on Vipunen's own database path — and waits for it to answer before handing
you off. If no such binary exists yet, the page says so by name instead of
showing a dead link.

This means: build the `vipunen-support` binary (§2) if you want those pages
to work, and keep it at `player/target/release/lempi[.exe]` (or on `PATH`)
— that's where Vipunen looks for it.

---

## 7. Syncing the fleet (star sync)

If you have appliances (a Pi with a speaker) as well as this desktop, the
**star sync** brings their preferences, flags and catalogue together. This
machine is the **hub**: the source of truth, and the machine Vipunen runs on.
Each appliance is a **node**. The design is `docs/spec/SPEC046-star-sync.md`;
this is only how to drive it.

**Before a sync**

1. **Rebuild the local Lempi after every pull that touches `player/`** (§2,
   with `--features vipunen-support`). The console runs the binary at
   `player/target/release/lempi[.exe]`, and a stale one is the most common
   reason the hub is older than its nodes. `lempi --version` shows the build.
2. **Put a current player on each node** with `build/deploy-appliance.sh
   pi@HOST` (cross-compiles in Docker, installs it, restarts the player and
   checks the result). A node on an older build is warned about loudly,
   `OLD PLAYER`, and its catalogue is sent nothing `[SPEC-STAR-132]`.
3. **Write the plan, `fleet/star-plan.json`** (untracked): copy
   `fleet-example/star-plan.json` and fill in the hub (this machine's
   `data/listener.db` and `data/library.db`) and each node's `member`
   fingerprint. A node must first be **enrolled** in the mesh, by a person
   confirming a code on both sides `[SPEC-MTR-130]`. `LEMPI_STAR_PLAN`
   names another plan file.
4. Start the console on the hub's own databases:
   `python tools/console.py data/library.db`.

**In the console: `http://127.0.0.1:5730/sync`**

1. **Tick the nodes this run takes part with.** The hub always takes part and
   has no box. A node you leave unticked is not read, not started, not written
   and keeps its last-sent record; nothing is ticked until you do it, and a
   run needs at least one node `[SPEC-STAR-130, 134]`.
2. Press the stages in order: **Snapshot, Merge, Prepare patches, Rehearse,
   Commit.** Rehearse changes nothing anywhere. Commit stays disabled until
   every item has a verdict and every chosen node has rehearsed clean.
3. **Items** are changes the merge would otherwise discard (two nodes changed
   the same thing differently, or one deleted what another edited). Each shows
   its default, the most recent change; pick another per item, or press
   **Approve all** to take every default. One-sided changes carry over without
   asking, and a node that already matches the hub has nothing to approve.
4. A big catalogue patch goes in parts and takes minutes on a Pi; the page
   shows progress. Close other users of the hub's databases first.

**The same from a terminal**, stage by stage, to read a run between steps:
`python tools/star_sync.py fleet/star-plan.json snapshot --nodes lempi02w`,
then `merge`, `items`, `patch`, `rehearse`, `commit` (`--help` lists them all).
The hub is included in each. From a terminal the hub is patched by the
command itself, so **close the console and the local Lempi first** (or let the
console's own job do it); the command says so and stops if one is open.

---

## Where to go next

- Once Lempi is running (§4), it serves its own in-app user's guide at
  `/guide`, also reachable from a **Help** link in every skin — a
  multi-tiered, listener-facing document (quick start through advanced
  features and an algorithm appendix), distinct from this file and from
  `README.md`, both of which stay developer-facing.
- `README.md` — what Lempi is and why.
- `docs/GUIDE001-lineage-and-lessons.md` — start here for the project's
  own history and design lessons.
- `docs/spec/SPEC013-vipunen-console.md` — the console's own design.
- `docs/IMPL003-vipunen-console-build.md`, `IMPL006`, `IMPL007` — what's
  actually built, stage by stage, with measured results.
- `docs/spec/SPEC046-star-sync.md` and `SPEC058-catalogue-patch-by-natural-key.md` — the star sync and its catalogue patch; `fleet/FLEET001-the-fleet.md` — which nodes exist and what each runs.
- `LempiPi/` — building and deploying the Raspberry Pi appliance image.
- `build/README.md` — cross-compilation and the Windows `CC` trap in full.
