"""ワークフローノードの実行関数。

各ノードは config と context（先行ノードの出力）を受け取り、出力 dict を返す。
任意シェル実行ノードは提供しない（安全モード、要求仕様 §20.6）。
"""
from __future__ import annotations

import asyncio
import copy
import csv
import contextvars
import heapq
import io
import json
import re
from pathlib import Path
from typing import Any

import httpx

TEMPLATE_RE = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")
_progress_reporter: contextvars.ContextVar[Any] = contextvars.ContextVar("workflow_node_progress", default=None)


def report_progress(message: str, current: int = 0, total: int = 0) -> None:
    reporter = _progress_reporter.get()
    if reporter is not None:
        reporter(message, current, total)


def render_template(text: str, context: dict[str, Any]) -> str:
    """{{nodeId.field.subfield}} を先行ノード出力で置換する。

    {{vars.名前}} / {{vars.名前.フィールド}} で名前付き変数（ノード設定の
    「出力変数名」で保存された出力）も参照できる。
    """

    def repl(m: re.Match) -> str:
        parts = m.group(1).split(".")
        if parts[0] == "vars":
            if len(parts) < 2:
                return ""
            value: Any = (context.get("__vars__") or {}).get(parts[1])
            rest = parts[2:]
        elif parts[0] == "secrets":
            # {{secrets.名前}}: 暗号化ストアの値（engine が実行開始時に復号注入）
            if len(parts) < 2:
                return ""
            value = (context.get("__secrets__") or {}).get(parts[1], "")
            rest = parts[2:]
        else:
            value = context.get(parts[0], {}).get("output", {})
            rest = parts[1:]
        for part in rest:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list):
                try:
                    value = value[int(part)]
                except (ValueError, IndexError):
                    return ""
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
    # チャットフロー等の入力を出力へ展開（{{trigger.message}} で参照可能）
    inp = ctx.get("__input__") or {}
    out = {"ok": True, "message": str(inp.get("message", ""))}
    for k, v in inp.items():
        if k != "message":
            out[k] = v
    return out


async def node_signal_display(config: dict, ctx: dict) -> dict:
    """信号表示ノード。入力値を記録し、チャットウィンドウへ表示するために出力へ格納する。"""
    signal = str(config.get("signal", "output"))
    value = render_template(str(config.get("value", "")), ctx)
    return {"signal": signal, "value": value, "display": True}


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


MAX_TRANSFORM_BYTES = 2 * 1024 * 1024


def _json_value(raw: Any, ctx: dict, *, label: str = "JSON") -> Any:
    if isinstance(raw, (dict, list, int, float, bool)) or raw is None:
        return copy.deepcopy(raw)
    text = render_template(str(raw or ""), ctx)
    if len(text.encode("utf-8")) > MAX_TRANSFORM_BYTES:
        raise NodeError(f"{label}が2MiB上限を超えました")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise NodeError(f"{label}が不正です: {exc}") from exc


def _path_parts(raw: Any) -> list[str]:
    parts = [part for part in str(raw or "").split(".") if part]
    if not parts:
        raise NodeError("pathを指定してください")
    return parts


async def node_data_transform(config: dict, ctx: dict) -> dict:
    report_progress("データを変換中", 0, 1)
    operation = str(config.get("operation") or "json_parse")
    raw_input = config.get("input", "")
    if operation == "json_parse":
        value = _json_value(raw_input, ctx)
        return {"value": value, "valid": True}
    if operation in {"json_get", "json_set", "schema_validate", "json_to_csv"}:
        value = _json_value(raw_input, ctx)
    if operation == "json_get":
        result = value
        try:
            for part in _path_parts(config.get("path")):
                result = result[int(part)] if isinstance(result, list) else result[part]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise NodeError(f"JSON pathが見つかりません: {exc}") from exc
        return {"value": result, "valid": True}
    if operation == "json_set":
        result = copy.deepcopy(value)
        parts = _path_parts(config.get("path"))
        target = result
        try:
            for part in parts[:-1]:
                target = target[int(part)] if isinstance(target, list) else target[part]
            new_value_raw = render_template(str(config.get("value", "null")), ctx)
            if len(new_value_raw.encode("utf-8")) > MAX_TRANSFORM_BYTES:
                raise NodeError("設定値が2MiB上限を超えました")
            try:
                new_value = json.loads(new_value_raw)
            except json.JSONDecodeError:
                new_value = new_value_raw
            if isinstance(target, list):
                target[int(parts[-1])] = new_value
            elif isinstance(target, dict):
                target[parts[-1]] = new_value
            else:
                raise TypeError("更新対象がobject/arrayではありません")
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise NodeError(f"JSON更新失敗: {exc}") from exc
        return {"value": result, "valid": True}
    if operation == "schema_validate":
        from jsonschema import Draft202012Validator, SchemaError

        schema = _json_value(config.get("schema", ""), ctx, label="JSON Schema")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise NodeError(f"JSON Schemaが不正です: {exc.message}") from exc
        errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path))
        messages = [f"{'/'.join(str(x) for x in error.path) or '$'}: {error.message}"[:500] for error in errors[:50]]
        return {"value": value, "valid": not errors, "errors": messages}
    if operation == "csv_to_json":
        text = render_template(str(raw_input or ""), ctx)
        if len(text.encode("utf-8")) > MAX_TRANSFORM_BYTES:
            raise NodeError("CSVが2MiB上限を超えました")
        try:
            rows = list(csv.DictReader(io.StringIO(text), delimiter=str(config.get("delimiter") or ",")[:1]))
        except csv.Error as exc:
            raise NodeError(f"CSV解析失敗: {exc}") from exc
        if len(rows) > 10_000:
            raise NodeError("CSV行数が10000件上限を超えました")
        return {"value": rows, "rows": rows, "count": len(rows), "valid": True}
    if operation == "json_to_csv":
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            raise NodeError("json_to_csvのinputはobject配列にしてください")
        if len(value) > 10_000:
            raise NodeError("JSON行数が10000件上限を超えました")
        fields = list(dict.fromkeys(str(key) for row in value for key in row))
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore",
                                delimiter=str(config.get("delimiter") or ",")[:1])
        writer.writeheader()
        writer.writerows(value)
        text = output.getvalue()
        if len(text.encode("utf-8")) > MAX_TRANSFORM_BYTES:
            raise NodeError("CSV出力が2MiB上限を超えました")
        return {"value": text, "csv": text, "count": len(value), "valid": True}
    raise NodeError(f"未対応のデータ変換です: {operation}")


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


