"""Android SDK（コマンドライン）を ControlDeck の管理領域（data/tools/android-sdk）へ入れる機能。

目的: OpenCode の箱で Android の開発（ビルド・adb・fastboot）ができるようにする。root 不要（ユーザー空間）。
  - 取得は Google の公式の cmdline-tools の zip だけ。URL と sha256 は CATALOG に固定（最新を勝手に取らない）。
  - 外部への取得は利用者が設定の画面で「入れる」を押したときだけ（自動更新にも入れない）。取得の前に
    ensure_external_allowed（秘密の会話の中なら止まる）を通す。
  - zip は sha256 を確かめてから、中のパスが展開先の外へ出ない・リンクを含まない・名前が妥当、を検査して展開する。
  - 展開後、sdkmanager（argv・shell なし）で platform-tools・platforms;android-NN・build-tools;NN を入れる。
    ライセンスの同意は画面で利用者が押したときだけ（accept=True）。同意しなければ入れない。
  - 箱へは読み取り専用で bind し、ANDROID_HOME・ANDROID_SDK_ROOT・PATH を渡す（sandbox._tool_dirs / tool_path）。
    入っていなければ何も足さない。Gradle 本体は入れない（プロジェクトの gradlew を使う）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

FEATURE_ID = "android-sdk"

# 互換の目録（版の固定）。更新するときは人が動作を確かめて、ここの値を書き換える。
CATALOG = {
    "cmdline_tools": {
        "version": "15859902",
        "url": "https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip",
        "sha256": "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583",
        "max_bytes": 300 * 1024 * 1024,
        "hosts": ("dl.google.com",),
    },
    # 入れてよい platforms / build-tools（これ以外は断る）。既定は先頭
    "platforms": ("34", "35"),
    "build_tools": ("34.0.0", "35.0.0"),
    "defaults": {"platform": "34", "build_tools": "34.0.0"},
    "license_url": "https://developer.android.com/studio/terms",
}

PLATFORM_RE = re.compile(r"^\d{2}$")
BUILD_TOOLS_RE = re.compile(r"^\d{2}\.\d\.\d$")
MEMBER_RE = re.compile(r"^[A-Za-z0-9._+@~ -]{1,128}$")
STATE_FILE = ".controldeck-android.json"
MAX_MEMBERS = 5000
MAX_EXPANDED = 1024 * 1024 * 1024

GUIDE = "設定 → 機能と拡張 → Android SDK（コマンドライン）を導入してください"


class AndroidSdkError(RuntimeError):
    pass


def _data() -> Path:
    from app.config import data_dir

    return Path(data_dir()).resolve()


def sdk_root(data: Path | None = None) -> Path:
    return (data.resolve() if data is not None else _data()) / "tools" / "android-sdk"


def _bin(root: Path, *parts: str) -> Path:
    return root.joinpath(*parts)


def sdkmanager_path(root: Path | None = None) -> Path:
    return _bin(root or sdk_root(), "cmdline-tools", "latest", "bin", "sdkmanager")


def adb_path(root: Path | None = None) -> Path:
    return _bin(root or sdk_root(), "platform-tools", "adb")


def read_state(root: Path | None = None) -> dict:
    try:
        raw = json.loads(((root or sdk_root()) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_state(root: Path, state: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    temp = root / (STATE_FILE + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, root / STATE_FILE)


def installed(root: Path | None = None) -> bool:
    root = root or sdk_root()
    return sdkmanager_path(root).is_file() and adb_path(root).is_file()


def check_choice(platform: str, build_tools: str) -> None:
    if platform not in CATALOG["platforms"] or not PLATFORM_RE.match(platform):
        raise AndroidSdkError(f"対応していない Android のバージョンです（{platform}）")
    if build_tools not in CATALOG["build_tools"] or not BUILD_TOOLS_RE.match(build_tools):
        raise AndroidSdkError(f"対応していない build-tools です（{build_tools}）")


def packages(platform: str, build_tools: str) -> list[str]:
    """sdkmanager に渡すパッケージ名（固定の目録の範囲だけ）。"""
    check_choice(platform, build_tools)
    return ["platform-tools", f"platforms;android-{platform}", f"build-tools;{build_tools}"]


def sdkmanager_argv(root: Path, args: list[str]) -> list[str]:
    return [str(sdkmanager_path(root)), f"--sdk_root={root}", *args]


def java_ok(java: str | None = None) -> bool:
    return bool(java or shutil.which("java"))


# ---------------------------------------------------------------- 取得・展開

def _fetch(url: str, destination: Path, max_bytes: int) -> None:
    """公式の zip を取得する。呼ぶのは利用者のボタン操作のジョブだけ。試験では差し替える。"""
    from urllib.parse import urlsplit

    from app.security.offline import ensure_external_allowed

    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in CATALOG["cmdline_tools"]["hosts"]:
        raise AndroidSdkError("取得先が目録の外です")
    ensure_external_allowed("Android SDK の取得")
    request = urllib.request.Request(url, headers={"User-Agent": "ControlDeck"})
    total = 0
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as stream:  # noqa: S310 - https と host を固定
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise AndroidSdkError("取得したファイルが大きすぎます")
            stream.write(chunk)


def verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected.lower():
        raise AndroidSdkError("取得したファイルの sha256 が目録と一致しません（取り込みを中止しました）")


def safe_extract(archive: Path, target: Path) -> None:
    """zip を target へ展開する。パスの逃げ出し・絶対パス・リンク・不正な名前・大きすぎるものは断る。"""
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        if len(infos) > MAX_MEMBERS:
            raise AndroidSdkError("zip の中のファイルが多すぎます")
        if sum(i.file_size for i in infos) > MAX_EXPANDED:
            raise AndroidSdkError("zip の展開後の大きさが大きすぎます")
        for info in infos:
            name = info.filename
            posix = PurePosixPath(name)
            if name.startswith("/") or "\\" in name or ".." in posix.parts or not posix.parts:
                raise AndroidSdkError(f"zip の中のパスが不正です（{name[:80]}）")
            if any(not MEMBER_RE.match(part) for part in posix.parts):
                raise AndroidSdkError(f"zip の中の名前が不正です（{name[:80]}）")
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode) or (mode and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise AndroidSdkError(f"zip の中にリンクなど不正な種類があります（{name[:80]}）")
            dest = (target / Path(*posix.parts)).resolve()
            if dest != target and target not in dest.parents:
                raise AndroidSdkError(f"zip の中のパスが展開先の外へ出ます（{name[:80]}）")
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
            dest.chmod(0o755 if mode & 0o111 else 0o644)


def install_cmdline_tools(root: Path, *, fetch: Callable[[str, Path, int], None] | None = None) -> None:
    """cmdline-tools を root/cmdline-tools/latest へ入れる。入っていれば何もしない（壊れていれば置き換える）。"""
    if sdkmanager_path(root).is_file():
        return
    spec = CATALOG["cmdline_tools"]
    staging = root / ".staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        archive = staging / "cmdline-tools.zip"
        (fetch or _fetch)(spec["url"], archive, spec["max_bytes"])
        verify_sha256(archive, spec["sha256"])
        safe_extract(archive, staging / "x")
        inner = staging / "x" / "cmdline-tools"
        if not (inner / "bin" / "sdkmanager").is_file():
            raise AndroidSdkError("zip の中身が想定と違います（cmdline-tools/bin/sdkmanager が無い）")
        latest = root / "cmdline-tools" / "latest"
        if latest.exists():
            shutil.rmtree(latest)
        latest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(inner), str(latest))
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _run(run: Callable, argv: list[str], *, text_input: str | None = None, timeout: int = 3600) -> subprocess.CompletedProcess:
    try:
        return run(argv, capture_output=True, text=True, timeout=timeout, check=False, input=text_input,
                   stdin=None if text_input is not None else subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AndroidSdkError(f"sdkmanager を実行できませんでした（{exc}）") from exc


def _tail(result: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in ((result.stderr or "") + (result.stdout or "")).strip().splitlines() if ln.strip()]
    return f"（{lines[-1][-200:]}）" if lines else ""


def install(*, accept_licenses: bool, platform: str | None = None, build_tools: str | None = None,
            root: Path | None = None, fetch: Callable | None = None, run: Callable = subprocess.run) -> dict:
    """cmdline-tools を入れ、sdkmanager で platform-tools・platforms・build-tools を入れる（導入も修復も同じ）。
    外部から取得する。呼ぶのは利用者のボタン操作のジョブだけ。accept_licenses が False なら何も取得しない。"""
    if not accept_licenses:
        raise AndroidSdkError("Android SDK のライセンスへの同意が必要です（画面の同意にチェックを入れてください）")
    platform = platform or CATALOG["defaults"]["platform"]
    build_tools = build_tools or CATALOG["defaults"]["build_tools"]
    pkgs = packages(platform, build_tools)
    root = root or sdk_root()
    if not java_ok():
        raise AndroidSdkError("Java（JDK 17 以上）が見つかりません。sdkmanager は Java で動きます（sudo apt install openjdk-21-jdk-headless）")
    root.mkdir(parents=True, exist_ok=True)
    install_cmdline_tools(root, fetch=fetch)
    # 同意したときだけ yes を渡す（同意した記録は state に残す）
    licenses = _run(run, sdkmanager_argv(root, ["--licenses"]), text_input="y\n" * 64)
    if licenses.returncode != 0:
        raise AndroidSdkError(f"ライセンスの同意に失敗しました{_tail(licenses)}")
    result = _run(run, sdkmanager_argv(root, pkgs))
    if result.returncode != 0 or not adb_path(root).is_file():
        raise AndroidSdkError(f"Android SDK の導入に失敗しました{_tail(result)}")
    _write_state(root, {"cmdline_tools": CATALOG["cmdline_tools"]["version"], "platform": platform,
                        "build_tools": build_tools, "licenses_accepted": True})
    return status(root)


def update(*, root: Path | None = None, run: Callable = subprocess.run) -> dict:
    """入れてあるものを sdkmanager --update で更新する（同意済みのときだけ）。cmdline-tools の版は目録のまま。"""
    root = root or sdk_root()
    state = read_state(root)
    if not installed(root):
        raise AndroidSdkError("ControlDeck が導入した Android SDK がありません")
    if not state.get("licenses_accepted"):
        raise AndroidSdkError("ライセンスに同意した記録がありません（入れ直してください）")
    result = _run(run, sdkmanager_argv(root, ["--update"]))
    if result.returncode != 0:
        raise AndroidSdkError(f"Android SDK の更新に失敗しました{_tail(result)}")
    return status(root)


def uninstall(root: Path | None = None) -> None:
    """管理領域の Android SDK だけを消す（data/tools/android-sdk の外は消さない）。"""
    root = root or sdk_root()
    if root.name != "android-sdk" or root.parent.name != "tools":
        raise AndroidSdkError("削除対象が管理領域の外です")
    if root.exists():
        shutil.rmtree(root)


def _ls(directory: Path) -> list[str]:
    try:
        return sorted(p.name for p in directory.iterdir() if p.is_dir())
    except OSError:
        return []


def status(root: Path | None = None) -> dict:
    root = root or sdk_root()
    state = read_state(root)
    ok = installed(root)
    return {"installed": ok, "root": str(root), "cmdline_tools": state.get("cmdline_tools", ""),
            "platforms": [n.removeprefix("android-") for n in _ls(root / "platforms")],
            "build_tools": _ls(root / "build-tools"), "licenses_accepted": bool(state.get("licenses_accepted")),
            "java": java_ok(), "catalog": {"platforms": list(CATALOG["platforms"]), "build_tools": list(CATALOG["build_tools"]),
                                           "defaults": CATALOG["defaults"], "license_url": CATALOG["license_url"]}}


# ---------------------------------------------------------------- 箱へ渡すもの

def _newest(names: list[str]) -> str:
    return sorted(names, key=lambda n: [int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", n)])[-1] if names else ""


def path_entries(root: Path | None = None) -> list[str]:
    """箱の PATH に足すディレクトリ（入っていなければ空）。fastboot・adb は platform-tools のもの。"""
    root = root or sdk_root()
    if not installed(root):
        return []
    entries = [root / "platform-tools", root / "cmdline-tools" / "latest" / "bin"]
    newest = _newest(_ls(root / "build-tools"))
    if newest:
        entries.append(root / "build-tools" / newest)
    return [str(p) for p in entries if p.is_dir()]


def box_env(root: Path | None = None) -> dict[str, str]:
    """箱へ渡す環境変数（入っていなければ空）。Gradle のキャッシュは箱の HOME（~/.gradle）に残る。"""
    root = root or sdk_root()
    return {"ANDROID_HOME": str(root), "ANDROID_SDK_ROOT": str(root)} if installed(root) else {}


def notes_lines(root: Path | None = None) -> list[str]:
    """controldeck-usb.md に足す Android の説明（入っていなければ空）。"""
    root = root or sdk_root()
    if not installed(root):
        return []
    return ["", "Android の道具（ControlDeck が入れた Android SDK。読み取り専用）:",
            f"- ANDROID_HOME={root}。adb・fastboot は {root}/platform-tools、sdkmanager は cmdline-tools/latest/bin、aapt などは build-tools にあります（PATH に入っています）。",
            "- 機器が見えるかは `adb devices` で確かめます（sudo は不要・使えません）。`unauthorized` なら端末の画面で USB デバッグを許可するよう利用者に伝えてください。",
            "- ホストの adb サーバーが機器をつかんでいると箱の adb から見えません。見えないときは利用者に「USB 機器の画面でホストの adb を止める」よう案内してください。",
            "- Gradle 本体は入っていません。プロジェクトの ./gradlew を使ってください（キャッシュは ~/.gradle）。",
            "- SDK の中身は読み取り専用なので sdkmanager で追加の導入はできません（足りないときは利用者に設定の機能で入れ直すよう伝えてください）。",
            "- Gradle の依存の取得（Maven）には外への通信が要ります。作業場（通信が中継だけの箱）では取れないので、取れないときは利用者に伝えてください。"]
