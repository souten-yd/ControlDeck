"""ハブ（QNAP ニュースハブ）の関係・概要で、検索の語を広げる。

2026-11-27 利用者「（ニュースハブの）関係と概要あたりは参考にすることで、検索ワードを広げるとかそういうのに利用できないか。例えば特許とかにも」。
流れ: 問い → ハブの `/retrieve/seeds`（名前・別名で当たった Things だけ）→ `/things/context`（別名・概要）と `/graph/neighbors`（深さ 1・型つきの周り）→ 語と重み・理由。
**使う場所（特許・Web 調査・RAG）は設定 `news.hub.features.expand_*` で入り切り**（既定は docs/implementation-status.md の測定の結果）。

- 重み: 別名 1.0 ＞ 上位・下位・置き換え 0.7 ＞ 仲間・使う 0.5 ＞ 概要の語 0.3（× 確かさ × 種の順位の割引）。**既定は 0.45 未満を足さない**（測定: 仲間・概要の語は外れを足す）。ハブ全体で記事の多すぎる語（一般語）・問いと同じ語・短すぎる語は外す（辞書でなく、ハブの記事数で決める）。
- purpose=patent は製品名・社名・人名・版より、技術の言葉（tech・概念・上位の型）を重く。
- 区分: ハブは自宅の LAN 内で LLM を使わない検索なので、秘密の会話・自分の発明の流れの中でも呼んでよい（利用者の決定「研究ハブが正本・機密も QNAP に置いてよい」）。
  ただし秘密のスコープのときは `confidential=true` を付けて呼び、ハブ側が許さなければ（403）空の結果で元の語だけで続く。**外部（インターネット）へは何も送らない**（呼ぶのは hub_link.call だけ）。
- つながっていない・時間切れ・エラーは空の結果（理由つき）。同じ問いの結果は 10 分覚える（失敗も短く覚え、ハブが落ちている間に毎回待たない）。
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from app.news import hub_link

logger = logging.getLogger("control_deck.knowledge.hub_expand")

PURPOSES = ("patent", "web", "rag")
TTL_SECONDS = 600.0
FAIL_TTL_SECONDS = 30.0
CACHE_MAX = 256
SEED_VIAS = ("name_exact", "alias_exact", "in_query")      # 名前・別名で当たったものだけ。部分一致（partial）は語がぶれるので種にしない
SEED_MAX = 3
SEED_DECAY = 0.8                                            # 種の順位ごとの割引
NEIGHBOR_LIMIT = 40
MIN_WEIGHT = 0.45                                           # 既定の足切り。測定（hub_expand_eval）: 別名・正式名・確かな上位下位置換（0.45 以上）は効き、仲間・使う・概要の語（0.45 未満）は足すと外れる（特に Web の対照で悪化）
GENERIC_ARTICLE_COUNT = 150                                 # ハブでこれ以上の記事に出る物の名前は一般語として外す（どの検索にも当たってしまう）
MIN_ASCII, MIN_CJK = 3, 2
HEADERS = {"X-Requested-With": "NewsHub"}
_GROUP_WEIGHT = {"alias": 1.0, "name": 0.9, "upper": 0.7, "lower": 0.7, "replaces": 0.7, "replaced_by": 0.7, "peers": 0.5, "uses": 0.5, "used_by": 0.5,
                 "maker": 0.3, "definition": 0.3}
_PATENT_TYPE = {"tech": 1.3, "policy": 1.0, "other": 0.8, "software": 0.6, "model": 0.5, "game": 0.4, "product": 0.4, "org": 0.3, "person": 0.2, "place": 0.2}
_PATENT_GROUP = {"upper": 1.3, "maker": 0.3}
_CJK_RUN = re.compile(r"[ァ-ヴー]{3,}|[一-龥]{2,}|[A-Za-z][A-Za-z0-9+#.\-]{2,}")
_VERSIONISH = re.compile(r"^(v?\d[\w.\-]*|[0-9a-f]{7,}|b\d{3,})$", re.I)
_STOP = frozenset({"the", "and", "for", "with", "this", "that", "from", "using", "based", "software", "system", "tool", "tools", "model", "data", "api", "ソフトウェア", "システム", "ツール",
                   "データ", "サービス", "機能", "技術", "実行", "提供", "開発", "公開", "利用", "対応", "可能", "ため", "こと", "もの", "など", "において", "による"})
_lock = threading.Lock()
_cache: dict[tuple, tuple[float, dict]] = {}


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", str(text or "")).casefold().strip()


def _empty(reason: str, started: float, **extra) -> dict:
    return {"terms": [], "things": [], "elapsed_ms": round((time.monotonic() - started) * 1000, 1), "reason": reason, **extra}


def _ok_term(term: str, query_norm: str) -> bool:
    key = _norm(term)
    if not key or key in _STOP or _VERSIONISH.match(key):
        return False
    ascii_only = all(ord(ch) < 128 for ch in key)
    if len(key) < (MIN_ASCII if ascii_only else MIN_CJK):
        return False
    return key not in query_norm


def _definition_terms(definition: str) -> list[str]:
    return [m.group(0) for m in _CJK_RUN.finditer(unicodedata.normalize("NFKC", definition or ""))][:6]


def _group_of(edge: dict, seed_id: int) -> str:
    """辺の型と向きから、その物から見た関係の節（ハブの things.group_of と同じ分け方。目的の違う made_by・develops は maker）。"""
    kind, outgoing = edge.get("type"), edge.get("source") == seed_id
    if kind == "part_of":
        return "upper" if outgoing else "lower"
    if kind in ("competes",):
        return "peers"
    if kind in ("uses", "implements", "supports"):
        return "uses" if outgoing else "used_by"
    if kind == "replaces":
        return "replaces" if outgoing else "replaced_by"
    if kind in ("made_by", "develops"):
        return "maker"
    return ""      # co_occurs・cites・contradicts・other は語の足しにしない（同じ記事に出ただけ・反対の意味）


def _collect(query: str, purpose: str, seeds: list[dict], contexts: dict[int, dict], neighbors: dict[int, dict]) -> list[dict]:
    qn = _norm(query)
    found: dict[str, dict] = {}

    def add(term: str, group: str, seed: dict, rank: int, *, conf: float = 1.0, typ: str = "", reason: str = "") -> None:
        if not _ok_term(term, qn):
            return
        weight = _GROUP_WEIGHT[group] * (SEED_DECAY ** rank) * max(0.3, min(1.0, conf))
        if purpose == "patent":
            weight *= _PATENT_TYPE.get(typ, 0.8) if group not in ("alias", "name", "definition") else 1.0
            weight *= _PATENT_GROUP.get(group, 1.0)
        key = _norm(term)
        row = {"term": term, "weight": round(weight, 3), "reason": reason, "source_thing": seed["name"]}
        if key not in found or found[key]["weight"] < row["weight"]:
            found[key] = row

    for rank, seed in enumerate(seeds):
        ctx = contexts.get(seed["id"]) or {}
        for alias in ctx.get("aliases") or []:
            add(alias, "alias", seed, rank, reason=f"{seed['name']} の別名")
        add(seed["name"], "name", seed, rank, reason=f"問いの語「{seed['match_term']}」の正式名" if seed.get("match_term") else "名前")
        for word in _definition_terms(ctx.get("definition", "")):
            add(word, "definition", seed, rank, reason=f"{seed['name']} の概要: {ctx['definition'][:40]}")
        graph = neighbors.get(seed["id"]) or {}
        nodes = {n["id"]: n for n in graph.get("nodes", [])}
        for edge in graph.get("edges", []):
            other = nodes.get(edge["target"] if edge.get("source") == seed["id"] else edge["source"])
            group = _group_of(edge, seed["id"])
            if other is None or not group or int(other.get("article_count") or 0) >= GENERIC_ARTICLE_COUNT:
                continue
            label = {"upper": "上位", "lower": "下位", "replaces": "置き換える", "replaced_by": "置き換えられる", "peers": "仲間", "uses": "使う", "used_by": "使われる", "maker": "作り手"}[group]
            add(other["name"], group, seed, rank, conf=float(edge.get("confidence") or 0.7), typ=str(other.get("type") or ""), reason=f"{seed['name']} の{label}")
    return sorted(found.values(), key=lambda r: (-r["weight"], r["term"]))


def _fetch(query: str, purpose: str, confidential: bool, deadline: float, link: dict | None) -> dict:
    """ハブから種・文脈・周りを集める。deadline（monotonic）を過ぎそうなら、そこまでに集まったものだけを返す。"""
    def left() -> float:
        return deadline - time.monotonic()

    def call(method: str, path: str, body: dict | None = None):
        if left() <= 0.05:
            raise hub_link.HubError("時間切れ", 0)
        extra = {"url": link["url"], "token": link["token"]} if link else {}
        return hub_link.call(method, path, json_body=body, headers=HEADERS, timeout=left(), **extra)

    reply = call("POST", "/retrieve/seeds", {"q": query[:500], "kinds": ["entity"], "limit": 8, "confidential": confidential, "budget_ms": max(10, min(3000, int(left() * 500)))})
    seeds = []
    for item in reply.get("items", []):
        via = (item.get("match") or {}).get("via")
        if item.get("kind") == "entity" and via in SEED_VIAS and item.get("name") and int(item.get("article_count") or 0) < GENERIC_ARTICLE_COUNT * 4:
            seeds.append({"id": int(item["id"]), "name": str(item["name"]), "type": str(item.get("type") or ""), "match_term": ((item.get("match") or {}).get("terms") or [""])[0]})
        if len(seeds) >= SEED_MAX:
            break
    if not seeds:
        return {"seeds": [], "contexts": {}, "neighbors": {}}
    ids = [s["id"] for s in seeds]
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_ctx = pool.submit(call, "POST", "/things/context", {"ids": ids, "max_relations": 0, "max_facts": 0, "format": "json", "confidential": confidential})
        f_nb = pool.submit(call, "POST", "/graph/neighbors", {"ids": ids, "depth": 1, "limit": NEIGHBOR_LIMIT, "order": "weight", "hub_k": 12, "min_confidence": 0.3,
                                                              "confidential": confidential, "budget_ms": max(10, min(3000, int(left() * 500)))})
        contexts, graph = {}, {}
        try:
            contexts = {int(c["id"]): c for c in f_ctx.result().get("items", [])}
        except hub_link.HubError as exc:
            logger.info("ハブの文脈を取れませんでした: %s", exc.message)
        try:
            graph = f_nb.result()
        except hub_link.HubError as exc:
            logger.info("ハブの周りを取れませんでした: %s", exc.message)
    # 周りは入り口の物ごとに分ける（辺の via が入り口）
    nodes = [n for level in graph.get("levels", []) for n in level.get("nodes", [])]
    edges = [e for level in graph.get("levels", []) for e in level.get("edges", [])]
    per: dict[int, dict] = {sid: {"nodes": nodes, "edges": []} for sid in ids}
    for edge in edges:
        via = edge.get("via")
        if via in per:
            per[via]["edges"].append(edge)
    return {"seeds": seeds, "contexts": contexts, "neighbors": per}


def expand(query: str, *, purpose: str, limit: int = 12, budget_ms: int = 800, confidential: bool | None = None, link: dict | None = None,
           min_weight: float = MIN_WEIGHT) -> dict:
    """問いの語を広げる。`{"terms": [{"term","weight","reason","source_thing"}], "things": [...], "elapsed_ms", "reason"}`。失敗は空（reason に理由）。
    confidential=None は今の会話の秘密のスコープに従う。link={"url","token"} は評価用（保存したつなぎを使わない）。"""
    started = time.monotonic()
    query = " ".join(str(query or "").split())
    if purpose not in PURPOSES:
        raise ValueError(f"purpose は {PURPOSES} のどれかです")
    if not query:
        return _empty("empty_query", started)
    if confidential is None:
        from app.security import offline

        # 特許は自分の発明の流れの語を含みうるので、いつも秘密の区分として問う（ハブのつなぎが許していなければ 403 → 空）
        confidential = purpose == "patent" or offline.in_confidential_scope()
    key = (purpose, query, bool(confidential), int(limit), float(min_weight), link["url"] if link else "")
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return {**hit[1], "elapsed_ms": 0.0, "cached": True}
    if link is None and not hub_link.ready():
        return _empty("not_linked", started)
    try:
        data = _fetch(query, purpose, bool(confidential), started + max(0.05, budget_ms / 1000), link)
        terms = [t for t in _collect(query, purpose, data["seeds"], data["contexts"], data["neighbors"]) if t["weight"] >= min_weight][:max(0, int(limit))]
        result = {"terms": terms, "things": [{"id": s["id"], "name": s["name"], "type": s["type"], "definition": (data["contexts"].get(s["id"]) or {}).get("definition", "")} for s in data["seeds"]],
                  "reason": "ok" if terms else "no_match"}
        ttl = TTL_SECONDS
    except hub_link.HubError as exc:
        # 403（秘密の区分をこのつなぎは許されていない）・時間切れ・つながらない: 元の語だけで続ける
        result, ttl = {"terms": [], "things": [], "reason": "forbidden" if exc.status == 403 else "timeout" if exc.message == "時間切れ" else "error"}, FAIL_TTL_SECONDS
        logger.info("ハブでの語の拡張を飛ばしました（%s）", exc.message)
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
    with _lock:
        if len(_cache) >= CACHE_MAX:
            for stale in [k for k, v in _cache.items() if v[0] <= now] or [next(iter(_cache))]:
                _cache.pop(stale, None)
        _cache[key] = (now + ttl, result)
    return result


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def enabled(purpose: str) -> bool:
    """設定 `news.hub.features.expand_<purpose>`。つながっていなければ常に False。"""
    if purpose not in PURPOSES:
        return False
    from app.news import settings

    try:
        return bool(settings.load()["hub"]["features"].get(f"expand_{purpose}")) and hub_link.ready()
    except Exception:  # noqa: BLE001 — 検索の補助が検索を止めない
        return False


async def aexpand(query: str, *, purpose: str, **kwargs) -> dict:
    import asyncio

    return await asyncio.to_thread(expand, query, purpose=purpose, **kwargs)


def or_query(terms: list[dict], *, max_terms: int = 6) -> str:
    """元の語に足す OR の節（重み順・上限つき）。引用符で囲んだ語を ` OR ` でつなぐ。"""
    picked = [t["term"].replace('"', " ").strip() for t in terms[:max_terms] if t.get("term")]
    return " OR ".join(f'"{t}"' for t in picked if t)


WEB_EXTRA_QUERIES = 2      # Web 調査の追加の問いの本数（1〜2 本）
EXPAND_WEIGHT = 0.5      # 広げた問いの結果の重み（元の問いは 1.0。測定は hub_expand_eval）
FUSE_K = 60


def fuse_hits(base: list[dict], extra: list[dict], *, key: str = "chunk_ref", weight: float = EXPAND_WEIGHT) -> list[dict]:
    """元の問いの結果（base）に、広げた問いの結果（extra）を RRF で融合する（同じ断片は 1 つにして点数を足す）。extra の重みは 0.5 で、元の順を崩しにくい。"""
    score: dict[str, float] = {}
    row: dict[str, dict] = {}
    for items, w in ((base, 1.0), (extra, weight)):
        for rank, hit in enumerate(items):
            score[hit[key]] = score.get(hit[key], 0.0) + w / (FUSE_K + rank)
            row.setdefault(hit[key], hit)
    return [{**row[ref], "score": round(score[ref], 5)} for ref in sorted(score, key=lambda r: -score[r])]


# ---- AI アシスタント（MCP）の道具: 問い → Things のカードと根拠の記事 ----

CARD_THINGS_MAX = 3
CARD_DOCS_MAX = 5
CARD_CHARS_MAX = 12_000
_CARD_REL_LABEL = {"upper": "上位", "lower": "下位", "peers": "仲間", "uses": "使う", "used_by": "使われる", "replaces": "置き換える", "replaced_by": "置き換えられる",
                   "contradicts": "矛盾", "other": "その他"}


def _card_text(card: dict) -> str:
    lines = [f"# {card.get('name', '')} (#{card.get('id')}) — {card.get('type', '')}・{card.get('status', '')}"]
    if card.get("definition"):
        lines.append(str(card["definition"])[:300])
    if card.get("aliases"):
        lines.append("別名: " + "、".join(str(a) for a in card["aliases"][:6]))
    for key, rows in (card.get("relations") or {}).items():
        lines.append(f"{_CARD_REL_LABEL.get(key, key)}: " + "、".join(f"{r.get('name')}" for r in rows[:6]))
    facts = [f for f in card.get("facts", []) if f.get("text")][:4]
    if facts:
        lines.append("主張: " + " / ".join(f"{f['text'][:100]}" + (f" <{(f.get('src_label') or [''])[0]}>" if f.get("src_label") else "") for f in facts))
    for key, label in (("change_points", "変化"), ("opposing", "反対の見方"), ("open_questions", "未解決")):
        if card.get(key):
            lines.append(f"{label}: " + " / ".join(str(x)[:100] for x in card[key][:3]))
    return "\n".join(lines)


def news_cards(query: str, *, things: int = CARD_THINGS_MAX, docs: int = CARD_DOCS_MAX, max_chars: int = CARD_CHARS_MAX, budget_ms: int = 3000) -> dict:
    """問いに合うハブの Things のカード（概要・別名・関係・主張・変化と矛盾）と、根拠の記事（題・出どころ・日付）を返す。**公開の区分だけ**（MCP の相手のモデルは分からないので cloud とみなす）。
    件数（things・docs）と全体の大きさ（max_chars）に上限。つながっていなければ空（reason=not_linked）。"""
    started = time.monotonic()
    query = " ".join(str(query or "").split())[:500]
    if not query:
        return {"text": "", "things": [], "sources": [], "reason": "empty_query", "elapsed_ms": 0.0}
    if not hub_link.ready():
        return {"text": "", "things": [], "sources": [], "reason": "not_linked", "elapsed_ms": 0.0}
    things, docs, max_chars = max(1, min(int(things), 5)), max(0, min(int(docs), 8)), max(500, min(int(max_chars), CARD_CHARS_MAX))
    deadline = started + budget_ms / 1000
    try:
        def call(path: str, body: dict):
            return hub_link.call("POST", path, json_body=body, headers=HEADERS, timeout=max(0.2, deadline - time.monotonic()))

        items = call("/retrieve/seeds", {"q": query, "kinds": ["entity", "document"], "limit": 8, "confidential": False}).get("items", [])
        picked = [i for i in items if i.get("kind") == "entity" and (i.get("match") or {}).get("via") in SEED_VIAS][:things] or [i for i in items if i.get("kind") == "entity"][:things]
        cards = call("/things/context", {"ids": [int(i["id"]) for i in picked], "max_relations": 8, "max_facts": 4, "format": "json", "confidential": False}).get("items", []) if picked else []
        sources = [{"title": str(i.get("title", ""))[:160], "source": i.get("source", ""), "published_at": str(i.get("published_at", ""))[:10]}
                   for i in items if i.get("kind") == "document"][:docs]
    except hub_link.HubError as exc:
        return {"text": "", "things": [], "sources": [], "reason": "error", "error": exc.message, "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
    parts = [_card_text(c) for c in cards]
    if sources:
        parts.append("根拠の記事:\n" + "\n".join(f"- {s['title']}（{s['source']} {s['published_at']}）" for s in sources))
    text = "\n\n".join(parts)
    return {"text": text[:max_chars], "things": [{"id": c.get("id"), "name": c.get("name")} for c in cards], "sources": sources, "truncated": len(text) > max_chars,
            "reason": "ok" if text else "no_match", "elapsed_ms": round((time.monotonic() - started) * 1000, 1)}
