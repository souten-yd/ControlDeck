"""診断: 記録・詰まりの検出・journal の読み取り・レポートの秘密伏せ。"""
import time

from tests.conftest import CSRF_HEADERS


def test_redact_hides_secrets():
    from app.diagnostics.report import redact

    text = ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz token=supersecret123 cdk-0123456789abcdef "
            "api_key: \"xyz12345\" mail me@example.com sk-abcdefghijklmnop")
    out = redact(text)
    for secret in ("abcdefghijklmnopqrstuvwxyz", "supersecret123", "0123456789abcdef", "xyz12345",
                   "me@example.com", "sk-abcdefghijklmnop"):
        assert secret not in out, out


def test_store_round_trip(tmp_path, monkeypatch):
    from app.diagnostics import store

    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    store.record("log", level="ERROR", message="boom")
    store.record("loop_stall", seconds=2.5, stack=["a.py:1 in f"])
    assert [e["kind"] for e in store.read()] == ["loop_stall", "log"]
    assert [e["kind"] for e in store.read(kinds={"log"})] == ["log"]
    assert store.read(since=time.time() + 10) == []


def test_journal_parses_oom_and_restarts(monkeypatch):
    from app.diagnostics import journal

    t = 1_700_000_000.0
    kernel = [
        {"ts": t, "message": "oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,global_oom,"
                             "task_memcg=/user.slice/user-1000.slice/user@1000.service/app.slice/control-deck-strata-x.service,task=strata-host", "unit": ""},
        {"ts": t + 1, "message": "Out of memory: Killed process 552286 (strata-host) total-vm:118903904kB, "
                                 "anon-rss:57179032kB, file-rss:224kB, shmem-rss:11499080kB, UID:1000", "unit": ""},
    ]
    user = [
        {"ts": t, "message": "control-deck-web.service: Watchdog timeout (limit 1min)!", "unit": "control-deck-web.service"},
        {"ts": t + 2, "message": "control-deck-web.service: Failed with result 'watchdog'.", "unit": "control-deck-web.service"},
        {"ts": t + 5, "message": "Started control-deck-web.service - Ubuntu Control Deck Web.", "unit": "control-deck-web.service"},
        {"ts": t + 9, "message": "cdapp-llama-rerank-x.service: Failed with result 'exit-code'.", "unit": "cdapp-llama-rerank-x.service"},
        {"ts": t + 9, "message": "unrelated.service: Failed with result 'exit-code'.", "unit": "unrelated.service"},
    ]
    monkeypatch.setattr(journal, "_journal", lambda args, since, timeout=30: kernel if "-k" in args else user)
    oom = journal.oom_kills(t - 1)
    assert oom == [{"ts": t + 1, "pid": 552286, "process": "strata-host", "anon_rss_gb": 54.53,
                    "shmem_rss_gb": 10.97, "unit": "control-deck-strata-x.service"}]
    events = journal.unit_events(t - 1)
    assert [r["cause"] for r in events["restarts"]] == ["watchdog（応答なし）"]
    assert [f["unit"] for f in events["failures"]] == ["cdapp-llama-rerank-x.service"]


def test_stall_watcher_records_the_blocking_function(tmp_path, monkeypatch):
    """loop を 1.6 秒塞ぐと、塞いだ関数の場所が記録に残る。"""
    import threading

    from app.diagnostics import recorder, store

    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    state = {"tick": time.monotonic(), "thread": threading.get_ident()}
    stop = threading.Event()

    def blocking_helper_for_test():
        time.sleep(1.6)

    watcher = threading.Thread(target=recorder._stall_watcher, daemon=True,
                               args=(lambda: state["tick"], lambda: state["thread"], stop))
    watcher.start()
    for _ in range(4):
        state["tick"] = time.monotonic()
        time.sleep(0.1)
    blocking_helper_for_test()
    for _ in range(6):
        state["tick"] = time.monotonic()
        time.sleep(0.1)
    stop.set()
    watcher.join(timeout=2)
    stalls = store.read(kinds={"loop_stall"})
    assert stalls and stalls[0]["seconds"] >= 1.0
    assert any("blocking_helper_for_test" in line for line in stalls[0]["stack"])


def test_api_requires_admin_and_returns_report(admin_client, monkeypatch):
    from app.diagnostics import journal, selftest

    monkeypatch.setattr(journal, "summary", lambda hours=72.0: {"hours": hours, "restarts": [], "unit_failures": [], "oom_kills": []})
    monkeypatch.setattr(selftest, "_failed_units", lambda: (True, "なし"))
    r = admin_client.get("/api/v1/diagnostics/report?format=md")
    assert r.status_code == 200, r.text
    assert "# ControlDeck 診断レポート" in r.text and "## 自己診断" in r.text
    assert admin_client.post("/api/v1/diagnostics/selftest", headers=CSRF_HEADERS).json()["overall"] in ("ok", "warn", "fail")
    assert admin_client.post("/api/v1/diagnostics/tests", json={"selection": "tests/x.py; rm -rf /"},
                             headers=CSRF_HEADERS).status_code == 422


def test_agent_mcp_offers_diagnostics_to_admins(monkeypatch):
    from app.addons import agent_mcp

    tool = agent_mcp._diagnostics_tool()
    assert tool["name"] == "control_deck.diagnostics"
    assert set(tool["inputSchema"]["properties"]) == {"hours"}
