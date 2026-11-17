use std::collections::{BTreeSet, HashMap};

use axum::{
    body::to_bytes,
    extract::{FromRequest, Multipart, Request},
    http::header,
};
use serde::Serialize;
use serde_json::{Value, json};
use tokio_postgres::{Client, Error, Row};

pub(super) const PATH: &str = "/api/v1/saved_snippets";
pub(super) const SESSION_PATH: &str = "/json/saved_snippets";
pub(super) const DETAIL_PATH: &str = "/api/v1/saved_snippets/{id}";
pub(super) const NOTIFICATION_QUEUE: &str = "notify_tornado";
pub(super) const FORM_BODY_LIMIT: usize = 25 * 1024 * 1024;
const AUDIT_EVENT_CREATED: i16 = 800;
const MAX_TITLE_LENGTH: usize = 60;

#[derive(Serialize)]
pub(super) struct SavedSnippet {
    pub id: i64,
    title: String,
    content: String,
    date_created: i64,
}

impl SavedSnippet {
    fn from_row(row: &Row) -> Result<Self, Error> {
        Ok(Self {
            id: row.try_get("id")?,
            title: row.try_get("title")?,
            content: row.try_get("content")?,
            date_created: row.try_get("date_created")?,
        })
    }
}

pub(super) enum SnippetEvent {
    Added(SavedSnippet),
    Updated(SavedSnippet),
    Removed(i64),
}

impl SnippetEvent {
    pub fn snippet_id(&self) -> i64 {
        match self {
            Self::Added(snippet) | Self::Updated(snippet) => snippet.id,
            Self::Removed(id) => *id,
        }
    }

    pub fn payload(&self) -> Value {
        match self {
            Self::Added(snippet) => {
                json!({"type": "saved_snippets", "op": "add", "saved_snippet": snippet})
            }
            Self::Updated(snippet) => {
                json!({"type": "saved_snippets", "op": "update", "saved_snippet": snippet})
            }
            Self::Removed(id) => {
                json!({"type": "saved_snippets", "op": "remove", "saved_snippet_id": id})
            }
        }
    }
}

pub(super) async fn list(database: &Client, user_id: i32) -> Result<Vec<SavedSnippet>, Error> {
    database
        .query(
            "SELECT id, title, content,
                    trunc(extract(epoch FROM date_created)::double precision)::bigint AS date_created
             FROM zulip.zerver_savedsnippet WHERE user_profile_id = $1",
            &[&user_id],
        )
        .await?
        .iter()
        .map(SavedSnippet::from_row)
        .collect()
}

