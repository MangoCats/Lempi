# GUIDE022: The Follower Nothing Builds

**Development Guidance — written 2026-09-18, split out of
[GUIDE021](GUIDE021-echo-review.md) when acting on it grew past that document's
limit**

Three things a read of the source could not see, found while fixing what
[GUIDE021](GUIDE021-echo-review.md) `[GDE-ECHO-371]` did see. Two are small
corrections to that review. The third is larger than anything in it, and is
the same fault one level up: a mechanism that declines silently.

> **Related:** [GUIDE021](GUIDE021-echo-review.md) `[GDE-ECHO-371]` — the review this came out of · [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — the loop being reviewed · `player/Cargo.toml` — where the gate is · `build/deploy-appliance.sh` — what does not pass it

---

## 1. The gate

**`[GDE-ECHO-379]` `player/src/echo_client.rs` is behind a cargo feature that
nothing in the tree turns on.** So the follower is absent from every binary
any script here produces, while the control that starts it is present in every
interface.

`echo-client` is not in `default`. Searching the tree for anything that enables
it finds nothing: not `cargo test`, not `build/verify-targets.sh`, not
`build/deploy-local.sh`, not `build/deploy-appliance.sh` — whose `FEATURES` is
`${LEMPI_FEATURES:-}`, empty unless a caller sets it — and no document
anywhere names `--features echo-client`. The live two-node measurements
recorded in `[GDE-ECHO-341]`, `[GDE-ECHO-342]` and `[GDE-ECHO-347]` were
therefore taken against a binary built by hand, by a route nothing in the
repository describes.

What that costs is not that following is missing. It is **how** it is missing.
`lempi.rs` spawns the follower task inside `#[cfg(feature = "echo-client")]`,
and refuses the `--follow` flag loudly without it `[GOV-SRC-040]` — that half
is right. But the setting is the control `[SPEC-ECHO-010]`: the settings panel
offers *Follow this node*, the browser posts a host, `SetEchoFollow` stores it,
`remember_settings` persists it, and the panel then shows a node configured to
follow. Nothing reads it. `follow_status` stays empty, which the interface
renders as a node that has simply not connected yet. A listener has no way to
tell that from a network fault.

That is `[GDE-ECHO-378]`'s shape exactly, and the deploy script's own comment
about `vipunen-support` already states the principle it breaks: *"a UI that looks
like it works and does not, which is worse than a 404."*

**`[GDE-ECHO-384]` It also means the findings were never compiled.** Every
fault GUIDE021 records in `echo_client.rs` — the early returns
`[GDE-ECHO-374]`, the mid-join budget `[GDE-ECHO-375]`, the flow comparison
`[GDE-ECHO-376]` — sat in a file that `cargo test` does not build. Not
untested: **uncompiled**. A rename would not have caught them, clippy would not
have caught them, and no test could have caught them, which is a fair part of
why they survived to be found by reading.

**`echo-client` is now in `default`, decided 2026-09-18 by what the fleet
turned out to be.** The deployed binaries settled it rather than the argument
did. `pi@bose` carries five `echo-follow:` strings and none of the
`#[cfg(not(...))]` refusal, so it *was* built with the feature — by hand,
through `LEMPI_FEATURES`, by a route nothing here describes. `pi@lempi02w` has
neither. Two nodes of one fleet, differing in whether a control works, because
the correct build depended on an environment variable a person has to
remember.

That makes the third way out — hiding the control when the feature is off —
the wrong one: it would make the divergence a feature. And it makes the second
— setting `LEMPI_FEATURES` at deploy time — the mechanism that caused this,
chosen again. It also means a deploy that did *not* set it would have silently
removed the follower from `bose`, which is the fault running in reverse.

So: `default = ["echo-client"]`. The dependencies were always free —
`tokio-tungstenite` and `futures-util` are in the graph via axum's own `ws`
feature — and the cost is about 400 kB of appliance binary, against a control
that is offered on every node and worked on one.

---

## 2. Two corrections to the review

**`[GDE-ECHO-381]` `echoprobe` does not exercise `trim_for`, so nothing
does.** `[GDE-ECHO-377]` names it as the one remaining caller. It calls
`trim_for(st, None, now)`, and that function's second act is `let local =
local?` — it returns on a `None` air position before reaching any arithmetic.
The probe's own "would `DropFrame`" line is unreachable, and its comment
beside it ("no local air position here, so this is not expected") is the
closest thing in the tree to saying so.

The verdict is unchanged and slightly stronger: `residual_ns` and `trim_for`
were not two formulas with one caller between them, they were two formulas
with none. That is why deleting the first cost nothing and why the second now
carries a note about the clock translation an adopter must add
`[GDE-ECHO-366]`.

**`[GDE-ECHO-382]` The mid-join threshold is 600 ms, not "about 500".**
`[GDE-ECHO-375]` computes when a join lands `TooLate` from `skip_lead_ms +
echo_prep_ms` against the 1 s margin, and omits `ECHO_START_LATE_LIMIT`'s own
100 ms of grace. Immaterial to the finding — `ECHO_PREP_MAX_MS` is 2000 either
way, and the first join on a cold node guesses 400 — but the arithmetic is
worth having right, because it is the figure a fix has to clear.

---

## 3. What to take from it

**`[GDE-ECHO-383]` A test that cannot compile the file is not a weaker test
than one that does not assert; it is a different kind of absence, and only one
of them shows up in a count.** `[GDE-ECHO-378]` says the boundary between
deciding and acting was the only place in this subsystem with no assertion
across it. That was true of the code that was built. The subsystem also had a
whole file with no *compilation* across it, and 506 passing tests said nothing
about either.

The cheap general guard is the same one `[GDE-ECHO-378]` asks for, applied to
the build rather than to an actuator: **a feature that gates behaviour a
running interface still offers must be named somewhere a build reads**, or the
gate is not a gate, it is a silence.

**`[GDE-ECHO-385]` A third instance turned up while shipping the first two,
and it is the same sentence again.** `web::Snapshot` carried `echo` and not
`echo_node`, while `skin.js` reads `s.echo_node` — so `renderEchoNode` took
its `if (!n) return;` twice a second and the delay, calibration and follow
panel showed nothing on every node that has ever run. Neither guard could see
it: the Rust test lists snapshot fields by hand from the server's side, and
`build/verify-skins.js` renders against fixture snapshots written by hand too,
so both agreed with each other and neither with the skin. The new test derives
the list from the skin — every `s.<field>` in `skin.js` must be a field the
snapshot serialises — which found `echo_node` and one more, `sink`, read by
the Bluetooth helper and carried by no snapshot ever.

Three faults, three mechanisms, one shape: **an actuator, a build and a wire
each declining silently one call below something whose tests pass.** The guard
that catches all three is not a better test of either side. It is an assertion
that reads *the other side* — what admission will really spend, what the build
really compiles, what the skin really asks for.

---

## 4. And the gate that was supposed to catch all of it

**`[GDE-ECHO-386]` `build/verify-targets.sh` printed three failing suites and
then said `ALL TARGETS PASS`.** Found by running it, on 2026-09-18, to prove
the fixes above before shipping them.

Each stage read, in effect:

```sh
docker run ... cargo test ... | grep -E "^test result: ok\.|FAILED" || fail=$((fail+1))
```

The pattern contains `FAILED` so the failure is *shown* — and that match is
what makes `grep` exit 0, so the `||` never fires. `$?` is the filter's
status, not the command's. It is the first habit CLAUDE.md §6 names, in the
script written to enforce the discipline, and it had made the gate
decorative: stage A ran 519/521 and stage B 518/521, both red, both reported
as a pass, and the script exited 0.

The status now comes from the command and the grep only chooses what is
shown. **An empty result counts as failure too** — a run that printed no
`test result:` line did not run, and a gate that cannot tell that from a pass
is the same fault in a different hat `[GDE-ECHO-547]`.

**`[GDE-ECHO-387]` What it was hiding was not subtle.** Two of the three
failures were `ffmpeg not available: No such file or directory`:
`relink::hash_encoded` shells out to ffmpeg, two bundle-import tests hash a
real file through it, and neither Docker image had it installed. Those tests
have failed on every containerised target since **2026-09-06** — twelve days,
behind a green gate. ffmpeg is added to `Dockerfile.linux` and to stage B's
runtime image rather than skipped around: it is a real dependency of the path
under test, so an image without it is testing something else.

The third was honest and wanted the opposite treatment.
`output::tests::a_brief_hold_costs_no_glitch` asserts that a 100 µs lock hold
is waited out inside `LOCK_WAIT` — a **wall-clock** property, which under qemu
is not a property of the code at all. It now skips loudly on `LEMPI_EMULATED`,
which stage B sets, because a red line that is always red teaches a reader to
ignore red lines.

**`[GDE-ECHO-388]` And the follower was not on the deploy list.**
`deploy-everywhere.sh` named `pi@lempi02w` and `pi@bose`. `lp3-wifi` — aarch64,
overlay root, running `lempi.service`, and *the node that actually follows* —
was not there, so it sat four commits behind while the two that were got kept
current. It was reachable the whole time under a name nothing but
`tools/echo_skew.sh`'s usage line recorded: `lempiplay3` answers ping, its ssh
host key is under `lp3-wifi`. That is `[SPEC-SUI-227]`'s own failure mode, and
the one node where the echo correction path runs at all was the node it
happened to.
