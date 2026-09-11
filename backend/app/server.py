"""uvicorn 起動エントリ。待受ホスト/ポートを config.yaml（server.host/port）から読む。

環境変数 CONTROL_DECK_HOST / CONTROL_DECK_PORT があれば優先する。
これにより systemd ユニットにホストをハードコードせず、設定で LAN/Tailscale 公開を制御できる。
"""
from __future__ import annotations

import os

import uvicorn

from app.config import get_config
from app.maintenance import signal_origin


def main() -> None:
    # 停止の合図の見張りは、他の thread を作る前に置く。block は呼んだ thread の
    # mask であり、以後に作る thread が引き継ぐためで、順序を違えると引き継がな
    # かった thread に配送されて素通りする。
    signal_origin.install()
    cfg = get_config()
    host = os.environ.get("CONTROL_DECK_HOST") or cfg.server.host
    port = int(os.environ.get("CONTROL_DECK_PORT") or cfg.server.port)
    uvicorn.run("app.main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
