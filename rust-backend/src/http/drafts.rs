use serde::Serialize;
use tokio_postgres::{Client, Error, Row};

pub(super) const PATH: &str = "/api/v1/drafts";
pub(super) const SESSION_PATH: &str = "/json/drafts";
pub(super) const DETAIL_PATH: &str = "/api/v1/drafts/{id}";
pub(super) const SESSION_DETAIL_PATH: &str = "/json/drafts/{id}";
const STREAM: i16 = 2;

#[derive(Serialize)]
pub(super) struct Draft {
    id: i64,
    #[serde(rename = "type")]
    recipient_type: &'static str,
    to: Vec<i32>,
    topic: String,
    content: String,
    timestamp: i64,
}

impl Draft {
    fn from_row(row: &Row, user_id: i32) -> Result<Self, Error> {
        let (recipient_type, to) = match row.try_get::<_, Option<i16>>("recipient_type")? {
            None => ("", Vec::new()),
            Some(STREAM) => ("stream", vec![row.try_get("recipient_type_id")?]),
            Some(_) => {
                let mut users: Vec<i32> = row.try_get("recipient_users")?;
                if users.len() > 1 {
                    users.retain(|&id| id != user_id);
                }
                ("private", users)
            }
        };
        Ok(Self {
            id: row.try_get("id")?,
            recipient_type,
            to,
            topic: row.try_get("topic")?,
            content: row.try_get("content")?,
            timestamp: row.try_get("timestamp")?,
        })
    }
}

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

pub(super) async fn list(database: &Client, user_id: i32) -> Result<Vec<Draft>, Error> {
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
             WHERE d.user_profile_id = $1
             ORDER BY d.last_edit_time",
            &[&user_id, &STREAM],
        )
        .await?
        .iter()
        .map(|row| Draft::from_row(row, user_id))
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
