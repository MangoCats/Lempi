# HOWTO: deploy Lempi to the appliance

How to put a build of Lempi onto `lempipi` — the running Raspberry Pi
appliance — whether that's the latest commit or a specific tagged release.
Not how to build the Pi image itself (see [PI001](PI001-image-and-partitions.md))
and not the general dev build (see the repo root's `HOWTO.md`). One thing
gets done here: a binary is cross-compiled and put where the appliance's
`lempi` service runs it from.

Every command below is verified against this repository as it stands.

---

## 1. Prerequisites

- **Docker Desktop** (or a Linux `dockerd`), running. It supplies the
  aarch64 cross-compiler — nothing else on your machine needs to know how
  to target the Pi's CPU.
- **SSH access to `lempipi`** (or whatever host you're actually deploying
  to) — a working `ssh pi@lempipi` with no password prompt is what the
  script itself uses to restart the service and check what's running.

That's it. No local Rust toolchain, no manually-tracked cross-compiler
target, no separate checkout for building an older tag.

---

## 2. Deploy

One command, from anywhere in the repository:

```
LempiPi/deploy.sh
```

This builds whatever is currently checked out here and puts it on
`pi@lempipi`: cross-compiles in Docker, uploads, restarts the service, and
finishes by asking the appliance which commit it's actually running —
refusing to call the deploy done if the answer disagrees. If your checkout
has uncommitted changes, it stops and says so rather than deploying
something with no commit to point back to later; commit first, or pass
`ALLOW_DIRTY=1` if you really mean to test an edited tree on the appliance.

To deploy a specific tagged version instead of whatever's checked out:

```
LempiPi/deploy.sh pi-audio-stable-2026-08-16
```

This builds that tag in an isolated copy made just for the build — your own
checkout is never touched, so this is safe to run regardless of what you
currently have checked out or edited locally. `git tag` lists what exists.

To deploy to a different appliance, name it as the last argument:

```
LempiPi/deploy.sh pi-audio-stable-2026-08-16 pi@other-host
LempiPi/deploy.sh pi@other-host                    # latest, elsewhere
```

---

## 3. What "confirmed" means

The last line on success looks like:

```
deploy: confirmed -- pi@lempipi is running 05d501191f16
```

That hash is what the running binary itself reports back over SSH, not
just what was uploaded — the same "ask the process, don't trust a
checksum" check `install-player.sh` has always done, extended to also prove
it's the *commit you meant*. A build with local edits reports `+dirty` and
still has to match; there is never a passing case where what's running and
what you asked for silently differ. A build old enough to predate that
self-report (before `--version` existed) says so plainly instead of
guessing — the upload and restart are still verified, just not against a
commit hash.

If anything disagrees, the script says exactly what was expected vs. what
came back, and exits non-zero without pretending the deploy succeeded.

---

## Where to go next

- `build/install-player.sh` — the lower-level script `deploy.sh` wraps (named `deploy-player.sh`, under `LempiPi/`, until 2026-09-11); run it
  directly if you've already built the binary some other way (e.g. by
  hand, per `build/README.md`) and just need it uploaded.
- [PI001-image-and-partitions.md](PI001-image-and-partitions.md) — building the Pi image itself, not
  just what runs on it.
- [IMPL002-database-split.md](IMPL002-database-split.md) — the detailed
  design for splitting `lempi.db` into `library.db`/`listener.db`, PI001 §5
  worked out to the actual call sites. Designed and reviewed; not yet built.
- `build/README.md` — the manual two-step cross-compile process, and the
  Windows `CC` trap, in full.
- the repo root's `HOWTO.md` — building and running Lempi and Vipunen on a
  desktop dev machine.
