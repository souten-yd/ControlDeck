"""AI Commander のアカウント管理（app/commander/accounts.py と /accounts API）の試験。

目的: アカウント = 設定ディレクトリという C1 の根幹を守る。
- CLI の表（環境変数の対応）が公式の方法（CLAUDE_CONFIG_DIR・CODEX_HOME・XDG_*）どおりであること。
- アカウント名の検証・フォルダの権限（0700）・ログイン情報を返さないこと。
- 既定のアカウント（環境変数を付けない）という扱いが壊れないこと。

根拠: c1-spec.md（2026-11-26 の指示書）と docs/PLAN-ai-commander.md C1。
  秘密（auth.json の中身）を読まない・返さないことを、API の返事の形で確かめる。
  既存の Terminal の試験（test_terminals.py）は CLI のアカウントを扱わないので
  同じ観点の試験は無い（./deck.sh test-catalog で確認済み）。
"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest
from sqlalchemy import select

from app.commander import accounts
from app.config import data_dir
from app.database import SessionLocal
from app.models import AuditLog
from tests.conftest import CSRF_HEADERS


def _account_root() -> Path:
    return data_dir() / "ai-commander" / "accounts"


def test_cli_table_matches_official_account_isolation():
    """CLI の表: アカウントを分ける環境変数が公式の方法どおりであること。

    根拠: c1-spec.md「複数アカウントの公式の方法」。表を変えると既存のアカウントが
    見えなくなるので、形を固定する。
    """
    assert list(accounts.CLIS) == ["claude", "codex", "opencode", "gemini"]
    assert accounts.CLIS["claude"].env_targets == (("CLAUDE_CONFIG_DIR", "dir"),)
    assert accounts.CLIS["codex"].env_targets == (("CODEX_HOME", "dir"),)
    # OpenCode は ControlDeck の OpenCode（v2）を OpenCode 画面の API 設定で開く（2026-11-27 利用者）。
    # 接続先は OpenCode の設定が決めるので、アカウントは既定の 1 つだけ。
    assert accounts.CLIS["opencode"].env_targets == ()
    assert accounts.CLIS["opencode"].connector == accounts.CONNECTOR_CONTROLDECK_OPENCODE
    assert accounts.CLIS["gemini"].env_targets == ()
    # コマンドは表の実行ファイル名だけ。gemini は未導入でも表にある（画面で断るため）。
    assert accounts.CLIS["claude"].executable == "claude"
    assert accounts.CLIS["gemini"].executable == "gemini"


def test_account_folder_permissions_and_login_check():
    """アカウントのフォルダは 0700 で作られる。ログイン済みはファイルの有無だけ。

    根拠: アカウントのフォルダには CLI のログイン情報（鍵・トークン）が入る。
    他人に読める権限では置けない（AGENTS.md 秘密の扱い）。ログイン判定で
    ファイルの中身を読まないことを、空ファイルで判定できることで確かめる。
    """
    claude = accounts.CLIS["claude"]
    directory = accounts.create_account(claude, "login-probe")
    assert directory == _account_root() / "claude" / "login-probe"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert accounts.logged_in(claude, directory) is False
    (directory / ".credentials.json").write_text("", encoding="utf-8")
    assert accounts.logged_in(claude, directory) is True
    # codex は auth.json
    codex = accounts.CLIS["codex"]
    codex_dir = accounts.create_account(codex, "login-probe")
    assert accounts.logged_in(codex, codex_dir) is False
    (codex_dir / "auth.json").write_text("", encoding="utf-8")
    assert accounts.logged_in(codex, codex_dir) is True
    # opencode は ControlDeck の API で開くので、アカウントのフォルダを作らない
    with pytest.raises(ValueError):
        accounts.create_account(accounts.CLIS["opencode"], "login-probe")


def test_account_name_validation_and_reserved_default():
    """アカウント名の検証: 名前は folders の名前になるので素朴な形だけ。「default」は reserved。

    根拠: c1-spec.md の名前の規則 ^[a-z0-9][a-z0-9_-]{0,31}$。「default」は
    既定のアカウントを表す予約語なので、フォルダを作られても居場所が無い。
    """
    claude = accounts.CLIS["claude"]
    for bad in ("Bad", "あいう", "a" * 33, "-x", "x y", "", "UPPER"):
        with pytest.raises(ValueError):
            accounts.validate_name(bad)
    for good in ("a", "work-2", "sub_account", "0z9"):
        accounts.validate_name(good)
    with pytest.raises(ValueError):
        accounts.create_account(claude, "default")
    # gemini はアカウントを分けられない
    with pytest.raises(ValueError):
        accounts.create_account(accounts.CLIS["gemini"], "work")


def test_env_for_default_account_is_none():
    """既定のアカウントは環境変数を付けない（利用者の普段の ~/.claude などを使う）。

    根拠: c1-spec.md「既定のアカウント（環境変数を付けない＝利用者の普段の
    ~/.claude など）も選べるようにする」。None を返すことが「付けない」の表現。
    """
    claude = accounts.CLIS["claude"]
    accounts.create_account(claude, "env-probe")
    assert accounts.env_for(claude, "default") is None
    env = accounts.env_for(claude, "env-probe")
    assert env == {"CLAUDE_CONFIG_DIR": str(_account_root() / "claude" / "env-probe")}
    assert accounts.env_for(accounts.CLIS["opencode"], "default") is None
    # 無いアカウント・gemini のアカウントは断る
    with pytest.raises(FileNotFoundError):
        accounts.env_for(claude, "ghost")
    with pytest.raises(ValueError):
        accounts.env_for(accounts.CLIS["gemini"], "work")


def test_accounts_api_create_list_delete_and_audit(admin_client):
    """アカウントの API: 作成・一覧・削除と監査ログ。返事に秘密（中身）を含めない。

    根拠: アカウントの削除はログイン情報ごと消す破壊的操作なので監査ログ必須
    （AGENTS.md）。一覧は名前と「ログイン済み」だけを返す。
    """
    # 名前の検証は schema（422）と module（400）の両方で守る
    assert admin_client.post(
        "/api/v1/commander/accounts/claude", headers=CSRF_HEADERS, json={"name": "Bad Name"},
    ).status_code == 422
    assert admin_client.post(
        "/api/v1/commander/accounts/claude", headers=CSRF_HEADERS, json={"name": "default"},
    ).status_code == 400
    assert admin_client.post(
        "/api/v1/commander/accounts/gemini", headers=CSRF_HEADERS, json={"name": "work"},
    ).status_code == 400

    created = admin_client.post(
        "/api/v1/commander/accounts/claude", headers=CSRF_HEADERS, json={"name": "work"},
    )
    assert created.status_code == 201, created.text
    assert created.json() == {"name": "work", "logged_in": False}
    # 同名は 409
    assert admin_client.post(
        "/api/v1/commander/accounts/claude", headers=CSRF_HEADERS, json={"name": "work"},
    ).status_code == 409

    listed = admin_client.get("/api/v1/commander/accounts/claude")
    assert listed.status_code == 200
    body = listed.json()
    assert body["cli"]["id"] == "claude"
    names = [row["name"] for row in body["accounts"]]
    assert names[0] == "default" and "work" in names
    # 返事は名前・既定か・ログイン済み・画面の名前だけ（鍵・トークン・中身は無い）
    assert set(body["accounts"][0]) == {"name", "default", "logged_in", "label", "usage", "usage_statusline"}

    # 既定のアカウントは消せない
    assert admin_client.delete(
        "/api/v1/commander/accounts/claude/default", headers=CSRF_HEADERS,
    ).status_code == 400

    # 削除はフォルダごと
    account_dir = _account_root() / "claude" / "work"
    assert account_dir.is_dir()
    deleted = admin_client.delete(
        "/api/v1/commander/accounts/claude/work", headers=CSRF_HEADERS,
    )
    assert deleted.status_code == 204
    assert not account_dir.exists()
    assert admin_client.delete(
        "/api/v1/commander/accounts/claude/work", headers=CSRF_HEADERS,
    ).status_code == 404

    with SessionLocal() as db:
        rows = db.execute(
            select(AuditLog)
            .where(AuditLog.resource_id == "claude/work", AuditLog.action.like("commander.account.%"))
            .order_by(AuditLog.id)
        ).scalars().all()
        # 他の試験（コンソール）が同じ名前のアカウントを作って消した記録もあり得るので、最後の 2 件を見る
        assert [r.action for r in rows][-2:] == [
            "commander.account.create", "commander.account.delete",
        ]
        assert all(r.username for r in rows)


def test_commander_api_requires_authentication(client):
    """認証の無い呼び出しは 401。Terminal と同じ権限で守る。"""
    client.cookies.clear()
    for method, path in (
        ("get", "/api/v1/commander/clis"),
        ("get", "/api/v1/commander/accounts/claude"),
        ("post", "/api/v1/commander/accounts/claude"),
        ("delete", "/api/v1/commander/accounts/claude/work"),
        ("get", "/api/v1/commander/consoles"),
        ("post", "/api/v1/commander/consoles"),
        ("delete", "/api/v1/commander/consoles/0123abcd"),
    ):
        if method == "get":
            r = client.get(path)
        elif method == "delete":
            r = client.delete(path, headers=CSRF_HEADERS)
        else:
            r = client.post(path, headers=CSRF_HEADERS, json={})
        assert r.status_code == 401, (method, path, r.status_code)


def test_cli_list_reports_installed_state(admin_client):
    """/clis は導入済みか・アカウントを分けられるかを返す（gemini は分けられない）。"""
    body = admin_client.get("/api/v1/commander/clis").json()
    by_id = {row["id"]: row for row in body["clis"]}
    assert set(by_id) == {"claude", "codex", "opencode", "gemini"}
    for row in body["clis"]:
        assert isinstance(row["installed"], bool)
    assert by_id["gemini"]["multi_account"] is False
    assert by_id["opencode"]["multi_account"] is False
    assert by_id["opencode"]["connector"] == accounts.CONNECTOR_CONTROLDECK_OPENCODE
    assert by_id["gemini"]["name"] == "Gemini CLI"


def test_delete_account_refuses_symlinked_folder(tmp_path):
    """アカウントのフォルダがリンクなら削除しない（リンク先＝accounts の外を消さない）。

    根拠: 2026-11-27 の Opus の監査。delete は rmtree なので、入口がリンクだと
    利用者の普段の ~/.claude などを消す恐れがある。
    """
    claude = accounts.CLIS["claude"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    root = _account_root() / "claude"
    root.mkdir(parents=True, exist_ok=True)
    link = root / "linked"
    link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            accounts.delete_account(claude, "linked")
        assert (outside / "keep.txt").exists()
    finally:
        link.unlink()


def test_account_labels_including_the_default_account(admin_client):
    """アカウントに画面の名前を付けられる。既定のアカウントにも付けられ、消すと外れる。

    根拠: 2026-11-27 利用者「既定のアカウントにも名前を付けてほしい」。名前はフォルダの名前と別に
    labels.json（0600）へ置く。制御文字は一覧の表示を壊すので断る。
    """
    accounts.create_account(accounts.CLIS["codex"], "label-probe")
    r = admin_client.put("/api/v1/commander/accounts/codex/default/label", headers=CSRF_HEADERS,
                         json={"label": "仕事用（Plus）"})
    assert r.status_code == 200, r.text
    assert admin_client.put("/api/v1/commander/accounts/codex/label-probe/label", headers=CSRF_HEADERS,
                            json={"label": "個人"}).status_code == 200
    rows = {row["name"]: row for row in admin_client.get("/api/v1/commander/accounts/codex").json()["accounts"]}
    assert rows["default"]["label"] == "仕事用（Plus）" and rows["label-probe"]["label"] == "個人"
    assert stat.S_IMODE((data_dir() / "ai-commander" / "labels.json").stat().st_mode) == 0o600
    # 既定のアカウントしか無い CLI（gemini）でも既定には名前を付けられる
    assert admin_client.put("/api/v1/commander/accounts/gemini/default/label", headers=CSRF_HEADERS,
                            json={"label": "Google"}).status_code == 200
    # 無いアカウント・制御文字は断る
    assert admin_client.put("/api/v1/commander/accounts/codex/ghost/label", headers=CSRF_HEADERS,
                            json={"label": "x"}).status_code == 404
    assert admin_client.put("/api/v1/commander/accounts/codex/default/label", headers=CSRF_HEADERS,
                            json={"label": "a\tb"}).status_code == 400
    # 空で外す・アカウントを消すと名前も消える
    assert admin_client.put("/api/v1/commander/accounts/codex/default/label", headers=CSRF_HEADERS,
                            json={"label": ""}).json()["label"] == ""
    assert admin_client.delete("/api/v1/commander/accounts/codex/label-probe", headers=CSRF_HEADERS).status_code == 204
    assert "label-probe" not in accounts.labels_for("codex")
    assert "default" not in accounts.labels_for("codex")
