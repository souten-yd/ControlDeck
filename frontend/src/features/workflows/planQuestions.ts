/** OpenCode の回の本文から「決めてもらいたいこと」の番号付きの質問と、その選択肢・推奨を抜く（LLM は使わない）。
 *  書き方は SKILL.md「利用者に決めてもらうこと」: 質問の下に `- 選択肢（推奨）— 補足`、複数選べる質問は題に「（複数可）」。
 *  古い書き方（`→ 推奨：○○`・`- 推奨: ○○`）は、推奨 1 つの選択肢として読む。選択肢も推奨も無い質問は自由入力だけの質問。
 *  見出しが無ければ空を返す。2026-11-23 利用者の通し（評価 U13）: Claude の質問画面のように選択肢・推奨・自由入力で答えたい。 */
export interface PlanOption { label: string; recommended: boolean; note: string }
export interface PlanQuestion { n: number; question: string; options: PlanOption[]; multi: boolean }
/** 質問ごとの答え: 選んだ選択肢の位置と、「その他」の自由入力 */
export interface PlanPick { picked: number[]; other: string }

const SECTION = /決めて(もらい|もらう|ほし)(たい)?こと/;
const ITEM = /^\s{0,3}(\d{1,2})[.．)）]\s*(.+)$/;
const BULLET = /^\s*[-*・]\s+(.+)$/;
const RECOMMEND = /推奨\s*[:：]\s*([^\n]*)/;
const MARK = /[（(]\s*推奨\s*[)）]/;
const MULTI = /[（(]\s*複数可\s*[)）]/;
const plain = (s: string) => s.replace(/\*\*|__|`/g, "").trim();
const clip = (s: string, max: number) => (s.length > max ? `${s.slice(0, max - 1)}…` : s);
const oldRecommended = (s: string) => plain(s).split(/。|\s[—–-]\s|（|\(/)[0].trim();

function option(text: string): PlanOption {
  const t = plain(text);
  const recommended = MARK.test(t);
  const [label, ...rest] = t.replace(MARK, "").split(/\s*[—–]\s*|\s-\s/);
  return { label: clip(label.trim(), 60), recommended, note: clip(rest.join(" — ").trim(), 120) };
}

export function extractPlanQuestions(content: string): PlanQuestion[] {
  const lines = content.split("\n");
  const start = lines.findIndex((line) => SECTION.test(line) && plain(line.replace(/^#+/, "")).length <= 40);
  if (start < 0) return [];
  const items: { n: number; text: string[]; bullets: string[] }[] = [];
  for (const line of lines.slice(start + 1)) {
    if (/^ {0,3}#{1,4}\s/.test(line) || /^ {0,3}(-{3,}|\*{3,})\s*$/.test(line)) break;
    const m = ITEM.exec(line);
    const b = BULLET.exec(line);
    if (m) items.push({ n: Number(m[1]), text: [m[2]], bullets: [] });
    else if (items.length && b) items[items.length - 1].bullets.push(b[1]);
    else if (items.length && line.trim()) items[items.length - 1].text.push(line.trim());
  }
  return items.map((item) => {
    let joined = item.text.join(" ");
    const multi = MULTI.test(joined);
    joined = joined.replace(MULTI, "");
    const options: PlanOption[] = [];
    let question = joined;
    const rec = RECOMMEND.exec(joined);
    if (rec) {   // 古い書き方: 質問 → 推奨：○○
      question = joined.slice(0, rec.index);
      const label = oldRecommended(rec[1]);
      if (label) options.push({ label: clip(label, 60), recommended: true, note: "" });
    }
    for (const bullet of item.bullets) {
      const old = RECOMMEND.exec(bullet);
      if (old) {
        const label = oldRecommended(old[1]);
        if (label) options.push({ label: clip(label, 60), recommended: true, note: "" });
      } else options.push(option(bullet));
    }
    return { n: item.n, question: clip(plain(question).replace(/[→\-—–\s]+$/, ""), 80), options, multi };
  });
}

/** 質問の最初の状態: 推奨の選択肢を選んでおく（単一選択は最初の推奨だけ） */
export function initialPicks(questions: PlanQuestion[]): PlanPick[] {
  return questions.map((q) => {
    const rec = q.options.flatMap((o, i) => (o.recommended ? [i] : []));
    return { picked: q.multi ? rec : rec.slice(0, 1), other: "" };
  });
}

/** 質問 1 つの答えの文（無ければ空）。自由入力があればそれを使う */
export function answerOf(q: PlanQuestion, pick: PlanPick): string {
  const other = pick.other.trim();
  if (other) return other;
  const picked = [...pick.picked].sort((a, b) => a - b);
  if (!picked.length) return "";
  const labels = picked.map((i) => q.options[i].label).join("、");
  const rec = q.options.flatMap((o, i) => (o.recommended ? [i] : []));
  return rec.length && rec.length === picked.length && rec.every((i, k) => i === picked[k]) ? `${labels}（推奨どおり）` : labels;
}

/** 答えていない質問の番号 */
export function unansweredNumbers(questions: PlanQuestion[], picks: PlanPick[]): number[] {
  return questions.filter((q, i) => !answerOf(q, picks[i])).map((q) => q.n);
}

/** ボタンで同じ会話へ送る文（1 か所で組み立てる）。replan はプランを直させる送り方 */
export function buildPlanAnswer(questions: PlanQuestion[], picks: PlanPick[], extra: string, replan: boolean): string {
  const rows = questions.map((q, i) => `${q.n}. ${q.question} → ${answerOf(q, picks[i])}`);
  const note = extra.trim() ? [`補足: ${extra.trim()}`] : [];
  return ["決めたこと:", ...rows, ...note, replan ? "この答えでプランを直してください。" : "この答えでプランどおり実装してください。"].join("\n");
}
