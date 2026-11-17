use std::{env, error::Error, net::SocketAddr, path::PathBuf};

use _zulip_backend::http::{router, router_with_alert_words};
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
        Ok(database) => {
            let external_host = env::var("ZULIP_RUST_EXTERNAL_HOST")?;
            router_with_alert_words(&locale_dir, &database, &external_host).await?
        }
        Err(env::VarError::NotPresent) => router(&locale_dir)?,
        Err(error) => return Err(error.into()),
    };
    let listener = TcpListener::bind(address).await?;
    eprintln!("Zulip HTTP listening on {}", listener.local_addr()?);
    axum::serve(listener, app).await?;
    Ok(())
}
