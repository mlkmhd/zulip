use _zulip_backend::draft_text::{BodyError, normalize_body, normalize_topic};

#[test]
fn normalizes_body_with_python_whitespace_and_character_limits() {
    let body = normalize_body("\n  📝سلام🟢\u{1f}\u{3000}", 25).unwrap();
    assert_eq!(body, "  📝سلام🟢");
    let body = normalize_body(&"📝".repeat(24), 22).unwrap().into_owned();
    assert_eq!(body, "📝📝\n[message truncated]");
    let body = normalize_body("📝📝", 1).unwrap();
    assert_eq!(body, "\n[message truncated]");
}

#[test]
fn rejects_body_nul_before_truncation_and_topic_nul_after_truncation() {
    assert_eq!(normalize_body("\n\u{1e} ", 40), Err(BodyError::Empty));
    assert_eq!(
        normalize_body(&format!("{}\0", "📝".repeat(40)), 25),
        Err(BodyError::NullByte),
    );
    assert_eq!(normalize_topic("📝xyz\0", 4).unwrap(), "📝...");
    assert_eq!(normalize_topic("\0📝xyz", 4), None);
    assert_eq!(normalize_topic(" 📝 ", 4).unwrap(), " 📝 ");
}
