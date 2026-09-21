# GUIDE015: The Earlier Names

**Guide — written 2026-09-21, at the seeding of this repository.**

This project spent its first months under two different names, and changed both
after a trademark search found live commercial conflicts. This is the only file
in this repository that records what they were. Everything else — every dated
finding, every measurement, every line of prose — is written under the current
names, because the project is continuous and the earlier names were temporary.
A reading of *"Lempi measured -2.09 ppm"* against a 2026-08 date is not an
anachronism: Lempi is what did the measuring, under a name it no longer uses.

---

## 1. What changed, and why

**`[GDE-NAM-010]` Two names were retired together, for the same reason.** The
player and the library builder were both named from the *Kalevala*. A conflict
screen run in September 2026 found, for each of them, a registered trade mark
held by an active commercial vendor in the same classes this project would
need — software, recorded media, and in one case an actual music-playback
appliance, which is this project's own category.

The names were retired rather than contested. Nothing about the search implied
a claim against this project; a small personal project has no business spending
years and money defending a name it chose for its sound, and a name that
invites a dispute is a bad name regardless of who would win. The replacements
were screened against the same registers before adoption, and the screen now
ends at the trade mark register rather than at a domain search — a name that is
free on GitHub and as a domain can still be taken where it counts.

The retired names are recorded here, once:

```text
retired-names
Vaino
Väinö
Sampo
```

`Vaino` was the player, now **Lempi**. `Sampo` was the library builder, now
**Vipunen**. Both replacements come from the same source as the originals, so
the *Kalevala* references elsewhere in this documentation — the kantele mark
shown where a passage has no cover art `[REQ-VIS-128]`, for one — still read
the way they were meant to.

That block is machine-read. [tools/check_forbidden_names.py](../tools/check_forbidden_names.py)
takes its tokens from it at run time and fails if any of them appears anywhere
else in the tree `[GOV-FBN-010]`. A checker cannot search for a string without
holding a copy of it, and holding it in the checker would put a retired name in
a second file; taking it from here keeps the count at one and makes the guard
permanently useful, since it also catches a name creeping back in through a
copied snippet or a restored file.

---

## 2. The repository this one succeeds

**`[GDE-NAM-020]` Development history lives in the previous Lempi development
repository, which is private.** This repository was seeded from the final state
of that one as a single commit, not by filtering its history. The earlier work
is therefore not reachable from `git log` here, and that is deliberate rather
than lost.

It is private for two reasons. A public archive under the old name would sit in
search results beside this project and imply a relationship to the commercial
products described above, which is exactly the false impression the rename was
meant to avoid. And its commit metadata carries an employer email address on a
handful of early commits, which does not belong on this work.

It was not rewritten to remove either. Rewriting author metadata changes every
commit hash, which would invalidate the citations described below — and those
citations are the reason the archive is being kept at all.

**`[GDE-NAM-030]` A cited commit hash resolves in that repository, not in this
one.** Documentation here cites commit hashes as evidence for measured claims —
*"the previous Lempi development repository, commit `b032f3b`"*. Those hashes
resolve to nothing locally. They are not rot and not a mistake: they point into
the archive, which the maintainer can still read. The same applies to a handful
of abandoned investigations kept on branches there, such as the Sonos direct-play
work distilled into [GUIDE032](GUIDE032-sonos-direct-play-closed.md); the branch
stayed behind, and what was worth keeping was written down here instead.

The old project name is deliberately absent from that citation form. A reader
who searched it alongside this project would find a commercial product and infer
a relationship that does not exist. The citation's job is to let the maintainer
verify a measurement, not to be a search term.

---

## 3. What this means in practice

- A dated finding says **Lempi**, whatever the name was on the day it was
  measured. There is no asterisk and no bracketed correction.
- [GUIDE001](GUIDE001-lineage-and-lessons.md)'s lineage reads
  MuLibPlay → McRhythm → Lempi v1 → Lempi. The rename is not a generation in
  that lineage; it is a change of label on the current one.
- The working papers that planned and executed the rename — the screen, the
  surface audit, the per-host fleet procedure — stayed in the archive. They were
  scaffolding, and this note is their whole durable residue.
- If a retired name appears anywhere in this tree again, the guard fails and it
  is a mistake, not a citation. There is no second allowed place.
