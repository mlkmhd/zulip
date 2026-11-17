use axum::http::{HeaderMap, Uri, header};
use ctutils::CtEq;

const SECRET_LENGTH: usize = 32;
const ALPHABET: &[u8] = b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

#[derive(Clone, Copy)]
pub enum WebPostScheme {
    Http,
    Https,
}

impl WebPostScheme {
    fn check_origin(self, headers: &HeaderMap) -> Result<(), String> {
        let host = headers
            .get(header::HOST)
            .and_then(|value| value.to_str().ok())
            .expect("browser dispatcher already validated Host");
        if let Some(origin) = headers.get(header::ORIGIN) {
            let scheme = match self {
                Self::Http => "http",
                Self::Https => "https",
            };
            if origin.as_bytes() != format!("{scheme}://{host}").as_bytes() {
                let origin = String::from_utf8_lossy(origin.as_bytes());
                return Err(format!(
                    "Origin checking failed - {origin} does not match any trusted origins."
                ));
            }
        } else if matches!(self, Self::Https) {
            let referer = headers
                .get(header::REFERER)
                .ok_or("Referer checking failed - no Referer.")?;
            let value = referer.to_str().unwrap_or_default();
            let uri = value
                .split('#')
                .next()
                .and_then(|value| value.parse::<Uri>().ok())
                .filter(|uri| uri.scheme().is_some() && uri.authority().is_some())
                .ok_or("Referer checking failed - Referer is malformed.")?;
            if uri.scheme_str() != Some("https") {
                return Err(
                    "Referer checking failed - Referer is insecure while host is secure.".into(),
                );
            }
            if !uri
                .authority()
                .is_some_and(|authority| authority.as_str().eq_ignore_ascii_case(host))
            {
                return Err(format!(
                    "Referer checking failed - {value} does not match any trusted origins."
                ));
            }
        }
        Ok(())
    }

    pub(super) fn validate(
        self,
        headers: &HeaderMap,
        cookie: Option<&str>,
        form_token: Option<&str>,
    ) -> Result<(), String> {
        self.check_origin(headers)?;
        let cookie = cookie.ok_or("CSRF cookie not set.")?;
        let secret = token_secret(cookie).map_err(|reason| format!("CSRF cookie {reason}."))?;
        let (token, source) = if let Some(token) = form_token.filter(|token| !token.is_empty()) {
            (token, "POST")
        } else {
            let token = headers.get("x-csrftoken").ok_or("CSRF token missing.")?;
            (
                token.to_str().unwrap_or("\u{fffd}"),
                "the 'X-Csrftoken' HTTP header",
            )
        };
        let token =
            token_secret(token).map_err(|reason| format!("CSRF token from {source} {reason}."))?;
        if !bool::from(secret.ct_eq(&token)) {
            return Err(format!("CSRF token from {source} incorrect."));
        }
        Ok(())
    }
}

pub(super) fn token_format(value: &str) -> Result<(), &'static str> {
    if !matches!(value.chars().count(), 32 | 64) {
        return Err("has incorrect length");
    }
    if !value.bytes().all(|byte| byte.is_ascii_alphanumeric()) {
        return Err("has invalid characters");
    }
    Ok(())
}

fn token_secret(value: &str) -> Result<[u8; SECRET_LENGTH], &'static str> {
    token_format(value)?;
    let bytes = value.as_bytes();
    let mut secret = [0; SECRET_LENGTH];
    if bytes.len() == SECRET_LENGTH {
        secret.copy_from_slice(bytes);
    } else {
        let (mask, cipher) = bytes.split_at(SECRET_LENGTH);
        for ((output, mask_byte), cipher_byte) in secret.iter_mut().zip(mask).zip(cipher) {
            let mask = alphabet_index(*mask_byte);
            let cipher = alphabet_index(*cipher_byte);
            *output = ALPHABET[(cipher + ALPHABET.len() - mask) % ALPHABET.len()];
        }
    }
    Ok(secret)
}

fn alphabet_index(byte: u8) -> usize {
    match byte {
        b'a'..=b'z' => usize::from(byte - b'a'),
        b'A'..=b'Z' => usize::from(byte - b'A') + 26,
        b'0'..=b'9' => usize::from(byte - b'0') + 52,
        _ => unreachable!("validated CSRF token alphabet"),
    }
}
