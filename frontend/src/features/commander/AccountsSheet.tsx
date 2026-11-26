/** アカウントの管理: CLI ごとの一覧・作る・消す・名前を付ける・「ログイン済み」の印。
 *
 * 名前（画面の名前）は既定のアカウントにも付けられる。フォルダの名前とは別。
 *
 * ログインは利用者がそのアカウントでコンソールを開き、CLI の中で行う。
 * ControlDeck はログインの情報（鍵・トークン）を読まない・見せない。 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  accountTitle, createAccount, deleteAccount, fetchAccounts, setAccountLabel,
  type CommanderAccount, type CommanderCli,
} from "../../api/commander";
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

/** 1 行: 名前（押すとその場で編集）・ログインの印・削除。 */
function AccountRow({ cli, account, onDelete }: { cli: CommanderCli; account: CommanderAccount; onDelete: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(account.label);
  const save = useMutation({
    mutationFn: () => setAccountLabel(cli.id, account.name, draft.trim()),
    onSuccess: () => {
      setEditing(false);
      qc.invalidateQueries({ queryKey: ["commander-accounts", cli.id] });
      qc.invalidateQueries({ queryKey: ["commander-consoles"] });
      show(draft.trim() ? `名前を「${draft.trim()}」にしました` : "名前を外しました");
    },
    onError: (e) => show(e instanceof Error ? e.message : "名前を保存できませんでした", "error"),
  });
  const folder = account.default ? "既定" : account.name;
  if (editing) {
    return (
      <li className="rounded-xl bg-zinc-50 px-3 py-2 dark:bg-zinc-900">
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <input
            autoFocus
            value={draft}
            maxLength={32}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={account.default ? "例: 仕事用・個人" : account.name}
            aria-label={`${folder} のアカウントの名前`}
            className="h-11 min-w-0 flex-1 rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-950"
          />
          <button type="submit" disabled={save.isPending} className="min-h-11 shrink-0 rounded-xl bg-accent-600 px-4 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-40">保存</button>
          <button type="button" onClick={() => { setDraft(account.label); setEditing(false); }} className="min-h-11 shrink-0 rounded-xl px-3 text-sm text-zinc-500">やめる</button>
        </form>
        <p className="mt-1 text-[11px] text-zinc-400">空にすると名前を外します（{account.default ? "「既定」と出ます" : `フォルダの名前「${account.name}」が出ます`}）。</p>
      </li>
    );
  }
  return (
    <li className="flex min-h-11 items-center gap-2 rounded-xl bg-zinc-50 px-3 dark:bg-zinc-900">
      <span className="min-w-0 flex-1 truncate text-sm">
        {accountTitle(account.name, account.label, true, account.title)}
        {account.label && <span className="ml-1.5 text-[11px] text-zinc-400">（{account.title || folder}）</span>}
      </span>
      <LoginBadge logged_in={account.logged_in} />
      <button
        type="button"
        onClick={() => { setDraft(account.label); setEditing(true); }}
        aria-label={`${folder} のアカウントに名前を付ける`}
        title="名前を付ける"
        className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-base hover:bg-zinc-100 dark:hover:bg-zinc-800"
      >
        ✏️
      </button>
      {!account.default && (
        <button
          type="button"
          onClick={onDelete}
          aria-label={`アカウント ${account.name} を削除`}
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full text-base text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40"
        >
          🗑️
        </button>
      )}
    </li>
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
      <ul className="mt-2 grid gap-1">
        {(accounts ?? []).map((a) => (
          <AccountRow key={a.name} cli={cli} account={a} onDelete={() => setDeleting(a.name)} />
        ))}
      </ul>
      {cli.connector === "controldeck-opencode" ? (
        <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
          OpenCode 画面に登録した接続（ゲートウェイ・OpenRouter など）がアカウントとして並びます。接続を増やす・外すのは OpenCode 画面の設定です。
        </p>
      ) : !cli.multi_account ? (
        <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
          既定のアカウントだけ使えます（アカウントを分ける確かな方法がありません）。
        </p>
      ) : (
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
  // まず対象のコンソール（CLI）を選び、その CLI に登録済みのアカウントだけを出す。
  const [cliId, setCliId] = useState(() => (clis.find((c) => c.installed) ?? clis[0])?.id ?? "");
  const cli = clis.find((c) => c.id === cliId);
  return (
    <BottomSheet title="アカウントの管理" onClose={onClose} wide>
      <div role="radiogroup" aria-label="対象のコンソール" className="flex flex-wrap gap-1.5">
        {clis.map((c) => (
          <button
            key={c.id}
            type="button"
            role="radio"
            aria-checked={cliId === c.id}
            onClick={() => setCliId(c.id)}
            className={`min-h-11 rounded-full border px-4 text-sm font-medium transition ${
              cliId === c.id
                ? "border-accent-500 bg-accent-50 text-accent-700 dark:border-accent-500 dark:bg-accent-600/15 dark:text-accent-300"
                : "border-zinc-200 text-zinc-600 hover:border-zinc-300 dark:border-zinc-700 dark:text-zinc-300"
            }`}
          >
            {c.name}
          </button>
        ))}
      </div>
      <div className="mt-3">
        {cli && <CliAccountsSection key={cli.id} cli={cli} />}
      </div>
      <p className="mt-3 rounded-xl bg-zinc-50 px-3 py-2.5 text-xs leading-relaxed text-zinc-600 dark:bg-zinc-900 dark:text-zinc-300">
        ログインは、そのアカウントでコンソールを開き、CLI の中で行ってください。
        ControlDeck はログインの情報（鍵・トークン）を読みません・画面にも出しません。
      </p>
    </BottomSheet>
  );
}
