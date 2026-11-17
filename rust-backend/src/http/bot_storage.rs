use std::{
    collections::{BTreeMap, BTreeSet},
    fmt::Write,
};

use jiter::JsonValue;
use tokio_postgres::{Client, Error, Transaction};

use super::form::{Form, FormError};

pub(super) const PATH: &str = "/api/v1/bot_storage";
pub(super) const SESSION_PATH: &str = "/json/bot_storage";
pub(super) const KEYS: &str = "keys";
pub(super) const STORAGE: &str = "storage";

pub(super) fn keys(form: &Form) -> Result<Option<Vec<String>>, FormError> {
    let Some(value) = form.value(KEYS) else {
        return Ok(None);
    };
    match JsonValue::parse(value.as_bytes(), true).map_err(|_| FormError::InvalidJson(KEYS))? {
        JsonValue::Null => Ok(None),
        JsonValue::Array(items) => items
            .iter()
            .enumerate()
            .map(|(index, item)| match item {
                JsonValue::Str(value) => Ok(value.to_string()),
                _ => Err(FormError::ExpectedString(KEYS, index)),
            })
            .collect::<Result<_, _>>()
            .map(Some),
        _ => Err(FormError::ExpectedList(KEYS)),
    }
}

pub(super) fn storage(form: &Form) -> Result<BTreeMap<String, String>, FormError> {
    let value = form.value(STORAGE).ok_or(FormError::Missing(STORAGE))?;
    let JsonValue::Object(entries) =
        JsonValue::parse(value.as_bytes(), true).map_err(|_| FormError::InvalidJson(STORAGE))?
    else {
        return Err(FormError::ExpectedDict(STORAGE));
    };
    // Pydantic validates every occurrence of a key and retains the last value.
    let mut storage = BTreeMap::new();
    for (key, value) in entries.iter() {
        let JsonValue::Str(value) = value else {
            return Err(FormError::ExpectedDictString(STORAGE, error_key(key)));
        };
        storage.insert(key.to_string(), value.to_string());
    }
    Ok(storage)
}

fn error_key(key: &str) -> String {
    let quoted = serde_json::to_string(key).expect("strings can serialize to JSON");
    let mut escaped = String::new();
    for unit in quoted.encode_utf16() {
        if unit < 0x7f {
            escaped.push(char::from(unit as u8));
        } else {
            write!(escaped, "\\u{unit:04x}").expect("writing to a String succeeds");
        }
    }
    escaped
}

pub(super) async fn read(
    database: &Client,
    user_id: i32,
    keys: Option<Vec<String>>,
) -> Result<Option<BTreeMap<String, String>>, Error> {
    let keys = keys.map(|keys| keys.into_iter().collect::<BTreeSet<_>>());
    let rows = database
        .query(
            "SELECT key, value FROM zulip.zerver_botstoragedata WHERE bot_profile_id = $1",
            &[&user_id],
        )
        .await?;
    let mut storage = BTreeMap::new();
    for row in rows {
        let key: String = row.try_get("key")?;
        if keys.as_ref().is_none_or(|keys| keys.contains(&key)) {
            storage.insert(key, row.try_get("value")?);
        }
    }

    if keys.is_some_and(|keys| keys.len() != storage.len()) {
        return Ok(None);
    }
    Ok(Some(storage))
}

async fn lock_owner(transaction: &Transaction<'_>, user_id: i32) -> Result<(), Error> {
    // Django and Rust must take the same lock before changing a bot's storage.
    transaction
        .query_one(
            "SELECT pg_advisory_xact_lock(hashtext('zulip.bot_storage'), $1)",
            &[&user_id],
        )
        .await?;
    Ok(())
}

pub(super) async fn update(
    database: &mut Client,
    user_id: i32,
    storage: BTreeMap<String, String>,
    limit: u64,
) -> Result<Option<i128>, Error> {
    let input_size: i128 = storage
        .iter()
        .map(|(key, value)| key.chars().count() as i128 + value.chars().count() as i128)
        .sum();
    let (keys, values): (Vec<_>, Vec<_>) = storage.into_iter().unzip();
    let transaction = database.transaction().await?;
    lock_owner(&transaction, user_id).await?;
    let remaining: i64 = transaction
        .query_one(
            "SELECT COALESCE(SUM(length(key) + length(value)), 0)
             FROM zulip.zerver_botstoragedata
             WHERE bot_profile_id = $1 AND NOT (key = ANY($2::text[]))",
            &[&user_id, &keys],
        )
        .await?
        .try_get(0)?;
    let excess = i128::from(remaining) + input_size - i128::from(limit);
    if excess > 0 {
        transaction.rollback().await?;
        return Ok(Some(excess));
    }

    if !keys.is_empty() {
        transaction
            .execute(
                "INSERT INTO zulip.zerver_botstoragedata (bot_profile_id, key, value)
                 SELECT $1, entries.key, entries.value
                 FROM unnest($2::text[], $3::text[]) AS entries(key, value)
                 ON CONFLICT (bot_profile_id, key) DO UPDATE SET value = EXCLUDED.value",
                &[&user_id, &keys, &values],
            )
            .await?;
    }
    transaction.commit().await?;
    Ok(None)
}

pub(super) async fn delete(
    database: &mut Client,
    user_id: i32,
    keys: Option<Vec<String>>,
) -> Result<bool, Error> {
    let transaction = database.transaction().await?;
    lock_owner(&transaction, user_id).await?;
    let Some(keys) = keys else {
        transaction
            .execute(
                "DELETE FROM zulip.zerver_botstoragedata WHERE bot_profile_id = $1",
                &[&user_id],
            )
            .await?;
        transaction.commit().await?;
        return Ok(true);
    };
    if keys.is_empty() {
        return Ok(true);
    }
    if keys.iter().any(|key| key.contains('\0')) {
        return Ok(false);
    }

    // Lock matching rows in a consistent order before checking whether the
    // full request can succeed, including when two requests overlap.
    let deleted = transaction
        .execute(
            "WITH matching AS MATERIALIZED (
                 SELECT id
                 FROM zulip.zerver_botstoragedata
                 WHERE bot_profile_id = $1 AND key = ANY($2::text[])
                 ORDER BY id FOR UPDATE
             )
             DELETE FROM zulip.zerver_botstoragedata AS storage USING matching
             WHERE storage.id = matching.id
                 AND (SELECT count(*) FROM matching) = cardinality($2)",
            &[&user_id, &keys],
        )
        .await?;
    transaction.commit().await?;
    Ok(deleted == keys.len() as u64)
}
