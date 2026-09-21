> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/SPEC006-like_dislike.md` on 2026-08-09. **Not a Lempi specification.**
>
> Like/Dislike semantics: click stacking, undo window, passage-to-song weight distribution.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# Likes and Dislikes

**🎼 TIER 2 - DESIGN SPECIFICATION**

[LD-DESC-010] Defines Like and Dislike functionality and their impact on Musical Taste. Derived from [requirements.md](MCR-REQ001-requirements.md). See Document Hierarchy.

> **Note:** This feature is available in the **Full** and **Lite** versions of WKMP only.

> **Authentication Context:** Likes and Dislikes are recorded per user UUID. Anonymous users share a common UUID and therefore a shared taste profile. Registered users have individual UUIDs and separate taste profiles. See User Identity and Authentication for authentication details.

> **Related Documentation:** [Musical Taste](MCR-SPEC004-musical_taste.md), [Musical Flavor](MCR-SPEC003-musical_flavor.md)

---

## Description

[LD-DESC-020] Likes and Dislikes are the primary mechanism by which a user's Musical Taste is determined. While the user action of Liking or Disliking is applied to a **Passage** (the currently playing entity), the effect is ultimately registered against the individual **Songs** within that passage. This document specifies how a single user action on a passage is translated into weighted preferences on its constituent songs.

### User Experience

The user interface for registering Likes and Dislikes provides both simple, immediate controls and a more detailed view for fine-tuning.

[LD-DESC-030] The primary controls are simple "Like" and "Dislike" buttons displayed in the WebUI. A single click on either button applies a default weight (1.0) to the currently playing passage, which is then distributed algorithmically among its constituent songs and recorded against the authenticated user's UUID (see "Applying Likes and Dislikes to Passages").

[LD-DESC-040] To allow for more nuanced control using only these two buttons, their behavior is modified by time and context:

- **Stacking Clicks:** If a user clicks the same button multiple times within a short time window (e.g., 5 minutes) for the same song, instead of creating a new entry, the weight of the existing Like or Dislike is increased. For example, clicking "Like" twice results in a weight of 2.0.

- **Undo/Take-back Clicks:** If the user clicks the opposite button within that same time window, it acts as an "undo". For example, if a user accidentally clicks "Like" (creating a Like with weight 1.0), they can immediately click "Dislike" to reduce the weight back to 0.0, effectively canceling the action. If a Like has a weight of 2.0, a Dislike click would reduce it to 1.0.

[LD-DESC-050] Below these simple buttons, a detailed interface displays a list of recently Liked or Disliked songs and their current weights. This view provides transparency into the results of the simple button clicks and allows for direct manual adjustment. A user can edit the weight of any recent entry by typing in a precise floating-point value, giving them ultimate control over their taste profile.

### Likes and Dislikes

[LD-LIKE-010] Likes and Dislikes are used to compute two distinct Taste vectors: a "Like-Taste" from the centroid of the Flavors of all Liked Songs, and a "Dislike-Taste" from the centroid of the Flavors of all Disliked Songs. The Flavor of a Song is the Flavor of its constituent Recording. These Tastes are then used to generate two corresponding ranked lists of all available Passages.

-   [LD-LIKE-011] **The "Most Liked" List:** This list orders passages by their similarity (i.e., shortest flavor distance) to the "Like-Taste." Passages at the top of this list are considered the most preferred and are strong candidates for selection.

-   [LD-LIKE-012] **The "Most Disliked" List:** This list orders passages by their similarity to the "Dislike-Taste." A passage at the top of this list is very similar to what the user is known to dislike. Therefore, to find preferred passages, this list should be read from the bottom up.

[LD-LIKE-020] **Usage in Selection:**

[LD-LIKE-021] These two lists can be used together to refine passage selection. For example, one possible algorithm is to use the "Most Disliked" list as an exclusion filter. Passages appearing at the top of the "Most Disliked" list can be removed from the "Most Liked" list to create a final candidate pool. This process helps ensure the selection of a well-liked, yet potentially unexpected, passage.

**Note:** The final algorithm for how the "Like-Taste" and "Dislike-Taste" vectors influence the Program Director's passage selection is yet to be defined. This will be specified in a future update to the [Program Director](MCR-SPEC005-program_director.md) specification.

[LD-LIKE-030] When a single Passage with a single associated Song is Liked, the resulting Like-Taste is equal to the Flavor of that Song.

[LD-LIKE-040] When a single Passage with a single associated Song is Disliked, the resulting Dislike-Taste is equal to the Flavor of that Song.

[LD-LIKE-050] When additional Passages are Liked or Disliked, Taste is computed as a weighted centroid of the Flavor of each Liked or Disliked Song.

[LD-LIKE-060] For brevity, descriptions below may only mention Likes, but Dislikes are identically handled just with a distinct output.

### Applying Likes and Dislikes to Passages

[LD-APPL-010] When a user Likes or Dislikes a passage, the action's weight is applied to the individual Songs within that passage. The distribution of this weight depends on the passage's structure.

-   [LD-APPL-011] **Single-Song Passages:** If a passage contains only one Song, that Song receives the full weight (1.0) of the Like or Dislike.

-   [LD-APPL-012] **Multi-Song Passages:** If a passage contains multiple Songs, the Like or Dislike is assumed to apply to everything the user has heard in the passage up to that point. The weight of the action is distributed among all Songs that have played so far, with the currently playing Song receiving a larger share.

    [LD-APPL-020] The algorithm is as follows: If `n` is the number of songs that have finished or are currently playing in the passage at the moment of the action:
    -   [LD-APPL-021] The **currently playing** Song receives a weight of `2 / (n + 1)`.
    -   [LD-APPL-022] Each of the `n-1` **previously played** Songs in the passage receives a weight of `1 / (n + 1)`.

    [LD-APPL-030] **Example:** A passage consists of three songs (S1, S2, S3). A user clicks "Like" during the playback of S3. At this point, `n=3`.
    -   The weight applied to S3 (current) is `2 / (3 + 1) = 0.5`.
    -   The weight applied to S1 (previous) is `1 / (3 + 1) = 0.25`.
    -   The weight applied to S2 (previous) is `1 / (3 + 1) = 0.25`.

    [LD-APPL-040] The total weight distributed is `0.5 + 0.25 + 0.25 = 1.0`. This method also implicitly handles uncharacterized gaps between songs, as the weight is only distributed among the characterized Songs. The resulting list of weighted Songs and their Flavors is then used as the input for the Weighted Taste calculation.

---
End of document - Likes and Dislikes