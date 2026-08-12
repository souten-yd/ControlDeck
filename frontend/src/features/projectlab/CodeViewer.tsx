/** 依存なしの軽量シンタックスハイライト表示。行番号付きで内部スクロールする。 */
import { useMemo } from "react";

const MAX_LINES = 4000;

const C_LIKE = "//[^\\n]*|/\\*[\\s\\S]*?\\*/";
const HASH = "#[^\\n]*";
const STRING = "\"(?:\\\\.|[^\"\\\\])*\"|'(?:\\\\.|[^'\\\\])*'|`(?:\\\\.|[^`\\\\])*`";
const NUMBER = "\\b\\d[\\d_]*(?:\\.\\d+)?\\b";

const KEYWORDS: Record<string, string> = {
  python: "def|class|return|import|from|as|if|elif|else|for|while|try|except|finally|with|lambda|yield|async|await|pass|break|continue|raise|global|nonlocal|assert|del|in|is|not|and|or|None|True|False|self",
  javascript: "const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|class|extends|new|this|typeof|instanceof|import|export|from|default|async|await|try|catch|finally|throw|delete|in|of|null|undefined|true|false",
  typescript: "const|let|var|function|return|if|else|for|while|do|switch|case|break|continue|class|extends|implements|interface|type|enum|new|this|typeof|instanceof|import|export|from|default|async|await|try|catch|finally|throw|public|private|readonly|as|null|undefined|true|false",
  css: "important|media|import|keyframes|supports|charset|font-face|root",
  shell: "if|then|else|elif|fi|for|while|do|done|case|esac|function|return|export|local|source|echo|cd|set",
  yaml: "true|false|null|yes|no|on|off",
  toml: "true|false",
  ini: "true|false",
  sql: "select|from|where|insert|update|delete|join|left|right|inner|outer|on|group|order|by|limit|create|table|drop|alter|values|into|and|or|not|null",
  rust: "fn|let|mut|const|struct|enum|impl|trait|pub|use|mod|match|if|else|for|while|loop|return|self|Some|None|Ok|Err|async|await",
  go: "func|package|import|var|const|type|struct|interface|if|else|for|range|return|go|defer|chan|map|nil|true|false",
  java: "public|private|protected|class|interface|extends|implements|static|final|void|new|return|if|else|for|while|try|catch|finally|throw|throws|import|package|null|true|false",
  csharp: "public|private|protected|class|interface|struct|static|readonly|void|new|return|if|else|for|foreach|while|try|catch|finally|throw|using|namespace|var|null|true|false|async|await",
  ruby: "def|end|class|module|if|elsif|else|unless|while|until|do|return|yield|require|attr_accessor|nil|true|false|self",
  php: "function|class|return|if|else|elseif|foreach|for|while|echo|require|include|use|namespace|public|private|protected|static|null|true|false",
  c: "int|char|float|double|void|struct|enum|union|return|if|else|for|while|switch|case|break|continue|static|const|sizeof|include|define|typedef|NULL",
  cpp: "int|char|float|double|bool|void|class|struct|enum|namespace|template|typename|return|if|else|for|while|switch|case|break|continue|static|const|constexpr|auto|new|delete|nullptr|true|false|public|private|protected",
  kotlin: "fun|val|var|class|object|interface|return|if|else|when|for|while|import|package|null|true|false|suspend",
  swift: "func|let|var|class|struct|enum|protocol|return|if|else|for|while|guard|import|nil|true|false",
  vue: "const|let|function|return|if|else|import|export|from|default",
  svelte: "const|let|function|return|if|else|import|export|from|default",
  json: "true|false|null",
  xml: "",
};

const COMMENTS: Record<string, string> = {
  python: HASH, shell: HASH, yaml: HASH, toml: HASH, ini: `${HASH}|;[^\\n]*`,
  sql: "--[^\\n]*", ruby: HASH, xml: "<!--[\\s\\S]*?-->", html: "<!--[\\s\\S]*?-->",
};

const CLASSES: Record<string, string> = {
  comment: "text-zinc-400 italic dark:text-zinc-500",
  string: "text-emerald-600 dark:text-emerald-400",
  number: "text-amber-600 dark:text-amber-400",
  keyword: "text-violet-600 dark:text-violet-400",
  tag: "text-sky-600 dark:text-sky-400",
};

interface Token {
  text: string;
  className: string;
}

function buildPattern(language: string): RegExp | null {
  const comment = COMMENTS[language] ?? C_LIKE;
  const keywords = KEYWORDS[language];
  if (keywords === undefined) return null;
  const parts = [
    `(?<comment>${comment})`,
    `(?<string>${STRING})`,
    keywords ? `(?<keyword>\\b(?:${keywords})\\b)` : "",
    `(?<number>${NUMBER})`,
  ].filter(Boolean);
  return new RegExp(parts.join("|"), "g");
}

/** 全文を1度だけtoken化し、行単位へ切り直す（複数行にまたがるcomment/文字列も正しく着色）。 */
function tokenizeLines(text: string, language: string): Token[][] {
  const pattern = buildPattern(language);
  const tokens: Token[] = [];
  if (!pattern) {
    tokens.push({ text, className: "" });
  } else {
    let index = 0;
    for (const match of text.matchAll(pattern)) {
      const start = match.index ?? 0;
      if (start > index) tokens.push({ text: text.slice(index, start), className: "" });
      const groups = match.groups ?? {};
      const name = Object.keys(CLASSES).find((key) => groups[key] !== undefined) ?? "";
      tokens.push({ text: match[0], className: CLASSES[name] ?? "" });
      index = start + match[0].length;
    }
    if (index < text.length) tokens.push({ text: text.slice(index), className: "" });
  }
  const lines: Token[][] = [[]];
  for (const token of tokens) {
    const pieces = token.text.split("\n");
    pieces.forEach((piece, order) => {
      if (order > 0) lines.push([]);
      if (piece) lines[lines.length - 1].push({ text: piece, className: token.className });
    });
  }
  return lines;
}

export function CodeViewer({ text, language, wrap = false }: { text: string; language: string; wrap?: boolean }) {
  const lines = useMemo(() => tokenizeLines(text, language || "text"), [text, language]);
  const visible = lines.slice(0, MAX_LINES);
  return (
    <div className="h-full overflow-auto overscroll-contain bg-white font-mono text-[12px] leading-[1.65] dark:bg-zinc-950">
      <div className="min-w-full pb-24">
        {visible.map((tokens, index) => (
          <div key={index} className="flex">
            <span
              aria-hidden
              className="num sticky left-0 w-11 shrink-0 select-none border-r border-zinc-100 bg-white px-2 text-right text-[10px] text-zinc-300 dark:border-zinc-900 dark:bg-zinc-950 dark:text-zinc-700"
            >
              {index + 1}
            </span>
            <code className={`flex-1 px-3 text-zinc-800 dark:text-zinc-200 ${wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"}`}>
              {tokens.length === 0 ? " " : tokens.map((token, order) => (
                <span key={order} className={token.className}>{token.text}</span>
              ))}
            </code>
          </div>
        ))}
        {lines.length > MAX_LINES && (
          <p className="px-4 py-3 text-[11px] text-amber-600 dark:text-amber-400">
            先頭 {MAX_LINES} 行だけ表示しています。全文は保存して確認してください。
          </p>
        )}
      </div>
    </div>
  );
}
