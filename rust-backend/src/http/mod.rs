use std::{error::Error, path::Path};

use axum::{
    Router,
    extract::DefaultBodyLimit,
    routing::{delete, get, patch},
};
pub use csrf::WebPostScheme;
pub use rate_limit::ClientIpSource;

mod activity;
mod alert_word_cache;
pub mod alert_words;
mod compatibility;
mod csrf;
mod database;
mod drafts;
mod form;
mod linkifiers;
mod locale;
mod rate_limit;
mod saved_snippets;
mod session;
mod tornado_routing;
mod unicode_lowercase;
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
    pub alert_word_cache_manifest: Option<&'a Path>,
    pub alert_word_lowercase_file: Option<&'a Path>,
    pub alert_word_posts: bool,
    pub draft_deletes: bool,
    pub session: Option<SessionConfig<'a>>,
    pub web_post_scheme: Option<WebPostScheme>,
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
    pub tornado_routing_file: Option<&'a Path>,
}

pub async fn router_with_user_reads(
    locale_dir: &Path,
    config: UserReadConfig<'_>,
) -> Result<Router, Box<dyn Error>> {
    let locales = locale::Locales::load(locale_dir)?;
    let browser_enabled = config.session.is_some();
    let saved_snippet_writes = config.saved_snippet_max_content_length.is_some();
    let alert_word_writes = config.alert_word_lowercase_file.is_some();
    let alert_word_posts = config.alert_word_posts;
    let draft_deletes = config.draft_deletes;
    if alert_word_posts && !alert_word_writes {
        return Err("Alert-word POST requires ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE".into());
    }
    let web_writes_enabled = config.web_post_scheme.is_some();
    if web_writes_enabled
        && !(browser_enabled && (saved_snippet_writes || alert_word_writes || draft_deletes))
    {
        return Err("Web writes require Django sessions and an enabled write route".into());
    }
    let user_reads = user_reads::UserReads::connect(config, locales.clone()).await?;
    let alert_word_route = get(user_reads::get);
    let mut alert_word_write_route = get(user_reads::get).delete(user_reads::remove_alert_words);
    if alert_word_posts {
        alert_word_write_route = alert_word_write_route.post(user_reads::add_alert_words);
    }
    let alert_word_write_route =
        alert_word_write_route.layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    let mut snippet_route = get(user_reads::get);
    if saved_snippet_writes {
        snippet_route = snippet_route
            .post(user_reads::create_saved_snippet)
            .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    }
    let mut routes = Router::new()
        .route(
            user_reads::ALERT_WORDS_PATH,
            if alert_word_writes {
                alert_word_write_route.clone()
            } else {
                alert_word_route.clone()
            },
        )
        .route(saved_snippets::PATH, snippet_route)
        .route(drafts::PATH, get(user_reads::get))
        .route(linkifiers::PATH, get(user_reads::get))
        .route("/health/rust-alert-words", get(user_reads::ready));
    let snippet_detail_route = patch(user_reads::write_saved_snippet_detail)
        .delete(user_reads::write_saved_snippet_detail)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    if saved_snippet_writes {
        routes = routes.route(saved_snippets::DETAIL_PATH, snippet_detail_route.clone());
    }
    let draft_detail_route =
        delete(user_reads::delete_draft).layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    if draft_deletes {
        routes = routes.route(drafts::DETAIL_PATH, draft_detail_route.clone());
    }
    if browser_enabled {
        let mut web_snippet_route = get(user_reads::get);
        if web_writes_enabled && saved_snippet_writes {
            web_snippet_route = web_snippet_route
                .post(user_reads::create_saved_snippet)
                .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
            routes = routes.route(saved_snippets::SESSION_DETAIL_PATH, snippet_detail_route);
        }
        if web_writes_enabled && draft_deletes {
            routes = routes.route(drafts::SESSION_DETAIL_PATH, draft_detail_route);
        }
        routes = routes
            .route(
                user_reads::ALERT_WORDS_SESSION_PATH,
                if web_writes_enabled && alert_word_writes {
                    alert_word_write_route
                } else {
                    alert_word_route
                },
            )
            .route(drafts::SESSION_PATH, get(user_reads::get))
            .route(linkifiers::SESSION_PATH, get(user_reads::get))
            .route(saved_snippets::SESSION_PATH, web_snippet_route);
    }
    Ok(compatibility_router(locales).merge(routes.with_state(user_reads)))
}

fn compatibility_router(locales: locale::Locales) -> Router {
    Router::new()
        .route("/compatibility", get(compatibility::get))
        .with_state(locales)
}
