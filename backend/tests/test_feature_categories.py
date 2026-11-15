"""オプション機能の画面での分け方（用途で分ける）。"""
from app.features import registry


def test_every_feature_has_a_known_category():
    for feature_id in registry.KNOWN_FEATURES:
        assert registry.category(feature_id) in registry.CATEGORIES


def test_opencode_tools_are_grouped_with_opencode():
    for feature_id in ("opencode-v2", "opencode-superpowers", "playwright-mcp", "context7-mcp", "ast-grep"):
        assert registry.category(feature_id) == "opencode", feature_id
    assert registry.category("strata") == "runtime"
    assert registry.category("media-forge") == "addon"
    assert registry.category("pyinstaller") == "tools"


def test_status_reports_the_category(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    assert registry.status("ast-grep")["category"] == "opencode"
