use std::{error::Error, path::Path};

use axum::{Router, routing::get};

mod compatibility;
mod locale;

pub fn router(locale_dir: &Path) -> Result<Router, Box<dyn Error>> {
    let messages = locale::MissingUserAgentMessages::load(locale_dir)?;
    Ok(Router::new()
        .route("/compatibility", get(compatibility::get))
        .with_state(messages))
}
