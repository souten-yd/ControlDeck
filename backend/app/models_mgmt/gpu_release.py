"""GitHub リリースで配布される GPU ランタイム資材の取得・検証・展開。

llama.cpp（souten-yd/llama-builder）と Lucebox（souten-yd/AMDLucebox）は
どちらも「タグ付きリリースの tar アーカイブ + SHA256SUMS」という同じ形で
配布される。取得・整合性検証・安全な展開はランタイム非依存なのでここへ集約し、
ランタイム固有の判断（どの asset を選ぶか、展開後に何を current にするか）だけを
呼び出し側へ残す。

方針:
- 署名まではリリース側に無いため、SHA256SUMS があれば必ず突き合わせる。
- 展開はパストラバーサル・シンボリックリンク・特殊ファイルを拒否する。
- .tar.zst は zstd CLI へ委譲する（Ubuntu では標準で入る）。shell は使わない。
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger("control_deck.gpu_release")

API_ROOT = "https://api.github.com"
USER_AGENT = "ControlDeck"
# リリース情報は数分単位でしか変わらない。更新確認のたびに GitHub を叩くと
# 未認証の 60req/h をすぐ使い切るため、短時間だけ memo 化する。
CACHE_TTL_SECONDS = 300
# SHA256SUMS 自体は小さい。取り違え防止のため上限を明示する。
MAX_CHECKSUM_BYTES = 1 << 20

_cache: dict[tuple[str, str], tuple[float, dict]] = {}


class ReleaseError(RuntimeError):
    """リリースの取得・検証・展開に失敗した（利用者向けメッセージを持つ）。"""


def _now() -> float:
    import time

    return time.monotonic()


async def fetch_release(repo: str, *, tag: str = "", use_cache: bool = True) -> dict:
    """リリース1件を取得する。tag 未指定なら最新（prerelease を除く）。

    戻り値は {"tag", "name", "published_at", "prerelease", "assets": [...]}。
    """
    key = (repo, tag)
    if use_cache:
        cached = _cache.get(key)
        if cached and _now() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
    url = (f"{API_ROOT}/repos/{repo}/releases/tags/{tag}" if tag
           else f"{API_ROOT}/repos/{repo}/releases/latest")
    try:
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        raise ReleaseError(f"リリース情報を取得できませんでした（{repo}）") from exc
    if response.status_code == 404:
        raise ReleaseError(f"リリースが見つかりません（{repo}{f' / {tag}' if tag else ''}）")
    if response.status_code == 403:
        raise ReleaseError("GitHub APIの利用制限に達しました。しばらく待って再試行してください")
    if response.status_code >= 400:
        raise ReleaseError(f"リリース情報の取得に失敗しました（HTTP {response.status_code}）")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ReleaseError("リリース情報の形式が不正です") from exc
    release = {
        "tag": str(payload.get("tag_name") or ""),
        "name": str(payload.get("name") or ""),
        "published_at": str(payload.get("published_at") or ""),
        "prerelease": bool(payload.get("prerelease")),
        "assets": [
            {
                "name": str(asset.get("name") or ""),
                "size": int(asset.get("size") or 0),
                "download_url": str(asset.get("browser_download_url") or ""),
                "updated_at": str(asset.get("updated_at") or ""),
            }
            for asset in payload.get("assets", [])
            if isinstance(asset, dict) and asset.get("browser_download_url")
        ],
    }
    if not release["tag"]:
        raise ReleaseError("リリースにタグがありません")
    _cache[key] = (_now(), release)
    return release


def invalidate_cache(repo: str = "") -> None:
    """導入直後など、次の確認で最新を引き直したいときに使う。"""
    for key in [k for k in _cache if not repo or k[0] == repo]:
        _cache.pop(key, None)


def pick_asset(assets: list[dict], pattern: re.Pattern[str]) -> dict | None:
    """パターンに一致する asset を1つ返す。複数あれば名前順で決定的に選ぶ。"""
    matched = sorted((a for a in assets if pattern.search(a["name"])), key=lambda a: a["name"])
    return matched[0] if matched else None


async def fetch_checksums(assets: list[dict]) -> dict[str, str]:
    """SHA256SUMS asset を読み、{ファイル名: 16進digest} を返す。無ければ空。"""
    entry = next((a for a in assets if a["name"] == "SHA256SUMS"), None)
    if entry is None or entry["size"] > MAX_CHECKSUM_BYTES:
        return {}
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            response = await client.get(entry["download_url"])
        if response.status_code >= 400:
            return {}
        text = response.text
    except (httpx.HTTPError, UnicodeDecodeError):
        return {}
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            continue
        sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


async def download_asset(job, asset: dict, destination: Path, *, expected_sha256: str = "",
                         label: str = "ダウンロード中") -> str:
    """asset をダウンロードし、sha256 を返す。expected 指定時は不一致で失敗する。"""
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    done = 0
    total = int(asset.get("size") or 0)
    try:
        async with httpx.AsyncClient(timeout=None, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            async with client.stream("GET", asset["download_url"]) as response:
                if response.status_code >= 400:
                    raise ReleaseError(f"ダウンロードに失敗しました（HTTP {response.status_code}）")
                with destination.open("wb") as handle:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        handle.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        if job is not None:
                            job.set_progress(label, done, total or done)
    except httpx.HTTPError as exc:
        destination.unlink(missing_ok=True)
        raise ReleaseError("ダウンロード中に通信が切れました") from exc
    hexdigest = digest.hexdigest()
    if expected_sha256 and hexdigest != expected_sha256.lower():
        destination.unlink(missing_ok=True)
        raise ReleaseError(f"{asset['name']} のSHA256が公開値と一致しません")
    return hexdigest


def _inside(root: Path, candidate: Path) -> bool:
    """候補パスが root 配下かを、実体を辿らずに判定する。

    展開中はリンク先がまだ存在しないので resolve() は使えない（存在しない側を
    辿れず、途中の既存シンボリックリンクは辿ってしまう）。字句的に正規化して見る。
    """
    normalized = Path(os.path.normpath(str(candidate)))
    return normalized == root or normalized.is_relative_to(root)


def _safe_members(tar: tarfile.TarFile, root: Path):
    """展開して安全なメンバーだけを返す。危険なものは即座に失敗させる。

    共有ライブラリの soname（libfoo.so → libfoo.so.1）はランタイム配布物に必ず
    含まれるので、リンク自体は拒否しない。拒否するのは展開先の外を指すものだけ。
    """
    resolved_root = root.resolve()
    for member in tar.getmembers():
        target = resolved_root / member.name
        if not _inside(resolved_root, target):
            raise ReleaseError(f"不正なアーカイブメンバー: {member.name}")
        if member.issym() or member.islnk():
            link = member.linkname
            if member.islnk():
                destination = resolved_root / link   # hard link はアーカイブ root 起点
            elif os.path.isabs(link):
                destination = Path(link)             # 絶対 symlink は必ず外を指す
            else:
                destination = target.parent / link   # 相対 symlink はメンバー位置起点
            if not _inside(resolved_root, destination):
                raise ReleaseError(f"展開先の外を指すリンクです: {member.name} -> {link}")
        elif not (member.isfile() or member.isdir()):
            raise ReleaseError(f"アーカイブに特殊ファイルが含まれています: {member.name}")
        yield member


def _decompress_zst(archive: Path, destination: Path) -> None:
    zstd = shutil.which("zstd")
    if zstd is None:
        raise ReleaseError("zstd が必要です（sudo apt install zstd）")
    result = subprocess.run(
        [zstd, "-d", "-q", "-f", str(archive), "-o", str(destination)],
        capture_output=True, text=True, timeout=1800, check=False,
    )
    if result.returncode != 0 or not destination.is_file():
        raise ReleaseError("アーカイブの展開（zstd）に失敗しました")


def extract_archive(archive: Path, destination: Path) -> Path:
    """.tar.gz / .tar.zst を destination へ安全に展開する（destination は作り直す）。"""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    name = archive.name.lower()
    temporary: Path | None = None
    try:
        if name.endswith(".tar.zst") or name.endswith(".tzst"):
            handle, path = tempfile.mkstemp(suffix=".tar", dir=str(archive.parent))
            os.close(handle)
            temporary = Path(path)
            _decompress_zst(archive, temporary)
            source, mode = temporary, "r:"
        elif name.endswith(".tar.gz") or name.endswith(".tgz"):
            source, mode = archive, "r:gz"
        else:
            raise ReleaseError(f"未対応のアーカイブ形式です: {archive.name}")
        with tarfile.open(source, mode) as tar:
            # メンバー検査は _safe_members が行う。filter="data" は Python 3.14 の
            # 既定に先回りして、所有者・権限・特殊ファイルの扱いも安全側へ固定する。
            tar.extractall(destination, members=_safe_members(tar, destination), filter="data")
    except tarfile.TarError as exc:
        shutil.rmtree(destination, ignore_errors=True)
        raise ReleaseError("アーカイブの展開に失敗しました") from exc
    except ReleaseError:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def find_binary(root: Path, name: str) -> Path | None:
    """展開ディレクトリ配下から実行ファイルを探す（決定的に浅い方を優先）。"""
    candidates = sorted(
        (path for path in root.rglob(name) if path.is_file()),
        key=lambda path: (len(path.relative_to(root).parts), str(path)),
    )
    return candidates[0] if candidates else None


def relink(link: Path, target: Path) -> None:
    """current シンボリックリンクを差し替える（同一パスのまま指し先だけ変える）。"""
    link.parent.mkdir(parents=True, exist_ok=True)
    staging = link.with_name(link.name + ".next")
    if staging.is_symlink() or staging.exists():
        staging.unlink()
    staging.symlink_to(target, target_is_directory=True)
    os.replace(staging, link)


def prune_versions(root: Path, keep: list[str]) -> list[str]:
    """保持対象以外の版ディレクトリを削除して、削除した名前を返す。"""
    removed: list[str] = []
    if not root.is_dir():
        return removed
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.is_symlink() or child.name in keep:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed.append(child.name)
    return removed
