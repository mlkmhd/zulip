use std::collections::BTreeMap;

use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/realm/emoji";
pub(super) const SESSION_PATH: &str = "/json/realm/emoji";

#[derive(Serialize)]
pub(super) struct RealmEmoji {
    id: String,
    name: String,
    source_url: String,
    deactivated: bool,
    author_id: i32,
    still_url: Option<String>,
}

fn without_extension(file_name: &str) -> &str {
    let basename = file_name
        .rsplit('/')
        .next()
        .expect("split always yields a segment");
    if let Some((stem, extension)) = basename.rsplit_once('.')
        && stem.bytes().any(|byte| byte != b'.')
    {
        return &file_name[..file_name.len() - extension.len() - 1];
    }
    file_name
}

pub(super) async fn list(
    database: &Client,
    realm_id: i32,
    url_base: &str,
) -> Result<BTreeMap<String, RealmEmoji>, Error> {
    let directory = format!("{url_base}{realm_id}/emoji/images");
    database
        .query(
            "SELECT id, name, file_name, is_animated, deactivated, author_id
             FROM zulip.zerver_realmemoji WHERE realm_id = $1 AND file_name IS NOT NULL",
            &[&realm_id],
        )
        .await?
        .iter()
        .map(|row| {
            let id = row.try_get::<_, i64>("id")?.to_string();
            let file_name: &str = row.try_get("file_name")?;
            let is_animated: bool = row.try_get("is_animated")?;
            let emoji = RealmEmoji {
                id: id.clone(),
                name: row.try_get("name")?,
                source_url: format!("{directory}/{file_name}"),
                deactivated: row.try_get("deactivated")?,
                author_id: row.try_get("author_id")?,
                still_url: is_animated
                    .then(|| format!("{directory}/still/{}.png", without_extension(file_name))),
            };
            Ok((id, emoji))
        })
        .collect()
}
