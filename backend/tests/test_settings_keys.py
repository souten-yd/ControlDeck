"""Settings →「API キー」: 外部のサービスのキーを 1 か所で見る・入れる・消す。

目的:
- クラウドの AI（OpenRouter など）・Hugging Face・特許の公開データ（EPO・USPTO・特許庁）・Workflow のシークレットが 1 つの一覧に出ること。
- **今あるキーは置き場を変えずにそのまま見える**（移すときに消さない）。値は返さず、設定済みか・末尾 4 文字だけ。
- ここで入れたキーは元の置き場に入り、元の機能（Models・Patent・Workflow）がそのまま使えること。空の値は「変えない」。
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
    from app.models_mgmt import cloud, hf

    cloud.save_provider("openrouter", {"api_key": "sk-or-existing-1234"})
    sources.set_keys({"epo_key": "epo-existing", "epo_secret": "epo-secret-existing"})
    hf.set_token("hf_existing_token")
    admin_client.put("/api/v1/workflows-secrets/SLACK_TOKEN", json={"value": "xoxb-existing-settings-test"}, headers=CSRF_HEADERS)

    body = admin_client.get(API).json()
    keys = _by_id(body)
    assert {"ai", "patent", "models", "workflow"} == {g["id"] for g in body["groups"]}
    assert keys["cloud:openrouter"]["configured"] and keys["cloud:openrouter"]["hint"] == "••••1234"
    assert keys["source:epo"]["configured"] and not keys["source:uspto"]["configured"]
    assert [f["configured"] for f in keys["source:jpo"]["fields"]] == [False, False, False]
    assert keys["hf"]["configured"] and keys["secret:SLACK_TOKEN"]["configured"]
    text = str(body)
    for secret in ("sk-or-existing", "epo-existing", "epo-secret-existing", "hf_existing_token", "xoxb-existing-settings-test"):
        assert secret not in text
    # 元の置き場はそのまま（消していない）
    assert cloud.status()["providers"][0]["configured"] and sources._secret("epo_key") == "epo-existing" and hf.has_token()
    admin_client.delete("/api/v1/workflows-secrets/SLACK_TOKEN", headers=CSRF_HEADERS)


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
    # シークレットの値は Workflow の出力で伏せられるので、短い値を残すと他の試験の出力を壊す。長い値にして最後に消す
    admin_client.put(f"{API}/secret:NEW_KEY", json={"values": {"value": "settings-keys-test-value-7f3a"}}, headers=CSRF_HEADERS)
    assert any(s["name"] == "NEW_KEY" for s in admin_client.get("/api/v1/workflows-secrets").json())
    assert admin_client.delete(f"{API}/secret:NEW_KEY", headers=CSRF_HEADERS).status_code == 200
    assert admin_client.put(f"{API}/nope", json={"values": {"x": "y"}}, headers=CSRF_HEADERS).status_code == 404


def test_delete_is_explicit(admin_client):
    from app.models_mgmt import hf

    hf.set_token("hf_to_delete")
    assert admin_client.delete(f"{API}/hf", headers=CSRF_HEADERS).status_code == 200
    assert not hf.has_token()
