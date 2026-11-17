use std::collections::{BTreeMap, BTreeSet};

use jiter::JsonValue;
use tokio_postgres::{Client, Error};

use super::form::{Form, FormError};

pub(super) const PATH: &str = "/api/v1/bot_storage";
pub(super) const SESSION_PATH: &str = "/json/bot_storage";
pub(super) const KEYS: &str = "keys";

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

pub(super) async fn delete(
    database: &Client,
    user_id: i32,
    keys: Option<Vec<String>>,
) -> Result<bool, Error> {
    let Some(keys) = keys else {
        database
            .execute(
                "DELETE FROM zulip.zerver_botstoragedata WHERE bot_profile_id = $1",
                &[&user_id],
            )
            .await?;
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
    let deleted = database
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
    Ok(deleted == keys.len() as u64)
}
