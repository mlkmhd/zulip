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
