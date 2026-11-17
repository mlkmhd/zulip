use std::collections::{HashMap, HashSet};
use std::{io, net::SocketAddr};

use pyo3::exceptions::{PyConnectionError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyString};

use crate::{alert_words, bot_storage, draft_text, memcached, stream_color, user_status};

#[pymodule]
mod _zulip_backend {
    use super::*;

    #[pyfunction]
    fn storage_size_difference(entries: Vec<(String, String, u64)>) -> i128 {
        bot_storage::storage_size_difference(&entries)
    }

    #[pyfunction]
    fn initialize_direct_message_group(
        cursor: &Bound<'_, PyAny>,
        group_id: i64,
        recipient_type: i16,
        member_ids: Vec<i32>,
        default_color: &str,
    ) -> PyResult<i32> {
        cursor.call_method1(
            "execute",
            (
                "WITH recipient AS (
                     INSERT INTO zulip.zerver_recipient (type_id, type)
                     VALUES (%s, %s) RETURNING id, type_id
                 ), initialized_group AS (
                     UPDATE zulip.zerver_huddle AS h SET recipient_id = recipient.id
                     FROM recipient WHERE h.id = recipient.type_id
                     RETURNING h.recipient_id
                 ), subscriptions AS (
                     INSERT INTO zulip.zerver_subscription
                         (recipient_id, user_profile_id, active, is_user_active,
                          is_muted, color, pin_to_top)
                     SELECT recipient_id, u.id, TRUE, u.is_active, FALSE, %s, FALSE
                     FROM initialized_group CROSS JOIN zulip.zerver_userprofile AS u
                     WHERE u.id = ANY(%s::integer[])
                 )
                 SELECT recipient_id FROM initialized_group",
                (group_id, recipient_type, default_color, member_ids),
            ),
        )?;
        let (recipient_id,) = cursor.call_method0("fetchone")?.extract()?;
        Ok(recipient_id)
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
                "WITH drafts AS MATERIALIZED (
                     SELECT nextval(pg_get_serial_sequence('zulip.zerver_draft', 'id')) AS id,
                            recipient_id, topic, content, last_edit_time, position
                     FROM unnest(%s::integer[], %s::text[], %s::text[], %s::timestamptz[])
                          WITH ORDINALITY AS input(recipient_id, topic, content, last_edit_time, position)
                     ORDER BY position
                 ), inserted AS (
                     INSERT INTO zulip.zerver_draft
                         (id, user_profile_id, recipient_id, topic, content, last_edit_time)
                     SELECT id, %s, recipient_id, topic, content, last_edit_time FROM drafts
                     RETURNING id
                 )
                 SELECT inserted.id FROM inserted JOIN drafts USING (id)
                 ORDER BY drafts.position",
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
