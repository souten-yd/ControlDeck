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
