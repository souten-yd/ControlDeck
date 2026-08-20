import { useState } from "react";
import { api } from "../../api/client";
import { addonLabel, useEffectiveAddons } from "../../api/addons";
import { DropdownMenu } from "../../components/ui";
import { useToasts } from "../../stores";

export function ContextActionsMenu({
  contextType,
  resourceId,
}: {
  contextType: "file" | "project" | "workflow" | "job";
  resourceId: string;
}) {
  const show = useToasts((state) => state.show);
  const [running, setRunning] = useState<string | null>(null);
  const { data } = useEffectiveAddons(false);
  const actions = (data?.contributions.context_actions ?? []).filter((item) => item.contexts?.includes(contextType));
  if (actions.length === 0) return null;

  const invoke = async (addonId: string, contributionId: string, label: string) => {
    const key = `${addonId}:${contributionId}`;
    setRunning(key);
    try {
      await api(`/addons/${encodeURIComponent(addonId)}/context-actions/${encodeURIComponent(contributionId)}/invoke`, {
        method: "POST",
        json: { context_type: contextType, resource_id: resourceId, input: {} },
      });
      show(`${label}を実行しました`);
    } catch (error) {
      show(error instanceof Error ? error.message : "拡張機能actionを実行できません", "error");
    } finally {
      setRunning(null);
    }
  };

  return (
    <DropdownMenu
      ariaLabel="拡張機能のコンテキストアクション"
      trigger={<span aria-hidden className="text-base font-semibold text-violet-600">◇</span>}
      items={actions.map((action) => {
        const label = `${addonLabel(action.label)}（${action.addon_id}）`;
        return {
          label: running === `${action.addon_id}:${action.id}` ? `${label} 実行中…` : label,
          onSelect: () => {
            if (!running) void invoke(action.addon_id, action.id, addonLabel(action.label));
          },
        };
      })}
    />
  );
}
