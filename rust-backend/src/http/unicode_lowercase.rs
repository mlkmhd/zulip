use std::{collections::HashMap, fs, io, path::Path};

use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct UnicodeLowercase {
    mappings: HashMap<char, String>,
    cased: Vec<[u32; 2]>,
    case_ignorable: Vec<[u32; 2]>,
}

impl UnicodeLowercase {
    pub fn load(path: &Path) -> io::Result<Self> {
        if !fs::metadata(path)?.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Expected a lowercase table file",
            ));
        }
        let tables: Self = serde_json::from_slice(&fs::read(path)?)?;
        if !valid_ranges(&tables.cased)
            || !valid_ranges(&tables.case_ignorable)
            || tables.lower("AZİΣ AΣ AΣ'A") != "azi\u{307}ς aς aσ'a"
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "Invalid Unicode lowercase tables",
            ));
        }
        Ok(tables)
    }

    pub fn lower(&self, value: &str) -> String {
        let mut result = String::with_capacity(value.len());
        let mut chars = value.chars();
        let mut preceded_by_cased = false;
        while let Some(ch) = chars.next() {
            if ch == 'Σ'
                && preceded_by_cased
                && !chars
                    .clone()
                    .find(|next| !in_ranges(&self.case_ignorable, *next))
                    .is_some_and(|next| in_ranges(&self.cased, next))
            {
                result.push('ς');
            } else if let Some(lowercase) = self.mappings.get(&ch) {
                result.push_str(lowercase);
            } else {
                result.push(ch);
            }
            if !in_ranges(&self.case_ignorable, ch) {
                preceded_by_cased = in_ranges(&self.cased, ch);
            }
        }
        result
    }
}

fn valid_ranges(ranges: &[[u32; 2]]) -> bool {
    ranges
        .iter()
        .all(|[start, end]| start <= end && *end <= u32::from(char::MAX))
        && ranges.windows(2).all(|pair| pair[0][1] < pair[1][0])
}

fn in_ranges(ranges: &[[u32; 2]], ch: char) -> bool {
    let codepoint = u32::from(ch);
    let index = ranges.partition_point(|[_, end]| *end < codepoint);
    ranges
        .get(index)
        .is_some_and(|[start, _]| *start <= codepoint)
}
