use std::collections::{BTreeSet, HashMap};

use axum::{
    body::to_bytes,
    extract::{FromRequest, Multipart, Request},
    http::header,
};

pub(super) const FORM_BODY_LIMIT: usize = 25 * 1024 * 1024;

#[derive(Clone, Copy)]
pub(super) enum FormError {
    Missing(&'static str),
    Blank(&'static str),
    TooLong(&'static str, usize),
    Malformed,
}

pub(super) struct Form {
    fields: HashMap<String, String>,
    query: HashMap<String, String>,
}

impl Form {
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

    pub fn post_value(&self, name: &str) -> Option<&str> {
        self.fields.get(name).map(String::as_str)
    }

    pub fn value(&self, name: &str) -> Option<&str> {
        self.fields
            .get(name)
            .or_else(|| self.query.get(name))
            .map(String::as_str)
    }

    pub fn ignored_parameters(&self, processed: &[&str]) -> BTreeSet<&str> {
        self.fields
            .keys()
            .chain(self.query.keys())
            .map(String::as_str)
            .filter(|name| !processed.contains(name))
            .collect()
    }

    pub fn has_query_client(&self) -> bool {
        self.query.contains_key("client")
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
