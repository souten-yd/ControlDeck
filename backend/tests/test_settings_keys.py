"""Settings →「API キー」: 外部のサービスのキーを 1 か所で見る・入れる・消す。

目的:
- クラウドの AI（OpenRouter など）・特許の公開データ（EPO・USPTO・特許庁）が 1 つの一覧に出ること。
  Hugging Face と Workflow のシークレットは出さない（2026-11-21 利用者「モデルのダウンロードと Workflow のシークレットは設定不要」）。
- **今あるキーは置き場を変えずにそのまま見える**（移すときに消さない）。値は返さず、設定済みか・末尾 4 文字だけ。
- ここで入れたキーは元の置き場に入り、元の機能（Models・Patent）がそのまま使えること。空の値は「変えない」。
- 消すのは明示の DELETE だけ。管理の権限が要ること。

根拠: 2026-11-21 利用者「EPO や特許庁の API のキーはどこで設定できる？設定の中にキーをまとめてほしい。OpenRouter のキーも。
今あるキーを全部移動してほしいので、消さないように」。
"""
from __future__ import annotations

from tests.conftest import CSRF_HEADERS

API = "/api/v1/settings/keys"


def _by_id(body):
    return {k["id"]: k for k in body["keys"]}


def test_existing_keys_show_up_without_moving_or_exposing_them(admin_client):
    from app.knowledge import sources
    from app.models_mgmt import cloud

    cloud.save_provider("openrouter", {"api_key": "sk-or-existing-1234"})
    sources.set_keys({"epo_key": "epo-existing", "epo_secret": "epo-secret-existing"})

    body = admin_client.get(API).json()
    keys = _by_id(body)
    assert {"ai", "patent", "research", "dev"} == {g["id"] for g in body["groups"]}
    assert not any(k["id"] == "hf" or k["id"].startswith("secret:") for k in body["keys"])
    assert keys["cloud:openrouter"]["configured"] and keys["cloud:openrouter"]["hint"] == "••••1234"
    assert keys["source:epo"]["configured"] and not keys["source:uspto"]["configured"]
    assert [f["configured"] for f in keys["source:jpo"]["fields"]] == [False, False, False]
    text = str(body)
    for secret in ("sk-or-existing", "epo-existing", "epo-secret-existing"):
        assert secret not in text
    # 元の置き場はそのまま（消していない）
    assert cloud.status()["providers"][0]["configured"] and sources._secret("epo_key") == "epo-existing"


def test_saving_here_writes_to_the_original_store_and_blank_means_unchanged(admin_client):
    from app.knowledge import sources
    from app.models_mgmt import cloud

    r = admin_client.put(f"{API}/source:jpo", json={"values": {"jpo_token_url": "https://ip-data.jpo.go.jp/auth/token",
                                                              "jpo_id": "me", "jpo_password": ""}}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    assert sources._secret("jpo_id") == "me" and sources._secret("jpo_password") == ""
    admin_client.put(f"{API}/cloud:anthropic", json={"values": {"api_key": "sk-ant-new-9876"}}, headers=CSRF_HEADERS)
    assert next(p for p in cloud.status()["providers"] if p["id"] == "anthropic")["key_hint"] == "9876"
    assert admin_client.put(f"{API}/cloud:anthropic", json={"values": {"api_key": "  "}}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(f"{API}/source:epo", json={"values": {"jpo_id": "x"}}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(f"{API}/hf", json={"values": {"token": "x"}}, headers=CSRF_HEADERS).status_code == 404
    assert admin_client.put(f"{API}/secret:X", json={"values": {"value": "x"}}, headers=CSRF_HEADERS).status_code == 404
    assert admin_client.put(f"{API}/nope", json={"values": {"x": "y"}}, headers=CSRF_HEADERS).status_code == 404


def test_delete_is_explicit(admin_client):
    from app.knowledge import sources

    sources.set_keys({"uspto_key": "uspto-to-delete"})
    assert admin_client.delete(f"{API}/source:uspto", headers=CSRF_HEADERS).status_code == 200
    assert sources._secret("uspto_key") == ""


def test_opencode_zen_key_is_listed_saved_and_deleted_without_exposing_it(admin_client):
    """目的: 「開発」の組に OpenCode Zen の項目が出て、保存・削除でき、値は返さず末尾 4 文字だけ。
    根拠: 2026-11-21 Zen の無料モデルは OpenCode の中からしか使えず、箱の OpenCode へ直接キーを渡す（HANDOFF 不具合と課題 5）。"""
    from app.integrations.opencode import zen

    body = admin_client.get(API).json()
    assert "dev" in {g["id"] for g in body["groups"]}
    item = _by_id(body)["dev:opencode-zen"]
    assert item["group"] == "dev" and not item["configured"] and item["signup"] == "https://opencode.ai/zen"
    secret = "zen-secret-value-wxyz"
    r = admin_client.put(f"{API}/dev:opencode-zen", json={"values": {"api_key": secret}}, headers=CSRF_HEADERS)
    assert r.status_code == 200, r.text
    item = _by_id(r.json())["dev:opencode-zen"]
    assert item["configured"] and item["hint"] == "••••wxyz" and secret not in str(r.json())
    assert zen.get() == secret and secret not in zen._path().read_text()
    assert admin_client.put(f"{API}/dev:opencode-zen", json={"values": {"x": "y"}}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(f"{API}/dev:opencode-zen", json={"values": {"api_key": "a b"}}, headers=CSRF_HEADERS).status_code == 422
    assert admin_client.put(f"{API}/dev:other", json={"values": {"api_key": "x"}}, headers=CSRF_HEADERS).status_code == 404
    r = admin_client.delete(f"{API}/dev:opencode-zen", headers=CSRF_HEADERS)
    assert r.status_code == 200 and not _by_id(r.json())["dev:opencode-zen"]["configured"] and zen.get() == ""
