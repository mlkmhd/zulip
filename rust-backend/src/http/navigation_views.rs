use serde::Serialize;
use serde_json::{Value, json};
use tokio_postgres::{Client, Error, Transaction};

use super::form::{Form, FormError};

pub(super) const PATH: &str = "/api/v1/navigation_views";
pub(super) const SESSION_PATH: &str = "/json/navigation_views";
pub(super) const DETAIL_PATH: &str = "/api/v1/navigation_views/{*fragment}";
pub(super) const SESSION_DETAIL_PATH: &str = "/json/navigation_views/{*fragment}";
const AUDIT_EVENT_CREATED: i16 = 850;
const AUDIT_EVENT_UPDATED: i16 = 851;
const AUDIT_EVENT_DELETED: i16 = 852;
const LOCK_NAMESPACE: i32 = i32::from_be_bytes(*b"NAVV");
const BUILT_IN_NAME_ERROR: &str = "Built-in views cannot have a custom name.";
const BUILT_IN_FRAGMENTS: &[&str] = &[
    "inbox",
    "recent",
    "feed",
    "drafts",
    "narrow/has/reaction/sender/me",
    "narrow/is/mentioned",
    "narrow/is/starred",
    "scheduled",
    "reminders",
];

#[derive(Serialize)]
pub(super) struct NavigationView<T> {
    fragment: T,
    is_pinned: bool,
    name: Option<T>,
}

pub(super) async fn list(
    database: &Client,
    user_id: i32,
) -> Result<Vec<NavigationView<String>>, Error> {
    database
        .query(
            "SELECT fragment, is_pinned, name FROM zulip.zerver_navigationview WHERE user_id = $1",
            &[&user_id],
        )
        .await?
        .iter()
        .map(|row| {
            Ok(NavigationView {
                fragment: row.try_get("fragment")?,
                is_pinned: row.try_get("is_pinned")?,
                name: row.try_get("name")?,
            })
        })
        .collect()
}

impl<'a> NavigationView<&'a str> {
    pub(super) fn from_form(form: &'a Form) -> Result<Self, FormError> {
        let fragment = form
            .nonblank("fragment")?
            .ok_or(FormError::Missing("fragment"))?;
        let is_pinned = form
            .json_bool("is_pinned")?
            .ok_or(FormError::Missing("is_pinned"))?;
        let name = form.nonblank("name")?;
        Ok(Self {
            fragment,
            is_pinned,
            name,
        })
    }

    pub(super) fn validate_name(&self) -> Result<(), &'static str> {
        match (BUILT_IN_FRAGMENTS.contains(&self.fragment), self.name) {
            (true, Some(_)) => Err(BUILT_IN_NAME_ERROR),
            (false, None) => Err("Custom views must have a valid name."),
            _ => Ok(()),
        }
    }

    pub(super) async fn create(
        &self,
        database: &mut Client,
        user_id: i32,
        realm_id: i32,
    ) -> Result<bool, Error> {
        let transaction = database.transaction().await?;
        lock_user_views(&transaction, user_id).await?;
        let inserted = transaction
            .execute(
                "WITH view AS (
                    INSERT INTO zulip.zerver_navigationview (user_id, fragment, is_pinned, name)
                    SELECT $1, $2, $3, $4
                    WHERE NOT EXISTS (
                        SELECT FROM zulip.zerver_navigationview WHERE user_id = $1 AND name = $4
                    )
                    ON CONFLICT (user_id, fragment) DO NOTHING
                    RETURNING fragment
                 )
                 INSERT INTO zulip.zerver_realmauditlog
                    (realm_id, acting_user_id, modified_user_id, event_type, event_time, extra_data, backfilled)
                 SELECT $5, $1, $1, $6, clock_timestamp(), jsonb_build_object('fragment', fragment), false
                 FROM view",
                &[&user_id, &self.fragment, &self.is_pinned, &self.name, &realm_id, &AUDIT_EVENT_CREATED],
            )
            .await?;
        transaction.commit().await?;
        Ok(inserted == 1)
    }

    pub(super) fn add_event(&self) -> Value {
        json!({"type": "navigation_view", "op": "add", "navigation_view": self})
    }
}

async fn lock_user_views(transaction: &Transaction<'_>, user_id: i32) -> Result<(), Error> {
    // Only fragments have a unique index. Serialize Rust writes per user
    // before checking names so multiple service instances agree.
    transaction
        .query_one(
            "SELECT pg_advisory_xact_lock($1, $2)",
            &[&LOCK_NAMESPACE, &user_id],
        )
        .await?;
    Ok(())
}

