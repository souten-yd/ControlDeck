"""目的: ニュースの下ごしらえを CPU と RAM だけの LLM（Gemma 4 E2B）で行う仕組みの試験。llama-server は全部ニセの実行ファイル・モックで、本物のモデル・GPU・外部通信は使わない。

根拠: 2026-11-24 利用者「CPU で動く Gemma とかで記事の翻訳できないか？画像生成と同じような管理で LLM を動かしたい。基本的に CPU と RAM で必ず動かすようにしたい」
「基本的に CPU で動かした Gemma の小を使う…それを既定に設定する。なお中と大は不要」。
守ること:
- 起動の引数は --device none と -ngl 0、環境は GPU を見せない（HIP/ROCR/CUDA を空に）。ログに GPU を使った印が出たら失敗にする。
- 下ごしらえの回が終われば、正常・例外・時間切れのどれでも llama-server のプロセスが残らない。同時に 2 つは起動しない。
- CPU のときは 1 回 10 件・出力の上限 2500・system と日本語の見本つきの指示・出力の形を json_schema で強制する。
- 設定の既定は CPU の LLM。まとめ・一言要約には選べない。機能の状態（未導入 → 導入済み → 削除）。
"""
from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import textwrap
import time
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select

from app.features import registry
from app.news import cpu_llm, prep, settings
from app.news import db as news_db
from app.news.db import NewsItem
from app.news.sources import ConfigError
from tests.test_news_rag_llm import _rows, prep_counter  # noqa: F401


def run(coro):
    return asyncio.run(coro)


