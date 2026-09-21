//! Reading and editing a subject's own `rotation`/`recovery`/`restraint`
//! `[SPEC-DIR-115]`, `[REQ-VIS-290]` -- the per-artist/per-recording tuning
//! MuLibPlay let a listener hand-set directly, migrated into
//! `listener_preferences` unchanged but, until this, never reachable from
//! anywhere but a live `Director::load()` and the one-time migration
//! script.
//!
//! **Not gated behind `vipunen-support`.** This is core Program Director
//! data, not a Vipunen-only capability -- the appliance needs it exactly as
//! much as the desktop does, so these routes are registered unconditionally.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::IntoResponse;

use super::Ui;

#[derive(serde::Serialize)]
struct Defaults {
    rotation: f64,
    recovery: f64,
    restraint: f64,
}

#[derive(serde::Serialize)]
struct PreferenceView {
    rotation: Option<f64>,
    recovery: Option<f64>,
    restraint: Option<f64>,
    defaults: Defaults,
    /// Title, artist and album, so the panel's own heading names the
    /// recording the same way wherever it was opened from `[SPEC-PREF-090]`.
    subject: crate::db::SubjectNaming,
    /// Every registered special and this subject's own value for it
    /// `[SPEC-PREF-080]`. Always present, empty where the subject is an
    /// artist (specials are per recording) or no occasion is registered.
    specials: Vec<crate::db::SpecialRow>,
}

fn defaults_for(kind: &str) -> Defaults {
    let t = if kind == "artist" {
        crate::director::frequency::Tuning::artist_defaults()
    } else {
        crate::director::frequency::Tuning::recording_defaults()
    };
    Defaults { rotation: t.rotation, recovery: t.recovery, restraint: t.restraint }
}

