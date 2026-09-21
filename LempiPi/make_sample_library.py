#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a small library from a full one, and put it on a LempiPi.

The full library here is 43.9 GB across 7,450 files. Waiting on that transfer
to find out whether the Pi plays at all is the wrong order: PI002 asks four
questions -- does it play, how long does it boot, how much memory does it take,
does Bluetooth survive a crossfade -- and a few albums answer all four.

**Idempotent.** The database is rebuilt only when the selection changes, audio
is copied only when absent, and the deploy is a sync. Re-running is the
supported way to add albums or to repair a partial transfer.

Python rather than shell because the subset is a dozen related DELETEs and the
paths are Windows paths going to a Linux box; the quoting alone justifies it.

    python LempiPi/make_sample_library.py
    python LempiPi/make_sample_library.py --albums 6 --push pi@lempipi
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys

DEST_AUDIO = "/srv/library/audio"

# Taken by DELETING from a copy rather than by building one up: the schema has
# foreign keys and a dozen tables referencing a passage, and reproducing those
# relationships by hand is how a subset ends up subtly unlike its source.
PRUNE = """
DELETE FROM passages WHERE file_id NOT IN (SELECT file_id FROM files);
DELETE FROM passage_recordings WHERE passage_id NOT IN (SELECT passage_id FROM passages);
DELETE FROM file_tags  WHERE file_id NOT IN (SELECT file_id FROM files);
DELETE FROM recordings WHERE mbid NOT IN (SELECT mbid FROM passage_recordings);
DELETE FROM recording_artists WHERE mbid NOT IN (SELECT mbid FROM recordings);
DELETE FROM artists    WHERE mbid NOT IN (SELECT artist_mbid FROM recording_artists);
DELETE FROM release_recordings WHERE mbid NOT IN (SELECT mbid FROM recordings);
DELETE FROM releases   WHERE mbid NOT IN (SELECT release_mbid FROM release_recordings);
DELETE FROM cover_art  WHERE release_mbid NOT IN (SELECT mbid FROM releases);
DELETE FROM flavor     WHERE subject_id NOT IN (SELECT mbid FROM recordings);
DELETE FROM lowlevel_cache WHERE audio_md5 NOT IN (SELECT audio_md5 FROM files);
DELETE FROM identification_cache WHERE audio_md5 NOT IN (SELECT audio_md5 FROM files);
DELETE FROM id_checks  WHERE passage_id NOT IN (SELECT passage_id FROM passages);
-- Vipunen's working caches. They exist to save re-fetching during a build and
-- are dead weight on an appliance that never builds: musicbrainz_cache alone
-- is most of what a naive subset carries over.
DELETE FROM musicbrainz_cache;
DELETE FROM ingest_decisions;
DELETE FROM selection_decisions;
"""
# Listener history is kept whole: keyed by recording, tiny, and rotation
# behaves oddly against an empty one.


def say(text=""):
    enc = sys.stdout.encoding or "utf-8"
    print(str(text).encode(enc, "replace").decode(enc), flush=True)


def note(what, state):
    say(f"  {what:<44} {state}")


def select(conn, albums):
    """File ids worth sampling, chosen for variety rather than at random.

    A random sample would very likely be N ordinary albums. These buckets
    exercise the paths that actually differ: a segmented DAO capture, audio
    ingested locally with no MusicBrainz id, and ordinary single-file albums.
    """
    dao = [r[0] for r in conn.execute(
        """SELECT file_id FROM passages WHERE kind='radio'
            GROUP BY file_id HAVING COUNT(*) > 1
            ORDER BY COUNT(*) ASC LIMIT 1""")]
    # The SMALLEST segmented file, not the largest: the point is to exercise
    # the multi-passage path, and the largest capture here is 160 minutes.
    local = [r[0] for r in conn.execute(
        """SELECT DISTINCT p.file_id FROM passages p
             JOIN passage_recordings pr ON pr.passage_id = p.passage_id
            WHERE pr.source = 'local:ingest'
            ORDER BY p.file_id LIMIT 6""")]
    plain = [r[0] for r in conn.execute(
        """SELECT f.file_id FROM files f
             JOIN passages p ON p.file_id = f.file_id AND p.kind='radio'
            WHERE f.file_id NOT IN (SELECT file_id FROM passages
                                     WHERE kind='radio' GROUP BY file_id
                                    HAVING COUNT(*) > 1)
            GROUP BY f.file_id ORDER BY f.path LIMIT ?1""", (albums * 10,))]
    return sorted(set(dao + local + plain))


