use std::sync::LazyLock;

use axum::http::HeaderValue;
use regex::{Captures, Regex};

static USER_AGENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^(?P<name>[^/ ]*[^0-9/(]*)(?:/(?P<version>[^/ ]*))?(?:[ /].*)?$").unwrap()
});

pub(super) fn decode(value: &HeaderValue) -> String {
    // WSGI exposes HTTP header bytes as ISO-8859-1 strings.
    value.as_bytes().iter().copied().map(char::from).collect()
}

pub(super) fn parse(value: &str) -> Option<Captures<'_>> {
    USER_AGENT.captures(value)
}
