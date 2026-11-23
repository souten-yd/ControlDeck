"""Settings →「API キー」: 外部のサービスのキーを 1 か所で見る・入れる・消す（2026-11-21 利用者「キーを設定の中にまとめて。
OpenRouter のキーも。今あるキーを全部移動して、消さないように」）。

キーの置き場（暗号化して保存）は変えない。ここは各置き場の読み書きをまとめる窓口で、今あるキーはそのまま見える:
- クラウドの AI（OpenRouter・OpenAI・Anthropic・Gemini・OpenAI 互換）: app/models_mgmt/cloud.py
- 特許・論文の公開データ（EPO・USPTO・特許庁）: app/knowledge/sources.py
- 調査（Reddit・Stack Exchange のキーと Discourse のフォーラムの一覧）: app/workflows/research_keys.py（2026-11-23 利用者「Reddit の API を登録するところを API の登録にまとめて」）
Hugging Face のトークンと Workflow のシークレットはここに出さない（2026-11-21 利用者「モデルのダウンロードと Workflow のシークレットは設定不要」）。
Workflow のシークレットは Workflow の画面で管理する。

キーの値は返さない（設定済みか・末尾 4 文字だけ）。監査にも値は残さない。
"""
from __future__ import annotations

import re

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


ZEN_NOTE = ("開発用の箱の OpenCode だけが使う。ControlDeck のゲートウェイは通らず、箱から opencode.ai へ直接送る。"
            "無料の範囲だけで課金のないアカウントのキーを入れてください。特許など未公開の情報を扱う箱では使わない")


def _dev() -> list[dict]:
    from app.integrations.opencode import zen

    ok = zen.configured()
    return [{"id": "dev:opencode-zen", "group": "dev", "name": "OpenCode Zen（無料のモデル）", "note": ZEN_NOTE,
             "signup": "https://opencode.ai/zen", "configured": ok, "hint": f"••••{zen.hint()}" if ok and zen.hint() else "",
             "fields": [{"name": "api_key", "label": "API キー", "configured": ok}],
             "used_by": "開発用サンドボックスの箱の OpenCode（箱ごとに `zen <名前> on|off`）", "manage": ""}]


REDDIT_NOTE = ("AI アシスタントの調査で Reddit を検索・読むときに使う（r/LocalLLaMA など）。https://www.reddit.com/prefs/apps で「script」か「web app」を作り、"
               "Client ID（アプリ名の下の文字列）と secret を入れる。Reddit の承認（API の利用の申請）が要る場合があるので、保存したら「つながりを確かめる」で確認する。"
               "キーが無い・使えないときは、キーなしの経路（検索エンジン）で探す")
SE_NOTE = ("Stack Overflow などの検索・読み取りに使う。無くても 1 日 300 件まで使える。stackapps.com でアプリを登録すると 1 日 10,000 件になる")


def _research() -> list[dict]:
    from app.workflows import research_keys as rk

    red = rk.reddit() is not None
    extra = rk.extra_forums()
    return [
        {"id": "research:reddit", "group": "research", "name": "Reddit", "note": REDDIT_NOTE, "signup": "https://www.reddit.com/prefs/apps",
         "configured": red, "hint": rk.hint("reddit_client_id"), "testable": True,
         "fields": [{"name": "reddit_client_id", "label": "Client ID", "configured": bool(rk.secret("reddit_client_id"))},
                    {"name": "reddit_client_secret", "label": "Client Secret", "configured": bool(rk.secret("reddit_client_secret"))},
                    {"name": "reddit_username", "label": "Reddit のユーザー名（User-Agent に使う）", "configured": bool(rk.secret("reddit_username")), "plain": True}],
         "used_by": "AI アシスタントの調査（web_search・read_page の Reddit）", "manage": ""},
        {"id": "research:stackexchange", "group": "research", "name": "Stack Exchange（任意）", "note": SE_NOTE, "signup": "https://stackapps.com/apps/oauth/register",
         "configured": bool(rk.secret("stackexchange_key")), "hint": rk.hint("stackexchange_key"), "testable": True,
         "fields": [{"name": "stackexchange_key", "label": "API キー", "configured": bool(rk.secret("stackexchange_key"))}],
         "used_by": "AI アシスタントの調査（Stack Overflow・Super User・Server Fault・Ask Ubuntu・*.stackexchange.com）", "manage": ""},
        {"id": "research:forums", "group": "research", "name": "Discourse のフォーラム（追加）",
         "note": "キー不要。次のフォーラムは最初から調べられる: " + "、".join(rk.DEFAULT_FORUMS) + "。足したいフォーラムのドメインをカンマで区切って入れる"
                 + (f"（今足してあるもの: {'、'.join(extra)}）" if extra else "") + "。XDA など API の無いサイトは検索エンジン経由で探す",
         "signup": "", "configured": bool(extra), "hint": f"{len(extra)} 件" if extra else "",
         "fields": [{"name": "domains", "label": "足すドメイン（例: forum.example.com, discuss.example.org）", "configured": bool(extra), "plain": True}],
         "used_by": "AI アシスタントの調査（web_search の sites・read_page）", "manage": ""},
        {"id": "research:limits", "group": "research", "name": "調べる量（読むページ数・出典の上限）",
         "note": "「観点ごとに読むページ数」はふつうの深さの値。速い調査は 2 少なく、深い調査は 3 多く読む（ニュースなど時事の依頼は最低 5 件）。"
                 "増やすほど確かだが時間がかかる。キーは不要。空の欄は今のまま",
         "signup": "", "configured": rk.limits() != {n: v[2] for n, v in rk.LIMITS.items()},
         "hint": f"{rk.limits()['read_per_aspect']} 件・上限 {rk.limits()['max_sources']} 件",
         "fields": [{"name": "read_per_aspect", "label": f"観点ごとに読むページ数（{rk.LIMITS['read_per_aspect'][0]}〜{rk.LIMITS['read_per_aspect'][1]}。今 {rk.limits()['read_per_aspect']}）",
                     "configured": True, "plain": True},
                    {"name": "max_sources", "label": f"出典の上限（{rk.LIMITS['max_sources'][0]}〜{rk.LIMITS['max_sources'][1]}。今 {rk.limits()['max_sources']}）",
                     "configured": True, "plain": True}],
         "used_by": "AI アシスタントの Web 調査・調査モード", "manage": ""},
    ]


