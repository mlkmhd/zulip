use serde::Serialize;
use tokio_postgres::{Client, Error};

pub(super) const PATH: &str = "/api/v1/realm/profile_fields";
pub(super) const SESSION_PATH: &str = "/json/realm/profile_fields";

#[derive(Serialize)]
pub(super) struct CustomProfileField {
    id: i64,
    name: String,
    #[serde(rename = "type")]
    field_type: i16,
    hint: String,
    field_data: String,
    order: i32,
    required: bool,
    editable_by_user: bool,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    display_in_profile_summary: bool,
    #[serde(skip_serializing_if = "std::ops::Not::not")]
    use_for_user_matching: bool,
}

pub(super) async fn list(
    database: &Client,
    realm_id: i32,
) -> Result<Vec<CustomProfileField>, Error> {
    database
        .query(
            r#"SELECT id, name, field_type, hint, field_data, "order", required, editable_by_user,
                      display_in_profile_summary, use_for_user_matching
               FROM zulip.zerver_customprofilefield WHERE realm_id = $1 ORDER BY "order""#,
            &[&realm_id],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(CustomProfileField {
                id: row.try_get("id")?,
                name: row.try_get("name")?,
                field_type: row.try_get("field_type")?,
                hint: row.try_get("hint")?,
                field_data: row.try_get("field_data")?,
                order: row.try_get("order")?,
                required: row.try_get("required")?,
                editable_by_user: row.try_get("editable_by_user")?,
                display_in_profile_summary: row.try_get("display_in_profile_summary")?,
                use_for_user_matching: row.try_get("use_for_user_matching")?,
            })
        })
        .collect()
}
