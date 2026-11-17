export type AssistantMode = "chat" | "web" | "academic" | "deep" | "research" | "gen" | "run" | "code";
/** "investigate" は「調査」: Web・論文・Deep Research をまとめた選択。深さ（ResearchDepth）で中身が決まる。 */
export type AssistantModeChoice = "auto" | "investigate" | AssistantMode;
export type ResearchDepth = "auto" | "web" | "research" | "deep" | "academic";
export const SEARCH_MODES: AssistantMode[] = ["web", "academic", "research", "deep"];

export interface ModeDecision {
  mode: AssistantMode;
  reason: string;
  workflowId?: number;
}

interface NamedWorkflow { id: number; name: string }

import routingWords from "./routingWords.json";

const includesAny = (text: string, words: string[]) => words.some((word) => text.includes(word.toLocaleLowerCase("ja")));

/** 入力だけで説明可能・即時に判定する。明示性の高い副作用モードを優先する。 */
export function detectAssistantMode(
  input: string,
  workflows: NamedWorkflow[] = [],
  canEditWorkflow = false,
  opencodeAvailable = false,
): ModeDecision {
  const text = input.trim().toLocaleLowerCase("ja");
  if (!text) return { mode: "chat", reason: "入力後に自動判定します" };

  if (opencodeAvailable && includesAny(text, ["opencode", "オープンコード", "コーディングエージェント"])) {
    return { mode: "code", reason: "OpenCodeによるコーディング作業" };
  }
  const mentionsWorkflow = includesAny(text, ["ワークフロー", "workflow", "フロー"]);
  const wantsRun = includesAny(text, ["実行して", "実行する", "走らせて", "起動して", "run "]);
  if (mentionsWorkflow && wantsRun) {
    const matches = workflows.filter((workflow) => text.includes(workflow.name.toLocaleLowerCase("ja")));
    return {
      mode: "run",
      reason: matches.length === 1 ? `「${matches[0].name}」の実行` : "ワークフロー実行の依頼",
      workflowId: matches.length === 1 ? matches[0].id : undefined,
    };
  }
  if (canEditWorkflow && mentionsWorkflow && includesAny(text, ["作って", "作成", "構築", "生成", "設計"])) {
    return { mode: "gen", reason: "ワークフロー作成の依頼" };
  }
  // 調査の語はサーバー（assistant_planner.py）と同じ表（routingWords.json）を使う
  if (includesAny(text, routingWords.deep)) {
    return { mode: "deep", reason: "複数ソースを使う詳細調査" };
  }
  const academic = includesAny(text, routingWords.academic);
  const current = includesAny(text, routingWords.current);
  const web = current || includesAny(text, routingWords.search);
  if (academic && (current || includesAny(text, routingWords.combined))) {
    return { mode: "research", reason: "Webと学術情報を組み合わせる調査" };
  }
  if (academic) {
    return { mode: "academic", reason: "学術情報の検索" };
  }
  if (web) {
    return { mode: "web", reason: "現在のWeb情報が必要" };
  }
  return { mode: "chat", reason: "通常の対話" };
}
