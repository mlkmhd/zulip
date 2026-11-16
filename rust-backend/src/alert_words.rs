use std::collections::{HashMap, HashSet};

pub fn clean_alert_words(words: &[String]) -> impl Iterator<Item = &str> {
    words
        .iter()
        .map(|word| {
            // Python also treats the four ASCII information separators as whitespace.
            word.trim_matches(|character: char| {
                character.is_whitespace() || matches!(character, '\u{1c}'..='\u{1f}')
            })
        })
        .filter(|word| !word.is_empty())
}

/// Select new words using lowercase keys computed by the caller.
/// For repeat keys, keep the first position and the last spelling.
pub fn select_new_alert_words<W>(
    existing: &HashSet<String>,
    words: impl IntoIterator<Item = (String, W)>,
) -> Vec<W> {
    let mut positions = HashMap::new();
    let mut result = Vec::new();

    for (key, word) in words {
        if !existing.contains(&key) {
            let position = *positions.entry(key).or_insert(result.len());
            if position == result.len() {
                result.push(word);
            } else {
                result[position] = word;
            }
        }
    }

    result
}
