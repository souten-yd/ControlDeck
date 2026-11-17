/** アシスタントの回答を Markdown（表・見出し・リスト・コード）と TeX の式で描く。
 *
 * AssistantChat から遅延読み込みする（ライブラリと KaTeX の CSS はアシスタント画面でだけ読む）。
 * 生の HTML は描かない（react-markdown の既定。回答に紛れた <script> などは文字のまま）。 */
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";

/** LLM がよく使う \( … \) と \[ … \] を、remark-math の $ … $ / $$ … $$ に直す。
 * コードブロック（``` で囲んだ所）と `インラインのコード` の中は触らない。 */
export function normalizeMath(text: string): string {
  return text
    .split(/(```[\s\S]*?(?:```|$)|`[^`\n]*`)/g)
    .map((part, i) => {
      if (i % 2 === 1) return part; // コード
      return part
        .replace(/\\\[([\s\S]+?)\\\]/g, (_m, body: string) => `\n$$\n${body.trim()}\n$$\n`)
        .replace(/\\\(([\s\S]+?)\\\)/g, (_m, body: string) => `$${body.trim()}$`);
    })
    .join("");
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
  pre: ({ children }) => (
    <pre className="my-2 max-w-full overflow-x-auto rounded-lg bg-zinc-900 p-2.5 text-[12px] leading-snug text-zinc-100 dark:bg-black/40">{children}</pre>
  ),
  code: ({ className, children }) => {
    // ``` のブロックは pre の中（言語の className が付く）。それ以外はインライン。
    if (className) return <code className={`${className} font-mono`}>{children}</code>;
    return <code className="rounded bg-zinc-200/70 px-1 py-px font-mono text-[0.9em] dark:bg-zinc-700/70">{children}</code>;
  },
};

export default function MarkdownMessage({ text }: { text: string }) {
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
