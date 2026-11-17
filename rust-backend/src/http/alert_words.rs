use std::{
    error::Error,
    net::IpAddr,
    sync::Arc,
    time::{Duration, SystemTime},
};

use axum::{
    extract::State,
    http::{HeaderMap, HeaderValue, StatusCode, Uri, header, uri::Authority},
    response::{IntoResponse, Response},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use tokio_postgres::{Client, Config, NoTls, config::Host};

use super::{
    AlertWordsConfig,
    activity::ActivityPublisher,
    locale::Locales,
    rate_limit::{RateLimit, RateLimiter},
    session::{SessionLookup, Sessions},
};

pub(super) const PATH: &str = "/api/v1/users/me/alert_words";
pub(super) const SESSION_PATH: &str = "/json/users/me/alert_words";
const INCOMING_WEBHOOK_BOT: i16 = 2;

#[derive(Clone)]
pub(super) struct AlertWords {
    database: Arc<Client>,
    external_host: Arc<str>,
    locales: Locales,
    rate_limiter: Option<RateLimiter>,
    activity: Option<ActivityPublisher>,
    sessions: Option<Arc<Sessions>>,
}

impl AlertWords {
    pub(super) async fn connect(
        settings: AlertWordsConfig<'_>,
        locales: Locales,
    ) -> Result<Self, Box<dyn Error>> {
        let sessions = settings
            .session
            .map(Sessions::new)
            .transpose()?
            .map(Arc::new);
        if !settings.external_host.split('.').all(valid_label) {
            return Err("ZULIP_RUST_EXTERNAL_HOST must be a hostname without a port".into());
        }
        let mut config: Config = settings.database.parse()?;
        if config.get_hosts().is_empty()
            || config.get_hosts().iter().any(|host| match host {
                Host::Tcp(host) => {
                    host != "localhost" && !host.parse::<IpAddr>().is_ok_and(|ip| ip.is_loopback())
                }
                #[cfg(unix)]
                Host::Unix(_) => false,
            })
            || config.get_hostaddrs().iter().any(|ip| !ip.is_loopback())
        {
            return Err("zulip-http requires an explicit local PostgreSQL host or socket".into());
        }
        config.connect_timeout(Duration::from_secs(5));
        let (database, connection) = config.connect(NoTls).await?;
        tokio::spawn(async move {
            if let Err(error) = connection.await {
                eprintln!("Alert-word PostgreSQL connection failed: {error}");
            }
        });
        database
            .batch_execute("SET statement_timeout = '5s'")
            .await?;
        let rate_limiter = match settings.rate_limit {
            Some(config) => Some(RateLimiter::connect(config).await?),
            None => None,
        };
        let activity = match settings.activity {
            Some(config) => Some(ActivityPublisher::connect(config).await?),
            None => {
                database
                    .batch_execute("SET default_transaction_read_only = on")
                    .await?;
                None
            }
        };
        Ok(Self {
            database: Arc::new(database),
            external_host: Arc::from(settings.external_host.to_ascii_lowercase()),
            locales,
            rate_limiter,
            activity,
            sessions,
        })
    }

    async fn authenticate(&self, headers: &HeaderMap) -> Result<AuthenticatedUser, ApiError> {
        let subdomain = subdomain(headers, &self.external_host).ok_or(ApiError::InvalidHost)?;
        let auth = credentials(headers)?;
        let user = self
            .database
            .query_opt(
                "SELECT u.id, u.delivery_email, u.is_active, u.bot_type, u.rate_limits,
                        r.string_id, r.deactivated
                 FROM zulip.zerver_userprofile AS u
                 JOIN zulip.zerver_realm AS r ON r.id = u.realm_id
                 WHERE u.api_key = $1",
                &[&auth.key],
            )
            .await?
            .ok_or(ApiError::Unauthorized("Invalid API key"))?;
        let email: &str = user.try_get("delivery_email")?;
        if email.to_lowercase() != auth.email.to_lowercase() {
            return Err(ApiError::Unauthorized("Invalid API key"));
        }
        if user.try_get("deactivated")? {
            return Err(ApiError::Unauthorized(
                "This organization has been deactivated",
            ));
        }
        if !user.try_get::<_, bool>("is_active")? {
            return Err(ApiError::Unauthorized("Account is deactivated"));
        }
        if user.try_get::<_, &str>("string_id")? != subdomain {
            return Err(ApiError::Unauthorized(
                "Account is not associated with this subdomain",
            ));
        }
        if user.try_get::<_, Option<i16>>("bot_type")? == Some(INCOMING_WEBHOOK_BOT) {
            return Err(ApiError::Unauthorized(
                "This API is not available to incoming webhook bots.",
            ));
        }

        Ok(AuthenticatedUser {
            id: user.try_get("id")?,
            rate_limits: user.try_get("rate_limits")?,
        })
    }

    async fn words(&self, user_id: i32) -> Result<Vec<String>, ApiError> {
        self.database
            .query(
                "SELECT word FROM zulip.zerver_alertword WHERE user_profile_id = $1",
                &[&user_id],
            )
            .await?
            .iter()
            .map(|row| row.try_get("word").map_err(ApiError::from))
            .collect()
    }

    async fn limit(
        &self,
        user_id: i32,
        rules: &str,
        result: &mut Option<RateLimit>,
    ) -> Result<(), ApiError> {
        if let Some(limiter) = &self.rate_limiter {
            let limit = limiter.check(user_id, rules).await.map_err(|error| {
                eprintln!("Alert-word rate limit check failed: {error}");
                ApiError::RateLimiterUnavailable
            })?;
            let retry = limit.retry_after;
            *result = Some(limit);
            if let Some(seconds) = retry {
                return Err(ApiError::RateLimited(seconds));
            }
        }
        Ok(())
    }

    async fn record_activity(
        &self,
        user_id: i32,
        query: Option<&str>,
        headers: &HeaderMap,
        browser: bool,
    ) -> Result<(), ApiError> {
        if let Some(activity) = &self.activity {
            activity
                .record(&self.database, user_id, query, headers, browser)
                .await
                .map_err(|error| {
                    eprintln!("Alert-word activity failed: {error}");
                    ApiError::ActivityUnavailable
                })?;
        }
        Ok(())
    }
}

struct AuthenticatedUser {
    id: i32,
    rate_limits: String,
}

fn valid_label(label: &str) -> bool {
    !label.is_empty()
        && label.len() <= 63
        && !label.starts_with('-')
        && !label.ends_with('-')
        && label
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
}

fn subdomain(headers: &HeaderMap, external_host: &str) -> Option<String> {
    let mut hosts = headers.get_all(header::HOST).iter();
    let host: Authority = hosts.next()?.to_str().ok()?.parse().ok()?;
    if hosts.next().is_some() || host.as_str().contains('@') {
        return None;
    }
    let host = host.host().to_ascii_lowercase();
    if host == external_host {
        return Some(String::new());
    }
    let (subdomain, domain) = host.split_once('.')?;
    if domain != external_host || !valid_label(subdomain) {
        return None;
    }
    Some(if subdomain == "www" {
        String::new()
    } else {
        subdomain.to_owned()
    })
}

struct Credentials {
    email: String,
    key: String,
}

fn credentials(headers: &HeaderMap) -> Result<Credentials, ApiError> {
    let mut values = headers.get_all(header::AUTHORIZATION).iter();
    let Some(header) = values.next() else {
        return Err(ApiError::MissingAuthentication);
    };
    let malformed = ApiError::Unauthorized("Invalid authorization header for basic auth");
    let mut fields = header.to_str().map_err(|_| malformed)?.split_whitespace();
    let (Some(scheme), Some(encoded), None, None) =
        (fields.next(), fields.next(), fields.next(), values.next())
    else {
        return Err(malformed);
    };
    if !scheme.eq_ignore_ascii_case("basic") {
        return Err(ApiError::BadRequest(
            "This endpoint requires HTTP basic authentication.",
        ));
    }
    let decoded = STANDARD.decode(encoded).map_err(|_| malformed)?;
    let decoded = std::str::from_utf8(&decoded).map_err(|_| malformed)?;
    let (email, key) = decoded.split_once(':').ok_or(malformed)?;
    if key.contains(':') {
        return Err(malformed);
    }
    let key = key.trim();
    if key.len() != 32 || !key.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
        return Err(ApiError::Unauthorized("Malformed API key"));
    }
    Ok(Credentials {
        email: email.trim().to_owned(),
        key: key.to_owned(),
    })
}