/// The current tuning, `None` per field where nothing has ever been set --
/// never a fabricated default, so the panel can say "at the default" rather
/// than show a number nobody actually chose.
pub(super) async fn get_preference(
    State(ui): State<Ui>,
    axum::extract::Path((kind, id)): axum::extract::Path<(String, String)>,
) -> axum::response::Response {
    if kind != "recording" && kind != "artist" {
        return (StatusCode::BAD_REQUEST, "kind must be recording or artist").into_response();
    }
    let db = ui.db.clone();
    let library = ui.library.clone();
    let got = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        let row = store.get_preference(&kind, &id).map_err(|e| e.message().to_string())?;
        // Specials are per recording: `flavor` itself only holds
        // `subject_kind IN ('recording','passage')`, and an occasion is a
        // property of a song rather than of everything a performer ever
        // recorded. An artist gets an empty list, not a missing field, so
        // the panel has one shape to render either way.
        let specials = if kind == "artist" {
            Vec::new()
        } else {
            store.list_specials(&id).map_err(|e| e.message().to_string())?
        };
        // Naming failing must not fail the whole panel -- an unknown mbid,
        // or a library half-populated, should still let a person tune
        // rotation. The heading falls back to whatever the caller passed.
        let subject = store.subject_naming(&kind, &id).unwrap_or_default();
        Ok::<_, String>(PreferenceView {
            rotation: row.rotation,
            recovery: row.recovery,
            restraint: row.restraint,
            defaults: defaults_for(&kind),
            subject,
            specials,
        })
    })
    .await;
    match got {
        Ok(Ok(view)) => axum::Json(view).into_response(),
        Ok(Err(why)) => (StatusCode::BAD_REQUEST, why).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// What a `POST`'s own query string means for one field: left alone,
/// cleared back to the default, or set to a value.
#[derive(Debug, Clone, Copy, PartialEq)]
enum FieldEdit {
    Unchanged,
    Reset,
    Set(f64),
}

/// The three-way meaning of a query parameter, kept as a pure function
/// separate from the HTTP call for the same reason `parse_mb_results`/
/// `parse_release_tracks` are (`musicbrainz.rs`) -- checkable without a
/// request. **Absent** from the query string means "leave it exactly as
/// stored"; **empty** (`?rotation=`) means "reset to the default"; a
/// **number** sets it. This is why the query is read as a plain string map
/// rather than a typed extractor, which would collapse "absent" and
/// "empty" into the same `None`.
fn parse_field(q: &std::collections::HashMap<String, String>, field: &str) -> Result<FieldEdit, String> {
    match q.get(field).map(String::as_str) {
        None => Ok(FieldEdit::Unchanged),
        Some("") => Ok(FieldEdit::Reset),
        Some(v) => v.parse().map(FieldEdit::Set).map_err(|_| format!("{field} must be a number")),
    }
}

/// The `(characteristic, class)` a `special:` query key names, or `None` for
/// any key that is not one `[SPEC-PREF-087]`.
///
/// `special:user.christmas:christmasy`. Two colons, and the characteristic is
/// the middle field rather than the last, because a characteristic legitimately
/// contains a dot (`user.christmas`) while neither field ever contains a colon
/// -- so splitting on colons from the left is unambiguous where splitting a
/// dotted name would not be. Anything else in the query string (`rotation`,
/// or a key some future caller adds) falls through untouched rather than
/// being rejected, the same tolerance `parse_field` already shows for keys it
/// does not recognise.
fn parse_special_key(key: &str) -> Option<(&str, &str)> {
    let rest = key.strip_prefix("special:")?;
    let (characteristic, class) = rest.split_once(':')?;
    if characteristic.is_empty() || class.is_empty() || class.contains(':') {
        return None;
    }
    Some((characteristic, class))
}

/// Write a subject's tuning `[REQ-VIS-290]`.
///
/// Reloads the Director the same way `POST /library/reload` does
/// (`control.rs`'s `reload_library`) -- one request both saves the value
/// and asks the running engine to pick it up on its next refill, rather
/// than requiring the client to make two calls.
pub(super) async fn set_preference(
    State(ui): State<Ui>,
    axum::extract::Path((kind, id)): axum::extract::Path<(String, String)>,
    axum::extract::Query(q): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> axum::response::Response {
    if kind != "recording" && kind != "artist" {
        return (StatusCode::BAD_REQUEST, "kind must be recording or artist").into_response();
    }

    let mut edits = Vec::with_capacity(3);
    for field in ["rotation", "recovery", "restraint"] {
        match parse_field(&q, field) {
            Ok(edit) => edits.push((field, edit)),
            Err(why) => return (StatusCode::BAD_REQUEST, why).into_response(),
        }
    }
    let value_of = |field: &str| {
        edits.iter().find(|(f, _)| *f == field).and_then(|(_, e)| match e {
            FieldEdit::Set(v) => Some(*v),
            _ => None,
        })
    };
    let sets = (value_of("rotation"), value_of("recovery"), value_of("restraint"));
    let resets: Vec<&'static str> = edits
        .iter()
        .filter(|(_, e)| *e == FieldEdit::Reset)
        .map(|(f, _)| *f)
        .collect();

    // Specials ride the same three-way query as the three tuning fields
    // `[SPEC-PREF-087]`: absent means untouched, empty means "drop my own
    // value and use whatever `flavor` inherited", a number sets it. Parsed
    // before any write so a malformed key is a 400 with nothing stored,
    // rather than half a save.
    let mut special_edits: Vec<(String, String, FieldEdit)> = Vec::new();
    for key in q.keys() {
        let Some((characteristic, class)) = parse_special_key(key) else { continue };
        if kind == "artist" {
            return (StatusCode::BAD_REQUEST, "specials are per recording").into_response();
        }
        match parse_field(&q, key) {
            Ok(edit) => special_edits.push((characteristic.to_string(), class.to_string(), edit)),
            Err(why) => return (StatusCode::BAD_REQUEST, why).into_response(),
        }
    }

    let db = ui.db.clone();
    let library = ui.library.clone();
    let (kind2, id2) = (kind.clone(), id.clone());
    let done = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        if sets != (None, None, None) {
            store
                .set_preference(&kind2, &id2, sets.0, sets.1, sets.2)
                .map_err(|e| e.message().to_string())?;
        }
        for field in &resets {
            store.reset_preference(&kind2, &id2, field).map_err(|e| e.message().to_string())?;
        }
        for (characteristic, class, edit) in &special_edits {
            match edit {
                FieldEdit::Unchanged => {}
                FieldEdit::Reset => store
                    .reset_special(&id2, characteristic, class)
                    .map_err(|e| e.message().to_string())?,
                FieldEdit::Set(v) => store
                    .set_special(&id2, characteristic, class, *v)
                    .map_err(|e| e.message().to_string())?,
            }
        }
        Ok::<(), String>(())
    })
    .await;

    match done {
        Ok(Ok(())) => {
            if let Ok(mut c) = ui.controls.lock() {
                c.reload_requested = true;
                c.reload_status = Some("requested".into());
            }
            StatusCode::NO_CONTENT.into_response()
        }
        Ok(Err(why)) => (StatusCode::BAD_REQUEST, why).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

/// How often a subject has played, by window and by who selected it
/// `[REQ-VIS-300]` -- the play-frequency panel underneath the preference
/// panel. Never delays that panel: this is its own independent route,
/// fetched only after the preference panel is already open.
pub(super) async fn play_frequency(
    State(ui): State<Ui>,
    axum::extract::Path((kind, id)): axum::extract::Path<(String, String)>,
) -> axum::response::Response {
    if kind != "recording" && kind != "artist" {
        return (StatusCode::BAD_REQUEST, "kind must be recording or artist").into_response();
    }
    let db = ui.db.clone();
    let library = ui.library.clone();
    let got = tokio::task::spawn_blocking(move || {
        let store = crate::db::PlayerStore::open_split(&db, &library).map_err(|e| e.message().to_string())?;
        store.play_frequency(&kind, &id).map_err(|e| e.message().to_string())
    })
    .await;
    match got {
        Ok(Ok(rows)) => axum::Json(rows).into_response(),
        Ok(Err(why)) => (StatusCode::BAD_REQUEST, why).into_response(),
        Err(_) => StatusCode::INTERNAL_SERVER_ERROR.into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn q(pairs: &[(&str, &str)]) -> std::collections::HashMap<String, String> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
    }

    #[test]
    fn absent_field_is_unchanged() {
        assert_eq!(parse_field(&q(&[]), "rotation"), Ok(FieldEdit::Unchanged));
    }

    #[test]
    fn empty_field_is_a_reset() {
        assert_eq!(parse_field(&q(&[("rotation", "")]), "rotation"), Ok(FieldEdit::Reset));
    }

    #[test]
    fn numeric_field_is_a_set() {
        assert_eq!(parse_field(&q(&[("rotation", "1.5")]), "rotation"), Ok(FieldEdit::Set(1.5)));
        // Restraint is legitimately negative (a boost) -- the parser must
        // not special-case the sign.
        assert_eq!(parse_field(&q(&[("restraint", "-0.939")]), "restraint"), Ok(FieldEdit::Set(-0.939)));
    }

    #[test]
    fn a_special_key_splits_at_the_first_colon_not_the_last_dot() {
        assert_eq!(
            parse_special_key("special:user.christmas:christmasy"),
            Some(("user.christmas", "christmasy")),
            "the characteristic's own dot must not be read as the separator"
        );
    }

    #[test]
    fn a_key_that_is_not_a_special_is_ignored_rather_than_rejected() {
        assert_eq!(parse_special_key("rotation"), None);
        assert_eq!(parse_special_key("special:user.christmas"), None, "no class");
        assert_eq!(parse_special_key("special::christmasy"), None, "no characteristic");
        assert_eq!(parse_special_key("special:a:b:c"), None, "a third field is not a shape we know");
    }

    #[test]
    fn a_special_reads_the_same_three_ways_a_tuning_field_does() {
        let key = "special:user.christmas:christmasy";
        assert_eq!(parse_field(&q(&[]), key), Ok(FieldEdit::Unchanged));
        assert_eq!(parse_field(&q(&[(key, "")]), key), Ok(FieldEdit::Reset));
        assert_eq!(parse_field(&q(&[(key, "0.25")]), key), Ok(FieldEdit::Set(0.25)));
    }

    #[test]
    fn non_numeric_field_is_rejected_by_name() {
        let err = parse_field(&q(&[("rotation", "soon")]), "rotation").unwrap_err();
        assert!(err.contains("rotation"), "the error should name the offending field, got {err:?}");
    }
}
