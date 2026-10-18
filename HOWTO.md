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

## Quick start on this machine

Both, on the hub's own library in `data/` — on a fresh clone there is none
there yet, so read [`data/README.md`](data/README.md) first. From **Git
Bash**, in the repository root:

```
build/deploy-local.sh
nohup python tools/console.py data/library.db > data/vipunen-launch.log 2>&1 &
```

- `build/deploy-local.sh` stops any running Lempi, builds it with
  `--features vipunen-support` — the build Vipunen's review and editor links
  need (§2, §6) — starts it on `data/listener.db` and `data/library.db`, and
  checks the build it reports against `HEAD`. Run it again after any pull
  that touches `player/`.
- **Vipunen from Git Bash, not PowerShell**: its jobs find `ffmpeg`, `ssh`
  and `docker` through the `PATH` they inherit (§6).
- **Open** Lempi at `http://127.0.0.1:5720/` and Vipunen at
  `http://127.0.0.1:5730/`.
- **Which build is running**: `curl -s http://127.0.0.1:5720/build` for
  Lempi, the **System** page (`/system`) for Vipunen. Both should name the
  commit `git log -1` does; when they do not, §6 says which one to move.
- **Stopping**: Vipunen with **shut down this instance** on its System page;
  Lempi with `taskkill //IM lempi.exe //F`, as the script does. That stops
  every `lempi.exe` — check first that none is someone else's test build
  (§6 has how to stop one port's alone).

The numbered sections are the reference: other paths, other machines, and
what each step does.

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
same way. `--snapshot` takes a name as `--list` prints it, or a path.

Without a shell, the Lempi skin's Settings has a **Listening backups** row
that does the same: it stages the restore, and the player puts it back the
next time it starts.

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
is the star sync's commit ([GUIDE039](docs/GUIDE039-running-the-star-sync.md)): it pauses the local Lempi, takes the console's
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

**When Vipunen and Lempi disagree.** The handoff refuses a Lempi built from
a different commit than the one Vipunen started from `[SPEC-SUI-227]`, and
says which is older. Bring that one to the checkout — and if the message
says the checkout has moved on past both, bring both:

- **Lempi.** Rebuild it with the feature (§2). Stop the running one — on
  Windows, `netstat -ano | grep ':5720 .*LISTENING'` gives its PID, and
  `taskkill //PID <pid>` from Git Bash stops it. Reopen the page; Vipunen
  starts the new build itself.
- **Vipunen.** Check no job is running, then press **shut down this
  instance** on its System page, and start it again **from Git Bash**, in
  the repository root (§5). Not PowerShell's `Start-Process`: the console's
  jobs find `ffmpeg`, `ssh` and `docker` through the `PATH` it inherits,
  and from PowerShell `bash` once resolved to the WSL launcher — a send to
  an appliance failed `execvpe(/bin/bash)` (2026-10-14).

A Lempi on that port may be someone else's test build — the message says
*with uncommitted changes* when it is. Ask before stopping one of those.

---

## 7. Syncing the fleet (star sync)

Moved to [GUIDE039](docs/GUIDE039-running-the-star-sync.md) on 2026-10-18: it is
fleet work, not running things on one machine. The number is kept so that
references to "HOWTO §7" still lead somewhere.

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
- [GUIDE039](docs/GUIDE039-running-the-star-sync.md) — running the star sync; `docs/spec/SPEC046-star-sync.md` and `SPEC058-catalogue-patch-by-natural-key.md` — its design and its catalogue patch; `fleet/FLEET001-the-fleet.md` — which nodes exist and what each runs.
- `LempiPi/` — building and deploying the Raspberry Pi appliance image.
- `build/README.md` — cross-compilation and the Windows `CC` trap in full.
