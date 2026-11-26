/** アカウントの管理: CLI ごとの一覧・作る・消す・「ログイン済み」の印。
 *
 * ログインは利用者がそのアカウントでコンソールを開き、CLI の中で行う。
 * ControlDeck はログインの情報（鍵・トークン）を読まない・見せない。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createAccount, deleteAccount, fetchAccounts, type CommanderCli } from "../../api/commander";
import { BottomSheet, ConfirmDialog } from "../../components/ui";
import { useToasts } from "../../stores";

function LoginBadge({ logged_in }: { logged_in: boolean | null }) {
  if (logged_in === null) return null;
  return logged_in ? (
    <span className="shrink-0 rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">ログイン済み</span>
  ) : (
    <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">未ログイン</span>
  );
}

function CliAccountsSection({ cli }: { cli: CommanderCli }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [name, setName] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const { data: accounts } = useQuery({
    queryKey: ["commander-accounts", cli.id],
    queryFn: () => fetchAccounts(cli.id),
    enabled: cli.multi_account,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["commander-accounts", cli.id] });
  const create = useMutation({
    mutationFn: () => createAccount(cli.id, name.trim()),
    onSuccess: () => {
      show(`アカウント「${name.trim()}」を作りました`);
      setName("");
      invalidate();
    },
    onError: (e) => show(e instanceof Error ? e.message : "作れませんでした", "error"),
  });
  const remove = useMutation({
    mutationFn: (account: string) => deleteAccount(cli.id, account),
    onSuccess: (_data, account) => {
      show(`アカウント「${account}」を削除しました`);
      invalidate();
    },
    onError: (e) => show(e instanceof Error ? e.message : "削除できませんでした", "error"),
  });

  return (
    <section aria-label={`${cli.name}のアカウント`} className="rounded-2xl border border-zinc-200 p-3 dark:border-zinc-800">
      <h3 className="flex min-w-0 items-center gap-2 text-sm font-semibold">
        <span className="min-w-0 truncate">{cli.name}</span>
        {!cli.installed && <span className="shrink-0 rounded-full bg-zinc-100 px-2 py-0.5 text-[10px] font-medium text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">未導入</span>}
      </h3>
      {!cli.multi_account ? (
        <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
          既定のアカウントだけ使えます（アカウントを分ける確かな方法がありません）。
        </p>
      ) : (
        <>
          <ul className="mt-2 grid gap-1">
            {(accounts ?? []).map((a) => (
              <li key={a.name} className="flex min-h-11 items-center gap-2 rounded-xl bg-zinc-50 px-3 dark:bg-zinc-900">
                <span className="min-w-0 flex-1 truncate text-sm">
                  {a.default ? "既定のアカウント（普段の設定）" : a.name}
                </span>
                <LoginBadge logged_in={a.logged_in} />
                {!a.default && (
                  <button
                    type="button"
                    onClick={() => setDeleting(a.name)}
                    aria-label={`アカウント ${a.name} を削除`}
                    className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-base text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
                  >
                    🗑️
                  </button>
                )}
              </li>
            ))}
          </ul>
          <form
            className="mt-2 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate();
            }}
          >
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="新しいアカウント名（英小数字・_・-）"
              aria-label={`${cli.name}の新しいアカウント名`}
              className="h-11 min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
            />
            <button
              type="submit"
              disabled={!name.trim() || create.isPending}
              className="min-h-11 shrink-0 rounded-xl bg-zinc-100 px-4 text-sm font-semibold text-zinc-700 hover:bg-zinc-200 disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
            >
              追加
            </button>
          </form>
        </>
      )}
      {deleting && (
        <ConfirmDialog
          title={`アカウント「${deleting}」を削除しますか？`}
          message="アカウントのフォルダごと消えます。中のログインの情報も消えるので、使い回す場合は CLI でログインし直してください。"
          confirmLabel="削除する"
          busy={remove.isPending}
          onConfirm={() => {
            const target = deleting;
            setDeleting(null);
            remove.mutate(target);
          }}
          onClose={() => setDeleting(null)}
        />
      )}
    </section>
  );
}

export function AccountsSheet({ clis, onClose }: { clis: CommanderCli[]; onClose: () => void }) {
  return (
    <BottomSheet title="アカウントの管理" onClose={onClose} wide>
      <p className="rounded-xl bg-zinc-50 px-3 py-2.5 text-xs leading-relaxed text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
        ログインは、そのアカウントでコンソールを開き、CLI の中で行ってください。
        ControlDeck はログインの情報（鍵・トークン）を読みません・画面にも出しません。
      </p>
      <div className="mt-3 grid gap-3">
        {clis.map((cli) => <CliAccountsSection key={cli.id} cli={cli} />)}
      </div>
    </BottomSheet>
  );
}
