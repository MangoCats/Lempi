plugins {
    id("com.android.application")
}

// Every build says what it was built from `[REQ-AND-400]`: android/build.sh
// passes the commit, and a build without one says so rather than guessing.
val lempiCommit = (project.findProperty("lempiCommit") as String?) ?: "unknown"

android {
    namespace = "io.github.mangocats.lempi"
    // 36 is the level Play requires an app to target from 2026-08-31, and the
    // floor is Android 11 `[REQ-AND-100]`.
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.mangocats.lempi"
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "0.1-spike+$lempiCommit"
        // The phone is arm64 (moto g power 2021 reads arm64-v8a); the Rust
        // library is built for that ABI alone.
        ndk { abiFilters += listOf("arm64-v8a") }
    }

    buildFeatures { buildConfig = true }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    // The library arrives already built by cargo-ndk, in release; nothing is
    // gained by AGP looking for an NDK of its own to strip it with.
    packaging { jniLibs { keepDebugSymbols += listOf("**/*.so") } }
}
