"""L2 の承認（docs/POLICY-autonomous-development.md）。利用者はワンタップで承認・却下する。

統括（開発エージェント）が `./deck.sh approval request` で依頼を作ると、通知先へ知らせ、
ControlDeck のホーム画面に「承認待ち」を出す。承認されたら ControlDeck が PR をマージする。

- 承認の対象は「その時点のコミット」。依頼の後に PR が変わっていたらマージしない
  （`gh pr merge --match-head-commit`）。見ていない変更を通さないため。
- 依頼は data/approvals/<id>.json に置く（小さく、DB の移行を要らない形）。
- 依頼を作れるのはこの PC の利用者（CLI）だけ。Web からは承認・却下だけ。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from app.config import data_dir

_ID = re.compile(r"^[0-9a-f]{16}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
STATES = ("pending", "merging", "merged", "rejected", "failed")


class ApprovalError(ValueError):
    pass


def _root() -> Path:
    path = data_dir().resolve() / "approvals"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _path(approval_id: str) -> Path:
    if not _ID.fullmatch(approval_id):
        raise ApprovalError("承認の id が不正です")
    return _root() / f"{approval_id}.json"


def _write(record: dict) -> dict:
    path = _path(record["id"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return record


def get(approval_id: str) -> dict:
    try:
        return json.loads(_path(approval_id).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApprovalError("承認が見つかりません") from exc


def list_all(*, include_done: bool = False, limit: int = 50) -> list[dict]:
    rows = []
    for path in sorted(_root().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if include_done or record.get("status") in ("pending", "merging"):
            rows.append(record)
        if len(rows) >= limit:
            break
    return rows


def create(*, repo: str, pr: int, head_sha: str, title: str, summary: str, risks: list[str],
           tier: str = "L2") -> dict:
    if not _REPO.fullmatch(repo) or not isinstance(pr, int) or pr < 1 or not _SHA.fullmatch(head_sha):
        raise ApprovalError("repo / pr / head_sha が不正です")
    title, summary = str(title).strip()[:200], str(summary).strip()[:4000]
    if not title or not summary:
        raise ApprovalError("題名と要約が要ります（利用者は差分を読まずに判断する）")
    for record in list_all():
        if record["repo"] == repo and record["pr"] == pr and record["head_sha"] == head_sha:
            return record
    record = {
        "id": secrets.token_hex(8), "kind": "github_pr_merge", "repo": repo, "pr": pr,
        "head_sha": head_sha, "title": title, "summary": summary,
        "risks": [str(item)[:300] for item in risks][:20], "tier": tier,
        "status": "pending", "created_at": time.time(), "decided_at": None, "decided_by": "", "result": "",
    }
    return _write(record)


def _gh() -> str:
    gh = shutil.which("gh")
    if not gh:
        raise ApprovalError("gh（GitHub CLI）がありません")
    return gh


def approve(approval_id: str, *, user: str) -> dict:
    """承認して PR をマージする。依頼の後に PR が変わっていたらマージしない。"""
    record = get(approval_id)
    if record["status"] != "pending":
        raise ApprovalError("この依頼は既に処理されています")
    record.update(status="merging", decided_at=time.time(), decided_by=user)
    _write(record)
    result = subprocess.run(
        [_gh(), "pr", "merge", str(record["pr"]), "--repo", record["repo"], "--merge",
         "--match-head-commit", record["head_sha"]],
        capture_output=True, text=True, timeout=120, check=False, stdin=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        record.update(status="merged", result="マージしました")
        if record["repo"] == RELEASE_REPO:
            # 承認されたものは、そのまま本番まで進める: 次の版の正式 Release を出し、
            # 本番の自動適用に「使われていなければすぐ」と伝える（10 分の待ちを飛ばす）。
            try:
                tag = _release(record)
                record.update(release_tag=tag, result=f"マージし、{tag} を出しました。使われていなければすぐ本番へ適用します")
                from app.maintenance import auto_update

                auto_update.request_prompt_apply(tag)
            except ApprovalError as exc:
                record.update(result=f"マージしました（Release は出せませんでした: {exc}）")
    else:
        # gh の出力は URL などを含みうるので、利用者へは固定の文言だけを出す。
        changed = "head" in (result.stderr or "").lower() and "match" in (result.stderr or "").lower()
        record.update(status="failed", result="PR が依頼の後に変わったためマージしませんでした" if changed
                      else "マージできませんでした（PR の状態を確認してください）")
    return _write(record)


RELEASE_REPO = "souten-yd/ControlDeck"
_TAG = re.compile(r"^ControlDeck_v(\d+)\.(\d+)$")


def _release(record: dict) -> str:
    """マージ後の main から次の版の正式 Release を出す（ControlDeck 本体だけ）。"""
    def gh(*args: str) -> str:
        out = subprocess.run([_gh(), *args, "--repo", RELEASE_REPO], capture_output=True, text=True,
                             timeout=120, check=False, stdin=subprocess.DEVNULL)
        if out.returncode != 0:
            raise ApprovalError("GitHub の操作に失敗しました")
        return out.stdout.strip()

    merged = gh("pr", "view", str(record["pr"]), "--json", "mergeCommit", "-q", ".mergeCommit.oid")
    if not _SHA.fullmatch(merged):
        raise ApprovalError("マージのコミットを確認できません")
    tags = [m for m in (_TAG.match(line.split("\t")[0].strip()) for line in
                         gh("release", "list", "--limit", "50", "--json", "tagName", "-q", ".[].tagName").splitlines()) if m]
    major, minor = max(((int(m.group(1)), int(m.group(2))) for m in tags), default=(0, 0))
    tag = f"ControlDeck_v{major}.{minor + 1}"
    notes = (f"## ControlDeck v{major}.{minor + 1}\n\n### 主な変更\n- **{record['title']}**（#{record['pr']}）\n\n"
             f"{record['summary']}\n\n### 判断\n- 利用者が承認（L2）: {', '.join(record.get('risks') or [])}\n\n"
             "### 更新方法\n使われていなければすぐ本番へ自動で適用されます。\n\n"
             "### 前の版へ戻すには\n更新後の確認に失敗すると自動で前の版へ戻ります。\n")
    gh("release", "create", tag, "--target", merged, "--title", f"ControlDeck v{major}.{minor + 1}", "--notes", notes)
    return tag


def reject(approval_id: str, *, user: str, reason: str = "") -> dict:
    record = get(approval_id)
    if record["status"] != "pending":
        raise ApprovalError("この依頼は既に処理されています")
    record.update(status="rejected", decided_at=time.time(), decided_by=user, result=str(reason)[:500])
    return _write(record)


def main(argv: list[str]) -> int:
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(prog="deck.sh approval")
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request", help="L2 の承認を依頼する（通知を送る）")
    request.add_argument("--repo", required=True)
    request.add_argument("--pr", type=int, required=True)
    request.add_argument("--title", required=True)
    request.add_argument("--summary", required=True)
    request.add_argument("--risk", action="append", default=[])
    request.add_argument("--head-sha", default="")
    sub.add_parser("list")
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            print(json.dumps(list_all(include_done=True), ensure_ascii=False, indent=1))
            return 0
        head = args.head_sha
        if not head:
            head = subprocess.run([_gh(), "pr", "view", str(args.pr), "--repo", args.repo, "--json", "headRefOid",
                                   "-q", ".headRefOid"], capture_output=True, text=True, timeout=60).stdout.strip()
        record = create(repo=args.repo, pr=args.pr, head_sha=head, title=args.title,
                        summary=args.summary, risks=args.risk)
        from app.maintenance.auto_update import notify

        from app.config import get_config

        public = get_config().server.public_url.rstrip("/")
        where = f"{public}/?approvals=1" if public else "ControlDeck を開くと承認の画面が出ます"
        asyncio.run(notify(f"承認待ち: {record['title']}", f"{record['summary'][:300]}\n{where}"))
        print(json.dumps(record, ensure_ascii=False, indent=1))
    except ApprovalError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
