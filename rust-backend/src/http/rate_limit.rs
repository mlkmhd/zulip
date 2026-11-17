use std::{
    error::Error,
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

#[derive(Clone)]
pub(super) struct RateLimiter {
    connection: ConnectionManager,
    script: Arc<Script>,
    rules: Arc<[(u64, u64)]>,
    prefix: Arc<str>,
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
        })
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
