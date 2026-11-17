use std::{collections::HashMap, fs, io, path::Path};

use serde::Deserialize;

pub(super) const DEFAULT_QUEUE: &str = "notify_tornado";
const PORT_QUEUE_PREFIX: &str = "notify_tornado_port_";

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct TornadoRouting {
    realm_queues: HashMap<i32, Vec<String>>,
}

impl TornadoRouting {
    pub fn load(path: &Path) -> io::Result<Self> {
        if !fs::metadata(path)?.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Expected a Tornado routing file",
            ));
        }
        Self::parse(&fs::read(path)?)
    }

    fn parse(bytes: &[u8]) -> io::Result<Self> {
        let routing: Self = serde_json::from_slice(bytes)?;
        if routing.realm_queues.iter().any(|(realm_id, queues)| {
            *realm_id <= 0 || queues.is_empty() || queues.iter().any(|queue| !valid_queue(queue))
        }) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Invalid Tornado queue routing",
            ));
        }
        Ok(routing)
    }

    pub fn queues(&self) -> impl Iterator<Item = &String> {
        self.realm_queues.values().flatten()
    }

    pub fn queue_for_user(&self, realm_id: i32, user_id: i32) -> Option<&str> {
        let queues = self.realm_queues.get(&realm_id)?;
        let user_id = usize::try_from(user_id).ok()?;
        Some(&queues[user_id % queues.len()])
    }
}

fn valid_queue(queue: &str) -> bool {
    queue == DEFAULT_QUEUE
        || queue.strip_prefix(PORT_QUEUE_PREFIX).is_some_and(|suffix| {
            !suffix.starts_with('0')
                && suffix.bytes().all(|byte| byte.is_ascii_digit())
                && suffix.parse::<u16>().is_ok()
        })
}

#[cfg(test)]
mod tests {
    use super::TornadoRouting;

    #[test]
    fn routes_users_using_the_order_of_each_realms_queues() {
        let routing = TornadoRouting::parse(
            br#"{
            "realm_queues": {
                "1": ["notify_tornado_port_9984", "notify_tornado_port_9983"],
                "2": ["notify_tornado_port_9983", "notify_tornado_port_9984"]
            }
        }"#,
        )
        .unwrap();
        assert_eq!(
            routing.queue_for_user(1, 10),
            Some("notify_tornado_port_9984")
        );
        assert_eq!(
            routing.queue_for_user(1, 11),
            Some("notify_tornado_port_9983")
        );
        assert_eq!(
            routing.queue_for_user(2, 10),
            Some("notify_tornado_port_9983")
        );
        assert_eq!(routing.queue_for_user(3, 10), None);
    }

    #[test]
    fn rejects_empty_assignments_and_invalid_queue_names() {
        for invalid in [
            r#"[]"#,
            r#"["user_activity"]"#,
            r#"["notify_tornado_port_0"]"#,
            r#"["notify_tornado_port_65536"]"#,
        ] {
            let data = format!(r#"{{"realm_queues":{{"1":{invalid}}}}}"#);
            assert!(TornadoRouting::parse(data.as_bytes()).is_err());
        }
    }
}
