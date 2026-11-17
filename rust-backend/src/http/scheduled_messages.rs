use serde::Serialize;
use tokio_postgres::{Client, Error, Row};

pub(super) const PATH: &str = "/api/v1/scheduled_messages";
pub(super) const SESSION_PATH: &str = "/json/scheduled_messages";
pub(super) const REMINDERS_PATH: &str = "/api/v1/reminders";
pub(super) const SESSION_REMINDERS_PATH: &str = "/json/reminders";
const STREAM: i16 = 2;

#[derive(Clone, Copy)]
#[repr(i16)]
pub(super) enum JobKind {
    Message = 1,
    Reminder = 2,
}

#[derive(Serialize)]
#[serde(tag = "type")]
enum Recipient {
    #[serde(rename = "stream")]
    Channel { to: i32, topic: String },
    #[serde(rename = "private")]
    Direct { to: Vec<i32> },
}

#[derive(Serialize)]
pub(super) struct MessageFields {
    #[serde(flatten)]
    recipient: Recipient,
    content: String,
    rendered_content: String,
    scheduled_delivery_timestamp: i64,
    failed: bool,
}

#[derive(Serialize)]
#[serde(untagged)]
pub(super) enum ScheduledJob {
    Message {
        scheduled_message_id: i64,
        #[serde(flatten)]
        fields: MessageFields,
    },
    Reminder {
        reminder_id: i64,
        #[serde(flatten)]
        fields: MessageFields,
        reminder_target_message_id: i32,
    },
}

impl ScheduledJob {
    fn from_row(row: &Row, user_id: i32, kind: JobKind) -> Result<Self, Error> {
        let recipient = if row.try_get::<_, i16>("recipient_type")? == STREAM {
            Recipient::Channel {
                to: row.try_get("recipient_type_id")?,
                topic: row.try_get("subject")?,
            }
        } else {
            let mut users: Vec<i32> = row.try_get("recipient_users")?;
            if users.len() > 1 {
                users.retain(|&id| id != user_id);
            }
            Recipient::Direct { to: users }
        };
        let id = row.try_get("id")?;
        let fields = MessageFields {
            recipient,
            content: row.try_get("content")?,
            rendered_content: row.try_get("rendered_content")?,
            scheduled_delivery_timestamp: row.try_get("delivery_timestamp")?,
            failed: row.try_get("failed")?,
        };
        Ok(match kind {
            JobKind::Message => Self::Message {
                scheduled_message_id: id,
                fields,
            },
            JobKind::Reminder => Self::Reminder {
                reminder_id: id,
                fields,
                reminder_target_message_id: row.try_get("reminder_target_message_id")?,
            },
        })
    }
}

pub(super) async fn list(
    database: &Client,
    user_id: i32,
    realm_id: i32,
    kind: JobKind,
) -> Result<Vec<ScheduledJob>, Error> {
    database
        .query(
            "SELECT m.id, m.subject, m.content, m.rendered_content, m.failed,
                    m.reminder_target_message_id, r.type AS recipient_type, r.type_id AS recipient_type_id,
                    trunc(extract(epoch FROM m.scheduled_timestamp)::double precision)::bigint AS delivery_timestamp,
                    CASE WHEN r.type <> $4 THEN ARRAY(
                        SELECT s.user_profile_id FROM zulip.zerver_subscription AS s
                        WHERE s.recipient_id = r.id ORDER BY s.user_profile_id
                    ) ELSE ARRAY[]::integer[] END AS recipient_users
             FROM zulip.zerver_scheduledmessage AS m
             JOIN zulip.zerver_recipient AS r ON r.id = m.recipient_id
             WHERE m.sender_id = $1 AND m.realm_id = $2 AND m.delivery_type = $3 AND NOT m.delivered
             ORDER BY m.scheduled_timestamp",
            &[&user_id, &realm_id, &(kind as i16), &STREAM],
        )
        .await?
        .iter()
        .map(|row| ScheduledJob::from_row(row, user_id, kind))
        .collect()
}
