use std::collections::{HashMap, HashSet};
use std::{io, net::SocketAddr};

use jiter::NumberInt;
use pyo3::exceptions::{PyConnectionError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

use crate::draft_fields::{DraftFields, Recipients};
use crate::draft_input::{self, DraftInput, Location, ParseError, Timestamp};
use crate::draft_recipients::{DirectRecipient, DirectRecipientError, validate_direct_recipients};
use crate::{
    alert_words, bot_storage, direct_message_group, draft_text, draft_timestamp, memcached,
    stream_color, user_status,
};

type DraftParseResult<T> = (Option<T>, Option<(&'static str, Vec<Location>)>);

struct PythonDraftInteger(NumberInt);

impl<'py> IntoPyObject<'py> for PythonDraftInteger {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        match self.0 {
            NumberInt::Int(value) => Ok(value.into_pyobject(py)?.into_any()),
            NumberInt::BigInt(value) => Ok(value.into_pyobject(py)?.into_any()),
        }
    }
}

impl<'py> IntoPyObject<'py> for Timestamp {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        match self {
            Self::Integer(value) => PythonDraftInteger(value).into_pyobject(py),
            Self::Float(value) => Ok(value.into_pyobject(py)?.into_any()),
        }
    }
}

impl<'py> IntoPyObject<'py> for DraftInput {
    type Target = PyDict;
    type Output = Bound<'py, PyDict>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        let draft = PyDict::new(py);
        draft.set_item("type", self.r#type)?;
        draft.set_item(
            "to",
            PyList::new(py, self.to.into_iter().map(PythonDraftInteger))?,
        )?;
        draft.set_item("topic", self.topic)?;
        draft.set_item("content", self.content)?;
        draft.set_item("timestamp", self.timestamp)?;
        Ok(draft)
    }
}

impl<'py> IntoPyObject<'py> for Location {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        match self {
            Self::Index(value) => Ok(value.into_pyobject(py)?.into_any()),
            Self::Field(value) => Ok(value.into_pyobject(py)?.into_any()),
        }
    }
}

fn draft_parse_result<T>(
    value: &Bound<'_, PyString>,
    parse: impl FnOnce(&str) -> Result<T, ParseError>,
) -> DraftParseResult<T> {
    match value
        .to_str()
        .map_err(|_| ParseError::Unsupported)
        .and_then(parse)
    {
        Ok(draft) => (Some(draft), None),
        Err(ParseError::Schema(kind, path)) => (None, Some((kind, path))),
        Err(ParseError::Unsupported) => (None, None),
    }
}

#[pymodule]
mod _zulip_backend {
    use super::*;

    #[pyfunction]
    fn parse_draft(value: &Bound<'_, PyString>) -> DraftParseResult<DraftInput> {
        draft_parse_result(value, draft_input::parse_draft)
    }

    #[pyfunction]
    fn parse_draft_batch(value: &Bound<'_, PyString>) -> DraftParseResult<Vec<DraftInput>> {
        draft_parse_result(value, draft_input::parse_draft_batch)
    }

    #[pyfunction]
    fn draft_channel_recipient(
        cursor: &Bound<'_, PyAny>,
        stream_id: i64,
        realm_id: i32,
        user_id: i32,
        is_guest: bool,
    ) -> PyResult<Option<(Option<i32>,)>> {
        // Drafts require channel content access, even for realm administrators.
        cursor.call_method1(
            "execute",
            (
                crate::draft_recipients::CHANNEL_RECIPIENT_SQL,
                (user_id, stream_id, realm_id, is_guest),
            ),
        )?;
        cursor.call_method0("fetchone")?.extract()
    }

    #[pyfunction]
    fn storage_size_difference(entries: Vec<(String, String, u64)>) -> i128 {
        bot_storage::storage_size_difference(&entries)
    }

    #[pyfunction]
    fn validate_draft_direct_recipients(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        realm_id: i32,
        email: &str,
        is_guest: bool,
        ids: Vec<i64>,
        cross_realm_bot_emails: HashSet<String>,
    ) -> PyResult<Option<(&'static str, String)>> {
        cursor.call_method1(
            "execute",
            (
                crate::draft_recipients::DIRECT_RECIPIENTS_SQL,
                (user_id, realm_id, is_guest, &ids),
            ),
        )?;
        type RecipientRow = (i32, i32, String, String, bool, bool);
        let rows: Vec<RecipientRow> = cursor.call_method0("fetchall")?.extract()?;
        let recipients: Vec<_> = rows
            .into_iter()
            .map(
                |(id, realm_id, delivery_email, email, deactivated, accessible)| DirectRecipient {
                    id,
                    realm_id,
                    delivery_email,
                    email,
                    deactivated,
                    accessible,
                },
            )
            .collect();
        Ok(validate_direct_recipients(
            user_id,
            realm_id,
            email,
            &ids,
            &cross_realm_bot_emails,
            &recipients,
        )
        .err()
        .map(|error| match error {
            DirectRecipientError::InvalidId(id) => ("invalid_id", id.to_string()),
            DirectRecipientError::Inaccessible => ("inaccessible", String::new()),
            DirectRecipientError::Deactivated(email) => ("deactivated", email.to_owned()),
            DirectRecipientError::CrossRealm => ("cross_realm", String::new()),
        }))
    }

