export type AssistantMode = "chat" | "web" | "academic" | "deep" | "research" | "gen" | "run" | "code";
/** "investigate" は「調査」: Web・論文・Deep Research をまとめた選択。深さ（ResearchDepth）で中身が決まる。 */
export type AssistantModeChoice = "auto" | "investigate" | AssistantMode;
export type ResearchDepth = "auto" | "web" | "research" | "deep" | "academic";
export const SEARCH_MODES: AssistantMode[] = ["web", "academic", "research", "deep"];

export interface ModeDecision {
  mode: AssistantMode;
  reason: string;
  workflowId?: number;
  /** OpenCode に渡す前に、ControlDeck の調査で調べる（「調べて反映して」） */
  research?: boolean;
}

/** 会話の流れ（ボタンを押さずに OpenCode へ引き継ぐため。2026-11-18 利用者の依頼） */
export interface ConversationContext {
  /** この会話で OpenCode がすでに作業している */
  inCodeConversation?: boolean;
  /** この会話にチャット・調査の回答がある（引き継ぐものがある） */
  hasAnswers?: boolean;
}

// 作業を頼む語（コードを書く・直す・作る）
const WORK_WORDS = ["実装", "反映", "直して", "修正", "書き換え", "作り替え", "追加して", "組み込", "改良", "改善して",
  "リファクタ", "コードに", "プログラムに", "アプリに", "ウェブアプリ", "webアプリ", "スクリプトに", "ツールに", "移植",
  "作って", "書いて", "使って", "適用", "置き換え", "fix", "implement", "refactor"];
// チャットの後でも OpenCode へ切り替える、はっきりした開発の依頼
const BUILD_WORDS = ["実装して", "実装する", "コードにして", "コード化", "プログラムにして", "アプリにして", "アプリ化",
  "ウェブアプリ", "webアプリ", "スクリプトにして", "ツールにして", "これで作って", "これを作って", "開発して", "移植して"];

interface NamedWorkflow { id: number; name: string }

import routingWords from "./routingWords.json";

const includesAny = (text: string, words: string[]) => words.some((word) => text.includes(word.toLocaleLowerCase("ja")));

/** 入力だけで説明可能・即時に判定する。明示性の高い副作用モードを優先する。 */
export function detectAssistantMode(
  input: string,
  workflows: NamedWorkflow[] = [],
  canEditWorkflow = false,
  opencodeAvailable = false,
  context: ConversationContext = {},
): ModeDecision {
  const text = input.trim().toLocaleLowerCase("ja");
  if (!text) return context.inCodeConversation && opencodeAvailable
    ? { mode: "code", reason: "この会話は OpenCode で作業中" }
    : { mode: "chat", reason: "入力後に自動判定します" };
  const base = detectWithoutContext(text, workflows, canEditWorkflow, opencodeAvailable);
  if (!opencodeAvailable || base.mode === "run" || base.mode === "gen") return base;
  const research = SEARCH_MODES.includes(base.mode);
  const work = includesAny(text, WORK_WORDS);
  if (context.inCodeConversation) {
    // OpenCode の会話中: 調べる語があれば、ControlDeck の調査で調べてから OpenCode へ渡す（同じ 1 回の送信で）。
    // それ以外は OpenCode のまま（チャットへ判定し直さない）
    if (research) return { mode: "code", research: true, reason: work ? "調べてから OpenCode が反映" : "調べた結果を OpenCode に渡す" };
    return { mode: "code", reason: "この会話は OpenCode で作業中" };
  }
  if (base.mode === "chat" && context.hasAnswers && includesAny(text, BUILD_WORDS)) {
    return { mode: "code", reason: "ここまでの会話を OpenCode に引き継いで開発" };
  }
  if (research && includesAny(text, BUILD_WORDS)) {
    return { mode: "code", research: true, reason: "調べてから OpenCode が開発" };
  }
  return base;
}

function detectWithoutContext(
  text: string, workflows: NamedWorkflow[], canEditWorkflow: boolean, opencodeAvailable: boolean,
): ModeDecision {
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
