import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { BottomSheet } from "../../components/ui";
import { useToasts } from "../../stores";
import type { TriggerInputDef } from "./nodeTypes";
import { initialRuntimeValues, RuntimeField } from "./RuntimeComponents";

interface TestCaseCheck {
  path: string;
  operator: string;
  expected: unknown;
  actual: unknown;
  passed: boolean;
  error?: string;
}

interface TestCase {
  id: number;
  name: string;
  inputs: Record<string, unknown>;
  last_status: string;
  last_execution_id: number | null;
  last_result: {
    passed?: boolean;
    execution_status?: string;
    checks?: TestCaseCheck[];
    error?: string;
    summary?: { passed: number; total: number };
  };
}

function preview(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text === undefined ? "—" : text.length > 80 ? `${text.slice(0, 80)}…` : text;
}

/** 失敗したときに「何が起きたか」をその場で読めるようにする。 */
function FailureDetail({ testCase }: { testCase: TestCase }) {
  const result = testCase.last_result ?? {};
  const failed = (result.checks ?? []).filter((check) => !check.passed);
  const status = result.execution_status ?? "";
  return (
    <div className="mt-2 space-y-2 rounded-xl bg-red-50 p-2.5 text-[11px] leading-relaxed text-red-700 dark:bg-red-950/30 dark:text-red-300">
      {result.error && <p>{result.error}</p>}
      {status && status !== "SUCCEEDED" && <p>実行が {status} で終わりました。</p>}
      {failed.length > 0 && (
        <ul className="space-y-1.5">
          {failed.slice(0, 5).map((check, index) => (
            <li key={`${check.path}-${index}`}>
              <code className="font-mono">{check.path}</code>
              <span className="block text-red-600/80 dark:text-red-300/80">
                期待 {check.operator} {preview(check.expected)} / 実際 {preview(check.actual)}
              </span>
            </li>
          ))}
        </ul>
      )}
      {failed.length === 0 && status === "SUCCEEDED" && !result.error && (
        <p>期待値との違いは記録されていません。実行履歴で詳細を確認してください。</p>
      )}
    </div>
  );
}

/** 回帰テストは普段は見せず、必要なときだけこのシートで扱う。 */
export function TestCasesSheet({
  workflowId, inputs, onClose,
}: {
  workflowId: number;
  inputs: TriggerInputDef[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const show = useToasts((state) => state.show);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, unknown>>(() => initialRuntimeValues(inputs));

  const cases = useQuery({
    queryKey: ["workflow-test-cases", workflowId],
    queryFn: () => api<TestCase[]>(`/workflows/${workflowId}/test-cases`),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((item) => item.last_status === "RUNNING") ? 1500 : false,
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["workflow-test-cases", workflowId] });

  const act = async (run: () => Promise<unknown>, message: string) => {
    try {
      await run();
      await refresh();
      show(message);
    } catch (error) {
      show(error instanceof Error ? error.message : "失敗しました", "error");
    }
  };

  const rows = cases.data ?? [];
  return (
    <BottomSheet title="テストケース" onClose={onClose} stable>
      <p className="mb-3 text-[11px] leading-relaxed text-zinc-500">
        よく使う入力を保存しておき、変更後にまとめて実行して結果が変わっていないかを確認します。
      </p>

      {rows.length > 0 && (
        <button
          type="button"
          onClick={() => void act(() => api(`/workflows/${workflowId}/test-cases/run-batch`, { method: "POST" }), "まとめて実行しました")}
          disabled={rows.some((item) => item.last_status === "RUNNING")}
          className="mb-3 min-h-11 w-full rounded-xl bg-zinc-900 text-xs font-semibold text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {rows.length} 件をまとめて実行
        </button>
      )}

      <div className="space-y-2">
        {rows.map((testCase) => (
          <div key={testCase.id} className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-800">
            <div className="flex items-center gap-2">
              <strong className="min-w-0 flex-1 truncate text-sm">{testCase.name}</strong>
              <span className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                testCase.last_status === "PASSED"
                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300"
                  : testCase.last_status === "RUNNING"
                    ? "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300"
                    : testCase.last_status
                      ? "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-300"
                      : "bg-zinc-100 text-zinc-500 dark:bg-zinc-800"
              }`}>
                {testCase.last_status === "PASSED" ? "一致" : testCase.last_status === "RUNNING" ? "実行中" : testCase.last_status === "NEVER" || !testCase.last_status ? "未実行" : "不一致"}
              </span>
            </div>
            {testCase.last_status === "FAILED" || testCase.last_status === "ERROR" ? <FailureDetail testCase={testCase} /> : null}
            {testCase.last_status === "PASSED" && (testCase.last_result?.summary?.total ?? 0) > 0 && (
              <p className="mt-1.5 text-[11px] text-zinc-500">
                チェック {testCase.last_result?.summary?.passed}/{testCase.last_result?.summary?.total} が一致しました。
              </p>
            )}
            <div className="mt-2 flex gap-1.5">
              <button
                type="button"
                onClick={() => void act(() => api(`/workflows/${workflowId}/test-cases/${testCase.id}/run`, { method: "POST" }), "実行しました")}
                disabled={testCase.last_status === "RUNNING"}
                className="min-h-10 flex-1 rounded-xl border border-accent-300 text-xs font-medium text-accent-700 disabled:opacity-50 dark:border-accent-700 dark:text-accent-300"
              >
                実行
              </button>
              <button
                type="button"
                onClick={() => void act(() => api(`/workflows/${workflowId}/test-cases/${testCase.id}`, { method: "DELETE" }), "削除しました")}
                className="min-h-10 rounded-xl px-3 text-xs text-red-600 dark:text-red-400"
              >
                削除
              </button>
            </div>
          </div>
        ))}
        {rows.length === 0 && !creating && (
          <p className="py-4 text-center text-xs text-zinc-500">まだテストケースはありません。</p>
        )}
      </div>

      {creating ? (
        <div className="mt-3 space-y-3 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-900">
          <label className="block">
            <span className="mb-1 block text-xs font-medium">名前</span>
            <input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              className="min-h-11 w-full rounded-xl border border-zinc-300 bg-transparent px-3 text-sm dark:border-zinc-700"
            />
          </label>
          {inputs.filter((input) => input.key).map((input) => (
            <RuntimeField
              key={input.key}
              input={input}
              value={values[input.key]}
              idPrefix="test-case-input"
              onChange={(next) => setValues((current) => ({ ...current, [input.key]: next }))}
            />
          ))}
          <div className="grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setCreating(false)} className="min-h-11 rounded-xl border border-zinc-300 text-xs dark:border-zinc-700">取消</button>
            <button
              type="button"
              disabled={!name.trim()}
              onClick={() => void act(
                async () => {
                  await api(`/workflows/${workflowId}/test-cases`, {
                    method: "POST", json: { name: name.trim(), inputs: values, expected_outputs: {} },
                  });
                  setCreating(false);
                  setName("");
                },
                "テストケースを追加しました",
              )}
              className="min-h-11 rounded-xl bg-accent-600 text-xs font-semibold text-white disabled:opacity-40"
            >
              追加
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="mt-3 min-h-11 w-full rounded-xl border border-dashed border-zinc-300 text-xs font-medium text-zinc-500 dark:border-zinc-700"
        >
          テストケースを追加
        </button>
      )}
    </BottomSheet>
  );
}
