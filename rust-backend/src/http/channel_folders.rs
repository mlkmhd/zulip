use jiter::JsonValue;
use serde::Serialize;
use tokio_postgres::{Client, Error};

use super::form::{Form, FormError};

pub(super) const PATH: &str = "/api/v1/channel_folders";
pub(super) const SESSION_PATH: &str = "/json/channel_folders";
pub(super) const INCLUDE_ARCHIVED: &str = "include_archived";

#[derive(Serialize)]
pub(super) struct ChannelFolder {
    id: i64,
    name: String,
    description: String,
    rendered_description: String,
    order: i32,
    creator_id: Option<i32>,
    date_created: i64,
    is_archived: bool,
}

pub(super) fn include_archived(form: &Form) -> Result<bool, FormError> {
    let Some(value) = form.value(INCLUDE_ARCHIVED) else {
        return Ok(false);
    };
    match JsonValue::parse(value.as_bytes(), true)
        .map_err(|_| FormError::InvalidJson(INCLUDE_ARCHIVED))?
    {
        JsonValue::Bool(value) => Ok(value),
        _ => Err(FormError::ExpectedBoolean(INCLUDE_ARCHIVED)),
    }
}

pub(super) async fn list(
    database: &Client,
    realm_id: i32,
    include_archived: bool,
) -> Result<Vec<ChannelFolder>, Error> {
    database
        .query(
            r#"SELECT id, name, description, rendered_description, "order", creator_id,
                      trunc(extract(epoch FROM date_created)::double precision)::bigint AS date_created,
                      is_archived
               FROM zulip.zerver_channelfolder
               WHERE realm_id = $1 AND ($2 OR NOT is_archived) ORDER BY "order""#,
            &[&realm_id, &include_archived],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(ChannelFolder {
                id: row.try_get("id")?,
                name: row.try_get("name")?,
                description: row.try_get("description")?,
                rendered_description: row.try_get("rendered_description")?,
                order: row.try_get("order")?,
                creator_id: row.try_get("creator_id")?,
                date_created: row.try_get("date_created")?,
                is_archived: row.try_get("is_archived")?,
            })
        })
        .collect()
}
