"""Settings →「API キー」: 外部のサービスのキーを 1 か所で見る・入れる・消す（2026-11-21 利用者「キーを設定の中にまとめて。
OpenRouter のキーも。今あるキーを全部移動して、消さないように」）。

キーの置き場（暗号化して保存）は変えない。ここは各置き場の読み書きをまとめる窓口で、今あるキーはそのまま見える:
- クラウドの AI（OpenRouter・OpenAI・Anthropic・Gemini・OpenAI 互換）: app/models_mgmt/cloud.py
- Hugging Face のトークン: app/models_mgmt/hf.py（これまで画面が無かった）
- 特許・論文の公開データ（EPO・USPTO・特許庁）: app/knowledge/sources.py
- Workflow のシークレット（{{secrets.名前}}）: workflow_secrets 表

キーの値は返さない（設定済みか・末尾 4 文字だけ）。監査にも値は残さない。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.audit import service as audit
from app.database import get_db
from app.models import User
from app.security.deps import require_permission

router = APIRouter(prefix="/settings/keys", tags=["settings"])
MANAGE = Depends(require_permission("settings.manage"))

SOURCE_NOTES = {
    "epo": "世界（日本を含む）の公報の書誌・要約を、言葉や分類で探して取る。登録は無料",
    "uspto": "米国の出願の書誌を取る。登録は無料",
    "jpo": "日本の出願の経過情報を出願番号で引く（言葉では探せない）。特許庁へ利用登録の申込みが要る",
}
SOURCE_FIELDS = {
    "epo_key": "Consumer Key", "epo_secret": "Consumer Secret", "uspto_key": "API キー",
    "jpo_token_url": "トークン取得の URL（利用登録の後に特許庁から通知）", "jpo_id": "ID", "jpo_password": "パスワード",
}


def _cloud() -> list[dict]:
    from app.models_mgmt import cloud

    out = []
    for p in cloud.status()["providers"]:
        out.append({"id": f"cloud:{p['id']}", "group": "ai", "name": p["name"], "note": p["note"],
                    "signup": p["keys_url"], "configured": p["configured"],
                    "hint": f"••••{p['key_hint']}" if p.get("key_hint") else "",
                    "fields": [{"name": "api_key", "label": "API キー", "configured": p["configured"]}],
                    "used_by": "AI アシスタント・OpenCode などでクラウドのモデルを使うとき（有効化・使うモデル・月の上限は Models）",
                    "manage": "/models?tab=frontier", "enabled": p["enabled"]})
    return out


def _hf() -> dict:
    from app.models_mgmt import hf

    has = hf.has_token()
    return {"id": "hf", "group": "models", "name": "Hugging Face", "note": "制限付き（gated）のモデルをダウンロードするときのトークン",
            "signup": "https://huggingface.co/settings/tokens", "configured": has, "hint": "",
            "fields": [{"name": "token", "label": "アクセストークン", "configured": has}],
            "used_by": "Models の Hugging Face からのダウンロード", "manage": "/models"}


def _sources() -> list[dict]:
    from app.knowledge import sources

    out = []
    for s in sources.listing():
        if not s["keys"]:
            continue
        fields = [{"name": k, "label": SOURCE_FIELDS.get(k, k), "configured": bool(sources._secret(k))}  # noqa: SLF001
                  for k in s["keys"]]
        out.append({"id": f"source:{s['id']}", "group": "patent", "name": s["label"], "note": SOURCE_NOTES.get(s["id"], s["hint"]),
                    "signup": s["signup"], "configured": s["ready"], "hint": "", "fields": fields,
                    "used_by": "Patent の公報の取得・ウォッチ", "manage": "/patents?tab=docs"})
    return out


def _secrets(db) -> list[dict]:
    from app.models import WorkflowSecret

    rows = db.query(WorkflowSecret).order_by(WorkflowSecret.name).all()
    return [{"id": f"secret:{r.name}", "group": "workflow", "name": r.name, "note": "Workflow で {{secrets." + r.name + "}} として使う",
             "signup": "", "configured": True, "hint": "", "fields": [{"name": "value", "label": "値", "configured": True}],
             "used_by": "Workflow", "manage": "/workflows", "updated_at": str(r.updated_at or "")} for r in rows]


@router.get("")
def list_keys(user: User = MANAGE, db=Depends(get_db)):
    groups = [
        {"id": "ai", "label": "クラウドの AI", "description": "OpenRouter など。キーを入れた後、使うかどうかとモデルは Models で選ぶ"},
        {"id": "patent", "label": "特許・論文の公開データ", "description": "EPO・USPTO・特許庁。OpenAlex・RSS・GDELT はキーなしで使える"},
        {"id": "models", "label": "モデルのダウンロード", "description": "Hugging Face"},
        {"id": "workflow", "label": "Workflow のシークレット", "description": "Workflow の中で {{secrets.名前}} として使う値"},
    ]
    return {"groups": groups, "keys": [*_cloud(), *_sources(), _hf(), *_secrets(db)]}


class KeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(default_factory=dict)


def _apply(key_id: str, values: dict[str, str], db) -> None:
    """空でない値だけを保存する（空は「変えない」）。消すのは DELETE。"""
    values = {k: v.strip() for k, v in values.items() if isinstance(v, str) and v.strip()}
    if not values:
        raise HTTPException(status_code=422, detail="保存する値を入れてください")
    kind, _, name = key_id.partition(":")
    if kind == "cloud":
        from app.models_mgmt import cloud

        if set(values) != {"api_key"}:
            raise HTTPException(status_code=422, detail="API キーだけを保存できます")
        try:
            cloud.save_provider(name, {"api_key": values["api_key"]})
        except cloud.CloudError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    elif key_id == "hf":
        from app.models_mgmt import hf

        if set(values) != {"token"} or len(values["token"]) > 200:
            raise HTTPException(status_code=422, detail="トークンの形式が不正です")
        hf.set_token(values["token"])
    elif kind == "source":
        from app.knowledge import sources

        allowed = next((s["keys"] for s in sources.listing() if s["id"] == name), None)
        if allowed is None or set(values) - set(allowed):
            raise HTTPException(status_code=422, detail="この取得先に無い項目です")
        try:
            sources.set_keys(values)
        except sources.SourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif kind == "secret":
        from app.workflows.router import upsert_secret

        if set(values) != {"value"}:
            raise HTTPException(status_code=422, detail="値だけを保存できます")
        upsert_secret(db, name, values["value"])
    else:
        raise HTTPException(status_code=404, detail="知らないキーです")


@router.put("/{key_id}")
def save_key(key_id: str, body: KeyBody, request: Request, user: User = MANAGE, db=Depends(get_db)):
    _apply(key_id, body.values, db)
    audit.record(db, "settings.key_save", user=user, resource_type="key", resource_id=key_id, request=request,
                 metadata={"fields": sorted(k for k, v in body.values.items() if v.strip())})
    return list_keys(user, db)


@router.delete("/{key_id}")
def delete_key(key_id: str, request: Request, user: User = MANAGE, db=Depends(get_db)):
    """キーを消す（画面で確認してから呼ぶ）。"""
    kind, _, name = key_id.partition(":")
    if kind == "cloud":
        from app.models_mgmt import cloud

        try:
            cloud.save_provider(name, {"api_key": ""})
        except cloud.CloudError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    elif key_id == "hf":
        from app.models_mgmt import hf

        hf.set_token("")
    elif kind == "source":
        from app.knowledge import sources

        keys = next((s["keys"] for s in sources.listing() if s["id"] == name), None)
        if keys is None:
            raise HTTPException(status_code=404, detail="知らない取得先です")
        sources.set_keys({k: "" for k in keys})
    elif kind == "secret":
        from app.models import WorkflowSecret

        row = db.query(WorkflowSecret).filter(WorkflowSecret.name == name).first()
        if row is not None:
            db.delete(row)
            db.commit()
    else:
        raise HTTPException(status_code=404, detail="知らないキーです")
    audit.record(db, "settings.key_delete", user=user, resource_type="key", resource_id=key_id, request=request)
    return list_keys(user, db)
