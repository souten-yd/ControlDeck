"""画像の無い記事に、ニュース専用の軽い画像生成でイメージ画像を作る（CPU と RAM だけ・生成したらすぐアンロード）。

2026-11-23 利用者「ニュースから画像が取れない場合は本文の意図を汲み取って、AI で生成画像をする…時間がかかってもいいので、CPU と RAM の利用のみ
…画像を生成したらすぐにアンロード…連続で生成する必要がある場合は、一気に順次生成…品質は 2 の次…メディアフォージとかを使わずに、
ニュースフィード独自の生成 AI を保有してほしい。なるべく容量は軽い方がよくて、生成速度が重要」。

- 実行は子プロセス（機能「ニュースの画像生成（CPU）」の専用 venv の python で app/news/imagegen_worker.py）。1 回の取得のあと、画像の無い記事を
  まとめて渡し、モデルを 1 回だけ読み込んで順に生成し、終わったら子プロセスごと終える。スレッド数の上限・nice・件数の上限・時間の上限を持つ。
- 精度は 3 段（設定 → ニュース。小=SDXS・中=LCM-Dreamshaper・大=SDXL-Lightning。2026-11-23 利用者「もうちょい精度の高いモデル…このモデルを
  残しておいて、小中大みたいな感じで、選べるように、設定で」）。段の部品・ステップ数・解像度は imagegen_worker.TIER_SPECS。段ごとに導入・削除できる。
- プロンプトは LLM を使わない規則（英語の見出しはそのまま、日本語は語の対応表と種類の決まり文句）。費用も GPU も使わない。
- 画像は data/news/images/<記事の id>.webp。配る API はこの id（整数）だけでパスを組み、リンクは辿らない。
- 記事の image_url には配る API の URL を入れる。画面は「AI 生成のイメージ」の印を、この URL の形で判定する（列は足さない）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from sqlalchemy import select

from app.config import data_dir
from app.news import db as news_db
from app.news import imagegen_worker
from app.news.db import NewsItem

logger = logging.getLogger("control_deck.news.imagegen")

FEATURE_ID = "news-imagegen"
URL_PREFIX = "/api/v1/news/images/"
_URL_RE = re.compile(r"^/api/v1/news/images/\d{1,12}\.webp$")
TIMEOUT_SECONDS = 900          # 子プロセス全体の上限（小・中。段ごとの上限は imagegen_worker.TIER_SPECS の job_timeout。超えたら殺す。できた分は残る）
TIERS = imagegen_worker.TIERS
TIER_DEFAULT = "small"
NICE = "10"
THREADS_MIN, THREADS_MAX, THREADS_DEFAULT = 1, 16, 8
COUNT_MIN, COUNT_MAX, COUNT_DEFAULT = 1, 100, 30

_lock = threading.Lock()
_failed: set[int] = set()      # 作れなかった記事（再起動までは何度も試さない）

# 絵の言い回し（全段で同じ）。CLIP は「no ○○」を理解せず、書くとかえって描くので、否定は書かない。
# 人物・文字を避けるには、物・風景・抽象の図として描かせる言い回しを足す（2026-11-23 利用者の試し 16 枚: 人物の崩れ・題に無い人物・文字のような模様）。
STYLE = "concept illustration of objects and scenery, still life, clean minimal composition, soft studio lighting, smooth flat colors, wordless"
# ガイダンスのある段だけが使う否定のプロンプト。今の 3 段（SDXS・LCM・SDXL-Lightning）は蒸留でガイダンス無し、または w の埋め込みなので、台本は使わない。
NEGATIVE = "text, letters, logo, watermark, people, face"
TITLE_MAX = 120

# 日本語の語 → 英語の絵の語。先に当たったものを最大 3 つ使う。
KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("AI", "人工知能", "生成AI", "LLM", "機械学習"), "glowing artificial intelligence neural network"),
    (("半導体", "チップ", "GPU", "CPU"), "semiconductor chip circuit board close up"),
    (("宇宙", "ロケット", "衛星", "天文", "NASA"), "rocket and stars in outer space"),
    (("株", "為替", "円安", "円高", "金利", "日銀", "市場", "決算"), "stock market chart and city skyline"),
    (("経済", "景気", "物価", "インフレ", "賃上げ"), "economy graph with coins and city"),
    (("台風", "豪雨", "大雨", "天気", "気象", "猛暑", "雪"), "dramatic weather clouds over landscape"),
    (("地震", "津波", "震度", "噴火"), "mountain and sea after earthquake, rescue"),
    (("選挙", "首相", "政府", "国会", "政治", "大統領"), "government building and flags"),
    (("戦争", "ミサイル", "軍", "紛争", "ウクライナ", "ガザ"), "city skyline at dusk, smoke in distance"),
    (("医療", "病院", "ワクチン", "感染", "がん", "薬"), "hospital and medical research laboratory"),
    (("自動車", "EV", "電気自動車", "トヨタ", "テスラ"), "electric car charging at station"),
    (("野球", "サッカー", "五輪", "オリンピック", "試合", "優勝"), "stadium with cheering crowd"),
    (("セキュリティ", "ハッキング", "脆弱性", "不正アクセス", "ランサム"), "padlock and digital shield on dark background"),
    (("ロボット", "ドローン", "自動運転"), "friendly robot in modern city"),
    (("エネルギー", "原発", "太陽光", "電力", "再エネ", "脱炭素"), "solar panels and wind turbines"),
    (("環境", "気候", "温暖化", "森林"), "green forest and clear river"),
    (("アップデート", "リリース", "新機能", "バージョン"), "software update progress on abstract screen"),
    (("研究", "論文", "発見", "実験"), "scientist laboratory with microscope"),
)
KIND_PHRASE = {
    "release": "software release, abstract code and gears, blue tones",
    "news": "news broadcast concept, globe and city lights",
    "model": "artificial intelligence model, abstract neural network, purple tones",
    "forum": "community conversation, speech bubbles, warm colors",
    "reddit": "community conversation, speech bubbles, warm colors",
}
_ASCII_LETTER = re.compile(r"[A-Za-z]")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _mostly_english(text: str) -> bool:
    letters = len(_ASCII_LETTER.findall(text))
    return letters >= 12 and len(_NON_ASCII.findall(text)) <= max(2, len(text) // 10)


def build_prompt(title: str, summary: str = "", kind: str = "news", topics: list[str] | tuple[str, ...] = (), img: str = "") -> str:
    """見出しと抜粋から英語のプロンプトを作る。下ごしらえ（prep.py）の英語の描写 img があればそれを使う（2026-11-24 利用者の試し: 日本語の題が
    決まり文句に落ち、3 件が同じ地球儀の絵になった）。無ければ LLM を使わない規則: 見出しが英語ならそのまま、日本語なら語の対応表と種類の決まり文句。"""
    img = re.sub(r"\s+", " ", str(img or "")).strip()
    if img and img.isascii():
        return f"{img[:200]}, {STYLE}"
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    summary = re.sub(r"\s+", " ", str(summary or "")).strip()
    if _mostly_english(title):
        head = title[:TITLE_MAX]
        return f"{head}, {STYLE}"
    haystack = " ".join([title, summary[:400], *[str(t) for t in topics]])
    scenes: list[str] = []
    for words, scene in KEYWORDS:
        if any(w in haystack for w in words) and scene not in scenes:
            scenes.append(scene)
        if len(scenes) >= 3:
            break
    english_tail = " ".join(re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", haystack)[:4])
    parts = scenes or [KIND_PHRASE.get(kind, KIND_PHRASE["news"])]
    if english_tail and not scenes:
        parts.append(english_tail)
    return f"{', '.join(parts)}, news illustration, {STYLE}"


# ---- 置き場とパス ----

def images_dir() -> Path:
    return data_dir() / "news" / "images"


def image_path(item_id: object) -> Path | None:
    """記事の id（整数）だけからパスを組む。置き場の外・リンク・ファイルでないものは None。"""
    if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 0:
        return None
    root = images_dir().resolve()
    path = root / f"{item_id}.webp"
    if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
        return None
    return path


def url_for(item_id: int) -> str:
    return f"{URL_PREFIX}{int(item_id)}.webp"


def is_generated_url(value: object) -> bool:
    return isinstance(value, str) and bool(_URL_RE.match(value))


def delete_images(item_ids: list[int]) -> int:
    """記事を消すとき、その画像も消す。消した枚数（リンクは辿らず、置き場の直下の <id>.webp だけ）。"""
    root = images_dir()
    removed = 0
    for item_id in item_ids:
        if isinstance(item_id, bool) or not isinstance(item_id, int) or item_id < 0:
            continue
        path = root / f"{item_id}.webp"
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
                removed += 1
        except OSError:
            logger.warning("生成画像を消せませんでした: %s", path.name)
        _failed.discard(item_id)
    return removed


# ---- 機能（専用 venv・モデル）の有無 ----

def feature_python() -> Path:
    return data_dir() / "features" / FEATURE_ID / "venv" / "bin" / "python"


def models_dir() -> Path:
    return data_dir() / "models" / FEATURE_ID


def normalize_tier(value: object) -> str:
    """設定の「モデルの精度」。知らない値は既定（小）。"""
    return value if isinstance(value, str) and value in TIERS else TIER_DEFAULT


def models_ready(root: Path | None = None, tier: str = TIER_DEFAULT) -> bool:
    base = root or models_dir()
    return all((base / rel).is_file() for rel in imagegen_worker.TIER_FILES[normalize_tier(tier)])


def available(tier: str = TIER_DEFAULT) -> bool:
    """機能（専用 venv）とその段のモデルが揃っているか（軽い確認だけ。子プロセスは起こさない）。"""
    return feature_python().is_file() and models_ready(tier=tier)


def tiers_info() -> list[dict]:
    """段ごとの案内（画面用）。導入済みか・1 枚の目安の時間・取得の大きさ・RAM の目安。"""
    out = []
    for tier in TIERS:
        spec = imagegen_worker.TIER_SPECS[tier]
        out.append({"id": tier, "label": spec["label"], "installed": models_ready(tier=tier), "megabytes": spec["megabytes"],
                    "time_seconds": spec["time_seconds"], "ram_gb": spec["ram_gb"], "note": spec["note"],
                    "max_per_run": spec["max_per_run"]})
    return out


def remove_models(tier: str | None = None) -> None:
    """段の部品を消す（段を指定しなければ全部）。各段の部品は他の段と共有しない。"""
    base = models_dir().resolve()
    if base.parent != (data_dir() / "models").resolve():
        return
    if tier is None:
        if base.exists():
            shutil.rmtree(base)
        return
    for name in imagegen_worker.TIER_SPECS[normalize_tier(tier)]["models"]:
        target = (base / name)
        if target.is_dir() and not target.is_symlink() and target.resolve().parent == base:
            shutil.rmtree(target)


# ---- 子プロセス ----

def _run_worker(job: dict, timeout: float | None = None) -> list[dict]:
    """専用 venv の python で台本を動かし、1 行 1 件の結果を返す。時間を超えたら殺す（できた分の行だけ返る）。"""
    timeout = timeout if timeout is not None else _job_timeout(job.get("tier"))
    cmd = ["nice", "-n", NICE, str(feature_python()), "-I", str(Path(imagegen_worker.__file__).resolve()), "generate"]
    threads = str(job["threads"])
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OMP_NUM_THREADS": threads, "HF_HUB_OFFLINE": "1", "HOME": str(data_dir())}
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        out, err = proc.communicate(json.dumps(job, ensure_ascii=False))
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    rows: list[dict] = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), int):
            rows.append(row)
    if proc.returncode != 0:
        logger.warning("ニュースの画像生成の子プロセスが異常終了しました (%s): %s", proc.returncode, (err or "")[-200:])
    return rows


def _job_timeout(tier: object) -> int:
    return int(imagegen_worker.TIER_SPECS[normalize_tier(tier)]["job_timeout"])


def pending(limit: int) -> list[dict]:
    """画像の無い記事（新しい順）。event・作れなかった記事は除く。"""
    with news_db.session() as db:
        rows = db.scalars(select(NewsItem).where(NewsItem.image_url.is_(None), NewsItem.kind != "event")
                          .order_by(NewsItem.published_at.desc(), NewsItem.id.desc()).limit(limit + len(_failed))).all()
        picked = [r for r in rows if r.id not in _failed][:limit]
        out = []
        for r in picked:
            try:
                topics = [t for t in json.loads(r.topics or "[]") if isinstance(t, str)]
            except ValueError:
                topics = []
            out.append({"id": r.id, "title": r.title, "summary": r.summary or "", "kind": r.kind, "topics": topics, "img": r.img_prompt or ""})
        return out


def generate_pending(cfg: dict, *, runner=None) -> dict:
    """設定が入で機能が入っていれば、画像の無い記事（上限まで）をまとめて生成して image_url を付ける。
    {"done": 成功, "failed": 失敗, "skipped": 理由（空なら実行した）}。runner は試験用（_run_worker の代わり）。"""
    conf = cfg.get("imagegen", {})
    if not conf.get("enabled"):
        return {"done": 0, "failed": 0, "skipped": "disabled"}
    tier = normalize_tier(conf.get("tier"))
    if not available(tier):
        return {"done": 0, "failed": 0, "skipped": "not_installed", "tier": tier}
    if not _lock.acquire(blocking=False):
        return {"done": 0, "failed": 0, "skipped": "running"}
    try:
        # 1 回の件数は、設定の上限と、段ごとの上限（大は少なめ）の小さい方
        limit = max(COUNT_MIN, min(int(conf.get("max_per_run", COUNT_DEFAULT)), COUNT_MAX, imagegen_worker.TIER_SPECS[tier]["max_per_run"]))
        threads = max(THREADS_MIN, min(int(conf.get("threads", THREADS_DEFAULT)), THREADS_MAX))
        items = pending(limit)
        if not items:
            return {"done": 0, "failed": 0, "skipped": "nothing"}
        job = {"tier": tier, "models_dir": str(models_dir()), "out_dir": str(images_dir()), "threads": threads,
               "deadline_seconds": _job_timeout(tier) - 60,
               "items": [{"id": i["id"], "prompt": build_prompt(i["title"], i["summary"], i["kind"], i["topics"], i.get("img", "")), "seed": i["id"]} for i in items]}
        started = time.monotonic()
        rows = (runner or _run_worker)(job)
        ok_ids = [r["id"] for r in rows if r.get("ok") is True and image_path(r["id"]) is not None]
        asked = {i["id"] for i in items}
        ok_ids = [i for i in ok_ids if i in asked]
        _failed.update(asked - set(ok_ids))
        if ok_ids:
            with news_db.session() as db:
                for row in db.scalars(select(NewsItem).where(NewsItem.id.in_(ok_ids), NewsItem.image_url.is_(None))):
                    row.image_url = url_for(row.id)
                db.commit()
        logger.info("ニュースの画像を生成しました: %s / %s 件・%.1f 秒", len(ok_ids), len(items), time.monotonic() - started)
        return {"done": len(ok_ids), "failed": len(items) - len(ok_ids), "skipped": "", "tier": tier}
    finally:
        _lock.release()
