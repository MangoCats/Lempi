//! Stamp the build with the commit it came from `[REQ-VIS-200]`.
//!
//! A running player must be able to say which source it is, because the
//! alternative is asking a person to remember — and the moment that matters is
//! exactly the moment nobody does: a control that is missing, a fix that seems
//! not to have landed, an appliance that was deployed to twice.
//!
//! **The dirty marker earns its keep.** A hash alone says which commit the tree
//! was *at*, not what was compiled: a build from an edited tree is not that
//! commit, and reporting it as one would be a confident wrong answer of exactly
//! the kind `[PI3-API-030]` refuses.
//!
//! Absent git, or a source tarball with no repository, is not a failure. The
//! version still exists; the hash becomes `unknown`, which is honest and lets
//! the build proceed.

use std::path::Path;
use std::process::Command;

/// Ask git something, or `None` if git or the repository is absent.
fn git(args: &[&str]) -> Option<String> {
    Command::new("git")
        .args(args)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .filter(|s| !s.is_empty())
}

fn watch(p: &Path) {
    if p.exists() {
        println!("cargo:rerun-if-changed={}", p.display());
    }
}

fn main() {
    // **Naming any input replaces cargo's default**, which is to re-run this
    // whenever a file in the package changes. That default is what kept the
    // dirty marker true, so listing only the git files silently traded one kind
    // of staleness for another: HEAD was watched, the working tree was not, and
    // a build from edited sources went on reporting a clean tree. Both halves
    // have to be named now that either is.
    println!("cargo:rerun-if-changed=src");
    println!("cargo:rerun-if-changed=Cargo.toml");
    println!("cargo:rerun-if-changed=build.rs");

    // Asked for rather than guessed at: the repository is a directory up from
    // this crate today, but a worktree or a submodule puts it somewhere else
    // entirely, and a guess that misses simply stops watching without saying so.
    if let Some(dir) = git(&["rev-parse", "--absolute-git-dir"]) {
        let git_dir = Path::new(&dir);
        // HEAD moves on checkout; the branch's own ref file moves on commit;
        // the index moves on both. Watching all three covers every way the
        // answer below can change without a source file changing.
        watch(&git_dir.join("HEAD"));
        watch(&git_dir.join("index"));
        if let Some(head_ref) = git(&["rev-parse", "--symbolic-full-name", "HEAD"]) {
            watch(&git_dir.join(head_ref));
        }
    }

    let hash = git(&["rev-parse", "--short=12", "HEAD"]).unwrap_or_else(|| "unknown".into());

    // **A diff, not a status, and line endings do not count as a difference.**
    // The appliance binary is cross-compiled in a Linux container against a
    // bind-mounted Windows checkout, where the worktree is CRLF and the
    // container's git has no `autocrlf` of its own to undo it: `status
    // --porcelain` there reports 104 phantom modifications, so every
    // appliance build stamped `+dirty` no matter how clean the tree was. A
    // stamp that always says `+dirty` says nothing, and it said it on the one
    // machine where nobody can check by looking.
    //
    // `--ignore-cr-at-eol` was the first fix tried here, and it is not
    // enough: verified live in the actual cross-compile container (git
    // 2.39.5, not the 2.51 on this Windows host, where the flag was first
    // tried and looked like it worked) that the flag changes what `git diff`
    // *prints* -- an all-CRLF file legitimately shows zero hunks -- without
    // changing which paths `--name-only`/`--name-status` lists as changed at
    // all: 275 files, unmoved, with or without it. Testing only on the
    // machine that already had `core.autocrlf=true` set globally proved
    // nothing about the one machine that doesn't -- the exact mistake this
    // comment already warns about, made once fixing it.
    //
    // `-c core.autocrlf=true`, passed to this one invocation rather than set
    // globally in either the container or the mounted repository's own
    // config, fixes it for real: confirmed live back to 0 in the same
    // container. This makes the invocation behave the same way regardless of
    // whatever autocrlf this or any future build environment happens to
    // have configured for itself, rather than depending on it.
    //
    // It answers for the **repository**, while what is watched above is this
    // package. An edit to something the player does not compile — a document,
    // another crate — therefore marks the tree dirty without rebuilding this
    // binary, and the stamp will say so only at the next rebuild. That is the
    // right way round: the stamp describes the sources the binary was built
    // from, and those have not changed.
    //
    // Counted, not just asked yes/no `[REQ-VIS-200]`: the Settings page names
    // *how many* files, the same figure Vipunen's own `/system` page already
    // shows `[SPEC-SUI-211]`, one `git diff` rather than the two a count-then-
    // ask-again pair would cost.
    let dirty_files = git(&["-c", "core.autocrlf=true", "diff", "--name-only", "HEAD"])
        .map(|out| out.lines().filter(|l| !l.trim().is_empty()).count())
        .unwrap_or(0);
    let dirty = dirty_files > 0;

    let stamp = if dirty { format!("{hash}+dirty") } else { hash };
    println!("cargo:rustc-env=LEMPI_GIT={stamp}");

    // Branch, commit date and subject -- the same fields Vipunen's own
    // `/system` page shows `[SPEC-SUI-210..213]`, so "which build is this"
    // reads the same way from either side of the handoff. Compile-time,
    // matching `LEMPI_GIT` above: a running binary's own answer cannot
    // change from under it, the same reasoning that made `[SPEC-SUI-211]`
    // read Vipunen's build once at startup rather than per request.
    let branch = git(&["rev-parse", "--abbrev-ref", "HEAD"]).unwrap_or_else(|| "unknown".into());
    let commit_date = git(&["show", "-s", "--format=%cI", "HEAD"]).unwrap_or_else(|| "unknown".into());
    let commit_subject = git(&["show", "-s", "--format=%s", "HEAD"]).unwrap_or_default();
    println!("cargo:rustc-env=LEMPI_BRANCH={branch}");
    println!("cargo:rustc-env=LEMPI_COMMIT_DATE={commit_date}");
    println!("cargo:rustc-env=LEMPI_COMMIT_SUBJECT={commit_subject}");
    println!("cargo:rustc-env=LEMPI_DIRTY_FILES={dirty_files}");
}
