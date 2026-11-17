use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/realm/linkifiers";
pub(super) const SESSION_PATH: &str = "/json/realm/linkifiers";

#[derive(Serialize)]
pub(super) struct Linkifier {
    pattern: String,
    url_template: String,
    id: i64,
    example_input: Option<String>,
    reverse_template: Option<String>,
    alternative_url_templates: Vec<String>,
}

pub(super) async fn list(database: &Client, realm_id: i32) -> Result<Vec<Linkifier>, Error> {
    database
        .query(
            r#"SELECT pattern, url_template, id, example_input, reverse_template,
                      ARRAY(SELECT jsonb_array_elements_text(alternative_url_templates)) AS alternatives
               FROM zulip.zerver_realmfilter WHERE realm_id = $1 ORDER BY "order""#,
            &[&realm_id],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(Linkifier {
                pattern: row.try_get("pattern")?,
                url_template: row.try_get("url_template")?,
                id: row.try_get("id")?,
                example_input: row.try_get("example_input")?,
                reverse_template: row.try_get("reverse_template")?,
                alternative_url_templates: row.try_get("alternatives")?,
            })
        })
        .collect()
}
