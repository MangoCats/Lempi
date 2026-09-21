# Working in this repository

Conventions that are **not discoverable from the code**, and that cost real
time when they are unknown. Everything here is written because it actually
went wrong, not as general advice.

---

## 1. `cargo fmt` is off limits

**This repository is hand-formatted. Do not run `cargo fmt`.** Use
`cargo clippy` when you want feedback on the code — it reports real problems
and changes nothing on its own.

This is enforced mechanically by [`player/rustfmt.toml`](player/rustfmt.toml)
(`disable_all_formatting = true`), so a `cargo fmt` is now a no-op rather than
a disaster. The number behind that: measured 2026-09-11, `cargo fmt --check`
wanted to rewrite **959 diff lines** across the crate.

The reason is not taste. The code carries unusually long explanatory
comments — measurements, dated findings, why a constant has the value it has —
and their line breaks are part of how they read. rustfmt reflows them, and the
resulting mechanical diff buries whatever real change it is mixed with.

**If you are one of several agents in this repo, assume the others have not
read this.** The rule existed only in one contributor's memory until
2026-09-11, which is exactly how it got broken: an agent working on Vipunen ran
`cargo fmt`, reformatted `player/src/web/vipunen.rs`, and had no way to know a
convention existed. A convention only one participant can see is not one.

## 2. Unset `CC` before building the player

`libsqlite3-sys` fails to link if a global `CC` is set. Every build script
here already does this — see `env -u CC cargo build` in
[`build/deploy-local.sh`](build/deploy-local.sh) — but a build typed by hand
will not, and the error it produces does not point at the cause.

## 3. Documentation has enforced governance

Run [`tools/check_docs.py`](tools/check_docs.py) before committing any
documentation change; CI runs it with `--strict` on every push. It fails on
dangling tags, identifier collisions, unresolvable links, and cited file paths
that do not exist — so a rename and its documentation citations **must land in
one commit**.

- Every requirement/spec/finding carries a bracketed tag `[GOV-DOC-010]`.
  A tag at the start of a line reads as its *definition*; cite one mid-line.
- **300 lines is a hard limit** per document, 100–250 the target. Above 300,
  split rather than trim.
- Machine-specific material lives in that machine's folder (`LempiPi/`,
  `BosePi/`, `SmartPC/`); generic tooling does not `[GDE-DEP-040]`.
- [GOV002](docs/GOV002-sources-of-truth.md) is the house discipline: when two
  sources answer one question, rank them **by measurement**, and make a
  fallback visible in the output rather than silently equivalent.

## 4. Deploying to an appliance is not a file copy

`bose` has an **overlay root**: an ordinary write to `/` lands in a tmpfs upper
layer, survives a service restart, passes every check — and is gone at the next
reboot. Five days of player deploys went that way
`[IMPL-BOS-185]`. `lempipi` has a plain writable root and does not.

- Use [`build/deploy-appliance.sh`](build/deploy-appliance.sh) for the player
  and [`build/install-config.sh`](build/install-config.sh) for any file; both
  detect the overlay and write **both** layers.
- For a package, or anything needing a root environment, use
  `sudo overlayroot-chroot` — see
  [BOSE010](BosePi/BOSE010-changing-a-locked-card.md).
- **Verify the durable copy, never the running one** `[GDE-DEP-070]`. Asking
  the live process proves only what is running now.

## 5. Say what you assume about a target before acting on it

This is `[GDE-DEP-060]`. A script that silently assumes a machine's shape produces a
log indistinguishable from one that assumed correctly. State it — *"pi@bose
has an overlay root; will persist through /media/root-ro"* — so a wrong guess
becomes a visible line rather than a silent success.

The same applies to checks: a guard that cannot run must say so loudly, not
report a plausible reason it was skipped. One written here did exactly that
and went unnoticed through a full fleet deploy.

## 6. An empty result with a zero status is not success

**Check what a command actually did, not what its exit code says** — and be
especially suspicious when the output is empty. This cost time three times in
one session, in three disguises:

- a cross-compile reported exit 0 and built nothing, because Docker was not
  running and the output went through `grep`, so the status was grep's
- a settle curve read `0 underruns, 0 recoveries` for five minutes on an audio
  stream that had **never opened** — a stream that does not exist cannot
  underrun `[GDE-ECHO-547]`