    #[pyfunction]
    fn get_or_create_direct_message_group(
        cursor: &Bound<'_, PyAny>,
        member_ids: Vec<i32>,
        recipient_type: i16,
        default_color: &str,
    ) -> PyResult<(i64, Option<i32>)> {
        let member_hash = direct_message_group::member_hash(&member_ids);
        let group_size = i32::try_from(member_ids.len())
            .map_err(|_| PyValueError::new_err("Too many direct message group members"))?;
        let lookup = || -> PyResult<Option<(i64, Option<i32>)>> {
            cursor.call_method1(
                "execute",
                (
                    direct_message_group::FIND_GROUP_SQL,
                    (&member_hash, group_size),
                ),
            )?;
            cursor.call_method0("fetchone")?.extract()
        };
        if let Some(group) = lookup()? {
            return Ok(group);
        }

        cursor.call_method1(
            "execute",
            (
                direct_message_group::INSERT_GROUP_SQL,
                (&member_hash, group_size),
            ),
        )?;
        let inserted: Option<(i64,)> = cursor.call_method0("fetchone")?.extract()?;
        let Some((group_id,)) = inserted else {
            // PostgreSQL can observe the conflicting insert before it is
            // visible in this statement's snapshot. Read it in a new statement.
            return lookup()?
                .ok_or_else(|| PyValueError::new_err("Direct message group not found"));
        };

        cursor.call_method1(
            "execute",
            (
                direct_message_group::INITIALIZE_GROUP_SQL,
                (group_id, recipient_type, default_color, member_ids),
            ),
        )?;
        let (recipient_id,) = cursor.call_method0("fetchone")?.extract()?;
        Ok((group_id, Some(recipient_id)))
    }

    #[pyfunction]
    fn normalize_draft_body(
        body: &str,
        max_length: usize,
    ) -> (Option<String>, Option<&'static str>) {
        match draft_text::normalize_body(body, max_length) {
            Ok(body) => (Some(body.into_owned()), None),
            Err(draft_text::BodyError::Empty) => (None, Some("empty")),
            Err(draft_text::BodyError::NullByte) => (None, Some("null_byte")),
        }
    }

    #[pyfunction]
    fn normalize_draft_topic(topic: &str, max_length: usize) -> Option<String> {
        draft_text::normalize_topic(topic, max_length).map(std::borrow::Cow::into_owned)
    }

    #[pyfunction]
    fn normalize_draft_timestamp(timestamp: f64) -> (Option<i64>, bool) {
        match draft_timestamp::unix_microseconds(timestamp) {
            Ok(micros) => (Some(micros), false),
            Err(draft_timestamp::TimestampError::Negative) => (None, true),
            Err(draft_timestamp::TimestampError::OutOfRange) => (None, false),
        }
    }

    #[pyfunction(signature = (id, owner_id, channel_id, dm_members, topic, content, timestamp))]
    fn serialize_draft(
        id: i64,
        owner_id: i32,
        channel_id: Option<i32>,
        dm_members: Option<Vec<i32>>,
        topic: String,
        content: String,
        timestamp: i64,
    ) -> DraftFields {
        let recipients = match (channel_id, dm_members) {
            (Some(id), _) => Recipients::Channel(id),
            (None, Some(members)) => Recipients::Direct(members),
            (None, None) => Recipients::Unset,
        };
        DraftFields::new(id, owner_id, recipients, topic, content, timestamp)
    }

    #[pyfunction]
    fn insert_drafts(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        recipients: &Bound<'_, PyList>,
        topics: &Bound<'_, PyList>,
        contents: &Bound<'_, PyList>,
        edit_times: &Bound<'_, PyList>,
    ) -> PyResult<Vec<(i64,)>> {
        // Keep each ID paired with its input position regardless of RETURNING order.
        cursor.call_method1(
            "execute",
            (
                crate::draft_fields::INSERT_DRAFTS_SQL,
                (recipients, topics, contents, edit_times, user_id),
            ),
        )?;
        cursor.call_method0("fetchall")?.extract()
    }

