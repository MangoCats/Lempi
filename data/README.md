# `data/`

## Where the live pair lives — and where it is now

**The arrangement, as the maintainer describes it:** the Vipunen primary is in
this directory on **the Windows desktop**, `GMKtec` (Windows 11, where the 44 GB
of music is under `C:\Users\Mango Cat\Music`). **`teacherslounge`** (Ubuntu
22.04) keeps a mirror in `/home/sw/lempi-data/`, which validates the same tools
under Linux. Each appliance keeps its own catalogue copy and its **own** listener
history, and those are not mirrors of this one.

**Measured 2026-09-26: the primary is not here.** It lived in `data/` of the
previous repository's checkout on this PC, a sibling of `Dev\Lempi` whose path
is recorded in `[GDE-NAM-040]`. This repository was seeded on 2026-09-21
without those untracked files, and that checkout no longer exists; it is not in
the Recycle Bin. Its sidecars went
with it: `library.console.db` and `library.idchecks.db`, the 8,330
fingerprints described below. The copies that survive, read that day:

| copy | files | passages | recordings | `recording_works` | plays (newest) |
| :--- | ---: | ---: | ---: | ---: | :--- |
| a scratch pair on this PC, 2026-09-10 | 5,709 | 16,661 | 8,185 | — | 37,763 (09-08) |
| `teacherslounge:~/lempi-data`, copied 09-21 from its own 09-12 data | 5,709 | 16,661 | **8,224** | — | 37,762 (09-06) |
| `bose`, `lp3-wifi` | 5,709 | 16,661 | 8,185 | 7,154 | each its own |
| `lempi02w` | 5,709 | 16,409 | 8,191 | 7,188 | its own |

None is a superset of the others. So the hub is rebuilt by merging all of
them, not by choosing one: [SPEC046](../docs/spec/SPEC046-star-sync.md), by
`tools/star_merge.py`.

**State, 2026-09-26: a merged candidate awaits the maintainer's verification.**
It is in `data/recovery/2026-09-26/merge-full-4/`, with `REVIEW.md` (the recent
edits by name) and `report.md` (every decision). Every node's snapshot is in
the folder beside it, hash-verified against the node and made read-only.
Until the candidate is promoted, a tool run here against `data/library.db`
finds no file, and that failure is correct.

## What the pair is

**`library.db` and `listener.db` are the live pair** — the catalogue half and
the listener half of one database, split per `[IMPL-DBSPLIT-025]` and matching
the shape all three appliances run. Every tool and doc example in this repo means
this pair when it says "the library", and every tool takes **either** path as
its single argument: `tools/lempi_db.py` finds the other half beside it and
attaches it.

`lempi_new.db` was the whole pre-split database and is **gone** as of
2026-09-11, along with its dated backups. It was superseded on 2026-09-11
00:01 and had fallen behind on schema as well as data — it never had
`listener_characteristics`. While it existed, a tool pointed at it ran
perfectly and wrote to a file nothing read; deleting it makes that mistake
fail immediately instead `[PI-PRE-098]`.

Sidecars are derived from the database path, so they follow the split:
`library.console.db` (console state) and `library.idchecks.db` (8,330
Chromaprint fingerprints and their AcoustID verdicts — carried over from the
pre-split sidecar, 99.7% of its rows still keyed to live passages).

`flavor.db`, `flavor-sample.db`, `sample-library.db` and
`sample-library.console.db` are fixtures and extraction data, not copies of
the live pair.
