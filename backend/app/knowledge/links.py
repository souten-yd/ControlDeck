"""技術仮説のリンク（計画 §10 R3-5、PatentBuddy Draft #35 を作り直したもの）。

- 承認済みのエッセンスどうし（別の文献）を、利用者が明示して結ぶ。`basis=inferred` で固定し、
  承認しても「原文の事実」にはしない（統合の決まり 1）。AI は呼ばない（推論すら作らない）。
- 理由のメモは必須（なぜ結ぶのかを利用者の言葉で残す）。
- 作っただけでは「未確認」。別の操作（承認・却下）で確かめる。レビューは消さず追記だけ
  （K1 のレビューと同じ考え）。最新のレビューがそのリンクの状態。
- 失効（stale）: どちらかのエッセンスが今の版で承認済みでなくなったら、リンクも stale と
  扱う。判定は K1 の list_essences に任せる（版・承認・epoch がそこで判定される）。
  リンクのレビューには両側の版の番号（epoch）を記録し、番号が変わったら前の判断は効かない
  （知識の承認をやり直しても同じ文面でも承認は戻らない。K1 と同じ決まり）。
- 保存先はナレッジのストア（shard DB に表を足す。K1 の表の足し方と同じ）。リンクは
  source（結ぶ側）の文献の分野に置き、target（結ばれる側）は「分野:番号」で持つ。
- 消さない: リンクの削除は作らない（追記だけの世界観。K1 のレビューと同じ）。
"""
from __future__ import annotations

import logging
import time

from app.knowledge import documents, essence, store

logger = logging.getLogger("control_deck.knowledge.links")

