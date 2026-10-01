# Essentia: the extractor and the flavor models

Third-party files Vipunen needs to analyse a passage's **flavor**, committed
here so that a fresh clone has them. They are not Lempi's work and are not
under Lempi's licences; each keeps its own, below.

Until 2026-10-01 they lived untracked in `data/essentia/`. That folder was
lost when this repository was re-seeded, and flavor extraction then failed on
every passage without saying why. A tool that cannot find them now stops at
once and names what is missing.

| Path | What | Used by |
| :--- | :--- | :--- |
| `streaming_extractor_music.exe` | Essentia's music extractor, Windows 32-bit: audio to low-level features | `tools/extract_library.py` |
| `svm_beta1/` | The 18 Gaia/SVM classifier models: low-level features to the 71 flavor dimensions | `tools/gaia_classify.py` |

`LEMPI_ESSENTIA_DIR` points the tools at another copy.

## Where they come from

Both are AcousticBrainz's own published files, unmodified, from the MetaBrainz
archive. Why these exact versions -- the extractor vintage behind
AcousticBrainz's data, and the *beta1* models rather than the later beta5 --
is [LOG002](../../docs/LOG002-feature-reproduction-investigation.md)
`[LOG-FEX-062]`, `[LOG-FEX-093]`.

- **Extractor**:
  `https://data.metabrainz.org/pub/musicbrainz/acousticbrainz/extractors/essentia-extractor-v2.1_beta2-1-ge3940c0-win-i686.zip`,
  which holds only `streaming_extractor_music.exe`. The zip's SHA-1, as the
  archive's own `sha1sum` file gives it and as verified on download
  2026-10-01: `271230742cb92ee16347478c763ed2454a1d4e01`. Run in LOG002, it
  reported itself as `essentia 2.1-beta2`, git `v2.1_beta2-1-ge3940c0`.
- **Models**: every file in
  `https://data.metabrainz.org/pub/musicbrainz/acousticbrainz/svm_models/` --
  for each of the 18 classifiers a `.history` and a `.param`, and
  `accuracies_v2.1_beta1.html`: 37 files, 83 MB. No checksums are published
  for them.

Linux and macOS builds of the same extractor are in the same archive directory;
only the Windows one is here, because only the Windows desktop analyses.

## Licences

- **The extractor** is Essentia, by the Music Technology Group (MTG),
  Universitat Pompeu Fabra, under the **GNU Affero General Public License v3**
  ([`tools/LICENSE`](../../tools/LICENSE) has the full text). Its source, at the
  version built: `https://github.com/MTG/essentia/tree/v2.1_beta2`. MTG
  describes the AGPL as its licence for non-commercial use, and offers
  commercial licences separately.
- **The models** are by MTG, under **Creative Commons
  Attribution-NonCommercial-ShareAlike 4.0**
  (`https://creativecommons.org/licenses/by-nc-sa/4.0/`), as the README in their
  own directory, `https://essentia.upf.edu/svm_models/`, states. They are here
  unmodified. **Non-commercial**: using them commercially needs MTG's
  proprietary licence (`https://www.upf.edu/web/mtg/contact`).

Lempi's own terms are in [`LICENSING.md`](../../LICENSING.md). Lempi, the
player, contains none of this and needs none of it to run.
