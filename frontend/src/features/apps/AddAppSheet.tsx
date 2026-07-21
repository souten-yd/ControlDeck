import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { Drawer } from "../../components/ui";
import { FilePicker } from "../../components/FilePicker";
import { IconFolder } from "../../components/icons";
import { CodeEditor } from "./CodeEditor";
import type { ManagedApp } from "../../types";

type AppType = "python_script" | "shell_script" | "executable" | "systemd_service" | "url_shortcut";

const TYPE_LABELS: Record<AppType, string> = {
  python_script: "Python スクリプト",
  shell_script: "シェルスクリプト",
  executable: "実行ファイル",
  systemd_service: "既存 systemd サービス",
  url_shortcut: "Web ページ / URL",
};

interface PythonCandidate {
  path: string;
  version: string | null;
}

interface HealthCommandOption {
  id: string;
  label: string;
}

export function AddAppSheet({ onClose, editApp }: { onClose: () => void; editApp?: ManagedApp }) {
  const editing = !!editApp;
  const [step, setStep] = useState(editing ? 2 : 1); // 編集は手順1（種類選択）をスキップ
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);

  // Step 1
  const [name, setName] = useState(editApp?.name ?? "");
  const [icon, setIcon] = useState<File | null>(null);
  const [type, setType] = useState<AppType>((editApp?.application_type as AppType) ?? "python_script");
  const [projectDir, setProjectDir] = useState("");
  // Step 2
  const [pythonPath, setPythonPath] = useState(editApp?.python_path ?? "");
  const [scriptPath, setScriptPath] = useState(editApp?.script_path ?? "");
  const [execPath, setExecPath] = useState(editApp?.executable_path ?? "");
  const [unitName, setUnitName] = useState(editApp?.systemd_unit_name ?? "");
  const [args, setArgs] = useState((editApp?.arguments ?? []).join(" "));
  const [workDir, setWorkDir] = useState(editApp?.working_directory ?? "");
  const [webPort, setWebPort] = useState(editApp?.web_port != null ? String(editApp.web_port) : "");
  const detectedPorts = editApp?.runtime?.listening_ports ?? [];
  // Step 3
  const [autoStart, setAutoStart] = useState(editApp?.auto_start ?? false);
  const [restartPolicy, setRestartPolicy] = useState(editApp?.restart_policy ?? "on-failure");
  const [advanced, setAdvanced] = useState(false);
  const [stopTimeout, setStopTimeout] = useState(editApp?.stop_timeout_seconds ?? 20);
  const [envText, setEnvText] = useState("");
  const [url, setUrl] = useState(editApp?.url ?? "");
  const [healthType, setHealthType] = useState(editApp?.health_check.type ?? "none");
  const [healthHost, setHealthHost] = useState(editApp?.health_check.host ?? "127.0.0.1");
  const [healthPort, setHealthPort] = useState(editApp?.health_check.port != null ? String(editApp.health_check.port) : "");
  const [healthUrl, setHealthUrl] = useState(editApp?.health_check.url ?? "");
  const [healthStatus, setHealthStatus] = useState(String(editApp?.health_check.expected_status ?? 200));
  const [healthBody, setHealthBody] = useState(editApp?.health_check.body_contains ?? "");
  const [healthPath, setHealthPath] = useState(editApp?.health_check.path ?? "");
  const [healthCommandId, setHealthCommandId] = useState(editApp?.health_check.command_id ?? "");
  const [healthCommands, setHealthCommands] = useState<HealthCommandOption[]>([]);
  // インラインコード編集
  const [codeMode, setCodeMode] = useState(false);
  const [code, setCode] = useState("");

  // 編集時: 管理コードなら読み込んでコードモードに
  useEffect(() => {
    if (editApp && (editApp.application_type === "python_script" || editApp.application_type === "shell_script")) {
      api<{ code: string | null; managed: boolean }>(`/apps/${editApp.id}/code`)
        .then((r) => {
          if (r.managed && r.code != null) {
            setCode(r.code);
            setCodeMode(true);
          }
        })
        .catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [pythons, setPythons] = useState<PythonCandidate[]>([]);
  useEffect(() => {
    if (type === "python_script") {
      api<PythonCandidate[]>("/apps/python-interpreters")
        .then(setPythons)
        .catch(() => setPythons([]));
    }
  }, [type]);

  useEffect(() => {
    api<HealthCommandOption[]>("/apps/health-commands")
      .then(setHealthCommands)
      .catch(() => setHealthCommands([]));
  }, []);

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
    const usingCode = codeMode && (type === "python_script" || type === "shell_script");
    const payload: Record<string, unknown> = {
      name,
      web_port: webPort ? Number(webPort) : null,
      working_directory: workDir || null,
      python_path: type === "python_script" ? pythonPath : null,
      script_path: type === "python_script" || type === "shell_script" ? (usingCode ? null : scriptPath) : null,
      executable_path: type === "executable" ? execPath : null,
      url: type === "url_shortcut" ? url : null,
      arguments: args.trim() ? args.trim().split(/\s+/) : [],
      auto_start: autoStart,
      restart_policy: restartPolicy,
      stop_timeout_seconds: stopTimeout,
      health_check: {
        type: healthType,
        host: healthHost,
        port: healthPort ? Number(healthPort) : null,
        url: healthUrl,
        expected_status: Number(healthStatus) || 200,
        body_contains: healthBody,
        path: healthPath,
        command_id: healthCommandId,
        timeout_seconds: 3,
      },
    };
    if (usingCode) payload.code = code;
    // 環境変数は入力がある場合のみ更新（編集時に空で消さない）
    const env = parseEnv();
    if (!editing || Object.keys(env).length > 0) payload.environment = env;
    try {
      let saved: ManagedApp;
      if (editing) {
        saved = await api<ManagedApp>(`/apps/${editApp!.id}`, { method: "PATCH", json: payload });
      } else {
        saved = await api<ManagedApp>("/apps", {
          method: "POST",
          json: { ...payload, application_type: type, systemd_unit_name: type === "systemd_service" ? unitName : null, environment: env },
        });
      }
      if (icon) {
        const form = new FormData();
        form.append("file", icon);
        await api(`/apps/${saved.id}/icon`, { method: "POST", body: form });
      }
      qc.invalidateQueries({ queryKey: ["apps"] });
      show(editing ? `「${name}」を更新しました` : `「${name}」を登録しました`);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : editing ? "更新に失敗しました" : "登録に失敗しました");
    } finally {
      setBusy(false);
    }
  };

  const step1Valid = name.trim().length > 0;
  const healthValid = healthType !== "command" || healthCommandId.trim() !== "";
  const step2Valid =
    type === "python_script"
      ? pythonPath.trim() !== "" && (codeMode ? code.trim() !== "" : scriptPath.trim() !== "")
      : type === "shell_script"
        ? (codeMode ? code.trim() !== "" : scriptPath.trim() !== "")
        : type === "executable"
          ? execPath.trim() !== ""
          : type === "url_shortcut"
            ? /^https?:\/\//.test(url.trim())
            : unitName.trim() !== "" && healthValid;

  return (
    <Drawer title={editing ? `「${editApp!.name}」を編集` : `アプリを追加 (${step}/3)`} onClose={onClose}>
      {/* ステップインジケーター（新規登録時のみ） */}
      {!editing && (
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
      )}

      {step === 1 && (
        <div className="space-y-4">
          <Field label="アプリ名" required>
            <TextInput value={name} onChange={setName} placeholder="My LLM Server" />
          </Field>
          <Field label="アイコン" hint="PNG / JPEG / WebP / SVG、2MB以下">
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml"
              onChange={(e) => setIcon(e.target.files?.[0] ?? null)}
              className="block w-full text-xs text-zinc-500 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-2 file:text-xs file:font-medium dark:file:bg-zinc-800" />
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
              <PathInput
                value={projectDir}
                onChange={setProjectDir}
                placeholder="/home/user/projects/my-app"
                mode="dir"
                title="プロジェクトフォルダを選択"
              />
            </Field>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="space-y-4">
          {editing && (
            <>
              <Field label="アプリ名" required>
                <TextInput value={name} onChange={setName} />
              </Field>
              <Field label="アイコン" hint="選択すると現在のアイコンを置き換えます">
                <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml"
                  onChange={(e) => setIcon(e.target.files?.[0] ?? null)}
                  className="block w-full text-xs text-zinc-500 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-100 file:px-3 file:py-2 file:text-xs file:font-medium dark:file:bg-zinc-800" />
              </Field>
            </>
          )}
          {/* Python / Shell: ファイル指定 or コード直接入力の切替 */}
          {(type === "python_script" || type === "shell_script") && (
            <div className="flex gap-1 rounded-xl bg-zinc-100 p-1 dark:bg-zinc-800">
              <button
                type="button"
                onClick={() => setCodeMode(false)}
                className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${!codeMode ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}
              >
                ファイルを指定
              </button>
              <button
                type="button"
                onClick={() => setCodeMode(true)}
                className={`flex-1 rounded-lg py-1.5 text-xs font-medium ${codeMode ? "bg-white shadow-sm dark:bg-zinc-900" : "text-zinc-500"}`}
              >
                コードを書く
              </button>
            </div>
          )}
          {type === "python_script" && (
            <>
              <Field label="Python 実行ファイル" required>
                <PathInput value={pythonPath} onChange={setPythonPath} placeholder="/usr/bin/python3" mode="file" title="Python 実行ファイルを選択" />
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
              {codeMode ? (
                <Field label="コード" hint="動作確認ボタンで一時実行できます">
                  <CodeEditor appType="python_script" pythonPath={pythonPath} workDir={workDir} code={code} onChange={setCode} />
                </Field>
              ) : (
                <Field label="スクリプト" required>
                  <PathInput value={scriptPath} onChange={setScriptPath} placeholder="/path/to/main.py" mode="file" title="スクリプトを選択" />
                </Field>
              )}
            </>
          )}
          {type === "shell_script" &&
            (codeMode ? (
              <Field label="コード" hint="動作確認ボタンで一時実行できます">
                <CodeEditor appType="shell_script" pythonPath="" workDir={workDir} code={code} onChange={setCode} />
              </Field>
            ) : (
              <Field label="シェルスクリプト" required>
                <PathInput value={scriptPath} onChange={setScriptPath} placeholder="/path/to/run.sh" mode="file" title="シェルスクリプトを選択" />
              </Field>
            ))}
          {type === "executable" && (
            <Field label="実行ファイル" required>
              <PathInput value={execPath} onChange={setExecPath} placeholder="/usr/local/bin/myapp" mode="file" title="実行ファイルを選択" />
            </Field>
          )}
          {type === "systemd_service" && (
            <Field label="ユニット名" hint="ユーザーユニット (systemctl --user) のみ" required>
              <TextInput value={unitName} onChange={setUnitName} placeholder="my-service.service" mono />
            </Field>
          )}
          {type === "url_shortcut" && (
            <Field label="URL" hint="ダッシュボードから開けるリンクとして登録します" required>
              <TextInput value={url} onChange={setUrl} placeholder="https://example.com" mono />
            </Field>
          )}
          {type !== "systemd_service" && type !== "url_shortcut" && (
            <>
              <Field label="起動引数" hint="空白区切り">
                <TextInput value={args} onChange={setArgs} placeholder="--port 8000" mono />
              </Field>
              <Field label="作業ディレクトリ" hint="未指定時はホームディレクトリで実行します">
                <PathInput value={workDir} onChange={setWorkDir} placeholder="/home/user/projects/my-app" mode="dir" title="作業ディレクトリを選択" />
              </Field>
              <Field label="Web ポート" hint="サーバーアプリを Web ボタンで開くときのポート（空欄なら検出ポートから自動）">
                <TextInput value={webPort} onChange={(v) => setWebPort(v.replace(/[^0-9]/g, ""))} placeholder="8000" mono />
                {detectedPorts.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {detectedPorts.map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => setWebPort(String(p))}
                        className={`rounded-lg px-2 py-1 text-xs ${
                          webPort === String(p)
                            ? "bg-accent-600 text-white"
                            : "bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-400"
                        }`}
                      >
                        検出: {p}
                      </button>
                    ))}
                  </div>
                )}
              </Field>
            </>
          )}
          {type === "systemd_service" && (
            <>
              <Field label="ヘルスチェック" hint="失敗すると実行中の状態をDEGRADEDとして表示します">
                <select aria-label="ヘルスチェック種別" value={healthType} onChange={(e) => setHealthType(e.target.value as ManagedApp["health_check"]["type"])}
                  className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="none">なし</option><option value="process">プロセス存在</option>
                  <option value="tcp">TCPポート</option><option value="http">HTTP GET</option><option value="file">ファイル存在</option>
                  {(healthCommands.length > 0 || healthType === "command") && <option value="command">許可コマンド</option>}
                </select>
              </Field>
              {healthType === "tcp" && <div className="grid grid-cols-[1fr_7rem] gap-2">
                <Field label="ホスト"><TextInput value={healthHost} onChange={setHealthHost} mono /></Field>
                <Field label="ポート"><TextInput value={healthPort} onChange={(v) => setHealthPort(v.replace(/[^0-9]/g, ""))} mono /></Field>
              </div>}
              {healthType === "http" && <div className="space-y-3">
                <Field label="確認URL"><TextInput value={healthUrl} onChange={setHealthUrl} placeholder="http://127.0.0.1:8000/health" mono /></Field>
                <div className="grid grid-cols-[7rem_1fr] gap-2">
                  <Field label="期待status"><TextInput value={healthStatus} onChange={(v) => setHealthStatus(v.replace(/[^0-9]/g, ""))} mono /></Field>
                  <Field label="本文に含む文字（任意）"><TextInput value={healthBody} onChange={setHealthBody} mono /></Field>
                </div>
              </div>}
              {healthType === "file" && <Field label="確認ファイル" hint="設定の許可ルート内だけを確認できます">
                <PathInput value={healthPath} onChange={setHealthPath} placeholder="/path/to/ready" mode="file" title="確認ファイルを選択" />
              </Field>}
              {healthType === "command" && <Field label="許可コマンド" hint="サーバー設定で固定されたargvだけをsystemd user unitで実行します">
                <select aria-label="許可コマンド" value={healthCommandId} onChange={(event) => setHealthCommandId(event.target.value)} className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="">選択してください</option>
                  {healthCommands.map((command) => <option key={command.id} value={command.id}>{command.label}</option>)}
                  {healthCommandId && !healthCommands.some((command) => command.id === healthCommandId) && <option value={healthCommandId}>現在の設定（利用不可）</option>}
                </select>
              </Field>}
            </>
          )}
        </div>
      )}

      {step === 3 && (
        <div className="space-y-4">
          {type !== "systemd_service" && type !== "url_shortcut" && (
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
              <Field label="ヘルスチェック" hint="失敗すると実行中の状態をDEGRADEDとして表示します">
                <select aria-label="ヘルスチェック種別" value={healthType} onChange={(e) => setHealthType(e.target.value as ManagedApp["health_check"]["type"])}
                  className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                  <option value="none">なし</option>
                  <option value="process">プロセス存在</option>
                  <option value="tcp">TCPポート</option>
                  <option value="http">HTTP GET</option>
                  <option value="file">ファイル存在</option>
                  {(healthCommands.length > 0 || healthType === "command") && <option value="command">許可コマンド</option>}
                </select>
              </Field>
              {healthType === "tcp" && (
                <div className="grid grid-cols-[1fr_7rem] gap-2">
                  <Field label="ホスト"><TextInput value={healthHost} onChange={setHealthHost} mono /></Field>
                  <Field label="ポート"><TextInput value={healthPort} onChange={(v) => setHealthPort(v.replace(/[^0-9]/g, ""))} placeholder={webPort || "8000"} mono /></Field>
                </div>
              )}
              {healthType === "http" && (
                <div className="space-y-3">
                  <Field label="確認URL"><TextInput value={healthUrl} onChange={setHealthUrl} placeholder="http://127.0.0.1:8000/health" mono /></Field>
                  <div className="grid grid-cols-[7rem_1fr] gap-2">
                    <Field label="期待status"><TextInput value={healthStatus} onChange={(v) => setHealthStatus(v.replace(/[^0-9]/g, ""))} mono /></Field>
                    <Field label="本文に含む文字（任意）"><TextInput value={healthBody} onChange={setHealthBody} mono /></Field>
                  </div>
                </div>
              )}
              {healthType === "file" && (
                <Field label="確認ファイル" hint="設定の許可ルート内だけを確認できます">
                  <PathInput value={healthPath} onChange={setHealthPath} placeholder="/path/to/ready" mode="file" title="確認ファイルを選択" />
                </Field>
              )}
              {healthType === "command" && (
                <Field label="許可コマンド" hint="サーバー設定で固定されたargvだけをsystemd user unitで実行します">
                  <select aria-label="許可コマンド" value={healthCommandId} onChange={(event) => setHealthCommandId(event.target.value)} className="w-full rounded-xl border border-zinc-300 bg-white px-3 py-2.5 text-sm dark:border-zinc-700 dark:bg-zinc-900">
                    <option value="">選択してください</option>
                    {healthCommands.map((command) => <option key={command.id} value={command.id}>{command.label}</option>)}
                    {healthCommandId && !healthCommands.some((command) => command.id === healthCommandId) && <option value={healthCommandId}>現在の設定（利用不可）</option>}
                  </select>
                </Field>
              )}
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
              {type === "url_shortcut" && <ConfirmRow k="URL" v={url} />}
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
          onClick={() => ((step === 1 || (editing && step === 2)) ? onClose() : setStep(step - 1))}
          className="rounded-xl px-4 py-2.5 text-sm font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
        >
          {step === 1 || (editing && step === 2) ? "キャンセル" : "戻る"}
        </button>
        {(type === "url_shortcut" ? step < 2 : step < 3) ? (
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
            disabled={busy || !healthValid}
            className="rounded-xl bg-accent-600 px-5 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50"
          >
            {busy ? (editing ? "更新中..." : "登録中...") : editing ? "更新する" : "登録する"}
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

/** パス入力 + 参照ボタン（サーバー上のファイル/フォルダ選択ダイアログ）。 */
function PathInput({
  value,
  onChange,
  placeholder,
  mode,
  title,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mode: "file" | "dir";
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  // ファイル選択は現在値の親フォルダから開く
  const initial = value.includes("/")
    ? mode === "file"
      ? value.slice(0, value.lastIndexOf("/")) || undefined
      : value
    : undefined;
  return (
    <div className="flex gap-1.5">
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 font-mono text-xs outline-none focus:border-accent-500 focus:ring-2 focus:ring-accent-500/30 dark:border-zinc-700 dark:bg-zinc-900"
      />
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="参照"
        title="サーバー上から選択"
        className="shrink-0 rounded-xl border border-zinc-300 px-3 text-zinc-500 hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
      >
        <IconFolder />
      </button>
      {open && (
        <FilePicker
          mode={mode}
          title={title}
          initialPath={initial}
          onSelect={(p) => {
            onChange(p);
            setOpen(false);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
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
