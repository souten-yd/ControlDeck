/** アシスタントの回答を Markdown（表・見出し・リスト・コード）と TeX の式で描く。
 *
 * AssistantChat から遅延読み込みする（ライブラリと KaTeX の CSS はアシスタント画面でだけ読む）。
 * 生の HTML は描かない（react-markdown の既定。回答に紛れた <script> などは文字のまま）。 */
import { Children, isValidElement, memo, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { IconCheck, IconCopy } from "../../components/icons";
import { writeClipboard } from "../../lib/clipboard";
import { RunCommandContext } from "./RunCommandSheet";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

/** LLM がよく使う \( … \) と \[ … \] を、remark-math の $ … $ / $$ … $$ に直す。
 * - コード（``` / ~~~ のブロック、`…` と ``…`` のインライン）の中は触らない。
 * - 行の頭から始まる \[ … \] だけ別行の式にする。行の途中（表のセル・リスト）は改行を入れず
 *   その場の式にする（改行を入れると表やリストが壊れる）。
 * - 金額の $（「$5 と $10」）は式にしない: 数字の直前の $ を文字の $ にする。 */
export function normalizeMath(text: string): string {
  return text
    .split(/(```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|``[^`\n]+``|`[^`\n]*`)/g)
    .map((part, i) => {
      if (i % 2 === 1) return part; // コード
      return part
        .replace(/(^|\n)([ \t]*)\\\[([\s\S]+?)\\\]/g,
          (_m, head: string, indent: string, body: string) => `${head}${indent}$$\n${body.trim()}\n$$`)
        .replace(/\\\[([^\n]+?)\\\]/g, (_m, body: string) => `$${body.trim()}$`)
        .replace(/\\\(([\s\S]+?)\\\)/g, (_m, body: string) => `$${body.trim()}$`)
        .replace(/(?<![$\\])\$(?=\d)/g, "\\$");
    })
    .join("");
}

/** React の子から文字だけを集める（コードブロックの中身全体。言語の印の ``` 行は含まない）。 */
function nodeText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return Children.toArray(node.props.children).map(nodeText).join("");
  return "";
}

/** ``` のコードブロック。上に細い帯を置き、右端にコピーボタン（見た目は小さく、押せる範囲は 44px）。
 * ボタンは横にずらす pre の外にあるので、長いコードを横へ動かしても右上に留まり、文字にも重ならない。
 * 2026-11-27: bash などのシェルの言語（と言語なしでコマンドに見えるもの）は「▶ 実行」も出す。
 * RunCommandContext の provider がある所（AI アシスタントの会話・管理者）だけ。 */
function CodeBlock({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  const onRun = useContext(RunCommandContext);
  const text = nodeText(children).replace(/\n$/, "");
  const language = blockLanguage(children);
  const command = shellCommandOf(text);
  const runnable = Boolean(onRun) && command.trim() !== ""
    && (SHELL_LANGUAGES.has(language) || (!language && looksLikeShellCommands(text)));
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const copy = async () => {
    // 末尾の改行 1 つは markdown の仕組みで付くだけなので落とす。
    const ok = await writeClipboard(text);
    setState(ok ? "done" : "failed");
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setState("idle"), 1800);
  };
  return (
    <div className="my-2 max-w-full overflow-hidden rounded-lg bg-zinc-900 text-zinc-100 dark:bg-black/40">
      <div className="flex h-7 items-center justify-end gap-0.5 px-1.5">
        <span role="status" aria-live="polite" className={`mr-1 text-[11px] ${state === "failed" ? "text-red-300" : "text-emerald-300"}`}>
          {state === "done" ? "コピーしました" : state === "failed" ? "コピーできませんでした" : ""}
        </span>
        {runnable && (
          <button
            type="button"
            aria-label="ターミナルで実行"
            data-testid="code-run"
            onClick={() => onRun?.({ command, language })}
            title="このコマンドを、確認の後に ControlDeck の Terminal で実行します"
            className="relative flex h-7 items-center gap-1 rounded px-2 text-[11px] text-zinc-300 hover:bg-white/10 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-400 before:absolute before:-inset-x-1 before:-inset-y-2 before:content-['']"
          >
            ▶ 実行
          </button>
        )}
        <button
          type="button"
          aria-label="コードをコピー"
          onClick={() => void copy()}
          className="relative flex h-7 w-8 items-center justify-center rounded text-zinc-300 hover:bg-white/10 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-400 before:absolute before:-inset-x-1 before:-inset-y-2 before:content-['']"
        >
          {state === "done" ? <IconCheck className="h-[15px] w-[15px]" /> : <IconCopy className="h-[15px] w-[15px]" />}
        </button>
      </div>
      {/* ブロックの中の code にはインラインコードの見た目を当てない（言語指定なしの ``` が読めなくなる）。 */}
      <pre className="max-w-full overflow-x-auto px-2.5 pb-2.5 text-[12px] leading-snug [&>code]:rounded-none [&>code]:bg-transparent [&>code]:p-0 [&>code]:text-inherit">{children}</pre>
    </div>
  );
}

/** 実行できる言語の印（```bash など） */
const SHELL_LANGUAGES = new Set(["bash", "sh", "shell", "console", "zsh"]);

/** pre の子（code 要素）の className `language-…` から言語の印を取る */
function blockLanguage(children: ReactNode): string {
  for (const child of Children.toArray(children)) {
    if (isValidElement<{ className?: string }>(child)) {
      const match = /language-([\w#+-]+)/.exec(child.props.className ?? "");
      if (match) return match[1].toLowerCase();
    }
  }
  return "";
}

/** 端末へ流す本文: 行頭のプロンプトの印（$・% に続き空白）は外す。`$FOO` のような変数や
 *  ヒアドキュメントの行は空白を要求することでそのまま残る。# はコメントとしてそのまま（端末で無害） */
function shellCommandOf(text: string): string {
  return text.split("\n").map((line) => line.replace(/^[$%][ \t]/, "")).join("\n").trim();
}

/** 言語なしのブロックをコマンドと見なすか。保守的に: 1〜5 行で、どの行も（$ の印を除いて）
 *  空白で区切られた語の並びに見えるもの。コードに見える記号（{ } ( ) = ; ` など）があれば出さない。
 *  誤って出すより見落とし（コピーはいつでもできる） */
function looksLikeShellCommands(text: string): boolean {
  const lines = text.split("\n").map((line) => line.replace(/\r$/, "")).filter((line) => line.trim() !== "");
  if (lines.length === 0 || lines.length > 5) return false;
  return lines.every((line) => {
    const body = /^[$%]\s+/.test(line) ? line.replace(/^[$%]\s+/, "") : line.trim();
    if (!body || body.length > 400) return false;
    if (/[{}();=`]|->|=>/.test(body)) return false;
    return body.split(/\s+/).every((word) => /^[\w.,:@/~"'%^\-]+$/.test(word));
  });
}

const components: Components = {
  h1: ({ children }) => <h3 className="mb-1 mt-3 text-base font-bold first:mt-0">{children}</h3>,
  h2: ({ children }) => <h3 className="mb-1 mt-3 text-[15px] font-bold first:mt-0">{children}</h3>,
  h3: ({ children }) => <h4 className="mb-1 mt-2.5 text-sm font-bold first:mt-0">{children}</h4>,
  h4: ({ children }) => <h5 className="mb-1 mt-2 text-sm font-semibold first:mt-0">{children}</h5>,
  p: ({ children }) => <p className="my-1.5 leading-relaxed first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 list-disc space-y-0.5 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal space-y-0.5 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="break-all text-accent-600 underline dark:text-accent-400">{children}</a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-2 border-l-2 border-zinc-300 pl-3 text-zinc-600 dark:border-zinc-600 dark:text-zinc-300">{children}</blockquote>
  ),
  hr: () => <hr className="my-3 border-zinc-200 dark:border-zinc-700" />,
  // 表は幅いっぱいに置き、セルの中で折り返す（携帯でも 2〜3 列なら横にずらさず読める）。
  // 列が多くて収まらないときだけ、表の中を横にずらす。画面全体は横に広げない。
  table: ({ children }) => (
    <div className="my-2 max-w-full overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
      <table className="min-w-full border-collapse text-[13px] leading-snug">{children}</table>
    </div>
  ),
  thead: ({ children }) => <thead className="bg-zinc-100 dark:bg-zinc-800">{children}</thead>,
  th: ({ children, style }) => (
    <th style={style} className="min-w-[5rem] border-b border-zinc-200 px-2 py-1.5 text-left font-semibold dark:border-zinc-700">{children}</th>
  ),
  td: ({ children, style }) => (
    <td style={style} className="min-w-[5rem] border-b border-zinc-100 px-2 py-1.5 align-top dark:border-zinc-800">{children}</td>
  ),
  pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
  code: ({ className, children }) => {
    // ``` のブロックは pre の中（言語の className が付く）。それ以外はインライン。
    if (className) return <code className={`${className} font-mono`}>{children}</code>;
    return <code className="rounded bg-zinc-200/70 px-1 py-px font-mono text-[0.9em] dark:bg-zinc-700/70">{children}</code>;
  },
};

function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="min-w-0 break-words text-sm [overflow-wrap:anywhere] [&_.katex-display]:my-2 [&_.katex-display]:overflow-x-auto [&_.katex-display]:overflow-y-hidden [&_.katex-display]:py-1">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, [remarkMath, { singleDollarTextMath: true }]]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: "ignore" }]]}
        components={components}
      >
        {normalizeMath(text)}
      </ReactMarkdown>
    </div>
  );
}

// 本文が変わらなければ描き直さない（生成中に過去の発言の式まで作り直さない）。
export default memo(MarkdownMessage);
