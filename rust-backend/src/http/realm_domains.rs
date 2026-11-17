use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/realm/domains";
pub(super) const SESSION_PATH: &str = "/json/realm/domains";

#[derive(Serialize)]
pub(super) struct RealmDomain {
    domain: String,
    allow_subdomains: bool,
}

pub(super) async fn list(database: &Client, realm_id: i32) -> Result<Vec<RealmDomain>, Error> {
    database
        .query(
            "SELECT domain, allow_subdomains FROM zulip.zerver_realmdomain WHERE realm_id = $1",
            &[&realm_id],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(RealmDomain {
                domain: row.try_get("domain")?,
                allow_subdomains: row.try_get("allow_subdomains")?,
            })
        })
        .collect()
}
