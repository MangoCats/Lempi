# GUIDE024: Measuring Two Speakers, And What The Clock Does To It

**Development Guidance — written 2026-09-18 after a listener reported the
follower wandering between 40 ms and 900 ms of the node it follows**

Two questions, one instrument, and a cause neither of them was looking at.
The correction loop turned out to be working exactly as designed; what it was
correcting was partly not there.

> **Related:** [GUIDE017](GUIDE017-echo-correction.md) `[GDE-ECHO-340]` — the loop · [GUIDE018](GUIDE018-echo-invalidation.md) `[GDE-ECHO-365]` — clocks that disagree · [GUIDE021](GUIDE021-echo-review.md) `[GDE-ECHO-371]` — the read of the built path · `tools/echo_skew.py` — the instrument

---

## 1. An instrument that does not correct for anything

**`[GDE-ARC-037]` Ask each node when it reached a sample, not what time it is
now.** `tools/echo_skew.py` replaces `echo_skew.sh` for anything longer than
a spot check.

The shell version ssh'es into each node in turn, curls the socket by hand,
and stamps each reading with `date` on the node — then subtracts the gap
between the two readings. It settled `[GDE-ECHO-344]` and it still works, but
it reads the two nodes *seconds apart* and corrects for it, it reads
`position_ms` (which does not subtract the device's presentation delay
`[AirPosition]`), and one sample cannot tell 40 ms of skew from 40 ms of the
ring jitter every anchor carries `[LOG-P4-010]`.

The Python version reads the `echo.anchor` both nodes already publish. An
anchor says *this node reached sample S of passage P at its own wall time T*,
with the device delay already subtracted. Both timestamps are the nodes' own,
so the polling jitter and the network delay drop out of the arithmetic rather
than being corrected for. It samples continuously and reports a distribution,
because a distribution is the only thing that can distinguish a small skew
from noise.

Measured against the live pair, it is steady to about **50–60 ms of spread**,
which is the anchor's own floor and matches `[LOG-P4-010]`. It agreed with
the follower's own residual to **10 ms** (688 measured against +678
reported), which is worth recording: `[GDE-ECHO-378]` warns that a loop
cannot be its own witness, and here the witness and the accused independently
agreed.

---

## 2. What it found the loop doing

**The loop was working.** Watched across a passage boundary, the follower
went from **688 ms behind to 179 ms** in one transition — the 500 ms bite
`[GDE-ECHO-341]` allows, landing exactly as designed.

So the listener's "40 ms up to 900 ms and more" is not a broken loop. It is
the **convergence path, seen from inside**: a join lands a few hundred
milliseconds to a second late `[GDE-ECHO-342]`, and the offset correction
then takes at most 500 ms per passage boundary. At four to six minutes a
passage, recovering from a 900 ms join costs **two boundaries and ten
minutes**, all of it audible.

Three things make that worse than it needs to be, and all three are visible
in three hours of one node's log:

- **The rate loop had never run.** Zero `echo-rate:` lines in three hours.
  `RateEstimate` needs fifteen unbroken minutes `[GDE-ECHO-356]`, and every
  offset correction clears it. A node that is persistently out corrects every
  passage, so the window never reaches its minimum span and the *slope* is
  never corrected at all. The two halves of `[GDE-ECHO-340]` are not
  independent: while the offset loop is busy, the rate loop cannot start.
- **"Join immediately" does not mean "align immediately".** The mid-passage
  join is suppressed whenever the node is already coming to the master's
  passage `[GDE-ECHO-343]`, unless the residual is past
  `OFFSET_REJOIN_BEYOND` (1.5 s). That suppression is right — joining at
  every boundary re-imposes the join bias and cuts the ring — but it means
  the setting a listener toggles does nothing for the 40–900 ms case, which
  is the case they are looking at. The control and the complaint are about
  different things.
- **A correction is once per master passage**, so a node that misses the
  window waits a whole passage for the next attempt.

---

## 3. The cause that was not in the loop

**`[GDE-ARC-038]` The follower's clock was not the clock the design assumes,
and a clock error becomes a playback error.** This is the finding.

The name says it: `[GDE-ECHO-160]` calls `WallNanos` "the chrony-disciplined wall clock". That
is an assumption, and nothing checks it. Measured 2026-09-18:

| | daemon | reference | quality |
| :--- | :--- | :--- | :--- |
| `bose` | chrony | `smartboardpc.lan` | 55 us, RMS 2.7 ms |
| `lp3-wifi` | systemd-timesyncd | `2.debian.pool.ntp.org`, over the internet | +20.6 ms offset, 22.6 ms jitter, poll 4–34 min |

Two different daemons, two different references, one of them across the
internet with 41 ms of path delay. `timesyncd` is SNTP: it corrects at poll
time and leaves the crystal to drift in between — at the ~14 ppm this fleet
shows `[LOG-P4-130]`, a 34-minute gap is about 28 ms of free drift on top of
the standing offset.

*First attempt at quantifying the gap, and why it is not quoted here.* An
ssh round trip from a Windows box, midpoint-corrected, read 90–105 ms across
four runs — and that consistency was stable path asymmetry, not accuracy. The
same method later read 186 ms on a node chrony said was within 113 us, and
the reference machine's own clock moved 52 ms between two sessions while
`bose` sat at 55 us. **Retracted.** The right measurement needs no network
timing at all: every node is disciplined to one LAN server, so each node's
own `chronyc tracking` offset is against the *same* reference and the
difference between two of them is the inter-node error, common-mode
cancelled. `tools/echo_skew.py --clocks` reports exactly that, beside the
skew, so a reading is never ambiguous about which of the two it is.