async def node_file_glob(config: dict, ctx: dict) -> dict:
    from app.files import service as files

    base_raw = render_template(str(config.get("base_path") or ""), ctx)
    pattern = render_template(str(config.get("pattern") or "*"), ctx).strip()
    pattern_path = Path(pattern)
    if pattern_path.is_absolute() or ".." in pattern_path.parts:
        raise NodeError("glob patternはbase_pathからの相対指定にしてください")
    if bool(config.get("recursive")) and "**" not in pattern_path.parts:
        pattern = f"**/{pattern}"
    kind = str(config.get("kind") or "all")
    if kind not in {"all", "files", "directories"}:
        raise NodeError("kindが不正です")
    limit = max(1, min(int(config.get("limit") or 100), 1000))
    try:
        base = files.resolve(base_raw)
    except (files.FileAccessError, FileNotFoundError) as exc:
        raise NodeError(f"base pathが不正です: {exc}") from exc
    if not base.is_dir():
        raise NodeError("base pathはディレクトリを指定してください")
    report_progress("ファイルを検索中", 0, limit)

    def scan() -> list[dict]:
        def validated():
            for candidate in base.glob(pattern):
                try:
                    resolved = files.resolve(str(candidate))
                except (files.FileAccessError, FileNotFoundError, OSError):
                    continue
                if not resolved.is_relative_to(base):
                    continue
                is_dir = resolved.is_dir()
                if (kind == "files" and is_dir) or (kind == "directories" and not is_dir):
                    continue
                stat = resolved.stat()
                yield {
                    "path": str(resolved), "relative_path": str(resolved.relative_to(base)),
                    "name": resolved.name, "size": stat.st_size, "is_dir": is_dir,
                }

        try:
            matches = heapq.nsmallest(limit, validated(), key=lambda item: item["path"])
        except (OSError, ValueError) as exc:
            raise NodeError(f"glob検索失敗: {exc}") from exc
        report_progress("ファイル検索完了", len(matches), len(matches))
        return matches

    matches = await asyncio.to_thread(scan)
    return {"matches": matches, "paths": [item["path"] for item in matches], "count": len(matches)}


# ---- LLM（OpenAI 互換 Chat Completions: Ollama / vLLM / llama.cpp / OpenAI 等） ----


def _strip_json_fences(text: str) -> str:
    """```json ... ``` フェンスを剥がす（ローカル LLM が付けがち）。"""
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()


