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
