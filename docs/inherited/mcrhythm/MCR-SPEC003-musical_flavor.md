> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC003-musical_flavor.md` on 2026-08-09. **Not a Lempi specification.**
>
> Definition of Musical Flavor: 18 classifiers, 71 dimensions, user-defined characteristics. SPEC005 and GUIDE003 depend on this normatively.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# Musical Flavor

**ðŸŽ¼ TIER 2 - DESIGN SPECIFICATION**

[MFL-DEF-010] Defines musical flavor system and distance calculations. Derived from [requirements.md](MCR-REQ001-requirements.md). See Document Hierarchy.

> **Related Documentation:** Architecture, [Musical Taste](MCR-SPEC004-musical_taste.md)

---

## Quantitative Definition

> **Entity Definitions:** See [REQ002-entity_definitions.md](MCR-REQ002-entity_definitions.md) for complete definitions of Passage, Song, and Recording entities referenced in this specification. See [§ 2.0 Entity Relationship Overview](MCR-REQ002-entity_definitions.md#20-entity-relationship-overview) for entity relationships.

[MFL-DEF-020] Musical flavor is a quantitative definition of a passage's musical characteristics in many dimensions. It uses the
[AcousticBrainz high level](https://acousticbrainz.org/data#highlevel-data) vector format for [recording(s)](https://musicbrainz.org/doc/Recording)
contained in a [passage](MCR-REQ002-entity_definitions.md#entities).  See: Sample AcousticBrainz highlevel json object.

[MFL-DEF-021] Flavor vectors are computed via local Essentia analysis (native binary or Docker container). AcousticBrainz was shut down in 2024; Essentia produces compatible output in the same vector format. For album files containing multiple passages, each passage is analyzed individually to produce a distinct flavor vector.

[MFL-DEF-030] These characteristic values break down into two categories:
- [MFL-DEF-031] binary characteristics with two dimensions whose values add up to 1.0 such as: 
  - highlevel.danceability.all.danceable, not_danceable 
  - highlevel.gender.all.female, male
  - highlevel.mood_relaxed.not_relaxed, relaxed
- [MFL-DEF-032] complex characteristics with 3 or more dimensions whose values add up to 1.0 such as:
  - highlevel.genre_electronic.all.ambient, dnb, house, techno, trance
  - highlevel.ismir04_rhythm.all.ChaChaCha, Jive, Quickstep, Rumba-American, Rumba-International, Rumba-Misc, Samba, Tango, VienneseWaltz, Waltz
  
[MFL-DEF-040] When characteristics do not add up to a value in the range: (0.9999,1.0001) this is flagged as a warning in the developers' stderr channel.  Experience has shown this to be rare.

### Additional characteristics 
[MFL-UDEF-010] Additional characteristics may be configured by the user.  For example: the user may add binary characteristics:

- user.christmas.all.christmasy, not_christmasy
- user.childrens_music.all.forChildren, not_forChildren

or complex characteristics:

- user.seasonal_affinity.all.winter, spring, summer, fall
- user.religious.all.atheist, buddhist, christian, folk, hindu, jewish, muslim, taoist, sikh, spirit

[MFL-UDEF-020] User defined characteristics are computed alongside AcousticBrainz characteristics, identically.  When a user defined characteristic or element of a 
characteristic is not available for some Recordings it is handled as described below.  User defined characteristics are constrained: the sum of all 
category values must be 1.0.

## Flavor Distance

> [MFL-DIST-010] **Design Note on Calculation Method:** The method of using only shared (intersecting) characteristics is intentional. For calculating the distance between two specific flavors, this approach avoids making assumptions about missing data (e.g., treating it as zero), which could inaccurately skew the result. It compares items based only on the attributes they are both known to possess. This differs from the 'Taste' calculation, which computes a centroid from a large collection and is designed to build a broad profile from all available data.

[MFL-DIST-020] Musical flavor is primarily used to determine the "flavor distance" between two passages.
[MFL-DIST-021] The shorter the flavor distance, the more musically similar the passages are deemed to be.

[MFL-DIST-030] There are no strict limits on flavor distance, being a distance quantity its minimum value is 0.0.
[MFL-DIST-031] Maximum flavor distance is generally 1.0, but if higher values are encountered they should be handled gracefully.

### Single Recording per Passage Calculation
[MFL-CALC-010] For the case where the two passages to be evaluated each contain a single song, and therefore a single recording per passage:

- [MFL-CALC-011] Step 1: create a list of all binary characteristics which are available for both passages, for example:
  - Passage A:
    - highlevel.danceability.all.danceable: 0.17, not_danceable 0.83 
    - highlevel.gender.all.female: 0.90, male: 0.10
    - highlevel.mood_relaxed.not_relaxed: 0.74, relaxed: 0.26
    - highlevel.xenophobia.all.not_xenophobic: 0.98, xenophobic 0.02 
  - Passage B:
    - highlevel.danceability.all.danceable: 0.93, not_danceable 0.07 
    - highlevel.gender.all.female: 0.33, male: 0.67
    - highlevel.mood_relaxed.not_relaxed: 0.78, relaxed: 0.22
  - list of binary characteristics available in both passages
    - danceability, gender, mood_relaxed
- [MFL-CALC-012] Note: Since binary characteristics sum to 1.0, only the first value is needed (the second is redundant).
- [MFL-CALC-013] Step 2: create vectors of FP64 (aka double) values of the first value in each characteristic in the list, for example:
  - Passage A: 0.17, 0.90, 0.74
  - Passage B: 0.93, 0.33, 0.78
- [MFL-CALC-014] Step 3: calculate the squared error between the two vectors:
  (0.17 - 0.93) * (0.17 - 0.93) + (0.90 - 0.33) * (0.90 - 0.33) + (0.74 - 0.78) * (0.74 - 0.78) = 0.9041
- [MFL-CALC-015] Step 4: normalize by dividing by the number of characteristics in a vector: this is the binary characteristic distance
  0.9041 / 3 = 0.30136667
- [MFL-CALC-016] Note: complex characteristic names must match exactly, variations in AcousticBrainz schema are handled by ignoring keys which are not exact matches.
- [MFL-CALC-017] Step 5: repeat steps 1 through 4 for each complex characteristic that is available for both passages, comparing all mutually available characteristics instead of just the first, for example:
  - Passage A: 
    - highlevel.genre_electronic.all.ambient: 0.11, dnb: 0.03, house: 0.47, techno: 0.02, trance: 0.37
    - highlevel.ismir04_rhythm.all.ChaChaCha: 0.02, Jive: 0.04, Quickstep: 0.08, Rumba-American: 0.16, Rumba-International: 0.32, Rumba-Misc: 0.09, Samba: 0.07, Tango: 0.05, VienneseWaltz: 0.03, Waltz: 0.14
  - Passage B: 
    - highlevel.animal_affinity.all.bird: 0.94, cat: 0.01, dog: 0.05
    - highlevel.genre_electronic.all.ambient: 0.11, dnb: 0.03, house: 0.01, techno: 0.82, trance: 0.03
    - highlevel.ismir04_rhythm.all.ChaChaCha: 0.02, Jive: 0.13, Quickstep: 0.36, Rumba: 0.23, Samba: 0.07, Tango: 0.05, Waltz: 0.14
- [MFL-CALC-018] animal_affinity isn't available in both passages, so only genre_electronic and ismir04_rhythm characteristic distances will be calculated
  - genre_electronic shared characteristic list: ambient, dnb, house, techno, trance.  Create vectors:
    - Passage A: 0.11, 0.03, 0.47, 0.02, 0.37
    - Passage B: 0.11, 0.03, 0.01, 0.82, 0.03
  - calculation: (0.11-0.11)*(0.11-0.11)+(0.03-0.03)*(0.03-0.03)+(0.47-0.01)*(0.47-0.01)+(0.02-0.82)*(0.02-0.82)+(0.37-0.03)*(0.37-0.03) = 0.9672
  - normalize: 0.9672 / 5 = 0.19344000 is the genre_electronic characteristic distance
  - ismir04_rhythm shared characteristic list: ChaChaCha, Jive, Quickstep, Samba, Tango, Waltz.  Note that missing characteristics in either list are ignored.  Create vectors:
    - Passage A: 0.02, 0.04, 0.08, 0.07, 0.05, 0.14
    - Passage B: 0.02, 0.13, 0.36, 0.07, 0.05, 0.14
  - calculation: (0.02-0.02)*(0.02-0.02)+(0.04-0.13)*(0.04-0.13)+(0.08-0.36)*(0.08-0.36)+(0.07-0.07)*(0.07-0.07)+(0.05-0.05)*(0.05-0.05)+(0.14-0.14)*(0.14-0.14) = 0.0865
  - normalize: 0.0865 / 6 = 0.01441667 is the ismir04_rhythm characteristic distance
- [MFL-CALC-019] Step 6: average all complex characteristic distances:
  - (0.19344 + 0.0144167) / 2 = 0.103928333 is the complex characteristic distance
- [MFL-CALC-020] Step 7: The average of the binary and complex characteristic distances: (0.30136667 + 0.10392833) / 2 = 0.2026475 is the flavor distance between passage A and passage B.

### Edge Cases
[MFL-EDGE-010] #### No common characteristics
[MFL-EDGE-011] In the case where two passages have no binary characteristics in common to compare, the binary characteristic distance is set to 1.0.

[MFL-EDGE-012] In the case where two passages share a complex characteristic, but have no elements in common, that complex characteristic is omitted from the calculation.

[MFL-EDGE-013] In the case where two passages have no complex characteristics in common, or all complex characteristics have been omitted, the complex characteristic distance is set to 1.0.

[MFL-EDGE-020] #### Recordings with no characteristics
[MFL-EDGE-021] In the case where a recording has no characteristics defined, it is handled the same as if it has no characteristics in common with other recordings / passages, the
reported flavor distance will be 1.0 when compared with any other recording / passage.

[MFL-EDGE-030] #### Passages with zero recordings
[MFL-EDGE-031] When one or both passages contain zero recordings the flavor distance between the passages is reported as 1.0.

[MFL-EDGE-032] Passages with zero songs (zero recordings) cannot be used by the automatic selection algorithm:
- Such passages have no flavor vector to compare against the target taste
- They cannot be ranked by flavor distance
- They are excluded from automatic selection entirely
- Users may still manually enqueue zero-song passages at any time

[MFL-EDGE-033] If a library contains only passages with zero songs:
- Automatic selection cannot operate (no valid candidates)
- The [Program Director](MCR-SPEC005-program_director.md) returns error code `NO_SONGS_WITH_FLAVOR`
- Users must manually enqueue passages to populate the queue
- The queue may become empty if all passages finish playing and no new passages are manually enqueued

> **See:** [Program Director - Selection Failure Communication](MCR-SPEC005-program_director.md#selection-failure-communication) for error codes and API response format
> **See:** UI Specification - Automatic Selection Unavailable for user-facing error messages

### More than one Recording per Passage Calculation
<a name="more-than-one-recording-per-passage-calculation"></a>

[MFL-MULT-010] When a passage contains more than one recording, its net flavor is calculated as a weighted centroid of the flavors of its constituent recordings. This process is identical to the "Weighted Taste" calculation described in `musical_taste.md`, ensuring that flavor combination is handled consistently throughout the system.

[MFL-MULT-020] The weight of each recording is its runtime within the passage.

[MFL-MULT-030] **Example:** A passage with a total runtime of 1320 seconds contains 3 recordings:
- Silence: 5 seconds
- Recording A: runtime 287 seconds
- Silence: 2 seconds
- Recording B: runtime 372 seconds
- Unrecognized audio: 350 seconds
- Recording C: runtime 304 seconds

[MFL-MULT-031] The total runtime of characterized audio is 287 + 372 + 304 = 963 seconds. The weights for the weighted centroid are:
- Recording A: 287 / 963 = 0.298
- Recording B: 372 / 963 = 0.386
- Recording C: 304 / 963 = 0.316

[MFL-MULT-040] The passage's net flavor is then computed by taking the union of all characteristics present in any of the recordings. For each characteristic, the values are combined using a weighted average. For example, if only Recordings A and C have a "danceability" score, the passage's "danceability" would be calculated as:
`((danceability_A * 0.298) + (danceability_C * 0.316)) / (0.298 + 0.316)`

[MFL-MULT-050] This ensures that all available data is used. After calculation, complex characteristics are re-normalized to ensure their components sum to 1.0.

[MFL-MULT-060] Once a passage's net flavor has been determined, it is treated as a single flavor vector for all subsequent calculations.

[MFL-MULT-070] **Implementation Note:** A passage's net flavor is computed when it is initially defined and is then stored in the database. If a passage's structure or any of its constituent recordings' flavors change, the net flavor must be re-computed.

## Usage of Musical Flavor

[MFL-USE-010] **Taste as Selection Target:** The target for the passage selection algorithm is a flavor vector representing the user's current musical **Taste**. This target Taste is calculated as described in the [musical_taste.md](MCR-SPEC004-musical_taste.md) document.

[MFL-TARG-010] A Taste can be generated in multiple ways:
-   [MFL-TARG-011] **From Reference Passages:** A user can select one or more "seed" passages to define an immediate listening target. The target Taste is the centroid of these passages' flavor vectors.
-   [MFL-TARG-012] **From Likes/Dislikes:** A more complex, long-term Taste can be computed from the user's history of Liked and Disliked Songs, potentially weighted by time of day, week, or season. See [Likes and Dislikes](MCR-SPEC006-like_dislike.md).

[MFL-ALGO-010] **Selection Algorithm:**

1.  [MFL-ALGO-011] **Filter:** Exclude zero-probability passages (due to cooldown criteria, empty passages, etc.).
2.  [MFL-ALGO-012] **Distance Calculation:** Compute the flavor distance from the target **Taste** to every non-zero probability passage.
3.  [MFL-ALGO-013] **Ranking:** Sort the passages by distance (closest first).
4.  [MFL-ALGO-014] **Candidate Pool:** Select the top 100 closest passages (or all available if fewer than 100).
5.  [MFL-ALGO-015] **Probability Weighting:** Each candidate's effective probability = base probability Ã— cooldown multipliers.
6.  [MFL-ALGO-016] **Weighted Random Selection:**
    -   Generate a random number R within the range [0, Î£(all candidate probabilities)).
    -   Iterate through the candidates, subtracting each one's probability from R.
    -   Select the first candidate that causes R to be ≤ 0.

> [MFL-ALGO-020] **Note:** The candidate pool size (default: 100) is a design parameter that balances performance vs. diversity.

> [MFL-USE-020] Implements requirement: [Automatic Passage Selection](MCR-REQ001-requirements.md#automatic-passage-selection)
   
----
End of document - Musical Flavor
