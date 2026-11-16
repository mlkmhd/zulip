use _zulip_backend::user_status::{StatusUpdateError, UserStatus, prepare_status_update};

#[test]
fn an_empty_emoji_name_clears_the_previous_code_and_type() {
    let update = prepare_status_update(None, None, Some(""), Some("42"), Some("realm_emoji"));
    assert!(matches!(update, Ok((Some(""), Some("unicode_emoji")))));
}

#[test]
fn rejection_depends_on_whether_any_status_fields_were_supplied() {
    assert!(matches!(
        prepare_status_update(None, None, None, Some("1f698"), None),
        Err(StatusUpdateError::NoValues)
    ));
    assert!(matches!(
        prepare_status_update(Some(false), None, None, Some("1f698"), None),
        Err(StatusUpdateError::MissingEmojiName)
    ));
    assert!(matches!(
        prepare_status_update(None, Some(""), None, Some(""), None),
        Ok((Some(""), None))
    ));
}

#[test]
fn a_cleared_emoji_is_omitted_even_if_old_metadata_is_present() {
    let status = UserStatus::from_row(false, "", "", "1f698", "unicode_emoji");

    assert!(status.away);
    assert!(status.text.is_none());
    assert!(status.emoji.is_none());
}
