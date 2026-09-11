"""停止の合図を誰が出したかを記録する。

2026-09-12、Control Deck 本体が 40 分止まった。journal に「Stopping」の行が
無く、systemd が止めたのではないことは分かったが、誰が撃ったのかは記録の
どこにも無かった。後から画面の履歴を辿って、OpenCode が `kill 3080550` を
実行していたと分かった——`http.server` の残骸と取り違えていた。

signal handler は送り主を教えてくれない。sigwaitinfo なら siginfo が付いてきて、
そこに si_pid と si_uid が入っている。取るには signal を block しておく必要が
あるので、block したうえで受け取り、記録してから改めて自分へ投げ直す。投げ直しは
block を外した見張りの側で行う——C の handler はそこで走り、Python 側の handler
（uvicorn が入れているもの）は従来どおり main thread が実行する。

block したまま見張りが死ぬと、二度と停止できなくなる（systemd が TimeoutStopSec
の後に SIGKILL するまで落ちない）。そうならないよう、何が起きても最後に block を
外す。記録が取れないことより、止まらないことの方が悪い。
"""
from __future__ import annotations

import logging
import os
import signal
import threading

logger = logging.getLogger("control_deck.signals")

WATCHED = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
_ANCESTRY_DEPTH = 4


def _read(path: str) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(4096).decode("utf-8", "replace")
    except OSError:
        return ""


def _proc(pid: int) -> dict[str, str]:
    """/proc から 1 つ分の素性を読む。消えていれば空で返す。"""
    status = _read(f"/proc/{pid}/status")
    fields: dict[str, str] = {}
    for line in status.splitlines():
        key, _, value = line.partition(":")
        if key in ("Name", "PPid", "Uid"):
            fields[key] = value.strip()
    unit = ""
    for line in _read(f"/proc/{pid}/cgroup").splitlines():
        tail = line.rsplit("/", 1)[-1]
        if tail.endswith((".service", ".scope", ".slice")):
            unit = tail
            break
    cmdline = _read(f"/proc/{pid}/cmdline").replace("\0", " ").strip()
    return {"comm": fields.get("Name", ""), "ppid": fields.get("PPid", ""),
            "unit": unit, "cmdline": cmdline[:240]}


def _origin(pid: int) -> str:
    """送り主とその祖先を、読める 1 行にする。

    systemctl 越しに止められると送り主は systemctl 自身になる。誰がそれを
    叩いたかは親にしか書いていないので、遡る。
    """
    if pid <= 0:
        return "送り主不明（カーネル由来か、既に終了）"
    parts: list[str] = []
    current = pid
    for _ in range(_ANCESTRY_DEPTH):
        if current <= 0:
            break
        info = _proc(current)
        if not info["comm"] and not info["cmdline"]:
            parts.append(f"pid={current}（既に終了）")
            break
        label = f"pid={current} {info['comm']}"
        if info["unit"]:
            label += f" [{info['unit']}]"
        if info["cmdline"]:
            label += f" « {info['cmdline']} »"
        parts.append(label)
        try:
            current = int(info["ppid"] or 0)
        except ValueError:
            break
        if current <= 1:
            break
    return " ← ".join(parts)


def _watch(signals: frozenset[int]) -> None:
    try:
        while True:
            try:
                received = signal.sigwaitinfo(signals)
            except InterruptedError:
                continue
            name = signal.Signals(received.si_signo).name
            logger.warning(
                "%s を受け取りました。送り主: uid=%s %s",
                name, received.si_uid, _origin(received.si_pid),
            )
            signal.pthread_sigmask(signal.SIG_UNBLOCK, {received.si_signo})
            signal.raise_signal(received.si_signo)
            return
    except BaseException:  # noqa: BLE001 - 見張りの失敗で停止不能にしない
        logger.exception("停止の合図の見張りが落ちました。以後は素通しします")
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, set(signals))


def install() -> None:
    """main thread から、他の thread を作る前に呼ぶ。

    block は呼んだ thread の mask であり、以後に作る thread が引き継ぐ。順序を
    違えると、引き継がなかった thread に配送されて素通りする。
    """
    signals = frozenset(int(item) for item in WATCHED)
    signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    thread = threading.Thread(target=_watch, args=(signals,),
                              name="control-deck-signal-origin", daemon=True)
    thread.start()