- a database-split rehearsal printed nothing and exited 0 through `| tail`,
  while Python had exited 1 on a full disk `[IMPL-VP3-120]`

Two habits fix all three. **Do not pipe a command through `grep`/`tail` and
then read `$?`** — that is the filter's status, not the command's. For anything
long-running, redirect to a file and append an explicit marker:

```
sh -c 'thing > /tmp/out.log 2>&1; echo EXIT=$? >> /tmp/out.log'
```

And **verify the thing itself, not a proxy for it**: that the PCM is open and
its `hw_ptr` advancing, that the binary's timestamp moved, that the output file
exists. "No errors" and "it worked" are different claims.

## 7. A quoting mistake usually still runs

§6's sibling, and the more dangerous of the two. There, a command did nothing
and reported success. Here, a command does **something other than what was
written** and reports success — so the wrong thing gets committed, pushed, or
deployed before anyone reads it back.

This is the most frequent single failure mode on this machine, because the Bash
tool is **Git Bash**, the surrounding shell is often PowerShell, and text
destined for git is full of `'`, `` ` ``, `$` and `!`. Six in one session,
2026-09-20:

- `sh -c '... IMPL016's ...'` — the apostrophe closed the quote. `git commit -F -`
  **still committed**, message truncated mid-word at `IMPL016s`, missing its last
  paragraph and its `Co-Authored-By` trailer. Pushed before it was noticed
  (`c9889ee`); not fixable afterwards, per §9.
- A PowerShell here-string (`@'…'@`) used in the Bash tool — not an error. The
  commit subject simply began with a literal `@`.
- A heredoc nested inside `sh -c '…'` — `here-document delimited by end-of-file`,
  and then the payload's own prose ran as commands (`60cb81d: command not found`).
- `grep -Fqx "$line"` where the line began with `-` — parsed as options.
- An over-escaped `grep -c` pattern returned `0`, which reads exactly like *the
  edit is missing*.

**The rule that prevents all of them: prose never goes inside a shell string.**
Write it with the Write tool and pass the path — `git commit -F <file>` — for
commit messages, documentation, or any payload containing punctuation. Every
message written to a file this session was correct; every message inlined was
not.

Three corollaries:

- **Never nest a heredoc inside `sh -c '…'`.** §6's `echo EXIT=$?` marker is for
  *commands*; it is not a wrapper for content.
- **Prefer Write/Edit over `cat > file <<'EOF'`.** The dedicated tools have no
  quoting layer to get wrong.
- **Read back what landed**, not the exit code: `git log -1 --format=%B` after
  any message that did not come from a file. Use `--` before data that might
  begin with `-`.

## 8. A cited commit hash will not resolve here

**`git show` on a hash cited in this documentation fails, and that is expected.**
This repository was seeded as a single commit from the final state of the
previous Lempi development repository; the history those hashes point into
stayed there, and that repository is private rather than deleted.

The measurements are still checkable --- the maintainer can read the archive ---
and the reasoning is in
[GUIDE015](docs/GUIDE015-the-earlier-names.md) `[GDE-NAM-030]`. Do not treat an
unresolvable hash as rot, and do not delete a citation because it does not
resolve locally.

Not every short hex string in these documents is a commit. Several are build
stamps a binary reported about itself, and at least one is a Bluetooth profile
UUID. Read the sentence before assuming.

## 9. Several agents may be working here at once

**Never `git add -A` or `git commit -a`. Stage the paths you actually
changed.** Another agent's in-progress work lives in the same working tree,
and a blanket stage sweeps it into your commit.

This is not hypothetical. On 2026-09-11 commit `294ab3e`, whose message is
entirely about deploy scripts, also carries 98 changed lines of
`player/src/web/vipunen.rs` and 48 of `tools/console.py` — a genuine Vipunen fix
for dead-but-bound console detection, written by a different agent, committed
and pushed under an unrelated message. Nothing was lost, but only because that
edit happened to be finished; a half-written one would have been published just
as readily, and the author had no way to know.

For the same reason, **check before reverting a file you did not change.**
`git status` and `git log --oneline -5` cost nothing. A `git checkout --` on a
file someone else is editing discards work that was never yours to discard.

Do not rewrite shared history to tidy any of this up — a misleading commit
message is a much smaller problem than a rebased branch under someone else's
feet.
