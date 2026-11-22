"""知識マップ（計画 §10 R3-5、PatentBuddy Draft #34 を作り直したもの）。

- 1 つの文献を中心に、書誌の関係（引用する・される・別の版。documents.related_documents）で
  1〜2 hop の文献を広げ、それぞれの承認済みの知識エッセンス（K1）をノードとエッジで返す。
- 読み取りだけ: LLM を呼ばない・書き込みもしない・新しい表も Job も監査も不要。
- 上限はノード 61・エッジ 60。超えたら打ち切りの印（truncated）を出す
  （PatentBuddy の 36/52 を、文献と知識を 1 つの地図に載せる分だけ広げた）。
- エッセンスは K1 の list_essences の判定（今の版・承認済み・epoch）をそのまま使い回す。
  引用を原文と読むたびに突き合わせ直すことはしない（K2 の横断検索と違い、ここは
  表示のための読み取りで、2 hop までの文献数ぶんの全文読みは重い。エッセンスは
  保存時に検めてある）。1 文献 8 件まで（1 文献の知識で地図が埋まらないように）。
- 意味の関係（AI の推論で繋ぐ）は作らない。書誌の関係と「出典」だけ（統合の決まり 1・K3 の割り切り）。
"""
from __future__ import annotations

import logging

from app.knowledge import documents, essence, store

logger = logging.getLogger("control_deck.knowledge.map")

MAX_NODES = 61
MAX_EDGES = 60
MAX_ESSENCES_PER_DOC = 8   # 1 文献の承認済みの知識は 8 件まで載せる
HOP_DOCS = 12              # 1 回の広げで見る文献の上限（上位の関係から順に）

RELATIONS = {
    "cites": "参照",
    "cited_by": "参照される",
    "version": "別の版",
    "evidence": "出典",
}


def knowledge_map(ref: str, hops: int = 1) -> dict:
    """1 つの文献の周りを、上限つきの部分グラフとして返す（読み取りだけ・書き込みしない）。

    - hops は 1 か 2。それ以外は受け付けない。
    - ノードは document（root・関係する文献）と essence（承認済みの知識）。id は
      "d:<文献ref>"・"k:<抽出ref>:<件番号>" で一意。
    - エッジは文献どうしの書誌の関係（cites・cited_by・version）と、文献から知識への「出典」。
    - 順序: root → root の知識 → 1 hop の文献と知識 → 2 hop の文献と知識。
      どの段階で上限に当たっても、それ以降は載せずに truncated を立てる。
    """
    if hops not in (1, 2):
        raise store.KnowledgeError("hop は 1 か 2 で指定してください")
    doc = documents.get_document(ref)  # 無い文書はここで「文書がありません」
    nodes: list[dict] = [{
        "id": f"d:{ref}", "kind": "document", "ref": ref, "title": doc["title"],
        "relation": "root", "depth": 0,
    }]
    edges: list[dict] = []
    truncated = False

    def full() -> bool:
        return len(nodes) >= MAX_NODES or len(edges) >= MAX_EDGES

    def add_essences(doc_ref: str) -> bool:
        """1 文献の承認済みの知識を載せる。上限に当たったら False（呼び出し側は打ち切る）。"""
        shown = 0
        for item in _approved_essences(doc_ref):
            if shown >= MAX_ESSENCES_PER_DOC:
                return False  # 1 文献の上限に達した（残りは載せないので打ち切りの印を立てる）
            if full():
                return False
            node_id = f"k:{item['essence_ref']}:{item['item_index']}"
            nodes.append({"id": node_id, "kind": "essence", "essence_ref": item["essence_ref"],
                          "item_index": item["item_index"], "doc_ref": doc_ref,
                          "essence_kind": item["kind"], "summary": item["summary"],
                          "reviewed_at": item["reviewed_at"]})
            edges.append({"source": f"d:{doc_ref}", "target": node_id, "relation": "evidence"})
            shown += 1
        return True

    if not add_essences(ref):
        truncated = True
    seen = {ref}
    frontier = [ref]
    for depth in (1, 2):
        if depth > hops or full():
            truncated = truncated or full()
            break
        next_frontier: list[str] = []
        for doc_ref in frontier:
            if full():
                truncated = True
                break
            try:
                related = documents.related_documents(doc_ref, limit=HOP_DOCS + 1)
            except store.KnowledgeError:
                # 走査の間に消えた文献: 地図全体を落とさず、その文献だけ飛ばす（AGENTS.md ルール 7）
                logger.debug("知識マップ: 文書 %s が読めませんでした", doc_ref)
                continue
            if len(related) > HOP_DOCS:
                # 1 回の広げで見る上限（HOP_DOCS）で切った: 見えていない関係があるので印を立てる
                truncated = True
                related = related[:HOP_DOCS]
            for rel in related:
                if rel["ref"] in seen or full():
                    continue
                seen.add(rel["ref"])
                nodes.append({"id": f"d:{rel['ref']}", "kind": "document", "ref": rel["ref"],
                              "title": rel["title"], "relation": rel["relation"],
                              "depth": depth, "via": rel["via"]})
                edges.append({"source": f"d:{doc_ref}", "target": f"d:{rel['ref']}",
                              "relation": rel["relation"]})
                next_frontier.append(rel["ref"])
                if not add_essences(rel["ref"]):
                    truncated = True
                if full():
                    truncated = True
                    break
        frontier = next_frontier
    return {"nodes": nodes, "edges": edges, "hops": hops, "truncated": truncated,
            "limits": {"nodes": MAX_NODES, "edges": MAX_EDGES},
            "kinds": essence.KINDS, "relations": RELATIONS}


def _approved_essences(ref: str) -> list[dict]:
    """1 文献の承認済み・今の版のエッセンス item（K1 の判定を使い回す）。上限は呼び出し側で切る。"""
    out: list[dict] = []
    for row in essence.list_essences(ref):
        if row["stale"] or row["status"] != "succeeded":
            continue
        for index, item in enumerate(row["items"]):
            if item["review_status"] != "approved":
                continue
            out.append({"essence_ref": f"{row['shard']}:{row['id']}", "item_index": index,
                        "kind": item["kind"], "summary": item["summary"],
                        "reviewed_at": (item["review"] or {}).get("at")})
    return out
