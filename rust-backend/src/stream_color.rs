use std::collections::{HashMap, HashSet};

const STREAM_ASSIGNMENT_COLORS: [&str; 24] = [
    "#76ce90", "#fae589", "#a6c7e5", "#e79ab5", "#bfd56f", "#f4ae55", "#b0a5fd", "#addfe5",
    "#f5ce6e", "#c2726a", "#94c849", "#bd86e5", "#ee7e4a", "#a6dcbf", "#95a5fd", "#53a063",
    "#9987e1", "#e4523d", "#c2c2c2", "#4f8de4", "#c6a8ad", "#e7cc4d", "#c8bebf", "#a47462",
];

/// Select display colors for new channel subscriptions. Explicit assignments
/// come first, followed by automatic assignments; each group is sorted by ID.
pub fn pick_colors<'a>(
    used_colors: &HashSet<String>,
    color_map: &'a HashMap<i64, String>,
    mut recipient_ids: Vec<i64>,
) -> Vec<(i64, &'a str)> {
    recipient_ids.sort_unstable();
    let mut result = Vec::with_capacity(recipient_ids.len());
    let mut automatic_ids = Vec::new();
    let mut explicit_colors = HashSet::new();

    for recipient_id in recipient_ids {
        if let Some(color) = color_map.get(&recipient_id) {
            explicit_colors.insert(color.as_str());
            if result.last().map(|&(id, _)| id) != Some(recipient_id) {
                result.push((recipient_id, color.as_str()));
            }
        } else {
            automatic_ids.push(recipient_id);
        }
    }

    let mut available_colors = STREAM_ASSIGNMENT_COLORS
        .into_iter()
        .filter(|color| !used_colors.contains(*color) && !explicit_colors.contains(color));

    let mut automatic_ids = automatic_ids.into_iter().peekable();
    while let Some(recipient_id) = automatic_ids.next() {
        let color = available_colors.next().unwrap_or_else(|| {
            // Once the palette runs out, reuse a color based on the recipient ID.
            STREAM_ASSIGNMENT_COLORS
                [recipient_id.rem_euclid(STREAM_ASSIGNMENT_COLORS.len() as i64) as usize]
        });
        // Repeated IDs consume palette slots, but only the last color is kept.
        if automatic_ids.peek() != Some(&recipient_id) {
            result.push((recipient_id, color));
        }
    }

    result
}
