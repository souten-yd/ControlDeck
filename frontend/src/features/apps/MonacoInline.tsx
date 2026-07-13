/** Monaco をインラインで埋め込む軽量ラッパー（アプリのコード編集用）。 */
import { useEffect, useRef } from "react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

(self as unknown as { MonacoEnvironment: unknown }).MonacoEnvironment = {
  getWorker: () => new EditorWorker(),
};

export default function MonacoInline({
  value,
  language,
  onChange,
}: {
  value: string;
  language: string;
  onChange: (v: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const editor = monaco.editor.create(host.current, {
      value,
      language,
      theme: document.documentElement.classList.contains("dark") ? "vs-dark" : "vs",
      automaticLayout: true,
      minimap: { enabled: false },
      fontSize: 13,
      scrollBeyondLastLine: false,
      lineNumbers: "on",
      tabSize: 2,
    });
    editor.onDidChangeModelContent(() => onChange(editor.getValue()));
    editorRef.current = editor;
    return () => editor.dispose();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={host} className="h-64" />;
}
