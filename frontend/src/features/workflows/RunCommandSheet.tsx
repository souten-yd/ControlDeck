/** 返事の中のコマンドを、確認のシートを通して実行し、出力をシートの中に見せる（2026-11-27 依頼・2026-11-28 に画面内実行へ）。
 *
 * MarkdownMessage のコードブロックの「▶ 実行」から開く（RunCommandContext を出す所だけが対象）。
 * 内容と場所を見せてから POST /terminals/run（tmux のセッションを作ってコマンドを貼る）で実行し、
 * 1 秒ごとに出力を読む。sudo などがパスワードを求めたらポップアップで入力を受け、標準入力で端末へ送る
 * （入力値は送る関数のローカル変数だけ。state・ログ・保存に残さない）。続きを手で行うときは
 * 「ターミナルで開く」で同じセッションの Terminal へ移る。押せるのは管理者だけ。 */
import { createContext, useEffect, useRef, useState } from "react";
import { BottomSheet, ConfirmDialog } from "../../components/ui";
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

interface RunState { output: string; done: boolean; exit_code: number | null; password_prompt: boolean }

/** 確認のシート。実行する場所は呼び出し側が決めたもの（その会話の作業場か、無ければホーム） */
export function RunCommandSheet({ request, placeLabel, placeValue, onClose, onOpenTerminal }: {
  request: RunCommandRequest;
  /** シートに見せる場所の名前（作業場の名前・「ホーム」） */
  placeLabel: string;
  /** セッションの cwd に使うパス（null なら cwd を送らない = ホーム） */
  placeValue: string | null;
  onClose: () => void;
  onOpenTerminal: (sessionId: string) => void;
}) {
  const show = useToasts((s) => s.show);
  const [working, setWorking] = useState(false);
  const [armed, setArmed] = useState(false);
  const [sid, setSid] = useState<string | null>(null);
  const [state, setState] = useState<RunState | null>(null);
  const [asking, setAsking] = useState(false);   // パスワードのポップアップを出しているか
  const [sending, setSending] = useState(false);
  const outRef = useRef<HTMLPreElement>(null);
  // 送った時点の出力。出力が変わるまで（= 次の要求が来るまで）ポップアップを出し直さない
  const sentAtOutput = useRef<string | null>(null);
  const notes = dangerNotes(request.command);

  const run = async () => {
    if (notes.length && !armed) {
      setArmed(true);   // 危険な内容は 2 段で確かめる（1 回目は用意だけ）
      return;
    }
    if (working) return;
    setWorking(true);
    try {
      const r = await api<{ session_id: string }>("/terminals/run",
        { method: "POST", json: { command: request.command, ...(placeValue ? { cwd: placeValue } : {}) } });
      setSid(r.session_id);
    } catch (e) {
      show(e instanceof Error ? e.message : "実行できませんでした", "error");
    } finally {
      setWorking(false);
    }
  };

  // 1 秒ごとに出力を読む（終わったら止める・シートを閉じたら止める）
  useEffect(() => {
    if (!sid || state?.done) return;
    let stopped = false;
    const tick = async () => {
      try {
        const next = await api<RunState>(`/terminals/run/${sid}`);
        if (stopped) return;
        setState(next);
        if (next.password_prompt && sentAtOutput.current !== next.output) setAsking(true);
        else if (!next.password_prompt) setAsking(false);
      } catch { /* 一時的な失敗は次の周期で読み直す */ }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1000);
    return () => { stopped = true; window.clearInterval(timer); };
  }, [sid, state?.done]);

  // 下へ追う
  useEffect(() => {
    const el = outRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [state?.output]);

  const act = async (path: string, init: RequestInit = { method: "POST" }) => {
    try { await api(`/terminals/run/${sid}${path}`, init); }
    catch (e) { show(e instanceof Error ? e.message : "操作できませんでした", "error"); }
  };

  /** パスワードを送る。値は引数と本文だけに置く（state・ログ・保存に残さない） */
  const sendSecret = async (secret: string) => {
    if (sending || !sid) return;
    setSending(true);
    sentAtOutput.current = state?.output ?? "";
    setAsking(false);
    try {
      await api(`/terminals/run/${sid}/input`, { method: "POST", json: { secret } });
    } catch (e) {
      sentAtOutput.current = null;
      show(e instanceof Error ? e.message : "送れませんでした", "error");
    } finally {
      setSending(false);
    }
  };

  const cancelPrompt = () => {
    sentAtOutput.current = state?.output ?? "";
    setAsking(false);
    void act("/stop");
  };

  const closeSession = async () => {
    if (sid) {
      try { await api(`/terminals/${sid}`, { method: "DELETE" }); }
      catch (e) { show(e instanceof Error ? e.message : "閉じられませんでした", "error"); return; }
    }
    onClose();
  };

  const finished = state?.done ?? false;
  const ok = finished && state?.exit_code === 0;

  return (
    <>
    <BottomSheet title="ターミナルで実行" onClose={() => { if (!asking) onClose(); }} wide>
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
        {!sid && notes.length > 0 && (
          <ul data-testid="run-command-warnings" className="space-y-1 rounded-lg border border-red-200 bg-red-50 p-2.5 text-xs text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300">
            {notes.map((note) => <li key={note}>⚠️ {note}</li>)}
            {!armed && <li className="font-medium">実行するには、もう一度「ターミナルで実行」を押してください。</li>}
          </ul>
        )}
        {sid && (
          <div data-testid="run-frame" className="space-y-2">
            <div data-testid="run-status" className="flex items-center gap-2 text-xs font-medium" role="status">
              {!finished ? (
                <>
                  <span aria-hidden className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" />
                  <span className="text-sky-700 dark:text-sky-300">実行中</span>
                </>
              ) : (
                <span className={ok ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400"}>
                  終了（コード {state?.exit_code}）
                </span>
              )}
            </div>
            <pre ref={outRef} data-testid="run-output" tabIndex={0} aria-label="実行の出力"
              className="h-56 overflow-auto whitespace-pre rounded-lg bg-zinc-900 p-2.5 font-mono text-[12px] leading-snug text-zinc-100 dark:bg-black/40">
              {state?.output || "（出力を待っています）"}
            </pre>
          </div>
        )}
        <div className="sheet-actions sticky bottom-0 -mx-5 flex flex-wrap gap-2 border-t border-zinc-100 bg-white/90 px-5 pb-4 pt-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/90">
          {!sid ? (
            <>
              <button type="button" onClick={onClose} className="min-h-11 rounded-lg border border-zinc-300 px-4 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800">
                キャンセル
              </button>
              <button type="button" data-testid="run-command-confirm" onClick={() => void run()} disabled={working}
                className="min-h-11 flex-1 rounded-lg bg-sky-600 px-4 text-sm font-medium text-white hover:bg-sky-700 disabled:opacity-50">
                {working ? "実行しています…" : armed || notes.length === 0 ? "ターミナルで実行" : "ターミナルで実行（注意を読んで押す）"}
              </button>
            </>
          ) : (
            <>
              {!finished && (
                <button type="button" data-testid="run-stop" onClick={() => void act("/stop")}
                  className="min-h-11 rounded-lg border border-red-300 px-4 text-sm text-red-700 hover:bg-red-50 dark:border-red-800 dark:text-red-300 dark:hover:bg-red-950/30">
                  止める
                </button>
              )}
              <button type="button" data-testid="run-open-terminal" onClick={() => onOpenTerminal(sid)}
                className="min-h-11 flex-1 rounded-lg border border-zinc-300 px-4 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800">
                ターミナルで開く
              </button>
              <button type="button" data-testid="run-close" onClick={() => void closeSession()}
                className="min-h-11 rounded-lg bg-zinc-800 px-4 text-sm font-medium text-white hover:bg-zinc-700 dark:bg-zinc-200 dark:text-zinc-900 dark:hover:bg-white">
                閉じる
              </button>
            </>
          )}
        </div>
      </div>
    </BottomSheet>
    {asking && <PasswordDialog sending={sending} onSend={sendSecret} onCancel={cancelPrompt} />}
    </>
  );
}

/** sudo のパスワードを受けるポップアップ。値は送る関数へ渡した直後に入力欄ごと空にし、state には持たない */
function PasswordDialog({ sending, onSend, onCancel }: {
  sending: boolean;
  onSend: (secret: string) => Promise<void>;
  onCancel: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { inputRef.current?.focus(); }, []);
  const submit = () => {
    const el = inputRef.current;
    if (!el || sending || !el.value) return;
    const secret = el.value;
    el.value = "";   // 送る前に画面から消す
    void onSend(secret);
  };
  return (
    <ConfirmDialog
      title="管理者のパスワード"
      message="sudo がパスワードを求めています。入力はこの実行の端末にだけ送り、保存しません"
      confirmLabel={sending ? "送っています…" : "送る"}
      cancelLabel="やめる"
      danger={false}
      disabled={sending}
      onConfirm={submit}
      onClose={onCancel}
    >
      <input ref={inputRef} type="password" data-testid="run-password" aria-label="管理者のパスワード"
        autoComplete="off" autoCapitalize="off" spellCheck={false}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } }}
        className="mt-3 min-h-11 w-full rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950" />
    </ConfirmDialog>
  );
}
