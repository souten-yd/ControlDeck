"""モデルの載せ替えの調停役（models_mgmt/swap_scheduler.py）の振る舞い。

目的: ローカル LLM の載せ替えを 1 か所で決め、(1) 実行中の要求を切らない、(2) 待たせても 503 にしない、
(3) 載せ替えの回数を到着順より減らす、(4) 待ち時間で優先度が上がり必ず実行される、(5) 二重のロックで
行き詰まらない、ことを守る。
根拠: 2026-11-22、箱の OpenCode が開発（Strata）と調査（Maya）を交互に呼ぶと、要求ごとに別々のモデルが互いを
降ろそうとし、解放の見送り・Strata の操作ロックの取り合いで、待てば通る要求が 300〜660 秒待って 503 だった。
利用者の指示（同日）: 並列は Radiance だけ（登録の数まで）・それ以外は 1 本ずつ。断ると要求が消えるので、
待ち時間で点数が上がる方式にする。
載せ替えはモックの loader（時間を制御）、載っている状態は注入した snapshot で模す。
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models_mgmt import gateway, local_llm, swap_scheduler as ss
from app.models_mgmt.swap_scheduler import ModelInfo, Settings, Snapshot, SwapScheduler


class World:
    """載っているモデルと、載せ替えの記録。loader が載せ替え（時間つき）を模す。"""

    def __init__(self, loaded=(), *, load_seconds=0.03, extra=None):
        self.loaded = set(loaded)
        self.load_seconds = load_seconds
        self.infos = {
            "A": ModelInfo("A", "strata", cold_seconds=load_seconds),
            "B": ModelInfo("B", "maya-s", cold_seconds=load_seconds),
            "C": ModelInfo("C", "strata", cold_seconds=load_seconds),
            "R": ModelInfo("R", "radiance", parallel=3, cold_seconds=load_seconds),
            "E": ModelInfo("E", "llama.cpp", role="embedding"),
        }
        self.infos.update(extra or {})
        self.swaps: list[tuple[set, str]] = []
        self.fail: dict[str, str] = {}
        self.active: dict[str, int] = {}
        self.violations: list[str] = []
        self.started: list[tuple[str, float]] = []

    def snapshot(self) -> Snapshot:
        models = {}
        for alias, info in self.infos.items():
            models[alias] = ModelInfo(**{**info.__dict__, "loaded": alias in self.loaded})
        large = any(models[a].runtime in ss.LARGE_RUNTIMES and models[a].role == "llm" for a in self.loaded)
        return Snapshot(models, large)

    async def loader(self, alias: str) -> None:
        # 載せ替えは実行中が 0 のときだけ起きる（実行中を切らない）。
        if any(self.active.values()):
            self.violations.append(f"実行中に載せ替え {alias}: {dict(self.active)}")
        await asyncio.sleep(self.load_seconds)
        if alias in self.fail:
            raise RuntimeError(self.fail[alias])
        self.swaps.append((set(self.loaded), alias))
        self.loaded = {alias}

    def scheduler(self, **kw) -> SwapScheduler:
        kw.setdefault("settings", lambda: Settings(aging_seconds=30.0, prefetch=False))
        # 実際の比（載せ替え ≈ 60 秒・続ける下限 10 秒 = 6 倍）を、載せ替え 0.03 秒の模擬へ縮める。
        kw.setdefault("min_cost_seconds", self.load_seconds / 6)
        return SwapScheduler(snapshot=self.snapshot, loader=self.loader, poll_seconds=0.02, **kw)

    async def run(self, sched, alias, seconds, *, request=None, log=None):
        ticket = await sched.acquire(alias, request=request)
        try:
            if alias in self.infos and self.infos[alias].role == "llm" and alias not in self.loaded:
                self.violations.append(f"{alias} が載っていないのに実行した")
            self.active[alias] = self.active.get(alias, 0) + 1
            if len([a for a, n in self.active.items() if n]) > 1:
                self.violations.append(f"別のモデルが同時に実行: {dict(self.active)}")
            self.started.append((alias, time.monotonic()))
            if log is not None:
                log.append(("start", alias))
            await asyncio.sleep(seconds)
            if log is not None:
                log.append(("end", alias))
        finally:
            self.active[alias] -= 1
            ticket.release()


class Req:
    def __init__(self):
        self.gone = False

    async def is_disconnected(self):
        return self.gone



import httpx as _httpx

_REAL_ASYNC_CLIENT = _httpx.AsyncClient

def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


def test_loaded_model_runs_at_once_and_other_model_waits_for_running_to_finish():
    """載っているモデルは待たずに実行・別のモデルは実行中が終わってから切り替える（実行中は切られない）。"""
    async def scenario():
        world = World(["A"])
        sched = world.scheduler()
        log: list = []
        t0 = time.monotonic()
        a = asyncio.create_task(world.run(sched, "A", 0.3, log=log))
        await asyncio.sleep(0.05)
        b = asyncio.create_task(world.run(sched, "B", 0.05, log=log))
        await asyncio.gather(a, b)
        assert world.started[0][1] - t0 < 0.15  # 載っている A は待たない
        assert log.index(("end", "A")) < log.index(("start", "B"))
        assert world.swaps == [({"A"}, "B")]
        assert world.violations == []
    run(scenario())


def test_same_model_runs_one_at_a_time_except_radiance():
    """同じモデルの並列は Radiance だけ（登録の数まで）。Strata・Maya・llama.cpp の chat は 1 本ずつ。"""
    async def scenario():
        peaks = {}
        for loaded, alias in (("A", "A"), ("B", "B"), ("L", "L"), ("R", "R")):
            extra = {"L": ModelInfo("L", "llama.cpp", parallel=1)}
            world = World([loaded], extra=extra)
            sched = world.scheduler()
            current = peak = 0

            async def one():
                nonlocal current, peak
                ticket = await sched.acquire(alias)
                current += 1
                peak = max(peak, current)
                await asyncio.sleep(0.05)
                current -= 1
                ticket.release()
            await asyncio.gather(*(one() for _ in range(7)))
            peaks[alias] = peak
        assert peaks == {"A": 1, "B": 1, "L": 1, "R": 3}
    run(scenario())


def test_snapshot_gives_parallel_only_to_radiance(monkeypatch):
    """既定の snapshot: Radiance だけ登録の max_num_seqs（n_parallel）まで。llama.cpp は n_parallel 4 でも 1。"""
    rows = [{"alias": "r", "runtime": "radiance", "role": "llm", "n_parallel": 4},
            {"alias": "l", "runtime": "llama.cpp", "role": "llm", "n_parallel": 4},
            {"alias": "s", "runtime": "strata", "role": "llm", "n_parallel": 2}]
    monkeypatch.setattr(local_llm, "list_instances", lambda **kw: rows)
    ss.invalidate_snapshot()
    models = ss.default_snapshot().models
    ss.invalidate_snapshot()
    assert (models["r"].parallel, models["l"].parallel, models["s"].parallel) == (4, 1, 1)


def test_score_prefers_more_waiting_then_faster_load_then_older():
    """点数: 待ちが多いモデルが先・載せ替えが速い（warm）モデルが先・同点は古い順。"""
    async def order(extra, arrivals, hold=0.15):
        world = World(["A"], extra=extra)
        sched = world.scheduler()
        a = asyncio.create_task(world.run(sched, "A", hold))
        await asyncio.sleep(0.02)
        tasks = []
        for alias in arrivals:
            tasks.append(asyncio.create_task(world.run(sched, alias, 0.01)))
            await asyncio.sleep(0.005)
        await asyncio.gather(a, *tasks)
        return [alias for alias, _ in world.started[1:]]

    async def scenario():
        # 待ちが多い B（3 件）が、先に来た C（1 件）より先。
        first = await order({}, ["C", "B", "B", "B"])
        assert first[0] == "B"
        # 同じ待ち数なら、メモリ上にある（warm: 見込み 0.005 秒）C が、読み込みの B（0.03 秒）より先。
        warm = await order({"C": ModelInfo("C", "strata", warm_seconds=0.005, cold_seconds=0.03,
                                           recently_unloaded=True)}, ["B", "C"])
        assert warm[0] == "C"
        # 同点なら最も古い要求のモデル。
        tie = await order({"B": ModelInfo("B", "strata", cold_seconds=0.03)}, ["C", "B"])
        assert tie[0] == "C"
    run(scenario())


def test_waiting_raises_priority_so_every_request_runs_and_none_gets_503():
    """載っているモデルに要求が来続けても、別のモデルの要求は待ち時間で点数が上がり、やがて必ず実行される。

    続ける点数 ≈ (待ち数 + 最古の待ち秒/aging)/MIN_COST、切り替える点数 = (1 + 待ち秒/aging)/60。
    aging=0.1 秒・A の待ちが常に 1 件前後（2 人が続けて送る）なら続ける点数は ≈ 0.15 以下 → B は待ち ≈ 0.8 秒で上回る。
    上限（数値）: B の待ちは 3 秒未満。A の要求は B が来るまで続けて通る。どの要求も 503 にならない。
    """
    async def scenario():
        world = World(["A"], load_seconds=0.03)
        sched = world.scheduler(settings=lambda: Settings(aging_seconds=0.1, prefetch=False))
        stop = False
        errors: list = []
        a_count = 0

        async def client():
            # 2 つの利用者が A を続けて送る（終わったらすぐ次）。A の待ちは常に 1 件前後。
            nonlocal a_count
            while not stop:
                try:
                    await world.run(sched, "A", 0.04)
                    a_count += 1
                except HTTPException as exc:
                    errors.append(exc)

        feed = asyncio.gather(client(), client())
        await asyncio.sleep(0.1)
        t0 = time.monotonic()
        await world.run(sched, "B", 0.01)
        waited = time.monotonic() - t0
        stop = True
        await feed
        await asyncio.sleep(0.5)
        assert waited < 3.0
        assert waited > 0.3  # 続ける点数が高い間は A が優先される（すぐには切り替わらない）
        assert a_count >= 5
        assert errors == [] and world.violations == []
    run(scenario())


def test_swap_always_serves_the_request_it_loaded_for():
    """載せた目的の要求は、載せ替えの直後に必ず 1 本は通す（載せたモデルを一度も使わず行き来し続けない）。

    載せ替えが「続ける下限」より安いと測られた世界で、2 つのモデルに待ちがあっても、全部の要求が終わる。
    """
    async def scenario():
        world = World(["A"], load_seconds=0.01)
        sched = world.scheduler(min_cost_seconds=1.0)  # 載せ替え 0.01 秒 < 続ける下限 1 秒
        tasks = [asyncio.create_task(world.run(sched, m, 0.01)) for m in ["B", "A", "B", "A", "B", "A"]]
        await asyncio.gather(*tasks)
        assert len(world.started) == 6 and world.violations == []
    run(scenario())


def test_requests_after_the_switch_decision_are_queued_not_refused():
    """切り替えると決めた後に来た元のモデルの要求も、断らず待ち行列へ入れて後で実行する。"""
    async def scenario():
        world = World(["A"])
        sched = world.scheduler()
        log: list = []
        a = asyncio.create_task(world.run(sched, "A", 0.3, log=log))
        await asyncio.sleep(0.03)
        b = asyncio.create_task(world.run(sched, "B", 0.02, log=log))
        await asyncio.sleep(0.05)  # B を待たせて切り替えを決めさせる（A は実行中）
        a2 = asyncio.create_task(world.run(sched, "A", 0.02, log=log))
        await asyncio.gather(a, b, a2)
        assert log.index(("start", "B")) < log.index(("start", "A"), 1)  # 後から来た A は B の後
        assert world.violations == []
    run(scenario())


def test_waiting_requests_are_not_refused_and_a_client_that_leaves_is_dropped():
    """待たせても 503 にしない。クライアントが切ったら待ち行列から外れ（499）、載せ替えもしない。"""
    async def scenario():
        world = World(["A"])
        sched = world.scheduler()
        a = asyncio.create_task(world.run(sched, "A", 0.4))
        await asyncio.sleep(0.03)
        req = Req()
        b = asyncio.create_task(world.run(sched, "B", 0.01, request=req))
        await asyncio.sleep(0.1)
        req.gone = True
        with pytest.raises(HTTPException) as info:
            await b
        assert info.value.status_code == 499
        await a
        await asyncio.sleep(0.2)
        assert world.swaps == [] and sched._waiters == []
    run(scenario())


def test_failed_swap_returns_503_with_reason_then_pauses_after_two_failures(caplog):
    """載せ替えの失敗は、そのモデルの待ちに理由つきの 503（ログにも出す）。2 回続いたら少し間を置く。"""
    async def scenario():
        world = World(["A"])
        world.fail["B"] = "Strataのモデル操作が実行中です"
        sched = world.scheduler(cooldown_seconds=0.4)
        for _ in range(2):
            with pytest.raises(HTTPException) as info:
                await world.run(sched, "B", 0.01)
            assert info.value.status_code == 503
            assert "Strataのモデル操作が実行中です" in info.value.detail
        with caplog.at_level("WARNING", logger="control_deck.swap_scheduler"):
            t0 = time.monotonic()
            with pytest.raises(HTTPException) as info:
                await world.run(sched, "B", 0.01)
        assert time.monotonic() - t0 < 0.2  # 間を置く間は載せ替えを試さず、すぐ理由を返す
        assert "後にもう一度" in info.value.detail and info.value.headers["Retry-After"]
        assert "model=B" in caplog.text
        # ほかのモデルは影響を受けない。
        world.loaded = {"A"}
        await world.run(sched, "A", 0.01)
        # 間が過ぎ、直れば通る。
        await asyncio.sleep(0.45)
        del world.fail["B"]
        await world.run(sched, "B", 0.01)
        assert world.loaded == {"B"}
    run(scenario())


def test_alternating_models_swap_less_than_arrival_order():
    """交互に来る 2 つのモデルの要求（開発と調査）で、載せ替えの回数が到着順（FIFO）より少ない。"""
    async def scenario():
        world = World(["A"], load_seconds=0.03)
        sched = world.scheduler(settings=lambda: Settings(aging_seconds=30.0, prefetch=False))
        sequence = ["A", "B"] * 8
        fifo_swaps = sum(1 for a, b in zip(["A"] + sequence, sequence) if a != b)
        tasks = []
        for alias in sequence:
            tasks.append(asyncio.create_task(world.run(sched, alias, 0.03)))
            await asyncio.sleep(0.01)
        await asyncio.gather(*tasks)
        assert len(world.started) == 16 and world.violations == []
        assert fifo_swaps == 15
        assert sched.swap_count <= 4, sched.swap_count  # 到着順なら 15 回
    run(scenario())


def test_prefetch_only_with_enough_memory_and_failures_are_ignored(tmp_path):
    """先読みはメモリが足りないときにしない・失敗しても載せ替えは続く。GGUF の分割も全部読む。"""
    shards = [tmp_path / f"m-0000{i}-of-00003.gguf" for i in (1, 2, 3)]
    for f in shards:
        f.write_bytes(b"x" * 1000)
    (tmp_path / "other-00001-of-00002.gguf").write_bytes(b"y")
    assert ss.weight_files(str(shards[0])) == shards
    assert ss.weight_files(str(tmp_path / "nothing.gguf")) == []
    folder = tmp_path / "maya"
    folder.mkdir()
    (folder / "a.bin").write_bytes(b"z" * (2 << 20))
    (folder / "tiny.json").write_text("{}")
    assert ss.weight_files(str(folder)) == [folder / "a.bin"]

    gib = 1024**3
    _files, size, why = ss.plan_prefetch(str(shards[0]), 8 * gib + 2999)
    assert size == 3000 and "メモリが足りません" in why
    assert ss.plan_prefetch(str(shards[0]), 8 * gib + 3000)[2] == ""
    assert "読めません" in ss.plan_prefetch(str(shards[0]), None)[2]

    async def scenario(avail, prefetcher):
        extra = {"B": ModelInfo("B", "maya-s", model_path=str(shards[0]), cold_seconds=0.03)}
        world = World(["A"], extra=extra)
        called: list = []

        def fake(files):
            called.append(files)
            return prefetcher(files)

        sched = world.scheduler(settings=lambda: Settings(aging_seconds=30.0, prefetch=True),
                                mem_available=lambda: avail, prefetcher=fake)
        a = asyncio.create_task(world.run(sched, "A", 0.3))
        await asyncio.sleep(0.03)
        b = asyncio.create_task(world.run(sched, "B", 0.01))
        await asyncio.gather(a, b)
        assert world.loaded == {"B"}
        return called

    assert run(scenario(100 * gib, lambda files: len(files))) == [shards]
    assert run(scenario(1 * gib, lambda files: len(files))) == []

    def boom(files):
        raise OSError("読めない")
    assert len(run(scenario(100 * gib, boom))) == 1  # 失敗しても載せ替えは通る（上の assert）


def test_run_prefetch_skips_unreadable_files(tmp_path, monkeypatch):
    """読めないファイル・fadvise の失敗は無視して残りを続ける。"""
    good = tmp_path / "g.gguf"
    good.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(ss.os, "posix_fadvise", lambda fd, off, ln, adv: calls.append(adv))
    assert ss.run_prefetch([tmp_path / "missing.gguf", good]) == 1
    assert calls == [ss.os.POSIX_FADV_WILLNEED]

    def fail(*a):
        raise OSError("x")
    monkeypatch.setattr(ss.os, "posix_fadvise", fail)
    assert ss.run_prefetch([good]) == 0


def test_auxiliary_and_unknown_models_are_not_scheduled():
    """補助のモデル（embedding）と設定に無いモデルは対象外。載せ替え中でも待たず、実行中に数えない。"""
    async def scenario():
        world = World(["A"], load_seconds=0.2)
        sched = world.scheduler()
        a = asyncio.create_task(world.run(sched, "A", 0.15))
        await asyncio.sleep(0.02)
        b = asyncio.create_task(world.run(sched, "B", 0.01))
        await asyncio.sleep(0.25)  # A が終わり B の載せ替え中
        t0 = time.monotonic()
        ticket = await sched.acquire("E")
        other = await sched.acquire("unknown-model")
        assert time.monotonic() - t0 < 0.1
        assert sched.running_total() == 0
        ticket.release()
        other.release()
        await asyncio.gather(a, b)
    run(scenario())


def test_status_reports_queue_next_and_recent_swaps():
    """swap-queue の中身: モデルごとの待ち数・最古の待ち秒・載っているモデル・次の予定と理由・最近の載せ替え。"""
    async def scenario():
        world = World(["A"])
        sched = world.scheduler()
        a = asyncio.create_task(world.run(sched, "A", 0.3))
        await asyncio.sleep(0.03)
        bs = [asyncio.create_task(world.run(sched, "B", 0.01)) for _ in range(2)]
        await asyncio.sleep(0.15)
        status = await sched.status()
        assert status["loaded"] == ["A"] and status["running"] == {"A": 1}
        row = status["queue"][0]
        assert row["alias"] == "B" and row["waiting"] == 2 and row["oldest_wait_seconds"] >= 0.1
        assert status["next"]["alias"] == "B" and "点数" in status["next"]["reason"]
        await asyncio.gather(a, *bs)
        status = await sched.status()
        assert status["recent_swaps"][0]["to"] == "B" and status["recent_swaps"][0]["ok"]
        assert status["swap_count"] == 1
    run(scenario())


def test_failopen_when_the_scheduler_itself_breaks():
    """調停役の不具合で要求を止めない。snapshot が壊れたら、待たせず通す。"""
    async def scenario():
        def broken():
            raise RuntimeError("壊れた")
        sched = SwapScheduler(snapshot=broken, loader=lambda a: None, poll_seconds=0.02)
        ticket = await asyncio.wait_for(sched.acquire("X"), 2)
        ticket.release()
    run(scenario())


# ── ゲートウェイとのつながり ─────────────────────────────────────────


class GwRequest:
    def __init__(self, model, stream=False):
        self.model = model
        self.stream = stream

    async def json(self):
        return {"model": self.model, "stream": self.stream, "messages": [{"role": "user", "content": "hi"}]}

    async def is_disconnected(self):
        return False


def _gateway_world(monkeypatch, world: World):
    """gateway_chat・/load を、載せ替えをモックした世界へつなぐ。lease・ensure_ready は本物の経路で呼ばれる。"""
    import httpx

    calls: list = []
    held = {"leases": 0, "max": 0}
    monkeypatch.setattr(gateway, "_authorize", lambda request: None)
    monkeypatch.setattr(gateway, "resolve_instance", lambda model: {
        "alias": model, "port": 8000, "runtime": world.infos[model].runtime,
        "loaded": model in world.loaded})

    live: set = set()

    async def lease(alias, request):
        held["leases"] += 1
        held["max"] = max(held["max"], held["leases"])
        calls.append(("lease", alias))
        done = asyncio.get_running_loop().create_future()
        lease_id = f"lease-{alias}-{len(calls)}"
        live.add(lease_id)
        return object(), lease_id, done

    async def release(adapter, lease_id, renew):
        # 本物と同じく、二重に返しても 1 回だけ数える。
        if lease_id in live:
            live.discard(lease_id)
            held["leases"] -= 1
        renew.cancel()

    async def ready(alias, timeout_seconds=0):
        calls.append(("ready", alias))
        if alias not in world.loaded:
            await world.loader(alias)
        return True

    async def admit(*a):
        return {"accepting": True}

    monkeypatch.setattr(gateway, "_acquire_gateway_lease", lease)
    monkeypatch.setattr(gateway, "_release_gateway_lease", release)
    monkeypatch.setattr(gateway, "_admit", admit)
    monkeypatch.setattr(gateway, "_record_gateway_oom", lambda *a: None)
    monkeypatch.setattr(local_llm, "ensure_ready", ready)
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"choices": []}))
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": transport}))
    monkeypatch.setattr(ss, "default_snapshot", world.snapshot)
    monkeypatch.setattr(ss, "default_settings", lambda: Settings(30.0, False))
    monkeypatch.setattr(ss, "MIN_COST_SECONDS", world.load_seconds / 6)  # 模擬の時間へ縮める（上と同じ比）
    return calls, held


def test_gateway_chat_through_the_scheduler_does_not_deadlock(monkeypatch):
    """gateway_chat → 調停役 → _swap_load（_model_startup・lease・ensure_ready）が二重のロックで止まらない。

    別々のモデル（Strata の A・Maya の B）の要求を同時に 6 本。全て 200、載せ替えは到着順より少なく、
    lease は返し切る（使用中が残らない）。載せ替え自体は _swap_load が lease の中で 1 回だけ行う。
    """
    async def scenario():
        world = World(["A"])
        calls, held = _gateway_world(monkeypatch, world)
        models = ["B", "A", "B", "A", "B", "A"]
        responses = await asyncio.gather(*(gateway.gateway_chat(GwRequest(m)) for m in models))
        assert [r.status_code for r in responses] == [200] * 6
        assert held["leases"] == 0
        assert len(world.swaps) <= 2
        assert sum(1 for c in calls if c == ("ready", "B")) >= 1
    run(scenario())


def test_load_endpoint_goes_through_the_scheduler(monkeypatch):
    """/v1/models/{m}/load も調停役に並ぶ。載っていれば載せ替えない・載っていなければ 1 回載せる。"""
    async def scenario():
        world = World(["A"])
        _gateway_world(monkeypatch, world)
        loads: list = []
        real = gateway._swap_load

        async def tracked(alias, request=None):
            loads.append(alias)
            await real(alias, request)

        monkeypatch.setattr(gateway, "_swap_load", tracked)
        again = await gateway.gateway_load_model("A", GwRequest("A"))
        assert again["already_loaded"] is True and loads == []
        # 他の大きなモデルを降ろす要る B は、調停役の載せ替え（_swap_load）が 1 回だけ行う。
        out = await gateway.gateway_load_model("B", GwRequest("B"))
        assert out["ready"] is True and loads == ["B"] and world.loaded == {"B"}
    run(scenario())


def test_load_waits_for_running_chat(monkeypatch):
    """/load は、載っているモデルの実行中の要求が終わるまで待つ（実行中を切らない）。"""
    async def scenario():
        world = World(["A"])
        _gateway_world(monkeypatch, world)
        sched_ticket = await ss.acquire("A")
        load = asyncio.create_task(gateway.gateway_load_model("B", GwRequest("B")))
        await asyncio.sleep(0.2)
        assert not load.done() and world.loaded == {"A"}
        sched_ticket.release()
        out = await asyncio.wait_for(load, 5)
        assert out["ready"] and world.loaded == {"B"}
    run(scenario())


def test_overload_never_starves_another_model():
    """到着が処理より速い過負荷でも、別のモデルの要求は上限の時間内に実行される（待てば必ず実行される）。

    続ける点数は min(待ち数, CONTINUE_CAP=2)/MIN_COST で頭打ち。別モデルの 1 件は t > aging×(見込み×CAP/MIN_COST − 1)
    で必ず上回る。実際の値（aging 30 秒・見込み 60 秒・MIN_COST 10 秒）は 330 秒。ここは同じ比を縮めた模擬
    （aging 0.1・見込み 0.03・MIN_COST 0.005）で上限 1.1 秒。A は 0.02 秒ごとに来て 0.04 秒かかる（待ちが増え続ける）。
    """
    async def scenario():
        world = World(["A"], load_seconds=0.03)
        sched = world.scheduler(settings=lambda: Settings(aging_seconds=0.1, prefetch=False))
        bound = 0.1 * (0.03 * ss.CONTINUE_CAP / (0.03 / 6) - 1)
        assert abs(bound - 1.1) < 1e-9
        errors: list = []
        stop = False

        async def one():
            try:
                await world.run(sched, "A", 0.04)
            except HTTPException as exc:
                errors.append(exc)

        async def feeder():
            pending = []
            while not stop:
                pending.append(asyncio.create_task(one()))
                await asyncio.sleep(0.02)
            await asyncio.gather(*pending)

        feed = asyncio.create_task(feeder())
        await asyncio.sleep(0.1)
        t0 = time.monotonic()
        await world.run(sched, "B", 0.01)
        waited = time.monotonic() - t0
        stop = True
        await feed
        assert waited < bound + 1.0, waited
        assert errors == [] and world.violations == []
    run(scenario())


def _stream_world(monkeypatch, handler):
    import httpx

    world = World(["A"])
    calls, held = _gateway_world(monkeypatch, world)
    # 本物は読み込み時に掴んだもの（場面を続けて流すと、前の場面の差し替えを本物として掴んでいた）
    real = _REAL_ASYNC_CLIENT
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(gateway.httpx, "AsyncClient", lambda **kw: real(**{**kw, "transport": transport}))
    return world, held


async def _drive(response, *, disconnect_after: float):
    """ASGI として応答を流す。disconnect_after 秒後にクライアントが切れる。"""
    sent: list = []

    async def receive():
        await asyncio.sleep(disconnect_after)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)
    return sent


class _Slow(_httpx.AsyncByteStream):
    """ゆっくり流れる上流のストリーム。"""
    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for _ in range(100):
            await asyncio.sleep(0.02)
            yield b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'


def test_stream_cleanup_runs_even_when_the_relay_never_starts_or_is_cut(monkeypatch):
    """クライアントが relay の始まる前・途中で切れても、lease と調停役の枠が戻る。上流のエラーでも戻る。

    根拠: relay の finally だけが後始末をしていたので、relay が一度も始まらない切断では lease も ticket も戻らず、
    実行中が 0 にならない＝以後どのモデルへも載せ替えられなくなった（lease は使用中のまま固定）。
    応答の終わりで必ず走る BackgroundTask が返す。
    """
    import httpx

    async def scenario(kind):
        if kind == "error":
            handler = lambda r: httpx.Response(500, json={"error": {"message": "boom"}})
        else:
            handler = lambda r: httpx.Response(200, stream=_Slow(),
                                               headers={"content-type": "text/event-stream"})
        world, held = _stream_world(monkeypatch, handler)
        response = await gateway.gateway_chat(GwRequest("A", stream=True))
        sched = ss.get_scheduler()
        if kind == "error":
            assert response.status_code == 500
        else:
            assert sched.running_total() == 1 and held["leases"] == 1  # 返し忘れを見つけられる状態
            await _drive(response, disconnect_after=0.0 if kind == "before" else 0.15)
        assert sched.running_total() == 0, kind
        assert held["leases"] == 0, kind
        # 載せ替えが止まらない: 別のモデルが載せ替えられて実行できる。
        ticket = await asyncio.wait_for(sched.acquire("B"), 5)
        ticket.release()

    for kind in ("before", "mid", "error"):
        run(scenario(kind))


def test_swap_queue_api(admin_client):
    """GET /models/swap-queue が調停役の状態を返す。"""
    response = admin_client.get("/api/v1/models/swap-queue")
    assert response.status_code == 200
    body = response.json()
    for key in ("loaded", "running", "swapping", "queue", "next", "recent_swaps", "settings"):
        assert key in body
    assert body["settings"]["aging_seconds"] == 30 and body["settings"]["prefetch"] is True
