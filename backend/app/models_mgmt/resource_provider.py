"""Common Resource Broker adapter for the locally managed LLM runtimes.

llama.cpp は同じ1枚のGPUを取り合うので、ブローカーから見ると1つの
provider として扱うのが正しい。ランタイム別の差（KVプールの有無など）は
models_mgmt/local_llm.py が吸収する。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Callable

from app.models_mgmt import local_llm
from app.models_mgmt.runtime_policy import get_policy
from app.resources.devices import DeviceCollection
from app.resources.providers import ProviderReservation, ResourceProvider
from app.resources.schema import ResourceRequest
from app.resources.telemetry import ResourceTelemetry

logger = logging.getLogger("control_deck.resources")

# 大きなランタイムの GPU の目安。reservations() と同じ値（実際は ~28GiB を抱える）。
LARGE_RUNTIME_BYTES = 28 * 1024**3




def model_is_on_local_nvme(model_path: str) -> bool:
    """Fail closed unless the model's backing block-device path is NVMe."""
    try:
        stat = os.stat(Path(model_path).resolve())
        device = Path(f"/sys/dev/block/{os.major(stat.st_dev)}:{os.minor(stat.st_dev)}")
        return "nvme" in str(device.resolve()).lower()
    except OSError:
        return False


class LocalLlmCapacityProvider(ResourceProvider):
    """ローカル常駐LLM（llama.cpp）のGPU占有をブローカーへ申告する。"""

    id = "local-llm"
    # 最後に頼む。降ろすと、載せ直しの間だけでなく利用者の会話そのものが止まる。
    # 音楽は int8 + offload なら 7.4 GiB で動き、LLM が 22.8 GiB 常駐でも残りの
    # 約 9 GiB に収まる（実測 2026-09-16）。**音楽のために LLM を降ろす必要は
    # 無い。** 画像の全常駐（実測 19.4GB）のように、どうしても収まらないものが
    # 来たときだけ最後の手段として降りる。
    step_aside_order = 90

    def __init__(
        self,
        devices: DeviceCollection,
        telemetry: ResourceTelemetry,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._devices = devices
        self._telemetry = telemetry
        self._clock = clock
        self._resident_since: dict[str, float] = {}
        self._condition = asyncio.Condition()
        self._active_requests = 0
        self._draining = False
        self._stopping = False
        # Voice/live consumers may request a short, renewable residency hold so
        # the LLM is not unloaded between conversational turns. Holds are
        # intentionally in-memory and TTL-bound: if the consumer crashes or
        # ControlDeck restarts, they disappear without requiring orphan cleanup.
        self._hold_lock = threading.RLock()
        self._residency_holds: dict[str, tuple[str, str, float]] = {}
        # Add-on へ GPU を貸している間の状態。貸した (runtime, alias) と、貸し始めた時刻。
        # 0 なら貸していない。貸している間、新しい LLM 要求は返ってくるまで待つ——
        # 待たせないと、LLM の受け入れは空きに合わせて縮む（resource_request）ので、
        # 生成中の Add-on の隣へ LLM が載って両方 OOM で落ちる。
        self._lent: list[tuple[str, str]] = []
        self._lent_since = 0.0
        self._lent_quiet_since = 0.0
        # swap: 退くための drain 中に来た要求を、取り消しの合図にせず待たせる。
        self._hold_new = False
        self._held_waiters = 0

    def resource_request(self, alias: str, job_id: str) -> ResourceRequest:
        instance = local_llm.get_instance(alias)
        current = local_llm.find(alias) or instance
        loaded = bool(current.get("loaded"))
        model_bytes = 0
        for key in ("model_path", "mmproj_path"):
            path = str(instance.get(key) or "")
            if not path:
                continue
            try:
                model_bytes += Path(path).stat().st_size
            except OSError:
                continue
        # Strata uses many sharded MoE weights, not one model_path; reserve a
        # conservative VRAM envelope rather than reporting a zero-size model.
        if str(instance.get("runtime")) in ("strata", "maya-s", "radiance"):
            model_bytes = 28 * 1024**3
        device = self._devices.get("gpu0")
        headroom = 0 if loaded else 512 * 1024 * 1024
        peak = 0 if loaded else max(model_bytes + 2 * 1024**3, int(model_bytes * 1.75))
        if device is not None:
            peak = min(
                peak,
                max(0, device.total_bytes - device.observed_used_bytes - headroom),
            )
        return ResourceRequest.model_validate({
            "owner": f"llm:{alias}",
            "job_id": job_id,
            "device": "gpu0",
            "vram": {
                "resident_bytes": 0 if loaded else model_bytes,
                "execution_peak_bytes": peak,
                "cold_load_peak_bytes": peak,
                "headroom_bytes": headroom,
                "confidence": "measured" if loaded else "low",
            },
            "compute_mode": "endpoint-managed",
            "priority": 100,
            "class": "interactive",
            "residency_key": local_llm.residency_key({**instance, "runtime": current.get("runtime", "")}),
            "max_wait_sec": 300,
            "on_insufficient": "queue",
        })

    def _cleanup_residency_holds(self) -> None:
        now = self._clock()
        with self._hold_lock:
            for hold_id, (_owner, _key, expires_at) in list(self._residency_holds.items()):
                if expires_at <= now:
                    self._residency_holds.pop(hold_id, None)

    def create_residency_hold(self, residency_key: str, owner: str, *, ttl_seconds: float) -> str:
        ttl = max(10.0, float(ttl_seconds))
        hold_id = f"hold:{uuid.uuid4().hex}"
        with self._hold_lock:
            self._cleanup_residency_holds()
            self._residency_holds[hold_id] = (owner, residency_key, self._clock() + ttl)
        self._telemetry.record("residency.hold.created", reason="consumer_session")
        return hold_id

    def renew_residency_hold(self, hold_id: str, owner: str, *, ttl_seconds: float) -> bool:
        ttl = max(10.0, float(ttl_seconds))
        with self._hold_lock:
            self._cleanup_residency_holds()
            current = self._residency_holds.get(hold_id)
            if current is None or current[0] != owner:
                return False
            self._residency_holds[hold_id] = (current[0], current[1], self._clock() + ttl)
        return True

    def release_residency_hold(self, hold_id: str, owner: str) -> bool:
        with self._hold_lock:
            self._cleanup_residency_holds()
            current = self._residency_holds.get(hold_id)
            if current is None or current[0] != owner:
                return False
            self._residency_holds.pop(hold_id, None)
        self._telemetry.record("residency.hold.released", reason="consumer_session")
        return True

    def has_residency_hold(self, residency_key: str | None = None) -> bool:
        with self._hold_lock:
            self._cleanup_residency_holds()
            if residency_key is None:
                return bool(self._residency_holds)
            return any(key == residency_key for _owner, key, _expires in self._residency_holds.values())

    def reservations(self) -> list[ProviderReservation]:
        running = [item for item in local_llm.list_instances() if item.get("loaded")]
        now = self._clock()
        aliases = {str(item.get("alias") or "llama") for item in running}
        self._resident_since = {
            alias: self._resident_since.get(alias, now) for alias in aliases
        }
        if not running:
            return []
        device = self._devices.get("gpu0")
        observed = device.observed_used_bytes if device is not None else 0
        sizes = []
        for item in running:
            if item.get("runtime") in ("strata", "maya-s", "radiance"):
                # Disk-streamed MoE GGUF size is not GPU residency.
                sizes.append(min(28 * 1024**3, device.total_bytes) if device else 28 * 1024**3)
                continue
            try:
                sizes.append(Path(str(item.get("model_path") or "")).stat().st_size)
            except OSError:
                sizes.append(0)
        total_size = sum(sizes)
        reserved_total = max(observed, total_size)
        values = []
        for item, size in zip(running, sizes, strict=True):
            alias = str(item.get("alias") or "llama")
            share = (
                int(reserved_total * size / total_size)
                if total_size > 0 else int(reserved_total / len(running))
            )
            values.append(ProviderReservation(
                provider_id=self.id,
                device_id="gpu0",
                owner=f"llm:{alias}",
                reserved_bytes=share,
                residency_key=local_llm.residency_key(item),
                draining=self._draining,
            ))
        return values

    async def enter_request(self) -> None:
        async with self._condition:
            if self._draining and not self._stopping and not self._hold_new:
                self._draining = False
                self._telemetry.record("release.drain_canceled", reason="llm_request")
            self._held_waiters += 1
            try:
                while self._stopping or self._hold_new or self.lending():
                    try:
                        # 貸出の上限は時計で切れるので、通知が無くても見直す。
                        await asyncio.wait_for(self._condition.wait(), timeout=2.0)
                    except TimeoutError:
                        pass
            finally:
                self._held_waiters -= 1
            self._active_requests += 1
            self._condition.notify_all()

    # ── Add-on への GPU の貸し出し ─────────────────────────────────────

    def lending(self) -> bool:
        """いま Add-on へ GPU を貸しているか。上限を過ぎた貸出は貸していないものとする。"""
        if not self._lent_since:
            return False
        return self._clock() - self._lent_since < get_policy().addon_gpu.max_lend_sec

    def lending_state(self) -> dict:
        return {
            "lending": self.lending(),
            "lent": [{"runtime": runtime, "alias": alias} for runtime, alias in self._lent],
            "since": self._lent_since,
            "waiting_llm_requests": self._held_waiters,
        }

    async def wait_returned(self, timeout: float = 0.0) -> bool:
        """GPU が LLM へ返ってくるまで待つ。起動を伴う呼び出しが Add-on の隣へ載らないため。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout if timeout > 0 else None
        async with self._condition:
            self._held_waiters += 1
            try:
                while self.lending() or self._hold_new:
                    remaining = None if deadline is None else deadline - loop.time()
                    if remaining is not None and remaining <= 0:
                        return False
                    try:
                        await asyncio.wait_for(self._condition.wait(),
                                               timeout=2.0 if remaining is None else min(2.0, remaining))
                    except TimeoutError:
                        pass
            finally:
                self._held_waiters -= 1
        return True

    def _addon_mode(self, request: ResourceRequest | None) -> str:
        """Add-on の要求なら方針（auto / swap / llm_first）、それ以外は ""。"""
        if request is None or not request.owner.startswith("addon:"):
            return ""
        return get_policy().addon_gpu.mode

    def would_step_aside(self, device_id: str, request: ResourceRequest) -> bool:
        """RAM や小さい枠で受けさせる前に、GPU を丸ごと貸せるか（broker がいま聞くだけ）。"""
        mode = self._addon_mode(request)
        if device_id != "gpu0" or mode in ("", "llm_first"):
            return False
        policy = get_policy()
        if not policy.gateway_only or self.has_residency_hold() or self._stopping:
            return False
        if not any(item.get("loaded") and str(item.get("role", "llm")) == "llm"
                   for item in local_llm.list_instances()):
            return False
        # auto は推論中の LLM を待たない。待つと会話（OpenCode のサブエージェント）が止まる。
        return mode == "swap" or self._active_requests == 0

    async def return_gpu(self, *, restore: bool) -> None:
        """貸出を終える。生成を終えた Add-on が抱えたままの model を降ろしてもらい、LLM を戻す。"""
        lent = list(self._lent)
        try:
            from app.resources.addon_provider import provider as addon_residency

            await addon_residency.step_aside("gpu0")
        except Exception:  # noqa: BLE001 - 返すこと自体は止めない
            logger.debug("add-on step aside before restore failed", exc_info=True)
        async with self._condition:
            self._lent, self._lent_since, self._lent_quiet_since = [], 0.0, 0.0
            self._condition.notify_all()
        self._telemetry.record("release.returned", reason=f"restore={restore} lent={len(lent)}")
        if restore:
            for _runtime, alias in lent:
                await self._restore(alias)

    async def _restore(self, alias: str) -> None:
        """broker を通して載せ直す。素通しで起こすと、来たばかりの生成と取り合う。"""
        from app.resources.broker import broker
        from app.resources.schema import RequestState

        try:
            status = await broker.acquire(self.resource_request(alias, f"restore-{uuid.uuid4().hex[:12]}"))
            if status.state != RequestState.GRANTED or not status.lease_id:
                logger.info("%s の載せ直しを見送りました: %s", alias, status.state.value)
                return
            await broker.activate(status.lease_id)
            try:
                ready = await local_llm.ensure_ready(alias, timeout_seconds=900)
                logger.info("Add-on の生成が終わったので %s を載せ直しました（ready=%s）", alias, ready)
            finally:
                await broker.release(status.lease_id)
        except Exception:  # noqa: BLE001 - 次の要求が自分で起こす
            logger.exception("%s を載せ直せませんでした", alias)

    async def lending_tick(self) -> None:
        """貸出中の 1 回分の見回り。Add-on が GPU を使い終えたら返す。"""
        if not self._lent_since:
            return
        from app.resources.broker import broker

        policy = get_policy().addon_gpu
        now = self._clock()
        if now - self._lent_since >= policy.max_lend_sec:
            logger.warning("Add-on への GPU の貸出が %s 秒を超えたので LLM へ戻します", policy.max_lend_sec)
            await self.return_gpu(restore=policy.restore)
            return
        if await broker.owner_active("addon:", "gpu0"):
            self._lent_quiet_since = 0.0
            return
        if not self._lent_quiet_since:
            self._lent_quiet_since = now
        # LLM を待っている要求があるなら、間を置かずに返す。その要求が自分で載せる。
        if self._held_waiters:
            await self.return_gpu(restore=False)
        elif now - self._lent_quiet_since >= policy.restore_delay_sec:
            await self.return_gpu(restore=policy.restore)

    async def lending_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(2)
                await self.lending_tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 見回りの失敗で service を止めない
                logger.exception("GPU lending tick failed")

    async def leave_request(self) -> None:
        async with self._condition:
            self._active_requests = max(0, self._active_requests - 1)
            self._condition.notify_all()


    can_step_aside = True
    step_aside_takes_request = True

    async def step_aside(self, device_id: str, *, request: ResourceRequest | None = None) -> tuple[bool, str, int]:
        """他に置き場所が無い要求のために、使っていない LLM だけを降ろす。

        broker が「どの device にも置けない」と判じる直前に呼ばれる。判断は
        release_on_request と同じで、実行中の推論は drain して待ち、使用中なら
        降ろさない。違うのは引き金だけである。

        時計での制限は置かない。振り回しを止めているのは「使用中なら退かない」の
        方であって、間隔ではない。誰も使っていない LLM を降ろす代償は次に使う人が
        載せ直しを待つことだけで、その人が居ないから降ろせている。間隔で縛ると、
        続けて来る生成が RAM 実行（実測で 17 倍遅い）へ落ちる。
        """
        if device_id != "gpu0":
            return False, "not_this_device", 0
        mode = self._addon_mode(request)
        if mode == "llm_first":
            return False, "llm_first", 0
        if mode == "auto" and self._active_requests:
            return False, "in_use", 0
        # 別の LLM のために大きなランタイムを降ろすのは起動側（prepare_switch）の仕事。
        large = request is None or not request.owner.startswith("llm:")
        released, reason, freed = await self.release_on_request(
            hold_new=mode == "swap", lend_to=request.owner if mode else "", large=large)
        if released:
            self._telemetry.record("release.step_aside", reason="no_room_elsewhere")
        return released, reason, freed

    async def release_on_request(
        self, *, include_helpers: bool = False, hold_new: bool = False, lend_to: str = "",
        large: bool = True,
    ) -> tuple[bool, str, int]:
        """Honour an explicit "my AI turn is over" declaration from a consumer.

        This is the same decision the idle-unload loop makes after 30 minutes,
        triggered by a request instead of by a clock. It reuses the identical
        in-use guards, so ControlDeck chat, an OpenCode session, and another
        add-on cannot have the shared model pulled out from under them.

これは利用者からの明示的な解放要求で、自動退避とは別物:
        those exist to stop involuntary preemption from thrashing, and a
        voluntary hand-back is not preemption.

        The drain below is the guarantee that matters: no running inference is
        ever cut, whether it belongs to ControlDeck chat, an OpenCode session,
        or another add-on. What an explicit release deliberately does not
        honour is the idle loop's 30-minute recency window — see
        llama.release_reason for why keeping it would make the capability
        useless on a single GPU.
        """
        policy = get_policy()
        if self.has_residency_hold():
            return False, "residency_held", 0
        if not policy.gateway_only:
            return False, "release_not_permitted_by_policy", 0
        async with self._condition:
            if self._stopping:
                return False, "already_stopping", 0
            self._draining = True
            self._hold_new = hold_new
            deadline = asyncio.get_running_loop().time() + policy.drain_timeout_sec
            while self._active_requests and self._draining:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    self._draining = self._hold_new = False
                    self._condition.notify_all()
                    return False, "drain_timeout", 0
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    self._draining = self._hold_new = False
                    self._condition.notify_all()
                    return False, "drain_timeout", 0
            if not self._draining:
                self._hold_new = False
                return False, "in_use", 0
            self._stopping = True
        lent: list[tuple[str, str]] = []
        try:
            from app.models_mgmt import large_model_lifecycle, llama

            llama_loaded = [
                ("llama.cpp", str(item.get("alias") or "llama"))
                for item in await asyncio.to_thread(local_llm.list_instances, runtime="llama.cpp")
                if item.get("loaded") and str(item.get("role", "llm")) == "llm"
            ]
            # add-on のための退避は「戻ってくる退避」である。会話の KV を
            # 預けておけば、戻ったときに読み直さずに済む（実測 165 秒 →
            # 0.44 秒）。時間経過や手動で降ろすときは預けない。
            released, reason, freed = await llama.release_loaded_llms(
                include_helpers=include_helpers, preserve_state=True
            )
            if released and freed:
                lent += llama_loaded
            # Strata / Radiance / Maya-S も降ろす。ここが llama.cpp だけだったので、
            # 大きなランタイムが載っていると「0 バイト空けた」と答え、Add-on は
            # 永遠に GPU を貰えなかった。
            if released and large:
                stopped, refusal = await large_model_lifecycle.release_for_addon()
                lent += stopped
                freed += LARGE_RUNTIME_BYTES * len(stopped)
                if refusal and not stopped and not freed:
                    released, reason = False, refusal
                elif stopped:
                    reason = "released"
            if released and freed:
                await asyncio.to_thread(self._reobserve)
        finally:
            async with self._condition:
                self._stopping = False
                self._draining = False
                self._hold_new = False
                if lend_to and lent:
                    self._lent = lent
                    self._lent_since = self._clock()
                    self._lent_quiet_since = 0.0
                    logger.info("%s のために GPU を貸しました: %s", lend_to,
                                ", ".join(f"{r}:{a}" for r, a in lent))
                self._condition.notify_all()
        if released and freed:
            self._telemetry.record("release.explicit", reason="consumer_request")
        return released, reason, freed


    def _reobserve(self) -> None:
        """降ろした直後の空きを broker へ見せる。2 秒ごとの再走査を待つと、まだ埋まって
        見えるので、借りた Add-on が RAM へ流れてしまう。"""
        try:
            from app.resources.devices import observed_system_devices

            observed = observed_system_devices().list()
            if observed:
                self._devices.replace(observed)
        except Exception:  # noqa: BLE001 - 次の再走査が拾う
            logger.debug("device re-observation failed", exc_info=True)


_provider: LocalLlmCapacityProvider | None = None


def provider() -> LocalLlmCapacityProvider:
    global _provider
    if _provider is None:
        from app.resources.broker import broker

        _provider = LocalLlmCapacityProvider(broker.devices, broker.telemetry)
    return _provider
