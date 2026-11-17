use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/saved_snippets";

#[derive(Serialize)]
pub(super) struct SavedSnippet {
    id: i64,
    title: String,
    content: String,
    date_created: i64,
}

pub(super) async fn list(database: &Client, user_id: i32) -> Result<Vec<SavedSnippet>, Error> {
    database
        .query(
            "SELECT id, title, content,
                    trunc(extract(epoch FROM date_created)::double precision)::bigint AS date_created
             FROM zulip.zerver_savedsnippet WHERE user_profile_id = $1",
            &[&user_id],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(SavedSnippet {
                id: row.try_get("id")?,
                title: row.try_get("title")?,
                content: row.try_get("content")?,
                date_created: row.try_get("date_created")?,
            })
        })
        .collect()
}
