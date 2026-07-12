/** guacamole-common-js を使ったリモートデスクトップ表示（遅延ロード）。
 * モバイル: タッチ操作（タップ=クリック、長押し=右クリック、2 本指スクロール）。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
// @ts-expect-error 型定義なしパッケージ
import Guacamole from "guacamole-common-js";
import { wsUrl } from "../../api/client";
import { IconX } from "../../components/icons";

interface Connection {
  id: number;
  name: string;
  protocol: string;
}

export default function RemoteViewer({ connection, onExit }: { connection: Connection; onExit: () => void }) {
  const displayRef = useRef<HTMLDivElement>(null);
  const clientRef = useRef<any>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [error, setError] = useState<string | null>(null);
  const [showKeyboard, setShowKeyboard] = useState(false);

  useEffect(() => {
    const host = displayRef.current;
    if (!host) return;
    const width = Math.floor(host.clientWidth || window.innerWidth);
    const height = Math.floor(host.clientHeight || window.innerHeight);

    // 注意: WebSocketTunnel は connect() 時に "?" + データを URL へ付与するため、
    // トンネル URL 自体にはクエリを付けず、寸法は connect データとして渡す。
    const tunnel = new Guacamole.WebSocketTunnel(
      wsUrl(`/remote/connections/${connection.id}/tunnel`),
    );
    const client = new Guacamole.Client(tunnel);
    clientRef.current = client;

    const displayEl = client.getDisplay().getElement();
    host.appendChild(displayEl);

    client.onstatechange = (state: number) => {
      // 3 = CONNECTED, 5 = DISCONNECTED
      if (state === 3) setStatus("connected");
      if (state === 5) setStatus("disconnected");
    };
    client.onerror = (err: any) => {
      setError(err?.message || "接続エラーが発生しました");
      setStatus("disconnected");
    };
    tunnel.onerror = (err: any) => {
      setError(err?.message || "トンネル接続に失敗しました（guacd 未起動の可能性）");
      setStatus("disconnected");
    };

    client.connect(`width=${width}&height=${height}&dpi=96`);

    // マウス
    const mouse = new Guacamole.Mouse(displayEl);
    mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = (state: any) => {
      client.sendMouseState(state);
    };
    // タッチ（タップ=クリック、長押し=右クリック）
    const touch = new Guacamole.Mouse.Touchpad(displayEl);
    touch.onmousedown = touch.onmouseup = touch.onmousemove = (state: any) => {
      client.sendMouseState(state);
    };
    // キーボード
    const keyboard = new Guacamole.Keyboard(document);
    keyboard.onkeydown = (keysym: number) => client.sendKeyEvent(1, keysym);
    keyboard.onkeyup = (keysym: number) => client.sendKeyEvent(0, keysym);

    const onResize = () => {
      const w = Math.floor(host.clientWidth);
      const h = Math.floor(host.clientHeight);
      if (w && h) client.sendSize(w, h);
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(host);

    return () => {
      observer.disconnect();
      keyboard.onkeydown = null;
      keyboard.onkeyup = null;
      try {
        client.disconnect();
      } catch {
        /* ignore */
      }
      if (displayEl.parentNode === host) host.removeChild(displayEl);
    };
  }, [connection.id]);

  const sendCtrlAltDel = () => {
    const c = clientRef.current;
    if (!c) return;
    for (const k of [0xffe3, 0xffe9, 0xffff]) c.sendKeyEvent(1, k);
    for (const k of [0xffff, 0xffe9, 0xffe3]) c.sendKeyEvent(0, k);
  };

  return createPortal(
    <div className="fixed inset-0 z-40 flex flex-col bg-black">
      <div className="safe-top flex shrink-0 items-center gap-2 border-b border-zinc-800 bg-zinc-950 px-3 py-1.5 text-zinc-200">
        <span className="truncate text-sm font-medium">{connection.name}</span>
        <span className={`text-xs ${status === "connected" ? "text-emerald-400" : status === "disconnected" ? "text-red-400" : "text-zinc-400"}`}>
          {status === "connected" ? "接続中" : status === "disconnected" ? "切断" : "接続中..."}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button onClick={sendCtrlAltDel} className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800">Ctrl+Alt+Del</button>
          <button onClick={() => setShowKeyboard((v) => !v)} className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 md:hidden">⌨</button>
          <button onClick={onExit} aria-label="切断して閉じる" className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-800"><IconX /></button>
        </div>
      </div>

      <div ref={displayRef} className="relative min-h-0 flex-1 overflow-hidden [&_canvas]:mx-auto" />

      {error && (
        <div className="shrink-0 bg-red-950/60 px-4 py-2 text-center text-xs text-red-300">{error}</div>
      )}

      {/* モバイル用の隠しフォーカス入力（ソフトキーボード呼び出し） */}
      {showKeyboard && (
        <input
          autoFocus
          className="absolute -left-full h-0 w-0 opacity-0"
          onBlur={() => setShowKeyboard(false)}
        />
      )}
    </div>,
    document.body,
  );
}
