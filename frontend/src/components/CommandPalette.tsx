import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useApps, useAppAction, useMeta } from "../api/hooks";
import { useAuth } from "../stores";
import { IconSearch } from "./icons";

interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

export function CommandPalette({
  onClose,
  onPower,
}: {
  onClose: () => void;
  onPower: (a: "reboot" | "shutdown") => void;
}) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const can = useAuth((s) => s.can);
  const { data: apps } = useApps();
  const { data: meta } = useMeta();
  const action = useAppAction();

  useEffect(() => inputRef.current?.focus(), []);

  const commands = useMemo<Command[]>(() => {
    const list: Command[] = [
      { id: "nav-home", label: "概要を開く", run: () => navigate("/") },
      { id: "nav-apps", label: "アプリ一覧を開く", run: () => navigate("/apps") },
      { id: "nav-assistant", label: "AIアシスタントを開く", run: () => navigate("/assistant") },
      { id: "nav-workflows", label: "ワークフローを開く", run: () => navigate("/workflows") },
      { id: "nav-runner", label: "公開アプリを開く", run: () => navigate("/runner") },
      { id: "nav-logs", label: "ログを開く", run: () => navigate("/logs") },
      { id: "nav-system", label: "システム監視を開く", run: () => navigate("/system") },
      { id: "nav-settings", label: "設定を開く", run: () => navigate("/settings") },
    ];
    if (meta?.enabled_features.includes("opencode"))
      list.push({ id: "nav-opencode", label: "OpenCodeを開く", run: () => navigate("/opencode") });
    if (can("apps.edit"))
      list.push({ id: "app-add", label: "アプリを追加", run: () => navigate("/apps?add=1") });
    for (const app of apps ?? []) {
      const running = app.runtime.status === "RUNNING";
      list.push({
        id: `app-${app.id}`,
        label: `${app.name} のログ`,
        run: () => navigate(`/logs?app=${app.id}`),
      });
      if (running && can("apps.stop"))
        list.push({
          id: `stop-${app.id}`,
          label: `${app.name} を停止`,
          run: () => action.mutate({ id: app.id, action: "stop" }),
        });
      if (!running && can("apps.start"))
        list.push({
          id: `start-${app.id}`,
          label: `${app.name} を起動`,
          run: () => action.mutate({ id: app.id, action: "start" }),
        });
    }
    if (can("power.manage")) {
      list.push({ id: "power-reboot", label: "PC を再起動", hint: "要確認", run: () => onPower("reboot") });
      list.push({ id: "power-shutdown", label: "PC をシャットダウン", hint: "要確認", run: () => onPower("shutdown") });
    }
    return list;
  }, [apps, can, navigate, action, onPower, meta?.enabled_features]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands.slice(0, 10);
    return commands.filter((c) => c.label.toLowerCase().includes(q)).slice(0, 10);
  }, [commands, query]);

  const run = (cmd: Command) => {
    onClose();
    cmd.run();
  };

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/40 pt-[15vh] backdrop-blur-[2px]"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-[min(560px,92vw)] overflow-hidden rounded-2xl bg-white shadow-2xl dark:bg-zinc-900">
        <div className="flex items-center gap-2 border-b border-zinc-200 px-4 dark:border-zinc-800">
          <IconSearch className="text-zinc-400" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSelected(0);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSelected((s) => Math.min(s + 1, filtered.length - 1));
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setSelected((s) => Math.max(s - 1, 0));
              }
              if (e.key === "Enter" && filtered[selected]) run(filtered[selected]);
            }}
            placeholder="アプリ・操作を検索..."
            aria-label="コマンド検索"
            className="w-full bg-transparent py-3.5 text-sm outline-none placeholder:text-zinc-400"
          />
        </div>
        <ul role="listbox" className="max-h-72 overflow-y-auto py-1">
          {filtered.map((c, i) => (
            <li key={c.id} role="option" aria-selected={i === selected}>
              <button
                onClick={() => run(c)}
                onMouseEnter={() => setSelected(i)}
                className={`flex w-full items-center px-4 py-2.5 text-left text-sm ${
                  i === selected ? "bg-accent-50 dark:bg-accent-600/15" : ""
                }`}
              >
                {c.label}
                {c.hint && <span className="ml-auto text-xs text-zinc-400">{c.hint}</span>}
              </button>
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="px-4 py-6 text-center text-sm text-zinc-400">該当なし</li>
          )}
        </ul>
      </div>
    </div>,
    document.body,
  );
}
