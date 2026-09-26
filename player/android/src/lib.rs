//! The phone's way into the player `[REQ-AND-410]`, as the device spike needs
//! it `[GDE-APP-020]`: three JNI entries, called from
//! `io.github.mangocats.lempi.Lempi`.
//!
//! * `init` -- once per process, before anything else. Hands the Android
//!   context to `ndk_context`, where cpal's AAudio backend reads it back
//!   `[GDE-HST-330]`, and sends the player's log lines to a file: Android
//!   discards a native library's stderr, so without this the spike would be
//!   measured blind.
//! * `start` -- a player, with its web server on loopback behind the launch
//!   key `[REQ-AND-160]`, writing nothing beside the audio `[REQ-AND-200]`.
//! * `stop` -- persist and shut down.
//!
//! **Refused, not crashed.** `start` before `init` returns a named error rather
//! than failing inside the audio stack `[REQ-AND-410]`, and a panic is caught
//! at the boundary and returned as text: unwinding into the JVM is undefined
//! behaviour, not an error the app could show.
#![cfg(target_os = "android")]
#![deny(clippy::print_stdout, clippy::print_stderr)]

use std::ffi::{c_void, CStr, CString};
use std::os::fd::AsRawFd;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};

use jni_sys::{jclass, jint, jobject, jstring, JNIEnv, JavaVM};
use lempi_player::host::{Config, Player};

/// Set once `init` has handed over the context. `ndk_context` asserts on a
/// second initialisation, so this is also what makes `init` safe to repeat.
static CONTEXT: OnceLock<()> = OnceLock::new();
static PLAYER: Mutex<Option<Player>> = Mutex::new(None);

/// A Java string as Rust's, or `None` for a null.
unsafe fn text(env: *mut JNIEnv, s: jstring) -> Option<String> {
    if s.is_null() {
        return None;
    }
    let chars = ((**env).v1_2.GetStringUTFChars)(env, s, std::ptr::null_mut());
    if chars.is_null() {
        return None;
    }
    let out = CStr::from_ptr(chars).to_string_lossy().into_owned();
    ((**env).v1_2.ReleaseStringUTFChars)(env, s, chars);
    Some(out)
}

/// `None` as Java's `null` -- success -- and an error as its message.
unsafe fn outcome(env: *mut JNIEnv, r: Result<(), String>) -> jstring {
    match r {
        Ok(()) => std::ptr::null_mut(),
        Err(e) => {
            let c = CString::new(e.replace('\0', " ")).unwrap_or_default();
            ((**env).v1_2.NewStringUTF)(env, c.as_ptr())
        }
    }
}

/// Run `f`, turning a panic into an error message.
fn guarded(f: impl FnOnce() -> Result<(), String>) -> Result<(), String> {
    catch_unwind(AssertUnwindSafe(f)).unwrap_or_else(|p| {
        let why = p
            .downcast_ref::<&str>()
            .map(|s| s.to_string())
            .or_else(|| p.downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "a panic with no message".into());
        Err(format!("panicked: {why}"))
    })
}

/// `static native String init(Context context, String logPath)`
#[no_mangle]
pub unsafe extern "system" fn Java_io_github_mangocats_lempi_Lempi_init(
    env: *mut JNIEnv,
    _class: jclass,
    context: jobject,
    log_path: jstring,
) -> jstring {
    let log = text(env, log_path);
    let r = guarded(|| {
        if let Some(path) = log {
            let f = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .map_err(|e| format!("cannot open the log at {path}: {e}"))?;
            // The file is kept open by fd 2 after `f` drops.
            if libc::dup2(f.as_raw_fd(), 2) < 0 {
                return Err(format!("cannot send stderr to {path}"));
            }
        }
        lempi_player::logging::install("lempi-android");
        if CONTEXT.get().is_none() {
            let mut vm: *mut JavaVM = std::ptr::null_mut();
            if ((**env).v1_2.GetJavaVM)(env, &mut vm) != 0 || vm.is_null() {
                return Err("GetJavaVM failed".into());
            }
            // Global, so it outlives this call: cpal reads it on its own
            // threads, whenever it enumerates or opens a device.
            let global = ((**env).v1_2.NewGlobalRef)(env, context);
            if global.is_null() {
                return Err("NewGlobalRef on the context failed".into());
            }
            ndk_context::initialize_android_context(vm as *mut c_void, global as *mut c_void);
            let _ = CONTEXT.set(());
        }
        tracing::info!("lempi-android: context handed over; logging to this file");
        Ok(())
    });
    outcome(env, r)
}

/// `static native String start(String listener, String library, int port, String key)`
#[no_mangle]
pub unsafe extern "system" fn Java_io_github_mangocats_lempi_Lempi_start(
    env: *mut JNIEnv,
    _class: jclass,
    listener: jstring,
    library: jstring,
    port: jint,
    key: jstring,
) -> jstring {
    let (listener, library, key) = (text(env, listener), text(env, library), text(env, key));
    let r = guarded(|| {
        if CONTEXT.get().is_none() {
            return Err("init was not called: the player needs the Android context before start"
                .into());
        }
        let (Some(listener), Some(library)) = (listener, library) else {
            return Err("start needs both database paths".into());
        };
        let port = u16::try_from(port).map_err(|_| format!("{port} is not a port"))?;
        let mut slot = PLAYER.lock().map_err(|_| "the player lock is poisoned".to_string())?;
        if slot.is_some() {
            return Err("already started".into());
        }
        let cfg = Config {
            listener: PathBuf::from(listener),
            library: PathBuf::from(library),
            depth: lempi_player::QUEUE_DEPTH,
            device: None,
            echo_offset_frames: 0,
            echo_fleet_min_frames: 0,
            echo_rate: 44_100,
            follow: None,
            mpd_addr: None,
            mpd_root: None,
            web_port: Some(port),
            also_port_80: false,
            web_loopback_only: true,
            web_secret: key,
            writes_beside_audio: false,
            backup: true,
            tag_scan: false,
        };
        let player = Player::start(cfg).map_err(|e| e.to_string())?;
        *slot = Some(player);
        Ok(())
    });
    if let Err(e) = &r {
        tracing::error!("start refused: {e}");
    }
    outcome(env, r)
}

/// `static native String stop()`
#[no_mangle]
pub unsafe extern "system" fn Java_io_github_mangocats_lempi_Lempi_stop(
    env: *mut JNIEnv,
    _class: jclass,
) -> jstring {
    let r = guarded(|| {
        let taken = PLAYER.lock().map_err(|_| "the player lock is poisoned".to_string())?.take();
        match taken {
            None => Ok(()),
            Some(p) => {
                p.persist();
                if p.shutdown() {
                    Ok(())
                } else {
                    Err("the engine did not stop within five seconds".into())
                }
            }
        }
    });
    outcome(env, r)
}
