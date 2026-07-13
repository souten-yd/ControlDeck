/** Web スクレイピングの抽出ビューワ + 抽出項目エディタ。
 * ページをサンドボックス iframe に描画し、要素をクリックで CSS セレクタを自動生成。
 * 抽出ワード（セレクタ）と抽出結果を対比しながら確認でき、複数の出力を選べる。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { IconTrash, IconX } from "../../components/icons";
import type { ExtractorDef } from "./nodeTypes";

const ATTR_OPTIONS = [
  { value: "text", label: "テキスト" },
  { value: "html", label: "内部HTML" },
  { value: "href", label: "リンク(href)" },
  { value: "src", label: "画像(src)" },
  { value: "title", label: "title属性" },
  { value: "value", label: "value属性" },
];

// iframe 内に注入して、ホバーでハイライト・クリックでセレクタ生成 → 親へ postMessage
const PICKER_SCRIPT = `
<style>
  .cd-hi { outline: 2px solid #f59e0b !important; outline-offset: -1px; cursor: pointer !important; background: rgba(245,158,11,.12) !important; }
  .cd-sel { outline: 2px solid #10b981 !important; outline-offset: -1px; background: rgba(16,185,129,.14) !important; }
</style>
<script>
(function () {
  function cssPath(el) {
    if (!el || el.nodeType !== 1) return "";
    if (el.id && /^[A-Za-z_][\\w-]*$/.test(el.id)) return "#" + el.id;
    var parts = [], depth = 0;
    while (el && el.nodeType === 1 && el.tagName.toLowerCase() !== "html" && depth < 4) {
      var seg = el.tagName.toLowerCase();
      var cls = (el.className && typeof el.className === "string")
        ? el.className.trim().split(/\\s+/).filter(function (c) { return /^[A-Za-z_][\\w-]*$/.test(c); }) : [];
      if (el.id && /^[A-Za-z_][\\w-]*$/.test(el.id)) { parts.unshift("#" + el.id); break; }
      if (cls.length) { seg += "." + cls.slice(0, 2).join("."); }
      else {
        var p = el.parentNode;
        if (p) {
          var same = Array.prototype.filter.call(p.children, function (c) { return c.tagName === el.tagName; });
          if (same.length > 1) seg += ":nth-of-type(" + (same.indexOf(el) + 1) + ")";
        }
      }
      parts.unshift(seg);
      el = el.parentNode;
      depth++;
    }
    return parts.join(" > ");
  }
  var last = null;
  document.addEventListener("mouseover", function (e) {
    if (last) last.classList.remove("cd-hi");
    last = e.target; if (last && last.classList) last.classList.add("cd-hi");
  }, true);
  document.addEventListener("mouseout", function () { if (last) last.classList.remove("cd-hi"); }, true);
  document.addEventListener("click", function (e) {
    e.preventDefault(); e.stopPropagation();
    var sel = cssPath(e.target);
    var text = (e.target.textContent || "").trim().slice(0, 120);
    parent.postMessage({ __cdscrape: true, selector: sel, sample: text }, "*");
  }, true);
})();
</script>
`;

interface Candidate {
  selector: string;
  kind: string;
  count: number;
  sample: string;
}

export function ScrapeViewer({
  url,
  extractors,
  onChange,
  onClose,
}: {
  url: string;
  extractors: ExtractorDef[];
  onChange: (v: ExtractorDef[]) => void;
  onClose: () => void;
}) {
  const show = useToasts((s) => s.show);
  const [urlInput, setUrlInput] = useState(url);
  const [loading, setLoading] = useState(false);
  const [viewerHtml, setViewerHtml] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [preview, setPreview] = useState<Record<string, { ok: boolean; count: number; results: string[]; error?: string }>>({});
  const [pending, setPending] = useState<{ selector: string; sample: string } | null>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  // iframe からのクリック結果を受け取る
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.data && e.data.__cdscrape) {
        setPending({ selector: e.data.selector, sample: e.data.sample });
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const analyze = async () => {
    if (!urlInput.trim()) return;
    setLoading(true);
    setViewerHtml(null);
    try {
      const r = await api<{ candidates: Candidate[]; viewer_html: string }>("/workflows/scrape/analyze", {
        method: "POST",
        json: { url: urlInput.trim() },
      });
      setCandidates(r.candidates);
      // ピッカースクリプトを注入して表示
      setViewerHtml(r.viewer_html + PICKER_SCRIPT);
    } catch (e) {
      show(e instanceof Error ? e.message : "解析に失敗しました", "error");
    } finally {
      setLoading(false);
    }
  };

  const runPreview = async (list: ExtractorDef[]) => {
    const valid = list.filter((x) => x.selector.trim());
    if (!urlInput.trim() || valid.length === 0) {
      setPreview({});
      return;
    }
    try {
      const r = await api<{ results: typeof preview }>("/workflows/scrape/preview", {
        method: "POST",
        json: { url: urlInput.trim(), extractors: valid },
      });
      setPreview(r.results);
    } catch {
      /* プレビュー失敗は無視 */
    }
  };

  const addExtractor = (selector: string) => {
    const base = selector.split(/[.#>: ]/)[0] || "field";
    const name = uniqueName(base, extractors);
    const next = [...extractors, { name, selector, attribute: "text", multiple: false }];
    onChange(next);
    setPending(null);
    void runPreview(next);
    show(`「${name}」を追加しました`);
  };

  const update = (i: number, patch: Partial<ExtractorDef>) => {
    const next = extractors.map((x, j) => (j === i ? { ...x, ...patch } : x));
    onChange(next);
    void runPreview(next);
  };
  const remove = (i: number) => {
    const next = extractors.filter((_, j) => j !== i);
    onChange(next);
    void runPreview(next);
  };

  return createPortal(
    <div className="fixed inset-0 z-[70] flex flex-col bg-white dark:bg-zinc-950">
      {/* ヘッダー */}
      <div className="safe-top flex shrink-0 items-center gap-2 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
        <span className="text-sm font-semibold">抽出ビューワ</span>
        <input
          value={urlInput}
          onChange={(e) => setUrlInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && analyze()}
          placeholder="https://example.com（{{変数}} も可）"
          className="min-w-0 flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-1.5 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
        />
        <button onClick={analyze} disabled={loading} className="shrink-0 rounded-lg bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
          {loading ? "解析中..." : "解析"}
        </button>
        <button onClick={onClose} aria-label="閉じる" className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
          <IconX />
        </button>
      </div>

      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        {/* 左: ページビューワ（クリックで選択） */}
        <div className="relative min-h-0 flex-1 border-b border-zinc-200 md:border-b-0 md:border-r dark:border-zinc-800">
          {viewerHtml ? (
            <iframe
              ref={iframeRef}
              title="ページプレビュー"
              sandbox="allow-same-origin"
              srcDoc={viewerHtml}
              className="h-full w-full bg-white"
            />
          ) : (
            <div className="grid h-full place-items-center p-6 text-center text-sm text-zinc-400">
              URL を入力して「解析」を押すと、ここにページが表示されます。<br />
              要素をクリックすると抽出セレクタが自動生成されます。
            </div>
          )}
          {pending && (
            <div className="absolute inset-x-3 bottom-3 rounded-xl border border-accent-300 bg-white p-3 shadow-lg dark:border-accent-700 dark:bg-zinc-900">
              <p className="truncate font-mono text-xs text-zinc-500">{pending.selector}</p>
              <p className="mt-0.5 truncate text-xs text-zinc-400">「{pending.sample}」</p>
              <div className="mt-2 flex gap-2">
                <button onClick={() => addExtractor(pending.selector)} className="rounded-lg bg-accent-600 px-3 py-1.5 text-xs font-medium text-white">
                  この要素を抽出に追加
                </button>
                <button onClick={() => setPending(null)} className="rounded-lg bg-zinc-100 px-3 py-1.5 text-xs dark:bg-zinc-800">
                  取消
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 右: 候補 + 抽出項目 + 対比プレビュー */}
        <div className="min-h-0 w-full shrink-0 overflow-y-auto p-3 md:w-96">
          {candidates.length > 0 && (
            <details className="mb-3">
              <summary className="cursor-pointer text-xs font-medium text-zinc-500">候補セレクタ（{candidates.length}）</summary>
              <div className="mt-1.5 space-y-1">
                {candidates.map((c) => (
                  <button
                    key={c.selector}
                    onClick={() => addExtractor(c.selector)}
                    className="flex w-full items-center gap-2 rounded-lg border border-zinc-200 px-2 py-1.5 text-left hover:border-accent-400 dark:border-zinc-700"
                  >
                    <span className="shrink-0 rounded bg-zinc-100 px-1 text-[10px] text-zinc-500 dark:bg-zinc-800">{c.count}</span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[11px]">{c.selector}</span>
                      <span className="block truncate text-[10px] text-zinc-400">{c.sample}</span>
                    </span>
                  </button>
                ))}
              </div>
            </details>
          )}

          <p className="mb-1.5 text-xs font-medium text-zinc-500">抽出項目（出力変数）</p>
          <div className="space-y-2">
            {extractors.map((ex, i) => {
              const pv = preview[ex.name || ex.selector];
              return (
                <div key={i} className="rounded-xl border border-zinc-200 p-2.5 dark:border-zinc-700">
                  <div className="flex items-center gap-1.5">
                    <input
                      value={ex.name}
                      onChange={(e) => update(i, { name: e.target.value.replace(/[^\w-]/g, "") })}
                      placeholder="出力名"
                      className="w-24 rounded-lg border border-zinc-300 bg-white px-2 py-1 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-900"
                    />
                    <select value={ex.attribute} onChange={(e) => update(i, { attribute: e.target.value })} className="rounded-lg border border-zinc-300 bg-white px-1.5 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-900">
                      {ATTR_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    <label className="flex items-center gap-1 text-[11px] text-zinc-500">
                      <input type="checkbox" checked={ex.multiple} onChange={(e) => update(i, { multiple: e.target.checked })} />複数
                    </label>
                    <button onClick={() => remove(i)} aria-label="削除" className="ml-auto p-1 text-zinc-400 hover:text-red-500">
                      <IconTrash />
                    </button>
                  </div>
                  <input
                    value={ex.selector}
                    onChange={(e) => update(i, { selector: e.target.value })}
                    placeholder="CSS セレクタ"
                    className="mt-1.5 w-full rounded-lg border border-zinc-300 bg-white px-2 py-1 font-mono text-[11px] dark:border-zinc-700 dark:bg-zinc-900"
                  />
                  {/* 抽出ワード ↔ 結果の対比 */}
                  {pv && (
                    <div className="mt-1.5 rounded-lg bg-zinc-50 p-2 dark:bg-zinc-800/60">
                      {pv.ok ? (
                        <>
                          <p className="mb-1 text-[10px] text-zinc-400">一致 {pv.count} 件</p>
                          {pv.results.length === 0 ? (
                            <p className="text-[11px] text-amber-600">一致なし</p>
                          ) : (
                            <ul className="space-y-0.5">
                              {pv.results.slice(0, 5).map((r, k) => (
                                <li key={k} className="truncate text-[11px] text-zinc-600 dark:text-zinc-300">・{r || "(空)"}</li>
                              ))}
                            </ul>
                          )}
                        </>
                      ) : (
                        <p className="text-[11px] text-red-500">{pv.error}</p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            <button
              onClick={() => addExtractor("")}
              className="w-full rounded-xl border border-dashed border-zinc-300 py-2 text-xs font-medium text-zinc-500 hover:border-accent-400 hover:text-accent-600 dark:border-zinc-700"
            >
              + 手動で抽出項目を追加
            </button>
          </div>

          {extractors.length > 0 && (
            <button onClick={() => runPreview(extractors)} className="mt-3 w-full rounded-xl bg-zinc-100 py-2 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300">
              抽出結果を再取得
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function uniqueName(base: string, existing: ExtractorDef[]): string {
  const names = new Set(existing.map((e) => e.name));
  if (!names.has(base)) return base;
  let i = 2;
  while (names.has(`${base}${i}`)) i++;
  return `${base}${i}`;
}
