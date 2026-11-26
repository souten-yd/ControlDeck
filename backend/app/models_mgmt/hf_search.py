"""Hugging Face のモデル検索（AI アシスタントの道具 huggingface_models の本体）。

2026-11-22 利用者「アシスタントのチャットで『Qwen の最新のモデルで Strata に対応しているモデルを抽出して特徴とともに教えて』
のような検索をしたとき、Hugging Face 内を検索して教えてくれる機能が欲しい」「制限によって検索が不十分になったり、かなり制限された
範囲でしか検索できないことはあまり無いように。検索の幅・十分な件数を担保して」。

検索の幅の担保（1 回の検索語で取りこぼさないため、次を合わせて重複を除く）:
- 語の揺れ: "Qwen3.8" → "Qwen 3.8"・"Qwen-3.8"・"Qwen3_8"・"Qwen38" など（Hugging Face の search は id の部分一致）
- GGUF の別作者（unsloth・ISTA-DASLab・bartowski など）で同じ語を再検索
- 見つけたモデルのベースモデルから派生（filter=base_model:…）を再検索
- どの検索もページ（Link ヘッダの cursor）をたどり、必要な件数が集まるまで取る

Strata 対応の判定は strata_gateway.check_runnable と同じ決まり（ファイル名からの判定。量子化は起動時にしか確かめられないので、
最終確認は起動時）。HTTP は httpx のみ。秘密の会話では通信の直前で止める（app.security.offline）。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime

import httpx

from app.models_mgmt.hf import HF_API, HfError, _headers

logger = logging.getLogger("control_deck.models_mgmt.hf_search")

CACHE_SECONDS = 600
DEFAULT_LIMIT, MAX_LIMIT = 50, 200
PAGE_SIZE = 100
MAX_PAGES_PER_QUERY = 12          # 1 つの検索で最大 1200 件（limit に届けば手前で止める）
MAX_VARIANTS = 6
MAX_BASE_MODELS = 40
TREE_CONCURRENCY = 8
# GGUF を出している主な作者（語の再検索に使う）。ここに無い作者はベースモデルからの派生と最初の検索で拾う
GGUF_AUTHORS = ("unsloth", "ISTA-DASLab", "bartowski", "lmstudio-community", "mradermacher", "ggml-org")
SORTS = {"updated": "lastModified", "downloads": "downloads", "likes": "likes"}
RUNTIMES = ("any", "strata", "llama.cpp")
_EXPAND = ("gguf", "safetensors", "cardData", "siblings", "pipeline_tag", "tags", "downloads", "likes",
           "lastModified", "createdAt", "config", "gated")
_VISION_TAGS = {"image-text-to-text", "image-to-text", "visual-question-answering", "video-text-to-text",
                "any-to-any", "image-text-to-image"}

# --- Strata 対応の判定（strata_gateway.check_runnable の決まりと一致させる）---
# Unsloth の UD-*（UD-IQ1_M〜UD-Q6_K_XL）。UD-Q8_K_XL は決まりの外
_UD_QUANT = re.compile(r"(?<![A-Za-z0-9])UD-((?:IQ[1-4]_[A-Z]+)|(?:Q[2-6]_K_XL))(?![A-Za-z0-9])", re.I)
_Q8_0 = re.compile(r"(?<![A-Za-z0-9])Q8_0(?![A-Za-z0-9])", re.I)
_GSQ_QUANT = re.compile(r"(?<![A-Za-z0-9])(Q2_0|IQ2_XS|IQ3_XXS|IQ3_S)(?![A-Za-z0-9])", re.I)
_QUANT = re.compile(r"(?<![A-Za-z0-9])((?:UD-)?(?:IQ[1-4]_[A-Z]+|Q[2-8]_K(?:_[A-Z]+)?|Q[2-8]_[01]|BF16|F16|F32))(?![A-Za-z0-9])", re.I)
_SHARD = re.compile(r"-\d{5}-of-\d{5}", re.I)
STRATA_NOTE = ("ファイル名・作者からの判定です。量子化の中身は Strata の起動時にしか確かめられないため、最終確認は起動時です。"
               "Strata が動かすのは Unsloth の UD-*・ISTA-DASLab の GSQ-RCO（Q2_0・IQ2_XS・IQ3_XXS・IQ3_S）・Q8_0 だけです。")

_cache: dict[tuple, tuple[float, dict]] = {}
_tree_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}


def clear_cache() -> None:
    _cache.clear()
    _tree_cache.clear()


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def query_variants(query: str) -> list[str]:
    """語の揺れ。"Qwen3.8" → Qwen3.8 / Qwen 3.8 / Qwen-3.8 / Qwen3_8 / Qwen38 / qwen3.8（大小は Hugging Face が区別しない）。"""
    base = re.sub(r"\s+", " ", query.strip())
    if not base:
        return []
    spaced = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", base)       # 英字と数字の境目に空白
    squeezed = re.sub(r"[\s_\-]+", "", base)
    candidates = [base, spaced, re.sub(r"[ _]", "-", spaced), base.replace(".", "_"), re.sub(r"[ ]", "-", base),
                  base.replace(" ", "_"), squeezed, squeezed.replace(".", "")]
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = item.casefold()
        # 空白を含む語は id（空白なし）に当たらない。ハイフン・下線の形だけを検索に使う
        if item and key not in seen and " " not in item:
            seen.add(key)
            out.append(item)
    return out[:MAX_VARIANTS]


def classify_gguf(repo: str, files: list[dict]) -> dict:
    """GGUF のファイル一覧から、量子化・Strata 対応・llama.cpp 候補を判定する。files = [{name, size}]。"""
    author = repo.split("/", 1)[0].casefold()
    quants: dict[str, dict] = {}
    basis: list[dict] = []
    for item in files:
        base = str(item["name"]).rsplit("/", 1)[-1]
        if not base.lower().endswith(".gguf") or base.lower().startswith("mmproj"):
            continue
        stem = _SHARD.sub("", base[:-5])
        found = _QUANT.search(stem)
        label = found.group(1).upper() if found else stem
        row = quants.setdefault(label, {"quant": label, "size": 0, "files": 0})
        row["size"] += int(item.get("size") or 0)
        row["files"] += 1
        kind = ""
        if _UD_QUANT.search(stem) and author == "unsloth":
            kind = "unsloth-ud"
        elif _GSQ_QUANT.search(stem) and author == "ista-daslab":
            kind = "gsq-rco"
        elif _Q8_0.search(stem) and not _UD_QUANT.search(stem):
            kind = "q8_0"
        if kind and not any(b["quant"] == label for b in basis):
            basis.append({"quant": label, "kind": kind, "file": base})
    if not quants:
        return {"gguf": False, "quants": [], "strata": False, "strata_basis": [], "strata_family": "",
                "strata_registerable": [], "llama_cpp": False}
    if basis:
        kinds = {b["kind"] for b in basis}
        family = "qwen" if "gsq-rco" in kinds and "deepseek" not in repo.casefold() else (
            "deepseek41" if "gsq-rco" in kinds else "unsloth")
        if "inkling" in repo.casefold():
            family = "inkling-2-mini"      # Strata 0.2.22 の strata-inkling（SQ-* と Unsloth の UD-* を読む）
    else:
        family = ""
    registerable: list[str] = []
    if family:
        from app.models_mgmt import strata_catalog

        allowed = strata_catalog.quant_ids(family)
        registerable = [b["quant"] for b in basis if b["quant"].casefold() in allowed]
    return {"gguf": True, "quants": sorted(quants.values(), key=lambda q: q["quant"]), "strata": bool(basis),
            "strata_basis": basis, "strata_family": family, "strata_registerable": registerable, "llama_cpp": True}


_DERIV_NAME = re.compile(r"uncensor|abliterat|heretic|merge|finetune|fine-tune|distill|(?<![a-z])dpo(?![a-z])|roleplay|"
                         r"(?<![a-z])rp(?![a-z])|jailbreak|lora|unslop|aggressive|hauhau|erotic|nsfw", re.I)
_TEXT_PIPELINES = {"text-generation", "image-text-to-text", ""}
_NON_TEXT_QUERY = re.compile(r"tts|asr|speech|whisper|embed|rerank|audio|image|video|vision|ocr|diffus", re.I)
_QUANT_SUFFIX = re.compile(r"-(FP8|AWQ|GPTQ.*|Int[48].*|NVFP4|MLX.*|bnb.*|FP4|W4A16.*|W8A8.*)$", re.I)
TRUST_ORDER = (("unsloth", "ista-daslab"), ("bartowski", "lmstudio-community", "ggml-org"))
MAX_PLACEMENTS, MAX_PLACEMENT_CANDIDATES, DERIVATIVES_SHOWN = 3, 6, 5

_PARAMS = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*([BMbm])(?![A-Za-z])")
_ACTIVE = re.compile(r"(?<![A-Za-z0-9])A(\d+(?:\.\d+)?)B(?![A-Za-z])")


def estimate_params(name: str) -> float | None:
    """名前から推定したパラメータ数（十億）。35B-A3B のような MoE は全体の方。"""
    found = [(float(v) / (1000 if u.casefold() == "m" else 1)) for v, u in _PARAMS.findall(name)]
    return max(found) if found else None


def _context_length(raw: dict) -> int | None:
    gguf = raw.get("gguf") or {}
    if gguf.get("context_length"):
        return int(gguf["context_length"])
    config = raw.get("config") or {}
    for holder in (config, config.get("text_config") or {}):
        value = holder.get("max_position_embeddings")
        if isinstance(value, int) and value > 0:
            return value
    return None


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def summarize(raw: dict, *, via: str = "search") -> dict:
    """API の 1 件を、道具が返す特徴の形にそろえる。"""
    repo = str(raw.get("id") or raw.get("modelId") or "")
    card = raw.get("cardData") or {}
    tags = [str(t) for t in (raw.get("tags") or [])]
    pipeline = str(raw.get("pipeline_tag") or card.get("pipeline_tag") or "")
    license_ = str(card.get("license") or next((t[8:] for t in tags if t.startswith("license:")), ""))
    base = _first(card.get("base_model")) or next((t.split(":")[-1] for t in tags if t.startswith("base_model:")
                                                    and t.count(":") == 1), "")
    safetensors = (raw.get("safetensors") or {}).get("total")
    gguf_total = (raw.get("gguf") or {}).get("total")
    if safetensors:
        params, source = round(safetensors / 1e9, 2), "safetensors"
    elif gguf_total:
        params, source = round(gguf_total / 1e9, 2), "gguf"
    else:
        params, source = estimate_params(repo.split("/", 1)[-1]), "名前から推定"
    siblings = [{"name": s.get("rfilename", ""), "size": 0} for s in (raw.get("siblings") or [])]
    gguf = classify_gguf(repo, [f for f in siblings if f["name"].lower().endswith(".gguf")])
    vision = pipeline in _VISION_TAGS or any(t in _VISION_TAGS for t in tags) or any(
        s["name"].lower().startswith("mmproj") or "mmproj" in s["name"].lower() for s in siblings)
    relation = next((t.split(":")[1] for t in tags if t.startswith("base_model:") and t.count(":") == 2), "")
    return {
        "relation": relation, "derivative_name": bool(_DERIV_NAME.search(repo.split("/", 1)[-1])),
        "id": repo, "url": f"https://huggingface.co/{repo}", "author": repo.split("/", 1)[0],
        "updated": str(raw.get("lastModified") or "")[:10], "created": str(raw.get("createdAt") or "")[:10],
        "downloads": int(raw.get("downloads") or 0), "likes": int(raw.get("likes") or 0),
        "pipeline": pipeline, "license": license_, "base_model": base, "gated": bool(raw.get("gated")),
        "params_b": params, "params_source": source, "context_length": _context_length(raw),
        "vision": vision, "via": via, **gguf,
        "files_known": bool(siblings), "registered": False, "registered_as": [],
    }


# --- 通信 ---

async def _get(client: httpx.AsyncClient, url: str, params=None) -> tuple[object, str]:
    """GET して (JSON, 次のページの URL) を返す。秘密の会話では通信しない。429 は待って 2 回まで再試行。"""
    from app.security import offline

    for attempt in range(3):
        offline.ensure_external_allowed("Hugging Face のモデル検索")
        response = await client.get(url, params=params)
        if response.status_code == 429 and attempt < 2:
            try:
                wait = float(response.headers.get("retry-after") or 3)
            except ValueError:
                wait = 3.0
            await asyncio.sleep(min(max(wait, 1.0), 20.0))
            continue
        if response.status_code >= 400:
            raise HfError(f"Hugging Face の検索エラー ({response.status_code})")
        nxt = (response.links.get("next") or {}).get("url", "")
        return response.json(), nxt
    raise HfError("Hugging Face の検索が混み合っています（429）")


async def _list_query(client, *, params: list[tuple[str, str]], want: int) -> list[dict]:
    """1 つの検索を、want 件に届くか最後のページまでたどる。"""
    query = [*params, *[("expand[]", e) for e in _EXPAND], ("limit", str(min(PAGE_SIZE, max(want, 20))))]
    out: list[dict] = []
    url: str = f"{HF_API}/models"
    first = True
    for _ in range(MAX_PAGES_PER_QUERY):
        data, nxt = await _get(client, url, query if first else None)
        first = False
        out.extend(x for x in data if isinstance(x, dict)) if isinstance(data, list) else None
        if len(out) >= want or not nxt:
            break
        url = nxt
    return out


async def trending_gguf(limit: int = 20) -> list[dict]:
    """話題の GGUF（trendingScore の高い順）。ニュースの専門ページ（app/news）が AI モデルの新着として使う。
    1 行は summarize と同じ形（id・url・created・downloads・likes・pipeline・params_b …）。"""
    async with httpx.AsyncClient(timeout=30, headers=_headers(), follow_redirects=True) as client:
        rows = await _list_query(client, params=[("filter", "gguf"), ("sort", "trendingScore"), ("direction", "-1")], want=limit)
    return [summarize(row, via="trending") for row in rows[:limit]]


async def _tree(client, repo: str) -> list[dict]:
    """repo の GGUF ファイルと大きさ（分割は 1 本ずつ）。"""
    key = (repo, "main")
    hit = _tree_cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    files: list[dict] = []
    url, params = f"{HF_API}/models/{repo}/tree/main", {"recursive": "1"}
    for _ in range(20):
        data, nxt = await _get(client, url, params)
        files.extend({"name": str(e.get("path") or ""), "size": int(e.get("size") or 0)}
                     for e in data if isinstance(e, dict) and e.get("type") == "file"
                     and str(e.get("path") or "").lower().endswith(".gguf"))
        if not nxt:
            break
        url, params = nxt, None
    _tree_cache[key] = (time.monotonic(), files)
    return files


def _registered_index() -> list[tuple[str, str]]:
    """登録済みモデル（Strata・llama.cpp）の (別名, model_path)。読めなければ空（印が付かないだけ）。"""
    rows: list[tuple[str, str]] = []
    try:
        from app.models_mgmt import strata_gateway

        rows += [(alias, str(cfg.get("model_path") or "")) for alias, cfg in strata_gateway._read().items()]
    except Exception:  # noqa: BLE001 - 印は補助
        logger.debug("Strata の登録を読めませんでした", exc_info=True)
    try:
        from app.models_mgmt import llama

        rows += [(alias, str(cfg.get("model_path") or "")) for alias, cfg in llama.get_config().get("instances", {}).items()]
    except Exception:  # noqa: BLE001
        logger.debug("llama.cpp の登録を読めませんでした", exc_info=True)
    return [(a, p) for a, p in rows if p]


def mark_registered(items: list[dict], registered: list[tuple[str, str]] | None = None) -> None:
    """登録済みのモデルと同じものに印を付ける。ダウンロード先は <owner>--<name>/<file>（hf.download）。"""
    registered = _registered_index() if registered is None else registered
    for item in items:
        needle = item["id"].replace("/", "--").casefold()
        item["registered_as"] = sorted({alias for alias, path in registered if needle in path.casefold()})
        item["registered"] = bool(item["registered_as"])


# --- 検索 ---
# 答えの単位は「ベースモデル」。公式の作者のモデルを新しい順に並べ、それぞれに Strata で動かせる GGUF の置き場（最大 3）を付ける。
# 個人の派生（finetune・merge・uncensored など）も含めるが、公式の行の後ろに「派生」の印つきで続ける（include_derivatives 既定 true）。

def trust_rank(repo: str, official: str) -> int:
    author = repo.split("/", 1)[0].casefold()
    if official and author == official.casefold():
        return 0
    for rank, group in enumerate(TRUST_ORDER, start=1):
        if author in group:
            return rank
    return len(TRUST_ORDER) + 1


def _sort_value(item: dict, sort: str):
    if sort == "downloads":
        return (item["downloads"], item["updated"])
    if sort == "likes":
        return (item["likes"], item["updated"])
    return (item["created"] or item["updated"], item["updated"])      # 新しさ: 作成日、次に更新日


def detect_official(items: list[dict], query: str, author: str) -> str:
    """語に合う公式の作者（Qwen → Qwen、DeepSeek → deepseek-ai）。無ければ最もダウンロードの多い作者。"""
    if author:
        return author
    match = re.match(r"[A-Za-z]+", query.strip())
    token = _normalize(match.group(0)) if match else ""
    totals: dict[str, int] = {}
    for item in items:
        totals[item["author"]] = totals.get(item["author"], 0) + item["downloads"]
    if token:
        exact = [a for a in totals if _normalize(a) == token]
        if exact:
            return exact[0]
        near = [a for a in totals if _normalize(a).startswith(token)]
        if near:
            return max(near, key=lambda a: totals[a])
    return max(totals, key=lambda a: totals[a]) if totals else ""


def _pipeline_ok(item: dict, pipeline: str) -> bool:
    if pipeline == "any":
        return True
    if pipeline == "text":
        if item["pipeline"] == "":
            return not _NON_TEXT_QUERY.search(item["id"].split("/", 1)[-1])   # 種類の記載が無いものは名前で TTS・画像などを除く
        return item["pipeline"] in _TEXT_PIPELINES
    return item["pipeline"] == pipeline


async def search(query: str, *, author: str = "", sort: str = "updated", limit: int = DEFAULT_LIMIT,
                 runtime: str = "any", pipeline: str = "", include_derivatives: bool = True) -> dict:
    """検索の本体。{"items": [行...], "counts", "queries", "warnings", ...} を返す。10 分キャッシュ。
    行 = ベースモデル（または派生）+ placements（GGUF の置き場、最大 3）。"""
    query, author = query.strip(), author.strip()
    if sort not in SORTS:
        raise HfError(f"並べ方は {', '.join(SORTS)} のどれかです")
    if runtime not in RUNTIMES:
        raise HfError(f"runtime は {', '.join(RUNTIMES)} のどれかです")
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    if not query and not author:
        raise HfError("検索語（query）か作者（author）が必要です")
    pipeline = pipeline.strip() or ("any" if _NON_TEXT_QUERY.search(query) else "text")   # TTS・ASR などは求められたときだけ
    key = (query.casefold(), author.casefold(), sort, limit, runtime, pipeline, include_derivatives)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        result = dict(hit[1], cached=True)
        mark_registered(_all_repos(result["items"]))
        return result

    want = min(max(limit * 2, 100), 600)
    variants = query_variants(query) or [""]
    base_params = [("sort", SORTS[sort]), ("direction", "-1")]
    raw_by_id: dict[str, tuple[dict, str]] = {}
    warnings: list[str] = []
    queries: list[dict] = []

    async with httpx.AsyncClient(timeout=30, headers=_headers(), follow_redirects=True) as client:
        sem = asyncio.Semaphore(4)

        async def run(label: str, extra: list[tuple[str, str]], *, by_downloads: bool = False, size: int = 0) -> None:
            params = [("sort", "downloads"), ("direction", "-1")] if by_downloads else base_params
            async with sem:
                try:
                    rows = await _list_query(client, params=[*params, *extra], want=size or want)
                except HfError as exc:
                    if "秘密の会話" in str(exc):
                        raise
                    warnings.append(f"{label}: {exc}")
                    return
                except httpx.HTTPError as exc:
                    warnings.append(f"{label}: 通信に失敗しました（{type(exc).__name__}）")
                    return
            fresh = 0
            for row in rows:
                if row.get("id") and row["id"] not in raw_by_id:
                    raw_by_id[row["id"]] = (row, label)
                    fresh += 1
            queries.append({"label": label, "returned": len(rows), "new": fresh})

        def searches(label: str, who: str = "", gguf: bool = False) -> list:
            out = []
            for variant in variants:
                extra = ([("search", variant)] if variant else []) + ([("author", who)] if who else []) + (
                    [("filter", "gguf")] if gguf else [])
                out.append(run(f"{label} {variant}".strip(), extra))
            return out

        # 1 段目: 語そのもの + GGUF の別作者。公式の作者がまだ分からない
        first = searches("検索語", author)
        if not author and query:
            first += [run(f"GGUF 作者 {a}: {v}", [("search", v), ("author", a), ("filter", "gguf")])
                      for a in GGUF_AUTHORS for v in variants[:3]]
        if query and not author:
            # 公式の作者を見つけるための探索: 語で最もダウンロードの多いモデル（公式の組織が上に来る）と、語そのものの名前の組織
            token = (re.match(r"[A-Za-z]+", query) or [""])[0]
            first.append(run("ダウンロード順", [("search", variants[0])], by_downloads=True, size=100))
            first += [run(f"組織 {name}", [("author", name)], size=100)
                      for name in dict.fromkeys([token, token.capitalize(), token.upper()]) if name]
        await asyncio.gather(*first)
        if not raw_by_id and warnings:
            raise HfError("Hugging Face を検索できませんでした: " + "; ".join(warnings[:3]))

        # 2 段目: 公式の作者の全モデル（語の揺れごと）。ここで新しい公式モデルを取りこぼさない
        pre = [summarize(row, via=label) for row, label in raw_by_id.values()]
        official = detect_official(pre, query, author)
        if official:
            await asyncio.gather(*searches("公式", official))
        items = [summarize(row, via=label) for row, label in raw_by_id.values()]
        total_found = len(items)
        by_id = {i["id"]: i for i in items}
        is_official = lambda i: official and i["author"].casefold() == official.casefold()   # noqa: E731
        heads = [i for i in items if is_official(i) and not i["relation"] and _pipeline_ok(i, pipeline)
                 and not _QUANT_SUFFIX.search(i["id"]) and not (i["gguf"] and i["id"].lower().endswith("-gguf")
                                                                 and i["id"][:-5] in by_id)]
        heads.sort(key=lambda i: _sort_value(i, sort), reverse=True)
        official_count = len(heads)

        # 3 段目: 上位のベースモデルごとに、派生（量子化・微調整）を filter=base_model: で探す（GGUF の置き場の取りこぼし防止）
        await asyncio.gather(*(run(f"派生 {h['id']}", [("filter", f"base_model:{h['id']}")])
                               for h in heads[:MAX_BASE_MODELS]))
        items = [summarize(row, via=label) for row, label in raw_by_id.values()]
        total_found = len(items)
        by_id = {i["id"]: i for i in items}
        head_ids = {h["id"] for h in heads}
        used: set[str] = set(head_ids)

        children: dict[str, list[dict]] = {}       # ベース id -> GGUF の置き場の候補
        derivs: list[dict] = []
        for item in items:
            if item["id"] in head_ids:
                continue
            base = item["base_model"]
            guessed = item["id"][:-5] if item["id"].lower().endswith("-gguf") else ""
            if is_official(item) and (item["id"] in used or guessed in head_ids):
                children.setdefault(guessed if guessed in head_ids else base, []).append(item)
                used.add(item["id"])
            elif (item["gguf"] or not item["files_known"]) and item["relation"] in ("quantized", "") and base in head_ids \
                    and not item["derivative_name"]:
                children.setdefault(base, []).append(item)
                used.add(item["id"])
        for item in items:
            if item["id"] in used or is_official(item) or not _pipeline_ok(item, pipeline):
                continue
            derivs.append(item)
        derivs_ids = {d["id"] for d in derivs}
        for d in list(derivs):                      # 派生のそのまた量子化は、派生の置き場にする
            if d["relation"] == "quantized" and d["base_model"] in derivs_ids and (d["gguf"] or not d["files_known"]):
                children.setdefault(d["base_model"], []).append(d)
                derivs.remove(d)
        derivs.sort(key=lambda i: _sort_value(i, sort), reverse=True)
        for h in heads:
            if h["gguf"]:
                children.setdefault(h["id"], []).append(h)

        sem2 = asyncio.Semaphore(TREE_CONCURRENCY)

        async def enrich(item: dict) -> dict:
            if item["gguf"] or not item["files_known"]:
                async with sem2:
                    try:
                        files = await _tree(client, item["id"])
                        item.update(classify_gguf(item["id"], files))
                        item["files_known"] = True
                    except (HfError, httpx.HTTPError) as exc:
                        if "秘密の会話" in str(exc):
                            raise
                        warnings.append(f"{item['id']}: GGUF の大きさを取れませんでした")
            return item

        def placement_of(item: dict) -> dict:
            sizes = {q["quant"]: q["size"] for q in item["quants"]}
            basis = sorted(({**b, "size": sizes.get(b["quant"], 0)} for b in item["strata_basis"]),
                       key=lambda b: (b["kind"] == "q8_0", b["size"]))   # UD・GSQ が先、Q8_0 は後
            rank = trust_rank(item["id"], official)
            return {"id": item["id"], "url": item["url"], "author": item["author"], "trust": rank,
                    "strata": item["strata"], "strata_family": item["strata_family"], "basis": basis,
                    "strata_registerable": item["strata_registerable"], "updated": item["updated"],
                    "quants": item["quants"], "registered": False, "registered_as": []}

        async def build(row: dict, candidates: list[dict], *, derivative: bool, parent: str = "") -> dict:
            """置き場の候補を信頼の順（UD・GSQ が先、Q8_0 は後）に tree で確かめ、最大 3 つ付ける。"""
            def order(c: dict):
                kinds = {b["kind"] for b in c["strata_basis"]}
                return (0 if kinds & {"unsloth-ud", "gsq-rco"} else 1, trust_rank(c["id"], official), -c["downloads"])
            pool = [c for c in candidates if (c["strata"] or not c["files_known"]) or runtime == "llama.cpp" and c["gguf"]]
            if runtime == "llama.cpp":
                pool = [c for c in candidates if c["gguf"] or not c["files_known"]]
            pool.sort(key=order)
            placements, gguf_repos = [], 0
            for cand in pool[:MAX_PLACEMENT_CANDIDATES]:
                await enrich(cand)
                if cand["gguf"]:
                    gguf_repos += 1
                if cand["strata"] or (runtime == "llama.cpp" and cand["gguf"]):
                    placements.append(placement_of(cand))
                if len(placements) >= MAX_PLACEMENTS:
                    break
            placements.sort(key=lambda p: (0 if {b["kind"] for b in p["basis"]} & {"unsloth-ud", "gsq-rco"} else 1, p["trust"]))
            return {**row, "derivative": derivative, "parent": parent, "placements": placements,
                    "gguf_repos": max(gguf_repos, len([c for c in candidates if c["gguf"]])),
                    "strata_found": any(p["strata"] for p in placements)}

        def keep(row: dict) -> bool:
            if runtime == "strata":
                return row["strata_found"]
            if runtime == "llama.cpp":
                return bool(row["placements"])
            return True

        async def evaluate(pool: list[dict], *, derivative: bool) -> list[dict]:
            kept, cursor = [], 0
            while cursor < len(pool) and len(kept) < limit:
                batch = pool[cursor:cursor + max(limit - len(kept), 10)]
                cursor += len(batch)
                built = await asyncio.gather(*(build(
                    h, children.get(h["id"], [h] if h["gguf"] else []) if not derivative else
                    ([h] if h["gguf"] else []) + children.get(h["id"], []), derivative=derivative,
                    parent=h["base_model"] if derivative else "") for h in batch))
                kept.extend(r for r in built if keep(r))
            return kept[:limit]

        head_rows = await evaluate(heads, derivative=False)
        deriv_rows = await evaluate(derivs, derivative=True) if include_derivatives else []
        derivative_total = len(derivs)

    # 並べ方: 公式のベースモデルが先。派生はそれぞれのベースの下、ベースが無いものは最後
    rows: list[dict] = []
    shown_heads = {r["id"] for r in head_rows}
    for r in head_rows:
        rows.append(r)
        rows.extend(d for d in deriv_rows if d["parent"] == r["id"])
    rows.extend(d for d in deriv_rows if d["parent"] not in shown_heads)
    mark_registered(_all_repos(rows))
    counts = {"searched": total_found, "official": official_count, "official_shown": len(head_rows),
              "with_strata": sum(1 for r in rows if r["strata_found"]), "derivatives_found": derivative_total,
              "derivatives_shown": len(deriv_rows)}
    result = {
        "query": query, "author": author, "official": official, "sort": sort, "runtime": runtime, "limit": limit,
        "pipeline": pipeline, "include_derivatives": include_derivatives, "variants": variants, "items": rows,
        "counts": counts, "total_found": total_found, "returned": len(rows), "queries": queries,
        "warnings": warnings[:10], "cached": False, "strata_note": STRATA_NOTE,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _cache[key] = (time.monotonic(), result)
    return result


def _all_repos(rows: list[dict]) -> list[dict]:
    """登録済みの印を付ける対象（行と置き場の全部。置き場は id だけ持つ辞書）。"""
    out = []
    for r in rows:
        out.append(r)
        out.extend(r["placements"])
    return out


# --- 道具の答えの形 ---

def _gb(size: int) -> str:
    return f"{size / 1e9:.1f}GB"


def _placement_text(p: dict) -> str:
    basis = "、".join(f"{b['quant']}（{b['file']}{'・' + _gb(b['size']) if b['size'] else ''}）" for b in p["basis"][:3])
    more = f" 他{len(p['basis']) - 3}" if len(p["basis"]) > 3 else ""
    note = f"。登録画面で選べる: {'・'.join(p['strata_registerable'])}" if p["strata_registerable"] else ""
    mark = "【登録済み】" if p["registered"] else ""
    return f"{mark}[{p['id']}]({p['url']}) {basis or '（GGUF）'}{more}{note}"


def _row(item: dict) -> str:
    size = f"{item['params_b']:g}B（{item['params_source']}）" if item["params_b"] else ""
    if item["placements"]:
        strata = "<br>".join(_placement_text(p) for p in item["placements"])
        if item["strata_found"]:
            strata = f"対応（{item['placements'][0]['strata_family']}）: " + strata
    elif item["gguf_repos"]:
        strata = f"Strata 対応の置き場なし（GGUF の置き場 {item['gguf_repos']} 件は対応の量子化なし）"
    else:
        strata = "置き場なし（GGUF なし）"
    feats = [item["pipeline"] or "", f"license {item['license']}" if item["license"] else "",
             f"文脈 {item['context_length'] // 1024}k" if item["context_length"] else "",
             "画像入力あり" if item["vision"] else "", f"作成 {item['created']}" if item["created"] else "",
             f"DL {item['downloads']:,}・いいね {item['likes']:,}"]
    if item["derivative"]:
        feats.insert(0, f"**派生**（ベース {item['base_model'] or '不明'}）")
    if item["registered"]:
        feats.insert(0, f"**登録済み**（{'・'.join(item['registered_as'])}）")
    name = f"[{item['id']}]({item['url']})"
    if item["derivative"]:
        name = "└ 派生 " + name
    cells = [name, item["updated"], size, strata, " / ".join(f for f in feats if f)]
    return "| " + " | ".join(c.replace("|", "/") for c in cells) + " |"


def render(result: dict, max_chars: int = 24000) -> str:
    """モデルへ返す文。表（ベースモデル・更新日・大きさ・Strata で動かせる置き場と根拠のファイル・特徴）と Hugging Face のリンク。"""
    c = result["counts"]
    head = [
        f"Hugging Face の検索結果（{result['fetched_at']} 時点）: 検索語「{result['query']}」"
        + (f"・作者 {result['author']}" if result["author"] else "")
        + f"・並べ方 {result['sort']}・runtime {result['runtime']}・pipeline {result['pipeline']}"
        + f"・公式の作者 {result['official'] or '不明'}",
        f"調べた件数 {c['searched']}（語の揺れ {len(result['variants'])} 通り・GGUF の別作者・派生で収集）／"
        f"見つけた公式モデル {c['official']}（表に {c['official_shown']}）／Strata で動かせる置き場が見つかった行 {c['with_strata']}。"
        + (f" 派生 {c['derivatives_found']} 件のうち {c['derivatives_shown']} 件を公式の行の下に「派生」の印つきで載せています。"
           if result["include_derivatives"] else f" 派生 {c['derivatives_found']} 件は省いています（include_derivatives=true で含める）。"),
        "表は公式のベースモデルが先で、新しい順です。置き場は UD・GSQ が先、Q8_0 は後、作者は 公式>unsloth・ISTA-DASLab>bartowski・lmstudio-community・ggml-org>その他 の順です。",
        result["strata_note"],
    ]
    if result["warnings"]:
        head.append("一部の検索が失敗しています: " + "; ".join(result["warnings"][:3]))
    head.append("| モデル | 更新日 | 大きさ | Strata で動かせる置き場と根拠のファイル | 特徴 |\n|---|---|---|---|---|")
    rows = [_row(i) for i in result["items"]]
    text, shown = "\n".join(head), 0
    for row in rows:
        if len(text) + len(row) + 1 > max_chars:
            break
        text += "\n" + row
        shown += 1
    if shown < len(rows):
        text += (f"\n\n（文字数の都合で {len(rows) - shown} 件を省きました。作者（author）や runtime を絞って、もう一度呼ぶと残りが見えます）")
    if not rows:
        text += "\n\n該当するモデルはありませんでした。語・作者・runtime を変えて探し直してください。"
    return text + "\n\n答えは、表の各行にこの Hugging Face のリンクを付けて、特徴とともに日本語で伝えてください。公式のモデルと派生は分けて説明し、結果にないことは推測と明示してください。"
