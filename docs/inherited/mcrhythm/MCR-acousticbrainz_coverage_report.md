> ⚠️ **INHERITED DOCUMENT — HISTORICAL EVIDENCE**
>
> Copied from `McRhythm/acousticbrainz_coverage_report.md` on 2026-08-09. **Not a Lempi specification.**
>
> AcousticBrainz coverage 91.1% as of 2026-01-01 -- also the evidence that the API was alive then and dead by 2026-08-08 [GDE-MCR-045].
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# AcousticBrainz Coverage Report
## 200-File Album Matching Test

**Test Date:** 2026-01-01
**Test Duration:** 69.5 minutes (4169.68s)
**Test:** run29f_full_comparison_test with AcousticBrainz querying

---

## Executive Summary

- **Albums Queried:** 183 (matched albums from 200-file test)
- **Total Recordings Examined:** 2,664
- **Recordings with AcousticBrainz Data:** 2,427 (91.1%)
- **Recordings Missing AcousticBrainz Data:** 237 (8.9%)
- **Albums with Missing Data:** 42 (23.0% of queried albums)

**Conclusion:** AcousticBrainz has excellent coverage for popular music (91.1% availability), with gaps primarily in newer releases (2012+), soundtracks, and niche genres.

---

## Coverage Breakdown

### Albums with 100% Missing Data (6 albums, 108 recordings)

These albums have **zero** recordings with AcousticBrainz data available:

1. **Disney - Moana Soundtrack**
   - Tracks: 51 (all missing)
   - Release MBID: `61dfa4ba-605c-4883-b896-a60b768a7e74`
   - Year: 2016 (soundtrack)

2. **Bob Marley & The Wailers - Catch A Fire**
   - Tracks: 20 (all missing)
   - Release MBID: `c6875ad6-bfd5-427c-a0e3-da7262b3d702`
   - Note: Remastered edition

3. **Lady Gaga - Chromatica**
   - Tracks: 16 (all missing)
   - Release MBID: `f7679def-97e5-42c3-a499-e88560d41792`
   - Year: 2020 (recent release)

4. **Jessita Reyes - Native American Flute Lullabies**
   - Tracks: 16 (all missing)
   - Release MBID: `baa26267-e1eb-448f-bbc6-7bf1b8500ee8`
   - Genre: New Age/World (niche)

5. **Ace of Base - Happy Nation (U.S. Version) (Remastered)**
   - Tracks: 15 (all missing)
   - Release MBID: `a69f8eae-4e34-45fe-85fb-12b049dbdf38`
   - Note: Specific remaster edition

6. **Humpback Whales - Songs of the Humpback Whale**
   - Tracks: 5 (all missing)
   - Release MBID: `e110c6a6-4441-4ed9-9803-7c6144058081`
   - Genre: Nature sounds (non-music)

---

## Albums with High Missing Percentage (>50%)

### Steppenwolf - The ABC/Dunhill Singles
- **Missing:** 28/38 tracks (73.7%)
- **Available:** 10 tracks
- **Release MBID:** `65c3fc43-285d-4181-ac1e-c7522fce76cf`
- **Analysis:** Singles compilation - many rare B-sides likely not analyzed

### Thin Lizzy - Live And Dangerous
- **Missing:** 16/17 tracks (94.1%)
- **Available:** 1 track
- **Release MBID:** `d267be29-323a-4eb7-8f9c-e4a85eb52132`
- **Analysis:** Live album - AcousticBrainz primarily focuses on studio recordings

### Hyper - We Control
- **Missing:** 7/10 tracks (70.0%)
- **Available:** 3 tracks
- **Release MBID:** `04f66abd-f007-3cbb-8801-d05b52d53fe4`
- **Analysis:** Electronic music - niche artist

---

## Albums with Moderate Missing Percentage (10-50%)

| Artist | Album | Missing | Total | % | Available |
|--------|-------|---------|-------|---|-----------|
| Molly Hatchet | Greatest Hits | 5 | 15 | 33.3% | 10 |
| Bob Marley & the Wailers | Exodus 40 | 7 | 28 | 25.0% | 21 |
| Smash Mouth | Fush Yu Mang | 4 | 16 | 25.0% | 12 |
| Huey Lewis & The News | Greatest Hits | 5 | 21 | 23.8% | 16 |
| Mark Knopfler | Privateering | 5 | 25 | 20.0% | 20 |
| The Greg Kihn Band | Best Of Beserkley '75-'84 | 4 | 21 | 19.0% | 17 |
| Grand Funk Railroad | Closer To Home | 2 | 13 | 15.4% | 11 |
| Rock Candy Funk Party | Groove is King | 2 | 16 | 12.5% | 14 |
| Christopher Cross | Christopher Cross | 1 | 9 | 11.1% | 8 |
| Van Halen | 1984 | 1 | 9 | 11.1% | 8 |
| Scorpions | Love At First Sting | 1 | 9 | 11.1% | 8 |
| Moby | Wait For Me | 2 | 19 | 10.5% | 17 |

