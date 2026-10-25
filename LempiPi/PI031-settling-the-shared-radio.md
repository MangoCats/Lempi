# PI031: Settling the Shared Radio

**Appliance Record — the design decided 2026-10-25 for every Pi whose Wi-Fi and Bluetooth share one chip, and the measurement under it**

The Pi Zero W, Zero 2 W and 3 B carry Wi-Fi and Bluetooth on one chip and one
antenna, taking turns; what takes the radio away from a playing speaker is in
the [report of 2026-10-24](../reports/bluetooth-on-a-shared-radio-2026-10-24.md).
On 2026-10-25 the maintainer chose what to do about it, preferring changes
that remove work over changes that add it, and dropouts near boot over
dropouts later. This records each decision, where it was built, and the one
measurement made for it.

> **Related:** [PI012](PI012-the-stutter-measured.md) for the stutter and
> the redial in packets · [PI016](PI016-the-redial.md) for why the redial was
> removed · [LP3002](../LempiPlay3/LP3002-power-and-the-bluetooth-glitches.md)
> for lp3-wifi's radio · [SPEC061](../docs/spec/SPEC061-travel-wifi-failover.md)
> for the access point

---

## 1. What was decided

| | decision | built as |
| :--- | :--- | :--- |
| 1 | Stop Wi-Fi roaming 30 s into a connection | `[PI3-AIM-160]`, `lempi-wifi-failover` |
| 2 | Signal strength: placement cannot be guaranteed | nothing built |
| 3 | A supply of the Pi's own: advised, not guaranteed | nothing built |
| 4 | Room in the codec: measure what can be measured | `[PI3-FOUND-850]` |
| 5 | Remove the channel-map seed | `[PI3-FOUND-860]` |
| 6 | Decide "settled", then go quiet | `[PI3-AIM-140]`, `lempi-speaker` |
| 7 | One conditional, early redial | `[PI3-AIM-150]`, `lempi-speaker`, `lempi-btctl a2dp-sample` |
| 8 | A bulk transfer may pause playback, and resumes it after | `[PI3-AIM-170]`, `/transfer/hold` |
| 9 | The travel access point only on request; fbui a button for it; a second LED brightness for "no Wi-Fi" | `[SPEC-WFO-090]`, `[SPEC-FBUI-120]`, `[PI3-LED-020]` |

The next hardware to try is an Orange Pi Zero 2W, later. Everything below was
built and tested on stubs and in the Linux gate on 2026-10-25, with both Pis
off the network; none of it has run on a node yet (section 9).

## 2. The measurement

**`[PI3-FOUND-850]` A link the radio keeps up with sends one packet size; a
failing one keeps changing size.** Item 4 asked whether the codec could be
given room. The answer came from PipeWire's own source and from the HCI
captures already in `logs/`.

PipeWire 1.4.2, the version on these nodes, read from its source
(`spa/plugins/bluez5/a2dp-codec-sbc.c`, `media-sink.c`):

- **No setting caps the bitpool.** The SBC codec reads no property; the
  bitpool it starts at is the lower of the speaker's maximum and 53 (48 kHz)
  or 64 (44.1 kHz) joint stereo.
- **It already makes room, by dropping audio.** When a write to the
  controller would block -- the radio not taking packets -- the packet is
  thrown away ("there will be a sound glitch in any case") and the bitpool
  lowered by 2, at most twice a second. It is raised by 1 a second while the
  send queue stays empty. Floor 12.

An SBC frame is 13 + 2 × bitpool bytes here, and a packet is 17 bytes of
headers plus whole frames, so **the packet size names the bitpool**: 612 B is
five frames at 53, 635 B six at 45, 659 and 671 B six at 47 and 48. The nine
captures of 2026-09-10 record bytes and packets each second; a second whose
bytes are an exact multiple of one of those sizes sent only that size:

| capture | link, by ear | seconds of one size |
| :--- | :--- | ---: |
| `hci-...-modeB-linkstate` | MIDDLETON, Mode B, clean | 164 of 168 |
| `hci-...141610Z-modeB` | MIDDLETON, Mode B, clean | 168 of 171 |
| `hci-...171700Z-oontz-modeB` | Oontz, Mode B, clean | 166 of 171 |
| `hci-...172457Z-oontz-modeA` | Oontz, Mode A, clean | 170 of 174 |
| `hci-...135726Z` | MIDDLETON, a couple of stutters, then redialled | 119 of 131 |
| `hci-...135004Z` | MIDDLETON, Mode A, stutters then clean | 124 of 179 |
| `hci-...160818Z-modeA-linkstate` | MIDDLETON, Mode A, stuttering | 29 of 165 |
| `hci-...165525Z-modeA-seeded` | MIDDLETON, Mode A, seeded, stuttering | 19 of 167 |
| `hci-...140551Z` | MIDDLETON, Mode A, stuttering | 18 of 169 |

The odd seconds of the clean links are their first few, as a stream starts.
In `135726Z` they are the stutters the ear heard at 48-51 s and the redial's
first seconds back; after 91 s, none. In `135004Z` they are the stuttering
stretch `[PI3-FOUND-430]` measured, and none after it.

**This is the speaker-independent measure `[PI3-FOUND-570]` found missing.**
The byte rate needs a baseline from each speaker, because the bitpool sets
it; whether the size moves needs none. It is what the redial of item 7 is
judged on. **Not measured:** the share of *packets* at the commonest size --
these logs count per second -- so the threshold of nine in ten is set between
the two groups above, not from them. And part of `[PI3-FOUND-430]`'s 17%
deficit may be bitpool lowered rather than audio missing; one-second totals
cannot separate the two.