def build(src, out, keep):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(out + suffix):
            os.remove(out + suffix)
    shutil.copy2(src, out)
    c = sqlite3.connect(out, isolation_level=None)
    c.execute("PRAGMA foreign_keys = OFF")
    ids = ",".join(str(i) for i in keep)
    c.execute(f"DELETE FROM files WHERE file_id NOT IN ({ids})")
    for stmt in PRUNE.strip().split(";"):
        if stmt.strip():
            c.execute(stmt)
    c.execute("VACUUM")
    c.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/library.db")
    ap.add_argument("--out", default="data/sample-library.db")
    ap.add_argument("--stage", default="data/sample-audio")
    ap.add_argument("--albums", type=int, default=8)
    ap.add_argument("--push", help="user@host of a prepared LempiPi")
    a = ap.parse_args()

    if not os.path.exists(a.src):
        say(f"no such database: {a.src}")
        return 1

    say(f"sample library from {a.src}")
    src = sqlite3.connect(f"file:{a.src}?mode=ro", uri=True)
    keep = select(src, a.albums)
    src.close()
    if not keep:
        say("selection matched nothing")
        return 1

    sig = str(hash(tuple(keep)))
    stamp = a.out + ".sig"
    current = (os.path.exists(a.out) and os.path.exists(stamp)
               and open(stamp).read() == sig)
    if current:
        note("database", f"current ({len(keep)} files)")
    else:
        build(a.src, a.out, keep)
        open(stamp, "w").write(sig)
        note("database", f"built ({len(keep)} files)")

    c = sqlite3.connect(a.out, isolation_level=None)
    q = lambda s: c.execute(s).fetchone()[0]          # noqa: E731
    say(f"    passages    {q('SELECT COUNT(*) FROM passages WHERE kind=\"radio\"')}")
    say(f"    recordings  {q('SELECT COUNT(*) FROM recordings')}")
    say(f"    with flavor {q('SELECT COUNT(DISTINCT subject_id) FROM flavor')}")
    say(f"    cover art   {q('SELECT COUNT(*) FROM cover_art')}")
    say(f"    plays kept  {q('SELECT COUNT(*) FROM listener_play_history')}")
    note("size", f"{os.path.getsize(a.out)/1048576:.0f} MB")

    # Stage from the SOURCE database, not the output. The output's paths are
    # rewritten to where the files will live on the Pi, so a re-run that read
    # them would try to copy from /srv/library/audio on this machine and find
    # nothing -- which is exactly what it did before this was fixed.
    src = sqlite3.connect(f"file:{a.src}?mode=ro", uri=True)
    ids = ",".join(str(i) for i in keep)
    paths = [r[0] for r in src.execute(
        f"SELECT path FROM files WHERE file_id IN ({ids})")]
    src.close()
    copied = 0
    for p in paths:
        norm = p.replace("\\", "/")
        rel = norm.split("/Music/", 1)[-1] if "/Music/" in norm else os.path.basename(norm)
        dst = os.path.join(a.stage, rel.replace("/", os.sep))
        if os.path.exists(dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(norm, dst)
            copied += 1
        except OSError as e:
            say(f"    missing: {os.path.basename(norm)} ({e.strerror})")
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(a.stage) for f in fs)
    note("audio staged", f"{copied} new, {total/1048576:.0f} MB total")

    # Rewrite to where the files will live. Idempotent: a path already
    # rewritten does not contain the marker and is left alone.
    n = 0
    for fid, p in c.execute("SELECT file_id, path FROM files").fetchall():
        norm = p.replace("\\", "/")
        if norm.startswith(DEST_AUDIO):
            continue
        rel = norm.split("/Music/", 1)[-1] if "/Music/" in norm else os.path.basename(norm)
        c.execute("UPDATE files SET path = ?1 WHERE file_id = ?2",
                  (f"{DEST_AUDIO}/{rel}", fid))
        n += 1
    note("paths rewritten", f"{n} changed" if n else "already correct")
    c.close()

    if a.push:
        say(f"\ndeploying to {a.push}")
        run = lambda *c: subprocess.run(c, check=False)   # noqa: E731
        run("ssh", a.push, f"sudo install -d -o lempi -g lempi {DEST_AUDIO}")
        if shutil.which("rsync"):
            run("rsync", "-a", "--info=stats1",
                "--rsync-path=sudo -u lempi rsync",
                a.stage.replace("\\", "/") + "/", f"{a.push}:{DEST_AUDIO}/")
        else:
            # tar over ssh, not scp per file: one stream, and no remote
            # shell quoting to get wrong on paths with spaces and brackets
            # -- which is exactly how the per-file version failed.
            note("transfer", "tar over ssh")
            with subprocess.Popen(
                    ["ssh", a.push,
                     f"sudo -u lempi tar -xf - -C {DEST_AUDIO}"],
                    stdin=subprocess.PIPE) as sink:
                subprocess.run(["tar", "-cf", "-", "-C", a.stage, "."],
                               stdout=sink.stdin, check=False)
                sink.stdin.close()
        run("scp", "-q", a.out, f"{a.push}:/tmp/listener.db")
        run("ssh", a.push,
            "sudo systemctl stop lempi 2>/dev/null; "
            "sudo install -o lempi -g lempi -m 0644 /tmp/listener.db /srv/library/library.db; "
            "rm -f /tmp/listener.db; sudo systemctl start lempi; sleep 3; "
            "systemctl is-active lempi")

    say("\nRe-run to add albums (--albums N) or repair a partial transfer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
