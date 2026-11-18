use std::{
    collections::{HashMap, HashSet},
    fmt::Write as _,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use axum::http::StatusCode;
use jiter::NumberInt;
use tokio_postgres::{Client, Error};

use crate::{
    direct_message_group::{FIND_GROUP_SQL, INITIALIZE_GROUP_SQL, INSERT_GROUP_SQL, member_hash},
    draft_fields::{DraftFields, INSERT_DRAFTS_SQL},
    draft_input::{DraftInput, Timestamp},
    draft_recipients::{
        CHANNEL_RECIPIENT_SQL, DIRECT_RECIPIENTS_SQL, DirectRecipient, DirectRecipientError,
        validate_direct_recipients,
    },
    draft_text::{self, BodyError},
    draft_timestamp::{self, TimestampError},
};

use super::drafts;

const DIRECT_MESSAGE_GROUP: i16 = 3;
const DEFAULT_SUBSCRIPTION_COLOR: &str = "#c2c2c2";
const MAX_TOPIC_LENGTH: usize = 60;
const ROLE_GUEST: i16 = 600;

#[derive(Debug)]
pub(super) enum CreateError {
    Database(Error),
    Invalid(&'static str),
    InvalidUser(String),
    DeactivatedUser(String),
    TimestampOutOfRange,
    IncompleteGroup,
    MissingDraft,
}

impl From<Error> for CreateError {
    fn from(error: Error) -> Self {
        Self::Database(error)
    }
}

struct Sender {
    id: i32,
    realm_id: i32,
    email: String,
    is_guest: bool,
}

struct ValidatedDraft {
    recipient_id: Option<i32>,
    topic: String,
    content: String,
    timestamp: SystemTime,
}

// The shared queries bind each parameter once, in argument order.
fn postgres_sql(query: &str) -> String {
    let mut parts = query.split("%s");
    let mut sql = parts.next().unwrap_or_default().to_owned();
    for (index, part) in parts.enumerate() {
        write!(sql, "${}{part}", index + 1).expect("writing to a String is infallible");
    }
    sql
}

fn timestamp(value: Option<Timestamp>) -> Result<SystemTime, CreateError> {
    let seconds = match value {
        None => SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system clock is after the Unix epoch")
            .as_secs_f64(),
        Some(Timestamp::Float(seconds)) => seconds,
        Some(Timestamp::Integer(NumberInt::Int(seconds))) => seconds as f64,
        Some(Timestamp::Integer(NumberInt::BigInt(seconds))) => {
            // Even the smallest timestamp outside i64 is out of range.
            seconds
                .to_string()
                .parse()
                .expect("integers parse as floats")
        }
    };
    let micros = draft_timestamp::unix_microseconds(seconds).map_err(|error| match error {
        TimestampError::Negative => CreateError::Invalid("Timestamp must not be negative."),
        TimestampError::OutOfRange => CreateError::TimestampOutOfRange,
    })?;
    Ok(UNIX_EPOCH + Duration::from_micros(micros as u64))
}

async fn group_recipient(database: &mut Client, members: &[i32]) -> Result<i32, CreateError> {
    let hash = member_hash(members);
    let count = i32::try_from(members.len()).expect("draft form limits bound the member count");
    let find_group = postgres_sql(FIND_GROUP_SQL);
    let transaction = database.transaction().await?;
    let recipient_id: Option<i32> =
        if let Some(group) = transaction.query_opt(&find_group, &[&hash, &count]).await? {
            group.try_get(1)?
        } else if let Some(inserted) = transaction
            .query_opt(&postgres_sql(INSERT_GROUP_SQL), &[&hash, &count])
            .await?
        {
            let group_id: i64 = inserted.try_get(0)?;
            transaction
                .query_one(
                    &postgres_sql(INITIALIZE_GROUP_SQL),
                    &[
                        &group_id,
                        &DIRECT_MESSAGE_GROUP,
                        &DEFAULT_SUBSCRIPTION_COLOR,
                        &members,
                    ],
                )
                .await?
                .try_get(0)?
        } else {
            // Read the winner in a new READ COMMITTED snapshot.
            transaction
                .query_one(&find_group, &[&hash, &count])
                .await?
                .try_get(1)?
        };
    let recipient_id = recipient_id.ok_or(CreateError::IncompleteGroup)?;
    transaction.commit().await?;
    Ok(recipient_id)
}

fn recipient_id(number: &NumberInt) -> Result<i64, String> {
    match number {
        NumberInt::Int(id) => Ok(*id),
        NumberInt::BigInt(id) => {
            let id = id.to_string();
            id.parse().map_err(|_| id)
        }
    }
}

async fn direct_recipient(
    database: &mut Client,
    sender: &Sender,
    to: Vec<NumberInt>,
    bot_emails: &HashSet<String>,
) -> Result<i32, CreateError> {
    let mut ids = Vec::with_capacity(to.len());
    let mut seen = HashSet::with_capacity(to.len());
    for id in &to {
        let id = recipient_id(id).map_err(CreateError::InvalidUser)?;
        if seen.insert(id) {
            ids.push(id);
        }
    }
    let recipients = database
        .query(
            &postgres_sql(DIRECT_RECIPIENTS_SQL),
            &[&sender.id, &sender.realm_id, &sender.is_guest, &ids],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(DirectRecipient {
                id: row.try_get(0)?,
                realm_id: row.try_get(1)?,
                delivery_email: row.try_get(2)?,
                email: row.try_get(3)?,
                deactivated: row.try_get(4)?,
                accessible: row.try_get(5)?,
            })
        })
        .collect::<Result<Vec<_>, Error>>()?;
    validate_direct_recipients(
        sender.id,
        sender.realm_id,
        &sender.email,
        &ids,
        bot_emails,
        &recipients,
    )
    .map_err(|error| match error {
        DirectRecipientError::InvalidId(id) => CreateError::InvalidUser(id.to_string()),
        DirectRecipientError::Inaccessible => {
            CreateError::Invalid("You do not have permission to access some of the recipients.")
        }
        DirectRecipientError::Deactivated(email) => CreateError::DeactivatedUser(email.into()),
        DirectRecipientError::CrossRealm => {
            CreateError::Invalid("You can't send direct messages outside of your organization.")
        }
    })?;

    let mut members: Vec<_> = recipients.into_iter().map(|row| row.id).collect();
    if seen.insert(i64::from(sender.id)) {
        members.push(sender.id);
    }
    members.sort_unstable();
    group_recipient(database, &members).await
}

async fn validate_draft(
    database: &mut Client,
    sender: &Sender,
    draft: DraftInput,
    max_message_length: usize,
    bot_emails: &HashSet<String>,
) -> Result<ValidatedDraft, CreateError> {
    let content = draft_text::normalize_body(&draft.content, max_message_length)
        .map_err(|error| match error {
            BodyError::Empty => CreateError::Invalid("Message must not be empty"),
            BodyError::NullByte => CreateError::Invalid("Message must not contain null bytes"),
        })?
        .into_owned();
    let timestamp = timestamp(draft.timestamp)?;

    let (recipient_id, topic) = match draft.r#type {
        "stream" => {
            let topic = draft_text::normalize_topic(&draft.topic, MAX_TOPIC_LENGTH)
                .ok_or(CreateError::Invalid("Topic must not contain null bytes"))?
                .into_owned();
            let [id] = draft.to.as_slice() else {
                return Err(CreateError::Invalid(
                    "Must specify exactly 1 channel ID for channel messages",
                ));
            };
            let invalid = CreateError::Invalid("Invalid channel ID");
            let id = recipient_id(id).map_err(|_| CreateError::Invalid("Invalid channel ID"))?;
            let row = database
                .query_opt(
                    &postgres_sql(CHANNEL_RECIPIENT_SQL),
                    &[&sender.id, &id, &sender.realm_id, &sender.is_guest],
                )
                .await?
                .ok_or(invalid)?;
            (Some(row.try_get(0)?), topic)
        }
        "private" if !draft.to.is_empty() => {
            let recipient_id = direct_recipient(database, sender, draft.to, bot_emails).await?;
            (Some(recipient_id), String::new())
        }
        _ => (None, String::new()),
    };
    Ok(ValidatedDraft {
        recipient_id,
        topic,
        content,
        timestamp,
    })
}

