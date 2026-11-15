"""変更の危険度の区分（L0 / L1 / L2）。方針は docs/POLICY-autonomous-development.md。

利用者の判断を減らすため、変更ごとの「誰が判断するか」を機械的に決める。区分は
変更されたファイルの場所（paths）と、変更された行の内容（patterns）で決める。
規則は config/change-policy/<対象>.json。

- L0: 自動（文書・テスト・CodeDEV のアプリ）
- L1: 自動＋事後報告（関門を通れば Release まで進め、本番は自動適用・自動で戻す）
- L2: 利用者の承認が要る（安全の境界・利用者データ・更新や隔離の仕組みそのもの）

迷うものは上の区分に寄せる。1 ファイルの区分は当たった規則の最大、どれにも当たらなければ
既定。内容の規則は、場所で L0（文書・テスト）と決まったファイルには当てない——テストは
csrf や権限の名前を当然含むので、当てると全部が L2 になって区分の意味が無くなる。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TIERS = ("L0", "L1", "L2")
POLICY_DIR = Path(__file__).resolve().parents[3] / "config" / "change-policy"


class PolicyError(ValueError):
    pass


def _rank(tier: str) -> int:
    if tier not in TIERS:
        raise PolicyError(f"区分が不正です: {tier}")
    return TIERS.index(tier)


def _glob(pattern: str) -> re.Pattern[str]:
    """`**` は階層を跨ぎ、`*` は跨がない。`/` を含まない型はファイル名だけで照らす。"""
    out = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if pattern.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
            continue
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
            continue
        out.append("[^/]*" if char == "*" else "[^/]" if char == "?" else re.escape(char))
        index += 1
    return re.compile("".join(out) + r"\Z")


def load(path: str | Path) -> dict:
    try:
        policy = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyError(f"区分の規則を読めません: {path}") from exc
    _rank(policy.get("default", "L1"))
    for rule in policy.get("rules", []):
        _rank(rule["tier"])
        rule["_paths"] = [(_glob(p), "/" not in p) for p in rule.get("paths", [])]
        rule["_patterns"] = [re.compile(p, re.IGNORECASE) for p in rule.get("patterns", [])]
        # 内容の規則を当てる場所。画面の文言は境界を強制しないので、強制する側だけに当てる。
        rule["_scope"] = [(_glob(p), "/" not in p) for p in rule.get("scope", [])]
    return policy


def _path_matches(rule: dict, path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(regex.match(name if basename_only else path) for regex, basename_only in rule["_paths"])


def classify_file(policy: dict, path: str, changed_lines: list[str]) -> tuple[str, list[str]]:
    """1 ファイルの区分と、当たった理由。"""
    by_path = [rule for rule in policy["rules"] if rule["_paths"] and _path_matches(rule, path)]
    tier = max((rule["tier"] for rule in by_path), key=_rank) if by_path else policy.get("default", "L1")
    reasons = [rule["why"] for rule in by_path if rule["tier"] == tier]
    if tier != "L0":
        text = "\n".join(changed_lines)
        for rule in policy["rules"]:
            in_scope = not rule["_scope"] or any(
                regex.match(path.rsplit("/", 1)[-1] if basename_only else path) for regex, basename_only in rule["_scope"])
            if in_scope and rule["_patterns"] and any(p.search(text) for p in rule["_patterns"]) and _rank(rule["tier"]) >= _rank(tier):
                if _rank(rule["tier"]) > _rank(tier):
                    tier, reasons = rule["tier"], []
                reasons.append(rule["why"] + "（変更内容）")
    return tier, reasons or ["既定"]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise PolicyError(f"git {args[0]} に失敗しました: {result.stderr.strip()[:300]}")
    return result.stdout


def changed(repo: Path, base: str, head: str) -> dict[str, list[str]]:
    """ファイルごとの変更行（追加・削除の両方。削除も境界を緩める変更になりうる）。"""
    files: dict[str, list[str]] = {}
    current = None
    for line in _git(repo, "diff", "-U0", "--no-color", "--no-renames", f"{base}...{head}").splitlines():
        if line.startswith("diff --git "):
            current = line.split(" b/", 1)[-1]
            files.setdefault(current, [])
        elif current and line[:1] in "+-" and not line.startswith(("+++", "---")):
            files[current].append(line[1:])
    return files


def classify(repo: str | Path, base: str, head: str = "HEAD", policy: str | Path | dict = "controldeck") -> dict:
    if isinstance(policy, dict):
        rules = policy
    else:
        candidate = Path(policy)
        rules = load(candidate if candidate.suffix == ".json" else POLICY_DIR / f"{policy}.json")
    files = changed(Path(repo), base, head)
    rows = []
    for path, lines in sorted(files.items()):
        tier, reasons = classify_file(rules, path, lines)
        rows.append({"path": path, "tier": tier, "reasons": reasons})
    overall = max((row["tier"] for row in rows), key=_rank) if rows else "L0"
    return {"policy": rules.get("name", ""), "tier": overall, "files": rows,
            "guarded": [row for row in rows if row["tier"] == "L2"]}


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="deck.sh change-class")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--policy", default="controldeck", help="controldeck / patentbuddy / codedev か JSON のパス")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(classify(args.repo, args.base, args.head, args.policy), ensure_ascii=False, indent=1))
    except PolicyError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
