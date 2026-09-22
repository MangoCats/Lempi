# GUIDE011: What the Deploy Scripts Should Be Called

**Development Guidance — planned 2026-09-11, after a misnamed script deployed to RAM for five days**

A naming and signposting convention for the scripts that build, deploy and
verify Lempi across the fleet. Written because a name proved load-bearing:
`[IMPL-BOS-185]`'s five days of RAM-only deploys to `bose` happened partly
because the script doing them was called, and filed, as though it only ever
touched `lempi02w`.

> **Related:** [BOSE010](../BosePi/BOSE010-changing-a-locked-card.md) — the traps this is meant to make visible · [BOSE006](../BosePi/BOSE006-what-lands-where.md) `[IMPL-BOS-185]` — the incident · [build/README.md](../build/README.md) — the cross-compile story these scripts automate

---

## 1. Why this is not cosmetic

**`[GDE-DEP-010]` The name is where the question should have been asked.**
`b032f3b` added `bose` to the appliance list by passing `pi@bose` to a script
called `deploy-lempi02w.sh`, which delegates to `deploy-player.sh` living in the
`LempiPi/` folder. Both are host-generic — each takes a host argument and
defaults to lempi02w — but nothing in either name, path, or call site ever
raised *does this appliance's root filesystem behave the same way?* It does
not: `bose` has an overlay root and `lempi02w` does not, and every deploy to
`bose` from that day until 2026-09-11 went to RAM and reported success.

A per-machine folder that contains machine-generic tooling is worse than a flat
one, because it makes a false promise about scope. `[GOV-DOC-030]`'s reasoning
for segregating inherited material applies here to code.

---

## 2. What the survey actually found

**`[GDE-DEP-020]` Three scripts are misnamed. The rest are fine, and saying so
matters as much.** Every script taking a `HOST` argument was checked, 2026-09-11.

| script | takes a host | named for a machine | verdict |
| :--- | :--- | :--- | :--- |
| `deploy-lempi02w.sh` (build/) | yes | yes | **misnamed** — generic appliance deploy |
| `deploy-player.sh` (LempiPi/) | yes | by folder | **misplaced** — generic installer |
| `deploy.sh` (LempiPi/) | yes | by folder | **misplaced** — see `[GDE-DEP-025]` |
| `provision-bose.sh`, `finalize-bose.sh`, `build-bose-card.sh`, `seed-library.sh`, `attended-import.sh`, `request-unlock.sh` (BosePi/) | yes | yes | **correct** |
| `deploy-everywhere.sh`, `deploy-local.sh`, `update-source-host.sh`, `verify-targets.sh` (build/) | n/a | no | **correct** |

**`[GDE-DEP-025]` Correction, 2026-09-11: `deploy.sh` is not a thin wrapper.**
An earlier revision of this document called it "a two-line wrapper over both"
and proposed folding it away. Reading it first would have prevented that: it is
~130 lines that build a *named ref* in a container-side git worktree without
touching the caller's checkout, refuse a dirty tree, handle MSYS path
translation, and cross-check the commit the appliance reports against the one
requested. It overlaps `deploy-appliance.sh` in the no-ref case only.

Whether those two should merge is a **consolidation** question, not a naming
one, and it is left open deliberately rather than answered by a rename
`[GDE-DEP-095]`.

The BosePi scripts are parameterised but genuinely implement *bose's*
procedure — its card layout, its lock-in, its library seed. A host argument is
not on its own evidence of genericity; what matters is whether the *procedure*
is machine-specific. Renaming those would be the same error in the opposite
direction.

---

## 3. The rule

**`[GDE-DEP-030]` A script that accepts a host must not be named after one, and
must not live in one machine's folder.** Its name should say what it acts on
and what it does to it — `install-player`, `deploy-appliance`,
`verify-targets` — so that an operator reading a command line can tell what is
about to be touched without opening the file.

**`[GDE-DEP-040]` Location carries scope.** `build/` means *runs on the
development host and reaches out to something else*. A machine folder
(`BosePi/`, `LempiPi/`, `SmartPC/`) means *this material is about that machine
and should not be pointed anywhere else*. Nothing generic belongs in a machine
folder, and nothing machine-specific belongs in `build/`.

**`[GDE-DEP-050]` Irreversible steps keep an explicit verb and a second
confirmation.** Already the practice worth preserving —
`finalize-bose.sh --lock-in --confirm-heard-it-play` says exactly what it is
and refuses to be typed by accident `[IMPL-BOS-120]`.

---

## 4. The names, applied 2026-09-11

| was | is now |
| :--- | :--- |
| `deploy-lempi02w.sh` (build/) | [`build/deploy-appliance.sh`](../build/deploy-appliance.sh) |
| `deploy-player.sh` (LempiPi/) | [`build/install-player.sh`](../build/install-player.sh) |
| `deploy.sh` (LempiPi/) | merged into [`build/deploy-appliance.sh`](../build/deploy-appliance.sh); a forwarder remains at the old path |

