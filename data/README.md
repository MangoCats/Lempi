# `data/`

**`library.db` and `listener.db` are the live pair** — the catalogue half and
the listener half of one database, split per `[IMPL-DBSPLIT-025]` and matching
the shape both appliances run. Every tool and doc example in this repo means
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
