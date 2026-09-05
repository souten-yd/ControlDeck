import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** 画面を切り替えたら復帰させるための鍵。route の pathname を渡す。 */
  resetKey?: string;
}

interface State {
  error: Error | null;
}

const RELOAD_MARK = "control-deck:shell-reloaded-at";

function looksLikeStaleShell(error: Error): boolean {
  const text = `${error.name} ${error.message}`;
  return /Failed to fetch dynamically imported module|Importing a module script failed|ChunkLoadError/i.test(text);
}

/** 1 画面の例外でアプリ全体を白くしない。
 *
 * React は境界が無いと、描画中の例外で木ごと unmount する。設定画面の小さな
 * 不具合で何も見えなくなり、利用者には原因も復帰方法も分からない。ここで受け止めて、
 * 何が起きたかと戻り方を出す。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // 原因を追えるように残す。利用者には下の画面で伝える。
    console.error("[error-boundary]", error, info.componentStack);
    if (!looksLikeStaleShell(error)) return;
    // 古い shell を掴んでいるなら、控えを捨てて一度だけ読み直せば戻る。
    try {
      const last = Number(sessionStorage.getItem(RELOAD_MARK) || 0);
      if (Date.now() - last < 30_000) return;
      sessionStorage.setItem(RELOAD_MARK, String(Date.now()));
    } catch {
      return;
    }
    void (async () => {
      try {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      } catch {
        /* 消せなくても読み直しは試す */
      }
      location.reload();
    })();
  }

  componentDidUpdate(previous: Props): void {
    // 別の画面へ移ったら、その画面は描けるかもしれないので試させる。
    if (this.state.error && previous.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="mx-auto max-w-3xl p-4 md:p-6">
        <div className="rounded-2xl border border-red-200 bg-red-50 p-5 dark:border-red-900 dark:bg-red-950/40">
          <h1 className="text-base font-semibold text-red-800 dark:text-red-300">
            この画面を表示できませんでした
          </h1>
          <p className="mt-2 text-sm text-red-700 dark:text-red-400">
            他の画面は使えます。同じ操作で繰り返し起きるなら、下の内容をお知らせください。
          </p>
          <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-xl bg-white/70 p-3 text-[11px] leading-4 text-red-900 dark:bg-black/30 dark:text-red-300">
            {error.name}: {error.message}
          </pre>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="min-h-11 rounded-xl bg-white px-4 text-sm font-semibold text-red-700 hover:bg-red-100 dark:bg-zinc-900 dark:text-red-300"
            >
              もう一度開く
            </button>
            <button
              type="button"
              onClick={() => location.reload()}
              className="min-h-11 rounded-xl px-4 text-sm text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-950"
            >
              読み直す
            </button>
          </div>
        </div>
      </div>
    );
  }
}