`lempi02w` remains the default host, so existing invocations keep working; only
the name and path changed. Every executable reference was updated in the same
commit, and the documentation citations with them, because `[GOV-DOC-040]`'s
path check makes a half-done rename fail rather than rot — the cost being
visible up front is the point `[GDE-DEP-080]`.

Historical records keep the name they were written with, annotated once:
`PI008`'s bringup narrative and `IMPL003`'s 2026-08-20 deploy both still say
`deploy-player.sh`, each with a pointer to where it went. Current-state specs
(`SPEC034`, `PI005`) name today's script, because they describe today's
system.

---

## 5. The part that matters more than the rename

**`[GDE-DEP-060]` Every script must say aloud what class of target it thinks it
is talking to, before it acts.** Renaming would not, by itself, have caught
`[IMPL-BOS-185]`. What catches it is the target announcing itself:

```
deploy: pi@bose has an overlay root; will persist through /media/root-ro
deploy: persisted to /media/root-ro/usr/local/bin/lempi (ba3d3e95…)
deploy: WARNING -- /media/root-ro left read-write; it returns to ro on the next reboot
```

Those lines are the model, and the convention this document most wants adopted.
A script that silently assumes a target's shape produces a log indistinguishable
from one that assumed correctly; a script that *states* the assumption turns a
wrong guess into a visible line. The same applies to architecture, to whether a
service is running, and to whether a partition is writable — each already a
real failure mode here `[IMPL-BOS-175]`, `[BOS-PWR-050]`.

**`[GDE-DEP-070]` Verify the durable artefact, not the convenient one.**
`deploy-everywhere.sh` asks the running player what it is, which its own header
correctly calls the only check that catches a stale service. It is also blind
to the case where the disk is RAM. Where a target has two copies — live and
persisted — both must be checked, and the *persisted* one is the answer.

---

## 6. What the migration costs

**`[GDE-DEP-080]` The rename is cheap; the citations are not.** Measured
2026-09-11: `deploy-lempi02w.sh` is named in 4 files, `LempiPi/deploy.sh` in 4,
and `deploy-player.sh` in **16** — including `HOWTO.md`, four `PI0*` records,
three `BOSE0*` records, and specs as far afield as `SPEC034` and `SPIN002`.

`tools/check_docs.py` validates that every cited path exists `[GOV-DOC-040]`,
so a rename and its documentation updates **must land in one commit** or CI
reports the difference. That is a feature: it makes the true cost visible up
front rather than leaving dangling references.

Historical records are the judgement call. `PI008` describes a bringup that
genuinely used the old name on the old date; rewriting it would falsify a
record. The rule applied here should be **rename living references, annotate
historical ones**, consistent with `[GOV-DOC-050]`'s separation of current from
historical.

---

## 7. Configuration, and what is still open

**`[GDE-DEP-090]` Configuration now persists too, by the same rule.**
[`build/install-config.sh`](../build/install-config.sh) puts one file on an
appliance so that it is still there after a reboot: detect the root's actual
filesystem, announce which kind it found, write the live copy and — on an
overlay — the durable copy beneath it, then verify the **durable** one by
checksum and report whether the lower layer could be returned to read-only.

It closes the gap that mattered most. `[IMPL-BOS-185]` cost a binary, but what
actually stopped the music was a *unit file* that reverted, and no tool existed
to put one on a locked appliance at all. Used 2026-09-11 to restore `bose`'s
`lempi.service` together with the `lempi-preflight` and `lempi-db-recover`
helpers its `ExecStartPre` lines need — all three absent since the reboot, all
three now verified across one. `lempi-db-recover` replayed an un-checkpointed
4.1 MB WAL on `listener.db` the first time it ran, which is the clearest
possible argument that a missing helper is not a cosmetic absence.

**`[GDE-DEP-092]` Packages are still the manual case.** `install-config.sh`
moves *files*. A package needs `overlayroot-chroot` and an `apt` run
`[IMPL-BOS-180]`, which also needs a nameserver inside the chroot, because A's
own `resolv.conf` is the NetworkManager stub. `sqlite3` remains absent from
`bose` for this reason — recovery falls back to `python3` and says so, which is
`[GOV-SRC-030]` working, but the preferred tool is still missing.

**`[GDE-DEP-095]` Merged 2026-09-11, guards first.** The order mattered more
than the merge. The dirty-tree refusal moved into `install-player.sh` *before*
the scripts were joined, so the two callers ended up differing only in
ref-selection and the merge became small instead of a 130-line consolidation.

That guard now checks the **artefact** rather than the checkout's git state —
it asks the staged binary its own version on the target, after upload and
before install, while backing out still costs nothing. Checking git state
would have re-made the mistake this document is about: trusting the convenient
proxy over the thing that actually ships `[GDE-DEP-070]`.

