use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/navigation_views";
pub(super) const SESSION_PATH: &str = "/json/navigation_views";

#[derive(Serialize)]
pub(super) struct NavigationView {
    fragment: String,
    is_pinned: bool,
    name: Option<String>,
}

pub(super) async fn list(database: &Client, user_id: i32) -> Result<Vec<NavigationView>, Error> {
    database
        .query(
            "SELECT fragment, is_pinned, name FROM zulip.zerver_navigationview WHERE user_id = $1",
            &[&user_id],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(NavigationView {
                fragment: row.try_get("fragment")?,
                is_pinned: row.try_get("is_pinned")?,
                name: row.try_get("name")?,
            })
        })
        .collect()
}
