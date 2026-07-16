import type { Terminal } from "@xterm/xterm";

const DEBUG_LOG_LIMIT = 200;

interface TerminalImeControllerOptions {
  host: HTMLElement;
  terminal: Terminal;
  debug: boolean;
  collectGeometryDebug?: () => Record<string, unknown>;
  onCompositionSettled: () => void;
}

interface ImeDebugEntry extends Record<string, unknown> {
  event: string;
  timestamp: number;
}

/** xtermのIME textareaとcomposition lifecycleだけを管理する。 */
export class TerminalImeController {
  private textarea: HTMLTextAreaElement | null = null;
  private composing = false;
  private settling = false;
  private disposed = false;
  private compositionGeneration = 0;
  private settleFrame1 = 0;
  private settleFrame2 = 0;
  private retryFrame = 0;
  private readonly debugLog: ImeDebugEntry[] = [];

  constructor(private readonly options: TerminalImeControllerOptions) {
    if (!this.attachTextarea()) {
      this.retryFrame = window.requestAnimationFrame(() => {
        this.retryFrame = 0;
        if (!this.disposed && !this.attachTextarea() && this.options.debug) {
          console.warn("[terminal-ime] xterm helper textarea was not found");
        }
      });
    }
    this.options.host.addEventListener("focusin", this.onHostFocusIn);
  }

  isGeometryLocked = (): boolean => this.composing || this.settling;

  isComposing = (): boolean => this.composing;

  getTextareaCount(): number {
    return this.options.host.querySelectorAll(".xterm-helper-textarea").length;
  }

  getDebugLog(): readonly ImeDebugEntry[] {
    return this.debugLog;
  }

  /**
   * xterm 6.0.0はcursor move時だけhelper textareaを同期し、resize時は同期しない。
   * composition終了後の最終resizeに限り、xterm自身の_syncTextAreaと同じセル座標式で追従させる。
   */
  syncTextareaToCursor(): boolean {
    if (this.disposed || this.isGeometryLocked()) return false;
    if ((!this.textarea || !this.textarea.isConnected) && !this.attachTextarea()) return false;
    const textarea = this.textarea;
    const screen = this.options.host.querySelector<HTMLElement>(".xterm-screen");
    if (!textarea || !screen || this.options.terminal.rows <= 0 || this.options.terminal.cols <= 0) return false;
    const screenRect = screen.getBoundingClientRect();
    if (!Number.isFinite(screenRect.width) || !Number.isFinite(screenRect.height)
      || screenRect.width <= 0 || screenRect.height <= 0) return false;
    const buffer = this.options.terminal.buffer.active;
    const cursorX = Math.min(buffer.cursorX, this.options.terminal.cols - 1);
    const cursorY = Math.min(buffer.cursorY, this.options.terminal.rows - 1);
    const cellWidth = screenRect.width / this.options.terminal.cols;
    const cellHeight = screenRect.height / this.options.terminal.rows;
    const cursorCellWidth = Math.max(buffer.getLine(buffer.baseY + cursorY)?.getCell(cursorX)?.getWidth() ?? 1, 1);
    const left = cursorX * cellWidth;
    const top = cursorY * cellHeight;
    const currentLeft = Number.parseFloat(textarea.style.left);
    const currentTop = Number.parseFloat(textarea.style.top);
    if (Math.abs(currentLeft - left) < 0.5 && Math.abs(currentTop - top) < 0.5) return false;
    textarea.style.left = `${left}px`;
    textarea.style.top = `${top}px`;
    textarea.style.width = `${Math.max(cellWidth * cursorCellWidth, 1)}px`;
    textarea.style.height = `${Math.max(cellHeight, 1)}px`;
    textarea.style.lineHeight = `${Math.max(cellHeight, 1)}px`;
    this.log("textarea-cursor-synced");
    return true;
  }

  private findTextarea(): HTMLTextAreaElement | null {
    return this.options.host.querySelector<HTMLTextAreaElement>(".xterm-helper-textarea");
  }

  private attachTextarea(): boolean {
    const next = this.findTextarea();
    if (!next) return false;
    if (this.textarea === next) return true;
    this.detachTextarea();
    this.textarea = next;
    next.addEventListener("compositionstart", this.onCompositionStart);
    next.addEventListener("compositionupdate", this.onCompositionUpdate);
    next.addEventListener("compositionend", this.onCompositionEnd);
    next.addEventListener("beforeinput", this.onBeforeInput);
    next.addEventListener("input", this.onInput);
    next.addEventListener("focus", this.onFocus);
    next.addEventListener("blur", this.onBlur);
    this.log("textarea-attached");
    return true;
  }

  private detachTextarea(): void {
    if (!this.textarea) return;
    this.textarea.removeEventListener("compositionstart", this.onCompositionStart);
    this.textarea.removeEventListener("compositionupdate", this.onCompositionUpdate);
    this.textarea.removeEventListener("compositionend", this.onCompositionEnd);
    this.textarea.removeEventListener("beforeinput", this.onBeforeInput);
    this.textarea.removeEventListener("input", this.onInput);
    this.textarea.removeEventListener("focus", this.onFocus);
    this.textarea.removeEventListener("blur", this.onBlur);
    this.textarea = null;
  }