`LempiPi/deploy.sh` remains as a forwarder rather than being deleted, because
the old path is named in `HOWTO.md`, BOSE008 and BOSE009 and in people's shell
history. It passes every argument through unchanged.

**`[GDE-DEP-098]` Closed 2026-09-12: appliances build WITHOUT `vipunen-support`,
and every future Pi target does too.** Decided by the project owner as part of
convergence between the open projects; `deploy-appliance.sh` already defaults
`FEATURES=""`, `player/Cargo.toml` has `default = []`, and
`deploy-everywhere.sh` routes `pi@lempi02w`/`pi@bose` through that path, so the
rule is mechanically enforced rather than remembered. Verified by building both
ways on 2026-09-12: **11,118,944 bytes with, 7,981,512 without — 2.99 MB**,
against the 3.05 MB `[SPEC-SUI-190]` measured at its own earlier commit. The
7.98 MB binary was deployed to `lempi02w` the same day. The original argument
follows, because the reasoning is why the rule holds rather than merely what it
is.

The two scripts disagreed —
`deploy-lempi02w.sh` passed `--features vipunen-support`, `deploy.sh` did not — so
which binary an appliance received depended on which command was typed. Merging
forced one answer, and it preserves today's fleet behaviour rather than
changing what runs on two appliances inside a merge.

The evidence says today's behaviour is wrong. `[SPEC-SUI-196]` states the gate
exists *"so an appliance build never resolves or compiles an HTTP client it
will never call"*, and `[SPEC-SUI-190]` measures the appliance binary **3.05 MB**
smaller without it. Measured 2026-09-11: `/review` answers **200 on both bose
and lempi02w**, so both carry a `reqwest`/`rustls` stack they never call, on
machines with a stated memory budget `[REQ-HW-140]`. The flag has been there
since `968bdca`, the commit that created these scripts, and no document argues
for it.

Changing it was one line — `LEMPI_FEATURES=""` already overrode it — but it
altered what runs on both appliances, so it waited for a deliberate decision
and a redeploy rather than a quiet edit. Both have now happened. **`bose` is
still to be redeployed**, and should be: the read-only-catalogue argument above
bites hardest there.

**`[GDE-DEP-097]` Closed 2026-09-11: `deploy.sh` now cross-checks the durable
binary.** It asked `/usr/local/bin/lempi --version`, which on an overlay host is
the ephemeral copy — the exact blind spot `[GDE-DEP-070]` names. It was never
wrong in practice, because `install-player.sh` has already verified the durable
copy by that point, but it was the weaker of two available checks and the whole
argument of this document is that the weaker one is what let five days pass. It
now resolves the `lowerdir` and asks the copy that survives a reboot, saying so
in the log `[GDE-DEP-060]`.

**`[GDE-DEP-100]` Open, found 2026-09-12: a cross-compile from a git *worktree*
cannot stamp its commit.** The deployed binary answers `lempi 0.1.0 (unknown)`.
A worktree's `.git` is a file pointing at the parent repository's
`.git/worktrees/<name>`, which lies outside the `-v "$REPO_ROOT":/w` bind
mount, so git inside the container resolves nothing and
[`player/build.rs`](../player/build.rs) takes its documented `unknown`
fallback. Demonstrated directly:

```
fatal: not a git repository: /w/C:/Users/.../Lempi/.git/worktrees/lempi-guide012
```

This is not a worktree-only curiosity dressed up: **working in a worktree is
the correct way to build while other agents hold the shared checkout**
`[GDE-LES-010]`, so the safe habit is exactly what breaks the stamp. The
binary is otherwise correct — the checksum cross-check is what actually proves
a deploy, and it passed — but `[REQ-VIS-200]`'s whole purpose is that a running
player can say which source it is, and here it cannot.

Two candidate fixes, not chosen here. `build.rs` could accept the hash from the
environment, which also helps container and source-tarball builds; or the
deploy script could mount the git common directory, which needs no source
change but must survive a path with spaces in it.

**`[GDE-DEP-110]` Open, found 2026-09-12: the final cross-check blames the
wrong cause, and it is the same conflation this repository has already been
caught by once.** `deploy-appliance.sh` greps `--version` for a 12-hex hash and,
finding none, reports the binary *"predates --version [421f7c1]"*. Under
`[GDE-DEP-100]` that is false: `--version` ran and answered `unknown`. The
check conflates **"no `--version` support"** with **"`--version` reported no
hash"**.

[`build/install-player.sh`](../build/install-player.sh) documents being bitten
by this exact conflation — an unexecutable staged binary returned "Permission
denied", *"the empty result was read as 'predates --version', and the guard
reported itself skipped for a reason it had never checked"*. That lesson was
fixed in one file and never carried to the other, so the stricter of the two
guards is now the one excusing its own failure `[GDE-DEP-060]`. Distinguishing
the two cases is a few lines and wants doing before a real version mismatch is
read as an old build.
