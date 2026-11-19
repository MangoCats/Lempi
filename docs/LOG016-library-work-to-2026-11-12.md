# LOG016: Library Work, to 2026-11-12

**Experiment Record — closed, continued in LOG014 · split from it 2026-11-19**

The entries of 2026-11-11 and 2026-11-12 from [LOG014](LOG014-library-work.md),
moved here by its own rule when it neared 250 lines `[LOG-LIBW-040]`, and
unchanged. What is still open, and every entry since, is there; those of
2026-11-08 and before are in [LOG015](LOG015-library-work-to-2026-11-08.md).

> **Related:** [LOG014](LOG014-library-work.md) for how an entry is written
> and what is open · [data/README.md](../data/README.md) for where each copy
> of the pair lives

---

## 1. Entries, newest first

**`[LOG-LIBW-280]` 2026-11-12: the hub's pair set to WAL mode.** Both halves
were in rollback-journal mode, as the merge of 2026-09-26 wrote them. That
evening a start after a long build read the library for 15 s, a queue save's
commit was refused, and the listener stayed locked for five hours
`[SPEC-STAR-106]`. At the maintainer's word, with the player stopped and the
console idle: `python tools/pair_journal.py data/library.db --wal`. No row
changed: 7,264 files, 24,201 passages and 41,367 plays as before, `quick_check` ok
on both, and the player came back paused on the passage it had left. A read
transaction held on the listener for 8 s no longer stops the player saving.
Hub only: every other node's pair is its own. Backup
`data/backups/pre-wal-20261112/` (1.5 GB, goes 2026-12-12).

**`[LOG-LIBW-270]` 2026-11-11: *Ghosts I–IV* as one album, in one folder.**
After both folders were inducted, 26 of the 36 tracks were on no release
and named `local:audio:` placeholders. At the maintainer's word, after a dry
run: the 18 disc 2 files moved from `Ghosts III-IV` into `Ghosts I–IV (2008)`
as `2-01.`…`2-18.` `[SPEC-RIP-108]`, its sheet with them as `… (Disc 2).cue`,
and the empty folder removed. The 26 files got release `0822f7c5…`
(`file_releases` source `manual`). Their 52 passages, radio and album, now
name the release's recordings, mapped by disc and position and checked
against title and length. The 1,846 flavor values were moved to the real
ids, the 26 placeholders removed, and their stale `id_checks` cleared. One
`ingest_decisions` row per file, stage `release_alignment`. **Track 18** is
the digital edition's recording, `1d35585f` (323 s; the file is 317.7 s),
not the CD's 314 s `74c113dc`: the only one of 36 that differs between the
editions. The maintainer chose to place it at disc 1 track 18 by a
tracklist row of source `manual`, which MusicBrainz does not have. The
desktop player shows one album of 36, 1-1 to 2-18. Hub only. Backup
`data/backups/pre-ghosts-align-20261111/` (goes 2026-12-11).

**`[LOG-LIBW-260]` 2026-11-11: *Ghosts I–IV* completed on disk from the
Internet Archive.** Both CD rips of 2026-11-08 had stopped partway: disc 1
after track 10, disc 2 after track 4, so the hub held 14 of 36. The missing
22 (11–18, 23–36) are the VBR MP3s of archive.org item
`nine-inch-nails-ghosts-IV`, copied byte for byte at the maintainer's word:
each matched the md5 and size IA lists, and decoded with no error. That item
is a fan upload with no licence of its own, but the album was released under
CC BY-NC-SA 3.0, which allows a non-commercial copy; its two extra tracks,
37 and 38, are not on the release and were not taken. Track 22, fetched only
to compare, measured like the CD's: mean −14.4 dB, peak 0.0 dB. Disc 1's
cue sheet named the eight absent files by the ripper's names; its `FILE`
lines now name `1-11.`…`1-18.`, the original in
`data/backups/pre-ghosts-fill-20261111/` (goes 2026-12-11). **Nothing is
catalogued yet**: `ingest_folder.py`'s dry run would add 8 and 14, with 10
and 4 already present. Every other partial or damaged album in the catalogue
is all rights reserved, so none was fetched.

---

**Traceability:** an Experiment Record per [GOV001](GOV001-document-hygiene.md);
nothing here is a requirement. Each entry traces to the tag of the tool or
rule it applied.
