from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.addons import registry as addon_registry
from app.addons.schema import AddonManifestV2, load_manifest_file
from app.applications import systemd
from app.config import data_dir

PACKAGE_MANIFEST = "control-deck-feature.json"
MAX_METADATA_BYTES = 2 * 1024 * 1024
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


class ReleaseBundleError(RuntimeError):
    pass


class PackageManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=1, le=1)
    feature_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(min_length=1, max_length=64)
    platform: str = Field(pattern=r"^linux$")
    architecture: str = Field(pattern=r"^x86_64$")
    entrypoint: str = Field(min_length=1, max_length=256)
    addon_manifest: str = Field(min_length=1, max_length=256)
    smoke_args: list[str] = Field(default_factory=lambda: ["doctor"], max_length=8)
    service_args: list[str] = Field(default_factory=lambda: ["serve"], max_length=8)
    health_url: HttpUrl

    @field_validator("version")
    @classmethod
    def valid_version(cls, value: str) -> str:
        if VERSION_RE.fullmatch(value) is None:
            raise ValueError("version contains unsupported characters")
        return value

    @field_validator("entrypoint", "addon_manifest")
    @classmethod
    def relative_file(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or value.startswith("."):
            raise ValueError("bundle paths must be normalized relative paths")
        return value

    @field_validator("smoke_args", "service_args")
    @classmethod
    def bounded_args(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 128 or "\x00" in item or "\n" in item for item in value):
            raise ValueError("bundle arguments are invalid")
        return value

    @field_validator("health_url")
    @classmethod
    def loopback_health(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "http" or value.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("health_url must use loopback HTTP")
        return value


def host_platform() -> tuple[str, str]:
    machine = platform.machine().lower()
    return ("linux", "x86_64" if machine in {"x86_64", "amd64"} else machine)


def _feature_root(feature_id: str) -> Path:
    base = (data_dir() / "features").resolve()
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = base / feature_id
    if root.is_symlink() or root.resolve() != root or not root.resolve().is_relative_to(base):
        raise ReleaseBundleError("feature root is outside the managed directory")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ReleaseBundleError("feature root must be a private user-owned directory")
    return root


def _managed_directory(root: Path, name: str) -> Path:
    target = root / name
    if target.is_symlink():
        raise ReleaseBundleError(f"managed {name} directory cannot be a symlink")
    target.mkdir(mode=0o700, exist_ok=True)
    info = target.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ReleaseBundleError(f"managed {name} directory has unsafe ownership or permissions")
    return target


def _bounded_get(url: str, *, allowed_hosts: set[str], limit: int) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ReleaseBundleError("catalog URL host is not trusted")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "ControlDeck"})
    with urllib.request.urlopen(request, timeout=30) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname not in allowed_hosts:
            raise ReleaseBundleError("download redirected outside trusted hosts")
        length = response.headers.get("Content-Length")
        if length and int(length) > limit:
            raise ReleaseBundleError("download exceeds catalog size limit")
        content = response.read(limit + 1)
    if len(content) > limit:
        raise ReleaseBundleError("download exceeds catalog size limit")
    return content


def _metadata(spec: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(_bounded_get(
            str(spec["release_metadata_url"]),
            allowed_hosts={"api.github.com"},
            limit=MAX_METADATA_BYTES,
        ))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseBundleError("release metadata could not be loaded") from exc
    if not isinstance(value, dict) or not isinstance(value.get("assets"), list):
        raise ReleaseBundleError("release metadata is invalid")
    return value


def _select_release(spec: dict[str, Any], metadata: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
    version = str(metadata.get("tag_name") or "").removeprefix("v")
    if VERSION_RE.fullmatch(version) is None or metadata.get("draft") is True:
        raise ReleaseBundleError("release version is invalid or draft")
    system, architecture = host_platform()
    name = str(spec["artifact_name"]).format(version=version, platform=system, arch=architecture)
    assets = {str(item.get("name")): item for item in metadata["assets"] if isinstance(item, dict)}
    artifact = assets.get(name)
    checksum = assets.get(name + ".sha256")
    if artifact is None or checksum is None:
        raise ReleaseBundleError(f"release has no verified artifact for {system}-{architecture}")
    return version, artifact, checksum


def _download(spec: dict[str, Any], asset: dict[str, Any], destination: Path) -> None:
    url = str(asset.get("browser_download_url") or "")
    allowed = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed:
        raise ReleaseBundleError("release download host is not trusted")
    limit = int(spec["max_download_bytes"])
    request = urllib.request.Request(url, headers={"User-Agent": "ControlDeck"})
    written = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as stream:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname not in allowed:
            raise ReleaseBundleError("release download redirected outside trusted hosts")
        while chunk := response.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                raise ReleaseBundleError("download exceeds catalog size limit")
            stream.write(chunk)


def _expected_sha(spec: dict[str, Any], checksum: dict[str, Any], artifact_name: str) -> str:
    content = _bounded_get(
        str(checksum.get("browser_download_url") or ""),
        allowed_hosts={"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"},
        limit=4096,
    ).decode("ascii", errors="strict").strip()
    fields = content.split()
    if not fields or re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]) is None:
        raise ReleaseBundleError("release checksum is invalid")
    if len(fields) > 1 and fields[-1].lstrip("*") != artifact_name:
        raise ReleaseBundleError("release checksum names another artifact")
    pinned = str(spec.get("sha256") or "")
    if not pinned and not bool(spec.get("preview", False)):
        raise ReleaseBundleError("generally available catalog entries require a pinned SHA-256")
    if pinned and pinned.lower() != fields[0].lower():
        raise ReleaseBundleError("release checksum differs from trusted catalog pin")
    return fields[0].lower()


