use std::{cmp::Ordering, sync::LazyLock};

use axum::{
    extract::State,
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Response},
};
use regex::Regex;

use super::{locale::Locales, user_agent};

static ANDROID: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)\bAndroid\b").unwrap());
static NUMERALS: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^([0-9]+(?:\.[0-9]+)*)(.*)").unwrap());

// Android 16.2.96 fixed broken notifications when the server removes an old notification.
fn older_android_version(version: &str) -> bool {
    let Some(captures) = NUMERALS.captures(version) else {
        return false;
    };
    let mut parts = captures[1].split('.');
    for minimum in ["16", "2", "96"] {
        let Some(part) = parts.next() else {
            // Decoration makes a comparison with missing numeric parts ambiguous.
            return captures[2].is_empty();
        };
        let part = part.trim_start_matches('0');
        match part.len().cmp(&minimum.len()).then(part.cmp(minimum)) {
            Ordering::Less => return true,
            Ordering::Greater => return false,
            Ordering::Equal => {}
        }
    }
    false
}

pub(super) async fn get(State(locales): State<Locales>, headers: HeaderMap) -> Response {
    let language = Locales::for_request(&headers);
    let response = match headers.get(header::USER_AGENT) {
        None => error(locales.gettext(language, "User-Agent header missing from request")),
        Some(value) => {
            let user_agent = user_agent::decode(value);
            let Some(parts) = user_agent::parse(&user_agent) else {
                return StatusCode::INTERNAL_SERVER_ERROR.into_response();
            };
            match &parts["name"] {
                "ZulipInvalid" => error("Client is too old"),
                "ZulipMobile" if ANDROID.is_match(&user_agent) => match parts.name("version") {
                    Some(version) if older_android_version(version.as_str()) => {
                        error("Client is too old")
                    }
                    Some(_) => success(),
                    None => return StatusCode::INTERNAL_SERVER_ERROR.into_response(),
                },
                _ => success(),
            }
        }
    };
    (
        response.0,
        [
            (header::CONTENT_TYPE, "application/json"),
            (header::CONTENT_LANGUAGE, language),
            (header::VARY, "Accept-Language, Cookie"),
        ],
        response.1,
    )
        .into_response()
}

fn error(message: &str) -> (StatusCode, String) {
    let message = serde_json::to_string(message).expect("strings can serialize to JSON");
    (
        StatusCode::BAD_REQUEST,
        format!("{{\"result\":\"error\",\"msg\":{message},\"code\":\"BAD_REQUEST\"}}\n"),
    )
}

fn success() -> (StatusCode, String) {
    (
        StatusCode::OK,
        "{\"result\":\"success\",\"msg\":\"\"}\n".to_owned(),
    )
}
