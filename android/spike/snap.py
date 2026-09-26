"""Read one snapshot from the phone's player over /ws, through `adb forward`.

The key comes from the WebView's own cookie store (copied to $S/cookies.db, /s by default, by
run-as): the player never writes it anywhere [REQ-AND-160]. Standard library
only -- a WebSocket handshake and one unmasked server frame is all this needs.
Usage: python3 snap.py [field ...]   (no fields: print every top-level key)
"""
import base64
import json
import os
import socket
import sqlite3
import sys

key = sqlite3.connect(os.path.join(os.environ.get("S", "/s"), "cookies.db")).execute(
    "SELECT value FROM cookies WHERE name='lempi_key'").fetchone()[0]
s = socket.create_connection(("127.0.0.1", 5720), timeout=10)
nonce = base64.b64encode(os.urandom(16)).decode()
s.sendall((f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:5720\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {nonce}\r\n"
           f"Sec-WebSocket-Version: 13\r\nx-lempi-key: {key}\r\n\r\n").encode())
head = b""
while b"\r\n\r\n" not in head:
    head += s.recv(1)
if b" 101 " not in head.split(b"\r\n")[0]:
    sys.exit("handshake refused: " + head.split(b"\r\n")[0].decode())


def exact(n):
    b = b""
    while len(b) < n:
        chunk = s.recv(n - len(b))
        if not chunk:
            sys.exit("closed mid-frame")
        b += chunk
    return b


while True:
    b0, b1 = exact(2)
    n = b1 & 0x7F
    if n == 126:
        n = int.from_bytes(exact(2), "big")
    elif n == 127:
        n = int.from_bytes(exact(8), "big")
    body = exact(n)
    if b0 & 0x0F == 1:  # text
        snap = json.loads(body)
        break

fields = sys.argv[1:]
if not fields:
    print(" ".join(sorted(snap)))
else:
    print(json.dumps({f: snap.get(f) for f in fields}))
