/** 自動再開で送る文言の設定。全体（既定）と、コンソールごと（空なら全体を使う）。 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchCommanderSettings, putCommanderSettings, setConsoleResumeMessage, type CommanderConsole } from "../../api/commander";
import { BottomSheet, primaryButtonClass, secondaryButtonClass } from "../../components/ui";
import { useToasts } from "../../stores";

const inputClass = "h-11 w-full rounded-xl border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900";

/** 全体の文言（コンソールに個別の文言が無いときに送る）。 */
export function GlobalResumeMessageSheet({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({ queryKey: ["commander-settings"], queryFn: fetchCommanderSettings });
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? data?.resume_message ?? "";
  const save = async (message: string) => {
    try {
      await putCommanderSettings({ resume_message: message });
      qc.invalidateQueries({ queryKey: ["commander-settings"] });
      show(message ? "全体の再開の文言を保存しました" : "全体の再開の文言を既定に戻しました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    }
  };
  return (
    <BottomSheet title="自動再開の文言（全体）" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        上限で止まったコンソールへ、回復の時刻に送る文言です。コンソールごとに文言を決めていなければ、これを送ります。
      </p>
      <input
        value={value}
        onChange={(e) => setDraft(e.target.value)}
        maxLength={500}
        aria-label="全体の再開の文言"
        className={`mt-3 ${inputClass}`}
      />
      <div className="mt-3 flex gap-2">
        <button type="button" onClick={() => void save(value.trim())} disabled={!value.trim()} className={`flex-1 ${primaryButtonClass}`}>保存</button>
        <button type="button" onClick={() => void save("")} className={secondaryButtonClass}>
          既定に戻す（{data?.default_resume_message ?? "続けてください"}）
        </button>
      </div>
    </BottomSheet>
  );
}

/** そのコンソールだけの文言（空なら全体の文言）。 */
export function ConsoleResumeMessageSheet({ item, cliName, onClose }: { item: CommanderConsole; cliName: string; onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({ queryKey: ["commander-settings"], queryFn: fetchCommanderSettings });
  const [value, setValue] = useState(item.commander_resume_message ?? "");
  const save = async (message: string) => {
    try {
      await setConsoleResumeMessage(item.id, message);
      qc.invalidateQueries({ queryKey: ["commander-consoles"] });
      show(message ? "このコンソールの再開の文言を保存しました" : "全体の再開の文言を使うようにしました");
      onClose();
    } catch (e) {
      show(e instanceof Error ? e.message : "保存できませんでした", "error");
    }
  };
  return (
    <BottomSheet title={`${cliName} の再開の文言`} onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        このコンソールだけで送る文言です。空にすると全体の文言（{data?.resume_message ?? "…"}）を送ります。
      </p>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        maxLength={500}
        placeholder={data?.resume_message ?? ""}
        aria-label="このコンソールの再開の文言"
        className={`mt-3 ${inputClass}`}
      />
      <div className="mt-3 flex gap-2">
        <button type="button" onClick={() => void save(value.trim())} className={`flex-1 ${primaryButtonClass}`}>保存</button>
        <button type="button" onClick={() => void save("")} className={secondaryButtonClass}>全体の文言を使う</button>
      </div>
    </BottomSheet>
  );
}
