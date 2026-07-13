"""guacd（Guacamole プロキシデーモン）との接続とハンドシェイク。

guacamole-lite 相当の実装: WebSocket クライアント（guacamole-common-js）と guacd(TCP:4822) を
橋渡しする。接続開始時のハンドシェイク（select → args → size/audio/video/image → connect）を
サーバー側で行い、以降は raw ストリームを双方向にパイプする。

Guacamole プロトコル命令形式: 各要素は "LENGTH.VALUE"、命令は要素をカンマ区切りで並べ ";" で終端。
"""
from __future__ import annotations

import asyncio
import shutil

GUACD_DEFAULT_HOST = "127.0.0.1"
GUACD_DEFAULT_PORT = 4822


def guacd_available() -> bool:
    return shutil.which("guacd") is not None


def encode_instruction(*elements: str) -> bytes:
    parts = [f"{len(el)}.{el}" for el in elements]
    return (",".join(parts) + ";").encode("utf-8")


class InstructionParser:
    """バイトストリームから Guacamole 命令（要素リスト）を切り出す。"""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, data: str) -> list[list[str]]:
        self._buf += data
        instructions: list[list[str]] = []
        while True:
            elements, consumed = self._try_parse_one()
            if elements is None:
                break
            instructions.append(elements)
            self._buf = self._buf[consumed:]
        return instructions

    def _try_parse_one(self) -> tuple[list[str] | None, int]:
        elements: list[str] = []
        i = 0
        buf = self._buf
        while i < len(buf):
            dot = buf.find(".", i)
            if dot == -1:
                return None, 0
            try:
                length = int(buf[i:dot])
            except ValueError:
                return None, 0
            start = dot + 1
            end = start + length
            if end >= len(buf):
                return None, 0
            elements.append(buf[start:end])
            terminator = buf[end]
            if terminator == ";":
                return elements, end + 1
            if terminator == ",":
                i = end + 1
                continue
            return None, 0
        return None, 0


class InstructionSplitter:
    """ストリームを Guacamole 命令境界で区切る。

    guacamole-common-js の WebSocketTunnel は「各 WebSocket メッセージ内で命令が
    完結している」前提でパースし、メッセージをまたぐバッファを持たない。
    命令の途中で分割して送ると、その命令（初回全画面 PNG など）は黙って捨てられる。
    そのため TCP チャンクをそのまま流さず、完全な命令列だけを切り出して送る。
    """

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, text: str) -> str:
        """text を取り込み、完全な命令のみからなる先頭部分を返す。残りは持ち越す。"""
        self._buf += text
        buf = self._buf
        n = len(buf)
        end = 0  # 完全な命令列の終端
        i = 0
        while i < n:
            dot = buf.find(".", i)
            if dot == -1:
                break
            try:
                length = int(buf[i:dot])
            except ValueError:
                # プロトコル違反: 同期不能なので全部流して切り詰め（安全弁）
                end = n
                break
            term = dot + 1 + length
            if term >= n:
                break  # 要素本体または終端記号が未着
            c = buf[term]
            if c == ";":
                end = term + 1
                i = end
            elif c == ",":
                i = term + 1
            else:
                end = n  # プロトコル違反（安全弁）
                break
        self._buf = buf[end:]
        return buf[:end]


async def perform_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    protocol: str,
    params: dict[str, str],
    width: int,
    height: int,
    dpi: int = 96,
) -> None:
    """guacd とのハンドシェイクを行う。失敗時は例外。"""
    # 1. select
    writer.write(encode_instruction("select", protocol))
    await writer.drain()

    # 2. args を受信
    parser = InstructionParser()
    args_names: list[str] | None = None
    deadline = asyncio.get_event_loop().time() + 15
    while args_names is None:
        if asyncio.get_event_loop().time() > deadline:
            raise TimeoutError("guacd の args を受信できませんでした")
        chunk = await asyncio.wait_for(reader.read(4096), timeout=15)
        if not chunk:
            raise ConnectionError("guacd 接続が閉じられました")
        for instr in parser.feed(chunk.decode("utf-8", errors="replace")):
            if instr and instr[0] == "args":
                args_names = instr[1:]
                break

    # 3. クライアント能力を送信
    writer.write(encode_instruction("size", str(width), str(height), str(dpi)))
    writer.write(encode_instruction("audio", "audio/L8", "audio/L16"))
    writer.write(encode_instruction("video"))
    writer.write(encode_instruction("image", "image/png", "image/jpeg", "image/webp"))
    await writer.drain()

    # 4. connect: args の順（先頭のバージョン要素は除く）に値を並べる
    values = []
    for name in args_names:
        if name.startswith("VERSION_"):
            values.append(name)  # バージョンはそのまま返す
        else:
            values.append(params.get(name, ""))
    writer.write(encode_instruction("connect", *values))
    await writer.drain()

    # 残った受信データ（初期描画命令）は tunnel 側で処理するため parser に残す
    return None


async def open_guacd(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(asyncio.open_connection(host, port), timeout=10)
