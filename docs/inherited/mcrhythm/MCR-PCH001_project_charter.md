> ⚠️ **INHERITED DOCUMENT — ACTIVE DESIGN INPUT**
>
> Copied from `McRhythm/docs/PCH001_project_charter.md` on 2026-08-09. **Not a Lempi specification.**
>
> Project charter: quality-absolute goals.
>
> Prose is unaltered. Cross-references were rewired to imported siblings where those exist, and de-linked to plain text where the target was not imported `[INH-HAZ-050]`.
>
> Identifier tags and document numbers below belong to McRhythm/WKMP's scheme, not Lempi's — see ../README.md.

---

# Project Charter - wkmp Wonderfully Kinetic Music Player

**Principal Developer:** Mango Cat
**Started:** 2025-10-25

**EDITING RESTRICTIONS:**
- This file SHALL NEVER be automatically edited
- Manual editing by users is permitted at any time

----

## Purpose

Define the scope and purpose of the wkmp project.

## Scope

- reading (decompressing) of audio files
- mixing of audio from files for overlay playback
- fade curve manipulation of audio for smooth transitions
- realtime high quality playback of audio on local system audio output devices
- database of available audio files identifying
  - identity and location of MusicBrainz recording(s) within the files
  - MusicBrainz release, artist and other information about the recordings
  - AcousticBrainz high level characteristics of the recordings
  - local play history
- Works with users' media files in-place without modifications
  - All necessary meta-data is stored in the database
  - Original meta-data in media files remains unchanged and duplicated to the database when needed.
  - Only the database is referenced for meta-data needs after initial import
  - Corrections of meta-data errors are made in the database only
- automatic selection of recording(s) to play based on user preferences
- http user interface suitable for control via smartphone on the local network

## Goals

- Flawless audio playback
- Minimal need for user to interact (automatic DJ)
- Easy selection and control of recordings to play when user wants to
- Attractive display of currently playing recording information
- Configurability for simple to complex programming of automatic music selection
- Listener experience reminiscent of 1970s FM radio on the US East and West coasts

----

## Approval

**Decision made by:** Mango Cat (Principal Developer)
**Review date:** 2025-10-25
**Next review:** As needed, none scheduled

