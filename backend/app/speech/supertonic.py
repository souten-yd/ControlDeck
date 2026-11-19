"""Supertonic 3（Supertone の多言語 TTS・ONNX・CPU）の保管庫・導入・合成。

2026-11-19 利用者「Supertonic 3 を TTS として使いたい。開発中止・サポート終了で更新も消去もありうるので、
リポジトリを丸ごとアーカイブとして ControlDeck に保存し、機能追加から導入できるように。導入時は音声モデルも」。
同日、公式 GitHub（supertone-inc/supertonic）は archived で、置き場は supertone-oss-archive へ移っていた。

保管庫（data/archives/supertonic-3/）に置くもの:
  git/        ソース 3 本の --mirror（全ブランチ・タグ）。一度取ったら上書きしない（消された後の取り直しで失わない）
  huggingface/ 重みの全ファイル（版を固定・サイズと SHA-256 を照合）。音声サンプルや図も含む
  pypi/       SDK（supertonic 1.3.1 の wheel・sdist）と、実行に要る wheel（版固定）。オフラインで入れ直せる
  MANIFEST.json  全ファイルのサイズ・SHA-256 と取得元
アンインストールで消すのは実行環境（venv）だけで、保管庫は残す。

実測（2026-11-19・CPU 4 スレッド）: 日本語 5.5 秒の音声を 0.38 秒で生成、最大 RSS 約 0.5 GB。Haas-ja で聞き戻して原文どおり。
合成は 1 回ごとに別プロセス（専用 venv の python -I）で動かし、本体にメモリを残さない。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import httpx

from app.config import data_dir
from app.jobs import service as jobs

logger = logging.getLogger("control_deck.speech.supertonic")

ENGINE_ID = "supertonic-3"
NAME = "Supertonic 3"
LICENSE_NOTE = "重み: OpenRAIL-M / コード: MIT（Supertone Inc.）"

GIT_REPOS = {
    "supertonic": "https://github.com/supertone-oss-archive/supertonic.git",
    "supertonic-py": "https://github.com/supertone-oss-archive/supertonic-py.git",
    "supertonic-rust": "https://github.com/supertone-oss-archive/supertonic-rust.git",
}
HF_REPO = "supertone-oss-archive/supertonic-3"
HF_REVISION = "aafc6e32416a594460b32413efc49d7fe4ce6d46"
# 全ファイル（2026-11-19 取得・照合）。path: (bytes, sha256)
HF_FILES: dict[str, tuple[int, str]] = {
    ".gitattributes": (127, "71da9814fb887a641658bbf45844621d725e8179cfcdf84cf68a8934a300d924"),
    ".gitignore": (22, "0562091e4be77c4262c11672de88360efc6d1819c999612e5d6d0e292baa1eda"),
    "LICENSE": (15007, "0d944a9110fed9a9602d60e0423a272903e7bd21ab060490774efc77c2275e9f"),
    "README.md": (9189, "d414e4b085fb9989ebfd2b64f868ff861694022b4bdb805b09e702386dbdc7d5"),
    "audio_samples/alphonse_reference.wav": (1152044, "09e613442124ee0cb31146431bbe0b3acd727569a55fc0ae4b4082b5de27c9f3"),
    "audio_samples/alphonse_supertonic3.wav": (970796, "f3dacbfa5695d6416fb46a3ea72e202e16a467a4cb5ead9425073ef83596811c"),
    "audio_samples/keld_reference.wav": (1152044, "8d7f3c2b15ee6ebafe88307658bf0787d3ca19f6347fd9e936e7bff786d1f70a"),
    "audio_samples/keld_supertonic3.wav": (1148972, "818c2b25bbf3e568ae8d4aeea5441605ed3e124ab28d746928972066902b35d9"),
    "audio_samples/luna_reference.wav": (1152044, "1fcec63c22ad76711ec9835a6ca21cb84227045e3aad808f841bcbec455a9470"),
    "audio_samples/luna_supertonic3.wav": (712748, "34527bf7ed968ebc5024f985d48ccf06c5c4418fb2d7fc7e3fb7a873e367c273"),
    "audio_samples/moka_reference.wav": (480044, "ad50e1f13c18aa6af2e0f518714675c03ed98f0e85653a642527f56c2d181a56"),
    "audio_samples/moka_supertonic3.wav": (479276, "bec7d6a5261240935d8436f3ada17aa50774878e8e6ad1696d131e4ec4ecd5f3"),
    "audio_samples/nora_reference.wav": (264644, "a59576e7ffe84093439924f14d7070fb8174f8306c27049c00e63d747434a772"),
    "audio_samples/nora_supertonic3.wav": (374828, "6d11415a428949df69191361b9dc5f567aaf6eccd89c13013c2fbb381b353321"),
    "audio_samples/watson_reference.wav": (2304044, "5449908651219298eab47a32a04b96291baceea00e772ea9cb16853a93127c30"),
    "audio_samples/watson_supertonic3.wav": (1019948, "e8ffb0051f00e3f6476694604a3d21f6e87b3570b9b73fc2fdb9560c9e6f1c67"),
    "config.json": (174, "4099082b107a9d4029849ac76b89eca65e03732660969c2babe5bf308c7357f2"),
    "img/Supertonic3_HeroImage.png": (1499338, "7498880b738494d758a6e37b0c730c46ad0f22775c5076cb0e3ad4062ef8e8be"),
    "img/metrics/model_size_comparison.png": (94546, "c7fcffffdb70b3850f3e3e9552eaf2c263749314f4e39ff9d88e189ce3593322"),
    "img/metrics/runtime_cpu_gpu_latency_memory.png": (262397, "a80c0def839ea570b207e06cc1d3d5aa99e58a24dc85b3dda2688b2dd2c79ec0"),
    "img/metrics/s3_vs_measured_wer_range_voxcpm2.png": (197548, "b04a427ca1f7a97b6021ba4c518f3318104d2e85023ac0af3373b844021a1db1"),
    "img/metrics/supertonic2_vs_3_comparison.png": (161950, "b40da99bfe032f8ce4713bb18305842a1a9a23b01a9642992721a4172949fb28"),
    "onnx/duration_predictor.onnx": (3700147, "c3eb91414d5ff8a7a239b7fe9e34e7e2bf8a8140d8375ffb14718b1c639325db"),
    "onnx/text_encoder.onnx": (36416150, "c7befd5ea8c3119769e8a6c1486c4edc6a3bc8365c67621c881bbb774b9902ff"),
    "onnx/tts.json": (8253, "42078d3aef1cd43ab43021f3c54f47d2d75ceb4e75f627f118890128b06a0d09"),
    "onnx/unicode_indexer.json": (277676, "9bf7346e43883a81f8645c81224f786d43c5b57f3641f6e7671a7d6c493cb24f"),
    "onnx/vector_estimator.onnx": (256534781, "883ac868ea0275ef0e991524dc64f16b3c0376efd7c320af6b53f5b780d7c61c"),
    "onnx/vocoder.onnx": (101424195, "085de76dd8e8d5836d6ca66826601f615939218f90e519f70ee8a36ed2a4c4ba"),
    "voice_styles/F1.json": (292046, "bbdec6ee00231c2c742ad05483df5334cab3b52fda3ba38e6a07059c4563dbc2"),
    "voice_styles/F2.json": (292423, "7c722c6a72707b1a77f035d67f0d1351ba187738e06f7683e8c72b1df3477fc6"),
    "voice_styles/F3.json": (290794, "12f6ef2573baa2defa1128069cb59f203e3ab67c92af77b42df8a0e3a2f7c6ab"),
    "voice_styles/F4.json": (291808, "c2fa764c1225a76dfc3e2c73e8aa4f70d9ee48793860eb34c295fff01c2e032b"),
    "voice_styles/F5.json": (291479, "45966e73316415626cf41a7d1c6f3b4c70dbc1ba2bee5c1978ef0ce33244fc8d"),
    "voice_styles/M1.json": (291748, "e35604687f5d23694b8e91593a93eec0e4eca6c0b02bb8ed69139ab2ea6b0a5b"),
    "voice_styles/M2.json": (292055, "b76cbf62bac707c710cf0ae5aba5e31eea1a6339a9734bfae33ab98499534a50"),
    "voice_styles/M3.json": (290198, "ea1ac35ccb91b0d7ecad533a2fbd0eec10c91513d8951e3b25fbba99954e159b"),
    "voice_styles/M4.json": (291522, "ca8eefad4fcd989c9379032ff3e50738adc547eeb5e221b82593a6d7b3bac303"),
    "voice_styles/M5.json": (291469, "dd22b92740314321f8ae11c5e87f8dd60d060f15dd3a632b5adf77f471f77af2"),
}
# 合成に要る重み（音声サンプル・図・README が欠けても話せる）
MODEL_FILES = tuple(name for name in HF_FILES if name.startswith(("onnx/", "voice_styles/")))
# SDK は PyPI から直接取る（取得元の URL と SHA-256 を固定）
PYPI_FILES = {
    "supertonic-1.3.1-py3-none-any.whl": (
        51871, "0079c9d4166008b8a6eeae95f20c092148786b7232192dd3dd9f358960c6c077",
        "https://files.pythonhosted.org/packages/8f/9f/d3c0367115b378d09a3866502609841c283fbd48c5b8f58902a03f81b752/supertonic-1.3.1-py3-none-any.whl"),
    "supertonic-1.3.1.tar.gz": (
        54853, "4367e8f61afea618dac948f6bee55fed4721ad66ca2d3fc90771a2a66740731e",
        "https://files.pythonhosted.org/packages/e9/fe/1431393433d0c0570b54b8bd1307502fa0231238ed2cd9506c3e2799a12a/supertonic-1.3.1.tar.gz"),
}
# 実行に要るもの。SDK の huggingface-hub・soundfile は使わない（重みは保管庫から読み、WAV は標準ライブラリで書く）
RUNTIME_PACKAGES = ("supertonic==1.3.1", "onnxruntime==1.32.0", "numpy==2.5.5", "flatbuffers==25.12.19",
                    "packaging==26.3", "protobuf==7.36.2")
VOICES = ("F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5")
LANGUAGES = ("ja", "en", "ko", "ar", "bg", "cs", "da", "de", "el", "es", "et", "fi", "fr", "hi", "hr", "hu", "id",
             "it", "lt", "lv", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "tr", "uk", "vi", "na")
MAX_TEXT_CHARS = 5000
THREADS = 4
SAMPLE_RATE = 44100
_gate = asyncio.Semaphore(1)     # 1 回 約 0.5 GB。同時に動かさない
last_error = ""                  # 直近の合成の失敗（自己診断）
last_archive_error = ""          # 保管庫の一部を取れなかった理由（自己診断）
_install_job_id: str | None = None


class SupertonicError(RuntimeError):
    pass


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise SupertonicError("Supertonic の領域外のパスは使えません")
    return resolved


def archive_root() -> Path:
    root = (data_dir() / "archives" / "supertonic-3").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def model_dir() -> Path:
    root = archive_root()
    return _inside(root / "huggingface" / HF_REPO.replace("/", "__") / HF_REVISION, root)


def runtime_root() -> Path:
    root = (data_dir() / "runtimes" / "tts-supertonic" / "v3").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def python_path() -> Path:
    return runtime_root() / "venv" / "bin" / "python"


def _size_ok(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def model_ready() -> bool:
    model = model_dir()
    return all(_size_ok(model / name, HF_FILES[name][0]) for name in MODEL_FILES)


def venv_ready() -> bool:
    try:
        marker = (runtime_root() / "venv-ready").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return python_path().is_file() and marker == ",".join(RUNTIME_PACKAGES)


def ready() -> bool:
    return venv_ready() and model_ready()


def archive_status() -> dict:
    root = archive_root()
    model = model_dir()
    git = {name: (root / "git" / f"{name}.git" / "HEAD").is_file() for name in GIT_REPOS}
    hf_missing = [name for name, (size, _) in HF_FILES.items() if not _size_ok(model / name, size)]
    pypi_missing = [name for name, (size, _, _) in PYPI_FILES.items() if not _size_ok(root / "pypi" / name, size)]
    wheels = sorted(p.name for p in (root / "pypi").glob("*.whl")) if (root / "pypi").is_dir() else []
    complete = all(git.values()) and not hf_missing and not pypi_missing
    return {
        "path": str(root),
        "complete": complete,
        "git": git,
        "model_files": len(HF_FILES) - len(hf_missing),
        "model_files_total": len(HF_FILES),
        "missing": hf_missing[:20] + pypi_missing,
        "wheels": wheels,
        "bytes": sum(HF_FILES[name][0] for name in HF_FILES if name not in hf_missing),
        "error": last_archive_error,
    }


def status() -> dict:
    active = jobs.get(_install_job_id) if _install_job_id else None
    installing = bool(active and active.status in ("queued", "running"))
    is_ready = ready()
    return {
        "id": ENGINE_ID,
        "name": NAME,
        "kind": "tts",
        "ready": is_ready,
        "installed": is_ready,
        "installing": installing,
        "job_id": active.id if installing and active else None,
        "runtime": "ONNX Runtime 1.32.0（CPU）",
        "model": f"{HF_REPO}@{HF_REVISION[:8]}",
        "model_bytes": sum(HF_FILES[name][0] for name in MODEL_FILES),
        "storage": str(model_dir()),
        "voices": list(VOICES),
        "languages": list(LANGUAGES),
        "license": LICENSE_NOTE,
        "error": last_error,
        "archive": archive_status(),
    }


async def _run(job: jobs.Job | None, *args: str, cwd: Path, timeout: float) -> None:
    process = await asyncio.create_subprocess_exec(
        *args, cwd=str(cwd), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise SupertonicError(f"{Path(args[0]).name} が時間内に終わりませんでした")
    if job is not None:
        for line in output.decode("utf-8", errors="replace").splitlines()[-15:]:
            if line.strip():
                job.log(line.strip()[:500])
    if process.returncode != 0:
        raise SupertonicError(f"{Path(args[0]).name} が終了コード {process.returncode} で失敗しました")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _download(client: httpx.AsyncClient, url: str, target: Path, size: int, sha256: str,
                    root: Path, on_chunk) -> None:
    part = _inside(target.with_name(target.name + ".part"), root)
    target.parent.mkdir(parents=True, exist_ok=True)
    part.unlink(missing_ok=True)
    digest = hashlib.sha256()
    received = 0
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with part.open("wb") as stream:
                def store(data: bytes) -> None:
                    stream.write(data)
                    digest.update(data)

                async for chunk in response.aiter_bytes(1024 * 1024):
                    await asyncio.to_thread(store, chunk)
                    received += len(chunk)
                    on_chunk(len(chunk))
        if received != size or digest.hexdigest() != sha256:
            raise SupertonicError(f"{target.name} のサイズまたは SHA-256 が一致しません")
        part.replace(target)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


async def _archive_git(job: jobs.Job, root: Path) -> list[str]:
    """ソースを --mirror で取る。既にあるものは取り直さない（消された・書き換えられた上流で上書きしない）。"""
    git = shutil.which("git")
    failed: list[str] = []
    if not git:
        return list(GIT_REPOS)
    (root / "git").mkdir(parents=True, exist_ok=True)
    for name, url in GIT_REPOS.items():
        target = _inside(root / "git" / f"{name}.git", root)
        if (target / "HEAD").is_file():
            continue
        part = _inside(root / "git" / f"{name}.git.part", root)
        shutil.rmtree(part, ignore_errors=True)
        job.set_progress(f"ソースを保管中: {name}", 0, 1)
        try:
            await _run(job, git, "clone", "--mirror", "--quiet", url, str(part), cwd=root, timeout=900)
            part.rename(target)
        except Exception as exc:  # noqa: BLE001 - 重みと実行環境の保管は続ける
            shutil.rmtree(part, ignore_errors=True)
            job.log(f"{name} を保管できませんでした: {exc}")
            failed.append(name)
    return failed


async def _archive_pypi(job: jobs.Job, root: Path, client: httpx.AsyncClient) -> list[str]:
    pypi = root / "pypi"
    pypi.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for name, (size, sha256, url) in PYPI_FILES.items():
        target = _inside(pypi / name, root)
        if _size_ok(target, size):
            continue
        try:
            await _download(client, url, target, size, sha256, root, lambda _count: None)
        except Exception as exc:  # noqa: BLE001
            job.log(f"{name} を保管できませんでした: {exc}")
            failed.append(name)
    # 依存の wheel（この PC の Python 向け）。揃っていれば取り直さない。
    present = {"-".join(p.name.split("-")[:2]).lower() for p in pypi.glob("*.whl")}
    if not all(spec.replace("==", "-").lower() in present for spec in RUNTIME_PACKAGES[1:]):
        job.set_progress("実行環境の wheel を保管中", 0, 1)
        try:
            await _run(job, sys.executable, "-m", "pip", "download", "--no-input", "--disable-pip-version-check",
                       "--no-deps", "--only-binary=:all:", "-d", str(pypi), *RUNTIME_PACKAGES[1:],
                       cwd=root, timeout=900)
        except Exception as exc:  # noqa: BLE001
            job.log(f"wheel を保管できませんでした: {exc}")
            failed.append("wheels")
    return failed


async def _archive_model(job: jobs.Job, root: Path, client: httpx.AsyncClient) -> None:
    model = model_dir()
    total = sum(size for size, _ in HF_FILES.values())
    done = 0

    def advance(count: int) -> None:
        nonlocal done
        done += count
        job.set_progress("音声モデル（重み）を保管中", done, total)

    for name, (size, sha256) in HF_FILES.items():
        target = _inside(model / name, root)
        if _size_ok(target, size):
            advance(size)
            continue
        url = f"https://huggingface.co/{HF_REPO}/resolve/{HF_REVISION}/{name}"
        await _download(client, url, target, size, sha256, root, advance)


def _write_manifest(root: Path) -> None:
    files = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative in ("MANIFEST.json", "README.md") or relative.endswith(".part") or "/objects/" in relative:
            continue
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    git_heads = {}
    for name in GIT_REPOS:
        packed = root / "git" / f"{name}.git"
        if (packed / "HEAD").is_file():
            git_heads[name] = {"source": GIT_REPOS[name], "path": f"git/{name}.git"}
    manifest = {
        "name": NAME,
        "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reason": "上流（Supertone）の開発・サポート終了。更新停止と削除に備えて保管（2026-11-19 利用者の依頼）",
        "license": LICENSE_NOTE,
        "huggingface": {"repo": HF_REPO, "revision": HF_REVISION, "path": model_dir().relative_to(root).as_posix()},
        "git": git_heads,
        "pypi": {"packages": list(RUNTIME_PACKAGES), "python": f"{sys.version_info.major}.{sys.version_info.minor}"},
        "files": files,
    }
    temp = root / "MANIFEST.json.tmp"
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, root / "MANIFEST.json")
    (root / "README.md").write_text(
        "# Supertonic 3 の保管庫\n\n"
        "Supertone の開発・サポート終了（2026）に備えて、ControlDeck が丸ごと保管したもの。\n\n"
        "- `git/*.git` — ソースの完全なミラー。`git clone git/supertonic.git` で取り出せる\n"
        f"- `{model_dir().relative_to(root).as_posix()}/` — 重みの全ファイル（{HF_REPO}@{HF_REVISION}）\n"
        "- `pypi/` — SDK と実行に要る wheel。`pip install --no-index --find-links pypi --no-deps ...` で入る\n"
        "- `MANIFEST.json` — 全ファイルのサイズと SHA-256\n\n"
        f"ライセンス: {LICENSE_NOTE}。再配布時は各 LICENSE に従うこと。\n",
        encoding="utf-8")


async def ensure_archive(job: jobs.Job) -> dict:
    """保管庫を揃える。揃っている部分は取り直さないので、上流が消えた後もオフラインで導入できる。"""
    global last_archive_error
    root = archive_root()
    timeout = httpx.Timeout(30.0, read=60.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": "ControlDeck"}) as client:
        await _archive_model(job, root, client)   # 重みが無ければ導入できないので、ここは失敗を上げる
        failed = await _archive_pypi(job, root, client)
    failed += await _archive_git(job, root)
    job.set_progress("保管庫の目録を作成中", 0, 1)
    await asyncio.to_thread(_write_manifest, root)
    last_archive_error = f"保管できなかったもの: {', '.join(failed)}" if failed else ""
    return archive_status()


async def install(job: jobs.Job) -> dict:
    """保管庫を揃え、そこから専用 venv と重みを使える状態にし、試しに 1 文話させて確かめる。"""
    global last_error
    root = archive_root()
    await ensure_archive(job)
    runtime = runtime_root()
    marker = runtime / "venv-ready"
    if not venv_ready():
        job.set_progress("TTS の実行環境を準備中", 0, 2)
        marker.unlink(missing_ok=True)
        await _run(job, sys.executable, "-m", "venv", "--clear", str(runtime / "venv"), cwd=runtime, timeout=180)
        base = (str(python_path()), "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "--no-deps")
        try:   # 保管庫から（上流が消えても入る）
            await _run(job, *base, "--no-index", "--find-links", str(root / "pypi"), *RUNTIME_PACKAGES,
                       cwd=runtime, timeout=900)
        except SupertonicError:
            job.log("保管庫の wheel で入らなかったため PyPI から入れます（Python の版違いなど）")
            await _run(job, *base, *RUNTIME_PACKAGES, cwd=runtime, timeout=900)
        marker.write_text(",".join(RUNTIME_PACKAGES) + "\n", encoding="utf-8")
    job.set_progress("試しに話させて確かめています", 1, 2)
    await synthesize("こんにちは。", voice="F1", lang="ja")
    last_error = ""
    job.set_progress("Supertonic 3 を利用できます", 2, 2)
    return status()


def uninstall() -> dict:
    """実行環境だけを消す。保管庫（ソース・重み・wheel）は残す。"""
    root = (data_dir() / "runtimes" / "tts-supertonic").resolve()
    if root.exists():
        shutil.rmtree(root)
    return status()


def start_install_job(owner_user_id: int | None) -> jobs.Job:
    global _install_job_id
    current = jobs.get(_install_job_id) if _install_job_id else None
    if current and current.status in ("queued", "running"):
        return current
    job = jobs.create("speech.install", "Supertonic 3（TTS）を導入", install, owner_user_id=owner_user_id, priority=-5)
    _install_job_id = job.id
    return job


async def synthesize(text: str, *, voice: str = "F1", lang: str = "ja", speed: float = 1.05, steps: int = 8) -> bytes:
    """テキストを 44.1 kHz・16 bit・1 ch の WAV にする。"""
    global last_error
    text = text.strip()
    if not text:
        raise ValueError("テキストが空です")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"テキストは {MAX_TEXT_CHARS} 文字以内にしてください")
    if voice not in VOICES or lang not in LANGUAGES or not 0.7 <= speed <= 2.0 or not 1 <= steps <= 32:
        raise ValueError("声・言語・速さ・ステップ数の指定が範囲外です")
    if not ready():
        raise SupertonicError("Supertonic 3 が未導入です")
    temp_root = (data_dir() / "tmp" / "tts").resolve()
    work = _inside(temp_root / uuid.uuid4().hex, temp_root)
    work.mkdir(parents=True, exist_ok=False)
    output = work / "speech.wav"
    worker = Path(__file__).with_name("supertonic_worker.py")
    request = json.dumps({"text": text, "voice": voice, "lang": lang, "speed": speed, "steps": steps,
                          "model_dir": str(model_dir()), "threads": THREADS, "output": str(output)}).encode("utf-8")
    try:
        async with _gate:
            process = await asyncio.create_subprocess_exec(
                str(python_path()), "-I", str(worker), cwd=str(work),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                _, stderr = await asyncio.wait_for(process.communicate(request), timeout=300)
            except TimeoutError:
                raise SupertonicError("音声合成が時間内に終わりませんでした")
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        if process.returncode != 0 or not output.is_file():
            detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
            raise SupertonicError((detail[-1] if detail else "Supertonic 3 が失敗しました")[:300])
        data = await asyncio.to_thread(output.read_bytes)
        last_error = ""
        return data
    except SupertonicError as exc:
        last_error = str(exc)[:300]
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
