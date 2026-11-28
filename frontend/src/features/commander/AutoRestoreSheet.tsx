/** 外から閉じられたロック中のコンソールを自動で開き直すかの全体設定（既定は入）。
 * 細かい設定なのでエキスパートのときだけメニューに出す。 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchCommanderSettings, putCommanderSettings } from "../../api/commander";
import { BottomSheet } from "../../components/ui";
import { useToasts } from "../../stores";

export function AutoRestoreSheet({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const { data } = useQuery({ queryKey: ["commander-settings"], queryFn: fetchCommanderSettings });
  const [busy, setBusy] = useState(false);
  const on = data?.auto_restore !== false;
  const toggle = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await putCommanderSettings({ auto_restore: !on });
      qc.invalidateQueries({ queryKey: ["commander-settings"] });
      show(on ? "自動で戻すのを切りました" : "自動で戻すのを入れました");
    } catch (e) {
      show(e instanceof Error ? e.message : "切り替えられませんでした", "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <BottomSheet title="自動で戻す（全体）" onClose={onClose}>
      <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
        ロックしたコンソールが ControlDeck を通さずに閉じられたとき、同じ CLI・アカウント・作業フォルダ・表示名で
        開き直します（1 つのコンソールにつき 10 分に 3 回まで）。オフにすると、閉じられたことを知らせるだけにします。
      </p>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="自動で戻す"
        disabled={busy}
        onClick={() => void toggle()}
        className="mt-3 flex min-h-11 w-full items-center justify-between gap-3 rounded-xl border border-zinc-200 px-3 text-left text-sm dark:border-zinc-800"
      >
        <span className="font-medium">自動で戻す</span>
        <span className={`relative h-5 w-9 shrink-0 rounded-full transition ${on ? "bg-accent-600" : "bg-zinc-300 dark:bg-zinc-700"}`}>
          <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${on ? "left-[18px]" : "left-0.5"}`} />
        </span>
      </button>
      <p data-auto-restore-state={on ? "on" : "off"} className="mt-2 text-[11px] text-zinc-500 dark:text-zinc-400">
        いま: {on ? "入（外から閉じられたら開き直す）" : "切（知らせるだけ）"}
      </p>
    </BottomSheet>
  );
}
