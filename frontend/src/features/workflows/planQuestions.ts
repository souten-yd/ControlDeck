/** OpenCode の回の本文から「決めてもらいたいこと」の番号付きの質問と、その「推奨：」を抜く（LLM は使わない）。
 *  質問の検出は本文の書き方に頼る（SKILL.md「利用者に決めてもらうこと」の書き方）。見つからなければ空を返し、何も出さない。
 *  2026-11-23 利用者の通し（評価 U13）: 推奨つきの質問に「両方推奨どおり」と文で答えていた。 */
export interface PlanQuestion { n: number; question: string; recommended: string }

const SECTION = /決めて(もらい|もらう|ほし)(たい)?こと/;
const ITEM = /^\s{0,3}(\d{1,2})[.．)）]\s*(.+)$/;
const RECOMMEND = /推奨\s*[:：]\s*([^\n]*)/;
const plain = (s: string) => s.replace(/\*\*|__|`/g, "").trim();
const clip = (s: string, max: number) => (s.length > max ? `${s.slice(0, max - 1)}…` : s);

export function extractPlanQuestions(content: string): PlanQuestion[] {
  const lines = content.split("\n");
  const start = lines.findIndex((line) => SECTION.test(line) && plain(line.replace(/^#+/, "")).length <= 40);
  if (start < 0) return [];
  const items: { n: number; text: string[] }[] = [];
  for (const line of lines.slice(start + 1)) {
    if (/^ {0,3}#{1,4}\s/.test(line) || /^ {0,3}(-{3,}|\*{3,})\s*$/.test(line)) break;
    const m = ITEM.exec(line);
    if (m) items.push({ n: Number(m[1]), text: [m[2]] });
    else if (items.length && line.trim()) items[items.length - 1].text.push(line.trim());
  }
  const out: PlanQuestion[] = [];
  for (const item of items) {
    const joined = item.text.join(" ");
    const rec = RECOMMEND.exec(joined);
    const recommended = rec ? plain(rec[1]).split(/。|\s[—–-]\s|（|\(/)[0].trim() : "";
    if (!recommended) continue;
    const question = plain(joined.slice(0, rec!.index)).replace(/[→\-—–\s]+$/, "");
    out.push({ n: item.n, question: clip(question, 60), recommended: clip(recommended, 40) });
  }
  return out.length && out.length === items.length ? out : [];   // 推奨の無い質問があれば「推奨どおり」は言えない
}

/** ボタンで同じ会話へ送る文。 */
export function recommendedAnswer(questions: PlanQuestion[]): string {
  return `推奨どおりでお願いします（${questions.map((q) => `${q.n}: ${q.recommended}`).join("、")}）`;
}
