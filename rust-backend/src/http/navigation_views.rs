use serde::Serialize;
use serde_json::{Value, json};
use tokio_postgres::{Client, Error};

use super::form::{Form, FormError};

pub(super) const PATH: &str = "/api/v1/navigation_views";
pub(super) const SESSION_PATH: &str = "/json/navigation_views";
const AUDIT_EVENT_CREATED: i16 = 850;
const LOCK_NAMESPACE: i32 = i32::from_be_bytes(*b"NAVV");
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
            (true, Some(_)) => Err("Built-in views cannot have a custom name."),
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
        // Only fragments have a unique index. Serialize Rust writes per user
        // before checking names so multiple service instances agree.
        transaction
            .query_one(
                "SELECT pg_advisory_xact_lock($1, $2)",
                &[&LOCK_NAMESPACE, &user_id],
            )
            .await?;
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
