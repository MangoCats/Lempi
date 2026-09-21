# PI017: The Hop Map, and a Hypothesis That Died Well

**Appliance Record — the theory that was tested instead of argued**

Split from [PI011](PI011-two-speakers-and-placement.md) on 2026-09-10, which had reached 1,645 lines against `[GOV-DOC-010]`'s 300-line limit by accumulating a day of dated findings under a single heading. It belongs with
[PI013](PI013-theories-withdrawn.md) -- it is withdrawn too -- but it ran
longest and is the only one that was **killed by its own pre-registered
prediction** rather than by another ear-measured guess.

That is why it is kept apart: the theory was wrong and the method was
right, and those are separable lessons.

> **Related:** [PI011](PI011-two-speakers-and-placement.md) is the front door and lists the rest.

---

### `[PI3-FOUND-500]` The hop-map hypothesis, killed in one boot

The hop-map hypothesis of `[PI3-FOUND-490]` predicted that a stuttering Mode A
boot would read `PERIPHERAL`, or show the low channels enabled, or both, and
said that a
stuttering boot reading CENTRAL with the WiFi band excluded would kill it
outright. That is exactly what happened, on the next Mode A cycle, 2026-09-10.

Twenty-two samples of `lempi-linkstate` across 225 s, alongside
`logs/hci-20260910T160818Z-modeA-linkstate.log`:

| | |
| --- | --- |
| `role CENTRAL` | 22 of 22 |
| channels 0-25 excluded (2402-2427 MHz) | 22 of 22 |
| `link_quality` 255, the maximum | 22 of 22 |
| `volume` 106 | unchanged throughout |
| throughput | 40199 B/s, **87.9% of clean** |
| ear | stutters from +45, continuing |

The Pi never became PERIPHERAL, the WiFi band was excluded at every sample,
and the audio was 12% short the whole time. **The hypothesis is withdrawn** --
the sixth in this document, and the first to cost one boot and one command
rather than a series of three-minute listening tests. That is the instrument
earning its cost.

**Three things the run gives us regardless.**

*AFH adapts in about ten seconds, not three minutes.* All 79 channels at
uptime 29.3, down to 44 by 38.6. Any story in which the three-minute settle is
the speaker slowly learning bad channels is dead alongside the main
hypothesis.

*The map never converges.* It moved between 41 and 50 channels for the whole
225 s -- `000000acbdfbdeffbc3f`, `000000ecefbbffffff3f`, `000000a4d7eb7dffff17`
-- so the radio is continuously reclassifying the upper band while the
stuttering continues regardless.

*The controller thinks the link is perfect.* `link_quality` read 255, its
maximum, at every sample, while 12% of the audio failed to get out. It joins
the table in `[PI3-FOUND-420]`: another instrument that reports health through
a fault the listener can hear.

**Where that leaves it.** Role, direction, codec, configuration, volume,
transport state, hop map, link quality, RSSI and transmit power have now all
been read on both a stuttering and a clean link, and every one of them is
either identical or reports perfect health. Nothing the Pi can see or set
distinguishes the two cases. Combined with `[PI3-FOUND-480]`'s attributable
silence, the evidence points inside the Middleton, where nothing here can
reach.

### `[PI3-FOUND-510]` The speaker remembers the band, and that is the difference

The listener's hypothesis, tested 2026-09-10: the Middleton learns this
room's interference, keeps it across a Mode B restart, and loses it across a
Mode A one. Two boots, same sweep, `lempi-linkstate` every 9 s.

| | Mode A (run 5) | Mode B (run 6) |
| --- | --- | --- |
| first sample with a link | 29.3 s, **79 channels, naive** | 32.7 s, **56 channels, low band already excluded** |
| by ~40 s | 44 ch; 2430, 2450, 2466-67, 2472, 2480 excluded | 52 ch, **the settled pattern exactly** |
| through ~220 s | wandering 41-50, up to ten upper exclusions | **stable 51-52, never more than one** |
| throughput | 40199 B/s, 87.9% of clean | 45790 B/s, **100.1%** |
| ear | stutters from +45, continuing | clean |
| logs | `logs/linkstate-...160818Z-modeA.log` | `logs/linkstate-...-modeB.log` |

**The Pi was power-cycled in both.** The listener confirmed it: power off,
Middleton drop tone, power on. So this appliance's controller began with no
channel assessment either way -- which run 5's naive 79-channel first sample
shows directly. **Mode B's adapted map at its first sample therefore cannot
have come from this machine.** The only other party on the link is the
speaker, and the mechanism the specification provides is peripheral channel
classification reporting, which a CENTRAL merges into the map it broadcasts.

**Role and direction are both eliminated, definitively.** Both boots read
CENTRAL at every sample. Direction came out the *opposite* way round from the
association recorded in section 9 -- this Mode A was outbound and stuttered,
this Mode B was inbound and was clean -- so a Mode B link can be inbound and
perfect. `[PI3-FOUND-390]` refuted direction on one boot; this refutes it in
the reverse configuration.

**The ordering favours cause over symptom.** In Mode A the naive map is
present at 29.3 s, before audio starts at about 38, and the deficit is in the
first packets `[PI3-FOUND-470]`. Map first, deficit second. It remains possible
that a bad link makes a controller blame channels, producing scatter as a
symptom -- but that story does not explain a map that is *already adapted*
before the audio begins.

