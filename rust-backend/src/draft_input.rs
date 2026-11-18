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

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum Location {
    Index(usize),
    Field(String),
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

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ParseError {
    Schema(&'static str, Vec<Location>),
    Unsupported,
}

impl ParseError {
    fn at_field(kind: &'static str, field: &str) -> Self {
        Self::Schema(kind, vec![Location::Field(field.to_owned())])
    }
}

impl DraftInput {
    fn from_json(value: &JsonValue<'_>) -> Result<Self, ParseError> {
        let JsonValue::Object(pairs) = value else {
            return Err(ParseError::Schema("model_type", Vec::new()));
        };
        let mut fields = std::collections::HashMap::with_capacity(pairs.len());
        for (key, value) in pairs.iter() {
            if fields.insert(key.as_ref(), value).is_some() {
                return Err(ParseError::Unsupported);
            }
        }
        if let Some((field, _)) = pairs.iter().find(|(field, _)| {
            !matches!(
                field.as_ref(),
                "type" | "to" | "topic" | "content" | "timestamp"
            )
        }) {
            return Err(ParseError::at_field("extra_forbidden", field));
        }
        let required_field = |field| {
            fields
                .get(field)
                .ok_or_else(|| ParseError::at_field("missing", field))
        };

        // Match the declaration order of DraftData, regardless of JSON key order.
        let message_type = match required_field("type")? {
            JsonValue::Str(value) => match value.as_ref() {
                "private" => "private",
                "stream" => "stream",
                "" => "",
                _ => return Err(ParseError::at_field("literal_error", "type")),
            },
            _ => return Err(ParseError::at_field("literal_error", "type")),
        };

        let JsonValue::Array(recipients) = required_field("to")? else {
            return Err(ParseError::at_field("list_type", "to"));
        };
        let mut to = Vec::with_capacity(recipients.len());
        for (index, value) in recipients.iter().enumerate() {
            match value {
                JsonValue::Int(id) => to.push(*id),
                JsonValue::BigInt(_) => return Err(ParseError::Unsupported),
                _ => {
                    return Err(ParseError::Schema(
                        "int_type",
                        vec![Location::Field("to".into()), Location::Index(index)],
                    ));
                }
            }
        }

        let JsonValue::Str(topic) = required_field("topic")? else {
            return Err(ParseError::at_field("string_type", "topic"));
        };
        let JsonValue::Str(content) = required_field("content")? else {
            return Err(ParseError::at_field("string_type", "content"));
        };
        let content = content.trim();
        if content.is_empty() {
            return Err(ParseError::at_field("string_too_short", "content"));
        }

        let timestamp = match fields.get("timestamp") {
            None | Some(JsonValue::Null) => None,
            Some(JsonValue::Int(value)) => Some(Timestamp::Integer(*value)),
            Some(JsonValue::Float(value)) if value.is_finite() => Some(Timestamp::Float(*value)),
            Some(JsonValue::BigInt(_) | JsonValue::Float(_)) => {
                return Err(ParseError::Unsupported);
            }
            _ => {
                return Err(ParseError::Schema(
                    "int_type",
                    vec![
                        Location::Field("timestamp".into()),
                        Location::Field("int".into()),
                    ],
                ));
            }
        };
        Ok(Self {
            r#type: message_type,
            to,
            topic: topic.to_string(),
            content: content.into(),
            timestamp,
        })
    }
}

pub(crate) fn parse_draft(value: &str) -> Result<DraftInput, ParseError> {
    DraftInput::from_json(
        &JsonValue::parse(value.as_bytes(), false).map_err(|_| ParseError::Unsupported)?,
    )
}

pub(crate) fn parse_draft_batch(value: &str) -> Result<Vec<DraftInput>, ParseError> {
    let JsonValue::Array(drafts) =
        JsonValue::parse(value.as_bytes(), false).map_err(|_| ParseError::Unsupported)?
    else {
        return Err(ParseError::Schema("list_type", Vec::new()));
    };
    drafts
        .iter()
        .enumerate()
        .map(|(index, draft)| {
            DraftInput::from_json(draft).map_err(|error| match error {
                ParseError::Schema(kind, mut location) => {
                    location.insert(0, Location::Index(index));
                    ParseError::Schema(kind, location)
                }
                ParseError::Unsupported => error,
            })
        })
        .collect()
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
        assert_eq!(parse_draft_batch("[]"), Ok(Vec::new()));
    }

    #[test]
    fn reports_the_first_schema_error_in_declaration_order() {
        let input = r#"[{"type":"","to":[],"topic":"","content":"Unsent"},
                        {"content":false,"topic":"","type":"","to":[3,"2"]}]"#;
        assert_eq!(
            parse_draft_batch(input),
            Err(ParseError::Schema(
                "int_type",
                vec![
                    Location::Index(1),
                    Location::Field("to".into()),
                    Location::Index(1),
                ]
            ))
        );
        assert_eq!(
            parse_draft("null"),
            Err(ParseError::Schema("model_type", Vec::new()))
        );
        assert_eq!(
            parse_draft(r#"{"content":false,"extra":true}"#),
            Err(ParseError::at_field("extra_forbidden", "extra"))
        );
    }

    #[test]
    fn delegates_ambiguous_or_unsupported_values_to_pydantic() {
        for unsupported in [
            r#"{"type":"","to":[],"topic":"","content":"Unsent","timestamp":null,"timestamp":1}"#,
            r#"{"type":"","to":[],"topic":"","content":"Unsent","timestamp":1e999}"#,
            r#"{"type":"","to":[],"topic":"","content":"Unsent","timestamp":9223372036854775808}"#,
            r#"{"type":"","to":[9223372036854775808],"topic":"","content":"Unsent"}"#,
            "",
        ] {
            assert_eq!(
                parse_draft(unsupported),
                Err(ParseError::Unsupported),
                "{unsupported}"
            );
        }
    }
}
