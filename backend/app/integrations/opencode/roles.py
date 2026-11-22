"""OpenCode の「役割ごとのモデル」を決める 1 か所（本番の session と開発の作業場の両方が使う）。

利用者の依頼（2026-11-22）: 計画・開発・調査・簡易検証・最終検証・評価に別々のモデルを選べるようにしたい。
同じモデルの役割が続くときは載せ替えない（ゲートウェイは起動中のモデルをそのまま使う）。全部同じでも、
全部違うモデルでもよい（2026-11-22 利用者「変な制限は設けなくて良い」）。ローカルのモデルは GPU で同時に
1 つしか動かせないが、ゲートウェイが要求を順番に受けて載せ替える（生成中の要求は最後まで返し、割り込んだ要求は
待ってから載せ替える。2026-11-22 Opus の実測）。同じ段に別のローカルがあれば、載せ替えで遅くなる旨を知らせるだけ。

役割（6）と段（3）:
  段 1 計画      plan
  段 2 作る      build / research / quick_check   （調査・簡易検証は、作るモデルの子エージェントが並んで動きうる）
  段 3 確かめる  final_check / evaluate           （最終検証と評価は並んで動きうる）
段をまたぐ載せ替えは許す（順番に動くため。ロードはゲートウェイが呼ばれたときに行う）。

決まり:
- どの役割にもどのモデルでも選べる（制限しない）。同じ段に別のローカルがあれば、お知らせ（notes）を出すだけ。
- 秘密（confidential / local_only）では、外部が選ばれていても全役割をローカルへ落とす。
- 最終検証が開発と同じモデルなら注意を出す（保存は止めない）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

ROLES = ("plan", "build", "research", "quick_check", "final_check", "evaluate")
STAGE_OF = {"plan": 1, "build": 2, "research": 2, "quick_check": 2, "final_check": 3, "evaluate": 3}
STAGE_NAMES = {1: "① 計画", 2: "② 作る", 3: "③ 確かめる"}
ROLE_LABELS = {
    "plan": "計画", "build": "開発", "research": "調査", "quick_check": "簡易検証",
    "final_check": "最終検証", "evaluate": "評価",
}
# 子エージェントの名前（plan・build は OpenCode の組み込みの主エージェント、final_check は reviewer 系）
SUBAGENT_NAMES = {"research": "researcher", "quick_check": "quick-checker", "evaluate": "evaluator"}
# 既定を持つ側が、同じ段の明示された別のローカルに合わせてよい役割（主の plan・build は黙って変えない）
_ADOPTABLE = frozenset({"research", "quick_check", "final_check", "evaluate"})

LOCAL_SWAP = "同時に動きうる役割に別のローカルのモデルがあります。並んで動くときは順番に載せ替えるので、待ち時間が増えます"
SAME_AS_BUILD = "最終検証が開発と同じモデルです。別の目で見られません"


class RolesError(ValueError):
    """（互換のために残す。いまは送出しない）"""


def is_local(model: str) -> bool:
    """ローカル（ControlDeck のゲートウェイが載せるモデル）か。cloud: と、controldeck/ 以外の provider/モデル は外部。"""
    value = str(model or "").strip()
    if value.startswith("cloud:"):
        return False
    return "/" not in value or value.startswith("controldeck/")


@dataclass
class Resolution:
    models: dict[str, str] = field(default_factory=dict)
    explicit: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)

    def view(self) -> dict[str, dict]:
        return {role: {"model": self.models[role], "explicit": role in self.explicit,
                       "stage": STAGE_OF[role]} for role in ROLES}


def _clean(roles: Mapping | None) -> dict[str, str]:
    roles = roles or {}
    return {role: str(roles.get(role) or "").strip() for role in ROLES}


def _label(role: str, res: Resolution) -> str:
    return f"{ROLE_LABELS[role]}（{'選択' if role in res.explicit else '既定'}: {res.models[role]}）"


def _settle(stage: int, res: Resolution, strict: bool = False) -> None:
    """同じ段に別のローカルのモデルがあれば知らせる（変えない・断らない）。strict は互換のために受けるだけ。"""
    names = [r for r in ROLES if STAGE_OF[r] == stage and is_local(res.models[r])]
    if len({res.models[r] for r in names}) > 1:
        detail = "・".join(f"{ROLE_LABELS[r]}＝{res.models[r]}" for r in names)
        res.notes.append(f"{STAGE_NAMES[stage]}（{detail}）: {LOCAL_SWAP}")


def resolve(roles: Mapping | None, *, session_model: str, first_external: str = "", legacy_review: str = "",
            local_only: bool = False, local_model: str = "", strict: bool = False) -> Resolution:
    """役割ごとの実際のモデルを決める。

    既定: 調査・簡易検証は開発と同じ。開発・計画の未設定はセッションのモデル。最終検証・評価は計画と同じ
    （計画が未設定なら first_external＝外部の最初のモデル、それも無ければ開発と同じ）。
    legacy_review は旧設定 review_model（最終検証の指定として読む）。
    strict は互換のために受けるだけ（制限はしない。同じ段の別のローカルは notes で知らせる）。
    local_only は秘密: 外部が選ばれていても、全役割をローカル（local_model、無ければセッションのモデル）へ落とす。
    """
    raw = _clean(roles)
    if not raw["final_check"]:
        raw["final_check"] = str(legacy_review or "").strip()
    session = str(session_model or "").strip()
    external = "" if local_only else str(first_external or "").strip()
    res = Resolution(explicit={role for role in ROLES if raw[role]})
    build = raw["build"] or session
    checker = raw["plan"] or external or build
    res.models = {
        "plan": raw["plan"] or session,
        "build": build,
        "research": raw["research"] or build,
        "quick_check": raw["quick_check"] or build,
        "final_check": raw["final_check"] or checker,
        "evaluate": raw["evaluate"] or checker,
    }
    if local_only:
        fallback = str(local_model or "").strip() or (session if is_local(session) else "auto")
        for role in ROLES:
            if not is_local(res.models[role]):
                res.models[role] = fallback
        res.notes.append("秘密のため、全役割をローカルのモデルにしました")
    for stage in (1, 2, 3):
        _settle(stage, res, strict)
    if res.models["final_check"] == res.models["build"]:
        res.notes.append(SAME_AS_BUILD)
    return res
