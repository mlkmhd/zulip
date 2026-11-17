use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/attachments";
pub(super) const SESSION_PATH: &str = "/json/attachments";

#[derive(Serialize)]
pub(super) struct Attachment {
    id: i64,
    name: String,
    path_id: String,
    size: i32,
    create_time: i64,
    message_ids: Vec<i32>,
}

pub(super) async fn list(
    database: &Client,
    user_id: i32,
    realm_id: i32,
) -> Result<(Vec<Attachment>, i128), Error> {
    // Realm usage adds uploads at and after the latest daily analytics snapshot.
    let rows = database
        .query(
            "WITH latest_count AS (
                SELECT value, end_time FROM zulip.analytics_realmcount
                WHERE realm_id = $2 AND property = 'upload_quota_used_bytes::day' AND subgroup IS NULL
                ORDER BY end_time DESC LIMIT 1
             ), usage AS (
                SELECT COALESCE((SELECT value FROM latest_count), 0) AS recorded,
                       COALESCE(sum(size), 0) AS added
                FROM zulip.zerver_attachment
                WHERE realm_id = $2
                  AND create_time >= COALESCE((SELECT end_time FROM latest_count), '-infinity')
             )
             SELECT a.id, a.file_name, a.path_id, a.size, usage.recorded, usage.added,
                    floor(extract(epoch FROM a.create_time))::bigint AS create_time,
                    ARRAY(
                        SELECT link.message_id FROM zulip.zerver_attachment_messages AS link
                        WHERE link.attachment_id = a.id
                    ) AS message_ids
             FROM usage LEFT JOIN zulip.zerver_attachment AS a ON a.owner_id = $1",
            &[&user_id, &realm_id],
        )
        .await?;
    let usage = rows
        .first()
        .expect("aggregate returns one row even without attachments");
    let recorded: i64 = usage.try_get("recorded")?;
    let added: i64 = usage.try_get("added")?;
    let mut attachments = Vec::new();
    for row in rows {
        if let Some(id) = row.try_get("id")? {
            attachments.push(Attachment {
                id,
                name: row.try_get("file_name")?,
                path_id: row.try_get("path_id")?,
                size: row.try_get("size")?,
                create_time: row.try_get("create_time")?,
                message_ids: row.try_get("message_ids")?,
            });
        }
    }
    Ok((attachments, i128::from(recorded) + i128::from(added)))
}
