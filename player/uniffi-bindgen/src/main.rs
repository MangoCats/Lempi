//! UniFFI's generator, as a binary of this workspace [REQ-AND-410]:
//!
//!     cargo run -p uniffi-bindgen -- generate --library <liblempi_android.so> \
//!         --language kotlin --out-dir android/app/src/main/java
//!
//! Built from the same `uniffi` release as the scaffolding in lempi-android,
//! so the two cannot drift apart.
fn main() {
    uniffi::uniffi_bindgen_main()
}
