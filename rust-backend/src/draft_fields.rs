#[cfg(any(feature = "python", feature = "http"))]
pub(crate) const INSERT_DRAFTS_SQL: &str = "
WITH drafts AS MATERIALIZED (
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
ORDER BY drafts.position
";

pub(crate) enum Recipients {
    Unset,
    Channel(i32),
    Direct(Vec<i32>),
}

#[cfg_attr(feature = "http", derive(serde::Serialize))]
#[cfg_attr(feature = "python", derive(pyo3::IntoPyObject))]
pub(crate) struct DraftFields {
    id: i64,
    r#type: &'static str,
    to: Vec<i32>,
    topic: String,
    content: String,
    timestamp: i64,
}

impl DraftFields {
    pub(crate) fn new(
        id: i64,
        owner_id: i32,
        recipients: Recipients,
        topic: String,
        content: String,
        timestamp: i64,
    ) -> Self {
        let (recipient_type, to) = match recipients {
            Recipients::Unset => ("", Vec::new()),
            Recipients::Channel(channel_id) => ("stream", vec![channel_id]),
            Recipients::Direct(mut members) => {
                if members.len() > 1 {
                    members.retain(|&id| id != owner_id);
                }
                ("private", members)
            }
        };
        Self {
            id,
            r#type: recipient_type,
            to,
            topic,
            content,
            timestamp,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{DraftFields, Recipients};

    #[test]
    fn direct_message_recipients_exclude_the_owner_except_in_self_messages() {
        for (members, expected) in [
            (vec![], vec![]),
            (vec![4], vec![4]),
            (vec![3, 4, 5], vec![3, 5]),
        ] {
            let fields = DraftFields::new(
                7,
                4,
                Recipients::Direct(members),
                String::new(),
                String::from("Unsent"),
                42,
            );
            assert_eq!(fields.r#type, "private");
            assert_eq!(fields.to, expected);
        }
    }
}
