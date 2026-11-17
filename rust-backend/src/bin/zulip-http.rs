use std::{
    env,
    error::Error,
    net::SocketAddr,
    path::{Path, PathBuf},
};

use _zulip_backend::http::{
    ActivityConfig, ClientIpSource, RateLimitConfig, SessionConfig, UserReadConfig, WebPostScheme,
    router, router_with_user_reads,
};
use axum::Router;
use tokio::net::TcpListener;

struct NavigationViewWrites {
    posts: bool,
    details: bool,
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn Error>> {
    let address: SocketAddr = env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:9997".to_owned())
        .parse()?;
    if !address.ip().is_loopback() {
        return Err("zulip-http must listen on a loopback address".into());
    }
    let locale_dir =
        env::var_os("ZULIP_LOCALE_DIR").map_or_else(|| PathBuf::from("locale"), PathBuf::from);
    let cache_manifest = env::var_os("ZULIP_RUST_ALERT_WORD_CACHE_MANIFEST").map(PathBuf::from);
    let lowercase_file = env::var_os("ZULIP_RUST_ALERT_WORD_LOWERCASE_FILE").map(PathBuf::from);
    let alert_word_posts = enabled("ZULIP_RUST_ALERT_WORD_POSTS")?;
    let draft_deletes = enabled("ZULIP_RUST_DRAFT_DELETES")?;
    let navigation_view_writes = NavigationViewWrites {
        posts: enabled("ZULIP_RUST_NAVIGATION_VIEW_POSTS")?,
        details: enabled("ZULIP_RUST_NAVIGATION_VIEW_DETAILS")?,
    };
    let app = match env::var("ZULIP_RUST_DATABASE_URL") {
        Ok(database) => {
            user_read_router(
                &locale_dir,
                &database,
                cache_manifest.as_deref(),
                lowercase_file.as_deref(),
                alert_word_posts,
                draft_deletes,
                navigation_view_writes,
            )
            .await?
        }
        Err(env::VarError::NotPresent) => {
            if optional_env("ZULIP_RUST_EMOJI_URL_BASE")?.is_some() {
                return Err("Custom emoji reads require ZULIP_RUST_DATABASE_URL".into());
            }
            if draft_deletes {
                return Err("Draft DELETE requires ZULIP_RUST_DATABASE_URL".into());
            }
            if navigation_view_writes.posts || navigation_view_writes.details {
                return Err("Navigation view writes require ZULIP_RUST_DATABASE_URL".into());
            }
            if lowercase_file.is_some() || alert_word_posts {
                return Err("Alert-word writes require ZULIP_RUST_DATABASE_URL".into());
            }
            if cache_manifest.is_some() {
                return Err("Alert-word cache readiness requires ZULIP_RUST_DATABASE_URL".into());
            }
            if optional_env("ZULIP_RUST_WEB_POST_SCHEME")?.is_some() {
                return Err("Web writes require ZULIP_RUST_DATABASE_URL".into());
            }
            router(&locale_dir)?
        }
        Err(error) => return Err(error.into()),
    };
    let listener = TcpListener::bind(address).await?;
    eprintln!("Zulip HTTP listening on {}", listener.local_addr()?);
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await?;
    Ok(())
}

async fn user_read_router(
    locale_dir: &Path,
    database: &str,
    alert_word_cache_manifest: Option<&Path>,
    alert_word_lowercase_file: Option<&Path>,
    alert_word_posts: bool,
    draft_deletes: bool,
    navigation_view_writes: NavigationViewWrites,
) -> Result<Router, Box<dyn Error>> {
    let external_host = env::var("ZULIP_RUST_EXTERNAL_HOST")?;
    let emoji_url_base = optional_env("ZULIP_RUST_EMOJI_URL_BASE")?;
    let redis_url = optional_env("ZULIP_RUST_REDIS_URL")?;
    let rules = if redis_url.is_some() {
        env::var("ZULIP_RUST_RATE_LIMIT_RULES")?
    } else {
        String::new()
    };
    let key_prefix = optional_env("ZULIP_RUST_REDIS_KEY_PREFIX")?.unwrap_or_default();
    let client_ip_source = match optional_env("ZULIP_RUST_CLIENT_IP_SOURCE")?.as_deref() {
        None => ClientIpSource::Unspecified,
        Some("peer") => ClientIpSource::Peer,
        Some("proxy") => ClientIpSource::Proxy,
        Some(_) => return Err("ZULIP_RUST_CLIENT_IP_SOURCE must be peer or proxy".into()),
    };
    let debug_rate_limiting = match optional_env("ZULIP_RUST_DEBUG_RATE_LIMITING")?.as_deref() {
        None | Some("0") => false,
        Some("1") => true,
        Some(_) => return Err("ZULIP_RUST_DEBUG_RATE_LIMITING must be 0 or 1".into()),
    };
    let amqp_url = optional_env("ZULIP_RUST_AMQP_URL")?;
    let tornado_routing_file = env::var_os("ZULIP_RUST_TORNADO_ROUTING_FILE").map(PathBuf::from);
    if tornado_routing_file.is_some() && amqp_url.is_none() {
        return Err("Tornado routing requires ZULIP_RUST_AMQP_URL".into());
    }
    let saved_snippet_max_content_length =
        match optional_env("ZULIP_RUST_SAVED_SNIPPET_WRITES")?.as_deref() {
            None | Some("0") => None,
            Some("1") => Some(
                optional_env("ZULIP_RUST_MAX_MESSAGE_LENGTH")?
                    .map_or(Ok(10_000), |value| value.parse())?,
            ),
            Some(_) => return Err("ZULIP_RUST_SAVED_SNIPPET_WRITES must be 0 or 1".into()),
        };
    let web_post_scheme = match optional_env("ZULIP_RUST_WEB_POST_SCHEME")?.as_deref() {
        None => None,
        Some("http") => Some(WebPostScheme::Http),
        Some("https") => Some(WebPostScheme::Https),
        Some(_) => return Err("ZULIP_RUST_WEB_POST_SCHEME must be http or https".into()),
    };
    let shards =
        optional_env("ZULIP_RUST_USER_ACTIVITY_SHARDS")?.map_or(Ok(1), |value| value.parse())?;
    let secret_file = env::var_os("ZULIP_RUST_DJANGO_SECRET_KEY_FILE").map(PathBuf::from);
    let backends = if secret_file.is_some() {
        env::var("ZULIP_RUST_DJANGO_AUTH_BACKENDS")?
    } else {
        String::new()
    };
    let cookie_name = optional_env("ZULIP_RUST_SESSION_COOKIE_NAME")?;
    let csrf_cookie_name = optional_env("ZULIP_RUST_CSRF_COOKIE_NAME")?;
    router_with_user_reads(
        locale_dir,
        UserReadConfig {
            database,
            alert_word_cache_manifest,
            alert_word_lowercase_file,
            alert_word_posts,
            draft_deletes,
            navigation_view_posts: navigation_view_writes.posts,
            navigation_view_details: navigation_view_writes.details,
            web_post_scheme,
            saved_snippet_max_content_length,
            external_host: &external_host,
            emoji_url_base: emoji_url_base.as_deref(),
            rate_limit: redis_url.as_deref().map(|url| RateLimitConfig {
                url,
                key_prefix: &key_prefix,
                rules: &rules,
                client_ip_source,
                debug_rate_limiting,
            }),
            activity: amqp_url.as_deref().map(|url| ActivityConfig {
                url,
                shards,
                tornado_routing_file: tornado_routing_file.as_deref(),
            }),
            session: secret_file.as_deref().map(|secret_file| SessionConfig {
                secret_file,
                backends: &backends,
                cookie_name: cookie_name.as_deref().unwrap_or("sessionid"),
                csrf_cookie_name: csrf_cookie_name.as_deref().unwrap_or("csrftoken"),
            }),
        },
    )
    .await
}

fn optional_env(name: &str) -> Result<Option<String>, env::VarError> {
    match env::var(name) {
        Ok(value) => Ok(Some(value)),
        Err(env::VarError::NotPresent) => Ok(None),
        Err(error) => Err(error),
    }
}

fn enabled(name: &str) -> Result<bool, Box<dyn Error>> {
    match optional_env(name)?.as_deref() {
        None | Some("0") => Ok(false),
        Some("1") => Ok(true),
        Some(_) => Err(format!("{name} must be 0 or 1").into()),
    }
}
