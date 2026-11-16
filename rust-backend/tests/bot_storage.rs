use _zulip_backend::bot_storage::storage_size_difference;

#[test]
fn applies_insertions_and_replacements_using_code_points() {
    let entries = [
        ("🦀".to_string(), "β".to_string(), 4),
        ("名".to_string(), "e\u{301}".to_string(), 0),
    ];
    assert_eq!(storage_size_difference(&entries), 1);
    assert_eq!(storage_size_difference(&entries[..1]), -2);
}
