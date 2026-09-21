> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC004-musical_taste.md` on 2026-08-09. **Not a Lempi specification.**
>
> Taste model: union centroid over Liked/Disliked flavors.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# Musical Taste

**🎼 TIER 2 - DESIGN SPECIFICATION**

[MTA-DESC-010] Defines musical taste definition and usage. Derived from [requirements.md](MCR-REQ001-requirements.md). See Document Hierarchy.

> **Related Documentation:** Architecture, [Musical Flavor](MCR-SPEC003-musical_flavor.md)

---

## Description

[MTA-DESC-020] Musical Taste is a quantifiable measure of what [Musical Flavor](MCR-SPEC003-musical_flavor.md) is preferred. The [Program Director](MCR-SPEC005-program_director.md) uses Musical Taste (or more simply: Taste) as one input to decide which passages to enqueue for play.

[MTA-DESC-030] Taste is determined by Likes and Dislikes.  At least one Like or Dislike must be registered with at least one Song in order for Taste
to be computed.  The time at which a Like or Dislike is registered also has impact on Taste, because Taste can vary over time: time of day,
day of the week, time of the year.

[MTA-DESC-040] Taste is used to sort all available Passages into lists, each list contains all available Passages.

[MTA-DESC-050] In terms of data structure, Taste and Flavor both have the same core structure of binary and complex characteristics. See [SPEC003-musical_flavor.md](MCR-SPEC003-musical_flavor.md) lines 17-26 for characteristic definitions (binary vs. complex).

### Likes and Dislikes

[MTA-LIKE-010] See [Likes and Dislikes](MCR-SPEC006-like_dislike.md) for a complete specification of this functionality.


## Simple Taste

> [MTA-SMPL-010] **Design Note on Calculation Method:** The method of using all available (union) characteristics to compute a centroid is intentional. For aggregating a user's preferences from a large collection of items, this approach creates the most complete "average" profile of their Taste. It differs from the 'flavor distance' calculation, which compares two specific items and therefore only uses their shared attributes to avoid making assumptions about missing data.

[MTA-SMPL-020] A simple Taste is computed from one or more Flavors as the centroid of all the Flavors.  Dimensions (characteristics) that are represented 
in all Flavors are computed as an average of those values; dimensions that are not represented in all the Flavors are computed as an average 
of those values which are available, for example:

Flavor 1:
 - binary characteristics: A=0.21, B=0.52, C=0.83
 - complex characteristics:
   - D: D1=0.51, D2=0.35, D3=0.14
   - E: E1=0.72, E2=0.03, E3=0.25
   
Flavor 2:
 - binary characteristics: A=0.31, B=0.27, C=0.09
 - complex characteristics:
   - D: D1=0.42, D2=0.22, D3=0.36
   - E: E1=0.24, E2=0.17, E3=0.59

Flavor 3:
 - binary characteristics: A=0.26, C=0.88 (B is not available for Flavor 3)
 - complex characteristics:
   - D: D1=0.33, D2=0.33, D3=0.34
   - E: E1=0.22, E2=0.56, E3=0.22

Flavor 4:
 - binary characteristics: A=0.31, B=0.27, C=0.09
 - complex characteristics: (D is not available for Flavor 4)
   - E: E1=0.22, E2=0.22, E3=0.56

Flavor 5:
 - binary characteristics: A=0.11, C=0.99 (B is not available for Flavor 5)
 - complex characteristics:
   - D: D1=0.51, D2=0.35, D3=0.14
   - E: E1=0.75, E3=0.25 (E2 is not available for Flavor 5)
   
[MTA-SMPL-030] The Taste of these flavors combined would be:
 - binary characteristics: A=(0.21+0.31+0.26+0.31+0.11)/5, B=(0.52+0.27+0.27)/3, C=(0.83+0.09+0.88+0.09+0.99)/5
 - complex characteristics:
   - D: D1=(0.51+0.42+0.33+0.51)/4, D2=(0.35+0.22+0.33+0.35)/4, D3=(0.14+0.36+0.34+0.14)/4
   - E: E1=(0.72+0.24+0.22+0.22+0.75)/5, E2=(0.03+0.17+0.56+0.22)/4, E3=(0.25+0.59+0.22+0.56+0.25)/5
with a further step to re-normalize **only complex characteristics** due to the possibility of their components not summing to 1.0 in the case of missing elements,
like E: E1=0.43, E2=0.245, E3=0.374 ; pre-normalization E1+E2+E3 = 1.049 ; normalize by dividing all elements by 1.049 to get the final values for the Taste.

*(Note: Binary characteristics do not require this step. For all calculations, only the first value of a binary pair is used. Since this value is always between 0.0 and 1.0, any weighted average will also fall within that range, and the second value can be inferred as `1.0 - avg`.)*

[MTA-SMPL-040] Any characteristics which are missing from all Flavors in the Taste are also missing from the Taste.

## Weighted Taste
<a name="weighted-taste"></a>

[MTA-WGHT-010] A weighted Taste is computed from a list of Flavors each with an associated weight value. As above, each characteristic is summed when present and in the
weighted case it is multiplied by its associated weight value and the net result is divided by the sum of all weight values of included characteristics.

## Time
[MTA-TIME-010] Relative cyclic time is used to weight Flavors in a Taste.

### Time of Day
[MTA-TOD-010] There are 24 hours, or 86400.0 seconds in a day.  Any given time in a day is within 12 hours (43200.0 seconds) of any other time.

[MTA-TOD-020] When a Taste is being computed from Likes (or Dislikes) including time of day as a factor, the following parameters are used:
- The list of Likes, including:
  - Flavor of the Liked Song (which is the Flavor of its constituent Recording)
  - The time the Like was applied
- a window time (1.0 to 43200.0 seconds)
- easing in and out curves (similar to Crossfade fade-in and fade-out curves)
  - Exponential
  - Logarithmic
  - S-Curve (Cosine)
  - Linear (default when undefined)
  - Square (100% throughout the range)
- time of day that the Taste is being computed for

[MTA-TOD-030] For each Like in the list, a time-weight is determined by:

[MTA-TOD-031] Difference between the Like time of day and the time of day that the Taste is being computed for, result is in the range: (-43200.0,43200.0) seconds.
[MTA-TOD-032] If the difference is outside the range (-window time, window time) then the weight for this Like's Flavor is 0, otherwise:
the weight is computed on the absolute value of the difference time according to the selected easing curve, with a weight of 1.0 at a difference time
of 0.0 and a weight of 0.0 at a |difference time| of window time.

[MTA-TOD-040] The resulting Taste is a Weighted Taste of all Flavors in the list, weights computed as described above.

### Day of Week
[MTA-DOW-010] There are 7 days, or 604800.0 seconds in a week.

[MTA-DOW-020] Taste computed on day of week works the same as time of day, but with a window time of 1.0 to 302400.0 seconds, and time difference computed weekly
in the range (-302400.0,302400.0)

### Day of Year
[MTA-DOY-010] There are 365.24 days, or 31556736.0 seconds in an average year.

[MTA-DOY-020] Taste computed on day of year (aka seasonal) works the same as day of week, but with a window time of 1.0 to 15778368.0 seconds,
and time difference computed annually in the range (-15778368.0,15778368.0)

### Phase of the Moon
[MTA-LUN-010] The lunar cycle, from one full moon to the next is approximately 2551442.9 seconds.

[MTA-LUN-020] Taste computed on phase of the moon works the same as day of week, but with a window time of 1.0 to 1275721.4 seconds,
and time difference computed annually in the range (-1275721.4,1275721.45)

----
End of document - Musical Taste