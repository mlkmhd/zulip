use tokio_postgres::{Client, Error};

use crate::draft_fields::{DraftFields, Recipients};

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
        .map(|row| {
            let recipients = match row.try_get::<_, Option<i16>>("recipient_type")? {
                None => Recipients::Unset,
                Some(STREAM) => Recipients::Channel(row.try_get("recipient_type_id")?),
                Some(_) => Recipients::Direct(row.try_get("recipient_users")?),
            };
            Ok(DraftFields::new(
                row.try_get("id")?,
                user_id,
                recipients,
                row.try_get("topic")?,
                row.try_get("content")?,
                row.try_get("timestamp")?,
            ))
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
