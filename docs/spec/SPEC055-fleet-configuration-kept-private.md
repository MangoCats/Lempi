# SPEC055: Fleet Configuration Kept Private

**Design Specification — Tier 2 · written 2026-09-28 · accepted 2026-09-28 · built 2026-09-28 but `[SPEC-FCP-010]`, which awaits the maintainer's private repository · build plan in [IMPL017](../IMPL017-closing-the-mesh-arc.md)**

The repository is public, and the household's real configuration -- machine
logins, a time server's address, the star-sync plan -- has been reaching it
through the scripts and machine folders. The existing detail stays as it is, by
the maintainer's decision `[SPEC-SEC-030]`: it is already public. This is about
the next commit: where real values live so they are never typed into a tracked
file again, and a gate that holds even when the local hook is not installed.

> **Related:** [SPEC053](SPEC053-security-hardening.md) `[SPEC-SEC-030]` `[SPEC-SEC-130]` · [SPEC054](SPEC054-mesh-without-ssh.md) (the mesh half of this work) · `build/lib-defaults.sh` · `fleet-example/` · `tools/check_fleet_leaks.py`

---

## 1. Where real values live

**`[SPEC-FCP-010]` The gitignored `fleet/` is a clone of a private repository.**
It already holds the real `targets.env` and star-sync plan, and nothing backs
them up or records their history. A private `lempi-fleet` repository, cloned into
`fleet/` on each operator machine, gives both, and carries the leak check's
denylist (`fleet/private-tokens.txt`) to every machine a commit is made from.

**`[SPEC-FCP-020]` A tracked file names roles; `fleet/` names machines.** Every
script reads hosts, logins and paths through `build/lib-defaults.sh`, which
takes `fleet-example/targets.env` (roles, plainly fake) and then `fleet/targets.env`
(real) on top. Two values move there that are still written in tracked files
today: the source hosts `build/deploy-everywhere.sh` lists as `login@host:path`,
and the fleet's time server.

**`[SPEC-FCP-030]` Machine configuration that must hold a real value is a
template.** The chrony fleet sources file is tracked with `@NTP_SERVER@` in place
of an address, and rendered on the operator's machine when it is deployed --
where `fleet/` exists, never on the node, which receives only the rendered file.
A value missing from `fleet/targets.env` stops the deploy with its name
(`${NTP_SERVER:?}`), rather than rendering a placeholder that fails later and
quietly `[GDE-DEP-060]`.

**`[SPEC-FCP-040]` FLEET001 stays tracked.** It was made the one tracked file in
`fleet/` on 2026-09-26 so the source hosts receive it through their checkouts,
and it deliberately carries no addresses or keys; its roles and hostnames are the
ones the machine folders already publish. Moving it private would leave its
`[FLT-*]` citations in tracked documents resolvable nowhere a fresh clone can
see, for little gained. The maintainer may still choose to move it; the cost is
rewording those citations as prose first.

## 2. The gate

**`[SPEC-FCP-050]` CI refuses a new leak, not only the hook.** The pre-commit
hook checks a commit's added lines, but a commit made where the hook is not
installed -- a fresh clone, the web editor -- is not checked at all
`[SPEC-SEC-130]`. So CI scans the whole tree strictly against a **baseline**: a
committed list of the findings already accepted, each recorded as a hash of its
file and line and never as text. A finding not in the baseline fails the build.

**`[SPEC-FCP-055]` The baseline only shrinks.** Accepted detail stays without a
redaction pass, as decided. When a line in it is edited, its hash no longer
matches and the edit must clear it -- so touching an old leak removes it rather
than re-accepting it. Regenerating the baseline to admit a *new* finding is a
deliberate, reviewed commit of its own, never a side effect of another.

**`[SPEC-FCP-060]` CI says what it could not check.** Without `fleet/`, CI has no
denylist, so it runs the generic patterns only and says so `[GOV-FBN-020]`.
Hostnames are caught where commits are made, by the denylist the private repo
carries there.

## 3. Optional

**`[SPEC-FCP-070]` ssh aliases.** An operator's `~/.ssh/config` may `Include`
a `fleet/ssh_config`, so a script's role name (`speaker-a`) resolves to the real
host and login. A convenience for the operator's own shell; the build does not
depend on it.

## 4. Open

**`[SPEC-FCP-900]`**
- the private repository is the maintainer's to create; nothing here assumes its
  name or host;
- whether the source hosts clone it too, so their own commits carry the denylist.

---

**Traceability:** `[SPEC-FCP-010..070]`, `[SPEC-FCP-900]` · extends `[SPEC-SEC-130]` · keeps `[SPEC-SEC-030]`'s decision that existing detail stays
