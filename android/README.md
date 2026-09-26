# The Android app

The Android app `[REQ-AND-400]`: the player in a foreground service, the
lempi skin in a WebView, and a notification that shows what is playing and
carries Pause, Skip and Stop. It is Kotlin, with no AndroidX, and reaches the
player through a **generated interface** `[REQ-AND-410]`: `start`, `command`,
`set_volume`, `state` and `stop` are UniFFI exports in
[`player/android`](../player/android/src/lib.rs), and `init`, which hands over
the Android context, is the one hand-written JNI entry. The device spike
`[GDE-APP-020]` that came before it passed on 2026-09-26 (LOG013); its
measuring scripts are in `spike/`.

Everything runs in the `app` stage of
[`build/Dockerfile.android`](../build/Dockerfile.android), so nothing Android
is installed on the build host.

## Build

```
docker build --target app -t lempi-android-app -f build/Dockerfile.android .
docker run -d --name lempi-adb -v "<repo>":/w -v "<music>":/music:ro -v "<work>":/s \
    -v lempi-adb:/root/.android -v lempi-gradle:/root/.gradle \
    lempi-android-app sh -c 'adb start-server; sleep infinity'

# the library, its Kotlin interface, and the APK -- android/build.sh
docker exec lempi-adb sh /w/android/build.sh <sha>
```

`build.sh` makes one unstripped cargo build, generates the Kotlin from it with
the workspace's `uniffi-bindgen` into the app's build output, strips a
copy into `jniLibs`, and runs Gradle. The generated Kotlin is never committed:
it is always the interface of the library it ships with. UniFFI reads that
interface out of the library's symbols, so it cannot be generated from the
stripped copy. The image's Rust is 1.90, and `Cargo.lock` holds
`cargo-platform` at 0.3.2, since 0.3.3, which `uniffi` 0.32.2 would take,
needs 1.91.

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

**A charge-only cable looks like a working one.** On 2026-09-26 the first
cable charged the phone, but Windows listed no USB device at all, so adb
never had anything to see. If `Get-PnpDevice` shows no "moto g power
(2021)", change the cable before anything else.

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

## Music onto the phone: a bundle

Vipunen sends music with everything it knows about it as a **bundle**
`[SPEC-PL-095]`: a folder, or the same folder as one `.zip`.

```
python tools/export_bundle.py data/library.db --like '%Frisina%'     --root "C:/Users/Mango Cat/Music" -o out/frisina --zip
```

On the phone, any of these imports it `[REQ-AND-230]`:
- share the `.zip` with **Import to Lempi**, or open it with Lempi;
- Lempi's menu, **Import a bundle (.zip)…**;
- Lempi's menu, **Import a bundle folder…**, for the unpacked folder. Android
  asks to allow that one folder and nothing more.

Every file is checked against Vipunen's byte hash before it is placed in
`Music/Lempi/`. The screen reports what was imported, what was there already,
and any file that arrived damaged, which is never placed. The running player
is then asked to rebuild its choices, so the new music can be picked.

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