#[derive(Clone, Copy)]
pub(super) enum FormError {
    Missing(&'static str),
    Blank(&'static str),
    TooLong(&'static str, usize),
    Malformed,
}

pub(super) struct SnippetForm {
    fields: HashMap<String, String>,
    query: HashMap<String, String>,
}

impl SnippetForm {
    pub async fn parse(request: Request) -> Result<Self, FormError> {
        let query = parse_urlencoded(request.uri().query().unwrap_or("").as_bytes())?;
        let content_type = request
            .headers()
            .get(header::CONTENT_TYPE)
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.split(';').next())
            .unwrap_or("")
            .trim();
        let fields = if content_type.eq_ignore_ascii_case("application/x-www-form-urlencoded") {
            let bytes = to_bytes(request.into_body(), FORM_BODY_LIMIT)
                .await
                .map_err(|_| FormError::Malformed)?;
            parse_urlencoded(&bytes)?
        } else if content_type.eq_ignore_ascii_case("multipart/form-data") {
            let mut form = Multipart::from_request(request, &())
                .await
                .map_err(|_| FormError::Malformed)?;
            let mut fields = HashMap::new();
            let mut count = 0;
            while let Some(field) = form.next_field().await.map_err(|_| FormError::Malformed)? {
                count += 1;
                if count > 1000 {
                    return Err(FormError::Malformed);
                }
                if let Some(name) = field.name().filter(|_| field.file_name().is_none()) {
                    let name = name.to_owned();
                    let value = field.text().await.map_err(|_| FormError::Malformed)?;
                    fields.insert(name, value);
                }
            }
            fields
        } else {
            HashMap::new()
        };
        Ok(Self { fields, query })
    }

    pub fn method_override(&self) -> Option<&str> {
        self.fields.get("method").map(String::as_str)
    }

    pub fn value(&self, name: &str) -> Option<&str> {
        self.fields
            .get(name)
            .or_else(|| self.query.get(name))
            .map(String::as_str)
    }

    fn optional(&self, name: &'static str, max: usize) -> Result<Option<&str>, FormError> {
        let Some(value) = self.value(name).map(str::trim) else {
            return Ok(None);
        };
        if value.is_empty() {
            return Err(FormError::Blank(name));
        }
        if value.chars().count() > max {
            return Err(FormError::TooLong(name, max));
        }
        Ok(Some(value))
    }

    pub fn create_values(&self, max_content_length: usize) -> Result<(&str, &str), FormError> {
        let content = self
            .optional("content", max_content_length)?
            .ok_or(FormError::Missing("content"))?;
        let title = self
            .optional("title", MAX_TITLE_LENGTH)?
            .ok_or(FormError::Missing("title"))?;
        // Django's create view also calls Python str.strip() after Pydantic validation.
        let strip = |ch: char| ch.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&ch);
        Ok((title.trim_matches(strip), content.trim_matches(strip)))
    }

    pub fn edit_values(
        &self,
        max_content_length: usize,
    ) -> Result<(Option<&str>, Option<&str>), FormError> {
        let content = self.optional("content", max_content_length)?;
        let title = self.optional("title", MAX_TITLE_LENGTH)?;
        Ok((title, content))
    }

    pub fn ignored_parameters(&self, is_edit: bool) -> BTreeSet<&str> {
        // Django identifies the client before it parses the PATCH body.
        let client_processed = !is_edit || self.query.contains_key("client");
        self.fields
            .keys()
            .chain(self.query.keys())
            .map(String::as_str)
            .filter(|name| match *name {
                "title" | "content" => false,
                "client" => !client_processed,
                _ => true,
            })
            .collect()
    }
}

fn parse_urlencoded(input: &[u8]) -> Result<HashMap<String, String>, FormError> {
    let mut fields = HashMap::new();
    for (index, (key, value)) in form_urlencoded::parse(input).enumerate() {
        if index >= 1000 {
            return Err(FormError::Malformed);
        }
        fields.insert(key.into_owned(), value.into_owned());
    }
    Ok(fields)
}

pub(super) async fn create(
    database: &Client,
    user_id: i32,
    realm_id: i32,
    title: &str,
    content: &str,
) -> Result<SavedSnippet, Error> {
    // One implicit transaction covers both inserts. query_one drains the response
    // through ReadyForQuery before the caller can notify Tornado.
    let row = database.query_one(
        "WITH snippet AS (
            INSERT INTO zulip.zerver_savedsnippet (user_profile_id, realm_id, title, content, date_created)
            VALUES ($1, $2, $3, $4, clock_timestamp()) RETURNING id, title, content, date_created
         ), audit AS (
            INSERT INTO zulip.zerver_realmauditlog
                (realm_id, acting_user_id, modified_user_id, event_type, event_time, extra_data, backfilled)
            SELECT $2, $1, $1, $5, clock_timestamp(), jsonb_build_object('saved_snippet_id', id), false
            FROM snippet RETURNING id
         )
         SELECT snippet.id, title, content,
                trunc(extract(epoch FROM date_created)::double precision)::bigint AS date_created
         FROM snippet CROSS JOIN audit",
        &[&user_id, &realm_id, &title, &content, &AUDIT_EVENT_CREATED],
    ).await?;
    SavedSnippet::from_row(&row)
}

pub(super) async fn edit(
    database: &Client,
    user_id: i32,
    snippet_id: i64,
    title: Option<&str>,
    content: Option<&str>,
) -> Result<Option<SavedSnippet>, Error> {
    // Resolve omitted fields in PostgreSQL so concurrent edits of different
    // fields do not overwrite each other's changes.
    let row = database.query_opt(
        "UPDATE zulip.zerver_savedsnippet
         SET title = COALESCE($3, title), content = COALESCE($4, content)
         WHERE id = $1 AND user_profile_id = $2
         RETURNING id, title, content,
                   trunc(extract(epoch FROM date_created)::double precision)::bigint AS date_created",
        &[&snippet_id, &user_id, &title, &content],
    ).await?;
    row.as_ref().map(SavedSnippet::from_row).transpose()
}

pub(super) async fn delete(
    database: &Client,
    user_id: i32,
    snippet_id: i64,
) -> Result<bool, Error> {
    // Only the request that commits the deletion may publish a removal event.
    let deleted = database
        .execute(
            "DELETE FROM zulip.zerver_savedsnippet WHERE id = $1 AND user_profile_id = $2",
            &[&snippet_id, &user_id],
        )
        .await?;
    Ok(deleted == 1)
}