def _safe_extract(archive: Path, destination: Path, *, max_expanded_bytes: int) -> Path:
    total = 0
    roots: set[str] = set()
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = bundle.getmembers()
        if len(members) > 10_000:
            raise ReleaseBundleError("archive contains too many entries")
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or not path.parts or ".." in path.parts:
                raise ReleaseBundleError("archive contains an unsafe path")
            roots.add(path.parts[0])
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ReleaseBundleError("archive contains an unsupported entry type")
            if member.mode & (stat.S_ISUID | stat.S_ISGID):
                raise ReleaseBundleError("archive contains privileged mode bits")
            if member.isfile():
                total += member.size
                if total > max_expanded_bytes:
                    raise ReleaseBundleError("expanded bundle exceeds catalog size limit")
        if len(roots) != 1:
            raise ReleaseBundleError("archive must contain exactly one top-level directory")
        bundle.extractall(destination, filter="data")
    root = (destination / next(iter(roots))).resolve()
    if not root.is_relative_to(destination.resolve()) or not root.is_dir():
        raise ReleaseBundleError("archive root is invalid")
    return root


def _load_package(root: Path, feature_id: str, version: str, spec: dict[str, Any]) -> tuple[PackageManifest, Path, Path]:
    try:
        package = PackageManifest.model_validate_json((root / PACKAGE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseBundleError("package manifest is invalid") from exc
    system, architecture = host_platform()
    if (package.feature_id, package.version, package.platform, package.architecture) != (
        feature_id, version, system, architecture,
    ):
        raise ReleaseBundleError("package identity does not match catalog release")
    entrypoint = (root / package.entrypoint).resolve()
    addon_path = (root / package.addon_manifest).resolve()
    if not entrypoint.is_relative_to(root) or not entrypoint.is_file() or not os.access(entrypoint, os.X_OK):
        raise ReleaseBundleError("package entrypoint is not an executable regular file")
    if not addon_path.is_relative_to(root) or not addon_path.is_file():
        raise ReleaseBundleError("package Add-on manifest is missing")
    parsed = load_manifest_file(addon_path)
    if (
        not isinstance(parsed.manifest, AddonManifestV2)
        or parsed.manifest.id != str(spec["addon_id"])
        or parsed.manifest.version != version
    ):
        raise ReleaseBundleError("package Add-on identity does not match trusted catalog")
    allowed = set(spec.get("allowed_host_capabilities", []))
    if not set(parsed.manifest.host_capabilities).issubset(allowed):
        raise ReleaseBundleError("package requests capabilities outside the trusted catalog")
    addon_health = parsed.manifest.runtime.base_url.rstrip("/") + parsed.manifest.runtime.health_path
    if addon_health != str(package.health_url):
        raise ReleaseBundleError("package health URL does not match the Add-on manifest")
    return package, entrypoint, addon_path


def _atomic_current(root: Path, target: Path | None) -> None:
    current = root / "current"
    temporary = root / f".current-{os.getpid()}.tmp"
    if target is None:
        current.unlink(missing_ok=True)
        return
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(Path("versions") / target.name, target_is_directory=True)
    os.replace(temporary, current)


def _unit_name(feature_id: str) -> str:
    return f"cdapp-feature-{feature_id}.service"


def _write_service(feature_id: str, version_root: Path, package: PackageManifest, entrypoint: Path) -> None:
    root = _feature_root(feature_id)
    logs = _managed_directory(root, "logs")
    persistent = data_dir() / "feature-data" / feature_id
    persistent.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = systemd.build_unit_content(
        name=f"Optional feature: {feature_id}",
        exec_argv=[str(entrypoint), *package.service_args],
        working_directory=str(version_root),
        environment={
            "CONTROL_DECK_FEATURE_ROOT": str(version_root),
            "CONTROL_DECK_FEATURE_DATA_DIR": str(persistent),
            "CONTROL_DECK_SHARED_CACHE_DIR": str(data_dir() / "cache"),
        },
        restart_policy="on-failure",
        stop_timeout_seconds=30,
        stdout_path=logs / "service.log",
        stderr_path=logs / "service.log",
    )
    systemd.write_unit(_unit_name(feature_id), content)


def _wait_health(url: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = json.loads(response.read(64 * 1024 + 1))
            if response.status == 200 and body.get("status") in {"healthy", "setup_required"}:
                return
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.25)
    raise ReleaseBundleError("feature service did not become healthy")


def install(feature_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    root = _feature_root(feature_id)
    versions = _managed_directory(root, "versions")
    downloads = _managed_directory(root, "downloads")
    metadata = _metadata(spec)
    version, artifact, checksum = _select_release(spec, metadata)
    artifact_name = str(artifact["name"])
    partial = downloads / f"{artifact_name}.partial"
    partial.unlink(missing_ok=True)
    try:
        _download(spec, artifact, partial)
        expected = _expected_sha(spec, checksum, artifact_name)
        digest = hashlib.sha256()
        with partial.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected:
            raise ReleaseBundleError("release artifact SHA-256 mismatch")
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    old_target = (root / "current").resolve() if (root / "current").is_symlink() else None
    with tempfile.TemporaryDirectory(prefix=f".{version}-", dir=versions) as temporary:
        extracted = _safe_extract(partial, Path(temporary), max_expanded_bytes=int(spec["max_expanded_bytes"]))
        package, entrypoint, addon_path = _load_package(extracted, feature_id, version, spec)
        smoke = subprocess.run(
            [str(entrypoint), *package.smoke_args], cwd=extracted, capture_output=True, text=True,
            timeout=int(spec.get("smoke_timeout_sec", 60)), check=False,
        )
        if smoke.returncode != 0:
            raise ReleaseBundleError("release bundle smoke test failed")
        destination = versions / version
        if old_target is not None and destination.resolve() == old_target.resolve():
            final_download = downloads / artifact_name
            final_download.unlink(missing_ok=True)
            partial.rename(final_download)
            return {"version": version, "previous_version": version}
        if destination.exists():
            shutil.rmtree(destination)
        os.replace(extracted, destination)
    partial.rename(downloads / artifact_name)
    package, entrypoint, addon_path = _load_package(destination, feature_id, version, spec)
    try:
        _atomic_current(root, destination)
        _write_service(feature_id, destination, package, entrypoint)
        ok, error = systemd.restart(_unit_name(feature_id))
        if not ok:
            raise ReleaseBundleError(f"feature service failed to start: {error[:200]}")
        _wait_health(str(package.health_url))
        parsed = load_manifest_file(addon_path)
        addon_registry.install(parsed)
    except Exception:
        systemd.stop(_unit_name(feature_id))
        _atomic_current(root, old_target)
        if old_target is not None:
            old_package, old_entrypoint, old_addon = _load_package(old_target, feature_id, old_target.name, spec)
            _write_service(feature_id, old_target, old_package, old_entrypoint)
            systemd.restart(_unit_name(feature_id))
            addon_registry.install(load_manifest_file(old_addon))
        raise
    keep = max(1, int(spec.get("retain_versions", 2)))
    ordered = sorted(
        (item for item in versions.iterdir() if item.is_dir() and not item.is_symlink()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    protected = {destination.resolve()}
    if old_target is not None:
        protected.add(old_target.resolve())
    retained = 0
    for item in ordered:
        if item.resolve() in protected or retained < keep:
            retained += 1
            continue
        shutil.rmtree(item)
    return {"version": version, "previous_version": old_target.name if old_target else ""}


def uninstall(feature_id: str, spec: dict[str, Any]) -> None:
    root = _feature_root(feature_id)
    systemd.stop(_unit_name(feature_id))
    systemd.remove_unit(_unit_name(feature_id))
    try:
        addon_registry.set_enabled(str(spec["addon_id"]), False)
        addon_registry.uninstall(str(spec["addon_id"]))
    except addon_registry.AddonRegistryError:
        pass
    (root / "current").unlink(missing_ok=True)
    for name in ("versions", "downloads", "logs"):
        target = root / name
        if target.exists() and target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)


def current(feature_id: str) -> tuple[PackageManifest, Path] | None:
    root = _feature_root(feature_id)
    link = root / "current"
    if not link.is_symlink():
        return None
    target = link.resolve()
    versions = (root / "versions").resolve()
    if not target.is_relative_to(versions) or not target.is_dir():
        return None
    try:
        package = PackageManifest.model_validate_json((target / PACKAGE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return package, target
