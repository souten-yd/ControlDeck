/** Monaco ベースのテキストエディター（遅延ロードチャンク、CDN 不使用）。 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";
import JsonWorker from "monaco-editor/esm/vs/language/json/json.worker?worker";
import TsWorker from "monaco-editor/esm/vs/language/typescript/ts.worker?worker";
import { api } from "../../api/client";
import { useToasts } from "../../stores";
import { IconX } from "../../components/icons";

(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
  getWorker: (_id: unknown, label: string) => {
    if (label === "json") return new JsonWorker();
    if (label === "typescript" || label === "javascript") return new TsWorker();
    return new EditorWorker();
  },
};

const LANG_BY_EXT: Record<string, string> = {
  py: "python", sh: "shell", bash: "shell", yml: "yaml", yaml: "yaml",
  md: "markdown", json: "json", js: "javascript", ts: "typescript",
  tsx: "typescript", jsx: "javascript", ini: "ini", cfg: "ini", conf: "ini",
  service: "ini", toml: "ini", xml: "xml", html: "xml", sql: "sql",
};

export default function TextEditor({
  path,
  onClose,
  readOnly,
}: {
  path: string;
  onClose: () => void;
  readOnly: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const show = useToasts((s) => s.show);
  const name = path.split("/").pop() ?? path;

  useEffect(() => {
    let disposed = false;
    api<{ content: string }>(`/files/text?path=${encodeURIComponent(path)}`)
      .then(({ content }) => {
        if (disposed || !containerRef.current) return;
        const ext = name.split(".").pop()?.toLowerCase() ?? "";
        const editor = monaco.editor.create(containerRef.current, {
          value: content,
          language: LANG_BY_EXT[ext] ?? "plaintext",
          theme: document.documentElement.classList.contains("dark") ? "vs-dark" : "vs",
          readOnly,
          automaticLayout: true,
          minimap: { enabled: false },
          fontSize: 13,
          scrollBeyondLastLine: false,
          wordWrap: "on",
        });
        editor.onDidChangeModelContent(() => setDirty(true));
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => save());
        editorRef.current = editor;
        setLoading(false);
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : "読み込みに失敗しました");
        setLoading(false);
      });
    return () => {
      disposed = true;
      editorRef.current?.dispose();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  const save = async () => {
    const editor = editorRef.current;
    if (!editor || readOnly) return;
    setSaving(true);
    try {
      await api("/files/text", {
        method: "PUT",
        json: { path, content: editor.getValue() },
      });
      setDirty(false);
      show("保存しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "保存に失敗しました", "error");
    } finally {
      setSaving(false);
    }
  };

  const close = () => {
    if (dirty && !confirm("未保存の変更があります。閉じますか？")) return;
    onClose();
  };

  return createPortal(
    <div className="fixed inset-0 z-50 flex flex-col bg-white dark:bg-zinc-950">
      <div className="safe-top flex shrink-0 items-center justify-between border-b border-zinc-200 px-4 py-2 dark:border-zinc-800">
        <p className="min-w-0 truncate text-sm font-medium">
          {name}
          {dirty && <span className="ml-1.5 text-accent-500">●</span>}
        </p>
        <div className="flex items-center gap-2">
          {!readOnly && (
            <button
              onClick={save}
              disabled={saving || !dirty}
              className="rounded-xl bg-accent-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40"
            >
              {saving ? "保存中..." : "保存"}
            </button>
          )}
          <button onClick={close} aria-label="閉じる" className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800">
            <IconX />
          </button>
        </div>
      </div>
      {loading && <div className="grid flex-1 place-items-center text-sm text-zinc-400">読み込み中...</div>}
      {error && <div className="grid flex-1 place-items-center text-sm text-red-500">{error}</div>}
      <div ref={containerRef} className={`min-h-0 flex-1 ${loading || error ? "hidden" : ""}`} />
    </div>,
    document.body,
  );
}
