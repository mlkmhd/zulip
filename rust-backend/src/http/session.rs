use std::{
    collections::HashSet,
    error::Error,
    fs,
    io::Read,
    time::{Duration, SystemTime},
};

use axum::http::{HeaderMap, HeaderValue, header};
use base64::{Engine, engine::general_purpose::URL_SAFE_NO_PAD};
use flate2::read::ZlibDecoder;
use hmac::{Hmac, KeyInit, Mac};
use rand::distr::{Alphanumeric, SampleString};
use serde_json::Value;
use sha2::{Digest, Sha256};
use tokio_postgres::Client;

use super::SessionConfig;

const SESSION_SALT: &[u8] = b"django.contrib.sessions.SessionStoresigner";
const AUTH_SALT: &[u8] = b"django.contrib.auth.models.AbstractBaseUser.get_session_auth_hash";
const MAX_SESSION_BYTES: usize = 1024 * 1024;
const CSRF_COOKIE_AGE: u64 = 31_449_600;
type Sha256Mac = Hmac<Sha256>;

pub(super) struct Sessions {
    signing_key: [u8; 32],
    auth_key: [u8; 32],
    backends: HashSet<String>,
    cookie_name: String,
    csrf_cookie_name: String,
}

pub(super) struct SessionUser {
    pub id: i32,
    pub rate_limits: String,
    pub subdomain: String,
    pub active: bool,
    pub realm_deactivated: bool,
    pub bot_type: Option<i16>,
}

pub(super) enum SessionLookup {
    User(SessionUser),
    Anonymous { clear_cookie: bool },
}

impl Sessions {
    pub(super) fn new(config: SessionConfig<'_>) -> Result<Self, Box<dyn Error>> {
        let secret = fs::read_to_string(config.secret_file)?;
        let secret = secret.trim_end_matches(['\r', '\n']);
        let backends: HashSet<String> = serde_json::from_str(config.backends)?;
        if secret.is_empty() || backends.is_empty() {
            return Err(
                "Django session authentication requires a secret and allowed backends".into(),
            );
        }
        for name in [config.cookie_name, config.csrf_cookie_name] {
            if name.is_empty()
                || !name
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
            {
                return Err("Invalid Django cookie name".into());
            }
        }
        if config.cookie_name == config.csrf_cookie_name {
            return Err("Session and CSRF cookie names must differ".into());
        }
        Ok(Self {
            signing_key: derive_key(SESSION_SALT, secret),
            auth_key: derive_key(AUTH_SALT, secret),
            backends,
            cookie_name: config.cookie_name.to_owned(),
            csrf_cookie_name: config.csrf_cookie_name.to_owned(),
        })
    }

