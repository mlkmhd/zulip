pub struct StatusEmoji<'a> {
    pub name: &'a str,
    pub code: &'a str,
    pub reaction_type: &'a str,
}

pub struct UserStatus<'a> {
    pub away: bool,
    pub text: Option<&'a str>,
    pub emoji: Option<StatusEmoji<'a>>,
}

impl<'a> UserStatus<'a> {
    pub fn from_row(
        presence_enabled: bool,
        text: &'a str,
        emoji_name: &'a str,
        emoji_code: &'a str,
        reaction_type: &'a str,
    ) -> Self {
        Self {
            away: !presence_enabled,
            text: (!text.is_empty()).then_some(text),
            emoji: (!emoji_name.is_empty()).then_some(StatusEmoji {
                name: emoji_name,
                code: emoji_code,
                reaction_type,
            }),
        }
    }
}

pub enum StatusUpdateError {
    NoValues,
    MissingEmojiName,
}

pub fn prepare_status_update<'a>(
    away: Option<bool>,
    status_text: Option<&str>,
    emoji_name: Option<&str>,
    emoji_code: Option<&'a str>,
    emoji_type: Option<&'a str>,
) -> Result<(Option<&'a str>, Option<&'a str>), StatusUpdateError> {
    if away.is_none() && status_text.is_none() && emoji_name.is_none() {
        return Err(StatusUpdateError::NoValues);
    }

    match emoji_name {
        Some("") => Ok((Some(""), Some("unicode_emoji"))),
        None if emoji_code.is_some_and(|code| !code.is_empty())
            || emoji_type.is_some_and(|kind| !kind.is_empty()) =>
        {
            Err(StatusUpdateError::MissingEmojiName)
        }
        _ => Ok((emoji_code, emoji_type)),
    }
}
