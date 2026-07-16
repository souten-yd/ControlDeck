import type { Terminal } from "@xterm/xterm";

const DEBUG_LOG_LIMIT = 300;
const decoder = new TextDecoder();

const summarizePty = (data: string | Uint8Array): Record<string, number> => {
  const text = typeof data === "string" ? data : decoder.decode(data);
  const count = (pattern: RegExp): number => text.match(pattern)?.length ?? 0;
  return {
    length: text.length,
    printable: count(/[\x20-\x7e\u0080-\uffff]/g),
    cr: count(/\r/g),
    lf: count(/\n/g),
    bs: count(/\x08/g),
    cursorPosition: count(/\x1b\[[0-9;?]*[Hf]/g),
    cursorMove: count(/\x1b\[[0-9;?]*[ABCD]/g),
    eraseDisplay: count(/\x1b\[[0-9;?]*J/g),
    eraseLine: count(/\x1b\[[0-9;?]*K/g),
    scrollRegion: count(/\x1b\[[0-9;?]*r/g),
    alternateScreenSet: count(/\x1b\[\?1049h/g),
    alternateScreenReset: count(/\x1b\[\?1049l/g),
  };
};

const normalizeRow = (value: string): string => value.replace(/\u00a0/g, " ").trimEnd();

/** opt-in時だけPTY制御要約と、明示要求時のbuffer/DOM座標snapshotを保持する。 */
export class TerminalDiagnostics {
  private readonly log: Record<string, unknown>[] = [];

  constructor(private readonly options: {
    terminal: Terminal;
    host: HTMLElement;
    root: HTMLElement;
    helper: HTMLElement;
    enabled: boolean;
    isComposing: () => boolean;
  }) {}

  record(event: string, details: Record<string, unknown> = {}): void {
    if (!this.options.enabled) return;
    const buffer = this.options.terminal.buffer.active;
    this.log.push({
      event,
      timestamp: performance.now(),
      rows: this.options.terminal.rows,
      cols: this.options.terminal.cols,
      bufferType: buffer.type,
      cursorX: buffer.cursorX,
      cursorY: buffer.cursorY,
      baseY: buffer.baseY,
      viewportY: buffer.viewportY,
      composing: this.options.isComposing(),
      ...details,
    });
    if (this.log.length > DEBUG_LOG_LIMIT) this.log.shift();
  }

  recordPty(event: string, data: string | Uint8Array, details: Record<string, unknown> = {}): void {
    if (!this.options.enabled) return;
    this.record(event, { ...details, pty: summarizePty(data) });
  }

  getLog(): readonly Record<string, unknown>[] {
    return this.log;
  }

  captureRenderState(): Record<string, unknown> {
    const terminal = this.options.terminal;
    const buffer = terminal.buffer.active;
    const visibleBufferRows = Array.from({ length: terminal.rows }, (_, row) =>
      normalizeRow(buffer.getLine(buffer.viewportY + row)?.translateToString(true) ?? ""));
    const domRows = [...this.options.host.querySelectorAll<HTMLElement>(".xterm-rows > div")].map((row, index) => {
      const rect = row.getBoundingClientRect();
      const style = getComputedStyle(row);
      return {
        index,
        text: normalizeRow(row.textContent ?? ""),
        top: rect.top,
        left: rect.left,
        width: rect.width,
        height: rect.height,
        position: style.position,
        transform: style.transform,
        lineHeight: style.lineHeight,
        fontSize: style.fontSize,
        letterSpacing: style.letterSpacing,
        whiteSpace: style.whiteSpace,
      };
    });
    const textarea = this.options.host.querySelector<HTMLTextAreaElement>(".xterm-helper-textarea");
    const cursor = this.options.host.querySelector<HTMLElement>(".xterm-cursor-layer .xterm-cursor");
    const screen = this.options.host.querySelector<HTMLElement>(".xterm-screen");
    const rect = (element: Element | null): DOMRect | undefined => element?.getBoundingClientRect();
    return {
      timestamp: performance.now(),
      rows: terminal.rows,
      cols: terminal.cols,
      bufferType: buffer.type,
      cursorX: buffer.cursorX,
      cursorY: buffer.cursorY,
      baseY: buffer.baseY,
      viewportY: buffer.viewportY,
      visibleBufferRows,
      domRows,
      mismatchedRows: domRows
        .filter((row) => row.text !== visibleBufferRows[row.index])
        .map((row) => row.index),
      textareaCount: this.options.host.querySelectorAll(".xterm-helper-textarea").length,
      activeElementIsTextarea: document.activeElement === textarea,
      textareaRect: rect(textarea),
      cursorRect: rect(cursor),
      screenRect: rect(screen),
      hostRect: rect(this.options.host),
      rootRect: rect(this.options.root),
      helperRect: rect(this.options.helper),
      visualViewport: window.visualViewport ? {
        width: window.visualViewport.width,
        height: window.visualViewport.height,
        offsetTop: window.visualViewport.offsetTop,
        offsetLeft: window.visualViewport.offsetLeft,
      } : undefined,
    };
  }
}
