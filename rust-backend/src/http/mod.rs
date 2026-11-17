use std::{error::Error, path::Path};

use axum::{Router, routing::get};

mod alert_words;
mod compatibility;
mod locale;

pub fn router(locale_dir: &Path) -> Result<Router, Box<dyn Error>> {
    Ok(compatibility_router(locale::Locales::load(locale_dir)?))
}

pub async fn router_with_alert_words(
    locale_dir: &Path,
    database: &str,
    external_host: &str,
) -> Result<Router, Box<dyn Error>> {
    let locales = locale::Locales::load(locale_dir)?;
    let alert_words =
        alert_words::AlertWords::connect(database, external_host, locales.clone()).await?;
    Ok(compatibility_router(locales).merge(
        Router::new()
            .route(alert_words::PATH, get(alert_words::get))
            .with_state(alert_words),
    ))
}

fn compatibility_router(locales: locale::Locales) -> Router {
    Router::new()
        .route("/compatibility", get(compatibility::get))
        .with_state(locales)
}