**A fix this suggests, and it is the first to come from evidence.** The host
can write channel classification down to its controller
(`HCI_Set_AFH_Host_Channel_Classification`). If this appliance persisted its
settled map across reboots and applied it at startup, a Mode A boot could
begin adapted instead of naive, without depending on the speaker's memory at
all. Untried, and it should be treated as a hypothesis until a boot tests it.

**Limits, stated plainly.** One boot per mode for the map comparison. The
interference is environmental and varies, so a quiet stretch could flatter
Mode B. The ZigBee reading of section `[PI3-FOUND-500]` is still unconfirmed:
the network's channel is not presently known, and the exclusions that looked
like ZigBee centres remain suggestive rather than established.

### `[PI3-FOUND-520]` Seeding the map: the experiment, armed 2026-09-10

If the speaker's retained channel map is what makes a Mode B boot clean
`[PI3-FOUND-510]`, then this appliance does not need to depend on the
speaker's memory. The host can write its own classification down to its
controller with `HCI_Set_AFH_Host_Channel_Classification` (OGF 0x03, OCF
0x003F), and the controller ANDs that with what it learns. So: save a map
once, while the link is settled and sounding right, and hand it back on every
boot.

`lempi-afh-seed` does that in three verbs -- `save`, `apply`, `show` -- plus a
`boot` mode that applies seven times at ten-second intervals, because BlueZ
powers the adapter up after the unit is ordered to start and a controller
reset silently discards any classification written before it.

Seeded from the settled link on 2026-09-10:

    map 000000fcffffffffff3f -- 52 of 79 channels
      excluded ch 0-25 = 2402-2427 MHz     (this Pi's WiFi, channel 1)
      excluded ch 78-78 = 2480-2480 MHz

Verified end to end on the appliance: saved, decoded, applied, controller
accepted it, map unchanged afterwards as expected for a mask identical to the
one already in use.

> **The prediction, written before the run.** A Mode A power cycle with the
> seed enabled reads **closer to 100% than to 88%** on `lempi-hci-capture`,
> and the map's first sample shows the low band already excluded rather than
> all 79 channels enabled. **If it stutters at ~88% anyway, the hop map was a
> bystander and this comes back out.** Six theories have been withdrawn from
> this document; the seventh gets no more faith than its evidence.

**Two ways it could mislead, named in advance.** A quiet stretch of
environmental interference would flatter any Mode A boot, so a single clean
run is suggestive rather than conclusive -- the comparison that counts is
against run 5's 87.9%, on the same speaker in the same room. And the seed
cannot help with interference the saved map does not describe; if the ZigBee
reading of `[PI3-FOUND-500]` is right and those transmitters move channel,
a stale map is worth nothing.

**The risk it carries.** A saved map is a claim about one room. Seeded
elsewhere, or after the interference moves, it excludes channels that were
fine and costs hop diversity for no benefit. Hence `save` refusing anything
with fewer than the specification's 20 usable channels, `apply` re-validating
before it writes, and the unit shipping disabled.

**Ran the same evening. It failed its own test.** See `[PI3-FOUND-530]`.

### `[PI3-FOUND-530]` The seed worked, the audio did not, and the map is a symptom

The experiment armed in `[PI3-FOUND-520]` ran on a Mode A cycle the same
evening. Logs: `logs/hci-20260910T165525Z-modeA-seeded.log`,
`logs/linkstate-20260910T165525Z-modeA-seeded.log`.

**The mechanism worked.** The journal shows the retry design earning itself:
the first write at 16:55:16 was rejected because the adapter was not up yet,
and the second at 16:55:26 succeeded. A single write at boot would have
silently done nothing and the run would have proved nothing.

**Half the prediction was met.** First sample, uptime 34.3: 47 channels with
the low band already excluded, against run 5's naive 79.

**The other half failed outright.** 39770 B/s, **86.9% of clean**, against run
5's 87.9% and run 3's 87.1% -- statistically indistinguishable -- with stutters
on the usual spacing from +65. The prediction was "closer to 100% than to 88%".
**`[PI3-FOUND-520]` is withdrawn**, the seventh in this document, and
`lempi-afh-seed` was disabled on the appliance the same hour, as the prediction
required.

**But it disconfirms something specific, which is why it was worth running.**
Counting exclusion zones above channel 26:

| | first sample | mean upper zones |
| --- | --- | --- |
| run 5, Mode A naive | 79 ch, 0 | **6.5** |
| run 6, Mode B clean | 56 ch, 0 | **1.8** |
| run 7, Mode A seeded | 47 ch, 5 | **4.2** |

The seed supplied the **low** band. Within thirty seconds the controller had
added five upper-band zones of its own, and it averaged 4.2 across the run.
Mode B, sounding perfect, averaged 1.8.

So the low-band difference between the modes was never the mechanism, and the
causal reading of `[PI3-FOUND-510]` is disconfirmed: Mode A now starts with
exactly the low-band exclusion Mode B had and sounds exactly as bad. What
still separates the modes is the **upper**-band scatter, and since seeding
could not prevent the controller from generating it, the scatter looks like a
controller marking channels bad because packets are failing on them -- a
symptom -- rather than packets failing because channels went unmarked.

The measurement in `[PI3-FOUND-510]` stands: the speaker does carry adapted
state across a Mode B restart. What is withdrawn is the inference that this
state is what makes Mode B sound clean.

**Direction refuted a third time.** This Mode A came up **inbound**; run 5 was
outbound. Both stuttered, both read CENTRAL throughout.

