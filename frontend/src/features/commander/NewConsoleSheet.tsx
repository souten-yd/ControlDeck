/** 「＋ コンソール」のシート: CLI・アカウント・作業フォルダ・個数・表示名を選んで開く。
 *
 * アカウントは既定・作ったもの・その場で新しく作るから選ぶ。
 * 作業フォルダは既存の FilePicker（files API の許可ルート内）を使う。 */
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  accountTitle, createAccount, fetchAccounts, openConsoles,
  type CommanderCli, type OpenConsolesBody,
} from "../../api/commander";
import { FilePicker } from "../../components/FilePicker";
import { BottomSheet, primaryButtonClass } from "../../components/ui";
import { useToasts } from "../../stores";

const COUNT_OPTIONS = [1, 2, 3, 4, 5, 6, 7, 8];

export function NewConsoleSheet({
  clis,
  onClose,
}: {
  clis: CommanderCli[];
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const installed = useMemo(() => clis.filter((cli) => cli.installed), [clis]);
  const [cliId, setCliId] = useState(() => installed[0]?.id ?? clis[0]?.id ?? "");
  const cli = clis.find((c) => c.id === cliId);
  const multi = cli?.multi_account ?? false;
  const [account, setAccount] = useState("default");
  const [newAccountName, setNewAccountName] = useState("");
  const [cwd, setCwd] = useState("");
  const [picking, setPicking] = useState(false);
  const [count, setCount] = useState(1);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);

  const { data: accounts } = useQuery({
    queryKey: ["commander-accounts", cliId],
    queryFn: () => fetchAccounts(cliId),
    enabled: Boolean(cliId),
  });

  const chooseCli = (id: string) => {
    setCliId(id);
    setAccount("default");
    setNewAccountName("");
  };

  const canOpen = Boolean(cli && cli.installed && cwd) && (account !== "__new__" || /^[a-z0-9][a-z0-9_-]{0,31}$/.test(newAccountName));

  const open = async () => {
    if (!cli || !canOpen || busy) return;
    setBusy(true);
    try {
      let accountName = account;
      if (account === "__new__") {
        await createAccount(cliId, newAccountName);
        accountName = newAccountName;
      }
      const body: OpenConsolesBody = { cli: cliId, account: accountName, cwd, count, label: label.trim() };
      const result = await openConsoles(body);
      const openedCount = result.opened.length;
      if (result.not_opened > 0) {
        show(`${openedCount} 個開きました（${result.not_opened} 個は開けません。${result.reason}）`, "error");
      } else {
        show(`${openedCount} 個のコンソールを開きました`);
      }
      qc.invalidateQueries({ queryKey: ["commander-consoles"] });
      qc.invalidateQueries({ queryKey: ["commander-accounts"] });
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "コンソールを開けませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <BottomSheet title="コンソールを開く" onClose={onClose} wide>
      <div className="space-y-5 pt-1">
        <section>
          <h3 className="mb-2 text-xs font-semibold text-zinc-500 dark:text-zinc-400">CLI</h3>
          <ul className="grid gap-1.5" role="radiogroup" aria-label="CLIを選ぶ">
            {clis.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  role="radio"
                  aria-checked={cliId === c.id}
                  disabled={!c.installed}
                  onClick={() => chooseCli(c.id)}
                  className={`flex min-h-11 w-full items-center gap-2 rounded-xl border px-3 py-2 text-left text-sm transition ${
                    !c.installed
                      ? "cursor-default border-zinc-200 bg-zinc-50 text-zinc-400 dark:border-zinc-800 dark:bg-zinc-900/50 dark:text-zinc-500"
                      : cliId === c.id
                        ? "border-accent-500 bg-accent-50 dark:border-accent-500 dark:bg-accent-600/10"
                        : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-700 dark:hover:border-zinc-600"
                  }`}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {c.name}
                    {!c.installed && <span className="ml-2 text-[11px] text-zinc-400 dark:text-zinc-500">未導入（開けません）</span>}
                  </span>
                  {cliId === c.id && c.installed && <span aria-hidden className="text-accent-600 dark:text-accent-400">✓</span>}
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section aria-label="アカウント">
          <h3 className="mb-2 text-xs font-semibold text-zinc-500 dark:text-zinc-400">アカウント</h3>
          {multi || cli?.connector ? (
            <>
            <ul className="grid gap-1.5" role="radiogroup" aria-label="アカウントを選ぶ">
              {(accounts ?? []).map((a) => (
                <li key={a.name}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={account === a.name}
                    onClick={() => setAccount(a.name)}
                    className={`flex min-h-11 w-full items-center gap-2 rounded-xl border px-3 py-2 text-left text-sm ${
                      account === a.name
                        ? "border-accent-500 bg-accent-50 dark:border-accent-500 dark:bg-accent-600/10"
                        : "border-zinc-200 hover:border-zinc-300 dark:border-zinc-700 dark:hover:border-zinc-600"
                    }`}
                  >
                    <span className="min-w-0 flex-1 truncate">
                      {accountTitle(a.name, a.label, true, a.title)}
                      {a.logged_in === true && <span className="ml-2 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">ログイン済み</span>}
                      {a.logged_in === false && <span className="ml-2 text-[10px] text-zinc-400">未ログイン（開いてから CLI の中でログイン）</span>}
                    </span>
                    {account === a.name && <span aria-hidden className="text-accent-600 dark:text-accent-400">✓</span>}
                  </button>
                </li>
              ))}
              {multi && <li>
                <button
                  type="button"
                  role="radio"
                  aria-checked={account === "__new__"}
                  onClick={() => setAccount("__new__")}
                  className={`flex min-h-11 w-full items-center gap-2 rounded-xl border px-3 py-2 text-left text-sm ${
                    account === "__new__"
                      ? "border-accent-500 bg-accent-50 dark:border-accent-500 dark:bg-accent-600/10"
                      : "border-dashed border-zinc-300 hover:border-zinc-400 dark:border-zinc-600 dark:hover:border-zinc-500"
                  }`}
                >
                  ＋ 新しいアカウント
                </button>
              </li>}
              {multi && account === "__new__" && (
                <li className="pl-1">
                  <input
                    value={newAccountName}
                    onChange={(e) => setNewAccountName(e.target.value)}
                    placeholder="アカウント名（英小数字・_・-）"
                    aria-label="新しいアカウント名"
                    className="h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
                  />
                  <p className="mt-1 text-[11px] text-zinc-400">ログインはこのコンソールを開き、CLI の中で行います。</p>
                </li>
              )}
            </ul>
            {cli?.connector === "controldeck-opencode" && (
              <p className="mt-1.5 text-[11px] text-zinc-500 dark:text-zinc-400">
                ControlDeck の OpenCode を、選んだ接続（OpenCode 画面に登録した API）で開きます。接続を増やすのは OpenCode 画面の設定です。
              </p>
            )}
            </>
          ) : (
            <p className="rounded-xl bg-zinc-50 px-3 py-2.5 text-xs text-zinc-500 dark:bg-zinc-900 dark:text-zinc-400">
              {(() => {
                const title = accountTitle("default", accounts?.[0]?.label, true);
                return `${title}: この CLI は既定のアカウントだけ使えます（アカウントを分ける確かな方法がありません）。`;
              })()}
            </p>
          )}
        </section>

        <section aria-label="作業フォルダ">
          <h3 className="mb-2 text-xs font-semibold text-zinc-500 dark:text-zinc-400">作業フォルダ</h3>
          <button
            type="button"
            onClick={() => setPicking(true)}
            className="flex min-h-11 w-full items-center gap-2 rounded-xl border border-zinc-200 px-3 py-2 text-left dark:border-zinc-700"
          >
            <span className="min-w-0 flex-1 truncate font-mono text-xs">{cwd || "フォルダを選ぶ"}</span>
            <span className="shrink-0 text-xs font-semibold text-accent-600 dark:text-accent-400">選ぶ</span>
          </button>
        </section>

        <div className="flex gap-3">
          <section className="w-28" aria-label="個数">
            <h3 className="mb-2 text-xs font-semibold text-zinc-500 dark:text-zinc-400">個数</h3>
            <select
              value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              aria-label="開く個数"
              className="h-11 w-full rounded-xl border border-zinc-300 bg-white px-2 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            >
              {COUNT_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </section>
          <section className="min-w-0 flex-1" aria-label="表示名">
            <h3 className="mb-2 text-xs font-semibold text-zinc-500 dark:text-zinc-400">表示名（任意）</h3>
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              maxLength={64}
              placeholder="例: 調査・リファクタ"
              aria-label="コンソールの表示名"
              className="h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
          </section>
        </div>

        <div className="sheet-actions sticky bottom-0 -mx-5 border-t border-zinc-100 bg-white/90 px-5 pb-4 pt-3 backdrop-blur dark:border-zinc-800 dark:bg-zinc-900/90">
          <button type="button" disabled={!canOpen || busy} onClick={() => void open()} className={`${primaryButtonClass} w-full`}>
            {busy ? "開いています..." : "開く"}
          </button>
        </div>
      </div>
      {picking && (
        <FilePicker
          mode="dir"
          title="作業フォルダを選択"
          initialPath={cwd || undefined}
          onSelect={(path) => {
            setCwd(path);
            setPicking(false);
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </BottomSheet>
  );
}
