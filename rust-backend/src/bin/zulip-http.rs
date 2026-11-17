use std::{
    env,
    error::Error,
    net::SocketAddr,
    path::{Path, PathBuf},
};

use _zulip_backend::http::{
    ActivityConfig, RateLimitConfig, SessionConfig, UserReadConfig, router, router_with_user_reads,
};
use axum::Router;
use tokio::net::TcpListener;

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
    let app = match env::var("ZULIP_RUST_DATABASE_URL") {
        Ok(database) => user_read_router(&locale_dir, &database).await?,
        Err(env::VarError::NotPresent) => router(&locale_dir)?,
        Err(error) => return Err(error.into()),
    };
    let listener = TcpListener::bind(address).await?;
    eprintln!("Zulip HTTP listening on {}", listener.local_addr()?);
    axum::serve(listener, app).await?;
    Ok(())
}

async fn user_read_router(locale_dir: &Path, database: &str) -> Result<Router, Box<dyn Error>> {
    let external_host = env::var("ZULIP_RUST_EXTERNAL_HOST")?;
    let redis_url = optional_env("ZULIP_RUST_REDIS_URL")?;
    let rules = if redis_url.is_some() {
        env::var("ZULIP_RUST_RATE_LIMIT_RULES")?
    } else {
        String::new()
    };
    let key_prefix = optional_env("ZULIP_RUST_REDIS_KEY_PREFIX")?.unwrap_or_default();
    let amqp_url = optional_env("ZULIP_RUST_AMQP_URL")?;
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
            external_host: &external_host,
            rate_limit: redis_url.as_deref().map(|url| RateLimitConfig {
                url,
                key_prefix: &key_prefix,
                rules: &rules,
            }),
            activity: amqp_url
                .as_deref()
                .map(|url| ActivityConfig { url, shards }),
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
