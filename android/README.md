# The Android app

The Android app `[REQ-AND-400]`. For now it is the **device spike**
`[GDE-APP-020]`, `[REQ-AND-530]`: the player in a foreground service, the lempi
skin in a WebView, and three hand-written JNI entries in
[`player/android`](../player/android/src/lib.rs). It is Java, with no AndroidX,
so the spike downloads and depends on as little as possible. Kotlin and UniFFI
follow once the spike passes `[REQ-AND-410]`.

Everything runs in the `app` stage of
[`build/Dockerfile.android`](../build/Dockerfile.android), so nothing Android
is installed on the build host.

## Build

```
docker build --target app -t lempi-android-app -f build/Dockerfile.android .
docker run -d --name lempi-adb -v "<repo>":/w -v "<music>":/music:ro -v "<work>":/s \
    -v lempi-adb:/root/.android -v lempi-gradle:/root/.gradle \
    lempi-android-app sh -c 'adb start-server; sleep infinity'

# the library, stripped, for API 30
docker exec -w /w/player -e CARGO_PROFILE_RELEASE_STRIP=symbols lempi-adb \
    cargo ndk -t arm64-v8a -P 30 -o /w/android/app/src/main/jniLibs \
    build --release -p lempi-android --target-dir /w/player/target/android-app
# the APK
docker exec -w /w/android lempi-adb ./gradlew --no-daemon assembleDebug -PlempiCommit=<sha>
```

Run it as **one long-lived container**, not one `docker run` per command.
Every command then shares one adb server and one connection to the phone.

## The phone

Wireless debugging, paired once. The key lives in the `lempi-adb` volume.
Two things measured on the moto g power (2021), Android 11, 2026-09-26:

- **Its connect port changes** whenever wireless debugging restarts. Read it
  from the phone's Wireless debugging screen; the pairing dialog's port is a
  different one, used only for pairing.
- **A long transfer switches wireless debugging off.** A 10 MB install
  succeeded once and failed once; a 350 MB copy of music never finished.
  `adb tcpip 5555` made it worse. For bulk copies, a USB cable is the
  reliable route.

**Over USB, adb runs on Windows**: Docker Desktop cannot see USB devices.
Google's platform-tools 37.0.1 for Windows is in `C:\Users\Mango Cat\Tools\`,
checked against the SHA-1 in `repository2-3.xml`, and nothing is on `PATH`. It
uses the key the phone already trusts, copied from the `lempi-adb` volume into
`%USERPROFILE%\.android`, so the two adbs are one identity to the phone. Once
connected by cable, `adb tcpip 5555` gives a fixed network port until the phone
reboots. Windows adb also finds the wireless-debugging port by itself (mDNS),
which Docker's network hides.

The scripts in `spike/` run there too, from Git Bash. `ADB`, `W`, `S` and `PY`
override the container's `adb`, `/w`, `/s` and `python3`. Use short paths for
anything under `Mango Cat`, since `ADB` runs as a command string:

```
ADB=C:/Users/MANGOC~1/Tools/platform-tools/adb.exe W=C:/Users/MANGOC~1/Dev/Lempi \
S=<work dir> PY=python sh android/spike/verify.sh <serial>
```

Each script sets `MSYS_NO_PATHCONV=1`, since Git Bash would otherwise
rewrite `/sdcard/...` into a Windows path before adb sees it.

## The spike's scripts — [`spike/`](spike/)

They run inside `lempi-adb`, with `/w` the repository, `/music` the PC's music
folder, and `/s` a work directory holding the catalogue and `pairs.tsv`.

| script | does |
| :--- | :--- |
| `rebase_paths.py` | points an ingested catalogue at `/storage/emulated/0/Music/Lempi/` and writes `pairs.tsv` |
| `trim.py` | drops albums that did not arrive, so a run does not also test missing files |
| `push.sh` | music to `Music/Lempi/` (`--sync`, resumable), the install, and the catalogue into private storage |
| `launch.sh` | checks every catalogued file is there, replaces the catalogue, launches, reads the log |
| `snap.py` | one snapshot over `/ws` through `adb forward`, with the key from the WebView's cookie store |
| `cpu.sh` | per-thread CPU over a window, from `/proc` ticks |
| `t0.sh`, `t1.sh` | the start and end readings of the screen-off run |

The catalogue was built with Vipunen's own tools: `sql/schema.sql`, then
`tools/ingest_folder.py` per album, then `tools/add_fade_columns.py --write`
(`schema.sql` predates the fade columns), then `tools/split_database.py`.
