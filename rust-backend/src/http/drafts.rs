use std::fmt::Write as _;

use tokio_postgres::{Client, Error, GenericClient};

use crate::{
    draft_fields::{DraftFields, Recipients},
    draft_input::Location,
};

pub(super) const PATH: &str = "/api/v1/drafts";
pub(super) const SESSION_PATH: &str = "/json/drafts";
pub(super) const DETAIL_PATH: &str = "/api/v1/drafts/{id}";
pub(super) const SESSION_DETAIL_PATH: &str = "/json/drafts/{id}";
const STREAM: i16 = 2;

pub(super) async fn synchronization_enabled(
    database: &Client,
    user_id: i32,
) -> Result<bool, Error> {
    database
        .query_one(
            "SELECT enable_drafts_synchronization FROM zulip.zerver_userprofile WHERE id = $1",
            &[&user_id],
        )
        .await?
        .try_get(0)
}

pub(super) async fn list(database: &Client, user_id: i32) -> Result<Vec<DraftFields>, Error> {
    read_fields(database, user_id, None)
        .await
        .map(|rows| rows.into_iter().map(|(_, fields)| fields).collect())
}

pub(super) async fn read_fields(
    database: &impl GenericClient,
    user_id: i32,
    ids: Option<&[i64]>,
) -> Result<Vec<(i64, DraftFields)>, Error> {
    database
        .query(
            "SELECT d.id, d.topic, d.content, r.type AS recipient_type, r.type_id AS recipient_type_id,
                    trunc(extract(epoch FROM d.last_edit_time)::double precision)::bigint AS timestamp,
                    CASE WHEN r.type <> $2 THEN ARRAY(
                        SELECT s.user_profile_id FROM zulip.zerver_subscription AS s
                        WHERE s.recipient_id = r.id ORDER BY s.user_profile_id
                    ) ELSE ARRAY[]::integer[] END AS recipient_users
             FROM zulip.zerver_draft AS d
             LEFT JOIN zulip.zerver_recipient AS r ON r.id = d.recipient_id
             WHERE d.user_profile_id = $1 AND ($3::bigint[] IS NULL OR d.id = ANY($3))
             ORDER BY d.last_edit_time",
            &[&user_id, &STREAM, &ids],
        )
        .await?
        .iter()
        .map(|row| {
            let recipients = match row.try_get::<_, Option<i16>>("recipient_type")? {
                None => Recipients::Unset,
                Some(STREAM) => Recipients::Channel(row.try_get("recipient_type_id")?),
                Some(_) => Recipients::Direct(row.try_get("recipient_users")?),
            };
            let id = row.try_get("id")?;
            Ok((id, DraftFields::new(
                id,
                user_id,
                recipients,
                row.try_get("topic")?,
                row.try_get("content")?,
                row.try_get("timestamp")?,
            )))
        })
        .collect()
}

pub(super) async fn delete(database: &Client, user_id: i32, draft_id: i64) -> Result<bool, Error> {
    let deleted = database
        .execute(
            "DELETE FROM zulip.zerver_draft WHERE id = $1 AND user_profile_id = $2",
            &[&draft_id, &user_id],
        )
        .await?;
    Ok(deleted == 1)
}

pub(super) struct SchemaError {
    kind: &'static str,
    path: Vec<Location>,
}

impl SchemaError {
    pub(super) fn new(kind: &'static str, path: Vec<Location>) -> Self {
        Self { kind, path }
    }

    pub(super) fn template(&self) -> &'static str {
        match self.kind {
            "list_type" => "{var_name} is not a list",
            "int_type" => "{var_name} is not an integer",
            "string_type" => "{var_name} is not a string",
            "literal_error" => "Invalid {var_name}",
            "string_too_short" => "{var_name} cannot be blank",
            "missing" => "{var_name} field is missing: {msg}",
            "model_type" => "Invalid {var_name}: {msg}",
            "extra_forbidden" => "Argument \"{argument}\" at {var_name} is unexpected",
            _ => unreachable!("unexpected draft schema error: {}", self.kind),
        }
    }

    pub(super) fn format(&self, translated: &str) -> String {
        let mut name = String::from("drafts");
        for location in &self.path {
            name.push('[');
            match location {
                Location::Index(index) => write!(name, "{index}").unwrap(),
                Location::Field(field) => {
                    // Python's json.dumps uses ASCII escapes in error locations.
                    let quoted = serde_json::to_string(field).expect("strings serialize to JSON");
                    for unit in quoted.encode_utf16() {
                        if unit < 0x7f {
                            name.push(char::from(unit as u8));
                        } else {
                            write!(name, "\\u{unit:04x}").unwrap();
                        }
                    }
                }
            }
            name.push(']');
        }
        let argument = match self.path.last() {
            Some(Location::Field(field)) => field.as_str(),
            _ => "",
        };
        let message = match self.kind {
            "model_type" => "Input should be a valid dictionary or instance of DraftData",
            "missing" => "Field required",
            _ => "",
        };
        // Interpolate each placeholder in the translation once. A field name
        // can itself contain braces.
        let mut result = String::new();
        for part in translated.split_inclusive('}') {
            let Some((prefix, key)) = part.rsplit_once('{') else {
                result.push_str(part);
                continue;
            };
            match key {
                "var_name}" => {
                    result.push_str(prefix);
                    result.push_str(&name);
                }
                "argument}" => {
                    result.push_str(prefix);
                    result.push_str(argument);
                }
                "msg}" => {
                    result.push_str(prefix);
                    result.push_str(message);
                }
                _ => result.push_str(part),
            }
        }
        result
    }
}
