"""ローカル LLM の同時実行数（並列のスロット）から、裏の作業に回せる本数を決める。

根拠: 2026-11-23 利用者「並列数が 5 並列で実行できるんであれば、1 並列でメインの会話を継続して、あとの 4 並列で調査の要約とか、
そういうタスクを分けて、極力その並列数を無駄に使わないように。並列数は事前に設定したものを読み取って。並列 1 の場合は従来通り」。
2026-11-15 の実測: Radiance・llama.cpp は並列で合計が伸びる。Strata は伸びない。Maya-S は並列で起動しない。

決め方:
  - 伸びないランタイム（Strata・Maya-S）→ 1 本
  - 伸びるランタイム（Radiance・llama.cpp）→ 登録のスロット数 n。n が 1 なら 1 本、n が 2 以上なら会話に 1 つ残して n-1 本
  - 利用者の上書き（調査の設定 read_parallel。0 は自動）があればそれを使う（スロット数・上限 MAX_HELPERS を超えない）
  - ローカルのモデルを特定できない・設定を読めないときは 1 本（安全側）。外部の API（ローカルでない宛先）はスロットの概念が無いので従来の本数
設計: docs/design-parallel-roles.md
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

from app.models_mgmt.container_runtime import CONTAINER_RUNTIMES

logger = logging.getLogger("control_deck.models_mgmt.parallel_slots")

SCALING_RUNTIMES = frozenset({*CONTAINER_RUNTIMES, "llama.cpp"})   # 並列で合計が伸びるもの（実測）
MAX_HELPERS = 8                                           # 裏の作業の上限（設定の上書きの上限と同じ）
EXTERNAL_DEFAULT = 4                                      # 外部 API はスロットを持たないので従来の本数
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def slots_of(instance: dict) -> int:
    """そのモデルに同時に流せる本数（伸びないランタイムは 1。読めない値は 1）。"""
    if str(instance.get("runtime") or "") not in SCALING_RUNTIMES:
        return 1
    try:
        return max(1, int(instance.get("n_parallel") or 1))
    except (TypeError, ValueError):
        return 1


def helper_slots(slots: int) -> int:
    """会話に 1 つ残した残り。スロットが 1 なら 1 本ずつ（従来どおり）。"""
    slots = max(1, int(slots))
    return 1 if slots <= 1 else min(MAX_HELPERS, slots - 1)


def resolve_instance(base_url: str, model: str) -> dict | None:
    """宛先（ポート）かモデルの別名からローカルのモデル設定を探す。見つからなければ None。"""
    from app.models_mgmt import local_llm

    try:
        port = urlsplit(base_url or "").port
        if port:
            item = local_llm.instance_for_port(port)
            if item is not None:
                return item
        return local_llm.find(model) if model else None
    except Exception as exc:  # noqa: BLE001 - 設定が読めなくても調査は止めない
        logger.info("並列数を読めませんでした（1 本にします）: %s", type(exc).__name__)
        return None


def is_local(base_url: str) -> bool:
    try:
        return urlsplit(base_url or "").hostname in _LOCAL_HOSTS
    except ValueError:
        return False


def background_parallel(base_url: str, model: str, override: int = 0) -> int:
    """調査で LLM を呼ぶ処理（観点ごとの要約・判定）を同時に何本流すか。override は設定の値（0 は自動）。"""
    item = resolve_instance(base_url, model)
    if item is None:
        if override > 0 and not is_local(base_url):
            return min(MAX_HELPERS, override)
        return 1 if is_local(base_url) or not base_url else EXTERNAL_DEFAULT
    slots = slots_of(item)
    if override > 0:
        return max(1, min(override, slots, MAX_HELPERS))
    return helper_slots(slots)
