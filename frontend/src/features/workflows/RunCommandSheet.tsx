/** 返事の中のコマンドを、確認のシートを通して ControlDeck の Terminal で実行する（2026-11-27 依頼）。
 *
 * MarkdownMessage のコードブロックの「▶ 実行」から開く（RunCommandContext を出す所だけが対象。
 * 特許・資料の画面など、provider の無い所ではボタンが出ない）。実行する内容と場所を見せてから、
 * 既存の tmux のセッションを作る API（POST /terminals・cwd つき）でセッションを起こし、Terminal の
 * 画面へ受け渡す（sessionStorage → /terminal?session=<id>。XtermView が接続した後で、既存の貼り付けの
 * 経路で流し込む。入力は端末にそのまま見える）。新しい危ない API は作らない。
 * sudo・rm -rf などが含まれるときは赤字で注意し、確認を 2 段にする。押せるのは管理者だけ。 */
import { createContext, useState } from "react";
import { BottomSheet } from "../../components/ui";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

/** コードブロックの「▶ 実行」を押した内容。command は端末へ流す本文（プロンプトの印は外してある） */
export interface RunCommandRequest { command: string; language: string }

/** MarkdownMessage のコードブロックが呼ぶ口（provider の無い所では null → ボタンを出さない） */
export const RunCommandContext = createContext<((request: RunCommandRequest) => void) | null>(null);

/** 危険なコマンドの注意。1 つの判定関数にまとめる（語の辞書を 1 事例ずつ足すのは AGENTS の決まりで禁止）。
 *  注意は実行を止めない（管理者が内容を見て 2 段で確かめる） */
export function dangerNotes(command: string): string[] {
  const notes: string[] = [];
  if (/\bsudo\b/.test(command)) notes.push("sudo（管理者権限）での実行を含みます");
  if (/\brm\b[^&|;\n]*\s-[a-zA-Z]*r[a-zA-Z]*f\b|\brm\b[^&|;\n]*\s-[a-zA-Z]*f[a-zA-Z]*r\b|\brm\b\s+--recursive\b/.test(command))
    notes.push("rm -rf（ディレクトリの再帰的な削除）を含みます");
  if (/\bmkfs(\.\w+)?\b/.test(command)) notes.push("mkfs（ファイルシステムの初期化）を含みます");
  if (/\bdd\b/.test(command)) notes.push("dd（ディスクへの直接書き込み）を含みます");
  if (/\|\s*(sudo\s+)?(ba|z|da|k)?sh\b/.test(command)) notes.push("ダウンロードした内容をそのままシェルへ流します（curl … | sh など）");
  if (/\b(shutdown|reboot|halt|poweroff)\b/.test(command)) notes.push("シャットダウン・再起動を含みます");
  if (/>\s*\/dev\//.test(command)) notes.push("デバイスへの直接の書き込みを含みます");
  return notes;
}

/** 確認のシート。実行する場所は呼び出し側が決めたもの（その会話の作業場か、無ければホーム） */
export function RunCommandSheet({ request, placeLabel, placeValue, onClose, onRan }: {
  request: RunCommandRequest;
  /** シートに見せる場所の名前（作業場の名前・「ホーム」） */
  placeLabel: string;
  /** セッションの cwd に使うパス（null なら cwd を送らない = ホーム） */
  placeValue: string | null;
  onClose: () => void;
  /** 実行の受け渡し（Terminal へ遷移する） */
  onRan: (sessionId: string, text: string) => void;
}) {
  const show = useToasts((s) => s.show);
  const [working, setWorking] = useState(false);
  const [armed, setArmed] = useState(false);
  const notes = dangerNotes(request.command);

  const run = async () => {
    if (notes.length && !armed) {
      setArmed(true);   // 危険な内容は 2 段で確かめる（1 回目は用意だけ）
      return;
    }
    if (working) return;
    setWorking(true);
    try {
      // 既存のセッション作成の API だけを使う（cwd は 2026-11-27 に足した任意項目）
      const session = await api<{ id: string }>("/terminals",
        { method: "POST", ...(placeValue ? { json: { cwd: placeValue } } : {}) });
      onRan(session.id, request.command);
    } catch (e) {
      show(e instanceof Error ? e.message : "ターミナルのセッションを作れませんでした", "error");
    } finally {
      setWorking(false);
    }
  };

  return (
    <BottomSheet title="ターミナルで実行" onClose={onClose}>
      <div className="space-y-3 pt-1">
        <div>
          <p className="mb-1 text-xs font-medium text-zinc-500 dark:text-zinc-400">実行する内容</p>
          <pre data-testid="run-command-text"
            className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-zinc-900 p-2.5 font-mono text-[12px] leading-snug text-zinc-100 dark:bg-black/40">
            {request.command}
          </pre>
        </div>
        <p data-testid="run-command-place" className="text-xs text-zinc-600 dark:text-zinc-300">
          実行する場所: <span className="font-medium">{placeLabel}</span>
          {placeValue && <span className="block break-all text-zinc-500 dark:text-zinc-400">{placeValue}</span>}
        </p>
        {notes.length > 0 && (
          <ul data-testid="run-command-warnings" className="space-y-1 rounded-lg border border-red-200 bg-red-50 p-2.5 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
            {notes.map((note) => <li key={note}>⚠️ {note}</li>)}
            {!armed && <li className="font-medium">実行するには、もう一度「ターミナルで実行」を押してください。</li>}
          </ul>
        )}
        <div className="sheet-actions sticky bottom-0 -mx-5 flex flex-wrap gap-2 border-t border-zinc-100 bg-white/90 px-5 pb-4 pt-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/90">
          <button type="button" onClick={onClose} className="min-h-11 rounded-lg border border-zinc-300 px-4 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800">
            キャンセル
          </button>
          <button type="button" data-testid="run-command-confirm" onClick={() => void run()} disabled={working}
            className="min-h-11 flex-1 rounded-lg bg-sky-600 px-4 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50">
            {working ? "セッションを作っています…" : armed || notes.length === 0 ? "ターミナルで実行" : "ターミナルで実行（注意を読んで押す）"}
          </button>
        </div>
      </div>
    </BottomSheet>
  );
}
