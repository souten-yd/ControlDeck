from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Literal

from app.config import data_dir

logger = logging.getLogger("control_deck.features")

FeatureAction = Literal["install", "update", "enable", "disable", "uninstall"]

# アドオン定義。npmはユーザー空間のprefix、pipは専用venvへ導入する（どちらもsudo不要）。
FEATURES: dict[str, dict] = {
    "opencode": {
        "name": "OpenCode",
        "kind": "npm",
        "package": "opencode-ai",
        "executable": "opencode",
        # 有効化でAPI/画面/ノードを登録するため、反映にプラットフォーム再読み込みが必要。
        "route_gated": True,
        "summary": "OpenCode画面とAIチャットのcodeモードで使うコーディングエージェント",
    },
    "omo": {
        "name": "OMo（多エージェント編成）",
        "kind": "npm",
        "package": "oh-my-openagent",
        "executable": "omo",
        # OpenCode のプラグインとして動くため、OpenCode 側の導入が前提。
        "route_gated": False,
        "summary": "OpenCodeで複数エージェントを並列に動かす。並列数はモデル設定に合わせて調整する",
        "requires": "opencode",
    },
    "pyinstaller": {
        "name": "アプリビルド環境（単一バイナリ）",
        "kind": "pip",
        "package": "pyinstaller",
        "executable": "pyinstaller",
        # 使うのはApp Studioの書き出し時だけなので、導入すればそのまま使える。
        "route_gated": False,
        "summary": "App Studioで、配布先にPython不要の単一バイナリを作れるようにする",
    },
}