@router.get("")
def list_keys(user: User = MANAGE, db=Depends(get_db)):
    groups = [
        {"id": "ai", "label": "クラウドの AI", "description": "OpenRouter など。キーを入れた後、使うかどうかとモデルは Models で選ぶ"},
        {"id": "patent", "label": "特許・論文の公開データ", "description": "EPO・USPTO・特許庁。OpenAlex・RSS・GDELT はキーなしで使える"},
        {"id": "research", "label": "調査（フォーラム・検索）", "description": "AI アシスタントが Reddit・フォーラム・Q&A サイトを調べるときのキー。無くても検索エンジン経由で探せる"},
        {"id": "dev", "label": "開発", "description": "開発用サンドボックスの箱の OpenCode が使うキー"},
    ]
    return {"groups": groups, "keys": [*_cloud(), *_sources(), *_research(), *_dev()]}


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
    elif kind == "source":
        from app.knowledge import sources

        allowed = next((s["keys"] for s in sources.listing() if s["id"] == name), None)
        if allowed is None or set(values) - set(allowed):
            raise HTTPException(status_code=422, detail="この取得先に無い項目です")
        try:
            sources.set_keys(values)
        except sources.SourceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif kind == "research":
        from app.workflows import research_keys as rk

        allowed = {"reddit": {"reddit_client_id", "reddit_client_secret", "reddit_username"},
                   "stackexchange": {"stackexchange_key"}, "forums": {"domains"},
                   "limits": set(rk.LIMITS)}.get(name)
        if allowed is None or set(values) - allowed:
            raise HTTPException(status_code=422 if allowed else 404, detail="この項目は保存できません" if allowed else "知らないキーです")
        try:
            if name == "forums":
                rk.set_extra_forums([*rk.extra_forums(), *[d for d in re.split(r"[,\s、]+", values["domains"]) if d]])
            elif name == "limits":
                rk.set_limits(values)
            else:
                rk.set_keys(values)
        except rk.ResearchKeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif key_id == "dev:opencode-zen":
        from app.integrations.opencode import zen

        if set(values) != {"api_key"}:
            raise HTTPException(status_code=422, detail="API キーだけを保存できます")
        try:
            zen.set(values["api_key"])
        except zen.ZenKeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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
    elif kind == "source":
        from app.knowledge import sources

        keys = next((s["keys"] for s in sources.listing() if s["id"] == name), None)
        if keys is None:
            raise HTTPException(status_code=404, detail="知らない取得先です")
        sources.set_keys({k: "" for k in keys})
    elif kind == "research":
        from app.workflows import research_keys as rk

        fields = {"reddit": ("reddit_client_id", "reddit_client_secret", "reddit_username"), "stackexchange": ("stackexchange_key",),
                  "forums": (), "limits": ()}.get(name)
        if fields is None:
            raise HTTPException(status_code=404, detail="知らないキーです")
        rk.set_keys({f: "" for f in fields})
        if name == "forums":
            rk.set_extra_forums([])
        elif name == "limits":
            rk.reset_limits()
    elif key_id == "dev:opencode-zen":
        from app.integrations.opencode import zen

        zen.clear()
    else:
        raise HTTPException(status_code=404, detail="知らないキーです")
    audit.record(db, "settings.key_delete", user=user, resource_type="key", resource_id=key_id, request=request)
    return list_keys(user, db)


@router.post("/{key_id}/test")
async def test_key(key_id: str, user: User = MANAGE):
    """「つながりを確かめる」: 保存したキーで 1 回だけ問い合わせて、結果を平易な 1 行で返す（キーの値は返さない）。"""
    from app.workflows import research_tools

    if key_id == "research:reddit":
        ok, message = await research_tools.check_reddit()
    elif key_id == "research:stackexchange":
        ok, message = await research_tools.check_stackexchange()
    else:
        raise HTTPException(status_code=404, detail="確かめられないキーです")
    return {"ok": ok, "message": message}
