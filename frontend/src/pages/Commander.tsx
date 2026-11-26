/** AI Commander: AI の CLI のコンソールを 1 つの画面で何個でも開く。
 *
 * PC（768px〜）はタイル（2 列、幅が広ければ 3 列）で並べ、タイルの中に
 * 端末を埋め込む。押すと大きく開く。モバイル（〜767px）は 1 つずつ
 * （リスト → 全画面）。主操作は「＋ コンソール」1 個、アカウントの管理は
 * その他メニュー（UI ルール: 全ボタンを並べない）。 */
import { lazy, Suspense, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { closeConsole, fetchClis, fetchConsoles, type CommanderConsole } from "../api/commander";
import { useMediaQuery } from "../hooks/useMediaQuery";
import { ConfirmDialog, EmptyState, IconButton, Skeleton, DropdownMenu } from "../components/ui";
import { IconDots, IconPlus } from "../components/icons";
import { PageHeader } from "../components/PageHeader";
import { ConsoleRow, ConsoleTile } from "../features/commander/ConsoleTile";
import { NewConsoleSheet } from "../features/commander/NewConsoleSheet";
import { AccountsSheet } from "../features/commander/AccountsSheet";
import { useToasts } from "../stores";

const XtermView = lazy(() => import("../features/terminal/XtermView"));

export default function CommanderPage() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const isDesktop = useMediaQuery("(min-width: 768px)");
  const [newSheet, setNewSheet] = useState(false);
  const [accountsSheet, setAccountsSheet] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [closing, setClosing] = useState<CommanderConsole | null>(null);

  const { data: consoles, isLoading } = useQuery({
    queryKey: ["commander-consoles"],
    queryFn: fetchConsoles,
    refetchInterval: expanded ? 5_000 : 10_000,
  });
  const { data: clis = [] } = useQuery({
    queryKey: ["commander-clis"],
    queryFn: fetchClis,
  });
  const cliName = (id: string) => clis.find((c) => c.id === id)?.name ?? id;

  const invalidate = () => qc.invalidateQueries({ queryKey: ["commander-consoles"] });

  const kill = async (item: CommanderConsole) => {
    try {
      await closeConsole(item.id);
      invalidate();
      if (expanded === item.id) setExpanded(null);
      show("コンソールを閉じました");
    } catch (e) {
      show(e instanceof Error ? e.message : "閉じられませんでした", "error");
    }
    setClosing(null);
  };

  // 全画面表示（タイルを大きく開く・モバイルで 1 つ選ぶ）
  if (expanded) {
    const items = consoles ?? [];
    return (
      <Suspense fallback={<div className="grid h-full place-items-center text-sm text-zinc-400">ターミナルを読み込み中...</div>}>
        <XtermView
          sessionId={expanded}
          sessions={items}
          onSwitch={setExpanded}
          onExit={() => {
            setExpanded(null);
            invalidate();
          }}
        />
      </Suspense>
    );
  }

  const items = consoles ?? [];
  return (
    <div className="mx-auto max-w-7xl p-4 md:p-6">
      <PageHeader title="AI Commander" actions={<>
        <DropdownMenu
          ariaLabel="その他の操作"
          trigger={<IconDots />}
          items={[{ label: "アカウントの管理", onSelect: () => setAccountsSheet(true) }]}
        />
        <IconButton variant="primary" label="＋ コンソール" onClick={() => setNewSheet(true)}>
          <IconPlus />
        </IconButton>
      </>} />

      <p className="mb-4 text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        Claude Code・Codex・OpenCode などのコンソールをアカウントごとに開いて、1 つの画面で並べます。
        {items.length > 0 && <span className="ml-1 tabular-nums">（{items.length} 個）</span>}
      </p>

      {isLoading ? (
        <Skeleton className="h-24" />
      ) : items.length === 0 ? (
        <EmptyState actions={
          <button onClick={() => setNewSheet(true)} className="rounded-xl bg-accent-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-accent-700">コンソールを開く</button>
        }>
          コンソールはありません
        </EmptyState>
      ) : isDesktop ? (
        <ul className="grid grid-cols-2 gap-3 xl:grid-cols-3">
          {items.map((item) => (
            <ConsoleTile
              key={item.id}
              item={item}
              cliName={cliName(item.commander_cli)}
              onExpand={() => setExpanded(item.id)}
              onClose={() => setClosing(item)}
            />
          ))}
        </ul>
      ) : (
        <ul className="grid gap-2">
          {items.map((item) => (
            <ConsoleRow
              key={item.id}
              item={item}
              cliName={cliName(item.commander_cli)}
              onOpen={() => setExpanded(item.id)}
              onClose={() => setClosing(item)}
            />
          ))}
        </ul>
      )}

      {closing && (
        <ConfirmDialog
          title={`${cliName(closing.commander_cli)} のコンソールを閉じますか？`}
          message="実行中の CLI は終了します。この操作は取り消せません。"
          confirmLabel="閉じる"
          onConfirm={() => void kill(closing)}
          onClose={() => setClosing(null)}
        />
      )}
      {newSheet && <NewConsoleSheet clis={clis} onClose={() => setNewSheet(false)} />}
      {accountsSheet && <AccountsSheet clis={clis} onClose={() => setAccountsSheet(false)} />}
    </div>
  );
}
