"""uvicorn 起動エントリ。待受ホスト/ポートを config.yaml（server.host/port）から読む。

環境変数 CONTROL_DECK_HOST / CONTROL_DECK_PORT があれば優先する。
これにより systemd ユニットにホストをハードコードせず、設定で LAN/Tailscale 公開を制御できる。
"""
from __future__ import annotations

import os

import uvicorn

from app.config import get_config
from app.maintenance import signal_origin

# 停止の合図を受けてから、残っている接続を切って落ちるまでの上限（秒）。
#
# uvicorn の既定は「待ち続ける」である。LLM ゲートウェイは応答を流したまま
# 数分保つので、restart のたびに接続が空くのを待ち、systemd の
# DefaultTimeoutStopSec（90秒）に負けて SIGKILL される。
# 実測 2026-09-14 07:12:58 に SIGTERM、07:13:57 にまだ chat/completions が
# 1 本流れており、07:14:28 に Killing process ... with signal SIGKILL。
#
# SIGKILL は shutdown handler を 1 つも走らせない（lease の返却も、prompt 状態の
# 書き出しも、監査の書き終わりも飛ぶ）。上限を切れば、残りを切ってでも
# 自分の後始末をしてから落ちる。90 秒より十分手前に置く。
GRACEFUL_SHUTDOWN_SECONDS = 30


def main() -> None:
    # 停止の合図の見張りは、他の thread を作る前に置く。block は呼んだ thread の
    # mask であり、以後に作る thread が引き継ぐためで、順序を違えると引き継がな
    # かった thread に配送されて素通りする。
    signal_origin.install()
    cfg = get_config()
    host = os.environ.get("CONTROL_DECK_HOST") or cfg.server.host
    port = int(os.environ.get("CONTROL_DECK_PORT") or cfg.server.port)
    uvicorn.run("app.main:app", host=host, port=port, log_level="info",
                timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS)


if __name__ == "__main__":
    main()
