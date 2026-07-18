from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from app.config import data_dir
from app.features import registry
from app.files import service as files
from app.jobs.service import Job

OPERATIONS = {"analyze", "implement", "fix", "test", "review"}
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_SETTINGS = {
    "base_url": "http://127.0.0.1:11434/v1",
    "model": "llama3.2",
    "project_path": "",
}


class CodeAgentError(RuntimeError):
    pass


def _integration_dir() -> Path:
    root = (data_dir() / "integrations" / "opencode").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _settings_path() -> Path:
    return _integration_dir() / "settings.json"


def get_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(_settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            settings.update({key: raw[key] for key in settings if key in raw})
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass
    return settings


def save_settings(patch: dict) -> dict:
    settings = get_settings()
    settings.update({key: patch[key] for key in settings if key in patch})
    settings["base_url"] = str(settings["base_url"]).strip().rstrip("/")
    settings["model"] = str(settings["model"]).strip()
    if not settings["base_url"].startswith(("http://", "https://")):
        raise ValueError("LLM endpointはhttp(s) URLで指定してください")
    if not settings["model"] or len(settings["model"]) > 200:
        raise ValueError("modelを指定してください")
    project = str(settings.get("project_path") or "")
    if project:
        resolved = files.resolve(project)
        if not resolved.is_dir():
            raise ValueError("project pathはディレクトリを指定してください")
        settings["project_path"] = str(resolved)
    path = _settings_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
    return settings


def _runtime_config(job_id: str, base_url: str, model: str) -> Path:
    safe_job_id = re.sub(r"[^a-zA-Z0-9_-]", "", job_id)[:24]
    path = _integration_dir() / f"runtime-config-{safe_job_id}.json"
    payload = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"controldeck/{model}",
        "provider": {
            "controldeck": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Control Deck LLM",
                "options": {"baseURL": base_url.rstrip("/"), "apiKey": "sk-no-key"},
                "models": {model: {"name": model}},
            }
        },
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, path)
    return path


def codedev_root() -> Path:
    """OpenCodeプロジェクトの既定ルート（~/CodeDEV）。

    ControlDeckのデータ領域やリポジトリ内ではなくホーム直下に置くことで、
    ファイルマネージャ・ターミナル・Git から普通のプロジェクトとして扱える。
    """
    root = Path.home() / "CodeDEV"
    root.mkdir(exist_ok=True)
    return root


def list_projects() -> list[dict]:
    """CodeDEV配下のプロジェクト一覧（更新の新しい順）。"""
    projects = []
    for path in codedev_root().iterdir():
        if not path.is_dir() or path.name.startswith("."):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        projects.append({
            "name": path.name, "path": str(path),
            "git": (path / ".git").is_dir(), "modified_at": mtime,
        })
    return sorted(projects, key=lambda item: -float(item["modified_at"]))


def ensure_project(name: str) -> dict:
    """プロジェクト名からCodeDEV配下のフォルダを取得（無ければ作成 + git init）。"""
    import subprocess

    cleaned = name.strip()
    if (not cleaned or len(cleaned) > 64 or cleaned in (".", "..")
            or cleaned.startswith(".") or any(c in cleaned for c in "/\\\0")):
        raise CodeAgentError("プロジェクト名は64文字以内で、/ や先頭の . は使えません")
    path = codedev_root() / cleaned
    created = not path.exists()
    path.mkdir(exist_ok=True)
    if created and shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=path, capture_output=True, timeout=15)
    return {"name": cleaned, "path": str(path), "created": created,
            "git": (path / ".git").is_dir()}


def import_project(source_path: str) -> dict:
    """CodeDEV外のフォルダをCodeDEVへコピーして取り込む（管理下は素通し）。

    依存物などの重量ディレクトリ（node_modules/.venv等）は再生成可能なため
    コピー対象から除外する。名前衝突時は -2, -3 と連番を付ける。
    """
    import shutil as _shutil

    source = files.resolve(source_path)
    if not source.is_dir():
        raise CodeAgentError("フォルダを指定してください")
    root = codedev_root()
    if source == root:
        raise CodeAgentError("CodeDEVルート自体は開けません。プロジェクトを選択してください")
    if source.is_relative_to(root):
        return {"name": source.name, "path": str(source), "imported": False}
    base = source.name or "project"
    destination = root / base
    counter = 2
    while destination.exists():
        destination = root / f"{base}-{counter}"
        counter += 1
    _shutil.copytree(
        source, destination,
        ignore=_shutil.ignore_patterns("node_modules", ".venv", "venv", "__pycache__",
                                       ".mypy_cache", ".pytest_cache"),
        symlinks=True,
    )
    return {"name": destination.name, "path": str(destination), "imported": True}