  private readonly onHostFocusIn = (): void => {
    if (!this.textarea || !this.textarea.isConnected) this.attachTextarea();
  };

  private readonly onCompositionStart = (event: CompositionEvent): void => {
    window.cancelAnimationFrame(this.settleFrame1);
    window.cancelAnimationFrame(this.settleFrame2);
    this.settleFrame1 = 0;
    this.settleFrame2 = 0;
    this.composing = true;
    this.settling = false;
    this.compositionGeneration += 1;
    this.log("compositionstart", event.data);
  };

  private readonly onCompositionUpdate = (event: CompositionEvent): void => {
    this.log("compositionupdate", event.data);
  };

  private readonly onCompositionEnd = (event: CompositionEvent): void => {
    const generation = this.compositionGeneration;
    this.composing = false;
    this.settling = true;
    this.log("compositionend", event.data);
    // iOSが未確定文字とtextarea座標を確定するまでgeometryを解放しない。
    this.settleFrame1 = window.requestAnimationFrame(() => {
      this.settleFrame1 = 0;
      this.settleFrame2 = window.requestAnimationFrame(() => {
        this.settleFrame2 = 0;
        if (this.disposed || generation !== this.compositionGeneration) return;
        this.settling = false;
        this.options.onCompositionSettled();
        this.log("composition-settled");
      });
    });
  };

  private readonly onBeforeInput = (event: InputEvent): void => {
    this.log("beforeinput", event.data, event.inputType);
  };

  private readonly onInput = (event: Event): void => {
    const input = event as InputEvent;
    this.log("input", input.data, input.inputType);
  };

  private readonly onFocus = (): void => this.log("focus");
  private readonly onBlur = (): void => this.log("blur");

  private log(event: string, data?: string | null, inputType?: string): void {
    if (!this.options.debug) return;
    const textareaRect = this.textarea?.getBoundingClientRect();
    const screen = this.options.host.querySelector<HTMLElement>(".xterm-screen");
    const screenRect = screen?.getBoundingClientRect();
    const hostRect = this.options.host.getBoundingClientRect();
    const rootRect = this.options.host.closest<HTMLElement>("[data-terminal-root]")?.getBoundingClientRect();
    const helperRect = this.options.host.closest<HTMLElement>("[data-terminal-root]")
      ?.querySelector<HTMLElement>("[data-terminal-helper]")?.getBoundingClientRect();
    const buffer = this.options.terminal.buffer.active;
    const cellWidth = screenRect && this.options.terminal.cols > 0
      ? screenRect.width / this.options.terminal.cols
      : 0;
    const cellHeight = screenRect && this.options.terminal.rows > 0
      ? screenRect.height / this.options.terminal.rows
      : 0;
    const expectedLeft = screenRect ? screenRect.left + buffer.cursorX * cellWidth : undefined;
    const expectedTop = screenRect ? screenRect.top + buffer.cursorY * cellHeight : undefined;
    const entry: ImeDebugEntry = {
      event,
      timestamp: performance.now(),
      data,
      inputType,
      activeElementIsTextarea: document.activeElement === this.textarea,
      composing: this.composing,
      settling: this.settling,
      compositionGeneration: this.compositionGeneration,
      cursorX: buffer.cursorX,
      cursorY: buffer.cursorY,
      viewportY: buffer.viewportY,
      baseY: buffer.baseY,
      rows: this.options.terminal.rows,
      cols: this.options.terminal.cols,
      textareaCount: this.getTextareaCount(),
      textareaRect,
      screenRect,
      hostRect,
      rootRect,
      helperRect,
      expectedLeft,
      expectedTop,
      textareaCursorDeltaX: textareaRect && expectedLeft !== undefined ? textareaRect.left - expectedLeft : undefined,
      textareaCursorDeltaY: textareaRect && expectedTop !== undefined ? textareaRect.top - expectedTop : undefined,
      visualViewportWidth: window.visualViewport?.width,
      visualViewportHeight: window.visualViewport?.height,
      visualViewportOffsetTop: window.visualViewport?.offsetTop,
      visualViewportOffsetLeft: window.visualViewport?.offsetLeft,
      ...this.options.collectGeometryDebug?.(),
    };
    this.debugLog.push(entry);
    if (this.debugLog.length > DEBUG_LOG_LIMIT) this.debugLog.shift();
    console.debug("[terminal-ime]", entry);
  }

  dispose(): void {
    this.disposed = true;
    this.composing = false;
    this.settling = false;
    window.cancelAnimationFrame(this.retryFrame);
    window.cancelAnimationFrame(this.settleFrame1);
    window.cancelAnimationFrame(this.settleFrame2);
    this.options.host.removeEventListener("focusin", this.onHostFocusIn);
    this.detachTextarea();
  }
}
