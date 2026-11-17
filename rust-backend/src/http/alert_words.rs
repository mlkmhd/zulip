use std::{error::Error, path::Path};

use jiter::JsonValue;
use tokio_postgres::{Client, GenericClient, Transaction};

use crate::alert_words::{clean_alert_words, select_new_alert_words};

use super::{
    alert_word_cache,
    form::{Form, FormError},
    unicode_lowercase::UnicodeLowercase,
};

pub(super) fn parse_words(
    form: &Form,
    max_length: Option<usize>,
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
        .map(|(index, value)| {
            let JsonValue::Str(word) = value else {
                return Err(FormError::ExpectedString(FIELD, index));
            };
            if let Some(max) = max_length
                && word.chars().count() > max
            {
                return Err(FormError::ItemTooLong(FIELD, index, max));
            }
            Ok(word.to_string())
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

pub(super) async fn add(
    database: &mut Client,
    cache_manifest: &Path,
    user_id: i32,
    realm_id: i32,
    words: &[String],
    lowercase: &UnicodeLowercase,
) -> Result<Vec<String>, Box<dyn Error + Send + Sync>> {
    let transaction = database.transaction().await?;
    let existing = list(&transaction, user_id)
        .await?
        .into_iter()
        .map(|word| lowercase.lower(&word))
        .collect();
    let selected = select_new_alert_words(
        &existing,
        clean_alert_words(words).map(|word| (lowercase.lower(word), word)),
    );
    if !selected.is_empty() {
        transaction
            .execute(
                "INSERT INTO zulip.zerver_alertword (user_profile_id, realm_id, word)
             SELECT $1, $2, input.word FROM unnest($3::text[]) AS input(word)",
                &[&user_id, &realm_id, &selected],
            )
            .await?;
    }
    flush_and_commit(transaction, cache_manifest, user_id, realm_id).await
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
    flush_and_commit(transaction, cache_manifest, user_id, realm_id).await
}

async fn flush_and_commit(
    transaction: Transaction<'_>,
    cache_manifest: &Path,
    user_id: i32,
    realm_id: i32,
) -> Result<Vec<String>, Box<dyn Error + Send + Sync>> {
    // Django flushes even for a no-op and aborts a write on a cache error.
    alert_word_cache::invalidate(cache_manifest, realm_id).await?;
    let remaining = list(&transaction, user_id).await?;
    transaction.commit().await?;
    Ok(remaining)
}
