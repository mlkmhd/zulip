use std::{error::Error, path::Path};

use jiter::JsonValue;
use tokio_postgres::{Client, GenericClient};

use super::{
    alert_word_cache,
    form::{Form, FormError},
    unicode_lowercase::UnicodeLowercase,
};

pub(super) fn parse_removals(
    form: &Form,
    lowercase: &UnicodeLowercase,
) -> Result<Vec<String>, FormError> {
    const FIELD: &str = "alert_words";
    let value = form.value(FIELD).ok_or(FormError::Missing(FIELD))?;
    let JsonValue::Array(items) =
        JsonValue::parse(value.as_bytes(), true).map_err(|_| FormError::InvalidJson(FIELD))?
    else {
        return Err(FormError::ExpectedList(FIELD));
    };
    items
        .iter()
        .enumerate()
        .map(|(index, value)| match value {
            JsonValue::Str(word) => Ok(lowercase.lower(word)),
            _ => Err(FormError::ExpectedString(FIELD, index)),
        })
        .collect()
}

pub(super) async fn list(
    database: &impl GenericClient,
    user_id: i32,
) -> Result<Vec<String>, tokio_postgres::Error> {
    database
        .query(
            "SELECT word FROM zulip.zerver_alertword WHERE user_profile_id = $1",
            &[&user_id],
        )
        .await?
        .iter()
        .map(|row| row.try_get("word"))
        .collect()
}

/// Delete words for a single user and return the list to publish after commit.
/// The caller supplies the authenticated user's realm and Python-lowercased input.
pub async fn remove(
    database: &mut Client,
    cache_manifest: &Path,
    user_id: i32,
    realm_id: i32,
    lowercase_words: &[String],
) -> Result<Vec<String>, Box<dyn Error + Send + Sync>> {
    let transaction = database.transaction().await?;
    if !lowercase_words.is_empty() {
        transaction
            .execute(
                "DELETE FROM zulip.zerver_alertword
                 WHERE user_profile_id = $1 AND lower(word) = ANY($2::text[])",
                &[&user_id, &lowercase_words],
            )
            .await?;
    }
    // Django flushes even for a no-op and aborts the deletion on a cache error.
    alert_word_cache::invalidate(cache_manifest, realm_id).await?;
    let remaining = list(&transaction, user_id).await?;
    transaction.commit().await?;
    Ok(remaining)
}