    pub(super) async fn lookup(
        &self,
        db: &Client,
        headers: &HeaderMap,
    ) -> Result<SessionLookup, tokio_postgres::Error> {
        let anonymous = SessionLookup::Anonymous {
            clear_cookie: false,
        };
        let expired = SessionLookup::Anonymous { clear_cookie: true };
        let Some(key) = cookie(headers, &self.cookie_name) else {
            return Ok(anonymous);
        };
        if !(8..=40).contains(&key.len()) || !key.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
            return Ok(expired);
        }
        let Some(row) = db
            .query_opt(
                "SELECT session_data FROM zulip.django_session
             WHERE session_key = $1 AND expire_date > statement_timestamp()",
                &[&key],
            )
            .await?
        else {
            return Ok(expired);
        };
        let Some(session) = self.decode(row.try_get("session_data")?) else {
            return Ok(anonymous);
        };
        let auth = session.get("_auth_user_backend").and_then(Value::as_str);
        let user = session.get("_auth_user_id").and_then(|value| match value {
            Value::String(id) => id.parse::<i32>().ok(),
            _ => i32::try_from(value.as_i64()?).ok(),
        });
        let (Some(backend), Some(id)) = (auth, user) else {
            return Ok(anonymous);
        };
        if !self.backends.contains(backend) {
            return Ok(anonymous);
        }
        let Some(row) = db.query_opt(
            "SELECT u.password, u.is_active, u.bot_type, u.rate_limits, r.string_id, r.deactivated
             FROM zulip.zerver_userprofile AS u
             JOIN zulip.zerver_realm AS r ON r.id = u.realm_id WHERE u.id = $1",
            &[&id],
        ).await? else {
            return Ok(anonymous);
        };
        let password: &str = row.try_get("password")?;
        let auth_hash = session
            .get("_auth_user_hash")
            .and_then(Value::as_str)
            .and_then(hex_digest);
        if !auth_hash.is_some_and(|hash| {
            mac(&self.auth_key, password.as_bytes())
                .verify_slice(&hash)
                .is_ok()
        }) {
            return Ok(expired);
        }
        Ok(SessionLookup::User(SessionUser {
            id,
            rate_limits: row.try_get("rate_limits")?,
            subdomain: row.try_get("string_id")?,
            active: row.try_get("is_active")?,
            realm_deactivated: row.try_get("deactivated")?,
            bot_type: row.try_get("bot_type")?,
        }))
    }

    fn decode(&self, signed: &str) -> Option<Value> {
        if signed.len() > MAX_SESSION_BYTES {
            return None;
        }
        let (value, signature) = signed.rsplit_once(':')?;
        let signature = URL_SAFE_NO_PAD.decode(signature).ok()?;
        mac(&self.signing_key, value.as_bytes())
            .verify_slice(&signature)
            .ok()?;
        let (payload, timestamp) = value.rsplit_once(':')?;
        if timestamp.is_empty() || !timestamp.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
            return None;
        }
        let json = if let Some(payload) = payload.strip_prefix('.') {
            let compressed = URL_SAFE_NO_PAD.decode(payload).ok()?;
            let mut json = Vec::new();
            ZlibDecoder::new(&compressed[..])
                .take(MAX_SESSION_BYTES as u64 + 1)
                .read_to_end(&mut json)
                .ok()?;
            if json.len() > MAX_SESSION_BYTES {
                return None;
            }
            json
        } else {
            URL_SAFE_NO_PAD.decode(payload).ok()?
        };
        serde_json::from_slice(&json).ok()
    }

    pub(super) fn delete_cookie_header(&self) -> HeaderValue {
        let name = &self.cookie_name;
        let secure = secure_attribute(name);
        HeaderValue::try_from(format!(
            "{name}=\"\"; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/; SameSite=Lax{secure}"
        )).expect("validated session cookie name")
    }

    // Django accepts safe methods before token checking, but replaces a malformed CSRF cookie.
    pub(super) fn refresh_csrf_cookie(&self, headers: &HeaderMap) -> Option<HeaderValue> {
        let name = &self.csrf_cookie_name;
        let value = cookie(headers, name)?;
        if matches!(value.len(), 32 | 64) && value.bytes().all(|byte| byte.is_ascii_alphanumeric())
        {
            return None;
        }
        let token = Alphanumeric.sample_string(&mut rand::rng(), 32);
        let expires =
            httpdate::fmt_http_date(SystemTime::now() + Duration::from_secs(CSRF_COOKIE_AGE));
        let secure = secure_attribute(name);
        Some(HeaderValue::try_from(format!(
            "{name}={token}; expires={expires}; HttpOnly; Max-Age={CSRF_COOKIE_AGE}; Path=/; SameSite=Lax{secure}"
        )).expect("validated CSRF cookie name"))
    }
}

fn secure_attribute(name: &str) -> &'static str {
    if name.starts_with("__Host-") || name.starts_with("__Secure-") {
        "; Secure"
    } else {
        ""
    }
}

fn cookie<'a>(headers: &'a HeaderMap, name: &str) -> Option<&'a str> {
    headers
        .get_all(header::COOKIE)
        .iter()
        .filter_map(|value| value.to_str().ok())
        .flat_map(|value| value.split(';'))
        .filter_map(|part| part.split_once('='))
        .filter(|(key, _)| key.trim() == name)
        .map(|(_, value)| {
            let value = value.trim();
            value
                .strip_prefix('"')
                .and_then(|value| value.strip_suffix('"'))
                .unwrap_or(value)
        })
        .next_back()
}

fn derive_key(salt: &[u8], secret: &str) -> [u8; 32] {
    let mut hash = Sha256::new();
    hash.update(salt);
    hash.update(secret.as_bytes());
    hash.finalize().into()
}

fn mac(key: &[u8; 32], value: &[u8]) -> Sha256Mac {
    let mut mac = Sha256Mac::new_from_slice(key).expect("SHA-256 key");
    mac.update(value);
    mac
}

fn hex_digest(value: &str) -> Option<[u8; 32]> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return None;
    }
    let mut digest = [0; 32];
    for (index, output) in digest.iter_mut().enumerate() {
        let offset = index * 2;
        *output = u8::from_str_radix(&value[offset..offset + 2], 16).ok()?;
    }
    Some(digest)
}
