/** 回答の Markdown から、声で読む主要な文言だけを取り出す（TTS 返答・会話モード）。
 *
 * 2026-11-19 利用者「チャットの文言や表示している文言の主要な文言を音声で回答する」。
 * 読まないもの: コード（「画面に表示」と一言だけ）、表、画像、URL、出典の番号と出典の節、Markdown の記号。
 * 長い回答は先頭から文の切れ目まで読み、「続きは画面」と添える（全部を読むと数分かかる）。 */

export const MAX_SPOKEN_CHARS = 1200;

export function speakableText(markdown: string, maxChars = MAX_SPOKEN_CHARS): string {
  let text = markdown.replace(/\r\n?/g, "\n");
  // 出典・参考文献の節から後ろは読まない
  text = text.replace(/\n#{1,6}\s*(出典|参考|参考文献|Sources?|References?)[^\n]*\n[\s\S]*$/i, "\n");
  let code = false;
  text = text.replace(/```[\s\S]*?(```|$)/g, () => {
    if (code) return "\n";
    code = true;
    return "\nコードは画面に表示しています。\n";
  });
  let table = false;
  text = text.split("\n").map((line) => {
    if (/^\s*\|.*\|\s*$/.test(line)) {
      if (table) return "";
      table = true;
      return "表は画面に表示しています。";
    }
    return line;
  }).join("\n");
  text = text
    .replace(/<think>[\s\S]*?<\/think>/g, "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")              // 画像
    .replace(/\[([^\]]+)\]\((?:[^)]*)\)/g, "$1")         // リンクは文字だけ
    .replace(/https?:\/\/\S+/g, "")                      // むき出しの URL
    .replace(/\[R?[A-Z0-9]{1,3}\](?:\s*[〜~,、-]\s*\[R?[A-Z0-9]{1,3}\])*/g, "")   // 出典の印 [1] [RK] [R1]〜[RA]
    .replace(/[（(]\s*[）)]/g, "")                         // 印を消して空になった括弧
    // 絵文字と、その後ろに付く見えない文字（異体字セレクタ・結合子）。Supertonic は読めず失敗する（2026-11-19 本番）
    .replace(/[\p{Extended_Pictographic}\u{1F3FB}-\u{1F3FF}\uFE0E\uFE0F\u200D\u20E3]/gu, "")
    .replace(/<[^>]+>/g, "")                             // HTML
    .replace(/`([^`]*)`/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")                  // 見出し
    .replace(/^\s*>\s?/gm, "")                           // 引用
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, "")           // 箇条書き
    .replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, "")     // 区切り線
    .replace(/(\*\*|__|~~)(.+?)\1/g, "$2")
    .replace(/(^|[^\w*])[*_]([^*_\n]+)[*_](?=[^\w*]|$)/g, "$1$2");
  // 行は文として区切る（句点の無い行は句点を補う）
  const lines = text.split("\n").map((line) => line.replace(/\s+/g, " ").replace(/\s+([。、．，.,!?！？])/g, "$1").trim()).filter(Boolean)
    .map((line) => (/[。．.!?！？」』）)]$/.test(line) ? line : `${line}。`));
  let spoken = lines.join("");
  if (spoken.length > maxChars) {
    const head = spoken.slice(0, maxChars);
    const end = Math.max(head.lastIndexOf("。"), head.lastIndexOf("！"), head.lastIndexOf("？"), head.lastIndexOf(". "));
    spoken = `${end > maxChars / 3 ? head.slice(0, end + 1) : head}続きは画面をご覧ください。`;
  }
  return spoken.trim();
}

/** 合成の単位に分ける。最初は短く（早く鳴らし始める）、後は長めに（つなぎ目を減らす）。 */
export function speechChunks(text: string, first = 80, rest = 300): string[] {
  const sentences = text.match(/[^。！？!?]+[。！？!?]*|[。！？!?]+/g) ?? [];
  const chunks: string[] = [];
  let current = "";
  for (const sentence of sentences) {
    const limit = chunks.length === 0 ? first : rest;
    if (current && current.length + sentence.length > limit) {
      chunks.push(current);
      current = "";
    }
    current += sentence;
  }
  if (current.trim()) chunks.push(current);
  return chunks.map((chunk) => chunk.trim()).filter(Boolean);
}

/** 日本語の文字があれば ja、無ければ en（混ざっていれば ja で読む） */
export function speechLang(text: string): "ja" | "en" {
  return /[぀-ヿ㐀-鿿]/.test(text) ? "ja" : "en";
}

/** その行が、書式の途中で終わっていないか（太字・斜体・コード・リンクが閉じている、URL の途中でない）。
 *  2026-11-20 利用者「多少文言を読み飛ばす」: 「**結論！** これは」を「！」で切ると、閉じていない ** のせいで
 *  切った所の前後で読む文がずれ、次の文の頭（「こ」）を飛ばし、記号を読んでいた。リンク・コード・URL の中の「？」も同じ。 */
function lineIsClosed(line: string): boolean {
  const body = line.replace(/^\s*(?:[-*+]|\d+[.)])\s+/, "");    // 箇条書きの印
  const count = (text: string, token: string) => text.split(token).length - 1;
  if (count(body, "`") % 2) return false;
  const noCode = body.replace(/`[^`]*`/g, "");
  if (count(noCode, "**") % 2 || count(noCode, "__") % 2 || count(noCode, "~~") % 2) return false;
  const singles = noCode.replace(/\*\*|__|~~/g, "");
  if (count(singles, "*") % 2) return false;
  if (count(noCode, "[") !== count(noCode, "]") || count(noCode, "(") < count(noCode, ")")) return false;
  if (/\]\([^)]*$/.test(noCode)) return false;                  // リンクの URL の途中
  const lastToken = noCode.split(/[\s　]/).pop() ?? "";
  return !/(?:https?:\/\/|www\.)/.test(lastToken);              // むき出しの URL の途中
}

const STREAM_CUTS = new Set(["。", "！", "？", "!", "?", "\n"]);

/** 生成中の Markdown を、読み上げに回してよい所（書式の閉じた文の終わり・改行）までで切る */
export function stablePrefix(markdown: string): string {
  for (let i = markdown.length - 1; i >= 0; i--) {
    const mark = markdown[i];
    if (!STREAM_CUTS.has(mark)) continue;
    if (mark === "\n") return markdown.slice(0, i + 1);
    const lineStart = markdown.lastIndexOf("\n", i) + 1;
    if (lineIsClosed(markdown.slice(lineStart, i + 1))) return markdown.slice(0, i + 1);
  }
  return "";
}

/** 読み上げる文のうち、まだ回していない続き。queued はこれまでに回した文そのもの。
 *  生成中は文の終わりまで。上限を超えたら「続きは画面」で終える。
 *  回した文が今の文の頭と食い違ったら（書式の解釈が変わった等）、食い違った所から読み直す（飛ばさない）。 */
export function nextSpoken(markdown: string, queued: string, done: boolean): { text: string; queued: string; capped: boolean } {
  const spoken = speakableText(done ? markdown : stablePrefix(markdown), Number.POSITIVE_INFINITY);
  let start = queued.length;
  if (!spoken.startsWith(queued)) {
    start = 0;
    while (start < queued.length && start < spoken.length && spoken[start] === queued[start]) start++;
  }
  let rest = spoken.slice(start);
  if (!done) {
    const end = Math.max(...["。", "！", "？", "!", "?", ".", "」", "』"].map((mark) => rest.lastIndexOf(mark)));
    rest = end < 0 ? "" : rest.slice(0, end + 1);
  }
  const next = spoken.slice(0, start) + rest;
  if (next.length > MAX_SPOKEN_CHARS) {
    const room = rest.slice(0, Math.max(0, MAX_SPOKEN_CHARS - start));
    const end = Math.max(room.lastIndexOf("。"), room.lastIndexOf("！"), room.lastIndexOf("？"), room.lastIndexOf(". "));
    // 文の途中では切らない（2026-11-20 実測: 「ため息を続きは画面…」）。文の終わりが無ければ、ここまでで終える
    const head = end >= 0 ? room.slice(0, end + 1) : "";
    return { text: `${head}続きは画面をご覧ください。`, queued: next, capped: true };
  }
  return { text: rest, queued: next, capped: false };
}

/** 無音とみなす振幅。Supertonic 3 の前後の無音はちょうど 0 なので、0 でない音は削らない
 *  （1% で削ると、小さく消えていく語尾「す」「た」を最大 0.08 秒削っていた。2026-11-20 実測） */
const SILENCE = 0.0005;

/** 前後の無音を削った範囲（サンプル位置）。全部が無音なら空 */
export function voicedRange(samples: Float32Array, rate: number): [number, number] {
  let start = 0;
  while (start < samples.length && Math.abs(samples[start]) < SILENCE) start++;
  let end = samples.length;
  while (end > start && Math.abs(samples[end - 1]) < SILENCE) end--;
  const pad = Math.round(rate * 0.04);   // 子音の立ち上がり・余韻を切らない
  return start >= end ? [0, 0] : [Math.max(0, start - pad), Math.min(samples.length, end + pad)];
}
