use std::{error::Error, path::Path};

use axum::{Router, routing::get};

mod activity;
mod alert_words;
mod compatibility;
mod database;
mod locale;
mod rate_limit;
mod session;
mod user_agent;

pub fn router(locale_dir: &Path) -> Result<Router, Box<dyn Error>> {
    Ok(compatibility_router(locale::Locales::load(locale_dir)?))
}

pub struct AlertWordsConfig<'a> {
    pub database: &'a str,
    pub external_host: &'a str,
    pub rate_limit: Option<RateLimitConfig<'a>>,
    pub activity: Option<ActivityConfig<'a>>,
    pub session: Option<SessionConfig<'a>>,
}

pub struct SessionConfig<'a> {
    pub secret_file: &'a Path,
    pub backends: &'a str,
    pub cookie_name: &'a str,
    pub csrf_cookie_name: &'a str,
}

pub struct RateLimitConfig<'a> {
    pub url: &'a str,
    pub key_prefix: &'a str,
    pub rules: &'a str,
}

pub struct ActivityConfig<'a> {
    pub url: &'a str,
    pub shards: u32,
}

pub async fn router_with_alert_words(
    locale_dir: &Path,
    config: AlertWordsConfig<'_>,
) -> Result<Router, Box<dyn Error>> {
    let locales = locale::Locales::load(locale_dir)?;
    let browser_enabled = config.session.is_some();
    let alert_words = alert_words::AlertWords::connect(config, locales.clone()).await?;
    let mut routes = Router::new()
        .route(alert_words::PATH, get(alert_words::get))
        .route("/health/rust-alert-words", get(alert_words::ready));
    if browser_enabled {
        routes = routes.route(alert_words::SESSION_PATH, get(alert_words::get));
    }
    Ok(compatibility_router(locales).merge(routes.with_state(alert_words)))
}

fn compatibility_router(locales: locale::Locales) -> Router {
    Router::new()
        .route("/compatibility", get(compatibility::get))
        .with_state(locales)
}
