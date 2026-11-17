use std::{
    error::Error,
    net::IpAddr,
    sync::Arc,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use axum::http::{HeaderMap, HeaderValue};
use redis::{
    Script,
    aio::{ConnectionManager, ConnectionManagerConfig},
};

const RATE_LIMIT_SCRIPT: &str = include_str!("../../../zerver/lib/rate_limiter.lua");
const NO_RULES: [(u64, u64); 1] = [(1, 9999)];

#[derive(Clone, Copy)]
pub enum ClientIpSource {
    Unspecified,
    Peer,
    Proxy,
}

impl ClientIpSource {
    fn is_local(self, peer: Option<IpAddr>, headers: &HeaderMap) -> bool {
        let is_local = |address: IpAddr| match address {
            IpAddr::V4(ip) => ip == std::net::Ipv4Addr::LOCALHOST,
            IpAddr::V6(ip) => ip == std::net::Ipv6Addr::LOCALHOST,
        };
        match self {
            Self::Unspecified => false,
            Self::Peer => peer.is_some_and(is_local),
            Self::Proxy => {
                // Only an explicitly configured local proxy may vouch for the client IP.
                let mut values = headers.get_all("x-real-ip").iter();
                peer.is_some_and(is_local)
                    && values
                        .next()
                        .is_some_and(|value| matches!(value.as_bytes(), b"127.0.0.1" | b"::1"))
                    && values.next().is_none()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selects_only_the_configured_client_address() {
        let local = Some("127.0.0.1".parse().unwrap());
        let other_loopback = Some("127.0.0.2".parse().unwrap());
        let remote = Some("192.0.2.1".parse().unwrap());
        let mut headers = HeaderMap::new();

        assert!(ClientIpSource::Peer.is_local(local, &headers));
        assert!(!ClientIpSource::Peer.is_local(other_loopback, &headers));
        assert!(!ClientIpSource::Proxy.is_local(local, &headers));

        headers.insert("x-real-ip", HeaderValue::from_static("::1"));
        assert!(!ClientIpSource::Unspecified.is_local(local, &headers));
        assert!(!ClientIpSource::Peer.is_local(remote, &headers));
        assert!(ClientIpSource::Proxy.is_local(local, &headers));
        assert!(!ClientIpSource::Proxy.is_local(remote, &headers));
        assert!(!ClientIpSource::Proxy.is_local(None, &headers));

        headers.append("x-real-ip", HeaderValue::from_static("127.0.0.1"));
        assert!(!ClientIpSource::Proxy.is_local(local, &headers));
        headers.insert(
            "x-real-ip",
            HeaderValue::from_static("127.0.0.1, 192.0.2.1"),
        );
        assert!(!ClientIpSource::Proxy.is_local(local, &headers));
    }
}

#[derive(Clone)]
pub(super) struct RateLimiter {
    connection: ConnectionManager,
    script: Arc<Script>,
    rules: Arc<[(u64, u64)]>,
    prefix: Arc<str>,
    client_ip_source: ClientIpSource,
    debug_rate_limiting: bool,
}

impl RateLimiter {
    pub(super) async fn ping(&self) -> bool {
        redis::cmd("PING")
            .query_async::<String>(&mut self.connection.clone())
            .await
            .is_ok_and(|response| response == "PONG")
    }

    pub(super) async fn connect(
        settings: super::RateLimitConfig<'_>,
    ) -> Result<Self, Box<dyn Error>> {
        let mut rules: Vec<(u64, u64)> = serde_json::from_str(settings.rules)?;
        if rules.is_empty() {
            rules.extend(NO_RULES);
        }
        validate_rules(&rules)?;
        let client = redis::Client::open(settings.url)?;
        if client.get_connection_info().redis_settings().db() != 0 {
            return Err("Zulip's shared limiter uses Redis database 0".into());
        }
        let config = ConnectionManagerConfig::new()
            .set_number_of_retries(1)
            .set_connection_timeout(Some(Duration::from_secs(5)))
            .set_response_timeout(Some(Duration::from_secs(5)));
        let connection = ConnectionManager::new_with_config(client, config).await?;
        Ok(Self {
            connection,
            script: Arc::new(Script::new(RATE_LIMIT_SCRIPT)),
            rules: rules.into(),
            prefix: settings.key_prefix.into(),
            client_ip_source: settings.client_ip_source,
            debug_rate_limiting: settings.debug_rate_limiting,
        })
    }

    pub(super) fn exempts_internal_client(
        &self,
        client: &str,
        peer: Option<IpAddr>,
        headers: &HeaderMap,
    ) -> bool {
        client.eq_ignore_ascii_case("internal")
            && (self.debug_rate_limiting || self.client_ip_source.is_local(peer, headers))
    }

    pub(super) async fn check(
        &self,
        user_id: i32,
        override_rules: &str,
    ) -> Result<RateLimit, Box<dyn Error + Send + Sync>> {
        let custom_rules;
        let rules = if override_rules.is_empty() {
            &self.rules
        } else {
            custom_rules = override_rules
                .split(',')
                .map(|rule| {
                    let (window, limit) = rule.split_once(':').ok_or("Invalid api_by_user rule")?;
                    Ok((window.trim().parse()?, limit.trim().parse()?))
                })
                .collect::<Result<Vec<_>, Box<dyn Error + Send + Sync>>>()?;
            validate_rules(&custom_rules)?;
            &custom_rules[..]
        };
        let prefix = format!(
            "{}ratelimit:RateLimitedUser:{user_id}:api_by_user",
            self.prefix
        );
        let mut invocation = self.script.prepare_invoke();
        invocation
            .key(format!("{prefix}:block"))
            .key(format!("{prefix}:gcra"))
            .arg(rules.len());
        for (window, limit) in rules {
            invocation.arg(window).arg(limit);
        }
        // The script reads Redis time so both backends maintain the same bucket.
        let (blocked, retry_after, remaining, reset): (u8, f64, u64, f64) = invocation
            .invoke_async(&mut self.connection.clone())
            .await?;
        let limit = rules.last().expect("at least one rule").1;
        Ok(RateLimit {
            limit,
            remaining,
            reset,
            retry_after: (blocked == 1).then_some(retry_after),
        })
    }
}

fn validate_rules(rules: &[(u64, u64)]) -> Result<(), &'static str> {
    if rules
        .iter()
        .any(|&(window, limit)| window == 0 || limit == 0)
    {
        return Err("api_by_user windows and limits must be positive");
    }
    Ok(())
}

pub(super) struct RateLimit {
    limit: u64,
    remaining: u64,
    reset: f64,
    pub(super) retry_after: Option<f64>,
}

impl RateLimit {
    pub(super) fn set_headers(&self, headers: &mut HeaderMap) {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time after epoch");
        let reset = (now.as_secs_f64() + self.retry_after.unwrap_or(self.reset)) as u64;
        headers.insert("x-ratelimit-limit", HeaderValue::from(self.limit));
        headers.insert("x-ratelimit-remaining", HeaderValue::from(self.remaining));
        headers.insert("x-ratelimit-reset", HeaderValue::from(reset));
    }
}
