use std::{
    error::Error,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use axum::http::{HeaderMap, header};
use lapin::{
    BasicProperties, Channel, Confirmation, Connection, ConnectionProperties,
    options::{BasicPublishOptions, ConfirmSelectOptions, QueueDeclareOptions},
    types::FieldTable,
};
use tokio::time::timeout;
use tokio_postgres::Client;

use super::user_agent;

#[derive(Clone)]
pub(super) struct ActivityPublisher(Arc<Publisher>);

struct Publisher {
    _connection: Connection,
    channel: Channel,
    shards: u32,
}

type ActivityError = Box<dyn Error + Send + Sync>;
const BROKER_TIMEOUT: Duration = Duration::from_secs(5);

impl ActivityPublisher {
    pub(super) async fn connect(
        settings: super::ActivityConfig<'_>,
    ) -> Result<Self, Box<dyn Error>> {
        if settings.shards == 0 {
            return Err("ZULIP_RUST_USER_ACTIVITY_SHARDS must be positive".into());
        }
        let connection = timeout(
            BROKER_TIMEOUT,
            Connection::connect(
                settings.url,
                ConnectionProperties::default().enable_auto_recover(),
            ),
        )
        .await??;
        let channel = timeout(BROKER_TIMEOUT, connection.create_channel()).await??;
        timeout(
            BROKER_TIMEOUT,
            channel.confirm_select(ConfirmSelectOptions::default()),
        )
        .await??;
        for shard in 0..settings.shards {
            timeout(
                BROKER_TIMEOUT,
                channel.queue_declare(
                    queue_name(shard, settings.shards).into(),
                    QueueDeclareOptions::durable(),
                    FieldTable::default(),
                ),
            )
            .await??;
        }
        Ok(Self(Arc::new(Publisher {
            _connection: connection,
            channel,
            shards: settings.shards,
        })))
    }

    pub(super) async fn record(
        &self,
        db: &Client,
        user_id: i32,
        query: Option<&str>,
        headers: &HeaderMap,
    ) -> Result<(), ActivityError> {
        let client_id = resolve_client(db, &client_name(query, headers)).await?;
        let event = serde_json::json!({
            "query": "list_alert_words",
            "user_profile_id": user_id,
            "time": SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
            "client_id": client_id,
        });
        let queue = queue_name(user_id as u32 % self.0.shards, self.0.shards);
        let body = serde_json::to_vec(&event)?;
        let confirmation = timeout(BROKER_TIMEOUT, async {
            self.0
                .channel
                .basic_publish(
                    "".into(),
                    queue.into(),
                    BasicPublishOptions {
                        mandatory: true,
                        ..Default::default()
                    },
                    &body,
                    BasicProperties::default().with_delivery_mode(2),
                )
                .await?
                .await
        })
        .await??;
        if !matches!(confirmation, Confirmation::Ack(None)) {
            return Err("RabbitMQ did not confirm a routed activity message".into());
        }
        Ok(())
    }
}

fn queue_name(shard: u32, shards: u32) -> String {
    if shards == 1 {
        "user_activity".to_owned()
    } else {
        format!("user_activity_shard{}", shard + 1)
    }
}

fn client_name(query: Option<&str>, headers: &HeaderMap) -> String {
    if let Some((_, name)) = query.and_then(|query| {
        form_urlencoded::parse(query.as_bytes())
            .filter(|(key, _)| key == "client")
            .last()
    }) {
        return name.chars().take(30).collect();
    }
    let Some(value) = headers.get(header::USER_AGENT) else {
        return "Unspecified".to_owned();
    };
    let value = user_agent::decode(value);
    user_agent::parse(&value)
        .map_or("Unspecified", |parts| {
            parts.name("name").expect("matched client name").as_str()
        })
        .chars()
        .take(30)
        .collect()
}

async fn resolve_client(db: &Client, name: &str) -> Result<i32, tokio_postgres::Error> {
    let lookup = "SELECT id FROM zulip.zerver_client WHERE name = $1";
    if let Some(client) = db.query_opt(lookup, &[&name]).await? {
        return client.try_get("id");
    }
    if let Some(client) = db
        .query_opt(
            "INSERT INTO zulip.zerver_client (name) VALUES ($1)
         ON CONFLICT (name) DO NOTHING RETURNING id",
            &[&name],
        )
        .await?
    {
        return client.try_get("id");
    }
    // A concurrent request created the client. A new statement sees its committed row.
    db.query_one(lookup, &[&name]).await?.try_get("id")
}
