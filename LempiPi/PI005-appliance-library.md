# PI005: The Library on the Appliance

**Appliance Record — getting the real library onto lempi02w, and what it cost**

The appliance runs from one SQLite file named outright by its unit
(`ExecStart=… /srv/library/library.db`). Nothing swaps that file on its own, and
this records the one time it was swapped, what the transfer had quietly broken,
and how long the repair actually took.

> **Related:** [SPEC012](../docs/spec/SPEC012-library-relink.md) for why binding
> is by content and not by path · [SPEC013 §5](../docs/spec/SPEC013-vipunen-console.md#5-export--new-music-to-a-remote-lempi)
> for the bundle that carries new music · [PI004](PI004-speaker-operation.md)

---

## 1. The appliance takes the real library

**`[PI5-LIB-010]` Swapped 2026-08-20, and nothing was reverting on its own.**
The appliance had been running a 31-file test library while the full one sat
beside it as `lempi-new.db`, untouched since 17 August. The unit names its path
outright — `ExecStart=… /srv/library/library.db` — so no power cycle was ever
going to change that.

> `lempi-new.db` was **deleted 2026-09-11**: it had been the staging copy for
> this swap and nothing had touched it since, three weeks after the swap it
> staged `[PI-PRE-098]`. The name survives in `BosePi/seed-library.sh`, which
> uses it as a staging path of its own on `bose` — that is a live filename
> there, unrelated to this dead one.

The test library's own listening was discarded deliberately: 252 plays made
against 31 files during testing, against 37,237 that describe six years of real
listening. A straight copy was therefore right, where `[SPEC-SUI-100]`'s
merge-don't-overwrite argument would otherwise apply — the rule protects
irreplaceable listening, and test plays are not that.

| | before | after |
| :--- | ---: | ---: |
| files / radio passages | 31 / 35 | **5,709 / 8,330** |
| plays · preferences | 37,489 · 3,261 | **37,238 · 3,261** |
| programmes · seeds | 8 · 49 | 8 · 49 |

**`[PI5-LIB-020]` 277 paths were rewritten, and the prediction held exactly.**
`[SPEC-RLK-025]` said 276 of the shipped paths would carry a Windows private-use
codepoint and fail to resolve on Linux. Measured before the swap: **276 carried
one, and the same 276 were precisely the paths that did not resolve.** Relink
rewrote 277 — the 276, plus one row bound to `The More We Get Together_2.mp3`
and rebound to the copy without the suffix, which is `[SPEC-RLK-120]` choosing
the first in sorted walk order.

Afterwards: **5,705 of 5,705 paths resolve, 0 missing, 0 private-use codepoints
left, 0 corrupt, `integrity_check` ok.** `History: America's Greatest Hits` now
browses with a real colon.

**`[PI5-LIB-030]` The four newest tracks had to be imported again**, because the
staged database predates them: it is an 17 August snapshot and they were
inducted on the 20th. Relink reported them as `unknown` — audio present, no row
claiming it — which is exactly the state `[SPEC-RLK-090]` says is ingest's
problem and not relink's. Re-running the bundle import restored them in one
command, against a **running** player and with no lock contention, and
`POST /library/reload` `[IMPL-SUI-078]` made them selectable without a restart.

> **Cost, for planning.** 2 h 37 m of hashing `[SPEC-RLK-075]`, during which the
> player is stopped and the appliance is silent. That is the number to quote
> before starting one, not the hour SPEC012 originally estimated.

---

## 2. Turning it off on purpose

**`[PI5-PWR-010]` The settings page can shut the appliance down.** An appliance
whose only interface is a web page has no other way to be switched off, and
pulling its power is how an SD card is corrupted and a database left mid-write.
`POST /power/off` does three things in order, and the first is what a bare
`poweroff` misses:

1. **`Command::Persist` writes the resume point now.** It is otherwise saved on
   an interval `[REQ-VIS-155]`, so a *deliberate* shutdown would still lose up
   to that much position — in exactly the case someone took care over.
2. **systemd stops the services and unmounts**, rather than power being cut
   under a live filesystem.
3. **The reply is 202, not 204.** Accepted, not done: the process answering is
   about to be stopped, so it cannot honestly claim the machine finished.

The button asks twice, because there is no third press available — nothing on
that page can switch the machine back on, and the walk to the plug is the cost
of a misclick `[PI3-UI-030]`.

**`[PI5-PWR-020]` Verified without using it.** Powering the appliance off to
prove it powers off is a poor trade when the evidence can be had otherwise:
`GET /power/off` answers **405**, which is a POST-only route present (a missing
one answers 404); `systemctl --dry-run poweroff` reports it permitted and ready;
and `player_state` is observably being written. The one untested step is the
transition itself.

**`[PI5-PWR-030]` On power-up it resumes playing, if it was playing.**
`player_state` has recorded the play flag since the row existed, and the session
read that column and **threw it away** — so an appliance that lost power, or was
shut down deliberately, came back holding its place and silent. Restoring the
position but not the intention is half a resume.

It is safe against a missing speaker without waiting for one, and the mechanism
was already present rather than added: playing marks the supervisor's interest,
a dummy sink is treated as a *failure* `[PI3-API-030]`, and a failed output
makes `path.audible()` false — so the engine advances nothing while nobody can
hear it. One change was needed: the supervisor checked for a dummy up to
`WATCH` (20 s) after playback began, which is 20 s of clock racing through music
nobody hears. It now checks **immediately** on any transition into playing,
which is exactly when a speaker is most likely to be absent.

> **Verified 2026-08-20** on the appliance: `resuming passage 15192 at 30.2s`,
> then `resuming playback: it was playing when it last stopped`, then the
> position advancing under a real sink. The dummy path is by construction and by
> the immediate check; it was **not** observed, because the speaker reconnected
> before the test could run.

---

## 3. Two things this work exposed

**Currently:** `install-player.sh` polls the new build to a bounded deadline
instead of assuming it will have answered by a fixed sleep, and the
appliance's `/etc/sudoers.d/` still carries the Raspberry Pi OS default
(`pi ALL=(ALL) NOPASSWD: ALL`) alongside `lempi-btctl`'s narrow verb-set rule —
accepted, since this is a development machine. What exposed each, and why the
sudoers gap was investigated and left in place, is history: see
[PI008 §3](PI008-appliance-bringup-history.md#3-what-the-real-library-swap-exposed).
