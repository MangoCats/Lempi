// The Android app `[REQ-AND-400]` -- for now, the device spike `[GDE-APP-020]`.
// Built in the `app` stage of `build/Dockerfile.android`; see android/README.md.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "lempi"
include(":app")
