# Cross-compilation

## aarch64 (Raspberry Pi Zero 2W, 64-bit Pi OS)

```
docker build -t lempi-aarch64 -f build/Dockerfile.aarch64 .
docker run --rm -v "$(pwd)":/w lempi-aarch64 \
    cargo build --release --target aarch64-unknown-linux-gnu --manifest-path player/Cargo.toml
```

### Why a container

A bare `cargo build --target aarch64-unknown-linux-gnu` from Windows fails twice,
and the two failures are different in kind:

1. `alsa-sys` -- *"pkg-config has not been configured to support cross-compilation"*.
   `cpal` needs libasound headers and libs for the target.
2. with `cpal` removed -- *"linker `cc` not found"*.

Everything else, including symphonia, rubato, rustfft and realfft, compiles for
aarch64 with no configuration at all. Rust's ARM friction here is not Rust's: it
is the single C dependency ALSA brings in, plus a linker. The image supplies both.

Verified 2026-08-10: full symphonia + rubato + cpal stack cross-compiled in 25.8 s
to a 1.9 MB binary, and **executed under ARM emulation**, linking only
`libasound.so.2`, `libgcc_s`, `libm`, `libc` -- all present on stock Raspberry Pi OS.

### Note on the target triple

The Pi currently running MuLibPlay (`bose.lan`) is **armv7l, 32-bit**, not aarch64.
If that machine is the deployment target rather than a 64-bit Pi Zero 2W, the
triple is `armv7-unknown-linux-gnueabihf` and needs its own probe -- the same
Dockerfile pattern applies with `gcc-arm-linux-gnueabihf` and `libasound2-dev:armhf`.


## Host builds on Windows

If `CC` is set globally to a MinGW compiler, bundled SQLite is compiled with
MinGW while rustc links with MSVC, and the build fails on an unresolved
`___chkstk_ms`. With `CC` unset the `cc` crate locates MSVC itself:

```
env -u CC cargo test --release
```

`build/verify-targets.sh` clears it, so verification does not depend on the
developer's environment. Nothing is wrong with the MinGW toolchain — it is
simply the wrong compiler for an `x86_64-pc-windows-msvc` target.

## Verifying every target

```
sh build/verify-targets.sh
```

Runs the suite on Linux x86_64, Linux aarch64 (cross-compiled then executed
under emulation) and the host. It deliberately reports what it cannot cover:
playback through a real audio device. A null sink reports no device rate, so it
cannot detect a sample-rate fault -- which is exactly how unresampled playback
survived until a real device was used `[REQ-HW-147]`.

## Commit before deploying

The Settings page's build stamp (`player/build.rs`, `[REQ-VIS-200]`) exists
so a running instance can say which source it actually came from — a
control that looks missing, a fix that seems not to have landed, an
appliance deployed to twice. That answer is only useful if the commit it
names is one that can still be found and read later: a build made from an
uncommitted working tree stamps `<hash>+dirty` against whatever commit
happened to be checked out, which is not the same claim as "this is commit
`<hash>`" and reads that way to nobody but the person who happened to have
that tree in front of them at the time.

So: **commit before running a deploy** (`build/deploy-local.sh`,
`build/deploy-appliance.sh`, `build/deploy-everywhere.sh`), even to a feature
branch nowhere near ready to merge to `main`. The commit doesn't need to be
merged, or even good enough to survive review — it only needs to exist, so
that six weeks from now "what was actually running when this was tested"
has a real answer instead of a shrug and `+dirty`. Merge to `main` on its
own schedule, same as always; this is only about there being *some* commit
checked out at build time.

## Before testing on hardware, build the binaries

```
cargo build --release        # NOT cargo test
```

`cargo test` builds test harnesses into `target/release/deps/`; it leaves
`target/release/station.exe` untouched. Running hardware tests straight after a
test run therefore exercises whatever binary was there last time. This has
already cost one debugging session: a feature appeared not to work at all, and
the "bug" was a binary four minutes older than the source. If a change seems to
have no effect on hardware, check the binary's timestamp before its logic.