async def node_llm(config: dict, ctx: dict) -> dict:
    from app.models_mgmt.runtime_provider import response_format_candidates
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    base_url = str(config.get("base_url", "http://127.0.0.1:11434/v1")).rstrip("/")
    try:
        await asyncio.to_thread(ensure_gpu_profile, base_url=base_url)
    except RuntimeError as e:
        raise NodeError(str(e)) from e
    model = str(config.get("model", "llama3"))
    prompt = render_template(str(config.get("prompt", "")), ctx)
    system = render_template(str(config.get("system", "")), ctx)
    api_key = str(config.get("api_key", "") or "sk-no-key")
    if str(config.get("agent_tools", "") or "") == "1":
        # エージェントモード: LLM が既存ノードをツールとして自律的に呼ぶ
        return await _agent_llm(base_url, model, api_key, system, prompt, config, ctx)
    response_format = str(config.get("response_format", "") or "")
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
    # モデル常駐時間（大型モデルの都度ロード＝数十秒を防ぐ）。設定値、ノードで上書き可
    ka = config.get("keep_alive")
    if not ka:
        try:
            from app.models_mgmt import ollama

            ka = ollama.get_settings().get("default_keep_alive")
        except Exception:
            ka = "30m"
    payload["keep_alive"] = ka or "30m"
    # 無指定でも有限にする。reasoning modelが回答なしでcontext上限まで走るのを防ぐ。
    payload["max_tokens"] = int(config.get("max_tokens") or 2048)
    # 構造化出力（OpenAI 互換 response_format。非対応サーバーはエラーを返すので
    # その場合はプロンプト指示のみで動くよう再送する）
    schema_obj = None
    if response_format == "json_object":
        payload["response_format"] = {"type": "json_object"}
    elif response_format == "json_schema":
        raw_schema = render_template(str(config.get("json_schema", "")), ctx).strip()
        if not raw_schema:
            raise NodeError("JSON スキーマが空です")
        try:
            schema_obj = json.loads(raw_schema)
        except json.JSONDecodeError as e:
            raise NodeError(f"JSON スキーマが不正です: {e}")
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "output", "schema": schema_obj, "strict": True},
        }
    timeout = min(float(config.get("timeout", 120)), 600)

    # think(推論表示/オフ): 指定あり & Ollama ネイティブ & 構造化出力なし の場合に適用。
    # OpenAI 互換 API では think が効かないためネイティブ /api/chat を使う
    think = None
    try:
        from app.models_mgmt import ollama as _ol

        tc = config.get("think")
        think = _ol.normalize_think(tc) if tc not in (None, "") else _ol.effective_think(model)
    except Exception:
        think = None
    if think is not None and not response_format and base_url.endswith("/v1"):
        opts: dict = {"temperature": payload["temperature"]}
        opts["num_predict"] = payload["max_tokens"]
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(base_url[:-3] + "/api/chat", json={
                    "model": model, "messages": messages, "stream": False,
                    "think": think, "keep_alive": payload["keep_alive"], "options": opts})
        except httpx.HTTPError as e:
            raise NodeError(f"LLM 接続失敗: {e}")
        if r.status_code >= 400:
            raise NodeError(f"LLM エラー {r.status_code}: {r.text[:200]}")
        msg = r.json().get("message", {})
        return {"content": msg.get("content", ""), "thinking": msg.get("thinking", "") or "", "model": model}

    async def call(p: dict) -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(
                f"{base_url}/chat/completions",
                json=p,
                headers={"Authorization": f"Bearer {api_key}"},
            )

    try:
        r: httpx.Response | None = None
        candidates = response_format_candidates(payload.get("response_format"))
        for index, candidate in enumerate(candidates):
            attempt = dict(payload)
            if candidate is None:
                attempt.pop("response_format", None)
            else:
                attempt["response_format"] = candidate
            if index > 0:
                instruction = "必ず JSON のみで応答してください。"
                if schema_obj is not None:
                    instruction += " スキーマ: " + json.dumps(schema_obj, ensure_ascii=False)
                attempt["messages"] = [{"role": "system", "content": instruction}, *messages]
            r = await call(attempt)
            if r.status_code < 400 or r.status_code not in {400, 404, 415, 422, 501}:
                break
    except httpx.HTTPError as e:
        raise NodeError(f"LLM 接続失敗: {e}")
    if r is None:
        raise NodeError("LLM 応答がありません")
    if r.status_code >= 400:
        raise NodeError(f"LLM エラー {r.status_code}: {r.text[:200]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise NodeError("LLM 応答の解析に失敗しました")
    usage = data.get("usage", {})
    out: dict = {"content": content, "model": model, "tokens": usage.get("total_tokens")}
    if response_format in ("json_object", "json_schema"):
        try:
            out["json"] = json.loads(_strip_json_fences(content))
        except json.JSONDecodeError:
            out["json"] = None
            out["json_error"] = "応答を JSON として解析できませんでした"
    return out


async def node_ai_utility(config: dict, ctx: dict) -> dict:
    from app.models_mgmt.runtime_policy import ensure_gpu_profile

    operation = str(config.get("operation") or "embedding")
    if operation not in {"embedding", "rerank", "judge"}:
        raise NodeError(f"未対応のAI補助操作です: {operation}")
    base_url = render_template(str(config.get("base_url") or "http://127.0.0.1:11434/v1"), ctx).rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise NodeError("base_urlはhttp(s) URLで指定してください")
    model = render_template(str(config.get("model") or ""), ctx).strip()
    if not model:
        raise NodeError("modelを指定してください")
    api_key = str(config.get("api_key") or "sk-no-key")
    timeout = max(5.0, min(float(config.get("timeout") or 120), 300.0))
    report_progress("AI補助処理を準備中", 0, 2)
    try:
        await asyncio.to_thread(ensure_gpu_profile, base_url=base_url)
    except RuntimeError as exc:
        raise NodeError(str(exc)) from exc

    headers = {"Authorization": f"Bearer {api_key}"}
    if operation == "embedding":
        raw = render_template(str(config.get("input") or ""), ctx)
        try:
            parsed = json.loads(raw)
            inputs = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            inputs = [line for line in raw.splitlines() if line.strip()] or [raw]
        if not inputs or len(inputs) > 100 or any(not isinstance(item, str) or not item.strip() or len(item) > 32_768 for item in inputs):
            raise NodeError("embedding inputは文字列100件以内・各32KiB以内です")
        endpoint, payload = f"{base_url}/embeddings", {"model": model, "input": inputs}
    elif operation == "rerank":
        query = render_template(str(config.get("query") or ""), ctx).strip()
        raw_docs = render_template(str(config.get("documents") or ""), ctx)
        try:
            documents = json.loads(raw_docs)
        except json.JSONDecodeError:
            documents = [line for line in raw_docs.splitlines() if line.strip()]
        if not query or not isinstance(documents, list) or not documents:
            raise NodeError("rerankにはqueryとdocuments配列が必要です")
        if len(documents) > 100 or any(not isinstance(item, str) or len(item) > 32_768 for item in documents):
            raise NodeError("rerank documentsは文字列100件以内・各32KiB以内です")
        top_n = max(1, min(int(config.get("top_n") or len(documents)), len(documents)))
        endpoint = f"{base_url}/rerank"
        payload = {"model": model, "query": query, "documents": documents, "top_n": top_n}
    else:
        subject = render_template(str(config.get("input") or ""), ctx).strip()
        rubric = render_template(str(config.get("rubric") or "正確性・関連性・明瞭さを評価"), ctx).strip()
        if not subject or len(subject) > 65_536 or len(rubric) > 16_384:
            raise NodeError("judge input/rubricの長さが不正です")
        endpoint = f"{base_url}/chat/completions"
        payload = {
            "model": model, "stream": False, "temperature": 0,
            "max_tokens": max(64, min(int(config.get("max_tokens") or 512), 2048)),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "評価者として0〜100のscoreと簡潔なreasonをJSON objectだけで返してください。"},
                {"role": "user", "content": f"評価基準:\n{rubric}\n\n評価対象:\n{subject}"},
            ],
        }
    try:
        report_progress("AI補助APIを実行中", 1, 2)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(endpoint, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise NodeError(f"AI補助APIへ接続できません: {type(exc).__name__}") from exc
    if len(response.content) > 2 * 1024 * 1024:
        raise NodeError("AI補助API応答が2MiB上限を超えました")
    if response.status_code >= 400:
        raise NodeError(f"AI補助APIエラー {response.status_code}")
    try:
        data = response.json()
        if operation == "embedding":
            vectors = [item["embedding"] for item in data.get("data", [])]
            return {"vectors": vectors, "count": len(vectors), "dim": len(vectors[0]) if vectors else 0,
                    "model": data.get("model") or model}
        if operation == "rerank":
            normalized = []
            for item in data.get("results", []):
                index = int(item.get("index"))
                if index < 0 or index >= len(documents):
                    raise ValueError("rerank indexが範囲外です")
                normalized.append({"index": index, "score": item.get("relevance_score", item.get("score")),
                                   "document": item.get("document", documents[index] if index < len(documents) else "")})
            return {"results": normalized, "count": len(normalized), "model": model}
        content = data["choices"][0]["message"]["content"]
        judged = json.loads(_strip_json_fences(content))
        score = max(0.0, min(float(judged["score"]), 100.0))
        return {"score": score, "reason": str(judged.get("reason") or "")[:4000], "model": model}
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise NodeError("AI補助API応答を解析できません") from exc


# ---- エージェントモード（llm.chat 拡張。既存ノードをツールとして公開） ----

AGENT_TOOLS = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Web を検索してタイトル/URL/スニペットを得る",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "academic_search",
        "description": "学術ソース（OpenAlex/arXiv/Crossref 等）を串刺し検索する",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "rag_query",
        "description": "ローカルナレッジ（RAG）から関連文脈を検索する",
        "parameters": {"type": "object", "properties": {
            "collection": {"type": "string", "description": "コレクション名（既定 docs）"},
            "question": {"type": "string"}}, "required": ["question"]}}},
    {"type": "function", "function": {
        "name": "http_get",
        "description": "URL に GET リクエストして本文を得る（http/https のみ）",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "許可ルート配下のテキストファイルを読む",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]


async def _agent_call_tool(name: str, args: dict, ctx: dict) -> str:
    """ツール呼び出しを既存ノード実装へ委譲する（安全なサブセットのみ）。"""
    try:
        if name == "web_search":
            out = await node_web_search({"query": str(args.get("query", "")), "max_results": 6}, ctx)
            return out["text"][:4000] or "(結果なし)"
        if name == "academic_search":
            from app.workflows import external_search as ext

            fed = await ext.federated(str(args.get("query", "")), 5)
            return "\n\n".join(
                f"{x['title']} ({x.get('meta', {}).get('year', '')})\n{x.get('snippet', '')[:300]}\n{x.get('url', '')}"
                for x in fed["results"][:8]) or "(結果なし)"
        if name == "rag_query":
            out = await node_rag_query({"collection": str(args.get("collection", "docs") or "docs"),
                                        "question": str(args.get("question", ""))}, ctx)
            return str(out.get("context", ""))[:4000] or "(該当なし)"
        if name == "http_get":
            out = await node_http_request({"method": "GET", "url": str(args.get("url", ""))}, ctx)
            return str(out.get("body", ""))[:4000]
        if name == "read_file":
            out = await node_file_read({"path": str(args.get("path", ""))}, ctx)
            return str(out.get("content", ""))[:4000]
        return f"未知のツール: {name}"
    except NodeError as e:
        return f"ツールエラー: {e}"


async def _agent_llm(
    base_url: str, model: str, api_key: str, system: str, prompt: str, config: dict, ctx: dict
) -> dict:
    """tool calling で反復実行するエージェントループ。"""
    max_rounds = max(1, min(int(config.get("agent_max_steps", 6) or 6), 12))
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    tool_log: list[dict] = []
    timeout = min(float(config.get("timeout", 180)), 600)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for round_no in range(1, max_rounds + 1):
            try:
                r = await client.post(
                    f"{base_url}/chat/completions",
                    json={"model": model, "messages": messages, "tools": AGENT_TOOLS,
                          "temperature": float(config.get("temperature", 0.4))},
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            except httpx.HTTPError as e:
                raise NodeError(f"LLM 接続失敗: {e}")
            if r.status_code >= 400:
                raise NodeError(f"LLM エラー {r.status_code}（モデルが tool calling 非対応の可能性）: {r.text[:150]}")
            msg = r.json()["choices"][0]["message"]
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                return {"content": msg.get("content", ""), "model": model,
                        "tool_log": tool_log, "rounds": round_no}
            messages.append(msg)
            for tc in tool_calls[:4]:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = await _agent_call_tool(name, args, ctx)
                tool_log.append({"round": round_no, "tool": name, "args": args, "result": result[:500]})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": result})
    return {"content": "(ツール使用の上限に達しました)", "model": model, "tool_log": tool_log, "rounds": max_rounds}


# ---- サブワークフロー呼び出し ----


async def node_flow_call(config: dict, ctx: dict) -> dict:
    """別のワークフローを実行し、完了を待って signal.display の出力を返す。"""
    from app.database import SessionLocal
    from app.models import WorkflowExecution
    from app.workflows import engine

    wf_id = config.get("workflow_id")
    if not isinstance(wf_id, (int, float)) or int(wf_id) <= 0:
        raise NodeError("呼び出すワークフローを選択してください")
    depth = int(ctx.get("__depth__", 0))
    message = render_template(str(config.get("message", "")), ctx)
    extra: dict = {}
    raw_input = render_template(str(config.get("input_json", "")), ctx).strip()
    if raw_input:
        try:
            parsed = json.loads(raw_input)
            if isinstance(parsed, dict):
                extra = parsed
        except json.JSONDecodeError as e:
            raise NodeError(f"入力 JSON が不正です: {e}")
    try:
        exec_id = await engine.run_workflow(
            int(wf_id), trigger_type="subflow",
            input_data={"message": message, **extra}, depth=depth + 1)
    except engine.DefinitionError as e:
        raise NodeError(str(e))

    wait_limit = max(10, min(int(config.get("timeout", 600) or 600), 3600))
    import asyncio as _asyncio

    deadline = _asyncio.get_event_loop().time() + wait_limit
    while _asyncio.get_event_loop().time() < deadline:
        await _asyncio.sleep(1.0)

        def fetch() -> tuple[str, str, str]:
            db = SessionLocal()
            try:
                row = db.get(WorkflowExecution, exec_id)
                return (row.status, row.error or "", row.context_json or "{}") if row else ("FAILED", "実行消失", "{}")
            finally:
                db.close()

        status, error, ctx_json = await _asyncio.to_thread(fetch)
        if status not in ("RUNNING", "WAITING"):
            sub_ctx = json.loads(ctx_json)
            displays = [str(e.get("output", {}).get("value", ""))
                        for e in sub_ctx.values()
                        if isinstance(e, dict) and isinstance(e.get("output"), dict) and e["output"].get("display")]
            if status != "SUCCEEDED":
                raise NodeError(f"サブフローが {status}: {error}"[:300])
            return {"execution_id": exec_id, "status": status,
                    "result": "\n\n".join(d for d in displays if d), "count": len(displays)}
    engine.cancel_execution(exec_id)
    raise NodeError(f"サブフローが {wait_limit} 秒以内に完了しませんでした")


# ---- ユーティリティ ----


async def node_now(config: dict, ctx: dict) -> dict:
    """現在日時。format（strftime）で整形した文字列と各要素を返す。"""
    from datetime import datetime

    fmt = str(config.get("format", "") or "%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    try:
        text = now.strftime(fmt)
    except ValueError as e:
        raise NodeError(f"日時フォーマットが不正です: {e}")
    return {
        "text": text,
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timestamp": int(now.timestamp()),
        "weekday": now.strftime("%a"),
    }


async def node_http_download(config: dict, ctx: dict) -> dict:
    """URL の内容をファイルへ保存する（許可ルート配下のみ、上限 500MB）。"""
    from app.files.service import FileAccessError, resolve

    url = render_template(str(config.get("url", "")), ctx)
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    raw_path = render_template(str(config.get("path", "")), ctx)
    try:
        dest = resolve(raw_path, must_exist=False)
    except FileAccessError as e:
        raise NodeError(f"保存先が不正です: {e}")
    limit = 500 * 1024 * 1024
    timeout = min(float(config.get("timeout", 300)), 1800)
    written = 0
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise NodeError(f"ダウンロード失敗: HTTP {r.status_code}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with open(dest, "wb") as f:
                    async for chunk in r.aiter_bytes(65536):
                        written += len(chunk)
                        if written > limit:
                            raise NodeError("ファイルサイズが上限（500MB）を超えました")
                        f.write(chunk)
    except httpx.HTTPError as e:
        raise NodeError(f"ダウンロード失敗: {e}")
    return {"path": str(dest), "bytes": written, "url": url}


# ---- Web スクレイピング ----


def _extract_one(soup, selector: str, attr: str, multiple: bool) -> Any:
    """1 つの抽出器を適用して値を返す（multiple なら配列、単体なら先頭のみ）。"""
    elements = soup.select(selector) if selector else []

    def value_of(el) -> str:
        if attr == "text" or not attr:
            return el.get_text(" ", strip=True)
        if attr == "html":
            return el.decode_contents()
        return el.get(attr, "")

    values = [value_of(el) for el in elements]
    if multiple:
        return values
    return values[0] if values else ""


async def node_scrape(config: dict, ctx: dict) -> dict:
    from bs4 import BeautifulSoup

    url = render_template(str(config.get("url", "")), ctx)
    if not url.startswith(("http://", "https://")):
        raise NodeError(f"不正な URL: {url}")
    timeout = min(float(config.get("timeout", 30)), 120)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ControlDeck/1.0"}) as client:
            r = await client.get(url)
    except httpx.HTTPError as e:
        raise NodeError(f"取得失敗: {e}")
    soup = BeautifulSoup(r.text, "html.parser")

    # 複数抽出器（extractors）優先。無ければ単一 selector（後方互換）
    extractors = config.get("extractors")
    if isinstance(extractors, list) and extractors:
        out: dict[str, Any] = {"status_code": r.status_code}
        for ex in extractors:
            if not isinstance(ex, dict):
                continue
            name = str(ex.get("name") or "").strip()
            selector = str(ex.get("selector") or "").strip()
            if not name or not selector:
                continue
            out[name] = _extract_one(soup, selector, str(ex.get("attribute") or "text"), bool(ex.get("multiple")))
        return out

    selector = str(config.get("selector", "")).strip()
    attr = str(config.get("attribute", "")).strip()
    if not selector:
        return {"text": soup.get_text(" ", strip=True)[:20000], "status_code": r.status_code}
    elements = soup.select(selector)
    results = [el.get(attr, "") if attr else el.get_text(" ", strip=True) for el in elements]
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
    """RAG 取り込み。チャンク戦略はコレクション設定に従う（ナレッジ画面で設定）。
    存在しないコレクションは設定を引き継いで自動作成する。"""
    from app.workflows import rag

    collection = str(config.get("collection", "default"))
    text = render_template(str(config.get("text", "")), ctx)
    if not text.strip() and config.get("path"):
        from app.files.service import FileAccessError, read_text

        try:
            text = read_text(render_template(str(config["path"]), ctx))
        except (FileAccessError, FileNotFoundError, OSError) as e:
            raise NodeError(str(e))
    # ノード側でチャンク戦略を上書き指定できる（コレクション設定へ反映）
    override: dict = {}
    for k in ("strategy", "size", "overlap", "parent_mode", "parent_size", "search_mode"):
        if config.get(k) not in (None, ""):
            override[k] = config[k]
    override["embed_base_url"] = str(config.get("base_url", "http://127.0.0.1:11434/v1"))
    override["embed_model"] = str(config.get("embed_model", "nomic-embed-text"))
    try:
        if not rag.collection_exists(collection):
            rag.create_collection(collection, override)
        return await rag.add_document(
            collection=collection,
            text=text,
            source=render_template(str(config.get("source", "workflow")), ctx),
            api_key=str(config.get("api_key", "")),
            config_override=override,
            reset=bool(config.get("reset")),
        )
    except rag.RagError as e:
        raise NodeError(f"RAG 構築失敗: {e}")


async def node_rag_query(config: dict, ctx: dict) -> dict:
    """RAG 検索。検索方式（vector/fulltext/hybrid）を選択できる（空はコレクション設定）。"""
    from app.workflows import rag

    question = render_template(str(config.get("question", "")), ctx)
    if not question.strip():
        raise NodeError("質問が空です")
    mode = str(config.get("search_mode", "") or "") or None
    hyde = bool(config.get("hyde"))
    try:
        mq = int(config.get("multi_query", 0) or 0)
    except (TypeError, ValueError):
        mq = 0
    try:
        if hyde or mq:
            return await rag.search_enhanced(
                collection=str(config.get("collection", "default")),
                question=question, top_k=int(config.get("top_k", 4)),
                api_key=str(config.get("api_key", "")), mode_override=mode,
                hyde=hyde, multi_query=mq,
                llm_base_url=str(config.get("llm_base_url", "http://127.0.0.1:11434/v1")),
                llm_model=str(config.get("llm_model", "llama3.2")),
            )
        return await rag.search(
            collection=str(config.get("collection", "default")),
            question=question,
            top_k=int(config.get("top_k", 4)),
            api_key=str(config.get("api_key", "")),
            mode_override=mode,
        )
    except rag.RagError as e:
        raise NodeError(f"RAG 検索失敗: {e}")


# ---- Web 検索（SearXNG / DuckDuckGo）: Deep Search フローの部品 ----


async def node_web_search(config: dict, ctx: dict) -> dict:
    """Web 検索結果（タイトル/URL/スニペット）を返す。ワークフローで組み合わせやすい部品。

    engine: searxng（自前/公開インスタンス, JSON API）/ duckduckgo（キー不要 HTML）
    結果の url は web.scrape / http.download と繋いで本文取得できる。
    """
    query = render_template(str(config.get("query", "")), ctx).strip()
    if not query:
        raise NodeError("検索クエリが空です")
    engine = str(config.get("engine", "duckduckgo"))
    limit = max(1, min(int(config.get("max_results", 8) or 8), 30))
    results: list[dict] = []

    if engine == "searxng":
        from app.workflows import searxng

        # 未指定なら登録済みローカルインスタンスを自動検出。停止していれば自動起動を試みる
        base = await searxng.resolve_url(render_template(str(config.get("searxng_url", "")), ctx))
        await searxng.ensure_running(base)
        params = {"q": query, "format": "json"}
        cat = str(config.get("categories", "") or "").strip()
        if cat:
            params["categories"] = cat
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "ControlDeck/1.0"}) as client:
                r = await client.get(base + "/search", params=params)
        except httpx.HTTPError as e:
            raise NodeError(f"SearXNG 取得失敗: {e}")
        if r.status_code >= 400:
            raise NodeError(f"SearXNG エラー {r.status_code}（JSON 出力が有効か確認してください）")
        for it in r.json().get("results", [])[:limit]:
            results.append({"title": it.get("title", ""), "url": it.get("url", ""), "snippet": (it.get("content", "") or "")[:500]})
    else:  # duckduckgo（キー不要）
        from bs4 import BeautifulSoup

        try:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 ControlDeck"}) as client:
                r = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
        except httpx.HTTPError as e:
            raise NodeError(f"検索取得失敗: {e}")
        from urllib.parse import parse_qs, unquote, urlparse

        def unwrap(href: str) -> str:
            # DuckDuckGo のリダイレクト(/l/?uddg=...)を実 URL へ復元
            if "uddg=" in href:
                qs = parse_qs(urlparse(href).query)
                if qs.get("uddg"):
                    return unquote(qs["uddg"][0])
            return href if href.startswith("http") else ("https:" + href if href.startswith("//") else href)

        soup = BeautifulSoup(r.text, "html.parser")
        for res in soup.select(".result")[:limit]:
            a = res.select_one("a.result__a")
            if not a:
                continue
            snip = res.select_one(".result__snippet")
            results.append({"title": a.get_text(" ", strip=True), "url": unwrap(a.get("href", "")),
                            "snippet": snip.get_text(" ", strip=True)[:500] if snip else ""})

    urls = [x["url"] for x in results if x["url"]]
    combined = "\n\n".join(f"# {x['title']}\n{x['snippet']}\n{x['url']}" for x in results)
    return {"results": results, "urls": urls, "count": len(results), "text": combined,
            "first_url": urls[0] if urls else ""}


# ---- Deep Research（反復探索エージェント。手軽な一括実行用） ----


async def node_deep_research(config: dict, ctx: dict) -> dict:
    """質問を分解し、選択ソースを反復的に探索して引用付きで統合する（Deep サーチ）。

    ソース: rag(ナレッジ) / web(スクレイピング) / arxiv / crossref / patent / market。
    LLM で サブ質問生成 → 各ソース検索 → 収集 → 統合レポート生成。
    """
    from app.workflows import external_search as ext
    from app.workflows import rag as ragmod

    topic = render_template(str(config.get("topic", "")), ctx).strip()
    if not topic:
        raise NodeError("調査テーマが空です")
    llm_base = str(config.get("llm_base_url", "http://127.0.0.1:11434/v1"))
    llm_model = str(config.get("llm_model", "llama3.2"))
    api_key = str(config.get("api_key", ""))
    sources = str(config.get("sources", "rag,arxiv") or "rag").split(",")
    sources = [s.strip() for s in sources if s.strip()]
    max_sub = max(1, min(int(config.get("sub_questions", 4) or 4), 8))
    per_source = max(1, min(int(config.get("results_per_source", 4) or 4), 10))
    collection = str(config.get("collection", "default"))

    # 1. サブ質問生成
    try:
        raw = await ragmod._llm_complete(
            f"調査テーマ「{topic}」を、体系的に調べるための具体的なサブ質問 {max_sub} 個に分解してください。"
            "1行に1問、番号なしで出力。", llm_base, llm_model, api_key)
    except ragmod.RagError as e:
        raise NodeError(str(e))
    sub_qs = [ln.strip("・-•*0123456789. \t") for ln in raw.splitlines() if ln.strip()][:max_sub] or [topic]

    # 2. 各サブ質問 × 各ソースを収集
    findings: list[dict] = []
    for q in sub_qs:
        for src in sources:
            try:
                if src == "rag":
                    if not ragmod.collection_exists(collection):
                        continue
                    res = await ragmod.search(collection, q, per_source, api_key=api_key)
                    for m in res["matches"]:
                        findings.append({"q": q, "source": "rag", "title": collection, "text": m["context"][:800], "url": ""})
                elif src == "web":
                    web = await node_web_search(
                        {"query": q, "max_results": per_source,
                         "engine": config.get("web_engine", "duckduckgo"),
                         "searxng_url": config.get("searxng_url", "")}, ctx)
                    for it in web["results"]:
                        findings.append({"q": q, "source": "web", "title": it["title"], "text": it.get("snippet", ""), "url": it["url"]})
                else:
                    items = await ext.search(src, q, per_source, api_key=api_key)
                    for it in items:
                        findings.append({"q": q, "source": src, "title": it["title"], "text": it.get("snippet", "")[:800], "url": it.get("url", "")})
            except (ext.SearchError, ragmod.RagError, httpx.HTTPError, Exception):
                continue

    if not findings:
        raise NodeError("収集結果がありません（ソース設定やコレクションを確認してください）")

    # 3. 統合レポート生成（引用付き）
    numbered = "\n".join(
        f"[{i+1}] ({f['source']}) {f['title']}: {f['text'][:300]} {f['url']}"
        for i, f in enumerate(findings[:40])
    )
    report = await ragmod._llm_complete(
        f"あなたは調査アナリストです。テーマ「{topic}」について、以下の収集資料のみに基づき、"
        "要点を体系的にまとめた日本語レポートを作成してください。主張には [番号] で出典を明記し、"
        f"最後に参考一覧を付けてください。\n\n収集資料:\n{numbered}",
        llm_base, llm_model, api_key, temperature=0.4)
    return {
        "report": report,
        "sub_questions": sub_qs,
        "findings": findings,
        "count": len(findings),
        "sources_used": sources,
    }


# ---- 外部検索（論文 / 文献 / 特許 / 市場調査 を統合） ----


async def node_academic_search(config: dict, ctx: dict) -> dict:
    """論文・文献・特許・市場情報をソース選択で検索する。RAG 取り込みや要約に渡せる。

    source: arxiv(論文) / crossref(文献) / patent(特許・要APIキー) / market(SEC EDGAR)
    """
    from app.workflows import external_search as ext

    source = str(config.get("source", "arxiv"))
    query = render_template(str(config.get("query", "")), ctx).strip()
    if not query:
        raise NodeError("検索クエリが空です")
    limit = int(config.get("max_results", 10) or 10)
    try:
        if source == "all":
            fed = await ext.federated(query, limit)
            results = fed["results"]
            combined = "\n\n".join(f"# {x['title']} ({x.get('source','')})\n{x.get('snippet','')}\n{x.get('url','')}" for x in results)
            return {"results": results, "count": len(results), "text": combined, "source": "all", "errors": fed["errors"]}
        results = await ext.search(source, query, limit, api_key=str(config.get("api_key", "")))
    except ext.SearchError as e:
        raise NodeError(str(e))
    combined = "\n\n".join(
        f"# {x['title']}\n{x.get('snippet','')}\n{x.get('url','')}" for x in results
    )
    return {"results": results, "count": len(results), "text": combined, "source": source}


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
    "signal.display": node_signal_display,
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
    "data.transform": node_data_transform,
    "text.markdown": node_markdown,
    "file.read": node_file_read,
    "file.write": node_file_write,
    "file.op": node_file_op,
    "file.glob": node_file_glob,
    "llm.chat": node_llm,
    "ai.utility": node_ai_utility,
    "util.now": node_now,
    "http.download": node_http_download,
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
    "academic.search": node_academic_search,
    "web.search": node_web_search,
    "research.deep": node_deep_research,
    "db.query": node_db_query,
    "flow.call": node_flow_call,
}

# ノードごとの既定タイムアウト（秒）
NODE_TIMEOUTS = {
    "util.wait": 3700,
    "http.request": 320,
    "http.download": 1830,
    "llm.chat": 620,
    "ai.utility": 320,
    "web.scrape": 130,
    "media.ocr": 130,
    "cmd.ssh": 620,
    "cmd.git": 620,
    "cmd.cpp_build": 1820,
    "cmd.python": 620,
    "web.browser": 190,
    "rag.build": 620,
    "rag.query": 320,
    "academic.search": 60,
    "web.search": 60,
    "research.deep": 1800,
    "db.query": 320,
    "flow.call": 3660,
}

# Optional integrationはfeature有効時だけexecutorへ登録する。通常起動ではimportもしない。
from app.features.registry import is_enabled as _feature_enabled

if _feature_enabled("opencode"):
    from app.integrations.opencode.node import node_code_agent

    NODE_EXECUTORS["code.agent"] = node_code_agent
    NODE_TIMEOUTS["code.agent"] = 3660

DEFAULT_NODE_TIMEOUT = 120