def _release_catalog() -> dict[str, dict]:
    path = Path(__file__).with_name("trusted-catalog.json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("trusted feature catalog is invalid") from exc
    if not isinstance(value, dict):
        raise RuntimeError("trusted feature catalog root must be an object")
    return {key: item for key, item in value.items() if isinstance(key, str) and isinstance(item, dict)}


FEATURES.update(_release_catalog())
NPM_PACKAGES = {key: value["package"] for key, value in FEATURES.items() if value["kind"] == "npm"}
KNOWN_FEATURES = set(FEATURES)


class FeatureError(RuntimeError):
    pass


def _features_root() -> Path:
    root = (data_dir() / "features").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _state_path() -> Path:
    return _features_root() / "state.json"


def _read_state() -> dict:
    try:
        raw = json.loads(_state_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _write_state(state: dict) -> None:
    path = _state_path()
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _known(feature_id: str) -> str:
    if feature_id not in KNOWN_FEATURES:
        raise FeatureError(f"未知のfeatureです: {feature_id}")
    return feature_id


def _feature_root(feature_id: str) -> Path:
    root = (_features_root() / _known(feature_id)).resolve()
    if not root.is_relative_to(_features_root()):
        raise FeatureError("feature pathがdata directory外です")
    return root


def _managed_executable(feature_id: str) -> Path:
    spec = FEATURES[_known(feature_id)]
    root = _feature_root(feature_id)
    if spec["kind"] == "release-bundle":
        from app.features import release_bundle

        selected = release_bundle.current(feature_id)
        return root / "current" / selected[0].entrypoint if selected else root / "current" / "missing"
    if spec["kind"] == "pip":
        return root / "venv" / "bin" / spec["executable"]
    return root / "node_modules" / ".bin" / spec["executable"]


def executable(feature_id: str) -> Path | None:
    spec = FEATURES[_known(feature_id)]
    if spec["kind"] == "release-bundle":
        managed = _managed_executable(feature_id)
        return managed.resolve() if managed.is_file() and os.access(managed, os.X_OK) else None
    binary_name = spec["executable"]
    managed = _managed_executable(feature_id)
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed.resolve()
    saved = str(_read_state().get(feature_id, {}).get("external_executable") or "")
    if saved:
        candidate = Path(saved).expanduser().resolve()
        if candidate.name == binary_name and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    external = shutil.which(binary_name)
    return Path(external).resolve() if external else None


def status(feature_id: str) -> dict:
    spec = FEATURES[_known(feature_id)]
    state = _read_state().get(feature_id, {})
    binary = executable(feature_id)
    managed = _managed_executable(feature_id).is_file()
    version = ""
    healthy = False
    error = ""
    release_enabled: bool | None = None
    if spec["kind"] == "release-bundle":
        from app.features import release_bundle
        from app.addons import registry as addon_registry

        selected = release_bundle.current(feature_id)
        managed = selected is not None
        binary = _managed_executable(feature_id) if selected else None
        installed = selected is not None
        version = selected[0].version if selected else ""
        healthy, error = release_bundle.health(feature_id, selected[0]) if selected else (False, "")
        if selected:
            try:
                release_enabled = bool(addon_registry.status(str(spec["addon_id"]))["enabled"])
            except addon_registry.AddonRegistryError:
                release_enabled = False
    elif binary is not None:
        try:
            result = subprocess.run(
                [str(binary), "--version"], capture_output=True, text=True, timeout=10, check=False,
            )
            healthy = result.returncode == 0
            lines = (result.stdout or result.stderr).strip().splitlines()
            version = lines[0][:80] if healthy and lines else ""
            if not healthy:
                error = "version確認に失敗しました"
        except (OSError, subprocess.TimeoutExpired):
            error = "実行ファイルを起動できません"
    installed = binary is not None if spec["kind"] != "release-bundle" else installed
    toolchain = (
        True if spec["kind"] == "release-bundle"
        else shutil.which("npm") if spec["kind"] == "npm" else shutil.which("python3")
    )
    # route_gatedでないアドオンは、導入＝利用可能（有効化の一手間と再読み込みを求めない）。
    enabled = (
        release_enabled is True and installed and healthy
        if spec["kind"] == "release-bundle"
        else (installed and healthy) if not spec["route_gated"]
        else bool(state.get("enabled")) and installed and healthy
    )
    return {
        "id": feature_id,
        "name": spec["name"],
        "summary": spec["summary"],
        "kind": spec["kind"],
        "route_gated": spec["route_gated"],
        # 依存アドオン。未導入なら UI で導入順を案内する。
        "requires": spec.get("requires", ""),
        "preview": bool(spec.get("preview", False)),
        "requires_installed": (
            True if not spec.get("requires")
            else _managed_executable(spec["requires"]).is_file()
            or shutil.which(FEATURES[spec["requires"]]["executable"]) is not None
        ),
        "available": toolchain is not None or installed,
        "installed": installed,
        "managed": managed,
        "enabled": enabled,
        "requested_enabled": release_enabled if release_enabled is not None else (
            bool(state.get("enabled")) or not spec["route_gated"]
        ),
        "version": version,
        "health": "healthy" if healthy else ("error" if installed else "not-installed"),
        "error": error,
        "executable": str(binary) if binary else "",
    }


def list_features() -> list[dict]:
    return [status(feature_id) for feature_id in sorted(KNOWN_FEATURES)]


def is_enabled(feature_id: str) -> bool:
    try:
        return bool(status(feature_id)["enabled"])
    except FeatureError:
        return False


def _npm_install(feature_id: str, package: str) -> subprocess.CompletedProcess[str]:
    root = _feature_root(feature_id)
    npm = shutil.which("npm")
    if npm is None:
        raise FeatureError("npmが必要です")
    root.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [npm, "install", "--prefix", str(root), "--no-fund", "--no-audit", package],
        capture_output=True, text=True, timeout=600, check=False,
    )


def _pip_install(feature_id: str, package: str) -> subprocess.CompletedProcess[str]:
    """アドオン専用venvへ導入する。Control Deck本体のvenvは汚さない。"""
    root = _feature_root(feature_id)
    python = shutil.which("python3")
    if python is None:
        raise FeatureError("python3が必要です")
    venv = root / "venv"
    if not (venv / "bin" / "pip").is_file():
        root.mkdir(parents=True, exist_ok=True)
        created = subprocess.run(
            [python, "-m", "venv", str(venv)], capture_output=True, text=True, timeout=300, check=False,
        )
        if created.returncode != 0:
            return created
    return subprocess.run(
        [str(venv / "bin" / "pip"), "install", "--upgrade", "--disable-pip-version-check", package],
        capture_output=True, text=True, timeout=900, check=False,
    )


def _install_package(feature_id: str, *, latest: bool) -> subprocess.CompletedProcess[str]:
    spec = FEATURES[_known(feature_id)]
    package = spec["package"]
    if spec["kind"] == "pip":
        return _pip_install(feature_id, package)
    return _npm_install(feature_id, f"{package}@latest" if latest else package)


def install(feature_id: str) -> dict:
    name = FEATURES[_known(feature_id)]["name"]
    required = FEATURES[feature_id].get("requires")
    if required and not status(required)["installed"]:
        raise FeatureError(f"先に{FEATURES[required]['name']}を導入してください")
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.features import release_bundle

        try:
            release_bundle.install(feature_id, FEATURES[feature_id])
        except release_bundle.ReleaseBundleError as exc:
            raise FeatureError(str(exc)) from exc
        return status(feature_id)
    result = _install_package(feature_id, latest=False)
    if result.returncode != 0 or not _managed_executable(feature_id).is_file():
        raise FeatureError(f"{name}の管理導入に失敗しました")
    _autoconfigure(feature_id)
    return status(feature_id)


def _autoconfigure(feature_id: str) -> None:
    """導入直後に通信できる状態へ整える。

    接続先やAPIキーを手で入れさせない。失敗しても導入自体は成功扱いにする
    （後から設定画面で直せるため、ここで導入を巻き戻す方が不便）。
    """
    try:
        if feature_id == "opencode":
            from app.integrations.opencode.provider import autoconfigure

            autoconfigure()
        elif feature_id == "omo":
            # 背景タスクの同時実行数をモデルのスロット数へ揃える。
            from app.integrations.opencode.provider import sync_omo_concurrency

            sync_omo_concurrency()
    except Exception:  # noqa: BLE001 - 自動設定の失敗で導入を失敗にしない
        logger.exception("%sの自動設定に失敗しました", feature_id)


def update(feature_id: str) -> dict:
    """管理導入のランタイムを最新版へ更新する。外部PATH上の実体には触れない。"""
    name = FEATURES[_known(feature_id)]["name"]
    if not _managed_executable(feature_id).is_file():
        raise FeatureError(f"Control Deckが導入した{name}のみ更新できます")
    previous = status(feature_id)["version"]
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.features import release_bundle

        try:
            result = release_bundle.install(feature_id, FEATURES[feature_id])
        except release_bundle.ReleaseBundleError as exc:
            raise FeatureError(str(exc)) from exc
        return {**status(feature_id), "previous_version": result["previous_version"] or previous}
    result = _install_package(feature_id, latest=True)
    if result.returncode != 0 or not _managed_executable(feature_id).is_file():
        raise FeatureError(f"{name}の更新に失敗しました")
    return {**status(feature_id), "previous_version": previous}


def enable(feature_id: str) -> dict:
    current = status(feature_id)
    if not current["installed"] or current["health"] != "healthy":
        raise FeatureError(f"正常な{FEATURES[feature_id]['name']}を先に導入してください")
    state = _read_state()
    remembered = "" if current["managed"] else current["executable"]
    state[feature_id] = {
        **state.get(feature_id, {}), "enabled": True,
        "external_executable": remembered,
    }
    _write_state(state)
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.addons import registry as addon_registry

        try:
            addon_registry.set_enabled(str(FEATURES[feature_id]["addon_id"]), True)
        except addon_registry.AddonRegistryError as exc:
            raise FeatureError(str(exc)) from exc
    # 外部PATHの実体を有効化した場合は install を通らないので、ここでも整える。
    _autoconfigure(feature_id)
    return status(feature_id)


def disable(feature_id: str) -> dict:
    _known(feature_id)
    state = _read_state()
    state[feature_id] = {**state.get(feature_id, {}), "enabled": False}
    _write_state(state)
    if FEATURES[feature_id]["kind"] == "release-bundle":
        from app.addons import registry as addon_registry

        try:
            addon_registry.set_enabled(str(FEATURES[feature_id]["addon_id"]), False)
        except addon_registry.AddonRegistryError as exc:
            raise FeatureError(str(exc)) from exc
    return status(feature_id)


def uninstall(feature_id: str) -> dict:
    disable(feature_id)
    if FEATURES[_known(feature_id)]["kind"] == "release-bundle":
        from app.features import release_bundle

        try:
            release_bundle.uninstall(feature_id, FEATURES[feature_id])
        except release_bundle.ReleaseBundleError as exc:
            raise FeatureError(str(exc)) from exc
        return status(feature_id)
    root = _feature_root(feature_id)
    if root.exists():
        # 管理prefixだけを削除。PATH上の外部OpenCodeと~/.config/~/.local/shareには触れない。
        if not root.is_relative_to(_features_root()):
            raise FeatureError("削除対象がfeature directory外です")
        shutil.rmtree(root)
    return status(feature_id)


def apply(action: FeatureAction, feature_id: str) -> dict:
    operations = {"install": install, "update": update, "enable": enable,
                  "disable": disable, "uninstall": uninstall}
    return operations[action](feature_id)