#[derive(Serialize)]
pub(super) struct NavigationViewUpdate<'a> {
    #[serde(skip_serializing_if = "Option::is_none")]
    is_pinned: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    name: Option<&'a str>,
}

pub(super) enum UpdateOutcome {
    Updated,
    DuplicateName,
    Missing,
}

impl<'a> NavigationViewUpdate<'a> {
    pub(super) fn from_form(form: &'a Form) -> Result<Self, FormError> {
        Ok(Self {
            is_pinned: form.json_bool("is_pinned")?,
            name: form.nonblank("name")?,
        })
    }

    pub(super) fn validate_name(&self, fragment: &str) -> Result<(), &'static str> {
        if self.name.is_some() && BUILT_IN_FRAGMENTS.contains(&fragment) {
            return Err(BUILT_IN_NAME_ERROR);
        }
        Ok(())
    }

    pub(super) async fn apply(
        &self,
        database: &mut Client,
        user_id: i32,
        realm_id: i32,
        fragment: &str,
    ) -> Result<UpdateOutcome, Error> {
        let transaction = database.transaction().await?;
        lock_user_views(&transaction, user_id).await?;
        if let Some(name) = self.name {
            let duplicate: bool = transaction
                .query_one(
                    "SELECT EXISTS (
                        SELECT FROM zulip.zerver_navigationview WHERE user_id = $1 AND name = $2
                    )",
                    &[&user_id, &name],
                )
                .await?
                .try_get(0)?;
            if duplicate {
                return Ok(UpdateOutcome::DuplicateName);
            }
        }
        let current = transaction
            .query_opt(
                "SELECT name, is_pinned FROM zulip.zerver_navigationview
                 WHERE user_id = $1 AND fragment = $2 FOR UPDATE",
                &[&user_id, &fragment],
            )
            .await?;
        let Some(current) = current else {
            return Ok(UpdateOutcome::Missing);
        };
        let mut audits = Vec::new();
        if let Some(name) = self.name {
            let previous: Option<&str> = current.try_get("name")?;
            audits
                .push(json!({"fragment": fragment, "property": "name", "1": previous, "2": name}));
        }
        if let Some(is_pinned) = self.is_pinned {
            let previous: bool = current.try_get("is_pinned")?;
            audits.push(json!({
                "fragment": fragment, "property": "is_pinned", "1": previous, "2": is_pinned,
            }));
        }
        if !audits.is_empty() {
            transaction
                .execute(
                    "UPDATE zulip.zerver_navigationview
                     SET name = COALESCE($3, name), is_pinned = COALESCE($4, is_pinned)
                     WHERE user_id = $1 AND fragment = $2",
                    &[&user_id, &fragment, &self.name, &self.is_pinned],
                )
                .await?;
            transaction
                .execute(
                    "INSERT INTO zulip.zerver_realmauditlog
                        (realm_id, acting_user_id, modified_user_id, event_type, event_time, extra_data, backfilled)
                     SELECT $2, $1, $1, $3, statement_timestamp(), entry, false
                     FROM jsonb_array_elements($4::text::jsonb) WITH ORDINALITY AS audit(entry, position)
                     ORDER BY position",
                    &[&user_id, &realm_id, &AUDIT_EVENT_UPDATED, &json!(audits).to_string()],
                )
                .await?;
        }
        transaction.commit().await?;
        Ok(UpdateOutcome::Updated)
    }

    pub(super) fn event(&self, fragment: &str) -> Value {
        json!({"type": "navigation_view", "op": "update", "fragment": fragment, "data": self})
    }
}

pub(super) async fn delete(
    database: &mut Client,
    user_id: i32,
    realm_id: i32,
    fragment: &str,
) -> Result<bool, Error> {
    let transaction = database.transaction().await?;
    let deleted = transaction
        .execute(
            "WITH view AS (
                DELETE FROM zulip.zerver_navigationview WHERE user_id = $1 AND fragment = $2
                RETURNING fragment
             )
             INSERT INTO zulip.zerver_realmauditlog
                (realm_id, acting_user_id, modified_user_id, event_type, event_time, extra_data, backfilled)
             SELECT $3, $1, $1, $4, clock_timestamp(), jsonb_build_object('fragment', fragment), false
             FROM view",
            &[&user_id, &fragment, &realm_id, &AUDIT_EVENT_DELETED],
        )
        .await?;
    transaction.commit().await?;
    Ok(deleted == 1)
}