MAX_NOTE_CHARS = 1000
MAX_LINKS = 100   # 1 回の一覧の上限（一覧は 1 文献に関わる分だけ）

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_links(
  id INTEGER PRIMARY KEY,
  source_doc_ref TEXT NOT NULL,
  source_essence_id INTEGER NOT NULL,
  source_item INTEGER NOT NULL,
  target_doc_ref TEXT NOT NULL,
  target_essence_id INTEGER NOT NULL,
  target_item INTEGER NOT NULL,
  basis TEXT NOT NULL DEFAULT 'inferred',
  note TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL, updated REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_knowledge_links_source ON knowledge_links(source_doc_ref, id DESC);
CREATE INDEX IF NOT EXISTS ix_knowledge_links_target ON knowledge_links(target_doc_ref, id DESC);
CREATE TABLE IF NOT EXISTS knowledge_link_reviews(
  id INTEGER PRIMARY KEY, link_id INTEGER NOT NULL,
  decision TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', reviewer TEXT NOT NULL DEFAULT '',
  source_epoch INTEGER NOT NULL DEFAULT 0, target_epoch INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_knowledge_link_reviews ON knowledge_link_reviews(link_id, id);
"""

# 後から足した列（既に作られた shard DB にも足す。store._LATE_COLUMNS と同じ考え）
_LATE_COLUMNS = {
    "knowledge_link_reviews": [
        ("source_epoch", "INTEGER NOT NULL DEFAULT 0"),
        ("target_epoch", "INTEGER NOT NULL DEFAULT 0"),
    ],
}

DECISIONS = ("approved", "rejected")


def connect(shard: str):
    """ナレッジの shard DB にリンクの表を足す（essence.connect と同じ: 表が無ければ作る）。

    後から足した列（レビューの版の番号）は、既に表が作られた DB にも足す
    （store._add_missing_columns と同じ考え。足さないと一覧・レビューが列不明で
    落ちる。表を作り直すと既存のレビューが消えるため、ALTER で足す）。
    """
    conn = store.connect(shard)
    missing = [name for name in ("knowledge_links", "knowledge_link_reviews")
               if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                (name,)).fetchone() is None]
    if missing:
        conn.executescript(SCHEMA)
    for table, columns in _LATE_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()
    return conn


def _now() -> float:
    return time.time()


def _epoch_of(doc_ref: str) -> int:
    """文書の版の番号（epoch）。K1（essence.py）の管理する番号をそのまま使う。

    置き換えのたびに単調に増えるので、知識の承認をやり直しても（同じ文面でも）
    番号は戻らない。リンクのレビューの効きをこの番号で判定する。
    """
    shard, doc_id = documents.parse_ref(doc_ref)
    return essence.epoch_of(shard, doc_id)


def parse_link_ref(link_ref: str) -> tuple[str, int]:
    """リンクを指す id。分野ごとに番号が振られるので「分野:番号」で一意に指す（K1 の parse_essence_ref と同じ考え）。"""
    shard, _, number = (link_ref or "").partition(":")
    if shard not in store.SHARDS or not number.isdigit():
        raise store.KnowledgeError("リンクの参照が不正です（分野:番号 の形で指定してください）")
    return shard, int(number)


def _essence_doc_ref(essence_ref: str) -> tuple[str, str]:
    """抽出（essence）が属する文献の ref を表から引く。無い抽出はここで断る。"""
    shard, essence_id = essence.parse_essence_ref(essence_ref)
    conn = essence.connect(shard)
    try:
        row = conn.execute("SELECT doc_id FROM knowledge_essences WHERE id=?", (essence_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise store.KnowledgeError("その知識はありません")
    return essence_ref, f"{shard}:{int(row['doc_id'])}"


def _approved_item(doc_ref: str, essence_id: int, item_index: int) -> dict | None:
    """K1 の判定を通った item だけ返す（今の版で承認済み。それ以外は None）。

    list_essences が版（stale）・レビュー（epoch つき）を判定するので、ここで改めて
    版を比べなくても、返ってきた item の承認は今の版で有効なものだけ。
    文書そのものが消えている・id が付き替えの残滓を指していることも None（失効）として
    扱う: 一覧を丸ごと落とさず、そのリンクだけが失効して見えるようにする。
    戻り値の epoch はリンクのレビューの効きの判定に使う。
    """
    try:
        rows = essence.list_essences(doc_ref)
    except store.KnowledgeError:
        return None
    for row in rows:
        if row["id"] != int(essence_id):
            continue
        if row["stale"] or row["status"] != "succeeded":
            return None
        if not 0 <= int(item_index) < len(row["items"]):
            return None
        item = row["items"][int(item_index)]
        if item["review_status"] != "approved":
            return None
        return {"essence_ref": f"{row['shard']}:{row['id']}", "item_index": int(item_index),
                "doc_ref": doc_ref, "doc_title": _doc_title(doc_ref),
                "kind": item["kind"], "summary": item["summary"],
                "reviewed_at": (item["review"] or {}).get("at"),
                "epoch": _epoch_of(doc_ref)}
    return None


def _doc_title(doc_ref: str) -> str:
    return documents.get_document(doc_ref)["title"]


def create(source_essence_ref: str, source_item: int, target_essence_ref: str, target_item: int,
           note: str, user: str = "") -> dict:
    """承認済みの知識どうしを結ぶ（未確認として保存。basis=inferred で固定）。

    - 両方が今の版で承認済みであること。
    - 別の文献であること（同じ文献の中で結ぶ意味は無い）。
    - 理由のメモ（1〜1000 字）が必須。
    - 同じ組合せ（どちらの知識のどちらの件か）のリンクが既にあれば作らない。
    """
    note = (note or "").strip()
    if not 1 <= len(note) <= MAX_NOTE_CHARS:
        raise store.KnowledgeError("結ぶ理由を書いてください（1〜1000 字）")
    _, source_doc_ref = _essence_doc_ref(source_essence_ref)
    _, target_doc_ref = _essence_doc_ref(target_essence_ref)
    if source_doc_ref == target_doc_ref:
        raise store.KnowledgeError("別の文献の知識を選んでください")
    src = _approved_item(source_doc_ref, essence.parse_essence_ref(source_essence_ref)[1], source_item)
    if src is None:
        raise store.KnowledgeError("結ぶ元の知識が、今の版で承認されていません")
    tgt = _approved_item(target_doc_ref, essence.parse_essence_ref(target_essence_ref)[1], target_item)
    if tgt is None:
        raise store.KnowledgeError("結ぶ相手の知識が、今の版で承認されていません")

    shard, source_essence_id = essence.parse_essence_ref(source_essence_ref)
    _, target_essence_id = essence.parse_essence_ref(target_essence_ref)
    conn = connect(shard)
    try:
        old = conn.execute(
            "SELECT id FROM knowledge_links WHERE source_doc_ref=? AND source_essence_id=? AND source_item=? "
            "AND target_doc_ref=? AND target_essence_id=? AND target_item=?",
            (source_doc_ref, source_essence_id, int(source_item),
             target_doc_ref, target_essence_id, int(target_item))).fetchone()
        if old is not None:
            raise store.KnowledgeError("同じ組合せのリンクが既にあります。一覧から確認してください")
        now = _now()
        link_id = conn.execute(
            "INSERT INTO knowledge_links(source_doc_ref, source_essence_id, source_item, target_doc_ref, "
            "target_essence_id, target_item, basis, note, created_by, created, updated) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (source_doc_ref, source_essence_id, int(source_item), target_doc_ref, target_essence_id,
             int(target_item), "inferred", note, (user or "")[:64], now, now)).lastrowid
        conn.commit()
    finally:
        conn.close()
    return {"link_ref": f"{shard}:{link_id}", "basis": "inferred", "review_status": "unreviewed",
            "source": src, "target": tgt, "note": note}


def _latest_review(conn, link_id: int):
    return conn.execute(
        "SELECT decision, note, reviewer, source_epoch, target_epoch, created FROM knowledge_link_reviews "
        "WHERE link_id=? ORDER BY id DESC LIMIT 1", (int(link_id),)).fetchone()


def list_for(ref: str, limit: int = MAX_LINKS) -> dict:
    """1 文献に関わるリンクの一覧（source でも target でも）。

    - 失効の判定は K1 に任せる: どちらかの知識が今の版で承認済みでなければ stale。
    - 最新のレビューの判断がそのリンクの状態（stale なら未確認に落ちる。前の判断は
      review に残してあるので、失効したものも「何と判断されたか」は見える）。
    - リンクの id は分野ごとに振られるため、一覧の重複は「分野と番号」の組で除く
      （番号だけでは別の分野の同じ番号のリンクを誤って消してしまう）。
    """
    shard, _ = documents.parse_ref(ref)
    limit = max(1, min(int(limit), MAX_LINKS))
    rows: list = []
    seen_ids: set[tuple[str, int]] = set()
    for other in store.SHARDS:
        if not store.shard_path(other).exists():
            continue
        conn = connect(other)
        try:
            for r in conn.execute(
                    "SELECT id, source_doc_ref, source_essence_id, source_item, target_doc_ref, "
                    "target_essence_id, target_item, basis, note, created_by, created "
                    f"FROM knowledge_links WHERE source_doc_ref=? OR target_doc_ref=? ORDER BY id DESC LIMIT ?",
                    (ref, ref, limit)):
                if (other, int(r["id"])) in seen_ids:
                    continue
                seen_ids.add((other, int(r["id"])))
                rows.append({"shard": other, **{k: r[k] for k in r.keys()}})
        finally:
            conn.close()
    rows.sort(key=lambda r: -int(r["id"]))
    links = []
    for row in rows[:limit]:
        src = _approved_item(row["source_doc_ref"], row["source_essence_id"], row["source_item"])
        tgt = _approved_item(row["target_doc_ref"], row["target_essence_id"], row["target_item"])
        stale = src is None or tgt is None
        conn = connect(row["shard"])
        try:
            review = _latest_review(conn, row["id"])
        finally:
            conn.close()
        review_out = None
        effective = False
        if review is not None:
            # レビューの効き: 失効しておらず、レビューを付けたときの両側の版の番号（epoch）が
            # 今と同じときだけ（K1 のレビューと同じ考え。知識を承認し直しても前の判断は戻らない）。
            effective = (not stale and src is not None and tgt is not None
                          and int(review["source_epoch"]) == src["epoch"]
                          and int(review["target_epoch"]) == tgt["epoch"])
            review_out = {"decision": review["decision"], "note": review["note"],
                          "reviewer": review["reviewer"], "at": review["created"], "stale": not effective}
        links.append({
            "link_ref": f"{row['shard']}:{row['id']}", "basis": "inferred",
            "source": src, "target": tgt, "note": row["note"],
            "created_by": row["created_by"], "created": row["created"],
            "stale": stale,
            "review": review_out,
            "review_status": (review["decision"] if effective else "unreviewed"),
        })
    return {"links": links}


def review(link_ref: str, decision: str, note: str = "", reviewer: str = "") -> dict:
    """リンクへの人のレビュー（承認・却下）を追記する（消さない・書き換えない）。

    失効したリンクには付けられない（どちらかの知識が今の版で承認済みでなくなっている）。
    承認しても basis は inferred のまま（推論が事実にならない。統合の決まり 1）。
    レビューには両側の版の番号（epoch）を記録する: 知識が承認し直された（版の置き換えが
    起きた）あとは前の判断が効かない（K1 のレビューと同じ考え）。
    """
    if decision not in DECISIONS:
        raise store.KnowledgeError("レビューは承認・却下のどちらかです")
    shard, link_id = parse_link_ref(link_ref)
    conn = connect(shard)
    try:
        row = conn.execute("SELECT * FROM knowledge_links WHERE id=?", (int(link_id),)).fetchone()
        if row is None:
            raise store.KnowledgeError("リンクがありません")
        src = _approved_item(row["source_doc_ref"], row["source_essence_id"], row["source_item"])
        tgt = _approved_item(row["target_doc_ref"], row["target_essence_id"], row["target_item"])
        if src is None or tgt is None:
            raise store.KnowledgeError(
                "どちらかの知識が失効しているためレビューできません。先にその文献の知識を新しい版で承認してください")
        conn.execute(
            "INSERT INTO knowledge_link_reviews(link_id, decision, note, reviewer, source_epoch, "
            "target_epoch, created) VALUES(?,?,?,?,?,?,?)",
            (int(link_id), decision, (note or "")[:1000], (reviewer or "")[:64],
             int(src["epoch"]), int(tgt["epoch"]), _now()))
        conn.commit()
    finally:
        conn.close()
    return {"link_ref": link_ref, "decision": decision, "reviewer": (reviewer or "")[:64]}
