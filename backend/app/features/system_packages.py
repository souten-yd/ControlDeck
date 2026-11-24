"""OS のパッケージ（apt）に頼る道具の、機能と拡張の項目（種類 `system-packages`）。

apt の導入は sudo が要り、ControlDeck（一般の利用者で動く）からは入れられない。ボタンで sudo を実行しない。
入っているかを読むだけ（`shutil.which` とファイルの有無）にして、足りないパッケージだけの
`sudo apt install …` を画面に出し、利用者がコピーして自分で実行する（2026-11-23 利用者「追加で入れないといけない
ものについては機能と拡張に導入ボタンを付けること」。apt は仕組み上ボタンで入れられないので、コピーできるコマンドにした）。
deck.sh の `ensure_apt_packages`（パスワード不要 sudo のときだけ自動導入）の一覧と揃える。

gh（GitHub の CLI）も案内だけにした。GitHub の Release の tar を管理領域へ展開する形は、版の固定・署名/ハッシュの検証・
`gh auth login`（利用者の GitHub アカウントのログイン）まで要り、apt の `gh` を使う deck.sh と二重になる。小さい方（案内）を選んだ。
"""
from __future__ import annotations

from pathlib import Path

import glob
import shutil

TESSDATA_GLOBS = ("/usr/share/tesseract-ocr/*/tessdata/jpn.traineddata", "/usr/share/tessdata/jpn.traineddata")

FEATURES: dict[str, dict] = {
    "system-ocr": {
        "name": "PDF・画像の文字の読み取り（poppler・tesseract）",
        "kind": "system-packages",
        "category": "search",
        "route_gated": False,
        "packages": {
            "poppler-utils": {"bins": ["pdfinfo", "pdftotext", "pdftoppm"]},
            "tesseract-ocr": {"bins": ["tesseract"]},
            "tesseract-ocr-jpn": {"globs": list(TESSDATA_GLOBS)},
        },
        "summary": "PDF の本文・ページ画像の取り出しと、画像の OCR（日本語＋英語）。知識の取り込み・Workflow の OCR ノードで使う。"
                   "OS のパッケージ（sudo が要る）なので、表示されたコマンドをコピーして自分で実行する",
    },
    "system-zstd": {
        "name": "zstd（GPU ランタイムの展開）",
        "kind": "system-packages",
        "category": "tools",
        "route_gated": False,
        "packages": {"zstd": {"bins": ["zstd"]}},
        "summary": "GPU ランタイム（.zst のアーカイブ）の展開で使う。OS のパッケージ（sudo が要る）なので、表示されたコマンドをコピーして自分で実行する",
    },
    "system-gh": {
        "name": "gh（GitHub の CLI）",
        "kind": "system-packages",
        "category": "tools",
        "route_gated": False,
        "packages": {"gh": {"bins": ["gh"]}},
        "summary": "開発の作業場の取り込みで Pull Request を作る。導入後に `gh auth login` でログインも要る。"
                   "OS のパッケージ（sudo が要る）なので、表示されたコマンドをコピーして自分で実行する",
    },
    "system-nct6775": {
        "name": "マザーボードのファン（Nuvoton のドライバ）",
        "kind": "system-packages",
        "category": "tools",
        "route_gated": False,
        "packages": {},
        "module": "nct6775",
        "summary": "CPU ファンの回転数を Home に出す。Nuvoton の Super I/O のドライバを読み込むと /sys/class/hwmon に出る。"
                   "ASRock の AM5 の板（NCT6686D/6687D）は nct6683 を force=1 で、ほかの板（NCT67xx）は nct6775。"
                   "読み込みは sudo が要るので、表示されたコマンドをコピーして自分で実行する（再起動後も読み込む設定を含む）",
    },
}


def _module_loaded(name: str) -> bool:
    """hwmon にそのドライバのチップが出ているか（読み込み済みの判定）。"""
    from app.monitoring import cpu_fan

    return bool(cpu_fan._super_io_directories())


def _has(probe: dict) -> bool:
    if not all(shutil.which(name) for name in probe.get("bins", [])):
        return False
    patterns = probe.get("globs", [])
    return not patterns or any(glob.glob(pattern) for pattern in patterns)


def missing_packages(feature_id: str) -> list[str]:
    module = FEATURES[feature_id].get("module")
    if module:
        return [] if _module_loaded(module) else [module]
    return [package for package, probe in FEATURES[feature_id]["packages"].items() if not _has(probe)]


def fan_driver_command(board_vendor: str | None = None) -> str:
    """板に合うファンのドライバの読み込みと、再起動後も読み込む設定のコマンド。
    ASRock の AM5 の板は NCT6686D/6687D で、nct6775 は「No such device」になる。nct6683 を force=1 で読む
    （2026-11-24 本番の ASRock X870 Taichi Creator で確認: nct6683 が NCT6686D を見つけ、fan1〜fan7 が出た）。"""
    if board_vendor is None:
        try:
            board_vendor = Path("/sys/class/dmi/id/board_vendor").read_text(encoding="utf-8").strip()
        except OSError:
            board_vendor = ""
    if "asrock" in board_vendor.lower():
        return ("sudo modprobe nct6683 force=1 && echo nct6683 | sudo tee /etc/modules-load.d/nct6683.conf"
                " && echo 'options nct6683 force=1' | sudo tee /etc/modprobe.d/nct6683.conf")
    return "sudo modprobe nct6775 && echo nct6775 | sudo tee /etc/modules-load.d/nct6775.conf"


def install_command(feature_id: str) -> str:
    """足りないパッケージだけの導入コマンド。揃っていれば空。"""
    missing = missing_packages(feature_id)
    module = FEATURES[feature_id].get("module")
    if module:
        return fan_driver_command() if missing else ""
    return f"sudo apt install -y {' '.join(missing)}" if missing else ""


def guide(feature_id: str) -> str:
    """道具が無いときのエラーに添える案内（機能と拡張の項目と、コピーできるコマンド）。"""
    if FEATURES[feature_id].get("module"):
        return f"設定 → 機能と拡張 → {FEATURES[feature_id]['name']}（{install_command(feature_id)}）"
    command = install_command(feature_id) or f"sudo apt install -y {' '.join(FEATURES[feature_id]['packages'])}"
    return f"設定 → 機能と拡張 → {FEATURES[feature_id]['name']} から導入してください（{command}）"