#[derive(Clone, Copy)]
enum ApiError {
    MissingAuthentication,
    Unauthorized(&'static str),
    BadRequest(&'static str),
    DeactivatedUser,
    DeactivatedRealm,
    InvalidHost,
    DatabaseUnavailable,
    RateLimiterUnavailable,
    ActivityUnavailable,
    RateLimited(f64),
}

impl From<tokio_postgres::Error> for ApiError {
    fn from(error: tokio_postgres::Error) -> Self {
        eprintln!("Alert-word PostgreSQL request failed: {error}");
        Self::DatabaseUnavailable
    }
}

// Django chooses the most specific accepted range for each response type,
// then compares their quality, specificity, and order in the Accept header.
fn prefers_html(headers: &HeaderMap) -> bool {
    let Some(accept) = headers
        .get(header::ACCEPT)
        .and_then(|value| value.to_str().ok())
    else {
        return false;
    };
    let score = |media_type, wildcard| {
        accept
            .split(',')
            .enumerate()
            .filter_map(|(index, range)| {
                let mut parts = range.split(';');
                let specificity = match parts.next()?.trim() {
                    range if range == media_type => 2,
                    range if range == wildcard => 1,
                    "*/*" => 0,
                    _ => return None,
                };
                let mut quality = 1.0;
                for parameter in parts {
                    if let Some((name, value)) = parameter.trim().split_once('=') {
                        if !name.trim().eq_ignore_ascii_case("q") {
                            return None;
                        }
                        quality = value.trim().trim_matches('"').parse::<f32>().unwrap_or(1.0);
                    }
                }
                if !(0.0..=1.0).contains(&quality) {
                    quality = 1.0;
                }
                (quality > 0.0).then_some((specificity, quality, std::cmp::Reverse(index)))
            })
            .max_by(|a, b| a.partial_cmp(b).expect("quality is finite"))
            .map(|(specificity, quality, index)| (quality, specificity, index))
    };
    score("text/html", "text/*") > score("application/json", "application/*")
}

pub(super) async fn get(State(state): State<AlertWords>, uri: Uri, headers: HeaderMap) -> Response {
    let language = Locales::for_request(&headers);
    let browser_session = if uri.path() == SESSION_PATH {
        state.sessions.as_deref()
    } else {
        None
    };
    let browser = browser_session.is_some();
    let mut clear_session_cookie = false;
    let mut rate_limit = None;
    let words = async {
        let user_id = if let Some(sessions) = browser_session {
            let subdomain =
                subdomain(&headers, &state.external_host).ok_or(ApiError::InvalidHost)?;
            let user = match sessions.lookup(&state.database, &headers).await? {
                SessionLookup::User(user) => user,
                SessionLookup::Anonymous { clear_cookie } => {
                    clear_session_cookie = clear_cookie;
                    return Err(ApiError::MissingAuthentication);
                }
            };
            // The browser dispatcher checks the quota before account state and activity.
            state
                .limit(user.id, &user.rate_limits, &mut rate_limit)
                .await?;
            if user.realm_deactivated {
                return Err(ApiError::DeactivatedRealm);
            }
            if !user.active {
                return Err(ApiError::DeactivatedUser);
            }
            if user.subdomain != subdomain {
                return Err(ApiError::BadRequest(
                    "Account is not associated with this subdomain",
                ));
            }
            if user.bot_type == Some(INCOMING_WEBHOOK_BOT) {
                return Err(ApiError::BadRequest(
                    "Webhook bots can only access webhooks",
                ));
            }
            state
                .record_activity(user.id, uri.query(), &headers, true)
                .await?;
            user.id
        } else {
            let user = state.authenticate(&headers).await?;
            // Basic Auth records activity before the quota check.
            state
                .record_activity(user.id, uri.query(), &headers, false)
                .await?;
            state
                .limit(user.id, &user.rate_limits, &mut rate_limit)
                .await?;
            user.id
        };
        state.words(user_id).await
    }
    .await;
    let mut response = match words {
        Ok(words) => {
            let words = serde_json::to_string(&words).expect("strings can serialize to JSON");
            (
                StatusCode::OK,
                [
                    (header::CONTENT_TYPE, "application/json"),
                    (
                        header::CACHE_CONTROL,
                        "max-age=0, no-cache, no-store, must-revalidate, private",
                    ),
                    (header::VARY, "Accept-Language"),
                ],
                format!("{{\"result\":\"success\",\"msg\":\"\",\"alert_words\":{words}}}\n"),
            )
                .into_response()
        }
        Err(error) => error_response(error, &state.locales, language, &headers, browser),
    };
    let response_headers = response.headers_mut();
    response_headers.insert(header::CONTENT_LANGUAGE, HeaderValue::from_static(language));
    if response_headers.contains_key(header::CACHE_CONTROL) {
        let expires = httpdate::fmt_http_date(SystemTime::now());
        response_headers.insert(
            header::EXPIRES,
            HeaderValue::try_from(expires).expect("HTTP date"),
        );
    }
    if let Some(limit) = rate_limit {
        limit.set_headers(response_headers);
    }
    if let Some(sessions) = browser_session {
        response_headers.insert(
            header::VARY,
            HeaderValue::from_static("Accept-Language, Cookie"),
        );
        if let Some(csrf) = sessions.refresh_csrf_cookie(&headers) {
            response_headers.append(header::SET_COOKIE, csrf);
        }
        if clear_session_cookie {
            response_headers.append(header::SET_COOKIE, sessions.delete_cookie_header());
        }
    }
    response
}

fn error_response(
    error: ApiError,
    locales: &Locales,
    language: &str,
    headers: &HeaderMap,
    browser: bool,
) -> Response {
    if matches!(error, ApiError::MissingAuthentication) && prefers_html(headers) {
        return (
            StatusCode::FOUND,
            [
                (header::CONTENT_TYPE, "text/html; charset=utf-8"),
                (
                    header::LOCATION,
                    if browser {
                        "/login/?next=%2Fjson%2Fusers%2Fme%2Falert_words"
                    } else {
                        "/login/?next=%2Fapi%2Fv1%2Fusers%2Fme%2Falert_words"
                    },
                ),
                (header::VARY, "Accept-Language, Cookie"),
            ],
            "",
        )
            .into_response();
    }
    let (status, code, message) = match error {
        ApiError::MissingAuthentication => (
            StatusCode::UNAUTHORIZED,
            "UNAUTHORIZED",
            "Not logged in: API authentication or user session required",
        ),
        ApiError::Unauthorized(message) => (StatusCode::UNAUTHORIZED, "UNAUTHORIZED", message),
        ApiError::BadRequest(message) => (StatusCode::BAD_REQUEST, "BAD_REQUEST", message),
        ApiError::DeactivatedUser => (
            StatusCode::UNAUTHORIZED,
            "USER_DEACTIVATED",
            "Account is deactivated",
        ),
        ApiError::DeactivatedRealm => (
            StatusCode::UNAUTHORIZED,
            "REALM_DEACTIVATED",
            "This organization has been deactivated",
        ),
        ApiError::InvalidHost => (StatusCode::BAD_REQUEST, "BAD_REQUEST", "Invalid host"),
        ApiError::DatabaseUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Database unavailable",
        ),
        ApiError::RateLimiterUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Rate limiter unavailable",
        ),
        ApiError::ActivityUnavailable => (
            StatusCode::SERVICE_UNAVAILABLE,
            "SERVICE_UNAVAILABLE",
            "Activity service unavailable",
        ),
        ApiError::RateLimited(_) => (
            StatusCode::TOO_MANY_REQUESTS,
            "RATE_LIMIT_HIT",
            "API usage exceeded rate limit; see https://zulip.com/api/http-headers#rate-limiting-response-headers",
        ),
    };
    let message = serde_json::to_string(locales.gettext(language, message))
        .expect("strings can serialize to JSON");
    let (extra, vary) = match error {
        ApiError::RateLimited(seconds) => {
            (format!(",\"retry-after\":{seconds:?}"), "Accept-Language")
        }
        _ => (String::new(), "Accept-Language, Cookie"),
    };
    let mut response = (
        status,
        [
            (header::CONTENT_TYPE, "application/json"),
            (header::VARY, vary),
        ],
        format!("{{\"result\":\"error\",\"msg\":{message},\"code\":\"{code}\"{extra}}}\n"),
    )
        .into_response();
    if matches!(
        error,
        ApiError::MissingAuthentication | ApiError::Unauthorized(_)
    ) {
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            HeaderValue::from_static(if browser {
                "Session realm=\"zulip\""
            } else {
                "Basic realm=\"zulip\""
            }),
        );
    }
    if let ApiError::RateLimited(seconds) = error {
        let retry = format!("{seconds:?}");
        let retry = if let Some((mantissa, exponent)) = retry.split_once('e') {
            format!(
                "{mantissa}e{:+03}",
                exponent.parse::<i32>().expect("float exponent")
            )
        } else {
            retry
        };
        response.headers_mut().insert(
            header::RETRY_AFTER,
            HeaderValue::try_from(retry).expect("numeric retry delay"),
        );
    }
    response
}
