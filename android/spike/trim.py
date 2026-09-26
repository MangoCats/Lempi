"""Drop the albums that did not reach the phone from the spike catalogue.

A catalogue naming files that are absent would test missing-file handling in
the middle of a playback measurement; that is a different question.
"""
import shutil
import sqlite3
import sys

src, out = sys.argv[1], sys.argv[2]
GONE = ("/storage/emulated/0/Music/Lempi/Pink Floyd/",
        "/storage/emulated/0/Music/Lempi/Vollenweider, Andreas/")
shutil.copy2(src, out)
c = sqlite3.connect(out)
c.execute("PRAGMA foreign_keys = OFF")
ids = [r[0] for r in c.execute("SELECT file_id, path FROM files") if r[1].startswith(GONE)]
q = ",".join("?" * len(ids))
c.execute(f"DELETE FROM passage_recordings WHERE passage_id IN "
          f"(SELECT passage_id FROM passages WHERE file_id IN ({q}))", ids)
c.execute(f"DELETE FROM passages WHERE file_id IN ({q})", ids)
c.execute(f"DELETE FROM file_tags WHERE file_id IN ({q})", ids)
c.execute(f"DELETE FROM files WHERE file_id IN ({q})", ids)
c.execute("DELETE FROM recordings WHERE mbid NOT IN (SELECT mbid FROM passage_recordings)")
c.commit()
left = c.execute("SELECT count(*) FROM files").fetchone()[0]
c.execute("VACUUM")
print(f"dropped {len(ids)} files; {left} remain")
