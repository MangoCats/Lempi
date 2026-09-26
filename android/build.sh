#!/bin/sh
# Build the Android app, inside the `lempi-adb` container [REQ-AND-400]:
#
#     docker exec lempi-adb sh /w/android/build.sh <commit>
#
# One cargo build, three uses [REQ-AND-410]:
#   1. the library, built unstripped for arm64 at API 30;
#   2. the Kotlin interface, generated from that library by the workspace's
#      uniffi-bindgen -- it reads the interface out of the library's symbols,
#      which is why this build is not stripped (a stripped one reported "No
#      UniFFI metadata found", 2026-09-26);
#   3. a stripped copy of the library into jniLibs, for the APK.
# The generated Kotlin lives under app/build/, never in the source tree, so it
# is always the interface of the library it ships with.
set -eu
COMMIT="${1:?usage: build.sh <commit>}"
W=/w
LIB=$W/player/target/android-app/aarch64-linux-android/release/liblempi_android.so
GEN=$W/android/app/build/generated/uniffi
STRIP="${ANDROID_NDK_HOME:?}/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip"

cd $W/player
CARGO_PROFILE_RELEASE_STRIP=none cargo ndk -t arm64-v8a -P 30 \
    build --release -p lempi-android --target-dir $W/player/target/android-app

rm -rf "$GEN"
cargo run -q -p uniffi-bindgen -- generate --library "$LIB" --language kotlin \
    --no-format --out-dir "$GEN"
test -f "$GEN/io/github/mangocats/lempi/ffi/lempi_android.kt" \
    || { echo "build.sh: no Kotlin was generated"; exit 1; }

mkdir -p $W/android/app/src/main/jniLibs/arm64-v8a
"$STRIP" --strip-all -o $W/android/app/src/main/jniLibs/arm64-v8a/liblempi_android.so "$LIB"

cd $W/android
./gradlew --no-daemon assembleDebug -PlempiCommit="$COMMIT"
echo "build.sh: $(ls -l app/build/outputs/apk/debug/app-debug.apk)"