**And that is not a harmless measurement error.** The follower reads the same
disagreement, believes it is a position error, and moves *real audio* to
remove it — putting the sound roughly 100 ms out in the other direction to
make two clocks agree. The correction loop is faithfully converging on the
wrong target. It does not stay a clock problem.

`clocks_agree` cannot catch this: its tolerance is thirty seconds, sized for
a node booted without an RTC before NTP has stepped it `[GDE-ECHO-365]`, not
for the tens of milliseconds a listener notices. Nothing in the fleet checks
that a node's clock is disciplined *well enough to be followed*, as against
merely being in the right century.

---

## 4. What follows

In order of how much of a listener's experience it returns:

1. **Done 2026-09-18: `lp3-wifi` is on chrony**, against the same
   `smartboardpc.lan` the other three use, same `lempi-fleet.sources`, same
   md5. It took the measured skew from **688 ms to a median of 12 ms** with
   no change to player code — which is the whole finding in one number. The
   install went through `sudo overlayroot-chroot` as `[IMPL-BOS-185]`
   requires; see §5 for what that turned out to involve.
2. **Done: the preflight now says so at every boot** `[PI-PRE-052]`. It
   names the disciplining daemon and its reference, warns specifically that
   `systemd-timesyncd` is the wrong tool with the reason, and warns again if
   nothing is disciplining the clock at all. This is the check
   `[GDE-ECHO-300]` asked for in its own second sentence and never got.
3. **Let the offset and rate loops coexist.** Clearing the rate window on
   every offset correction means the slope is never learned on exactly the
   nodes that need it most.
4. **Done: the setting now means both halves, and says so**
   `[GDE-ARC-041]`. "Join immediately" governed only the first moment; after
   it, a residual between the endgame band and the rejoin threshold was
   corrected *solely* at a passage boundary, so nothing acted for the four to
   six minutes in between. With the setting on, whatever the boundary will
   not take now goes to the frame trim at once — mid-passage, monotonic and
   inaudible. The control is relabelled "Get in step: straight away, and stay
   there", because the old label was the right behaviour under the wrong
   name and a listener read it, correctly, as a promise about alignment.

   **It still cannot mean "instantly", and the panel does not say so.** The
   inaudible actuator is capped at `ECHO_DEBT_PPM`; instant alignment costs
   an audible cut, which is exactly what the join half already spends. A
   control that promised more than physics allows would be this document's
   own subject matter one more time.

**The only instrument that can settle any of this against a listener is a
microphone.** Everything here — this tool, the follower's residual, the
anchors both rest on — is downstream of two clocks agreeing. Record both
speakers on one device and cross-correlate, and the question "what does a
person hear" stops depending on what either node believes the time is.

---

## 5. Installing a package on an overlay root, as it actually goes

**`[GDE-ARC-039]` `[IMPL-BOS-180]`'s one-liner is right and is not
sufficient.** `sudo overlayroot-chroot apt-get install -y <pkg>` is the
correct tool — the unlock hatch is what took `bose` off the network
`[IMPL-BOS-175]`, doing this same chrony install — but three things happened
on the way that the worked example does not mention, all of which would read
as success.

**The wrapper swallows the inner exit status.** The first attempt failed with
`Temporary failure resolving 'deb.debian.org'` and `overlayroot-chroot`
returned **0**. A caller checking `$?`, or a script wrapping that one-liner,
is told the package is installed. This is the same fault as
`[GDE-ARC-035]` and `[GDE-ECHO-386]`, one layer further out: write the inner
status down inside the chroot (`... ; echo EXIT=$? >> /tmp/log`) and read it
from `/media/root-ro/tmp/` afterwards, because the wrapper will not carry it.

**DNS does not work inside the chroot.** The durable `/etc/resolv.conf` has
no nameserver — the working one is written by DHCP into the *overlay* layer
at boot, which the chroot cannot see. Lend it one for the install and put the
original back; leaving the durable copy empty is correct, since DHCP rewrites
the live one every boot.

**"Write twice" applies to the package, not only to config.** After a clean
chroot install the durable layer had `chronyd`, `/etc/chrony/chrony.conf` and
the service symlinks — and the live system had none of it, because overlayfs
does not surface lower-layer changes made underneath a mounted overlay. Worse,
a live `apt-get install` then does **nothing**: the dpkg *state* does come
through from the lower layer, so dpkg believes the package is configured and
unpacks no conffiles. chrony started and died on
`Could not open /etc/chrony/chrony.conf`. The fix is to copy the conffiles
from `/media/root-ro/etc/...` into the live tree by hand, then start the
service. Verify the durable copy for the reboot and the live one for now —
both, separately, which is `[GDE-DEP-070]` said twice.

A last detail worth not rediscovering: the chroot leaves `/media/root-ro`
mounted read-write and a `remount,ro` returns `EBUSY`, because the live
overlay holds that filesystem as its own lowerdir. `install-player.sh`
already knows this and says so rather than implying otherwise; it returns to
read-only at the next reboot.
