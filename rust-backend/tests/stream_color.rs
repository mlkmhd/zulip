use std::collections::{HashMap, HashSet};

use _zulip_backend::stream_color::pick_colors;

#[test]
fn reserves_explicit_colors_before_assigning_automatic_colors() {
    let used = HashSet::from(["#fae589".to_owned()]);
    let explicit = HashMap::from([(90, "#76ce90".to_owned()), (91, "#a6c7e5".to_owned())]);

    assert_eq!(
        pick_colors(&used, &explicit, vec![2, 90, 1]),
        [(90, "#76ce90"), (1, "#a6c7e5"), (2, "#e79ab5")],
    );
}

#[test]
fn reuses_colors_by_recipient_id_when_the_palette_runs_out() {
    let explicit = HashMap::new();
    let assigned = pick_colors(&HashSet::new(), &explicit, (0..26).rev().collect());
    assert_eq!(assigned.len(), 26);
    assert_eq!(
        assigned[23..],
        [(23, "#a47462"), (24, "#76ce90"), (25, "#fae589")]
    );
}

#[test]
fn keeps_the_last_assignment_for_a_duplicate_id() {
    assert_eq!(
        pick_colors(&HashSet::new(), &HashMap::new(), vec![2, 1, 1]),
        [(1, "#fae589"), (2, "#a6c7e5")],
    );
}
