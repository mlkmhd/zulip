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
mod attachments;
mod bot_storage;
mod channel_folders;
mod compatibility;
mod csrf;
mod custom_profile_fields;
mod database;
mod drafts;
mod form;
mod linkifiers;
mod locale;
mod navigation_views;
mod rate_limit;
mod realm_domains;
mod realm_emoji;
mod saved_snippets;
mod scheduled_messages;
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
    pub emoji_url_base: Option<&'a str>,
    pub rate_limit: Option<RateLimitConfig<'a>>,
    pub activity: Option<ActivityConfig<'a>>,
    pub alert_word_cache_manifest: Option<&'a Path>,
    pub alert_word_lowercase_file: Option<&'a Path>,
    pub alert_word_posts: bool,
    pub draft_deletes: bool,
    pub bot_storage_deletes: bool,
    pub navigation_view_posts: bool,
    pub navigation_view_details: bool,
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
    let emoji_reads = config.emoji_url_base.is_some();
    let saved_snippet_writes = config.saved_snippet_max_content_length.is_some();
    let alert_word_writes = config.alert_word_lowercase_file.is_some();
    let alert_word_posts = config.alert_word_posts;
    let draft_deletes = config.draft_deletes;
    let bot_storage_deletes = config.bot_storage_deletes;
    let navigation_view_posts = config.navigation_view_posts;
    let navigation_view_details = config.navigation_view_details;
    if alert_word_posts && !alert_word_writes {
        return Err("Alert-word POST requires ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE".into());
    }
    let web_writes_enabled = config.web_post_scheme.is_some();
    if web_writes_enabled
        && !(browser_enabled
            && (saved_snippet_writes
                || alert_word_writes
                || draft_deletes
                || bot_storage_deletes
                || navigation_view_posts
                || navigation_view_details))
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
    let navigation_view_post_route = get(user_reads::get)
        .post(user_reads::create_navigation_view)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    let bot_storage_delete_route = get(user_reads::get)
        .delete(user_reads::delete_bot_storage)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
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
        .route(attachments::PATH, get(user_reads::get))
        .route(linkifiers::PATH, get(user_reads::get))
        .route(realm_domains::PATH, get(user_reads::get))
        .route(custom_profile_fields::PATH, get(user_reads::get))
        .route(
            navigation_views::PATH,
            if navigation_view_posts {
                navigation_view_post_route.clone()
            } else {
                get(user_reads::get)
            },
        )
        .route(channel_folders::PATH, get(user_reads::get))
        .route(
            bot_storage::PATH,
            if bot_storage_deletes {
                bot_storage_delete_route.clone()
            } else {
                get(user_reads::get)
            },
        )
        .route(scheduled_messages::PATH, get(user_reads::get))
        .route(scheduled_messages::REMINDERS_PATH, get(user_reads::get))
        .route("/health/rust-alert-words", get(user_reads::ready));
    if emoji_reads {
        routes = routes.route(realm_emoji::PATH, get(user_reads::get));
    }
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
    let navigation_view_detail_route = patch(user_reads::write_navigation_view_detail)
        .delete(user_reads::write_navigation_view_detail)
        .layer(DefaultBodyLimit::max(form::FORM_BODY_LIMIT));
    if navigation_view_details {
        routes = routes.route(
            navigation_views::DETAIL_PATH,
            navigation_view_detail_route.clone(),
        );
    }
    if browser_enabled {
        routes = routes.route(
            bot_storage::SESSION_PATH,
            if web_writes_enabled && bot_storage_deletes {
                bot_storage_delete_route
            } else {
                get(user_reads::get)
            },
        );
        if emoji_reads {
            routes = routes.route(realm_emoji::SESSION_PATH, get(user_reads::get));
        }
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
        if web_writes_enabled && navigation_view_details {
            routes = routes.route(
                navigation_views::SESSION_DETAIL_PATH,
                navigation_view_detail_route,
            );
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
            .route(attachments::SESSION_PATH, get(user_reads::get))
            .route(linkifiers::SESSION_PATH, get(user_reads::get))
            .route(realm_domains::SESSION_PATH, get(user_reads::get))
            .route(custom_profile_fields::SESSION_PATH, get(user_reads::get))
            .route(
                navigation_views::SESSION_PATH,
                if web_writes_enabled && navigation_view_posts {
                    navigation_view_post_route
                } else {
                    get(user_reads::get)
                },
            )
            .route(channel_folders::SESSION_PATH, get(user_reads::get))
            .route(scheduled_messages::SESSION_PATH, get(user_reads::get))
            .route(
                scheduled_messages::SESSION_REMINDERS_PATH,
                get(user_reads::get),
            )
            .route(saved_snippets::SESSION_PATH, web_snippet_route);
    }
    Ok(compatibility_router(locales).merge(routes.with_state(user_reads)))
}

fn compatibility_router(locales: locale::Locales) -> Router {
    Router::new()
        .route("/compatibility", get(compatibility::get))
        .with_state(locales)
}
