"""ローカル LLM の「載せ替えの調停役」。

目的: 載せ替え（別のモデルを載せるための解放と起動）を 1 か所で決める。
今までは要求ごとに gateway_chat → prepare_switch → ensure_ready が走り、別々のモデルの要求が
互いに相手を降ろそうとした。解放を「見送る」・起動の操作ロックが取れず失敗する、などで、待てば通る
はずの要求に 503 を返していた（2026-11-22、箱の OpenCode が開発 qwen-flash-next と調査
maya-deepseek-xs を交互に呼んだとき、中継のログで 300〜660 秒待って 503・500）。

決め方（利用者の依頼 2026-11-22・同日の追加の指示）:
  1. 載っているモデルの要求は待たずに実行する。同じモデルの並列は Radiance だけ（登録の max_num_seqs まで）。
     Strata・Maya-S・llama.cpp の chat は 1 本ずつ。
  2. 別のモデルが必要な要求は待ち行列へ。次を点数で比べる（どの要求も断らない・消さない）:
       点数 = (待っている数 + 最古の待ち秒 / AGING_SECONDS) ÷ 載せ替えの見込み秒
     載っているモデルを「続ける」点数は、載せ替え 0 なので見込み秒の代わりに小さな下限 MIN_COST 秒を使う
     （続けるのが自然に有利）。待ち時間で別のモデルの点数が少しずつ上がり、続ける点数を超えたら、
     実行中が終わり次第そのモデルへ切り替える。切り替えると決めた後に来た元のモデルの要求も、待ち行列へ入れて
     点数どおりに後で実行する。
     飢餓の保証: 続ける点数 = min(待ち数, CONTINUE_CAP) ÷ MIN_COST で頭打ち（最大 2/10 = 0.2）。
     別のモデルの要求 1 件の点数 (1 + t/aging) ÷ 見込み秒 は t とともに増えるので、
       t > aging × (見込み秒 × CONTINUE_CAP / MIN_COST − 1)
     で必ず上回る。既定（aging 30 秒・見込み 60 秒）なら 330 秒。到着が処理より速い過負荷でも同じ。
     上回ったら実行中が終わり次第（実行中は切らない）載せ替える。
  3. 実行中の要求は切らない。降ろすのは実行中が 0 のときだけ。
  4. 待っている要求には 503 を返さず待たせる。載せ替え自体が失敗したら、そのモデルの待ちに理由つきの 503。
     失敗が続く（2 回）と少し間を置く。

既知の方法との関係: Ollama・llama-swap は到着順に載せ替える（交互の要求のたびに載せ替わる）。
ServerlessLLM は「読み込みが速い（重みが近い）モデルを先にする」、JointServe は「載せ替えの回数を減らす」。
ここは両方を点数に入れ、飢餓を防ぐ上限を足した。

ロック: ここは asyncio の単一の駆動タスクだけが状態を決める（ロックを持ったまま外へ待たない）。
載せ替えは別タスク（実行中が 0 のときだけ起動）で、その間は他の要求を入れない。要求の側のロック
（_model_startup）は載せ替えが終わってから取るので、二重のロックで行き詰まらない。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import statistics
import time
import weakref
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

logger = logging.getLogger("control_deck.swap_scheduler")

MIN_COST_SECONDS = 10.0
# 続ける点数の待ち数の項の上限。過負荷で待ちが増え続けても続ける点数は頭打ちになり、
# 別のモデルの最古の要求は待ち時間が伸びれば必ず上回る（飢餓の保証）。
CONTINUE_CAP = 2
DEFAULT_AGING_SECONDS = 30.0
DEFAULT_LOAD_SECONDS = 60.0
FAILURES_BEFORE_COOLDOWN = 2
FAILURE_COOLDOWN_SECONDS = 30.0
SWAP_LOAD_TIMEOUT_SECONDS = 600
WARM_WINDOW_SECONDS = 900.0
PREFETCH_HEADROOM_BYTES = 8 * 1024**3
POLL_SECONDS = 1.0
SNAPSHOT_TTL_SECONDS = 1.0
LARGE_RUNTIMES = ("maya-s", "strata", "radiance")
CLIENT_GONE = "クライアントが切断しました"


@dataclass(frozen=True)
class Settings:
    aging_seconds: float = DEFAULT_AGING_SECONDS
    prefetch: bool = True


@dataclass(frozen=True)
class ModelInfo:
    alias: str
    runtime: str = ""
    role: str = "llm"
    loaded: bool = False
    parallel: int = 1  # 同時に実行できる本数（Radiance だけ 2 以上）
    model_path: str = ""
    warm_seconds: float | None = None  # load_profiles の実測（warm / cold）
    cold_seconds: float | None = None
    recently_unloaded: bool = False


@dataclass(frozen=True)
class Snapshot:
    models: dict[str, ModelInfo]
    large_loaded: bool = False

    def needs_swap(self, alias: str) -> bool:
        """このモデルの要求が、他のモデルの解放・起動を要するか。"""
        info = self.models.get(alias)
        if info is None or info.role != "llm" or info.loaded:
            return False
        # 大きなランタイムが載っていなければ llama.cpp は並べて起動できる（今までどおり broker が決める）。
        return not (info.runtime == "llama.cpp" and not self.large_loaded)

    def bypass(self, alias: str) -> bool:
        info = self.models.get(alias)
        return info is None or info.role != "llm"


@dataclass(eq=False)
class Ticket:
    alias: str
    kind: str
    enqueued_at: float
    scheduler: "SwapScheduler | None" = None
    fut: asyncio.Future | None = None
    admitted: bool = False
    released: bool = False
    counted: bool = False  # 実行中として数えるか（補助のモデル・/load は数えない）
    needs_load: bool = False  # /load だけ: 載っていないので呼び出し側が載せる
    waited: float = 0.0

    def release(self) -> None:
        if self.scheduler is not None:
            self.scheduler._release(self)


def _noop_ticket(alias: str, kind: str) -> Ticket:
    return Ticket(alias, kind, 0.0, admitted=True, released=True)


# ── 既定の部品（テストは差し替える） ───────────────────────────────────


_snapshot_cache: tuple[float, Snapshot] | None = None


def invalidate_snapshot() -> None:
    global _snapshot_cache
    _snapshot_cache = None


def default_snapshot() -> Snapshot:
    """全ローカルランタイムのモデルの載っている状態。短い時間だけ使い回す（systemctl を毎回呼ばない）。"""
    global _snapshot_cache
    now = time.monotonic()
    if _snapshot_cache and now - _snapshot_cache[0] < SNAPSHOT_TTL_SECONDS:
        return _snapshot_cache[1]
    from app.models_mgmt import local_llm

    models: dict[str, ModelInfo] = {}
    large = False
    telemetry = None
    try:
        from app.resources.broker import broker

        telemetry = broker.telemetry
    except Exception:  # noqa: BLE001 - 実測が読めなくても調停は続ける
        telemetry = None
    for item in local_llm.list_instances():
        alias = str(item.get("alias") or "")
        if not alias:
            continue
        runtime = str(item.get("runtime") or "")
        role = str(item.get("role") or "llm")
        loaded = bool(item.get("loaded"))
        if loaded and role == "llm" and runtime in LARGE_RUNTIMES:
            large = True
        warm = cold = None
        unloaded = False
        if role == "llm" and not loaded and telemetry is not None:
            try:
                key = local_llm.residency_key(item)
                warm = telemetry.expected_load_seconds(key, warm=True, strict=True)
                cold = telemetry.expected_load_seconds(key, warm=False, strict=True)
                unloaded = telemetry.recently_unloaded(key)
            except Exception:  # noqa: BLE001
                pass
        # 同じモデルを並列に実行するのは Radiance だけ（登録の max_num_seqs）。ほかは 1 本ずつ。
        parallel = 1
        if runtime == "radiance":
            try:
                parallel = max(1, int(item.get("n_parallel") or 1))
            except (TypeError, ValueError):
                parallel = 1
        models[alias] = ModelInfo(alias, runtime, role, loaded, parallel, str(item.get("model_path") or ""),
                                  warm, cold, unloaded)
    snap = Snapshot(models, large)
    _snapshot_cache = (now, snap)
    return snap


def default_settings() -> Settings:
    try:
        from app.models_mgmt.runtime_policy import get_policy

        cfg = get_policy().swap_scheduler
        return Settings(float(cfg.aging_seconds), bool(cfg.prefetch))
    except Exception:  # noqa: BLE001 - 設定が読めなくても既定で動く
        return Settings()


async def default_loader(alias: str) -> None:
    from app.models_mgmt import gateway

    await gateway._swap_load(alias)


# ── 先読み（重みのファイルをページキャッシュへ） ─────────────────────────


def mem_available_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


_SHARD = re.compile(r"^(?P<head>.*)-(?P<n>\d{5})-of-(?P<total>\d{5})\.gguf$")


def weight_files(model_path: str, *, limit: int = 512) -> list[Path]:
    """モデルの重みのファイル。GGUF の分割（-00001-of-0000N）・フォルダ（Maya）・単一のファイルに対応。"""
    if not model_path:
        return []
    path = Path(model_path)
    try:
        if path.is_dir():
            files = [p for p in path.rglob("*") if p.is_file() and p.stat().st_size >= 1 << 20]
            return sorted(files)[:limit]
        if not path.is_file():
            return []
        match = _SHARD.match(path.name)
        if match:
            siblings = sorted(path.parent.glob(f"{match['head']}-*-of-{match['total']}.gguf"))
            return siblings or [path]
        return [path]
    except OSError:
        return []


def plan_prefetch(model_path: str, available: int | None) -> tuple[list[Path], int, str]:
    """先読みするファイル・合計の大きさ・しない理由（空ならする）。"""
    files = weight_files(model_path)
    if not files:
        return [], 0, "重みのファイルが見つかりません"
    try:
        size = sum(p.stat().st_size for p in files)
    except OSError:
        return [], 0, "重みの大きさを読めません"
    if available is None:
        return files, size, "メモリの空きを読めません"
    if available < size + PREFETCH_HEADROOM_BYTES:
        return files, size, f"メモリが足りません（空き {available / 1024**3:.1f} GiB < 重み {size / 1024**3:.1f} GiB + 余裕 8 GiB）"
    return files, size, ""


def run_prefetch(files: list[Path]) -> int:
    """POSIX_FADV_WILLNEED で読み込みを先に始めさせる。読めたファイルの数を返す。失敗は無視して続ける。"""
    done = 0
    for path in files:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_WILLNEED)
            done += 1
        except (OSError, AttributeError):
            pass
        finally:
            os.close(fd)
    return done


# ── 調停役 ───────────────────────────────────────────────────────────


class SwapScheduler:
    def __init__(self, *, snapshot: Callable[[], Snapshot] | None = None,
                 loader: Callable[[str], Awaitable[None]] | None = None,
                 settings: Callable[[], Settings] | None = None,
                 mem_available: Callable[[], int | None] | None = None,
                 prefetcher: Callable[[list[Path]], int] | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 poll_seconds: float = POLL_SECONDS,
                 cooldown_seconds: float = FAILURE_COOLDOWN_SECONDS,
                 min_cost_seconds: float | None = None):
        self._snapshot = snapshot or default_snapshot
        self._loader = loader or default_loader
        self._settings = settings or default_settings
        self._mem_available = mem_available or mem_available_bytes
        self._prefetcher = prefetcher or run_prefetch
        self._clock = clock
        self._poll = poll_seconds
        self._cooldown = cooldown_seconds
        self._min_cost = MIN_COST_SECONDS if min_cost_seconds is None else min_cost_seconds
        self._waiters: list[Ticket] = []
        self._running: dict[str, int] = {}
        self._swapping: str | None = None
        self._swap_started = 0.0
        self._swap_task: asyncio.Task | None = None
        self._driver: asyncio.Task | None = None
        self._event = asyncio.Event()
        self._failures: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._measured: dict[tuple[str, bool], deque[float]] = {}
        self._unloaded_at: dict[str, float] = {}
        self._prefetched_at: dict[str, float] = {}
        self._prefetch_task: asyncio.Task | None = None
        self._prefetch_tried: dict[str, float] = {}
        self._prefetch_info: dict[str, Any] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=20)
        self._next: dict[str, Any] | None = None
        self._latched: str | None = None
        self._last_snapshot: Snapshot | None = None
        self._last_settings = Settings()
        self.swap_count = 0

    # ── 入口 ──────────────────────────────────────────────

    async def acquire(self, alias: str, *, request: Any = None, kind: str = "chat") -> Ticket:
        """このモデルで実行してよい順番が来るまで待つ。戻ったら ticket.release() を必ず呼ぶ。

        待たされても 503 にはしない。クライアントが切れたら待ち行列から外れる（499）。
        載せ替えが失敗したときだけ、そのモデルの待ちに理由つきの 503 を返す。
        """
        loop = asyncio.get_running_loop()
        ticket = Ticket(alias, kind, self._clock(), self, loop.create_future())
        ticket.fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        self._waiters.append(ticket)
        self._kick()
        try:
            while not ticket.fut.done():
                await asyncio.wait({ticket.fut}, timeout=0.25)
                if ticket.fut.done():
                    break
                if request is not None and await request.is_disconnected():
                    logger.info("切り替え待ちの要求を外しました（クライアントが切断）: %s", alias)
                    raise HTTPException(status_code=499, detail=CLIENT_GONE)
            ticket.fut.result()
        except BaseException:
            self._abandon(ticket)
            raise
        ticket.waited = self._clock() - ticket.enqueued_at
        return ticket

    def running_total(self) -> int:
        return sum(self._running.values())

    # ── 状態の更新（同期・await を挟まない） ─────────────────

    def _abandon(self, ticket: Ticket) -> None:
        if ticket in self._waiters:
            self._waiters.remove(ticket)
        if ticket.admitted:
            self._release(ticket)
        self._kick()

    def _release(self, ticket: Ticket) -> None:
        if ticket.released:
            return
        ticket.released = True
        if ticket.counted:
            left = self._running.get(ticket.alias, 0) - 1
            if left > 0:
                self._running[ticket.alias] = left
            else:
                self._running.pop(ticket.alias, None)
        self._kick()

    def _grant(self, ticket: Ticket, *, count: bool, needs_load: bool = False) -> None:
        if ticket in self._waiters:
            self._waiters.remove(ticket)
        if ticket.fut.done():
            return
        ticket.admitted = True
        ticket.counted = count
        ticket.needs_load = needs_load
        if count:
            self._running[ticket.alias] = self._running.get(ticket.alias, 0) + 1
        ticket.fut.set_result(None)

    def _fail(self, ticket: Ticket, detail: str, *, retry_after: int = 10) -> None:
        if ticket in self._waiters:
            self._waiters.remove(ticket)
        if ticket.fut.done():
            return
        logger.warning("切り替え待ちの要求に 503 を返します: model=%s 理由=%s", ticket.alias, detail)
        ticket.fut.set_exception(HTTPException(
            status_code=503, detail=detail, headers={"Retry-After": str(retry_after)}))

    # ── 駆動 ─────────────────────────────────────────────

    def _kick(self) -> None:
        self._event.set()
        if self._driver is None or self._driver.done():
            self._driver = asyncio.get_running_loop().create_task(self._drive())

    async def _drive(self) -> None:
        try:
            while True:
                self._event.clear()
                try:
                    await self._pump_once()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - 調停の不具合で要求を止めない（待たせず通す）
                    logger.exception("載せ替えの調停で例外。待っている要求をそのまま通します")
                    for ticket in list(self._waiters):
                        self._grant(ticket, count=True)
                    await asyncio.sleep(0.5)
                if not self._waiters and self._swapping is None:
                    if not self._event.is_set():
                        break
                    continue
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=self._poll)
                except TimeoutError:
                    pass
        finally:
            if self._driver is asyncio.current_task():
                self._driver = None

    async def _pump_once(self) -> None:
        snap = await asyncio.to_thread(self._snapshot)
        settings = await asyncio.to_thread(self._settings)
        self._decide(snap, settings)

    # ── 決める（同期） ────────────────────────────────────

    def _estimate(self, info: ModelInfo) -> tuple[float, bool]:
        """載せ替えの見込み秒と、メモリ上にある見込み（warm）か。"""
        now = self._clock()
        warm = (info.recently_unloaded
                or now - self._unloaded_at.get(info.alias, -1e18) <= WARM_WINDOW_SECONDS
                or now - self._prefetched_at.get(info.alias, -1e18) <= WARM_WINDOW_SECONDS)

        def own(flag: bool) -> float | None:
            values = self._measured.get((info.alias, flag))
            return statistics.median(values) if values else None

        order = [own(warm), info.warm_seconds if warm else info.cold_seconds,
                 own(not warm), info.cold_seconds if warm else info.warm_seconds]
        for value in order:
            if value is not None and value > 0:
                return float(value), warm
        return DEFAULT_LOAD_SECONDS, warm

    def _score(self, alias: str, waiters: list[Ticket], snap: Snapshot, settings: Settings,
               now: float) -> tuple[float, float]:
        info = snap.models[alias]
        seconds, _warm = self._estimate(info)
        oldest = max(now - w.enqueued_at for w in waiters)
        aging = max(settings.aging_seconds, 0.001)
        return (len(waiters) + oldest / aging) / max(seconds, 0.001), oldest

    def _pick(self, needers: list[Ticket], snap: Snapshot, settings: Settings,
              now: float) -> tuple[str, float, str]:
        groups: dict[str, list[Ticket]] = {}
        for ticket in needers:
            groups.setdefault(ticket.alias, []).append(ticket)
        scored = {a: self._score(a, g, snap, settings, now) for a, g in groups.items()}
        best = max(scored, key=lambda a: scored[a])  # 点数、同点は最も古い要求のモデル
        score, oldest = scored[best]
        seconds, warm = self._estimate(snap.models[best])
        return best, score, (f"待ち {len(groups[best])} 件・最長 {oldest:.0f} 秒・載せ替え見込み {seconds:.0f} 秒"
                             f"（{'メモリ上' if warm else '読み込み'}）・点数 {score:.3f}")

    def _continue_score(self, waiters: list[Ticket], settings: Settings, now: float) -> tuple[float, float]:
        """載っているモデルを続ける点数。載せ替えは 0 秒なので、見込み秒の代わりに MIN_COST を使う。

        待ち数の項は CONTINUE_CAP で頭打ち（過負荷でも別のモデルが必ず上回れる）。
        """
        if not waiters:
            return 0.0, 0.0
        oldest = max(now - w.enqueued_at for w in waiters)
        return min(len(waiters), CONTINUE_CAP) / self._min_cost, oldest

    def _decide(self, snap: Snapshot, settings: Settings) -> None:
        self._last_snapshot, self._last_settings = snap, settings
        now = self._clock()
        self._waiters = [w for w in self._waiters if not w.fut.done()]
        # 補助のモデル・設定に無いモデルは調停の対象外（今までどおり）。
        for ticket in list(self._waiters):
            if snap.bypass(ticket.alias):
                self._grant(ticket, count=False, needs_load=ticket.kind == "load")
        if self._swapping is not None or not self._waiters:
            if not self._waiters:
                self._next = None
                self._latched = None
            return
        # 起動の失敗が続いたモデルは、間を置く間は待たせず理由を返す。
        for ticket in list(self._waiters):
            until = self._cooldown_until.get(ticket.alias, 0.0)
            if snap.needs_swap(ticket.alias) and until > now:
                self._fail(ticket, f"モデル {ticket.alias} の起動に失敗が続いたため、{until - now:.0f} 秒後にもう一度試してください",
                           retry_after=max(1, int(until - now)))
        for ticket in list(self._waiters):
            if ticket.kind == "load" and not snap.needs_swap(ticket.alias):
                self._grant(ticket, count=False, needs_load=not snap.models[ticket.alias].loaded)
        needers = [w for w in self._waiters if snap.needs_swap(w.alias)]
        stay = [w for w in self._waiters if not snap.needs_swap(w.alias)]
        # 切り替えるか続けるかを同じ点数で比べる。決めたら、実行中が終わるまで元のモデルの新しい要求は待ち行列へ。
        if needers:
            target, switch_score, reason = self._pick(needers, snap, settings, now)
            if self._latched and not any(w.alias == self._latched for w in needers):
                self._latched = None  # 待っていた要求が外れた
            keep_score, keep_oldest = self._continue_score(stay, settings, now)
            if self._latched is None:
                oldest_switch = max(now - w.enqueued_at for w in needers if w.alias == target)
                if switch_score > keep_score or (switch_score == keep_score and oldest_switch > keep_oldest):
                    self._latched = target
            self._next = {"alias": target, "reason": reason, "continue_score": round(keep_score, 4),
                          "switch_score": round(switch_score, 4), "switching": self._latched is not None}
        else:
            self._latched = None
            self._next = None
        if self._latched is None:
            for ticket in list(stay):
                info = snap.models[ticket.alias]
                if self._running.get(ticket.alias, 0) >= max(1, info.parallel):
                    continue
                self._grant(ticket, count=True)
            return
        if self.running_total() > 0:
            self._maybe_prefetch(snap.models[self._next["alias"]], settings)
            return
        # 載せるモデルは実行中が 0 になった時点の点数で決め直す（切り替える決定だけを固定する）。
        target, _score, reason = self._pick(needers, snap, settings, now)
        self._latched = None
        self._start_swap(target, reason, snap, now)

    # ── 載せ替え ─────────────────────────────────────────

    def _start_swap(self, alias: str, reason: str, snap: Snapshot, now: float) -> None:
        waiting = sum(1 for w in self._waiters if w.alias == alias)
        before = [a for a, i in snap.models.items() if i.loaded and i.role == "llm"]
        self._swapping = alias
        self._swap_started = now
        invalidate_snapshot()
        self._swap_task = asyncio.get_running_loop().create_task(
            self._run_swap(alias, reason, before, waiting))

    async def _run_swap(self, alias: str, reason: str, before: list[str], waiting: int) -> None:
        started = self._clock()
        warm = self._estimate(self._last_snapshot.models[alias])[1] if self._last_snapshot else False
        error = ""
        try:
            await self._loader(alias)
        except asyncio.CancelledError:
            error = "載せ替えが取り消されました"
            raise
        except HTTPException as exc:
            error = str(exc.detail)
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or type(exc).__name__
        finally:
            seconds = self._clock() - started
            self._swapping = None
            self._swap_task = None
            invalidate_snapshot()
            self._finish_swap(alias, reason, before, waiting, seconds, warm, error)
        self._kick()

    def _finish_swap(self, alias: str, reason: str, before: list[str], waiting: int, seconds: float,
                     warm: bool, error: str) -> None:
        now = self._clock()
        self._history.appendleft({
            "at": time.time(), "from": before, "to": alias, "reason": reason, "seconds": round(seconds, 1),
            "waiting": waiting, "ok": not error, "error": error})
        if error:
            logger.warning("載せ替えに失敗: %s → %s（%s）%.1f 秒・待ち %d 件・理由=%s",
                           "+".join(before) or "なし", alias, reason, seconds, waiting, error)
            self._failures[alias] = self._failures.get(alias, 0) + 1
            if self._failures[alias] >= FAILURES_BEFORE_COOLDOWN:
                self._cooldown_until[alias] = now + self._cooldown
            detail = f"モデル {alias} を載せられませんでした: {error}"
            for ticket in [w for w in self._waiters if w.alias == alias]:
                self._fail(ticket, detail)
            return
        self.swap_count += 1
        # 載せた目的の要求を先に 1 本（枠の分）通す。通さないと、次の判定で「また切り替える」と決めて
        # 載せたモデルが一度も使われない行き来（livelock）になり得る。
        info = self._last_snapshot.models.get(alias) if self._last_snapshot else None
        cap = max(1, info.parallel if info else 1)
        for ticket in [w for w in self._waiters if w.alias == alias and not w.fut.done()]:
            if ticket.kind == "load":
                self._grant(ticket, count=False)
            elif self._running.get(alias, 0) < cap:
                self._grant(ticket, count=True)
        self._failures.pop(alias, None)
        self._cooldown_until.pop(alias, None)
        self._measured.setdefault((alias, warm), deque(maxlen=5)).append(seconds)
        for gone in before:
            if gone != alias:
                self._unloaded_at[gone] = now
        logger.info("載せ替え: %s → %s（%s）%.1f 秒・待っていた要求 %d 件",
                    "+".join(before) or "なし", alias, reason, seconds, waiting)

    # ── 先読み ───────────────────────────────────────────

    def _maybe_prefetch(self, info: ModelInfo, settings: Settings) -> None:
        if not settings.prefetch or self._prefetch_task is not None:
            return
        now = self._clock()
        if now - self._prefetch_tried.get(info.alias, -1e18) <= WARM_WINDOW_SECONDS:
            return  # 試した（成功・見送り・失敗を問わず）あとは 15 分は繰り返さない
        self._prefetch_tried[info.alias] = now
        self._prefetch_task = asyncio.get_running_loop().create_task(self._prefetch(info))

    async def _prefetch(self, info: ModelInfo) -> None:
        try:
            files, size, why = await asyncio.to_thread(
                lambda: plan_prefetch(info.model_path, self._mem_available()))
            record = {"alias": info.alias, "bytes": size, "skipped": why}
            if not why:
                done = await asyncio.to_thread(self._prefetcher, files)
                record["files"] = done
                if done:
                    self._prefetched_at[info.alias] = self._clock()
                logger.info("重みを先読みしました: %s（%.1f GiB・%d ファイル）", info.alias, size / 1024**3, done)
            else:
                # 足りない・見つからないときは何もしない（次の機会は 15 分後）。
                logger.info("先読みしません: %s: %s", info.alias, why)
            self._prefetch_info = record
        except Exception:  # noqa: BLE001 - 先読みの失敗は無視する
            logger.debug("先読みに失敗しました: %s", info.alias, exc_info=True)
        finally:
            self._prefetch_task = None

    # ── 見える化 ─────────────────────────────────────────

    async def status(self) -> dict[str, Any]:
        snap = await asyncio.to_thread(self._snapshot)
        settings = await asyncio.to_thread(self._settings)
        now = self._clock()
        groups: dict[str, list[Ticket]] = {}
        for ticket in self._waiters:
            if not ticket.fut.done():
                groups.setdefault(ticket.alias, []).append(ticket)
        queue = []
        for alias, tickets in groups.items():
            if alias in snap.models and snap.models[alias].role == "llm":
                score, oldest = self._score(alias, tickets, snap, settings, now)
                seconds, warm = self._estimate(snap.models[alias])
            else:
                score, seconds, warm = 0.0, 0.0, False
                oldest = max(now - t.enqueued_at for t in tickets)
            queue.append({"alias": alias, "waiting": len(tickets), "oldest_wait_seconds": round(oldest, 1),
                          "needs_swap": snap.needs_swap(alias), "score": round(score, 4),
                          "estimate_seconds": round(seconds, 1), "warm": warm})
        queue.sort(key=lambda row: -row["oldest_wait_seconds"])
        return {
            "loaded": [a for a, i in snap.models.items() if i.loaded and i.role == "llm"],
            "running": dict(self._running),
            "swapping": self._swapping,
            "swapping_seconds": round(now - self._swap_started, 1) if self._swapping else 0.0,
            "queue": queue,
            "next": self._next,
            "recent_swaps": list(self._history),
            "swap_count": self.swap_count,
            "settings": {"aging_seconds": settings.aging_seconds,
                         "prefetch": settings.prefetch},
            "prefetch": self._prefetch_info,
        }


_schedulers: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, SwapScheduler]" = weakref.WeakKeyDictionary()


def get_scheduler() -> SwapScheduler:
    """イベントループごとに 1 つ（ControlDeck の本体は 1 つのループ）。"""
    loop = asyncio.get_running_loop()
    scheduler = _schedulers.get(loop)
    if scheduler is None:
        scheduler = _schedulers[loop] = SwapScheduler()
    return scheduler


async def acquire(alias: str, *, request: Any = None, kind: str = "chat") -> Ticket:
    return await get_scheduler().acquire(alias, request=request, kind=kind)