def tui_command(*, project_path: str, prompt: str = "", base_url: str = "", model: str = "") -> tuple[str, str]:
    """対話TUIセッション用のshellコマンドを組み立てる。(command, project_dir)を返す。

    ターミナル基盤（tmux）の上でopencode TUIをそのまま動かす。設定は永続config
    （Control Deck LLM provider）を都度再生成して渡す。
    """
    import shlex

    if not registry.is_enabled("opencode"):
        raise CodeAgentError("OpenCode featureが有効ではありません")
    binary = registry.executable("opencode")
    if binary is None:
        raise CodeAgentError("OpenCodeを利用できません")
    settings = get_settings()
    endpoint = (base_url or settings["base_url"]).strip().rstrip("/")
    model_id = (model or settings["model"]).strip()
    if not endpoint.startswith(("http://", "https://")) or not model_id:
        raise CodeAgentError("LLM endpointとmodelを設定してください")
    raw_project = project_path or settings.get("project_path") or str(Path.home())
    try:
        project = files.resolve(raw_project)
    except (files.FileAccessError, FileNotFoundError) as exc:
        raise CodeAgentError(str(exc)) from exc
    if not project.is_dir():
        raise CodeAgentError("project pathはディレクトリを指定してください")
    config = _runtime_config("tui", endpoint, model_id)
    argv = [str(binary), "--model", f"controldeck/{model_id}"]
    if prompt.strip():
        argv += ["--prompt", prompt.strip()]
    argv.append(str(project))
    command = f"OPENCODE_CONFIG={shlex.quote(str(config))} exec " + " ".join(shlex.quote(a) for a in argv)
    return command, str(project)


def _prompt(operation: str, instruction: str) -> str:
    labels = {
        "analyze": "コードを読み取り、問題点と改善案を分析してください。ファイルは変更しないでください。",
        "implement": "要求を実装し、必要なテストも更新してください。",
        "fix": "不具合を再現・原因特定して修正し、回帰テストを追加してください。",
        "test": "対象をテストし、失敗があれば原因と再現手順を報告してください。",
        "review": "変更をレビューし、重大度順に具体的な指摘を報告してください。ファイルは変更しないでください。",
    }
    return f"{labels[operation]}\n\nユーザー要求:\n{instruction.strip()}"


