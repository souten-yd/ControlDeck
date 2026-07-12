import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { Drawer } from "../../components/ui";
import type { ManagedApp } from "../../types";

type AppType = "python_script" | "shell_script" | "executable" | "systemd_service";

const TYPE_LABELS: Record<AppType, string> = {
  python_script: "Python スクリプト",
  shell_script: "シェルスクリプト",
  executable: "実行ファイル",
  systemd_service: "既存 systemd サービス",
};

interface PythonCandidate {
  path: string;
  version: string | null;
}

export function AddAppSheet({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);

  // Step 1
  const [name, setName] = useState("");
  const [type, setType] = useState<AppType>("python_script");
  const [projectDir, setProjectDir] = useState("");
  // Step 2
  const [pythonPath, setPythonPath] = useState("");
  const [scriptPath, setScriptPath] = useState("");
  const [execPath, setExecPath] = useState("");
  const [unitName, setUnitName] = useState("");
  const [args, setArgs] = useState("");
  const [workDir, setWorkDir] = useState("");
  // Step 3
  const [autoStart, setAutoStart] = useState(false);
  const [restartPolicy, setRestartPolicy] = useState("on-failure");
  const [advanced, setAdvanced] = useState(false);
  const [stopTimeout, setStopTimeout] = useState(20);
  const [envText, setEnvText] = useState("");

  const [pythons, setPythons] = useState<PythonCandidate[]>([]);
  useEffect(() => {
    if (type === "python_script") {
      api<PythonCandidate[]>("/apps/python-interpreters")
        .then(setPythons)
        .catch(() => setPythons([]));
    }
  }, [type]);

  // プロジェクトフォルダから venv / エントリーポイントを提案
  useEffect(() => {
    if (!projectDir || type !== "python_script") return;
    const t = setTimeout(() => {
      api<{ exists: boolean; venvs: PythonCandidate[]; entries: string[] }>(
        `/apps/discover-project?path=${encodeURIComponent(projectDir)}`,
      )
        .then((d) => {
          if (!d.exists) return;
          if (d.venvs.length > 0 && !pythonPath) setPythonPath(d.venvs[0].path);
          if (d.entries.length > 0 && !scriptPath) setScriptPath(d.entries[0]);
          if (!workDir) setWorkDir(projectDir);
        })
        .catch(() => undefined);
    }, 500);
    return () => clearTimeout(t);
  }, [projectDir, type]); // eslint-disable-line react-hooks/exhaustive-deps

  const parseEnv = (): Record<string, string> => {
    const env: Record<string, string> = {};
    for (const line of envText.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq > 0) env[trimmed.slice(0, eq)] = trimmed.slice(eq + 1);
    }
    return env;
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await api<ManagedApp>("/apps", {
        method: "POST",
        json: {
          name,
          application_type: type,
          working_directory: workDir || null,
          python_path: type === "python_script" ? pythonPath : null,
          script_path:
            type === "python_script" || type === "shell_script" ? scriptPath : null,
          executable_path: type === "executable" ? execPath : null,
          systemd_unit_name: type === "systemd_service" ? unitName : null,
          arguments: args.trim() ? args.trim().split(/\s+/) : [],
          environment: parseEnv(),
          auto_start: autoStart,
          restart_policy: restartPolicy,
          stop_timeout_seconds: stopTimeout,
        },
      });
      qc.invalidateQueries({ queryKey: ["apps"] });
      show(`「${name}」を登録しました`);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "登録に失敗しました");
    } finally {
      setBusy(false);
    }
  };

  const step1Valid = name.trim().length > 0;
  const step2Valid =
    type === "python_script"
      ? pythonPath.trim() !== "" && scriptPath.trim() !== ""
      : type === "shell_script"
        ? scriptPath.trim() !== ""
        : type === "executable"
          ? execPath.trim() !== ""
          : unitName.trim() !== "";

  return (
    <Drawer title={`アプリを追加 (${step}/3)`} onClose={onClose}>
      {/* ステップインジケーター */}
      <div className="mb-5 flex gap-1.5" aria-hidden>
        {[1, 2, 3].map((s) => (
          <div
            key={s}
            className={`h-1 flex-1 rounded-full ${
              s <= step ? "bg-accent-500" : "bg-zinc-200 dark:bg-zinc-800"
            }`}
          />
        ))}
      </div>

      {step === 1 && (
        <div className="space-y-4">
          <Field label="アプリ名" required>
            <TextInput value={name} onChange={setName} placeholder="My LLM Server" />
          </Field>
          <Field label="種類">
            <div className="grid grid-cols-1 gap-2">
              {(Object.keys(TYPE_LABELS) as AppType[]).map((t) => (
                <label
                  key={t}
                  className={`flex cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 text-sm ${
                    type === t
                      ? "border-accent-500 bg-accent-50 dark:bg-accent-600/10"
                      : "border-zinc-200 dark:border-zinc-700"
                  }`}
                >
                  <input
                    type="radio"
                    name="app-type"
                    checked={type === t}
                    onChange={() => setType(t)}
                    className="accent-current"
                  />
                  {TYPE_LABELS[t]}
                </label>
              ))}
            </div>
          </Field>
          {type === "python_script" && (
            <Field label="プロジェクトフォルダ" hint="指定すると venv とエントリーポイントを自動提案します">
              <TextInput
                value={projectDir}
                onChange={setProjectDir}
                placeholder="/home/user/projects/my-app"
                mono
              />
            </Field>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="space-y-4">
          {type === "python_script" && (
            <>
              <Field label="Python 実行ファイル" required>
                <TextInput value={pythonPath} onChange={setPythonPath} placeholder="/usr/bin/python3" mono />
                {pythons.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {pythons.slice(0, 4).map((p) => (
                      <button
                        key={p.path}
                        type="button"
                        onClick={() => setPythonPath(p.path)}
                        className="rounded-lg bg-zinc-100 px-2 py-1 text-xs text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400"
                      >
                        {p.version ?? p.path}
                      </button>
                    ))}
                  </div>
                )}
              </Field>
              <Field label="スクリプト" required>
                <TextInput value={scriptPath} onChange={setScriptPath} placeholder="/path/to/main.py" mono />
              </Field>
            </>
          )}
          {type === "shell_script" && (
            <Field label="シェルスクリプト" required>
              <TextInput value={scriptPath} onChange={setScriptPath} placeholder="/path/to/run.sh" mono />
            </Field>
          )}
          {type === "executable" && (
            <Field label="実行ファイル" required>
              <TextInput value={execPath} onChange={setExecPath} placeholder="/usr/local/bin/myapp" mono />
            </Field>
          )}
          {type === "systemd_service" && (
            <Field label="ユニット名" hint="ユーザーユニット (systemctl --user) のみ" required>
              <TextInput value={unitName} onChange={setUnitName} placeholder="my-service.service" mono />
            </Field>
          )}
          {type !== "systemd_service" && (
            <>
              <Field label="起動引数" hint="空白区切り">
                <TextInput value={args} onChange={setArgs} placeholder="--port 8000" mono />
              </Field>
              <Field label="作業ディレクトリ">
                <TextInput value={workDir} onChange={setWorkDir} placeholder="/home/user/projects/my-app" mono />
              </Field>
            </>
          )}
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          {type !== "systemd_service" && (
            <>
              <label className="flex items-center justify-between rounded-xl border border-zinc-200 px-4 py-3 dark:border-zinc-700">
                <span className="text-sm">PC 起動時に自動起動</span>
                <input
                  type="checkbox"
                  checked={autoStart}
                  onChange={(e) => setAutoStart(e.target.checked)}
                  className="h-5 w-5 accent-current"
                />
              </label>
              <Field label="再起動ポリシー">
                <select
                  value={restartPolicy}
                  onChange={(e) => setRestartPolicy(e.target.value)}
                  className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                >
                  <option value="no">再起動しない</option>
                  <option value="on-failure">異常終了時のみ再起動</option>
                  <option value="always">常に再起動</option>
                  <option value="on-success">正常終了時のみ再起動</option>
                </select>
              </Field>
              <button
                type="button"
                onClick={() => setAdvanced((v) => !v)}
                className="text-sm font-medium text-accent-600 dark:text-accent-400"
              >
                {advanced ? "▾ 上級設定を閉じる" : "▸ 上級設定"}
              </button>
              {advanced && (
                <div className="space-y-4 rounded-xl bg-zinc-50 p-4 dark:bg-zinc-900">
                  <Field label="停止タイムアウト（秒）">
                    <TextInput
                      value={String(stopTimeout)}
                      onChange={(v) => setStopTimeout(Number(v) || 20)}
                      mono
                    />
                  </Field>
                  <Field label="環境変数" hint="1 行に KEY=VALUE。秘密値は暗号化保存されます">
                    <textarea
                      value={envText}
                      onChange={(e) => setEnvText(e.target.value)}
                      rows={4}
                      className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-950"
                      placeholder="PORT=8000&#10;API_KEY=..."
                    />
                  </Field>
                </div>
              )}
            </>
          )}
          {/* 確認 */}
          <div className="rounded-xl border border-zinc-200 p-4 text-sm dark:border-zinc-800">
            <p className="mb-2 font-medium">確認</p>
            <dl className="space-y-1 text-xs text-zinc-500">
              <ConfirmRow k="名前" v={name} />
              <ConfirmRow k="種類" v={TYPE_LABELS[type]} />
              {type === "python_script" && <ConfirmRow k="Python" v={pythonPath} />}
              {(type === "python_script" || type === "shell_script") && (
                <ConfirmRow k="スクリプト" v={scriptPath} />
              )}
              {type === "executable" && <ConfirmRow k="実行ファイル" v={execPath} />}
              {type === "systemd_service" && <ConfirmRow k="ユニット" v={unitName} />}
              {args && <ConfirmRow k="引数" v={args} />}
              {workDir && <ConfirmRow k="作業Dir" v={workDir} />}
            </dl>
          </div>
        </div>
      )}

      {error && (
        <p role="alert" className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950/40 dark:text-red-400">
          {error}
        </p>
      )}

      <div className="mt-6 flex justify-between">
        <button
          onClick={() => (step === 1 ? onClose() : setStep(step - 1))}
          className="rounded-xl px-4 py-2.5 text-sm font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
        >
          {step === 1 ? "キャンセル" : "戻る"}
        </button>
        {step < 3 ? (
          <button
            onClick={() => setStep(step + 1)}
            disabled={step === 1 ? !step1Valid : !step2Valid}
            className="rounded-xl bg-accent-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
          >
            次へ
          </button>
        ) : (
          <button
            onClick={submit}
            disabled={busy}
            className="rounded-xl bg-accent-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50"
          >
            {busy ? "登録中..." : "登録する"}
          </button>
        )}
      </div>
    </Drawer>
  );
}

function Field({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <span className="mb-1 block text-xs font-medium text-zinc-500">
        {label}
        {required && <span className="ml-0.5 text-red-500">*</span>}
      </span>
      {children}
      {hint && <p className="mt-1 text-xs text-zinc-400">{hint}</p>}
    </div>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
  mono,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
}) {
  return (
    <input
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className={`w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-sm outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-700 dark:bg-zinc-900 ${
        mono ? "font-mono text-xs" : ""
      }`}
    />
  );
}

function ConfirmRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex gap-3">
      <dt className="w-20 shrink-0">{k}</dt>
      <dd className="min-w-0 break-all font-mono">{v}</dd>
    </div>
  );
}
