"""ワークフローノードの実行関数。

各ノードは config と context（先行ノードの出力）を受け取り、出力 dict を返す。
任意シェル実行ノードは提供しない（安全モード、要求仕様 §20.6）。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

TEMPLATE_RE = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


def render_template(text: str, context: dict[str, Any]) -> str:
    """{{nodeId.field.subfield}} を先行ノード出力で置換する。"""

    def repl(m: re.Match) -> str:
        parts = m.group(1).split(".")
        value: Any = context.get(parts[0], {}).get("output", {})
        for part in parts[1:]:
            if isinstance(value, dict):
                value = value.get(part)
            else:
                return ""
        if value is None:
            return ""
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    return TEMPLATE_RE.sub(repl, text)


class NodeError(RuntimeError):
    pass


def _require_app(config: dict) -> int:
    app_id = config.get("app_id")
    if not isinstance(app_id, int):
        raise NodeError("アプリが選択されていません")
    return app_id


async def _app_control(action: str, config: dict) -> dict:
    from app.applications import service as apps
    from app.applications import systemd as sd
    from app.database import SessionLocal
    from app.models import ManagedApplication

    app_id = _require_app(config)

    def run() -> dict:
        db = SessionLocal()
        try:
            app = db.get(ManagedApplication, app_id)
            if app is None:
                raise NodeError(f"アプリ id={app_id} が見つかりません")
            if action == "status":
                rt = apps.runtime_info(app)
                return {"app": app.name, "status": rt.status, "pid": rt.pid, "uptime_seconds": rt.uptime_seconds}
            if action == "start" and app.application_type != "systemd_service":
                apps.sync_unit(app)
                sd.reset_failed(app.systemd_unit_name)
            fn = {"start": sd.start, "stop": sd.stop, "restart": sd.restart}[action]
            ok, err = fn(app.systemd_unit_name)
            if not ok:
                raise NodeError(f"{action} 失敗: {err}")
            rt = apps.runtime_info(app)
            return {"app": app.name, "ok": True, "status": rt.status}
        finally:
            db.close()

    return await asyncio.to_thread(run)


async def node_app_start(config: dict, ctx: dict) -> dict:
    return await _app_control("start", config)


async def node_app_stop(config: dict, ctx: dict) -> dict:
    return await _app_control("stop", config)


async def node_app_restart(config: dict, ctx: dict) -> dict:
    return await _app_control("restart", config)


async def node_app_status(config: dict, ctx: dict) -> dict:
    return await _app_control("status", config)


async def node_http_request(config: dict, ctx: dict) -> dict:
    url = render_template(str(config.get("url", "")), ctx)
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    method = str(config.get("method", "GET")).upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "HEAD"):
        raise NodeError(f"不正なメソッド: {method}")
    timeout = min(float(config.get("timeout", 30)), 300)
    body = config.get("body")
    if isinstance(body, str) and body.strip():
        body = render_template(body, ctx)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.request(method, url, content=body if isinstance(body, str) else None)
    expect = config.get("expect_status")
    ok = (r.status_code == int(expect)) if expect else r.status_code < 400
    output = {"status_code": r.status_code, "ok": ok, "body": r.text[:4096]}
    if expect and not ok:
        raise NodeError(f"期待ステータス {expect} に対し {r.status_code}")
    return output


OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: _num(a) > _num(b),
    "gte": lambda a, b: _num(a) >= _num(b),
    "lt": lambda a, b: _num(a) < _num(b),
    "lte": lambda a, b: _num(a) <= _num(b),
    "contains": lambda a, b: str(b) in str(a),
}


def _num(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        raise NodeError(f"数値比較できません: {v!r}")


async def node_condition(config: dict, ctx: dict) -> dict:
    left = render_template(str(config.get("left", "")), ctx)
    right = render_template(str(config.get("right", "")), ctx)
    op = str(config.get("op", "eq"))
    if op not in OPS:
        raise NodeError(f"不正な演算子: {op}")
    result = OPS[op](left, right)
    return {"result": bool(result), "left": left, "right": right}


async def node_wait(config: dict, ctx: dict) -> dict:
    seconds = min(float(config.get("seconds", 1)), 3600)
    await asyncio.sleep(seconds)
    return {"waited_seconds": seconds}


async def node_webhook(config: dict, ctx: dict) -> dict:
    url = str(config.get("url", ""))
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    message = render_template(str(config.get("message", "")), ctx)
    fmt = config.get("format", "generic")
    payload = {
        "generic": {"message": message},
        "discord": {"content": message},
        "slack": {"text": message},
    }.get(fmt, {"message": message})
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, json=payload)
    return {"status_code": r.status_code, "ok": r.status_code < 400}


async def node_file_exists(config: dict, ctx: dict) -> dict:
    from app.files.service import FileAccessError, resolve

    path = render_template(str(config.get("path", "")), ctx)
    try:
        p = resolve(path)
        return {"exists": True, "is_dir": p.is_dir(), "size": p.stat().st_size}
    except FileNotFoundError:
        return {"exists": False}
    except FileAccessError as e:
        raise NodeError(str(e))


async def node_trigger(config: dict, ctx: dict) -> dict:
    return {"ok": True}


# ---- 変数・文字列・Markdown ----


async def node_set_variable(config: dict, ctx: dict) -> dict:
    value = render_template(str(config.get("value", "")), ctx)
    return {"value": value, "name": config.get("name", "value")}


async def node_string_op(config: dict, ctx: dict) -> dict:
    text = render_template(str(config.get("text", "")), ctx)
    op = config.get("op", "upper")
    if op == "upper":
        result = text.upper()
    elif op == "lower":
        result = text.lower()
    elif op == "trim":
        result = text.strip()
    elif op == "replace":
        result = text.replace(str(config.get("find", "")), render_template(str(config.get("replace", "")), ctx))
    elif op == "regex_extract":
        m = re.search(str(config.get("pattern", "")), text)
        result = (m.group(config.get("group", 0)) if m else "") if m else ""
    elif op == "split":
        return {"result": text.split(str(config.get("sep", "\n"))), "text": text}
    elif op == "length":
        return {"result": len(text), "text": text}
    elif op == "template":
        result = text  # すでにテンプレート展開済み
    elif op == "json_extract":
        try:
            data = json.loads(text)
            for key in str(config.get("path", "")).split("."):
                if key:
                    data = data[key] if isinstance(data, dict) else data[int(key)]
            result = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        except (json.JSONDecodeError, KeyError, IndexError, ValueError) as e:
            raise NodeError(f"JSON 抽出失敗: {e}")
    else:
        raise NodeError(f"不明な文字列操作: {op}")
    return {"result": result, "text": text}


async def node_markdown(config: dict, ctx: dict) -> dict:
    import markdown as md

    text = render_template(str(config.get("text", "")), ctx)
    html = md.markdown(text, extensions=["fenced_code", "tables"])
    return {"html": html, "markdown": text}


# ---- ファイル入出力（許可ルート検証を通す） ----


async def node_file_read(config: dict, ctx: dict) -> dict:
    from app.files.service import FileAccessError, read_text

    path = render_template(str(config.get("path", "")), ctx)
    try:
        return {"content": read_text(path), "path": path}
    except (FileAccessError, FileNotFoundError, OSError) as e:
        raise NodeError(f"ファイル読み込み失敗: {e}")


async def node_file_write(config: dict, ctx: dict) -> dict:
    from app.files.service import FileAccessError, write_text

    path = render_template(str(config.get("path", "")), ctx)
    content = render_template(str(config.get("content", "")), ctx)
    if config.get("append"):
        from app.files.service import read_text

        try:
            content = read_text(path) + content
        except FileNotFoundError:
            pass
    try:
        write_text(path, content)
        return {"path": path, "bytes": len(content.encode())}
    except (FileAccessError, OSError) as e:
        raise NodeError(f"ファイル書き込み失敗: {e}")


async def node_file_op(config: dict, ctx: dict) -> dict:
    from app.files import service as files

    op = config.get("op", "copy")
    src = render_template(str(config.get("source", "")), ctx)
    try:
        if op == "copy":
            return {"path": files.copy(src, render_template(str(config.get("dest_dir", "")), ctx))}
        if op == "move":
            return {"path": files.move(src, render_template(str(config.get("dest_dir", "")), ctx))}
        if op == "delete":
            files.delete(src)
            return {"deleted": src}
        if op == "mkdir":
            files.make_directory(src)
            return {"created": src}
        raise NodeError(f"不明なファイル操作: {op}")
    except (files.FileAccessError, FileNotFoundError, FileExistsError, OSError) as e:
        raise NodeError(f"ファイル操作失敗: {e}")


# ---- LLM（OpenAI 互換 Chat Completions: Ollama / vLLM / llama.cpp / OpenAI 等） ----


async def node_llm(config: dict, ctx: dict) -> dict:
    base_url = str(config.get("base_url", "http://127.0.0.1:11434/v1")).rstrip("/")
    model = str(config.get("model", "llama3"))
    prompt = render_template(str(config.get("prompt", "")), ctx)
    system = render_template(str(config.get("system", "")), ctx)
    api_key = str(config.get("api_key", "") or "sk-no-key")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": float(config.get("temperature", 0.7)),
        "stream": False,
    }
    if config.get("max_tokens"):
        payload["max_tokens"] = int(config["max_tokens"])
    timeout = min(float(config.get("timeout", 120)), 600)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except httpx.HTTPError as e:
        raise NodeError(f"LLM 接続失敗: {e}")
    if r.status_code >= 400:
        raise NodeError(f"LLM エラー {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise NodeError("LLM 応答の解析に失敗しました")
    usage = data.get("usage", {})
    return {"content": content, "model": model, "tokens": usage.get("total_tokens")}


# ---- Web スクレイピング ----


async def node_scrape(config: dict, ctx: dict) -> dict:
    from bs4 import BeautifulSoup

    url = render_template(str(config.get("url", "")), ctx)
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    selector = str(config.get("selector", "")).strip()
    attr = str(config.get("attribute", "")).strip()
    timeout = min(float(config.get("timeout", 30)), 120)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ControlDeck/1.0"}) as client:
            r = await client.get(url)
    except httpx.HTTPError as e:
        raise NodeError(f"取得失敗: {e}")
    soup = BeautifulSoup(r.text, "html.parser")
    if not selector:
        return {"text": soup.get_text(" ", strip=True)[:20000], "status_code": r.status_code}
    elements = soup.select(selector)
    if attr:
        results = [el.get(attr, "") for el in elements]
    else:
        results = [el.get_text(" ", strip=True) for el in elements]
    return {"results": results, "count": len(results), "first": results[0] if results else "", "status_code": r.status_code}


# ---- OCR（tesseract） ----


async def node_ocr(config: dict, ctx: dict) -> dict:
    import shutil as _shutil

    from app.files.service import FileAccessError, resolve

    if not _shutil.which("tesseract"):
        raise NodeError("tesseract が未インストールです（sudo apt install tesseract-ocr tesseract-ocr-jpn）")
    path = render_template(str(config.get("path", "")), ctx)
    try:
        p = resolve(path)
    except (FileAccessError, FileNotFoundError) as e:
        raise NodeError(str(e))
    lang = str(config.get("lang", "jpn+eng"))

    def run() -> str:
        import subprocess

        r = subprocess.run(
            ["tesseract", str(p), "stdout", "-l", lang],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise NodeError(f"OCR 失敗: {r.stderr[:200]}")
        return r.stdout

    text = await asyncio.to_thread(run)
    return {"text": text.strip(), "chars": len(text)}


# ---- Wake-on-LAN ----


async def node_wol(config: dict, ctx: dict) -> dict:
    import socket

    mac = render_template(str(config.get("mac", "")), ctx).replace("-", "").replace(":", "").strip()
    if len(mac) != 12 or not re.fullmatch(r"[0-9a-fA-F]{12}", mac):
        raise NodeError(f"不正な MAC アドレス: {mac}")
    packet = b"\xff" * 6 + bytes.fromhex(mac) * 16
    broadcast = str(config.get("broadcast", "255.255.255.255"))
    port = int(config.get("port", 9))

    def send() -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(packet, (broadcast, port))

    await asyncio.to_thread(send)
    return {"sent": True, "mac": mac}


# ---- SSH / Git / C++ ビルド / Python 実行（コマンド実行系） ----
# これらは登録済みコマンド相当。shell=False の配列実行で、shell 文字列連結はしない。


async def _run_command(argv: list[str], cwd: str | None, timeout: float, input_text: str | None = None) -> dict:
    def run() -> dict:
        import subprocess

        r = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or None,
            input=input_text,
        )
        return {
            "exit_code": r.returncode,
            "stdout": r.stdout[-16000:],
            "stderr": r.stderr[-8000:],
            "ok": r.returncode == 0,
        }

    try:
        result = await asyncio.to_thread(run)
    except FileNotFoundError as e:
        raise NodeError(f"コマンドが見つかりません: {e}")
    except Exception as e:
        raise NodeError(f"実行失敗: {e}")
    if not result["ok"]:
        result["error"] = result["stderr"][:500] or f"exit {result['exit_code']}"
    return result


def _resolve_cwd(config: dict, ctx: dict) -> str | None:
    from app.files.service import FileAccessError, resolve

    raw = render_template(str(config.get("cwd", "")), ctx).strip()
    if not raw:
        return None
    try:
        return str(resolve(raw))
    except (FileAccessError, FileNotFoundError) as e:
        raise NodeError(f"作業ディレクトリが不正です: {e}")


async def node_ssh(config: dict, ctx: dict) -> dict:
    host = render_template(str(config.get("host", "")), ctx).strip()
    user = render_template(str(config.get("user", "")), ctx).strip()
    command = render_template(str(config.get("command", "")), ctx)
    if not host or not command:
        raise NodeError("host と command は必須です")
    if not re.fullmatch(r"[A-Za-z0-9._@-]+", host) or (user and not re.fullmatch(r"[A-Za-z0-9._-]+", user)):
        raise NodeError("host / user に使用できない文字が含まれます")
    target = f"{user}@{host}" if user else host
    port = str(int(config.get("port", 22)))
    # 鍵認証・非対話（BatchMode）。パスワードは扱わない
    argv = [
        "ssh", "-p", port,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        target, "--", command,
    ]
    timeout = min(float(config.get("timeout", 120)), 600)
    return await _run_command(argv, None, timeout)


GIT_SUBCOMMANDS = {
    "status", "pull", "push", "fetch", "add", "commit", "checkout", "clone",
    "log", "diff", "branch", "merge", "reset", "stash", "tag", "remote", "rev-parse",
}


async def node_git(config: dict, ctx: dict) -> dict:
    subcommand = str(config.get("subcommand", "status")).strip()
    if subcommand not in GIT_SUBCOMMANDS:
        raise NodeError(f"許可されていない git サブコマンド: {subcommand}")
    args_raw = render_template(str(config.get("args", "")), ctx).strip()
    import shlex

    extra = shlex.split(args_raw) if args_raw else []
    cwd = _resolve_cwd(config, ctx)
    timeout = min(float(config.get("timeout", 120)), 600)
    return await _run_command(["git", subcommand, *extra], cwd, timeout)


async def node_cpp_build(config: dict, ctx: dict) -> dict:
    cwd = _resolve_cwd(config, ctx)
    system = config.get("system", "cmake")
    timeout = min(float(config.get("timeout", 600)), 1800)
    if system == "cmake":
        build_dir = render_template(str(config.get("build_dir", "build")), ctx) or "build"
        import shlex

        configure = await _run_command(["cmake", "-S", ".", "-B", build_dir, *shlex.split(str(config.get("cmake_args", "")))], cwd, timeout)
        if not configure["ok"]:
            return {"stage": "configure", **configure}
        build = await _run_command(["cmake", "--build", build_dir, "-j"], cwd, timeout)
        return {"stage": "build", **build}
    if system == "make":
        import shlex

        return await _run_command(["make", *shlex.split(str(config.get("make_args", "")))], cwd, timeout)
    raise NodeError(f"不明なビルドシステム: {system}")


async def node_python_exec(config: dict, ctx: dict) -> dict:
    """Python コード実行。任意コード実行のため設定で明示的に許可された場合のみ動作する。"""
    from app.config import get_config

    if not get_config().security.allow_arbitrary_commands:
        raise NodeError(
            "Python コード実行は無効です。config.yaml の security.allow_arbitrary_commands: true で許可してください"
        )
    from app.config import data_dir

    code = str(config.get("code", ""))
    stdin_text = render_template(str(config.get("stdin", "")), ctx)
    cwd = _resolve_cwd(config, ctx)
    python_bin = str(data_dir().parent) and None  # プレースホルダ回避
    import sys

    timeout = min(float(config.get("timeout", 120)), 600)
    # venv の python で -c 実行（配列引数、shell 経由なし）
    return await _run_command([sys.executable, "-I", "-c", code], cwd, timeout, input_text=stdin_text)


# ---- RAG（埋め込み + SQLite ベクトルストア） ----


async def node_rag_build(config: dict, ctx: dict) -> dict:
    from app.workflows import rag

    collection = str(config.get("collection", "default"))
    text = render_template(str(config.get("text", "")), ctx)
    # text が空でパスが指定されていればファイルから読む
    if not text.strip() and config.get("path"):
        from app.files.service import FileAccessError, read_text

        try:
            text = read_text(render_template(str(config["path"]), ctx))
        except (FileAccessError, FileNotFoundError, OSError) as e:
            raise NodeError(str(e))
    try:
        return await rag.build(
            collection=collection,
            text=text,
            source=str(config.get("source", "workflow")),
            base_url=str(config.get("base_url", "http://127.0.0.1:11434/v1")),
            model=str(config.get("embed_model", "nomic-embed-text")),
            api_key=str(config.get("api_key", "")),
            reset=bool(config.get("reset")),
        )
    except (ValueError, RuntimeError) as e:
        raise NodeError(f"RAG 構築失敗: {e}")


async def node_rag_query(config: dict, ctx: dict) -> dict:
    from app.workflows import rag

    question = render_template(str(config.get("question", "")), ctx)
    if not question.strip():
        raise NodeError("質問が空です")
    try:
        return await rag.query(
            collection=str(config.get("collection", "default")),
            question=question,
            top_k=int(config.get("top_k", 4)),
            base_url=str(config.get("base_url", "http://127.0.0.1:11434/v1")),
            model=str(config.get("embed_model", "nomic-embed-text")),
            api_key=str(config.get("api_key", "")),
        )
    except (ValueError, RuntimeError) as e:
        raise NodeError(f"RAG 検索失敗: {e}")


# ---- データベース操作 ----

_DDL_DML_RE = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH|PRAGMA|REPLACE)\b", re.IGNORECASE)


async def node_db_query(config: dict, ctx: dict) -> dict:
    """SQLite（許可ルート配下のファイル）または任意 SQLAlchemy URL に対して SQL を実行する。"""
    from sqlalchemy import create_engine, text as sql_text

    engine_kind = config.get("engine", "sqlite")
    sql = render_template(str(config.get("query", "")), ctx).strip()
    if not sql:
        raise NodeError("SQL が空です")
    if not _DDL_DML_RE.match(sql):
        raise NodeError("先頭が SELECT/INSERT/UPDATE/DELETE/CREATE 等でない SQL は実行できません")

    if engine_kind == "sqlite":
        from app.files.service import FileAccessError, resolve

        raw = render_template(str(config.get("path", "")), ctx)
        try:
            db_path = resolve(raw, must_exist=False)
        except FileAccessError as e:
            raise NodeError(f"DB パスが不正です: {e}")
        url = f"sqlite:///{db_path}"
    else:
        url = render_template(str(config.get("url", "")), ctx).strip()
        if not url:
            raise NodeError("接続 URL が空です")

    params_raw = config.get("params")
    params: dict = {}
    if isinstance(params_raw, str) and params_raw.strip():
        try:
            params = json.loads(render_template(params_raw, ctx))
        except json.JSONDecodeError as e:
            raise NodeError(f"パラメータ JSON が不正です: {e}")
    elif isinstance(params_raw, dict):
        params = params_raw

    def run() -> dict:
        eng = create_engine(url)
        try:
            with eng.begin() as conn:
                result = conn.execute(sql_text(sql), params)
                if result.returns_rows:
                    rows = [dict(r._mapping) for r in result.fetchmany(500)]
                    return {"rows": rows, "row_count": len(rows), "columns": list(result.keys())}
                return {"rows": [], "row_count": result.rowcount, "affected": result.rowcount}
        finally:
            eng.dispose()

    try:
        return await asyncio.to_thread(run)
    except Exception as e:
        raise NodeError(f"DB エラー: {type(e).__name__}: {str(e)[:300]}")


# ---- ブラウザ操作（Playwright があれば） ----


async def node_browser(config: dict, ctx: dict) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise NodeError("Playwright が未インストールです（.venv で pip install playwright && playwright install chromium）")
    url = render_template(str(config.get("url", "")), ctx)
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    action = config.get("action", "content")
    selector = str(config.get("selector", "")).strip()
    timeout = min(float(config.get("timeout", 60)), 180) * 1000
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await (await browser.new_context()).new_page()
            await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            output: dict = {"url": url, "title": await page.title()}
            if action == "screenshot":
                from app.files.service import resolve

                out_path = render_template(str(config.get("output_path", "")), ctx)
                dest = resolve(out_path, must_exist=False)
                await page.screenshot(path=str(dest), full_page=bool(config.get("full_page")))
                output["screenshot"] = str(dest)
            elif action == "text" and selector:
                el = await page.query_selector(selector)
                output["text"] = (await el.inner_text()) if el else ""
            else:
                output["content"] = (await page.content())[:20000]
            await browser.close()
            return output
    except Exception as e:
        raise NodeError(f"ブラウザ操作失敗: {type(e).__name__}: {e}")


NODE_EXECUTORS = {
    "trigger": node_trigger,
    "app.start": node_app_start,
    "app.stop": node_app_stop,
    "app.restart": node_app_restart,
    "app.status": node_app_status,
    "http.request": node_http_request,
    "condition.if": node_condition,
    "util.wait": node_wait,
    "notify.webhook": node_webhook,
    "file.exists": node_file_exists,
    # v2 追加
    "var.set": node_set_variable,
    "string.op": node_string_op,
    "text.markdown": node_markdown,
    "file.read": node_file_read,
    "file.write": node_file_write,
    "file.op": node_file_op,
    "llm.chat": node_llm,
    "web.scrape": node_scrape,
    "media.ocr": node_ocr,
    "net.wol": node_wol,
    "cmd.ssh": node_ssh,
    "cmd.git": node_git,
    "cmd.cpp_build": node_cpp_build,
    "cmd.python": node_python_exec,
    "web.browser": node_browser,
    "rag.build": node_rag_build,
    "rag.query": node_rag_query,
    "db.query": node_db_query,
}

# ノードごとの既定タイムアウト（秒）
NODE_TIMEOUTS = {
    "util.wait": 3700,
    "http.request": 320,
    "llm.chat": 620,
    "web.scrape": 130,
    "media.ocr": 130,
    "cmd.ssh": 620,
    "cmd.git": 620,
    "cmd.cpp_build": 1820,
    "cmd.python": 620,
    "web.browser": 190,
    "rag.build": 620,
    "rag.query": 320,
    "db.query": 320,
}
DEFAULT_NODE_TIMEOUT = 120