def _extract_text(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if value.get("type") == "text" and isinstance(value.get("text"), str):
            found.append(value["text"])
        elif isinstance(value.get("content"), str) and value.get("type") in ("message", "result"):
            found.append(value["content"])
        for child in value.values():
            found.extend(_extract_text(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_extract_text(child))
    return found


def _find_session_id(value: Any) -> str:
    """opencode JSONイベントからセッションIDを再帰探索する（継続対話用）。"""
    if isinstance(value, dict):
        for key in ("sessionID", "session_id", "sessionId"):
            found = value.get(key)
            if isinstance(found, str) and found:
                return found
        for child in value.values():
            found = _find_session_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_session_id(child)
            if found:
                return found
    return ""


async def run_chat(
    job: Job, *, instruction: str, project_name: str = "", project_path: str = "",
    session_id: str = "", on_text=None,
) -> dict:
    """AIチャット用のheadless実行。JSONイベントを逐次読み、本文テキストを

    on_text コールバックへストリームする（Codex/Claude風のチャット内コーディング）。
    session_id 指定で前回のopencodeセッションを継続する。
    """
    if not registry.is_enabled("opencode"):
        raise CodeAgentError("OpenCode featureが有効ではありません")
    if not instruction.strip():
        raise CodeAgentError("指示が空です")
    binary = registry.executable("opencode")
    systemd_run = shutil.which("systemd-run")
    systemctl = shutil.which("systemctl")
    if binary is None or systemd_run is None or systemctl is None:
        raise CodeAgentError("OpenCodeまたはsystemd user managerを利用できません")
    settings = get_settings()
    if project_name.strip():
        project = Path(ensure_project(project_name)["path"])
    elif (project_path or "").strip():
        # CodeDEV外のフォルダはコピーして取り込んでから開く
        imported = await asyncio.to_thread(import_project, project_path)
        project = Path(imported["path"])
    else:
        raw = settings.get("project_path") or ""
        if not raw:
            raise CodeAgentError("プロジェクトを指定してください")
        project = files.resolve(raw)
    if not project.is_dir():
        raise CodeAgentError("project pathはディレクトリを指定してください")
    endpoint = str(settings["base_url"]).rstrip("/")
    model_id = str(settings["model"]).strip()
    runtime_config = _runtime_config(f"chat-{job.id}", endpoint, model_id)
    # LLM endpoint（llama.cpp instance）はondemand hookを通らないため先に起動保証する
    from app.models_mgmt import llama

    await llama.ensure_ready_by_base_url(endpoint)
    unit = f"cdfeature-opencode-{re.sub(r'[^a-zA-Z0-9_-]', '', job.id)[:24]}"
    argv = [
        systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
        f"--unit={unit}", f"--working-directory={project}",
        f"--setenv=OPENCODE_CONFIG={runtime_config}",
        str(binary), "run", instruction[:32_000],
        "--format", "json", "--auto",
        "--model", f"controldeck/{model_id}", "--dir", str(project),
    ]
    if session_id:
        argv += ["--session", session_id]
    job.set_progress("OpenCodeを起動", 0, 1)
    events = 0
    emitted: set[str] = set()
    found_session = ""
    reported_error = ""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )

        async def drain_stderr() -> bytes:
            assert proc is not None and proc.stderr is not None
            return await proc.stderr.read()

        stderr_task = asyncio.create_task(drain_stderr())
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            events += 1
            if not found_session:
                found_session = _find_session_id(event)
            if event.get("type") == "error":
                reported_error = str(event.get("error", {}).get("name") or "provider error")[:100]
            for text in _extract_text(event):
                cleaned = text.strip()
                if not cleaned or cleaned in emitted:
                    continue
                emitted.add(cleaned)
                if on_text is not None:
                    await on_text(cleaned)
            if events % 5 == 0:
                job.set_progress("OpenCode実行中", events, 0)
        await stderr_task
        await proc.wait()
    except asyncio.CancelledError:
        stop = await asyncio.create_subprocess_exec(
            systemctl, "--user", "stop", f"{unit}.service",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await stop.wait()
        raise
    finally:
        runtime_config.unlink(missing_ok=True)
    if reported_error:
        raise CodeAgentError(f"OpenCode provider error: {reported_error}")
    if proc is None or proc.returncode != 0:
        raise CodeAgentError(f"OpenCode実行失敗（終了コード {proc.returncode if proc else 'unknown'}）")
    output = "\n\n".join(emitted)
    job.set_progress("完了", 1, 1)
    return {"output": output[-100_000:], "events": events,
            "session_id": found_session, "project_path": str(project)}


class OpenCodeProvider:
    async def run(
        self, job: Job, *, operation: str, project_path: str, instruction: str,
        base_url: str = "", model: str = "",
    ) -> dict:
        if not registry.is_enabled("opencode"):
            raise CodeAgentError("OpenCode featureが有効ではありません")
        if operation not in OPERATIONS:
            raise CodeAgentError("未対応のoperationです")
        if not instruction.strip() or len(instruction) > 32_000:
            raise CodeAgentError("instructionは1〜32000文字で指定してください")
        try:
            project = files.resolve(project_path)
        except (files.FileAccessError, FileNotFoundError) as exc:
            raise CodeAgentError(str(exc)) from exc
        if not project.is_dir():
            raise CodeAgentError("project pathはディレクトリを指定してください")
        settings = get_settings()
        endpoint = (base_url or settings["base_url"]).strip().rstrip("/")
        model_id = (model or settings["model"]).strip()
        binary = registry.executable("opencode")
        systemd_run = shutil.which("systemd-run")
        systemctl = shutil.which("systemctl")
        if binary is None or systemd_run is None or systemctl is None:
            raise CodeAgentError("OpenCodeまたはsystemd user managerを利用できません")
        runtime_config = _runtime_config(job.id, endpoint, model_id)
        prompt_path = (_integration_dir() / f"prompt-{job.id}.txt").resolve()
        if not prompt_path.is_relative_to(_integration_dir()):
            raise CodeAgentError("prompt pathがintegration directory外です")
        prompt_path.write_text(_prompt(operation, instruction), encoding="utf-8")
        os.chmod(prompt_path, 0o600)
        unit = f"cdfeature-opencode-{re.sub(r'[^a-zA-Z0-9_-]', '', job.id)[:24]}"
        argv = [
            systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
            f"--unit={unit}", f"--working-directory={project}",
            f"--setenv=OPENCODE_CONFIG={runtime_config}",
            str(binary), "run", "添付されたControl Deckの指示を実行してください。",
            "--format", "json", "--auto",
            "--model", f"controldeck/{model_id}", "--dir", str(project),
            "--file", str(prompt_path),
        ]
        job.set_progress("OpenCodeを起動", 0, 1)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except asyncio.CancelledError:
            stop = await asyncio.create_subprocess_exec(
                systemctl, "--user", "stop", f"{unit}.service",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await stop.wait()
            raise
        finally:
            prompt_path.unlink(missing_ok=True)
            runtime_config.unlink(missing_ok=True)
        if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES:
            raise CodeAgentError("OpenCode出力が上限を超えました")
        if proc is None or proc.returncode != 0:
            # stderrはprovider/pluginがpromptやcredentialを含める可能性があるため公開しない。
            raise CodeAgentError(f"OpenCode実行失敗（終了コード {proc.returncode if proc else 'unknown'}）")
        events = []
        text_parts: list[str] = []
        reported_error = ""
        for line in stdout.decode("utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(event)
            if event.get("type") == "error":
                reported_error = str(event.get("error", {}).get("name") or "provider error")[:100]
            text_parts.extend(_extract_text(event))
            if len(events) % 10 == 0:
                job.set_progress("OpenCode実行中", len(events), 0)
        if reported_error:
            raise CodeAgentError(f"OpenCode provider error: {reported_error}")
        output = "\n".join(dict.fromkeys(part.strip() for part in text_parts if part.strip()))
        job.set_progress("完了", 1, 1)
        return {"output": output[-100_000:], "events": len(events), "operation": operation,
                "project_path": str(project), "model": model_id}


provider = OpenCodeProvider()
