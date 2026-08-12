"""Workflowを単一の実行ファイル（.pyz）へ書き出す。

追加のビルドツールもコンパイラも使わず、標準ライブラリのzipappだけで作る。
中身はControl Deck本体の`nodes.py`をそのまま同梱するため、ノードの挙動は本体と一致する。
配布先の要件はpython3のみ。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sysconfig
import tempfile
import zipapp
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.application_builder.flow_app import portable
from app.workflows.contracts import build_input_schema, build_output_schema

BUNDLE_DIR = Path(__file__).resolve().parent / "bundle"
# httpx（http系ノードとLLMノードが使う）とその純Python依存。すべてzipapp内で動く。
VENDORED = ("httpx", "httpcore", "h11", "anyio", "sniffio", "idna", "certifi", "typing_extensions", "h2", "hpack", "hyperframe")
COPIED_MODULES = (
    ("app/workflows/nodes.py", "app/workflows/nodes.py"),
    ("app/workflows/redaction.py", "app/workflows/redaction.py"),
    ("app/workflows/contracts.py", "app/workflows/contracts.py"),
)
FEATURE_STUB = '''"""配布アプリではオプトインfeatureを持たない。"""


def is_enabled(feature_id: str) -> bool:
    return False
'''
FILES_STUB = '''"""配布アプリ向けのfile操作。Control Deckのallowed rootsではなく、
実行したユーザーのカレントディレクトリを基準に素直に解決する。"""
from __future__ import annotations

import shutil
from pathlib import Path

MAX_TEXT_BYTES = 8 * 1024 * 1024


def allowed_roots() -> list[Path]:
    return [Path.cwd().resolve()]


def resolve(path: str, *, must_exist: bool = True) -> Path:
    if not path:
        raise ValueError("pathを指定してください")
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path.cwd() / resolved
    resolved = resolved.resolve()
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"見つかりません: {path}")
    return resolved


def read_text(path: str) -> str:
    return resolve(path).read_text(encoding="utf-8", errors="replace")[:MAX_TEXT_BYTES]


def write_text(path: str, content: str) -> None:
    target = resolve(path, must_exist=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def make_directory(path: str) -> None:
    resolve(path, must_exist=False).mkdir(parents=True, exist_ok=True)


def copy(src_path: str, dst_dir: str) -> str:
    source = resolve(src_path)
    destination = resolve(dst_dir, must_exist=False)
    destination.mkdir(parents=True, exist_ok=True)
    return str(shutil.copy2(source, destination / source.name))


def move(src_path: str, dst_dir: str) -> str:
    source = resolve(src_path)
    destination = resolve(dst_dir, must_exist=False)
    destination.mkdir(parents=True, exist_ok=True)
    return str(shutil.move(str(source), str(destination / source.name)))


def delete(path: str) -> None:
    target = resolve(path)
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
'''


class FlowAppError(RuntimeError):
    pass


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip()).strip("-.")
    return (slug or "flow-app")[:60].lower()


def _schema_fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    fields: list[dict[str, Any]] = []
    for name, raw in properties.items():
        field = raw if isinstance(raw, dict) else {}
        field_type = str(field.get("type") or "string")
        item: dict[str, Any] = {
            "name": str(name),
            "label": str(field.get("title") or name),
            "type": field_type,
            "required": name in required,
            "control": "textarea" if field_type in {"object", "array"} else "text",
        }
        if isinstance(field.get("enum"), list):
            item["enum"] = [str(value) for value in field["enum"]][:50]
        if field.get("default") is not None:
            item["default"] = field["default"]
        if field.get("description"):
            item["description"] = str(field["description"])[:300]
        fields.append(item)
    return fields


def _site_packages() -> Path:
    path = Path(sysconfig.get_paths()["purelib"])
    if not path.is_dir():
        raise FlowAppError("Python環境のsite-packagesを特定できません")
    return path


def _vendor(site_packages: Path, staging: Path) -> list[str]:
    copied: list[str] = []
    for name in VENDORED:
        package = site_packages / name
        module = site_packages / f"{name}.py"
        if package.is_dir():
            shutil.copytree(
                package, staging / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyi", "tests", "test"),
            )
            copied.append(name)
        elif module.is_file():
            shutil.copy2(module, staging / module.name)
            copied.append(name)
    if "httpx" not in copied:
        raise FlowAppError("httpxを同梱できません（Control Deckの実行環境を確認してください）")
    return copied


def _repo_root() -> Path:
    # backend/app/application_builder/flow_app/packager.py -> backend
    return Path(__file__).resolve().parents[3]


def _flow_metadata(name: str, description: str, definition: dict[str, Any], workflow_id: int,
                   workflow_version_id: int | None) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "name": name,
        "slug": slugify(name),
        "description": description,
        "workflowId": workflow_id,
        "workflowVersionId": workflow_version_id,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "generator": "control-deck-flow-app/1",
        "inputs": _schema_fields(build_input_schema(definition)),
        "outputs": _schema_fields(build_output_schema(definition)),
        "definition": {"nodes": definition.get("nodes") or [], "edges": definition.get("edges") or []},
    }


def _stage(flow: dict[str, Any], staging: Path) -> list[str]:
    """実行に必要なファイル一式（本体のnodes.py・runtime・vendor）を並べる。"""
    repo_root = _repo_root()
    staging.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BUNDLE_DIR / "__main__.py", staging / "__main__.py")
    shutil.copytree(
        BUNDLE_DIR / "flowapp", staging / "flowapp",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (staging / "flowapp" / "flow.json").write_text(
        json.dumps(flow, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8",
    )
    for source, target in COPIED_MODULES:
        destination = staging / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / source, destination)
    for package in ("app", "app/workflows", "app/features", "app/files"):
        directory = staging / package
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    (staging / "app" / "features" / "registry.py").write_text(FEATURE_STUB, encoding="utf-8")
    (staging / "app" / "files" / "service.py").write_text(FILES_STUB, encoding="utf-8")
    return _vendor(_site_packages(), staging)


def _result(output_path: Path, flow: dict[str, Any], analysis: dict[str, Any], *,
            fmt: str, vendored: list[str], requires: str) -> dict[str, Any]:
    output_path.chmod(output_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {
        "path": str(output_path),
        "filename": output_path.name,
        "format": fmt,
        "size": output_path.stat().st_size,
        "checksum": hashlib.sha256(output_path.read_bytes()).hexdigest(),
        "generatedAt": flow["generatedAt"],
        "inputs": flow["inputs"],
        "outputs": flow["outputs"],
        "nodeCount": len(flow["definition"]["nodes"]),
        "vendored": vendored,
        "diagnostics": analysis["diagnostics"],
        "requires": requires,
        "runHint": f"./{output_path.name}          # GUI起動\n./{output_path.name} --input '{{}}'  # CLI実行",
    }


def build_binary(
    *, name: str, description: str, definition: dict[str, Any], workflow_id: int,
    output_path: Path, workflow_version_id: int | None = None,
    progress=None,
) -> dict[str, Any]:
    """PyInstallerで単一バイナリを作る。配布先にはPythonも不要になる。

    ビルド環境（PyInstaller）は設定→アドオンで導入する。ここでは導入済みの
    アドオン専用venvのpyinstallerだけを使い、本体のvenvには触れない。
    """
    from app.features import registry

    analysis = portable.analyze(definition)
    if not analysis["portable"]:
        raise FlowAppError(analysis["diagnostics"][0]["message"])
    builder = registry.executable("pyinstaller")
    if builder is None:
        raise FlowAppError("単一バイナリの書き出しには、設定→アドオンで「アプリビルド環境」を導入してください")

    flow = _flow_metadata(name, description, definition, workflow_id, workflow_version_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flow-app-bin-") as workspace:
        root = Path(workspace)
        staging = root / "src"
        vendored = _stage(flow, staging)
        if progress:
            progress("依存を収集しました。バイナリを作成中", 1, 3)
        argv = [
            str(builder), "--onefile", "--noconfirm", "--clean", "--log-level=WARN",
            f"--name={output_path.name}",
            f"--distpath={root / 'dist'}", f"--workpath={root / 'build'}", f"--specpath={root}",
            f"--paths={staging}",
            f"--add-data={staging / 'flowapp' / 'ui.html'}:flowapp",
            f"--add-data={staging / 'flowapp' / 'flow.json'}:flowapp",
            str(staging / "__main__.py"),
        ]
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=1800, check=False, cwd=str(root),
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(root), "TMPDIR": str(root)},
        )
        produced = root / "dist" / output_path.name
        if result.returncode != 0 or not produced.is_file():
            detail = (result.stderr or result.stdout or "")[-400:].strip()
            raise FlowAppError(f"単一バイナリの作成に失敗しました: {detail}")
        if progress:
            progress("バイナリを保存中", 2, 3)
        shutil.move(str(produced), str(output_path))
    return _result(output_path, flow, analysis, fmt="binary", vendored=vendored, requires="Linux x86-64（追加インストール不要）")


def build_flow_app(
    *, name: str, description: str, definition: dict[str, Any], workflow_id: int,
    output_path: Path, workflow_version_id: int | None = None,
) -> dict[str, Any]:
    """.pyzを生成し、metadataを返す。生成前に携帯可否を検査する。"""
    analysis = portable.analyze(definition)
    if not analysis["portable"]:
        raise FlowAppError(analysis["diagnostics"][0]["message"])
    flow = _flow_metadata(name, description, definition, workflow_id, workflow_version_id)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flow-app-") as workspace:
        staging = Path(workspace) / "app"
        vendored = _stage(flow, staging)
        zipapp.create_archive(
            staging, target=output_path, interpreter="/usr/bin/env python3", compressed=True,
        )
    return _result(output_path, flow, analysis, fmt="pyz", vendored=vendored, requires="python3.11+")


def clean_environment() -> dict[str, Any]:
    """書き出し可否の事前確認（副作用なし）。"""
    try:
        site_packages = _site_packages()
        has_httpx = (site_packages / "httpx").is_dir()
    except FlowAppError:
        has_httpx = False
    from app.features import registry

    binary_ready = registry.executable("pyinstaller") is not None
    return {
        "available": has_httpx,
        "formats": [
            {
                "id": "pyz", "label": "軽量（.pyz）", "available": has_httpx,
                "requires": "配布先にpython3が必要", "size": "1MB未満", "buildTime": "1〜2秒",
                "note": "標準ライブラリのzipappだけで作ります。Control Deck側にも追加SDKは不要です。",
            },
            {
                "id": "binary", "label": "単一バイナリ", "available": binary_ready,
                "requires": "配布先は追加インストール不要（Linux x86-64）", "size": "約10MB", "buildTime": "5〜20秒",
                "note": "この端末に「アプリビルド環境」アドオン（PyInstaller）が必要です。設定→アドオンから導入できます。",
            },
        ],
        "supportedNodes": sorted(portable.PORTABLE_NODES),
    }
