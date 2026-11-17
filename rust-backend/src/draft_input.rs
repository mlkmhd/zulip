use jiter::JsonValue;
use pyo3::prelude::*;

#[derive(Debug, PartialEq)]
enum Timestamp {
    Integer(i64),
    Float(f64),
}

impl<'py> IntoPyObject<'py> for Timestamp {
    type Target = PyAny;
    type Output = Bound<'py, PyAny>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> PyResult<Self::Output> {
        match self {
            Self::Integer(value) => Ok(value.into_pyobject(py)?.into_any()),
            Self::Float(value) => Ok(value.into_pyobject(py)?.into_any()),
        }
    }
}

#[derive(Debug, PartialEq, pyo3::IntoPyObject)]
pub(crate) struct DraftInput {
    r#type: &'static str,
    to: Vec<i64>,
    topic: String,
    content: String,
    timestamp: Option<Timestamp>,
}

impl DraftInput {
    fn from_json(value: &JsonValue<'_>) -> Option<Self> {
        let JsonValue::Object(fields) = value else {
            return None;
        };
        let mut message_type = None;
        let mut to = None;
        let mut topic = None;
        let mut content = None;
        let mut timestamp = None;
        for (key, value) in fields.iter() {
            match (key.as_ref(), value) {
                ("type", JsonValue::Str(value)) if message_type.is_none() => {
                    message_type = Some(match value.as_ref() {
                        "private" => "private",
                        "stream" => "stream",
                        "" => "",
                        _ => return None,
                    });
                }
                ("to", JsonValue::Array(values)) if to.is_none() => {
                    to = Some(
                        values
                            .iter()
                            .map(|value| match value {
                                JsonValue::Int(id) => Some(*id),
                                _ => None,
                            })
                            .collect::<Option<Vec<_>>>()?,
                    );
                }
                ("topic", JsonValue::Str(value)) if topic.is_none() => {
                    topic = Some(value.to_string());
                }
                ("content", JsonValue::Str(value)) if content.is_none() => {
                    let trimmed = value.trim();
                    if trimmed.is_empty() {
                        return None;
                    }
                    content = Some(trimmed.to_owned());
                }
                ("timestamp", value) if timestamp.is_none() => {
                    timestamp = Some(match value {
                        JsonValue::Null => None,
                        JsonValue::Int(value) => Some(Timestamp::Integer(*value)),
                        JsonValue::Float(value) if value.is_finite() => {
                            Some(Timestamp::Float(*value))
                        }
                        _ => return None,
                    });
                }
                _ => return None,
            }
        }
        Some(Self {
            r#type: message_type?,
            to: to?,
            topic: topic?,
            content: content?,
            timestamp: timestamp.flatten(),
        })
    }
}

pub(crate) fn parse_draft(value: &str) -> Option<DraftInput> {
    DraftInput::from_json(&JsonValue::parse(value.as_bytes(), false).ok()?)
}

pub(crate) fn parse_draft_batch(value: &str) -> Option<Vec<DraftInput>> {
    let JsonValue::Array(drafts) = JsonValue::parse(value.as_bytes(), false).ok()? else {
        return None;
    };
    drafts.iter().map(DraftInput::from_json).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_recipient_order_and_timestamp_types() {
        let drafts = parse_draft_batch(
            r#"[
                {"type":"private","to":[3,2,3],"topic":" old ","content":"\u2003Unsent ","timestamp":42},
                {"type":"stream","to":[1],"topic":"","content":"Pending","timestamp":42.0},
                {"type":"","to":[],"topic":"","content":"Undirected","timestamp":null}
            ]"#,
        )
        .unwrap();
        assert_eq!(
            drafts[0],
            DraftInput {
                r#type: "private",
                to: vec![3, 2, 3],
                topic: " old ".into(),
                content: "Unsent".into(),
                timestamp: Some(Timestamp::Integer(42)),
            }
        );
        assert_eq!(drafts[1].timestamp, Some(Timestamp::Float(42.0)));
        assert_eq!(drafts[2].timestamp, None);
        assert_eq!(parse_draft_batch("[]"), Some(Vec::new()));
    }

    #[test]
    fn delegates_invalid_or_ambiguous_batches_to_pydantic() {
        let valid = r#"{"type":"","to":[],"topic":"","content":"Unsent"}"#;
        for unsupported in [
            r#"{"type":"","to":["2"],"topic":"","content":"Unsent"}"#,
            r#"{"type":"","to":[true],"topic":"","content":"Unsent"}"#,
            r#"{"type":"","to":[],"topic":"","content":"  "}"#,
            r#"{"type":"","to":[],"content":"Unsent"}"#,
            r#"{"type":"","to":[],"topic":"","content":"Unsent","extra":true}"#,
            r#"{"type":"","to":[],"topic":"","content":"Unsent","timestamp":null,"timestamp":1}"#,
            r#"{"type":"","to":[],"topic":"","content":"Unsent","timestamp":1e999}"#,
            r#"{"type":"","to":[],"topic":"","content":"Unsent","timestamp":9223372036854775808}"#,
            "null",
        ] {
            assert!(parse_draft(unsupported).is_none(), "{unsupported}");
            assert!(parse_draft_batch(&format!("[{valid},{unsupported}]")).is_none());
        }
    }
}
