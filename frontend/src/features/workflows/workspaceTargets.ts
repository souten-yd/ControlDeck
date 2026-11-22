// 開発の作業場の「開発対象」と機能（2026-11-22）。backend の workspaces.PRESETS と同じ内容を画面に持つ。
// 強制は backend（中継・箱の環境・取り込み）が行う。ここは選ぶ画面と説明だけ。
export type TargetId = "app" | "llm_app" | "controldeck";
export type CapabilityId = "llm" | "tools" | "mirror" | "gpu";

export const TARGETS: { id: TargetId; label: string; short: string; badge: string; note: string; capabilities: CapabilityId[] }[] = [
  { id: "app", label: "普通の開発", short: "普通の開発", badge: "開発", capabilities: ["tools"],
    note: "ローカル LLM は借りません（普通の開発）" },
  { id: "llm_app", label: "ローカル LLM を使うアプリ", short: "LLM アプリ", badge: "LLM アプリ", capabilities: ["llm", "tools"],
    note: "PC のローカル LLM を AI とアプリが借ります" },
  { id: "controldeck", label: "ControlDeck 本体", short: "本体", badge: "本体", capabilities: ["llm", "tools", "mirror"],
    note: "本体の複製で確かめ、GitHub の PR で取り込み" },
];

export const CAPABILITIES: { id: CapabilityId; label: string; note: string }[] = [
  { id: "llm", label: "ローカル LLM を借りる", note: "メイン PC のモデルを使う" },
  { id: "tools", label: "ControlDeck の道具", note: "MCP の道具を呼べる" },
  { id: "mirror", label: "ControlDeck の複製", note: "本体の複製を中で動かす" },
  { id: "gpu", label: "GPU を借りる", note: "準備中" },
];

export function targetInfo(id: string | undefined) {
  return TARGETS.find((t) => t.id === id);
}

/** 永続チャットの会話 ID を覚える localStorage の鍵。AssistantChat（読む・書く）と ProjectLab の
 *  「会話を開く」（書く）が共有する（2026-11-22 レビュー: 3 箇所の重複を 1 か所へ） */
export const LS_CONV = "cd-chat-conversation";

/** 作業場の札の文字（「作業場・本体」など）。古い API で target が無いときは「作業場」だけ */
export function badgeText(target: string | undefined): string {
  const info = targetInfo(target);
  return info ? `作業場・${info.badge}` : "作業場";
}

/** ＋メニューの「開発対象」へ渡す状態。create なら選べる、view なら表示だけ */
export type DevTargetState = {
  mode: "create" | "view";
  target: string;
  capabilities: string[];
  isBody: boolean;          // 元が ControlDeck 本体か（controldeck と mirror の条件）
  canManage: boolean;       // settings.manage（controldeck の作成の条件）
  onTarget: (id: TargetId) => void;
  onCapability: (id: CapabilityId, on: boolean) => void;
};
