use std::{fs, io, net::SocketAddr, path::Path, sync::Arc};

use serde::Deserialize;

use crate::memcached;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    address: SocketAddr,
    key_prefixes: Vec<String>,
}

impl Manifest {
    fn load(path: &Path) -> io::Result<Self> {
        if !fs::metadata(path)?.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Alert-word cache manifest must be a file",
            ));
        }
        let manifest: Self = serde_json::from_slice(&fs::read(path)?)?;
        if manifest.key_prefixes.is_empty() || manifest.key_prefixes.iter().any(String::is_empty) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Missing alert-word cache prefixes",
            ));
        }
        // Check the longest possible primary key before accepting the namespace.
        for key in manifest.keys_for_realm(i32::MAX) {
            memcached::validate_key(&key)?;
        }
        Ok(manifest)
    }

    fn keys_for_realm(&self, realm_id: i32) -> impl Iterator<Item = String> + '_ {
        ["realm_alert_words", "realm_alert_words_automaton"]
            .into_iter()
            .flat_map(move |name| {
                self.key_prefixes
                    .iter()
                    .map(move |prefix| format!("{prefix}{name}:{realm_id}"))
            })
    }
}

pub(super) async fn ready(path: Arc<Path>) -> bool {
    tokio::task::spawn_blocking(move || {
        Manifest::load(&path)
            .and_then(|manifest| memcached::ping(manifest.address))
            .unwrap_or(false)
    })
    .await
    .unwrap_or(false)
}

pub(super) async fn invalidate(path: &Path, realm_id: i32) -> io::Result<()> {
    let path = path.to_owned();
    tokio::task::spawn_blocking(move || {
        let manifest = Manifest::load(&path)?;
        let keys = manifest.keys_for_realm(realm_id).collect::<Vec<_>>();
        memcached::delete_keys(manifest.address, &keys)
    })
    .await?
}

#[cfg(test)]
mod tests {
    use super::Manifest;

    #[test]
    fn builds_both_wire_keys_for_each_deployment_prefix() {
        let manifest: Manifest = serde_json::from_str(
            r#"{
                "address":"127.0.0.1:11211",
                "key_prefixes":["django:7:current:","django:7:previous:"]
            }"#,
        )
        .unwrap();
        assert_eq!(
            manifest.keys_for_realm(42).collect::<Vec<_>>(),
            [
                "django:7:current:realm_alert_words:42",
                "django:7:previous:realm_alert_words:42",
                "django:7:current:realm_alert_words_automaton:42",
                "django:7:previous:realm_alert_words_automaton:42",
            ],
        );
    }
}
