"""作業場（箱）の中でアプリを動かして preview する（U12）。

目的:
- lab_start は利用者の command を文字列へ埋め込まず、箱の中の bash へ位置引数（$1）で渡す。{port}・{host}・{base} だけを
  置き換え、環境変数 PORT・HOST も渡す。ControlDeck の複製が動いているときは同じ公開ポートを使うので断る。
- API は持ち主だけ（他人は 404 相当の 422）・command は 1〜500 文字・監査に command を 200 文字に切って残す。
- preview の入口は URL の鍵（起こすたびに変わる）で通す。鍵違いは 404・止まっていれば 409・待ち受け前は 425。
  ControlDeck の Cookie は上流へ渡さない。{base} を渡した command は接頭辞を付けたまま上流へ送る。
根拠: ROADMAP U12。箱の隔離（pod の公開ポートだけが外へ出る）を保ったまま、作った画面を見られるようにする。
podman は呼ばない（モック）。上流は小さな HTTP サーバー。
"""
from __future__ import annotations

import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.maintenance import sandbox
from app.project_lab import webview
from tests.conftest import CSRF_HEADERS
from tests.test_dev_workspaces import api, repo, workspaces_create_box  # noqa: F401


@pytest.fixture
def podman(monkeypatch):
    """podman の代役。実行した argv と、動いているコンテナ名の集合を持つ。"""
    state = {"argv": [], "running": set(), "stops": []}

    def podman_argv(box, command, **kwargs):
        return ["podman", "run", kwargs["name"], *command, *(f"{k}={v}" for k, v in sorted((kwargs.get("env") or {}).items()))]

    real_run = subprocess.run

    def run(argv, **kwargs):
        if not str(argv[0]).endswith("podman"):
            return real_run(argv, **kwargs)           # git などは本物（box の作成に要る）
        if argv[1] == "ps":
            name = argv[3].removeprefix("name=^").removesuffix("$")
            return subprocess.CompletedProcess(argv, 0, "abc\n" if name in state["running"] else "", "")
        if argv[1] == "rm":
            state["stops"].append(argv[-1])
            state["running"].discard(argv[-1])
            return subprocess.CompletedProcess(argv, 0, "", "")
        state["argv"].append(argv)
        state["running"].add(argv[2])
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(sandbox, "podman_argv", podman_argv)
    monkeypatch.setattr(sandbox.subprocess, "run", run)
    real_which = sandbox.shutil.which
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, *a, **k: "podman" if name == "podman" else real_which(name, *a, **k))
    monkeypatch.setattr(sandbox, "_listening", lambda port: False)
    return state


def test_lab_start_passes_command_as_positional_argument(repo, podman):  # noqa: F811
    box = workspaces_create_box()
    state = sandbox.lab_start(box, "npm run dev --port {port} --host {host} --base {base}; echo $(whoami)", by="admin")
    argv = podman["argv"][0]
    # bash -c の本文は固定。利用者の command は位置引数（$1）、ログの場所は $2
    assert argv[2] == sandbox.lab_container(box)
    assert argv[3:6] == ["/bin/bash", "-c", ': >"$2"; exec /bin/bash -c "$1" >>"$2" 2>&1']
    assert argv[6] == "bash"
    actual = argv[7]
    base = sandbox.lab_base(box.name, state["started_at"])
    assert actual == f"npm run dev --port {box.port} --host 0.0.0.0 --base {base}; echo $(whoami)"
    assert argv[8] == str(box.path / "lab" / "run.log")
    assert f"PORT={box.port}" in argv and "HOST=0.0.0.0" in argv
    assert state["running"] is True and state["command"].startswith("npm run dev --port {port}")
    assert sandbox.load(box.name).meta["lab"]["by"] == "admin"


def test_lab_start_stops_the_previous_one_and_host_never_writes_the_log(repo, podman, tmp_path):  # noqa: F811
    """前の lab は止める。ログを空にするのは箱の中（argv の `: >"$2"`）で、ホストは box.path の中のログを書かない。
    根拠: 箱の AI は lab/ を書けるので、run.log をホストのファイルへの symlink にされると、ホストが空にしてしまう。"""
    box = workspaces_create_box()
    sandbox.lab_start(box, "first")
    precious = tmp_path / "precious.txt"
    precious.write_text("host secret")
    log = box.path / "lab" / "run.log"
    log.unlink(missing_ok=True)
    log.symlink_to(precious)
    second = sandbox.lab_start(box, "second")
    assert sandbox.lab_container(box) in podman["stops"]
    assert second["command"] == "second"
    assert precious.read_text() == "host secret", "ホストのファイルを空にしない"
    assert second["log_tail"] == "", "symlink の先は読まない"


def test_box_file_tail_does_not_follow_symlinked_folder(repo, tmp_path):  # noqa: F811
    """lab/ そのものを symlink にされても辿らない（ミラーの server.log も同じ読み方）。"""
    box = workspaces_create_box()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "run.log").write_text("host file")
    (box.path / "lab").symlink_to(outside)
    assert sandbox._box_file_tail(box, "lab", "run.log", 100) == ""


def test_lab_start_refuses_while_mirror_runs_and_checks_length(repo, podman):  # noqa: F811
    box = workspaces_create_box()
    podman["running"].add(sandbox.mirror_container(box))
    with pytest.raises(sandbox.SandboxError, match="ControlDeck の複製が動いています"):
        sandbox.lab_start(box, "npm run dev")
    assert podman["argv"] == []
    podman["running"].clear()
    for bad in ("  ", "x" * 501):
        with pytest.raises(sandbox.SandboxError):
            sandbox.lab_start(box, bad)


