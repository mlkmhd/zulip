use std::collections::HashSet;

use _zulip_backend::alert_words::{clean_alert_words, select_new_alert_words};

#[test]
fn trims_python_whitespace_without_changing_internal_spaces() {
    let words = ["\u{a0}two words\u{1e}", "\u{1c}\u{2003}", "\u{200b}word\t"].map(String::from);

    assert_eq!(
        clean_alert_words(&words).collect::<Vec<_>>(),
        ["two words", "\u{200b}word"],
    );
}

#[test]
fn existing_words_win_and_repeated_new_words_keep_the_last_spelling() {
    let existing = HashSet::from(["milk".to_owned()]);
    let words = [
        ("milk", "MILK"),
        ("tea", "tea"),
        ("cake", "cake"),
        ("tea", "TEA"),
    ];

    assert_eq!(
        select_new_alert_words(&existing, words.map(|(key, word)| (key.to_owned(), word))),
        ["TEA", "cake"],
    );
}
