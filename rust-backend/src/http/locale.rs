use std::{
    collections::HashMap,
    error::Error,
    fs, io,
    path::Path,
    sync::{Arc, LazyLock},
};

use axum::http::{HeaderMap, header};
use gettext::Catalog;
use regex::Regex;

// The existing endpoint uses Django's default LANGUAGES, including languages
// for which Zulip does not yet have its own translated message.
const SUPPORTED_LANGUAGES: &str = "af ar ar-dz ast az bg be bn br bs ca ckb cs cy da de dsb el en
    en-au en-gb eo es es-ar es-co es-mx es-ni es-ve et eu fa fi fr fy ga gd gl he hi hr hsb hu hy
    ia id ig io is it ja ka kab kk km kn ko ky lb lt lv mk ml mn mr ms my nb ne nl nn os pa pl pt
    pt-br ro ru sk sl sq sr sr-latn sv sw ta te tg th tk tr tt udm ug uk ur uz vi zh-hans zh-hant";
const LANGUAGE_HEADER_LIMIT: usize = 500;
static LANGUAGE_RANGE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(
        r"(?ix)([a-z]{1,8}(?:-[a-z0-9]{1,8})*|\*)
        (?:\s*;\s*q=(0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?))?
        (?:\s*,\s*|$)",
    )
    .unwrap()
});

#[derive(Clone)]
pub(super) struct Locales(Arc<HashMap<String, Catalog>>);

impl Locales {
    pub(super) fn load(directory: &Path) -> Result<Self, Box<dyn Error>> {
        let mut messages = HashMap::new();
        for entry in fs::read_dir(directory)? {
            let entry = entry?;
            if !entry.file_type()?.is_dir() {
                continue;
            }
            let path = entry.path().join("LC_MESSAGES/django.mo");
            let file = match fs::File::open(&path) {
                Ok(file) => file,
                Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
                Err(error) => return Err(error.into()),
            };
            let locale = entry
                .file_name()
                .to_string_lossy()
                .replace('_', "-")
                .to_ascii_lowercase();
            let locale = match locale.as_str() {
                // Zulip's catalog names for these predate Django's current language codes.
                "pt" => "pt-br".to_owned(),
                "pt-pt" => "pt".to_owned(),
                _ => locale,
            };
            let catalog =
                Catalog::parse(file).map_err(|error| format!("{}: {error}", path.display()))?;
            messages.insert(locale, catalog);
        }
        Ok(Self(Arc::new(messages)))
    }

    pub(super) fn for_request(headers: &HeaderMap) -> &'static str {
        if let Some(cookie) = headers
            .get(header::COOKIE)
            .and_then(|value| value.to_str().ok())
        {
            let language = cookie
                .split(';')
                .filter_map(|part| part.trim().split_once('='))
                .filter(|(name, _)| *name == "django_language")
                .map(|(_, value)| value.trim_matches('"'))
                .next_back();
            if let Some(language) = language.and_then(Self::supported_language) {
                return language;
            }
        }
        if let Some(value) = headers
            .get(header::ACCEPT_LANGUAGE)
            .and_then(|value| value.to_str().ok())
        {
            for language in preferences(value) {
                if language == "*" {
                    break;
                }
                if let Some(language) = Self::supported_language(language) {
                    return language;
                }
            }
        }
        "en"
    }

    fn supported_language(language: &str) -> Option<&'static str> {
        let language = if language.len() > LANGUAGE_HEADER_LIMIT {
            language.get(..LANGUAGE_HEADER_LIMIT)?.rsplit_once('-')?.0
        } else {
            language
        };
        let tag = language.to_ascii_lowercase();
        let tag = match tag.as_str() {
            "zh-cn" | "zh-my" | "zh-sg" => "zh-hans",
            "zh-hk" | "zh-mo" | "zh-tw" => "zh-hant",
            _ => &tag,
        };
        let variants =
            std::iter::successors(Some(tag), |tag| tag.rsplit_once('-').map(|(tag, _)| tag));
        let supported = SUPPORTED_LANGUAGES.split_ascii_whitespace();
        variants
            .clone()
            .find_map(|tag| supported.clone().find(|code| *code == tag))
            .or_else(|| {
                let base = variants.last()?;
                supported.clone().find(|code| {
                    code.strip_prefix(base)
                        .is_some_and(|rest| rest.starts_with('-'))
                })
            })
    }

    pub(super) fn gettext<'a>(&'a self, language: &str, message: &'a str) -> &'a str {
        let catalog = self.0.get(language).or_else(|| {
            let (base, _) = language.rsplit_once('-')?;
            self.0.get(base)
        });
        catalog.map_or(message, |catalog| catalog.gettext(message))
    }
}

fn preferences(value: &str) -> Vec<&str> {
    let value = if value.len() > LANGUAGE_HEADER_LIMIT {
        let Some((prefix, _)) = value[..LANGUAGE_HEADER_LIMIT].rsplit_once(',') else {
            return Vec::new();
        };
        prefix
    } else {
        value
    };
    let mut preferences = Vec::new();
    let mut offset = 0;
    for captures in LANGUAGE_RANGE.captures_iter(value) {
        let matched = captures.get(0).expect("the whole match is present");
        if matched.start() != offset {
            return Vec::new();
        }
        offset = matched.end();
        let tag = captures
            .get(1)
            .expect("the language range is present")
            .as_str();
        let weight = captures
            .get(2)
            .map_or(1.0, |q| q.as_str().parse::<f32>().expect("matched weight"));
        preferences.push((tag, weight));
    }
    if offset != value.len() {
        return Vec::new();
    }
    preferences.sort_by(|(_, a), (_, b)| b.total_cmp(a));
    preferences.into_iter().map(|(tag, _)| tag).collect()
}
