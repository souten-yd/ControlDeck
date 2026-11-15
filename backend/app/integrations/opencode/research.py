"""OpenCode から ControlDeck の Deep Research を使う（`control_deck.research`）。

新しい調査エージェントは作らない。AI チャットの Deep Research（複数ラウンドの検索・
再評価・反証・引用検証・GitHub・学術・ローカルコード）をそのまま呼び、結果を
`control_deck.report` と同じ形で残して画面に出す。

外へ出るかどうかは利用者が決める。既定は「ローカルだけ」（プロジェクトのコードと
RAG）で、検索語が外部の検索サービスへ出ることはない。未公開の発明や請求項を扱う
プロジェクトで、AI が勝手に外へ問い合わせないための既定である。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

LOCAL_SOURCES = ("local_code", "rag")
EXTERNAL_SOURCES = ("web", "academic", "github", "direct")
DEPTHS = ("quick", "standard", "deep")
REPORT_INLINE_CHARS = 12_000

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "調べたい問い。版や前提（使っているライブラリと版）を含めて具体的に"},
        "depth": {"type": "string", "enum": list(DEPTHS), "description": "quick（数分）/ standard / deep（長い）"},
        "sources": {"type": "array", "items": {"type": "string", "enum": [*LOCAL_SOURCES, *EXTERNAL_SOURCES]},
                    "description": "使う情報源。外部（web / academic / github / direct）は利用者が許可したときだけ使える"},
        "project_dir": {"type": "string", "description": "コードを調べる対象のプロジェクト（CodeDEV 配下の絶対パス）。いまの作業ディレクトリ"},
    },
    "required": ["question"],
    "additionalProperties": False,
}


class ResearchRefused(ValueError):
    pass


def allowed_sources(settings: dict) -> tuple[str, ...]:
    return (*LOCAL_SOURCES, *EXTERNAL_SOURCES) if settings.get("research_external") else LOCAL_SOURCES


def plan(arguments: dict[str, Any], settings: dict, *, project_path: Path | None) -> dict[str, Any]:
    """引数を検査し、Deep Research に渡す形にする。外部を許していなければ外部の情報源は落とす。"""
    question = str(arguments.get("question") or "").strip()
    if not question or len(question) > 2000:
        raise ResearchRefused("question は 1〜2000 文字で渡してください")
    depth = str(arguments.get("depth") or "standard")
    if depth not in DEPTHS:
        raise ResearchRefused(f"depth は {' / '.join(DEPTHS)} のどれかです")
    allowed = allowed_sources(settings)
    wanted = [str(item) for item in (arguments.get("sources") or [])] or list(allowed)
    refused = [item for item in wanted if item not in allowed]
    sources = [item for item in wanted if item in allowed]
    if not sources:
        raise ResearchRefused("外部の情報源は許可されていません（OpenCode の設定 →「精密調査で外部の検索を使う」）")
    if "local_code" in sources and project_path is None:
        sources = [item for item in sources if item != "local_code"] or []
        if not sources:
            raise ResearchRefused("project_dir に CodeDEV 配下のプロジェクトを渡してください")
    return {"question": question, "depth": depth, "sources": sources, "refused": refused,
            "project_path": str(project_path) if project_path else ""}


def resolve_project(value: object, *, codedev: Path, extra_roots: tuple[Path, ...] = ()) -> Path | None:
    """AI が渡したプロジェクトの場所。CodeDEV（と許された root）の中の既存ディレクトリだけを受ける。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    roots = [codedev.resolve(), *(root.resolve() for root in extra_roots)]
    if not path.is_dir() or not any(path == root or path.is_relative_to(root) for root in roots):
        return None
    return path


def to_report(question: str, result: dict[str, Any], *, refused: list[str]) -> dict[str, Any]:
    """Deep Research の結果を control_deck.report の形へ。"""
    accessed = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    sources = []
    for item in (result.get("sources") or [])[:120]:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        kind = str(item.get("kind") or "")
        sources.append({
            "title": str(item.get("title") or "")[:500],
            "url": str(item.get("url") or "")[:2048],
            "accessed_at": accessed,
            "published_at": str(meta.get("published") or meta.get("date") or item.get("published") or "")[:64],
            "version": str(meta.get("version") or meta.get("ref") or "")[:120],
            "claim": str(item.get("snippet") or "")[:600],
            "stance": "context",
            # 公式文書・仕様・ソース・論文を一次資料として扱う。ブログや Q&A は二次。
            "primary": kind in {"code", "repository", "paper", "spec", "official", "document"} or str(item.get("source") or "") in {"GitHub", "RAG"},
        })
    research = result.get("research") or {}
    limits = [str(item)[:1000] for item in (research.get("coverage_limits") or [])[:30]]
    if refused:
        limits.append(f"許可されていない情報源は使っていません: {', '.join(refused)}")
    metrics = research.get("citation_metrics") or {}
    coverage = float(metrics.get("citation_coverage") or 0)
    return {
        "kind": "research",
        "title": question[:300],
        "summary": str(result.get("report") or "")[:8000],
        "verdict": "pass" if coverage >= 0.8 and not limits else "partial",
        "target": question[:2048],
        "sources": sources,
        "unverified": limits,
    }
