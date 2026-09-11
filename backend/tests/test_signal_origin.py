"""停止の合図の送り主が記録に残ることを、実際に撃って確かめる。

2026-09-12、Control Deck 本体が 40 分止まった。journal に「Stopping」の行が無く
systemd が止めたのではないことは分かったが、誰が撃ったのかはどこにも無かった。
後から画面の履歴を辿って、OpenCode が `kill <本体の PID>` を実行していたと分かった。

見張りは signal を block して受け取る。block したまま見張りが死ぬと二度と停止でき
なくなるので、「記録が残ること」と同じ重みで「今までどおり止まること」を縛る。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

# 見張りを入れて、ただ待つだけの子。止まったことが分かるよう終わりに印を出す。
CHILD = """
import logging, signal, sys, time
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s %(message)s")
from app.maintenance import signal_origin

stopping = []
signal.signal(signal.SIGTERM, lambda signum, frame: stopping.append(signum))

signal_origin.install()
print("READY", flush=True)
for _ in range(600):
    if stopping:
        print("STOPPED CLEANLY", flush=True)
        break
    time.sleep(0.05)
"""


def _spawn() -> subprocess.Popen[str]:
    environment = dict(os.environ, PYTHONPATH=str(BACKEND), PYTHONUNBUFFERED="1")
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD], cwd=str(BACKEND), env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert child.stdout is not None
    deadline = time.time() + 30
    while time.time() < deadline:
        line = child.stdout.readline()
        if line.strip() == "READY":
            return child
        if child.poll() is not None:
            raise AssertionError(f"子が立ち上がらなかった: {child.communicate()}")
    raise AssertionError("子が READY を出さなかった")


def test_the_sender_of_a_stop_signal_is_named_in_the_log():
    """誰が撃ったかを、PID と系譜で残す。

    handler だけでは送り主が分からない。sigwaitinfo なら siginfo が付いてきて、
    そこに si_pid と si_uid が入っている。
    """
    child = _spawn()
    child.send_signal(signal.SIGTERM)
    stdout, stderr = child.communicate(timeout=30)

    assert "control_deck.signals" in stderr, f"記録が出ていない: {stderr}"
    assert "SIGTERM" in stderr
    # 撃ったのはこの試験自身である。名指しできていなければ意味が無い。
    assert f"pid={os.getpid()}" in stderr, f"送り主を名指しできていない: {stderr}"
    assert f"uid={os.getuid()}" in stderr


def test_the_process_still_stops_the_way_it_did_before():
    """記録を取るために止まらなくなっては、直した意味が無い。

    見張りは signal を block して受け取るので、投げ直しを忘れると handler が
    走らない。systemd から見れば「止まらない service」になり、TimeoutStopSec の
    後に SIGKILL されるまで居座る。
    """
    child = _spawn()
    child.send_signal(signal.SIGTERM)
    stdout, _stderr = child.communicate(timeout=30)

    assert "STOPPED CLEANLY" in stdout, "handler が走っていない（止まらなくなっている）"
    assert child.returncode == 0