**What it means for item 4.** A lower ceiling would leave the radio slack,
and the Oontz at 45 sounded clean where the MIDDLETON at 53 did not
`[PI3-FOUND-570]` -- two speakers, so not a test of the bitpool. Capping it
needs a patched PipeWire, which is not "less". PipeWire already backs off; the
cost of its backing off is the dropped packet each time, which is the glitch.
Nothing built.

## 3. The seed, retired

**`[PI3-FOUND-860]` `lempi-afh-seed` is removed from both nodes.** It handed
the controller a channel map saved from a settled link. On lempi02w the
controller excluded channels of its own within thirty seconds and the audio
stuttered as before `[PI3-FOUND-530]`; on lp3-wifi it held about ten more
channels and neither arm showed a stall `[LP3-RAD-020]`. Neither trial showed
it stopping a stutter. Both setup scripts now take out the script, its unit
and the saved map; `lempi_afh_channels`, which `lempi-linkstate` prints, stays.

## 4. Settled, then quiet

**`[PI3-AIM-140]` Two minutes held, judged steady, and playing: the keeper
stops asking bluetoothd anything.** The keeper's polling was audible once
`[PI3-FOUND-230]`, and a full tick is still four `bluetoothctl` calls and
more. A settled tick reads the kernel's link list (`hcitool con`, which never
reaches the chip `[PI3-FOUND-730]`), the player's sink and the listener's
choice. One link, to the settled speaker, the stream still on it, the choice
unchanged: done. Anything else ends the quiet, is said once, and the full tick
runs at once. Not while a dwell counts toward a new preferred speaker
`[PI3-AIM-130]`. A `bluetoothctl info` asked every tick and read by nothing
went too.

The Wi-Fi half of "quiet" is item 1.

## 5. One early redial

**`[PI3-AIM-150]` Between 20 s and 3 min into a speaker's hold, the audio
is sampled once; a failing link is redialled once.** A redial is the only
thing that ever cured the Mode A stutter `[PI3-FOUND-440]`; it was removed
for costing 36.2 s of silence on every boot `[PI3-FOUND-450]`. Now it is
earned. `lempi-btctl a2dp-sample 15` counts the media packets handed to the
controller and how many are the commonest size `[PI3-FOUND-850]`. Nine in ten
or more is steady: said, and the connection is judged. Fewer: the link is
dropped and dialled again outbound, once per speaker per boot. The reopen
waits for PipeWire's sink rather than for `Connected: yes`, which is what made
the old silence half a minute `[PI3-FOUND-440]`. A second sample afterwards
says whether it worked; nothing redials twice. A sample that cannot be taken
is said, and not counted as steady.

So the gap, when there is one, is a reconnection's few seconds, early, and
only on a link already failing.

## 6. Wi-Fi held still

**`[PI3-AIM-160]` Thirty seconds after Wi-Fi associates, it is held: no
roaming, no background scans, until the link drops.** NetworkManager's
`bgscan simple:30:-65:300` scans every 30 s below −65 dBm; with four access
points on one name a weak node roams, and each roam starts it again
`[LP3-RAD-010]`. NetworkManager 1.52 has no setting for either. So the keeper
asks wpa_supplicant directly: the network in use gets the BSSID it is on, and
its `bgscan` emptied. Nothing is written; NetworkManager hands wpa_supplicant
a fresh network at every activation, so a hold lasts exactly one association.
A held access point that goes away cannot be roamed from; NetworkManager then
fails the activation and starts a fresh one. A hold that cannot be made says
so once.

## 7. Transfers pause the music

**`[PI3-AIM-170]` A bulk transfer to a shared-radio node holds its playback.**
Every byte over Wi-Fi is airtime the speaker's audio does not get, and a
690 MB export under Bluetooth playback froze the Wi-Fi chip twice
`[PI3-FOUND-830]`. The sender asks `POST /transfer/hold` first and
`/transfer/release/<id>` after. The player pauses only if its radio is shared
(a Bluetooth node not plainly on a DAC of its own) and it is playing; the last
release plays again, unless the listener already did; a hold never released
ends on its own. Held: the player, fbui and `import_bundle` uploads
(`build/lib-defaults.sh`), the star sync's catalogue parts and
`star_distribute`'s uploads (`tools/transfer_hold.py`). A player that restarts
under a hold forgets it, so the release then asks it to play.

## 8. The access point on request, and what the LED says

**`[PI3-LED-020]` With no Wi-Fi link at all, the LED shows a second
brightness, 25% by default.** A node with no screen has to say that the
speaker's Left key would now start its access point `[SPEC-WFO-090]`. While
the LED is in its "on" mode, the Wi-Fi keeper shows `led_nowifi_pct` with no
link (neither a network nor the access point) and `led_brightness_pct`
otherwise, changing it only when that changes. Both are on the Settings page.
It applies wherever the LED setting is offered; lp3-wifi has a screen as well.
The access point itself is SPEC061's `[SPEC-WFO-090]`, and fbui's button
SPEC036's `[SPEC-FBUI-120]`.

## 9. Not yet known

- Whether wpa_supplicant's control socket answers on these nodes, and whether
  emptying `bgscan` stops its scans on the association in use. The keeper
  says which; `iw event -t` for ten minutes after a hold is the check.
- Whether the chip roams by itself: brcmfmac's `roamoff` is read and printed
  with every hold, not set.
- What a node with no link does to the radio. NetworkManager may scan for its
  known networks while disconnected; with the access point no longer up in
  the car, that is the first thing to measure there.
- The redial's threshold on real packets, and what a 15 s `btmon` costs a
  Pi Zero W while it plays.
- That none of it removes a stutter until a stuttering boot shows it.