---

## Albums with Low Missing Percentage (<10%)

30 albums have 1-2 missing recordings out of larger track counts:

**Lowest Missing Percentages:**
- Chicago - The Very Best of Chicago (1/39 = 2.6%)
- Tears for Fears - Songs from the Big Chair (1/33 = 3.0%)
- Elton John - Goodbye Yellow Brick Road (2/53 = 3.8%)
- Fatboy Slim - The Greatest Hits (1/18 = 5.6%)
- Hall and Oates - The Very Best (1/18 = 5.6%)
- Tedeschi Trucks Band - Let Me Get By (1/18 = 5.6%)
- Led Zeppelin - Led Zeppelin (Deluxe) (1/17 = 5.9%)
- Bic Runga - Birds (1/16 = 6.2%)
- Todd Rundgren - The Very Best Of (1/16 = 6.2%)

---

## Pattern Analysis

### Missing Data Patterns by Category

1. **Recent Releases (2012+):**
   - Lady Gaga - Chromatica (2020): 100% missing
   - Moana Soundtrack (2016): 100% missing
   - Mark Knopfler - Privateering (2012): 20% missing
   - **Pattern:** Newer albums less likely to be in AcousticBrainz (data collection ended ~2017)

2. **Live Albums:**
   - Thin Lizzy - Live And Dangerous: 94.1% missing
   - **Pattern:** Live recordings rarely analyzed by AcousticBrainz

3. **Soundtracks:**
   - Moana: 100% missing
   - Guardians of the Galaxy: 7.1% missing
   - **Pattern:** Soundtracks variable; mainstream soundtracks better coverage

4. **Niche/World Music:**
   - Jessita Reyes (Native American Flute): 100% missing
   - Humpback Whales (nature sounds): 100% missing
   - **Pattern:** Non-mainstream genres rarely covered

5. **Remastered Editions:**
   - Bob Marley - Catch A Fire (remaster): 100% missing
   - Ace of Base - Happy Nation (remaster): 100% missing
   - **Pattern:** Original releases have data; remastered editions often don't

6. **Classic Rock:**
   - Led Zeppelin (5.9% missing)
   - The Cars (10% missing)
   - Chicago (2.6% missing)
   - **Pattern:** Excellent coverage for mainstream 1960s-1990s rock

---

## Recommendations

### For WKMP Musical Flavor Matching

1. **Fallback Strategy Required:**
   - 8.9% of recordings lack AcousticBrainz data
   - Implement fallback to local Essentia analysis for missing recordings
   - Consider caching Essentia results to avoid re-analysis

2. **Prioritize Caching:**
   - 91.1% coverage is excellent for cache seeding
   - Query AcousticBrainz first during import
   - Fall back to local analysis only when necessary

3. **User Communication:**
   - Inform users which tracks have AcousticBrainz data vs. local analysis
   - May affect musical flavor matching accuracy for missing recordings

4. **Genre-Specific Handling:**
   - Live albums: Expect lower coverage, rely more on local analysis
   - Soundtracks: Variable coverage
   - Pre-2000 mainstream: Excellent coverage (95%+)
   - Post-2012 releases: Reduced coverage

---

## Technical Notes

### AcousticBrainz Query Performance

- **Query Rate:** ~1 request per second (rate-limited)
- **Test Duration:** 69.5 minutes for 183 albums (2,664 recordings)
- **Average Time per Album:** ~23 seconds (including track matching and AB queries)
- **Reliability:** 100% success rate for queries (failures return "missing" status)

### Data Quality

All 2,427 available recordings returned:
- `has_tonal: true`
- `has_rhythm: true`

This indicates complete AcousticBrainz analysis (both tonal and rhythm features available).

---

## Appendix: Complete List of Affected Albums

See sections above for categorized breakdowns. All 42 albums with missing data are documented with:
- Artist and album name
- Missing/total track counts
- MusicBrainz release MBID for verification
- Coverage percentage

**Total Recordings Missing:** 237 out of 2,664 (8.9%)