def test_lab_state_reports_listening_and_log_tail(repo, podman, monkeypatch):  # noqa: F811
    box = workspaces_create_box()
    assert sandbox.lab_state(box)["running"] is False
    sandbox.lab_start(box, "serve")
    (box.path / "lab" / "run.log").write_text("a" * 5000 + "END")
    state = sandbox.lab_state(box)
    assert state["running"] and not state["listening"]
    assert len(state["log_tail"]) == 4096 and state["log_tail"].endswith("END")
    monkeypatch.setattr(sandbox, "_listening", lambda port: True)
    assert sandbox.lab_state(box)["listening"] is True
    sandbox.lab_stop(box)
    assert sandbox.lab_state(box)["listening"] is False


# ---- API ----

def test_lab_api_owner_only_validation_and_audit(repo, api, podman):  # noqa: F811
    from app.database import SessionLocal
    from app.models import AuditLog

    name = workspaces_create_box().name
    url = f"/api/v1/opencode/workspaces/{name}/lab"
    assert api.get("/api/v1/opencode/workspaces/nothing/lab").status_code == 422
    first = api.get(url).json()
    assert first["running"] is False and first["preview_url"] is None
    assert api.post(f"{url}/run", json={"command": ""}, headers=CSRF_HEADERS).status_code == 422
    assert api.post(f"{url}/run", json={"command": "x" * 501}, headers=CSRF_HEADERS).status_code == 422
    long_command = "echo " + "y" * 300
    ran = api.post(f"{url}/run", json={"command": long_command}, headers=CSRF_HEADERS)
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["running"] and body["preview_url"].startswith(f"/project-view/w/{name}/")
    assert body["preview_url"] == sandbox.lab_base(name, body["started_at"])
    with SessionLocal() as db:
        row = db.query(AuditLog).filter(AuditLog.action == "workspace.lab.run").one()
        assert row.resource_id == name and len(row.metadata_json) < 400 and "echo yyy" in row.metadata_json
    stopped = api.post(f"{url}/stop", headers=CSRF_HEADERS).json()
    assert stopped["running"] is False and stopped["preview_url"] is None


def test_lab_api_refuses_while_mirror_runs(repo, api, podman):  # noqa: F811
    name = workspaces_create_box().name
    podman["running"].add(f"{sandbox.CONTAINER_PREFIX}{name}-mirror")
    refused = api.post(f"/api/v1/opencode/workspaces/{name}/lab/run", json={"command": "serve"}, headers=CSRF_HEADERS)
    assert refused.status_code == 422 and "複製が動いています" in refused.json()["detail"]


# ---- preview の中継 ----

class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        body = b"<html><head></head><body>lab</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "upstream=1")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def lab_upstream(monkeypatch):
    """上流の代役。作業場の箱を、port・lab の meta だけ持つ偽物に差し替える（git を呼ばない load の代わり）。"""
    from types import SimpleNamespace

    _Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    box = SimpleNamespace(name="lab1", port=server.server_address[1], meta={
        "lab": {"command": "vite --base {base}", "started_at": "2026-01-01T00:00:00+00:00", "by": "admin"}})
    flags = {"running": True, "listening": True}
    monkeypatch.setattr(sandbox, "load", lambda name, verify=True: box if name == "lab1" else (_ for _ in ()).throw(
        sandbox.SandboxError("なし")))
    monkeypatch.setattr(sandbox, "_container_running", lambda name: flags["running"])
    monkeypatch.setattr(sandbox, "_listening", lambda port: flags["listening"])
    webview._lab_running_seen.clear()
    yield box, flags
    server.shutdown()


def test_workspace_preview_key_state_cookie_and_base(client, lab_upstream):  # noqa: F811
    box, flags = lab_upstream
    client.cookies.clear()
    base = sandbox.lab_base("lab1", box.meta["lab"]["started_at"])
    assert client.get(f"/project-view/w/lab1/{'0' * 32}/").status_code == 404        # 鍵違い
    assert client.get(f"/project-view/w/nothing/{'0' * 32}/").status_code == 404     # 作業場なし
    client.cookies.set("cd_session", "secret-session")
    response = client.get(base + "src/main.js?x=1", headers={"authorization": "Bearer secret"})
    assert response.status_code == 200 and "lab" in response.text
    assert "__cd/live.js" not in response.text                                        # run 固有の差し込みはしない
    assert response.headers["access-control-allow-origin"] == "*" and "set-cookie" not in response.headers
    # 別タブで直接開かれても箱の中のアプリが ControlDeck の生まれにならないよう、CSP の sandbox（same-origin なし）を付ける
    assert response.headers["content-security-policy"].startswith("sandbox ")
    assert "allow-same-origin" not in response.headers["content-security-policy"]
    seen = _Handler.seen[-1]
    assert "cookie" not in seen["headers"] and "authorization" not in seen["headers"]
    assert seen["path"] == f"{base}src/main.js?x=1"                                   # {base} を渡した command は接頭辞付きのまま
    flags["listening"] = False
    assert client.get(base).status_code == 425
    flags["running"] = False
    webview._lab_running_seen.clear()
    assert client.get(base).status_code == 409
    # 起こし直すと鍵が変わり、古い URL は使えない
    box.meta["lab"]["started_at"] = "2026-01-01T00:00:01+00:00"
    assert client.get(base).status_code == 404
    client.cookies.clear()     # 共有の client に偽の Cookie を残さない（ほかのテストの認証が壊れる）
