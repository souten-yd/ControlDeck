"""OpenCode の作業結果（UI 検証・精密調査・レビュー）を ControlDeck が受け取り、画面へ出す。

AI に自由形式の文章で書かせると、画面は「何が良くて何が悪いか」を読み取れない。
`control_deck.report`（ControlDeck の MCP）で形をそろえて渡させ、ここで検証して保存する。
画面写真は session の出力先（provider.ui_verify_dir）から、報告の時点で取り込む——
同じ利用者の次の session が同じ名前で撮り直しても、前の報告の写真は変わらない。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import data_dir

MAX_IMAGES = 40
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_REPORTS = 200
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_ID = re.compile(r"^[0-9a-f]{32}$")

Severity = Literal["critical", "major", "minor", "info"]


class ReportError(ValueError):
    pass


def _image_name(value: str | None) -> str | None:
    if not value:
        return None
    name = Path(str(value)).name
    if not _NAME.match(name) or Path(name).suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"画面写真のファイル名が不正です: {name}")
    return name


class Check(BaseModel):
    model_config = ConfigDict(extra="ignore")
    viewport: str = Field(default="", max_length=32)
    theme: Literal["light", "dark", ""] = ""
    screenshot: str | None = Field(default=None, max_length=160)
    status: Literal["pass", "fail", "skip"] = "pass"
    note: str = Field(default="", max_length=2000)

    @field_validator("screenshot")
    @classmethod
    def _shot(cls, value: str | None) -> str | None:
        return _image_name(value)


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore")
    severity: Severity = "minor"
    title: str = Field(min_length=1, max_length=300)
    detail: str = Field(default="", max_length=4000)
    location: str = Field(default="", max_length=500)
    viewport: str = Field(default="", max_length=32)
    screenshot: str | None = Field(default=None, max_length=160)

    @field_validator("screenshot")
    @classmethod
    def _shot(cls, value: str | None) -> str | None:
        return _image_name(value)


class Source(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(default="", max_length=500)
    url: str = Field(default="", max_length=2048)
    accessed_at: str = Field(default="", max_length=64)
    published_at: str = Field(default="", max_length=64)
    version: str = Field(default="", max_length=120)
    claim: str = Field(default="", max_length=2000)
    stance: Literal["supports", "contradicts", "context"] = "supports"
    primary: bool = False


class ReportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["ui_verify", "research", "review"]
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=8000)
    verdict: Literal["pass", "fail", "partial"] = "partial"
    target: str = Field(default="", max_length=2048)
    checks: list[Check] = Field(default_factory=list, max_length=60)
    findings: list[Finding] = Field(default_factory=list, max_length=200)
    sources: list[Source] = Field(default_factory=list, max_length=120)
    console_errors: list[str] = Field(default_factory=list, max_length=100)
    failed_requests: list[str] = Field(default_factory=list, max_length=100)
    unverified: list[str] = Field(default_factory=list, max_length=60)

    @field_validator("console_errors", "failed_requests", "unverified")
    @classmethod
    def _short(cls, values: list[str]) -> list[str]:
        return [str(value)[:1000] for value in values]


REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["ui_verify", "research", "review"]},
        "title": {"type": "string"},
        "summary": {"type": "string", "description": "結論を 2〜5 文で"},
        "verdict": {"type": "string", "enum": ["pass", "fail", "partial"]},
        "target": {"type": "string", "description": "検証した URL、調べた問い、レビューした差分（ブランチやコミット）"},
        "checks": {"type": "array", "items": {"type": "object", "properties": {
            "viewport": {"type": "string", "description": "例: 320x800"},
            "theme": {"type": "string", "enum": ["light", "dark"]},
            "screenshot": {"type": "string", "description": "撮った画面写真のファイル名（例: w320-light.png）"},
            "status": {"type": "string", "enum": ["pass", "fail", "skip"]},
            "note": {"type": "string"}}}},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": ["critical", "major", "minor", "info"]},
            "title": {"type": "string"}, "detail": {"type": "string"},
            "location": {"type": "string", "description": "file:line または URL"},
            "viewport": {"type": "string"}, "screenshot": {"type": "string"}},
            "required": ["severity", "title"]}},
        "sources": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "url": {"type": "string"},
            "accessed_at": {"type": "string"}, "published_at": {"type": "string"},
            "version": {"type": "string"}, "claim": {"type": "string", "description": "この出典が支える主張"},
            "stance": {"type": "string", "enum": ["supports", "contradicts", "context"]},
            "primary": {"type": "boolean", "description": "一次資料（公式文書・仕様・ソース）か"}}}},
        "console_errors": {"type": "array", "items": {"type": "string"}},
        "failed_requests": {"type": "array", "items": {"type": "string"}},
        "unverified": {"type": "array", "items": {"type": "string"}, "description": "確かめられなかったこと"},
    },
    "required": ["kind", "title", "verdict"],
}


def _root() -> Path:
    root = (data_dir() / "integrations" / "opencode" / "reports").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _report_dir(report_id: str) -> Path:
    if not _ID.match(report_id):
        raise ReportError("報告の id が不正です")
    return _root() / report_id


def save(body: ReportBody, *, owner_user_id: int, session: str, images_from: Path | None) -> dict:
    """報告を保存する。参照された画面写真を取り込み、見つからない写真は報告に残す。"""
    report_id = uuid.uuid4().hex
    target = _report_dir(report_id)
    images = target / "images"
    images.mkdir(parents=True)
    wanted = {item.screenshot for item in [*body.checks, *body.findings] if item.screenshot}
    missing: list[str] = []
    copied = 0
    source_root = images_from.resolve() if images_from is not None else None
    for name in sorted(wanted):
        if copied >= MAX_IMAGES:
            missing.append(name)
            continue
        found = _find_image(source_root, name) if source_root else None
        if found is None or found.stat().st_size > MAX_IMAGE_BYTES:
            missing.append(name)
            continue
        shutil.copyfile(found, images / name)
        copied += 1
    record = {
        "id": report_id,
        "owner_user_id": owner_user_id,
        "session": session,
        "created_at": time.time(),
        **body.model_dump(),
        "missing_images": missing,
    }
    temp = target / "report.json.tmp"
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, target / "report.json")
    _prune()
    return record


def _find_image(root: Path, name: str) -> Path | None:
    """出力先の中から名前の一致する写真を探す。Playwright MCP は日付の下位フォルダを作ることがある。"""
    direct = root / name
    if direct.is_file():
        return direct
    for candidate in sorted(root.rglob(name), key=lambda p: p.stat().st_mtime, reverse=True):
        resolved = candidate.resolve()
        if resolved.is_file() and resolved.is_relative_to(root):
            return resolved
    return None


def _prune() -> None:
    """古い報告から消す。画面写真を含むので際限なく溜めない。"""
    rows = sorted((p for p in _root().iterdir() if p.is_dir() and _ID.match(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    for old in rows[MAX_REPORTS:]:
        shutil.rmtree(old, ignore_errors=True)


def _load(report_id: str) -> dict | None:
    try:
        return json.loads((_report_dir(report_id) / "report.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def summary(record: dict) -> dict:
    findings = record.get("findings") or []
    counts = {level: sum(1 for item in findings if item.get("severity") == level)
              for level in ("critical", "major", "minor", "info")}
    first = next((item.get("screenshot") for item in record.get("checks") or [] if item.get("screenshot")), None)
    return {key: record.get(key) for key in ("id", "kind", "title", "verdict", "target", "created_at", "session")} | {
        "counts": counts, "sources": len(record.get("sources") or []),
        "checks": len(record.get("checks") or []), "thumbnail": first,
    }


def list_reports(*, owner_user_id: int | None, limit: int = 50) -> list[dict]:
    """新しい順。owner_user_id が None なら全員分（管理者）。"""
    rows = sorted((p for p in _root().iterdir() if p.is_dir() and _ID.match(p.name)),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    result = []
    for path in rows:
        record = _load(path.name)
        if record is None:
            continue
        if owner_user_id is not None and record.get("owner_user_id") != owner_user_id:
            continue
        result.append(summary(record))
        if len(result) >= limit:
            break
    return result


def get(report_id: str, *, owner_user_id: int | None) -> dict | None:
    record = _load(report_id)
    if record is None or (owner_user_id is not None and record.get("owner_user_id") != owner_user_id):
        return None
    return record


def image_path(report_id: str, name: str, *, owner_user_id: int | None) -> Path | None:
    if get(report_id, owner_user_id=owner_user_id) is None:
        return None
    try:
        safe = _image_name(name)
    except ValueError:
        return None
    path = (_report_dir(report_id) / "images" / str(safe)).resolve()
    return path if path.is_file() and path.is_relative_to(_report_dir(report_id).resolve()) else None


def delete(report_id: str, *, owner_user_id: int | None) -> bool:
    if get(report_id, owner_user_id=owner_user_id) is None:
        return False
    shutil.rmtree(_report_dir(report_id), ignore_errors=True)
    return True
