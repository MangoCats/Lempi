# Payload conformance fixtures

Test data for [SPEC014](../../docs/spec/SPEC014-payload-schema.md). **Both**
implementations run these: Vipunen's exporter/validator (Python, AGPL) and Lempi's
importer (Rust, MIT). They are data, not code, and belong to neither.

They exist because `[SPEC-SUI-130]` puts two implementations in two languages on
either end of one format, with nothing keeping them in agreement. A fixture with
a stated expected outcome is that missing thing.

`01` is generated from the **real library** — the four Gerardo Frisina tracks
inducted 2026-08-20 — rather than hand-written, so the format is known to survive
contact with data before anything is built on it. Regenerate with:

```
python tools/payload.py data/library.db --like '%Frisina%' \
       --roots 'C:\Users\Mango Cat\Music' -o fixtures/payload/01-valid-four-tracks.json
```

The rest are minimal mutations of `01`, so a diff shows exactly what is being
tested and nothing else.

---

## Expected outcomes

Two independent axes, and conflating them is the mistake these guard against.
**Acceptance** is per bundle and all-or-nothing. **Arrival** is per encoding and
partial by nature — a transfer gap is not a schema disagreement.

| # | fixture | expected |
|---|---|---|
| 01 | `01-valid-four-tracks.json` | **Accept.** 4 encodings, 4 recordings, 71 flavor rows each, all four `local:audio:` ids. |
| 02 | `02-newer-unknown-fields.json` | **Accept.** `payload_version: 2` and unknown fields at three depths (top level, encoding, passage, flavor row). Every one is **stored and not used** `[SPEC-SUI-165]`. Acceptance must not consult the version number. |
| 03 | `03-missing-required.json` | **Reject whole**, target byte-identical. `boundary_src` is absent on one passage and is `NOT NULL` in SPEC008 — unconstructable, and no default may be invented. |
| 04 | `04-conflict-same-mbid.json` | **Reject whole.** Two `recordings[]` for one mbid with different titles. `[SPEC-DF-070]` ranks a payload against the *receiver*; nothing ranks it against itself, so this has no resolution. |
| 05 | `01` imported **twice** | **Second import is a no-op** `[SPEC-SUI-180]`. Row counts identical after the second. A resend after a dropped connection is ordinary, not a mistake. |
| 06 | `01`, one `bundle_path` absent from the bundle | **Accept; that encoding deferred**, the other three land. Reported as awaiting audio — *not* as a schema failure. |
| 07 | `01`, one file present but hashing to something else | **Accept; that encoding rejected as corrupt**, the other three land. `[SPEC-RLK-055]`: bytes present where a row expects them and disagreeing is a failed transfer, never a discovery. The same whether `audio_md5` or the bundle's `sha256` `[SPEC-PL-087]` is the hash that disagrees. |
| 08 | `08-would-overwrite-manual.json` | **Accept, and do not apply** the one changed value where the receiver holds `source = 'manual'` for it. Provenance outranks recency `[SPEC-DF-070]`; a user's correction is never silently overwritten. |
| 09 | `09-fade-fields.json` | **Accept.** `01` plus `fade_in_ms`/`fade_out_ms`/`fade_in_curve`/`fade_out_curve` `[SPEC-SC-046]`, `[SPEC-PL-032]` on every passage — a manual, non-default edit on the first, the fixed default on the rest. `01` itself still shows the fields *absent*, since the real library it is generated from predates `[SPEC-SUI-226]`; `09` is what the same payload looks like once a source has migrated. |

**08 is deliberately `COMPATIBLE`.** Overwriting a manual value is a merge-time
provenance decision, not an acceptance question, and a checker that rejected the
bundle for it would refuse good data over a single field.

06 and 07 are about the bundle rather than the payload, so they are constructed
at test time from `01` plus a doctored file tree rather than committed as JSON.

---

## Checking a fixture

```
python -c "import sys,json; sys.path.insert(0,'tools'); from payload import compatible; \
           print(compatible(json.load(open('fixtures/payload/03-missing-required.json'))))"
```

`compatible()` returns the reasons a payload is unacceptable; empty means import
it. It is the union of the two rules, and both halves are needed —
`missing_required()` alone passes `04`.

## Recorded measurements

Taken from `01`, 2026-08-20, 4 tracks with full 71-characteristic flavor:

| form | per track | whole library (8,330) |
|---|---:|---:|
| JSON, indented | 16.9 KB | 141 MB |
| JSON, compact | 11.0 KB | 91 MB |
| **gzip(compact)** | **1.27 KB** | **10.4 MB** |

Note that `[SPEC-DF-093]`'s "~1–2 KB per track" is right only for the **compressed** form;
readable JSON is roughly nine times that. The decision it was defending survives
unharmed — see [SPEC014](../../docs/spec/SPEC014-payload-schema.md) §5.
