use std::{env, error::Error, net::SocketAddr, path::PathBuf};

use _zulip_backend::http::router;
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
    let app = router(&locale_dir)?;
    let listener = TcpListener::bind(address).await?;
    eprintln!("Zulip HTTP listening on {}", listener.local_addr()?);
    axum::serve(listener, app).await?;
    Ok(())
}
