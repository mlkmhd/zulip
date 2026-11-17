use sha1_smol::Sha1;

pub fn member_hash(member_ids: &[i32]) -> String {
    let mut member_ids = member_ids.to_vec();
    member_ids.sort_unstable();
    member_ids.dedup();
    let key = member_ids
        .iter()
        .map(i32::to_string)
        .collect::<Vec<_>>()
        .join(",");
    Sha1::from(key).digest().to_string()
}

#[cfg(test)]
mod tests {
    use super::member_hash;

    #[test]
    fn matches_the_existing_hash_of_sorted_distinct_ids() {
        assert_eq!(member_hash(&[10, 2, 10, 1]), member_hash(&[1, 2, 10]));
        assert_eq!(
            member_hash(&[1, 2, 10]),
            "3faeb0fe3f2bdb01b06dc219c18a654f13db3c31"
        );
    }
}
