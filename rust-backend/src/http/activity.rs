use std::{
    collections::BTreeSet,
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
use tokio::{
    sync::Mutex,
    time::{Instant, timeout},
};
use tokio_postgres::Client;

use super::{
    tornado_routing::{DEFAULT_QUEUE, TornadoRouting},
    user_agent,
};

#[derive(Clone)]
pub(super) struct ActivityPublisher(Arc<Publisher>);

struct Publisher {
    url: String,
    queues: BTreeSet<String>,
    broker: Mutex<BrokerState>,
    shards: u32,
    tornado_routing: Option<TornadoRouting>,
}

struct BrokerState {
    connection: Arc<BrokerConnection>,
    retry_at: Instant,
}

struct BrokerConnection {
    connection: Connection,
    channel: Channel,
}

type ActivityError = Box<dyn Error + Send + Sync>;
const BROKER_TIMEOUT: Duration = Duration::from_secs(5);
const RETRY_INTERVAL: Duration = Duration::from_secs(1);

impl BrokerConnection {
    async fn open(url: &str, queues: &BTreeSet<String>) -> Result<Self, lapin::Error> {
        let connection = Connection::connect(url, ConnectionProperties::default()).await?;
        let channel = connection.create_channel().await?;
        channel
            .confirm_select(ConfirmSelectOptions::default())
            .await?;
        for queue in queues {
            channel
                .queue_declare(
                    queue.as_str().into(),
                    QueueDeclareOptions::durable(),
                    FieldTable::default(),
                )
                .await?;
        }
        Ok(Self {
            connection,
            channel,
        })
    }

    fn connected(&self) -> bool {
        self.connection.status().connected() && self.channel.status().connected()
    }
}

impl ActivityPublisher {
    pub(super) async fn connect(
        settings: super::ActivityConfig<'_>,
        publishes_events: bool,
    ) -> Result<Self, Box<dyn Error>> {
        if settings.shards == 0 {
            return Err("ZULIP_RUST_USER_ACTIVITY_SHARDS must be positive".into());
        }
        let tornado_routing = settings
            .tornado_routing_file
            .map(TornadoRouting::load)
            .transpose()?;
        let mut queues = (0..settings.shards)
            .map(|shard| queue_name(shard, settings.shards))
            .collect::<BTreeSet<_>>();
        if publishes_events {
            if let Some(routing) = &tornado_routing {
                queues.extend(routing.queues().cloned());
            } else {
                queues.insert(DEFAULT_QUEUE.to_owned());
            }
        }
        let connection = timeout(
            BROKER_TIMEOUT,
            BrokerConnection::open(settings.url, &queues),
        )
        .await??;
        Ok(Self(Arc::new(Publisher {
            url: settings.url.to_owned(),
            queues,
            broker: Mutex::new(BrokerState {
                connection: Arc::new(connection),
                retry_at: Instant::now(),
            }),
            shards: settings.shards,
            tornado_routing,
        })))
    }

    async fn connection(&self) -> Result<Arc<BrokerConnection>, ActivityError> {
        let mut state = timeout(BROKER_TIMEOUT, self.0.broker.lock()).await?;
        if !state.connection.connected() {
            if Instant::now() < state.retry_at {
                return Err("RabbitMQ reconnection is temporarily unavailable".into());
            }
            // Replace a failed connection without resending an unconfirmed publication.
            let result = timeout(
                BROKER_TIMEOUT,
                BrokerConnection::open(&self.0.url, &self.0.queues),
            )
            .await;
            state.retry_at = Instant::now() + RETRY_INTERVAL;
            state.connection = Arc::new(result??);
        }
        Ok(Arc::clone(&state.connection))
    }

    pub(super) async fn ready(&self) -> bool {
        self.connection().await.is_ok()
    }

    pub(super) async fn record(
        &self,
        db: &Client,
        user_id: i32,
        endpoint: &str,
        client_name: &str,
    ) -> Result<(), ActivityError> {
        let client_id = resolve_client(db, client_name).await?;
        let event = serde_json::json!({
            "query": endpoint,
            "user_profile_id": user_id,
            "time": SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
            "client_id": client_id,
        });
        let queue = queue_name(user_id as u32 % self.0.shards, self.0.shards);
        self.publish(&queue, event).await
    }

    pub(super) async fn notification_queue(&self, realm_id: i32, user_id: i32) -> Option<&str> {
        let queue = match &self.0.tornado_routing {
            Some(routing) => routing.queue_for_user(realm_id, user_id),
            None => Some(DEFAULT_QUEUE),
        }?;
        self.connection().await.ok()?;
        Some(queue)
    }

    pub(super) async fn notify_user(
        &self,
        queue: &str,
        user_id: i32,
        event: serde_json::Value,
    ) -> Result<(), ActivityError> {
        self.publish(
            queue,
            serde_json::json!({
                "event": event,
                "users": [user_id],
            }),
        )
        .await
    }

    async fn publish(&self, queue: &str, event: serde_json::Value) -> Result<(), ActivityError> {
        let body = serde_json::to_vec(&event)?;
        let connection = self.connection().await?;
        let confirmation = timeout(BROKER_TIMEOUT, async {
            connection
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
            return Err("RabbitMQ did not confirm a routed message".into());
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

pub(super) fn client_name(
    form_client: Option<&str>,
    query: Option<&str>,
    headers: &HeaderMap,
) -> String {
    if let Some(name) = form_client {
        return name.chars().take(30).collect();
    }
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