pub(super) async fn create(
    database: &mut Client,
    user_id: i32,
    realm_id: i32,
    drafts: Vec<DraftInput>,
    max_message_length: usize,
    bot_emails: &HashSet<String>,
) -> Result<(Vec<i64>, Vec<DraftFields>), CreateError> {
    if drafts.is_empty() {
        return Ok((Vec::new(), Vec::new()));
    }
    let profile = database
        .query_one(
            "SELECT email, role = $2 FROM zulip.zerver_userprofile WHERE id = $1",
            &[&user_id, &ROLE_GUEST],
        )
        .await?;
    let sender = Sender {
        id: user_id,
        realm_id,
        email: profile.try_get(0)?,
        is_guest: profile.try_get(1)?,
    };
    let mut validated = Vec::with_capacity(drafts.len());
    for draft in drafts {
        // Earlier direct-message groups persist if a later draft fails validation.
        validated
            .push(validate_draft(database, &sender, draft, max_message_length, bot_emails).await?);
    }
    let recipient_ids: Vec<_> = validated.iter().map(|draft| draft.recipient_id).collect();
    let topics: Vec<_> = validated.iter().map(|draft| draft.topic.as_str()).collect();
    let contents: Vec<_> = validated
        .iter()
        .map(|draft| draft.content.as_str())
        .collect();
    let times: Vec<_> = validated.iter().map(|draft| draft.timestamp).collect();
    let transaction = database.transaction().await?;
    let ids = transaction
        .query(
            &postgres_sql(INSERT_DRAFTS_SQL),
            &[&recipient_ids, &topics, &contents, &times, &user_id],
        )
        .await?
        .iter()
        .map(|row| row.try_get(0))
        .collect::<Result<Vec<i64>, Error>>()?;
    let mut fields: HashMap<_, _> = drafts::read_fields(&transaction, user_id, Some(&ids))
        .await?
        .into_iter()
        .collect();
    let fields = ids
        .iter()
        .map(|id| fields.remove(id).ok_or(CreateError::MissingDraft))
        .collect::<Result<Vec<_>, _>>()?;
    transaction.commit().await?;
    Ok((ids, fields))
}

impl CreateError {
    pub(super) fn response(&self) -> (StatusCode, &'static str, &'static str) {
        let message = match self {
            Self::Invalid(message) => message,
            Self::InvalidUser(_) => "Invalid user ID {user_id}",
            Self::DeactivatedUser(_) => "'{email}' is no longer using Zulip.",
            Self::TimestampOutOfRange | Self::IncompleteGroup | Self::MissingDraft => {
                return (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "INTERNAL_SERVER_ERROR",
                    "Internal server error",
                );
            }
            Self::Database(_) => unreachable!("database errors handled separately"),
        };
        (StatusCode::BAD_REQUEST, "BAD_REQUEST", message)
    }

    pub(super) fn format(&self, translated: &str) -> String {
        match self {
            Self::InvalidUser(id) => translated.replace("{user_id}", id),
            Self::DeactivatedUser(email) => translated.replace("{email}", email),
            _ => translated.to_owned(),
        }
    }
}
