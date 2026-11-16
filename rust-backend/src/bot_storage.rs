pub fn storage_size_difference(entries: &[(String, String, u64)]) -> i128 {
    entries
        .iter()
        .map(|(key, value, previous_size)| {
            key.chars().count() as i128 + value.chars().count() as i128 - i128::from(*previous_size)
        })
        .sum()
}
