use super::*;
use serde_json::json;

#[test]
fn frames_require_whole_packed_rows_and_valid_hex() {
    assert!(check_frame(9, 2, "80ff00ff").is_ok());
    for (w, h, pixels) in [(0, 2, ""), (9, 2, "000000"), (1, 1, "xx")] {
        assert!(check_frame(w, h, pixels).is_err());
    }
}

#[test]
fn configuration_rejects_unknown_roles_or_empty_driver_names() {
    assert!(serde_json::from_value::<Registry>(json!({"other":{"name":"dummy"}})).is_err());
    let registry: Registry = serde_json::from_value(json!({"input":{"name":" "}})).unwrap();
    assert!(registry.validate().is_err());
}

#[tokio::test]
async fn headless_core_requires_no_bridge() {
    let registry = Registry::default();
    assert!(registry.probe().await.is_ok());
    assert!(registry.read_mv().await.is_err());
}