    #[pyfunction]
    fn update_draft(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        draft_id: i64,
        recipient_id: Option<i32>,
        topic: &str,
        content: &str,
        last_edit_time: &Bound<'_, PyAny>,
    ) -> PyResult<bool> {
        cursor.call_method1(
            "execute",
            (
                "UPDATE zulip.zerver_draft
                 SET recipient_id = %s, topic = %s, content = %s, last_edit_time = %s
                 WHERE user_profile_id = %s AND id = %s",
                (
                    recipient_id,
                    topic,
                    content,
                    last_edit_time,
                    user_id,
                    draft_id,
                ),
            ),
        )?;
        Ok(cursor.getattr("rowcount")?.extract::<i64>()? == 1)
    }

    #[pyfunction]
    fn format_user_status<'py>(
        py: Python<'py>,
        presence_enabled: bool,
        status_text: &str,
        emoji_name: &str,
        emoji_code: &str,
        reaction_type: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let status = user_status::UserStatus::from_row(
            presence_enabled,
            status_text,
            emoji_name,
            emoji_code,
            reaction_type,
        );
        let result = PyDict::new(py);
        if status.away {
            result.set_item("away", true)?;
        }
        if let Some(text) = status.text {
            result.set_item("status_text", text)?;
        }
        if let Some(emoji) = status.emoji {
            result.set_item("emoji_name", emoji.name)?;
            result.set_item("emoji_code", emoji.code)?;
            result.set_item("reaction_type", emoji.reaction_type)?;
        }
        Ok(result)
    }

    #[pyfunction]
    #[pyo3(signature = (away, status_text, emoji_name, emoji_code, emoji_type))]
    fn prepare_status_update<'a>(
        away: Option<bool>,
        status_text: Option<&str>,
        emoji_name: Option<&str>,
        emoji_code: Option<&'a str>,
        emoji_type: Option<&'a str>,
    ) -> (Option<&'a str>, Option<&'a str>, Option<&'static str>) {
        match user_status::prepare_status_update(
            away,
            status_text,
            emoji_name,
            emoji_code,
            emoji_type,
        ) {
            Ok((code, kind)) => (code, kind, None),
            Err(user_status::StatusUpdateError::NoValues) => (None, None, Some("no_values")),
            Err(user_status::StatusUpdateError::MissingEmojiName) => {
                (None, None, Some("missing_emoji_name"))
            }
        }
    }

    #[pyfunction]
    fn clean_alert_words(words: Vec<String>) -> Vec<String> {
        alert_words::clean_alert_words(&words)
            .map(str::to_owned)
            .collect()
    }

    #[pyfunction]
    fn select_new_alert_words<'py>(
        existing: Vec<Bound<'py, PyString>>,
        words: &Bound<'py, PyAny>,
    ) -> PyResult<Vec<Bound<'py, PyString>>> {
        // Use the running Python version's Unicode lowercase rules while Django
        // and Rust share this operation.
        let lowercase =
            |word: &Bound<'py, PyString>| word.call_method0("lower")?.extract::<String>();
        let existing = existing
            .iter()
            .map(lowercase)
            .collect::<PyResult<HashSet<_>>>()?;
        let words = words
            .try_iter()?
            .map(|word| {
                let word = word?.cast_into::<PyString>()?;
                Ok((lowercase(&word)?, word))
            })
            .collect::<PyResult<Vec<_>>>()?;
        Ok(alert_words::select_new_alert_words(&existing, words))
    }

    #[pyfunction]
    fn delete_alert_words(
        cursor: &Bound<'_, PyAny>,
        user_id: i32,
        lowercase_words: &Bound<'_, PyList>,
    ) -> PyResult<()> {
        cursor.call_method1(
            "execute",
            (
                "DELETE FROM zulip.zerver_alertword
                 WHERE user_profile_id = %s AND lower(word) = ANY(%s::text[])",
                (user_id, lowercase_words),
            ),
        )?;
        Ok(())
    }

    #[pyfunction]
    fn delete_memcached_keys(py: Python<'_>, address: &str, keys: Vec<String>) -> PyResult<()> {
        let address: SocketAddr = address
            .parse()
            .map_err(|_| PyValueError::new_err("Expected an IP address and Memcached port"))?;
        py.detach(|| memcached::delete_keys(address, &keys))
            .map_err(|error| match error.kind() {
                io::ErrorKind::InvalidInput => PyValueError::new_err(error.to_string()),
                _ => PyConnectionError::new_err(error.to_string()),
            })
    }

    #[pyfunction]
    fn pick_colors<'py>(
        py: Python<'py>,
        used_colors: HashSet<String>,
        color_map: HashMap<i64, String>,
        recipient_ids: Vec<i64>,
    ) -> PyResult<Bound<'py, PyDict>> {
        let result = PyDict::new(py);
        for (recipient_id, color) in
            stream_color::pick_colors(&used_colors, &color_map, recipient_ids)
        {
            result.set_item(recipient_id, color)?;
        }
        Ok(result)
    }
}
