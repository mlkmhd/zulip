use std::{error::Error, io, net::IpAddr, sync::Arc, time::Duration};

use tokio::sync::Mutex;
use tokio_postgres::{Client, Config, NoTls, config::Host};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

pub(super) struct Database {
    config: Config,
    read_only: bool,
    client: Mutex<Arc<Client>>,
}

impl Database {
    pub(super) async fn connect(dsn: &str, read_only: bool) -> Result<Self, Box<dyn Error>> {
        let config: Config = dsn.parse()?;
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
        let client = Self::open(&config, read_only).await?;
        Ok(Self {
            config,
            read_only,
            client: Mutex::new(Arc::new(client)),
        })
    }

    pub(super) async fn client(&self) -> io::Result<Arc<Client>> {
        let mut client = tokio::time::timeout(CONNECT_TIMEOUT, self.client.lock()).await?;
        if client.is_closed() {
            *client = Arc::new(Self::open(&self.config, self.read_only).await?);
        }
        Ok(Arc::clone(&client))
    }

    async fn open(config: &Config, read_only: bool) -> io::Result<Client> {
        tokio::time::timeout(CONNECT_TIMEOUT, async {
            let (client, connection) = config.connect(NoTls).await?;
            tokio::spawn(async move {
                if let Err(error) = connection.await {
                    eprintln!("PostgreSQL connection failed: {error}");
                }
            });
            client.batch_execute("SET statement_timeout = '5s'").await?;
            if read_only {
                client
                    .batch_execute("SET default_transaction_read_only = on")
                    .await?;
            }
            Ok::<_, tokio_postgres::Error>(client)
        })
        .await?
        .map_err(io::Error::other)
    }
}
