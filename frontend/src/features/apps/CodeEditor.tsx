/** アプリ用インラインコードエディタ（Monaco 遅延ロード）+ 動作確認（テスト実行）。 */
import { lazy, Suspense, useState } from "react";
import { api } from "../../api/client";

const MonacoInline = lazy(() => import("./MonacoInline"));

interface TestResult {
  exit_code: number;
  stdout: string;
  stderr: string;
  ok: boolean;
}

export function CodeEditor({
  appType,
  pythonPath,
  code,
  onChange,
}: {
  appType: "python_script" | "shell_script";
  pythonPath: string;
  code: string;
  onChange: (v: string) => void;
}) {
  const [result, setResult] = useState<TestResult | null>(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const language = appType === "python_script" ? "python" : "shell";

  const testRun = async () => {
    setTesting(true);
    setError(null);
    setResult(null);
    try {
      const r = await api<TestResult>("/apps/test-run", {
        method: "POST",
        json: { application_type: appType, python_path: pythonPath || null, code },
      });
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "実行に失敗しました");
    } finally {
      setTesting(false);
    }
  };

  return (
    <div>
      <div className="overflow-hidden rounded-xl border border-zinc-300 dark:border-zinc-700">
        <Suspense fallback={<div className="grid h-48 place-items-center text-xs text-zinc-400">エディタを読み込み中...</div>}>
          <MonacoInline value={code} language={language} onChange={onChange} />
        </Suspense>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={testRun}
          disabled={testing || !code.trim()}
          className="rounded-lg bg-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-300"
        >
          {testing ? "実行中..." : "▶ 動作確認"}
        </button>
        <span className="text-xs text-zinc-400">一時実行して出力を確認します（30 秒まで）</span>
      </div>
      {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
      {result && (
        <div className="mt-2 rounded-lg border border-zinc-200 dark:border-zinc-800">
          <div className={`px-3 py-1.5 text-xs font-medium ${result.ok ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
            終了コード {result.exit_code}
          </div>
          {result.stdout && (
            <pre className="max-h-40 overflow-auto border-t border-zinc-200 bg-zinc-950 p-2 font-mono text-[11px] text-zinc-200 dark:border-zinc-800">{result.stdout}</pre>
          )}
          {result.stderr && (
            <pre className="max-h-40 overflow-auto border-t border-zinc-200 bg-zinc-950 p-2 font-mono text-[11px] text-red-400 dark:border-zinc-800">{result.stderr}</pre>
          )}
          {!result.stdout && !result.stderr && (
            <p className="border-t border-zinc-200 px-3 py-2 text-xs text-zinc-400 dark:border-zinc-800">（出力なし）</p>
          )}
        </div>
      )}
    </div>
  );
}
