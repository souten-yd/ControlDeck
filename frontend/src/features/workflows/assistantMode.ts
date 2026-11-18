export type AssistantMode = "chat" | "web" | "academic" | "deep" | "research" | "gen" | "run" | "code";
/** "investigate" は「調査」: Web・論文・Deep Research をまとめた選択。深さ（ResearchDepth）で中身が決まる。 */
export type AssistantModeChoice = "auto" | "investigate" | AssistantMode;
export type ResearchDepth = "auto" | "web" | "research" | "deep" | "academic";
export const SEARCH_MODES: AssistantMode[] = ["web", "academic", "research", "deep"];

/** 会話ごとに覚える選択。2026-11-18 利用者「固定している場合は変わって欲しくない。開き直すと外れないか」。
 *  「自動」は毎回判定し、機能を選んだときだけ固定する。固定は送信や判定では変えない。 */
export interface ConversationPrefs { choice: AssistantModeChoice; depth: ResearchDepth; agent: "build" | "plan" }
const LS_PREFS = "cd-chat-conversation-prefs";
const MAX_REMEMBERED = 200;
const CHOICES: AssistantModeChoice[] = ["auto", "investigate", "chat", "gen", "run", "code"];
const DEPTHS: ResearchDepth[] = ["auto", "web", "research", "deep", "academic"];

export const validChoice = (value: unknown): AssistantModeChoice | undefined =>
  CHOICES.includes(value as AssistantModeChoice) ? value as AssistantModeChoice : undefined;
export const validDepth = (value: unknown): ResearchDepth | undefined =>
  DEPTHS.includes(value as ResearchDepth) ? value as ResearchDepth : undefined;

type PrefField = keyof ConversationPrefs;
/** seen: この端末が選択を決めた時点で見えていた最後の発言（"*" は分岐のように「いまの選択が正」） */
type SavedPrefs = Partial<ConversationPrefs> & { seen?: string };

function allPrefs(): Record<string, SavedPrefs> {
  try {
    const value = JSON.parse(localStorage.getItem(LS_PREFS) || "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

/** 変えた項目だけを覚える（読み込み前の既定値で、他の項目を上書きしない） */
export function updateConversationPrefs(id: string, changed: Partial<ConversationPrefs>, seen?: string): void {
  try {
    const { [id]: previous = {}, ...rest } = allPrefs();
    const next: SavedPrefs = { ...previous, ...changed, ...(seen ? { seen } : {}) };
    const entries = [...Object.entries(rest), [id, next] as const].slice(-MAX_REMEMBERED);
    localStorage.setItem(LS_PREFS, JSON.stringify(Object.fromEntries(entries)));
  } catch {
    // 保存できない環境でも、送った発言の記録から戻せる
  }
}

/** 発言の順で新旧を決める（端末とサーバーの時計は比べない）。この端末が見ていない新しい発言があれば
 *  別の端末の選択を使い、そうでなければこの端末の選択を使う。 */
export function resolveConversationPrefs(id: string, sent: Partial<ConversationPrefs>, sentId: string,
  orderedIds: string[]): Partial<ConversationPrefs> {
  const saved = allPrefs()[id] ?? {};
  const localIsCurrent = saved.seen === "*" || !sentId
    || (saved.seen !== undefined && orderedIds.indexOf(saved.seen) >= orderedIds.indexOf(sentId));
  const pick = <K extends PrefField>(field: K): ConversationPrefs[K] | undefined => {
    const local = saved[field] as ConversationPrefs[K] | undefined;
    return localIsCurrent ? local ?? sent[field] : sent[field] ?? local;
  };
  const agent = pick("agent");
  return { choice: validChoice(pick("choice")), depth: validDepth(pick("depth")),
    agent: agent === "plan" || agent === "build" ? agent : undefined };
}

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
// チャットの後でも OpenCode へ切り替える、はっきりした開発の依頼（動詞の形だけ。名詞だけでは切り替えない:
// 「ウェブアプリとネイティブの違いは？」はチャット。レビュー指摘 2026-11-18）
const BUILD_WORDS = ["実装して", "実装する", "コードにして", "コード化して", "プログラムにして", "アプリにして",
  "アプリ化して", "ウェブアプリにして", "webアプリにして", "スクリプトにして", "ツールにして", "これで作って",
  "これを作って", "開発して", "移植して"];
// 質問（作業の依頼ではない）。OpenCode の会話中でも、作業の語が無ければチャットで答える

interface NamedWorkflow { id: number; name: string }

import routingWords from "./routingWords.json";

const includesAny = (text: string, words: string[]) => words.some((word) => text.includes(word.toLocaleLowerCase("ja")));

/** 入力だけで説明可能・即時に判定する。明示性の高い副作用モードを優先する。 */
/** OpenCode に送る前に調べるか（OpenCode を手で選んでいるとき用）。調べる語と作業の語の両方がある */
export function wantsResearch(input: string): boolean {
  const text = input.trim().toLocaleLowerCase("ja");
  const base = detectWithoutContext(text, [], false, true);
  return SEARCH_MODES.includes(base.mode) && includesAny(text, WORK_WORDS);
}

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
    // OpenCode の会話中:
    //  調べる語＋作業の語（調べて反映して）→ 同じ 1 回の送信で調べてから OpenCode へ
    //  調べる語だけ（詳しく調査してレポートに等）→ 調査で答える（結果は次に OpenCode へ送るとき引き継がれる）
    //  それ以外（質問も含む）→ OpenCode のまま。作業中の質問はファイルを見ないと答えられず、チャットへ回すと
    //  道具が無いのにモデルが <tool_call> を文字で書いていた（2026-11-18 本番）。チャットにしたいときは＋で選ぶ
    if (research && work) return { mode: "code", research: true, reason: "調べてから OpenCode が反映" };
    if (research) return { ...base, reason: `${base.reason}（結果は次に OpenCode へ引き継ぎ）` };
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
