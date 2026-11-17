use tokio_postgres::GenericClient;

pub(super) async fn list(
    database: &impl GenericClient,
    user_id: i32,
) -> Result<Vec<String>, tokio_postgres::Error> {
    database
        .query(
            "SELECT word FROM zulip.zerver_alertword WHERE user_profile_id = $1",
            &[&user_id],
        )
        .await?
        .iter()
        .map(|row| row.try_get("word"))
        .collect()
}
