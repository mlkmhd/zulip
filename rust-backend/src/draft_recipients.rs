use std::collections::{HashMap, HashSet};

pub(crate) const DIRECT_RECIPIENTS_SQL: &str = "
    WITH RECURSIVE actor AS (
        SELECT %s::integer AS id, %s::integer AS realm_id, %s::boolean AS is_guest
    ), member_groups(id) AS (
        SELECT m.user_group_id FROM zulip.zerver_usergroupmembership AS m
        JOIN actor ON actor.id = m.user_profile_id
        JOIN zulip.zerver_userprofile AS sender ON sender.id = actor.id
        WHERE actor.is_guest AND sender.is_active
        UNION
        SELECT m.supergroup_id FROM zulip.zerver_groupgroupmembership AS m
        JOIN member_groups AS g ON g.id = m.subgroup_id
    )
    SELECT u.id, u.realm_id, u.delivery_email, u.email,
           (NOT u.is_active AND NOT u.is_mirror_dummy) OR realm.deactivated,
           CASE
               WHEN u.is_bot OR NOT actor.is_guest THEN TRUE
               WHEN EXISTS (
                   SELECT 1 FROM member_groups AS g
                   WHERE g.id = sender_realm.can_access_all_users_group_id
               ) THEN TRUE
               ELSE EXISTS (
                   SELECT 1 FROM zulip.zerver_subscription AS target
                   JOIN zulip.zerver_subscription AS own USING (recipient_id)
                   JOIN zulip.zerver_recipient AS r ON r.id = own.recipient_id
                   WHERE target.user_profile_id = u.id AND target.active
                     AND own.user_profile_id = actor.id AND own.active
                     AND (r.type = 3 OR (r.type = 2 AND target.is_user_active))
               )
           END
    FROM zulip.zerver_userprofile AS u
    JOIN zulip.zerver_realm AS realm ON realm.id = u.realm_id
    CROSS JOIN actor
    JOIN zulip.zerver_realm AS sender_realm ON sender_realm.id = actor.realm_id
    WHERE u.id = ANY(%s::bigint[])
";

pub(crate) struct DirectRecipient {
    pub id: i32,
    pub realm_id: i32,
    pub delivery_email: String,
    pub email: String,
    pub deactivated: bool,
    pub accessible: bool,
}

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum DirectRecipientError<'a> {
    InvalidId(i64),
    Inaccessible,
    Deactivated(&'a str),
    CrossRealm,
}

pub(crate) fn validate_direct_recipients<'a>(
    user_id: i32,
    realm_id: i32,
    email: &str,
    ids: &[i64],
    cross_realm_bot_emails: &HashSet<String>,
    recipients: &'a [DirectRecipient],
) -> Result<(), DirectRecipientError<'a>> {
    let is_cross_realm_bot = |email: &str| cross_realm_bot_emails.contains(&email.to_lowercase());
    let by_id: HashMap<_, _> = recipients
        .iter()
        .map(|row| (i64::from(row.id), row))
        .collect();
    let mut resolved = Vec::with_capacity(ids.len());
    for id in ids {
        let row = by_id
            .get(id)
            .filter(|row| row.realm_id == realm_id || is_cross_realm_bot(&row.delivery_email))
            .ok_or(DirectRecipientError::InvalidId(*id))?;
        resolved.push(*row);
    }

    if ids != [i64::from(user_id)] && resolved.iter().any(|row| !row.accessible) {
        return Err(DirectRecipientError::Inaccessible);
    }

    let mut realms = HashSet::new();
    if !is_cross_realm_bot(email) {
        realms.insert(realm_id);
    }
    for row in resolved {
        if row.deactivated {
            return Err(DirectRecipientError::Deactivated(&row.email));
        }
        if !is_cross_realm_bot(&row.email) {
            realms.insert(row.realm_id);
        }
    }
    if realms.len() > 1 {
        return Err(DirectRecipientError::CrossRealm);
    }
    Ok(())
}
