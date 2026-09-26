"""Point the spike catalogue's paths at where the audio will sit on the phone.

Shared storage, `Music/Lempi/` [REQ-AND-210]: the app reads it by ordinary
path, which is exactly what [REQ-AND-920] asks the spike to find out.
Writes the list of (local, phone) pairs for the push beside the database.
"""
import sqlite3
import sys

db, pairs_out = sys.argv[1], sys.argv[2]
ROOT = "C:\\Users\\Mango Cat\\Music\\"
PHONE = "/storage/emulated/0/Music/Lempi/"

c = sqlite3.connect(db)
rows = c.execute("SELECT file_id, path FROM files").fetchall()
with open(pairs_out, "w", encoding="utf-8", newline="\n") as fh:
    for fid, path in rows:
        if not path.startswith(ROOT):
            sys.exit(f"unexpected path, not under {ROOT}: {path}")
        rel = path[len(ROOT):].replace("\\", "/")
        c.execute("UPDATE files SET path = ? WHERE file_id = ?", (PHONE + rel, fid))
        fh.write(f"{path}\t{rel}\n")
c.commit()
print(f"rebased {len(rows)} paths to {PHONE}")
