/** guacamole-common-js を使ったリモートデスクトップ表示（遅延ロード）。
 * タッチ操作（タッチパッド方式・相対移動）:
 *   1本指移動=カーソル移動 / タップ=左クリック / 長押し→移動=ドラッグ
 *   2本指タップ=右クリック / 2本指上下=スクロール / 3本指タップ=キーボード表示切替
 * 表示: タッチ端末はリモート解像度を画面の2倍で確保し縮小表示
 *   （ウィンドウが端末画面より大きくても収まる）。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
// @ts-expect-error 型定義なしパッケージ
import Guacamole from "guacamole-common-js";
import { wsUrl } from "../../api/client";
import { useToasts } from "../../stores";
import { IconX } from "../../components/icons";

// keysym: Ctrl 左 / C / V
const K_CTRL = 0xffe3;
const K_C = 0x63;
const K_V = 0x76;

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
  const [clipBusy, setClipBusy] = useState<"" | "copy" | "paste">("");
  const show = useToasts((s) => s.show);
  // リモートからのクリップボード受信を待つための resolver（コピー操作時のみセット）
  const clipWaitRef = useRef<((text: string) => void) | null>(null);

  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  /** 修飾キー付きでキーを一発送る（例: Ctrl+C） */
  const tapKey = async (mod: number, key: number) => {
    const c = clientRef.current;
    if (!c) return;
    c.sendKeyEvent(1, mod);
    c.sendKeyEvent(1, key);
    await sleep(30);
    c.sendKeyEvent(0, key);
    c.sendKeyEvent(0, mod);
  };

  /** この端末 → リモートへテキストを送る（Guacamole clipboard ストリーム） */
  const sendClipboard = (text: string) => {
    const c = clientRef.current;
    if (!c || !text) return;
    const stream = c.createClipboardStream("text/plain");
    const writer = new Guacamole.StringWriter(stream);
    writer.sendText(text);
    writer.sendEnd();
  };

  /** コピー: リモートで選択中のものを Ctrl+C させて受信 → この端末のクリップボードへ */
  const copyFromRemote = async () => {
    if (clipBusy) return;
    setClipBusy("copy");
    try {
      const received = new Promise<string>((resolve, reject) => {
        clipWaitRef.current = resolve;
        setTimeout(() => reject(new Error("timeout")), 1500);
      });
      await tapKey(K_CTRL, K_C); // リモートに Ctrl+C
      const text = await received.catch(() => "");
      clipWaitRef.current = null;
      if (!text) {
        show("コピーできませんでした。リモート側でテキストを選択してから押してください", "error");
        return;
      }
      await navigator.clipboard.writeText(text);
      show(`コピーしました（${text.length} 文字）`);
    } catch {
      show("この端末のクリップボードへ書き込めませんでした", "error");
    } finally {
      clipWaitRef.current = null;
      setClipBusy("");
    }
  };

  /** ペースト: この端末のクリップボード → リモートへ送信 → Ctrl+V */
  const pasteToRemote = async () => {
    if (clipBusy) return;
    setClipBusy("paste");
    try {
      const text = await navigator.clipboard.readText();
      if (!text) {
        show("この端末のクリップボードが空です", "error");
        return;
      }
      sendClipboard(text);
      await sleep(120); // ストリーム転送がリモートに届くのを待ってから貼り付け
      await tapKey(K_CTRL, K_V);
      show(`ペーストしました（${text.length} 文字）`);
    } catch {
      show("クリップボードの読み取りが許可されませんでした", "error");
    } finally {
      setClipBusy("");
    }
  };

  useEffect(() => {
    const host = displayRef.current;
    if (!host) return;
    // タッチ主体の端末のみ2倍解像度+縮小表示。マウス主体（デスクトップ、
    // タッチ対応ノート含む）は等倍・通常のマウス操作をそのまま受け付ける
    const isTouch = window.matchMedia?.("(pointer: coarse)").matches ?? "ontouchstart" in window;
    const FACTOR = isTouch ? 2 : 1;
    const width = Math.floor((host.clientWidth || window.innerWidth) * FACTOR);
    const height = Math.floor((host.clientHeight || window.innerHeight) * FACTOR);

    // 注意: WebSocketTunnel は connect() 時に "?" + データを URL へ付与するため、
    // トンネル URL 自体にはクエリを付けず、寸法は connect データとして渡す。
    const tunnel = new Guacamole.WebSocketTunnel(
      wsUrl(`/remote/connections/${connection.id}/tunnel`),
    );
    const client = new Guacamole.Client(tunnel);
    clientRef.current = client;

    const display = client.getDisplay();
    const displayEl = display.getElement();
    displayEl.style.margin = "0 auto";
    host.appendChild(displayEl);

    // リモート画面全体がクライアントに収まるよう縮小（拡大はしない）
    const rescale = () => {
      const rw = display.getWidth();
      const rh = display.getHeight();
      if (!rw || !rh || !host.clientWidth) return;
      display.scale(Math.min(host.clientWidth / rw, host.clientHeight / rh, 1));
    };
    display.onresize = rescale;

    client.onstatechange = (state: number) => {
      // 3 = CONNECTED, 5 = DISCONNECTED
      if (state === 3) {
        setStatus("connected");
        rescale();
      }
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

    // リモート → この端末: クリップボード受信。コピー操作待ちがあれば解決する
    client.onclipboard = (stream: any, mimetype: string) => {
      if (!/^text\//.test(mimetype)) {
        stream.sendAck("unsupported", 0x0100);
        return;
      }
      const reader = new Guacamole.StringReader(stream);
      let data = "";
      reader.ontext = (t: string) => { data += t; };
      reader.onend = () => {
        if (clipWaitRef.current) clipWaitRef.current(data);
      };
    };

    client.connect(`width=${width}&height=${height}&dpi=96`);

    // マウス（デスクトップ）。縮小表示中は要素座標→リモート座標へ換算する
    const mouse = new Guacamole.Mouse(displayEl);
    mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = (state: any) => {
      const s = display.getScale() || 1;
      client.sendMouseState(
        new Guacamole.Mouse.State(
          state.x / s, state.y / s,
          state.left, state.middle, state.right, state.up, state.down,
        ),
      );
    };

    // タッチ: タッチパッド方式（相対移動）のカスタム実装。
    // Touchpad(ライブラリ) には長押しドラッグ/2本指右クリック/3本指がないため自前で扱う。
    const cur = { x: width / 2, y: height / 2 }; // リモート座標のカーソル位置
    let dragging = false; // 長押し後の左ボタン保持
    let longPress: number | undefined;
    let scrollAcc = 0;
    let gesture: {
      startT: number;
      moved: number;
      maxFingers: number;
      scrolled: boolean;
      lastX: number;
      lastY: number;
      last2Y: number;
    } | null = null;

    const sendPointer = (btn?: { left?: boolean; right?: boolean; up?: boolean; down?: boolean }) => {
      client.sendMouseState(
        new Guacamole.Mouse.State(
          cur.x, cur.y,
          dragging || !!btn?.left, false, !!btn?.right, !!btn?.up, !!btn?.down,
        ),
      );
    };

    const onTouchStart = (e: TouchEvent) => {
      e.preventDefault();
      const t = e.touches;
      const now = performance.now();
      if (!gesture) {
        gesture = {
          startT: now, moved: 0, maxFingers: t.length, scrolled: false,
          lastX: t[0].clientX, lastY: t[0].clientY,
          last2Y: t.length >= 2 ? (t[0].clientY + t[1].clientY) / 2 : t[0].clientY,
        };
      } else {
        gesture.maxFingers = Math.max(gesture.maxFingers, t.length);
        gesture.lastX = t[0].clientX;
        gesture.lastY = t[0].clientY;
        if (t.length >= 2) gesture.last2Y = (t[0].clientY + t[1].clientY) / 2;
      }
      window.clearTimeout(longPress);
      if (t.length === 1 && !dragging) {
        // 静止したまま長押し → 左ボタンを押したままにする（以降の移動がドラッグになる）
        longPress = window.setTimeout(() => {
          dragging = true;
          sendPointer();
        }, 450);
      }
    };

    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault();
      if (!gesture) return;
      const t = e.touches;
      const dx = t[0].clientX - gesture.lastX;
      const dy = t[0].clientY - gesture.lastY;
      gesture.moved += Math.abs(dx) + Math.abs(dy);
      gesture.lastX = t[0].clientX;
      gesture.lastY = t[0].clientY;

      if (t.length === 1) {
        if (gesture.moved > 8 && !dragging) window.clearTimeout(longPress);
        // 画面上の指の移動量とカーソルの見かけの移動が 1:1 になるようスケール換算
        const s = display.getScale() || 1;
        cur.x = Math.min(Math.max(cur.x + dx / s, 0), (display.getWidth() || width) - 1);
        cur.y = Math.min(Math.max(cur.y + dy / s, 0), (display.getHeight() || height) - 1);
        sendPointer();
      } else if (t.length >= 2) {
        window.clearTimeout(longPress);
        const avgY = (t[0].clientY + t[1].clientY) / 2;
        scrollAcc += avgY - gesture.last2Y;
        gesture.last2Y = avgY;
        // 30px ごとにホイール1ノッチ（指を下へ=上スクロール）
        while (Math.abs(scrollAcc) >= 30) {
          const up = scrollAcc > 0;
          scrollAcc += up ? -30 : 30;
          sendPointer(up ? { up: true } : { down: true });
          sendPointer();
          gesture.scrolled = true;
        }
      }
    };

    const onTouchEnd = (e: TouchEvent) => {
      e.preventDefault();
      window.clearTimeout(longPress);
      if (e.touches.length > 0 || !gesture) return; // 全ての指が離れたときだけ確定
      const g = gesture;
      gesture = null;
      scrollAcc = 0;
      if (dragging) {
        dragging = false;
        sendPointer(); // ドラッグ終了（左ボタン解放）
        return;
      }
      const dur = performance.now() - g.startT;
      if (!g.scrolled && g.moved < 12 && dur < 350) {
        if (g.maxFingers === 1) {
          sendPointer({ left: true });
          sendPointer();
        } else if (g.maxFingers === 2) {
          sendPointer({ right: true });
          sendPointer();
        } else {
          setShowKeyboard((v) => !v);
        }
      }
    };

    const onTouchCancel = () => {
      window.clearTimeout(longPress);
      gesture = null;
      scrollAcc = 0;
      if (dragging) {
        dragging = false;
        sendPointer();
      }
    };

    host.addEventListener("touchstart", onTouchStart, { passive: false });
    host.addEventListener("touchmove", onTouchMove, { passive: false });
    host.addEventListener("touchend", onTouchEnd, { passive: false });
    host.addEventListener("touchcancel", onTouchCancel);

    // キーボード
    const keyboard = new Guacamole.Keyboard(document);
    keyboard.onkeydown = (keysym: number) => client.sendKeyEvent(1, keysym);
    keyboard.onkeyup = (keysym: number) => client.sendKeyEvent(0, keysym);

    const onResize = () => {
      const w = Math.floor(host.clientWidth * FACTOR);
      const h = Math.floor(host.clientHeight * FACTOR);
      if (w && h) client.sendSize(w, h);
      rescale();
    };
    const observer = new ResizeObserver(onResize);
    observer.observe(host);

    return () => {
      observer.disconnect();
      host.removeEventListener("touchstart", onTouchStart);
      host.removeEventListener("touchmove", onTouchMove);
      host.removeEventListener("touchend", onTouchEnd);
      host.removeEventListener("touchcancel", onTouchCancel);
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
          <button
            onClick={copyFromRemote}
            disabled={status !== "connected" || !!clipBusy}
            title="リモートで選択中のテキストをこの端末にコピー"
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
          >
            {clipBusy === "copy" ? "⏳" : "📋"}<span className="hidden sm:inline">コピー</span>
          </button>
          <button
            onClick={pasteToRemote}
            disabled={status !== "connected" || !!clipBusy}
            title="この端末のクリップボードをリモートに貼り付け"
            className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
          >
            {clipBusy === "paste" ? "⏳" : "📥"}<span className="hidden sm:inline">貼付</span>
          </button>
          <button onClick={sendCtrlAltDel} className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800">Ctrl+Alt+Del</button>
          <button onClick={() => setShowKeyboard((v) => !v)} className="rounded-lg px-2.5 py-1.5 text-xs font-medium text-zinc-300 hover:bg-zinc-800 md:hidden">⌨</button>
          <button onClick={onExit} aria-label="切断して閉じる" className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-800"><IconX /></button>
        </div>
      </div>

      <div ref={displayRef} className="relative min-h-0 flex-1 touch-none overflow-hidden" />

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
