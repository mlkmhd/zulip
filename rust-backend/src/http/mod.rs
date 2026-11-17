use std::{error::Error, path::Path};

use axum::{Router, extract::DefaultBodyLimit, routing::get};
pub use rate_limit::ClientIpSource;

mod activity;
mod compatibility;
mod database;
mod locale;
mod rate_limit;
mod saved_snippets;
mod session;
mod user_agent;
mod user_reads;

pub fn router(locale_dir: &Path) -> Result<Router, Box<dyn Error>> {
    Ok(compatibility_router(locale::Locales::load(locale_dir)?))
}

pub struct UserReadConfig<'a> {
    pub database: &'a str,
    pub external_host: &'a str,
    pub rate_limit: Option<RateLimitConfig<'a>>,
    pub activity: Option<ActivityConfig<'a>>,
    pub session: Option<SessionConfig<'a>>,
    pub saved_snippet_max_content_length: Option<usize>,
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
    pub client_ip_source: ClientIpSource,
    pub debug_rate_limiting: bool,
}

pub struct ActivityConfig<'a> {
    pub url: &'a str,
    pub shards: u32,
}

pub async fn router_with_user_reads(
    locale_dir: &Path,
    config: UserReadConfig<'_>,
) -> Result<Router, Box<dyn Error>> {
    let locales = locale::Locales::load(locale_dir)?;
    let browser_enabled = config.session.is_some();
    let saved_snippet_writes = config.saved_snippet_max_content_length.is_some();
    let user_reads = user_reads::UserReads::connect(config, locales.clone()).await?;
    let mut snippet_route = get(user_reads::get);
    if saved_snippet_writes {
        snippet_route = snippet_route
            .post(user_reads::create_saved_snippet)
            .layer(DefaultBodyLimit::max(saved_snippets::FORM_BODY_LIMIT));
    }
    let mut routes = Router::new()
        .route(user_reads::ALERT_WORDS_PATH, get(user_reads::get))
        .route(saved_snippets::PATH, snippet_route)
        .route("/health/rust-alert-words", get(user_reads::ready));
    if browser_enabled {
        routes = routes.route(user_reads::SESSION_PATH, get(user_reads::get));
    }
    Ok(compatibility_router(locales).merge(routes.with_state(user_reads)))
}

fn compatibility_router(locales: locale::Locales) -> Router {
    Router::new()
        .route("/compatibility", get(compatibility::get))
        .with_state(locales)
}
