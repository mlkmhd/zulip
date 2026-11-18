use sha1_smol::Sha1;

#[cfg(any(feature = "http", feature = "python"))]
pub(crate) const FIND_GROUP_SQL: &str = "SELECT id, recipient_id FROM zulip.zerver_huddle
WHERE huddle_hash = %s AND group_size = %s";

#[cfg(any(feature = "http", feature = "python"))]
pub(crate) const INSERT_GROUP_SQL: &str =
    "INSERT INTO zulip.zerver_huddle (huddle_hash, group_size)
VALUES (%s, %s) ON CONFLICT (huddle_hash) DO NOTHING RETURNING id";

#[cfg(any(feature = "http", feature = "python"))]
pub(crate) const INITIALIZE_GROUP_SQL: &str = "WITH recipient AS (
    INSERT INTO zulip.zerver_recipient (type_id, type)
    VALUES (%s::bigint, %s::smallint) RETURNING id, type_id
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
SELECT recipient_id FROM initialized_group";

pub fn member_hash(member_ids: &[i32]) -> String {
    let mut member_ids = member_ids.to_vec();
    member_ids.sort_unstable();
    member_ids.dedup();
    let key = member_ids
        .iter()
        .map(i32::to_string)
        .collect::<Vec<_>>()
        .join(",");
    Sha1::from(key).digest().to_string()
}

#[cfg(test)]
mod tests {
    use super::member_hash;

    #[test]
    fn matches_the_existing_hash_of_sorted_distinct_ids() {
        assert_eq!(member_hash(&[10, 2, 10, 1]), member_hash(&[1, 2, 10]));
        assert_eq!(
            member_hash(&[1, 2, 10]),
            "3faeb0fe3f2bdb01b06dc219c18a654f13db3c31"
        );
    }
}
