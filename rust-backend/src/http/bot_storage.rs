use std::collections::{BTreeMap, BTreeSet};

use jiter::JsonValue;
use tokio_postgres::{Client, Error};

use super::form::{Form, FormError};

pub(super) const PATH: &str = "/api/v1/bot_storage";
pub(super) const SESSION_PATH: &str = "/json/bot_storage";
pub(super) const KEYS: &str = "keys";

pub(super) fn keys(form: &Form) -> Result<Option<BTreeSet<String>>, FormError> {
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
    keys: Option<BTreeSet<String>>,
) -> Result<Option<BTreeMap<String, String>>, Error> {
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
