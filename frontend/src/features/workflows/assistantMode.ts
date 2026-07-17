export type AssistantMode = "chat" | "web" | "academic" | "deep" | "research" | "gen" | "run";
export type AssistantModeChoice = "auto" | AssistantMode;

export interface ModeDecision {
  mode: AssistantMode;
  reason: string;
  workflowId?: number;
}

interface NamedWorkflow { id: number; name: string }

const includesAny = (text: string, words: string[]) => words.some((word) => text.includes(word));

/** 入力だけで説明可能・即時に判定する。明示性の高い副作用モードを優先する。 */
export function detectAssistantMode(
  input: string,
  workflows: NamedWorkflow[] = [],
  canEditWorkflow = false,
): ModeDecision {
  const text = input.trim().toLocaleLowerCase("ja");
  if (!text) return { mode: "chat", reason: "入力後に自動判定します" };

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
  if (includesAny(text, ["deep research", "deepサーチ", "ディープリサーチ", "詳細に調査", "徹底的に調査", "調査レポート"])) {
    return { mode: "deep", reason: "複数ソースを使う詳細調査" };
  }
  const academic = includesAny(text, ["論文", "arxiv", "学術", "先行研究", "査読", "研究文献"]);
  const web = includesAny(text, ["web検索", "ウェブ検索", "検索して", "最新", "現在の", "ニュース", "価格", "天気", "調べて"]);
  if (academic && web) {
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