FAKE = '''#!{py}
import json, os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
port = int(args[args.index("--port") + 1])
open({rec!r}, "w").write(json.dumps({{"args": args, "hip": os.environ.get("HIP_VISIBLE_DEVICES"), "rocr": os.environ.get("ROCR_VISIBLE_DEVICES"),
                                      "cuda": os.environ.get("CUDA_VISIBLE_DEVICES"), "secret": os.environ.get("CD_TEST_SECRET"), "pid": os.getpid()}}))
mode = {mode!r}
if mode == "never":
    time.sleep(600)
sys.stderr.write({log!r} + "\\n"); sys.stderr.flush()
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def do_POST(self):
        n = int(self.headers.get("content-length", 0)); body = json.loads(self.rfile.read(n))
        open({rec!r} + ".req", "w").write(json.dumps(body))
        out = json.dumps({{"choices": [{{"message": {{"content": "{{}}"}}}}]}}).encode()
        self.send_response(200); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
HTTPServer(("127.0.0.1", port), H).serve_forever()
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """ニセの llama-server とモデルのファイルを置く。mode: ok（普通に待ち受け）/never（待ち受けない）。log: 起動のログに出す 1 行。"""
    monkeypatch.setattr(cpu_llm, "data_dir", lambda: tmp_path)
    binary = tmp_path / "bin" / "llama-server"
    binary.parent.mkdir()
    (tmp_path / "models" / "news-llm").mkdir(parents=True)
    cpu_llm.model_path().write_bytes(b"gguf")
    monkeypatch.setattr(cpu_llm, "server_binary", lambda: binary)

    def make(mode="ok", log="load_tensors: CPU_Mapped model buffer size = 2700 MiB"):
        binary.write_text(FAKE.format(py=sys.executable, rec=str(tmp_path / "rec.json"), mode=mode, log=log))
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return tmp_path / "rec.json"

    monkeypatch.setattr(cpu_llm, "STOP_GRACE", 2.0)
    return make


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:        # 終わって待たれていないゾンビは数えない
        return "Z" not in open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False


def test_command_and_env_hide_the_gpu(tmp_path, monkeypatch):
    """目的: 起動の引数に --device none と -ngl 0 があり、環境は GPU を見せない変数が空で、親の環境（秘密など）は引き継がれないこと。"""
    monkeypatch.setenv("CD_TEST_SECRET", "-".join(["not", "inherited"]))
    cmd = cpu_llm.build_command(tmp_path / "llama-server", tmp_path / "m.gguf", 18999)
    assert cmd[:3] == ["nice", "-n", "10"]
    assert cmd[cmd.index("--device") + 1] == "none" and cmd[cmd.index("-ngl") + 1] == "0"
    assert cmd[cmd.index("--host") + 1] == "127.0.0.1" and "--no-webui" in cmd and cmd[cmd.index("--port") + 1] == "18999"
    assert cmd[cmd.index("-c") + 1] == "16384" and cmd[cmd.index("-t") + 1] == "8"
    env = cpu_llm.build_env(tmp_path / "llama-server")
    assert env["HIP_VISIBLE_DEVICES"] == "" and env["ROCR_VISIBLE_DEVICES"] == "" and env["CUDA_VISIBLE_DEVICES"] == ""
    assert "CD_TEST_SECRET" not in env and env["LD_LIBRARY_PATH"] == str(tmp_path)


def test_gpu_marks_in_the_log_are_detected_but_cpu_lines_are_not():
    """目的: 起動のログに GPU を使った印があれば検知し、「GPU が見つからない」「CPU のバッファ」は誤検知しないこと。"""
    assert cpu_llm.gpu_used("load_tensors: ROCm0 model buffer size = 2700.00 MiB")
    assert cpu_llm.gpu_used("load_tensors: offloaded 36/36 layers to GPU")
    assert cpu_llm.gpu_used("llama_context: Vulkan0 compute buffer size = 100 MiB")
    assert not cpu_llm.gpu_used("ggml_cuda_init: failed to initialize ROCm: no ROCm-capable device is detected")
    assert not cpu_llm.gpu_used("load_tensors: CPU_Mapped model buffer size = 2700.00 MiB\nload_tensors: offloaded 0/36 layers to GPU")


def test_session_starts_with_cpu_only_args_and_stops_the_process(fake):
    """目的: 実際にプロセスを起動して、引数・環境が GPU を見せない形で、抜けたら（正常でも）プロセスが残らないこと。"""
    rec = fake()

    async def go():
        async with cpu_llm.session() as server:
            info = json.loads(rec.read_text())
            assert alive(info["pid"])
            text = await server.complete([{"role": "user", "content": "x"}], max_tokens=5, schema={"type": "object"})
            assert text == "{}"
            return info

    info = run(go())
    assert "--device" in info["args"] and info["args"][info["args"].index("--device") + 1] == "none"
    assert info["hip"] == "" and info["rocr"] == "" and info["cuda"] == "" and info["secret"] is None
    body = json.loads((rec.parent / "rec.json.req").read_text())
    assert body["response_format"]["type"] == "json_schema" and body["max_tokens"] == 5
    time.sleep(0.2)
    assert not alive(info["pid"]) and not cpu_llm._live


def test_session_stops_the_process_on_exception_and_on_timeout(fake, monkeypatch):
    """目的: 本体の処理が例外で終わっても、起動が時間切れになっても、GPU の印が出ても、プロセスが残らないこと。"""
    rec = fake()

    async def boom():
        async with cpu_llm.session():
            pid = json.loads(rec.read_text())["pid"]
            (rec.parent / "pid").write_text(str(pid))
            raise RuntimeError("途中で失敗")

    with pytest.raises(RuntimeError):
        run(boom())
    time.sleep(0.2)
    assert not alive(int((rec.parent / "pid").read_text())) and not cpu_llm._live
    # 起動の時間切れ（待ち受けない）
    rec2 = fake(mode="never")
    monkeypatch.setattr(cpu_llm, "START_TIMEOUT", 1.5)

    async def never():
        async with cpu_llm.session():
            pass

    with pytest.raises(cpu_llm.CpuLlmUnavailable, match="使えるようになりませんでした"):
        run(never())
    time.sleep(0.2)
    assert not alive(json.loads(rec2.read_text())["pid"]) and not cpu_llm._live
    # GPU を使った印
    rec3 = fake(log="load_tensors: ROCm0 model buffer size = 2700.00 MiB")
    monkeypatch.setattr(cpu_llm, "START_TIMEOUT", 30.0)
    with pytest.raises(cpu_llm.CpuLlmUnavailable, match="GPU"):
        run(never())
    time.sleep(0.2)
    assert not alive(json.loads(rec3.read_text())["pid"]) and not cpu_llm._live


def test_never_two_servers_at_once(fake):
    """目的: 下ごしらえが重なっても llama-server は同時に 2 つ起動しない（後の回は待つ）こと。"""
    fake()
    seen: list[int] = []

    async def one():
        async with cpu_llm.session():
            seen.append(len(cpu_llm._live))
            await asyncio.sleep(0.3)

    async def both():
        await asyncio.gather(one(), one())

    run(both())
    assert seen == [1, 1] and not cpu_llm._live


def test_not_ready_gives_guidance_and_does_not_start(tmp_path, monkeypatch):
    """目的: モデルまたは llama.cpp が無いときは、起動せず機能と拡張への案内を返すこと（記事は済みにしない）。"""
    monkeypatch.setattr(cpu_llm, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(cpu_llm, "server_binary", lambda: tmp_path / "none")
    ok, why = cpu_llm.check_ready()
    assert not ok and "モデルが未導入" in why
    cpu_llm.models_dir().mkdir(parents=True)
    cpu_llm.model_path().write_bytes(b"x")
    ok, why = cpu_llm.check_ready()
    assert not ok and "llama.cpp" in why


class FakeServer:
    def __init__(self):
        self.calls: list[dict] = []

    async def complete(self, messages, *, max_tokens, schema=None, temperature=0.2):
        count = int(messages[1]["content"].count("\n") and len([ln for ln in messages[1]["content"].splitlines() if ln[:1].isdigit() and ". " in ln[:5]]))
        self.calls.append({"messages": messages, "max_tokens": max_tokens, "schema": schema, "count": count})
        return json.dumps({str(n): {"ja": f"日本語の概要です {n}", "img": f"a calm lake number {n}", "topics": ["湖"]} for n in range(1, count + 1)}, ensure_ascii=False)


def test_cpu_prep_splits_into_10_and_sends_system_sample_and_schema(monkeypatch, prep_counter):
    """目的: 既定（CPU）では 1 回 10 件ずつ（23 件 → 10・10・3）、出力の上限 2500、system（英日翻訳者）と日本語の見本つきの指示、json_schema で形を強制し、
    その回の全バッチを 1 回の起動で流して終わったら止めること。
    根拠: 2026-11-24 利用者の試し: 20 件では出力の上限で切れた・「日本語で」だけでは英語のまま返すことがあり、system と見本で全部日本語になった。"""
    cfg = settings.load(); cfg["llm"]["prep_model"] = cpu_llm.MODEL_ID; cfg["llm"]["prep_max_per_run"] = 50; settings.save(cfg)
    server = FakeServer()
    started: list[str] = []

    @asynccontextmanager
    async def fake_session():
        started.append("start")
        try:
            yield server
        finally:
            started.append("stop")

    monkeypatch.setattr(cpu_llm, "session", fake_session)
    monkeypatch.setattr(cpu_llm, "check_ready", lambda: (True, ""))
    ids, rows = _rows(23, "cpu")
    out = run(prep.run(rows))
    assert out == {"done": 23, "calls": 3, "error": ""} and started == ["start", "stop"]
    assert [c["count"] for c in server.calls] == [10, 10, 3]
    first = server.calls[0]
    assert first["max_tokens"] == 2500 and first["messages"][0]["content"] == prep.SYSTEM_CPU and "英日翻訳者" in prep.SYSTEM_CPU
    assert "見本" in first["messages"][1]["content"] and "OpenAI が新しい推論モデル" in first["messages"][1]["content"]
    assert set(first["schema"]["required"]) == {str(n) for n in range(1, 11)} and first["schema"]["additionalProperties"] is False
    assert first["schema"]["properties"]["1"]["properties"]["ja"]["maxLength"] <= 240
    with news_db.session() as db:
        assert all(r.prep_tries == prep.PREP_DONE and r.summary_ja for r in db.scalars(select(NewsItem).where(NewsItem.id.in_(ids))))
    assert "見本" not in prep.build_prompt([{"title": "t", "source": "s", "summary": ""}])      # 今までのモデルの指示は変えない


def test_cpu_prep_reports_a_start_failure_without_marking_items(monkeypatch, prep_counter):
    """目的: llama-server を起動できなかった・未導入のときは、理由を返し、記事の試行回数を進めないこと。"""
    cfg = settings.load(); cfg["llm"]["prep_model"] = cpu_llm.MODEL_ID; settings.save(cfg)
    ids, rows = _rows(2, "cpufail")
    monkeypatch.setattr(cpu_llm, "check_ready", lambda: (False, "モデルが未導入です"))
    assert run(prep.run(rows)) == {"done": 0, "calls": 0, "error": "モデルが未導入です"}
    monkeypatch.setattr(cpu_llm, "check_ready", lambda: (True, ""))

    @asynccontextmanager
    async def broken():
        raise cpu_llm.CpuLlmUnavailable("起動できません")
        yield  # pragma: no cover

    monkeypatch.setattr(cpu_llm, "session", broken)
    assert run(prep.run(rows))["error"] == "起動できません"
    with news_db.session() as db:
        assert all(r.prep_tries == 0 for r in db.scalars(select(NewsItem).where(NewsItem.id.in_(ids))))


def test_settings_default_is_cpu_and_cpu_is_prep_only(tmp_path, monkeypatch):
    """目的: 下ごしらえの既定は CPU の Gemma。知らない cpu: は断り、まとめ・一言要約には CPU を選べず、今までのモデルは選べること。"""
    assert settings.normalize({})["llm"]["prep_model"] == "cpu:gemma-4-e2b" == cpu_llm.MODEL_ID
    assert settings.normalize({"llm": {"prep_model": "cloud:openrouter/x/y:free"}})["llm"]["prep_model"] == "cloud:openrouter/x/y:free"
    assert settings.normalize({"llm": {"prep_model": "auto"}})["llm"]["prep_model"] == "auto"
    with pytest.raises(ConfigError):
        settings.normalize({"llm": {"prep_model": "cpu:big"}})
    with pytest.raises(ConfigError):
        settings.normalize({"llm": {"gist_model": "cpu:gemma-4-e2b"}})
    with pytest.raises(ConfigError):
        settings.normalize({"llm": {"digest_model": "cpu:gemma-4-e2b"}})
    assert settings.normalize({"llm": {"gist_model": "nemotron"}})["llm"]["gist_model"] == "nemotron"       # 保存済みの設定（gist_model）は変わらない


def test_feature_status_install_and_uninstall(tmp_path, monkeypatch):
    """目的: 機能「ニュースの下ごしらえ（CPU の LLM）」: 未導入 → ファイルがあれば導入済み（置いてあるものは導入済みとみなす）→ アンインストールでファイルだけ消える。
    llama.cpp が未導入なら案内（requires）を出すこと。取得は版固定の URL・sha256 つき。"""
    monkeypatch.setattr(cpu_llm, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(cpu_llm, "server_binary", lambda: tmp_path / "none")
    st = registry.status("news-cpu-llm")
    assert st["installed"] is False and st["requires"] == "llama-cpp" and st["requires_installed"] is False and st["kind"] == "cpu-llm"
    cpu_llm.models_dir().mkdir(parents=True)
    cpu_llm.model_path().write_bytes(b"x")
    st = registry.status("news-cpu-llm")
    assert st["installed"] is True and st["version"] == cpu_llm.FILENAME and st["cpu_llm"]["installed"] is True and "llama.cpp" in st["error"]
    after = registry.uninstall("news-cpu-llm")
    assert after["installed"] is False and not cpu_llm.model_path().exists()
    with pytest.raises(registry.FeatureError):
        registry.update("news-cpu-llm")
    assert cpu_llm.REVISION in cpu_llm.URL and len(cpu_llm.SHA256) == 64 and cpu_llm.URL.startswith("https://huggingface.co/ggml-org/")
