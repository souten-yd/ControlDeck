import type { FitAddon } from "@xterm/addon-fit";
import type { Terminal } from "@xterm/xterm";
import type { TerminalWriteQueue } from "./TerminalWriteQueue";

export type GeometryInvalidation = "size" | "position" | "renderer" | "connection";

export interface TerminalPerfCounters {
  fitRequested: number;
  fitExecuted: number;
  fitSkipped: number;
  resizeExecuted: number;
  refreshExecuted: number;
  rectReads: number;
  viewportEvents: number;
  observerEvents: number;
  ptyResizeSent: number;
  geometryTasksQueued: number;
  geometryTasksPending: number;
  maxGeometryTasksPending: number;
  longTasks: number;
}

interface TerminalGeometryControllerOptions {
  root: HTMLElement;
  header: HTMLElement;
  body: HTMLElement;
  host: HTMLElement;
  helper: HTMLElement;
  terminal: Terminal;
  fitAddon: FitAddon;
  writeQueue: TerminalWriteQueue;
  coarseMobile: boolean;
  debug: boolean;
  isGeometryLocked: () => boolean;
  sendPtyResize: (cols: number, rows: number) => boolean;
  resumeConnection: () => void;
}

const PIXEL_EPSILON = 0.5;
const SETTLE_MS = 50;
const RECOVERY_COOLDOWN_MS = 1_000;
const MAX_PENDING_REASONS = 16;
const DEBUG_LOG_LIMIT = 200;
const changed = (a: number, b: number): boolean => Math.abs(a - b) >= PIXEL_EPSILON;

/** Visual Viewport/observer/fit/PTY resizeを単一schedulerへ集約する。 */
export class TerminalGeometryController {
  private disposed = false;
  private generation = 0;
  private frame1 = 0;
  private frame2 = 0;
  private measureFrame = 0;
  private completionFrame = 0;
  private settleTimer: number | undefined;
  private pendingInvalidations = new Set<GeometryInvalidation>();
  private pendingReasons = new Set<string>();
  private geometryTaskQueued = false;
  private forcePtySync = false;
  private afterCompositionFlush?: () => void;
  private lastHostWidth = 0;
  private lastHostHeight = 0;
  private lastViewportWidth = 0;
  private lastViewportHeight = 0;
  private lastViewportTop = 0;
  private lastViewportLeft = 0;
  private lastRecoveryAt = 0;
  private lastRecoveryGeneration = -1;
  private longTaskObserver?: PerformanceObserver;
  private readonly debugLog: Record<string, unknown>[] = [];
  private readonly observer: ResizeObserver;
  private readonly counters: TerminalPerfCounters = {
    fitRequested: 0,
    fitExecuted: 0,
    fitSkipped: 0,
    resizeExecuted: 0,
    refreshExecuted: 0,
    rectReads: 0,
    viewportEvents: 0,
    observerEvents: 0,
    ptyResizeSent: 0,
    geometryTasksQueued: 0,
    geometryTasksPending: 0,
    maxGeometryTasksPending: 0,
    longTasks: 0,
  };

  constructor(private readonly options: TerminalGeometryControllerOptions) {
    this.observer = new ResizeObserver(this.onObservedResize);
    this.observer.observe(options.host);
    window.visualViewport?.addEventListener("resize", this.onViewportResize);
    window.visualViewport?.addEventListener("scroll", this.onViewportScroll);
    window.addEventListener("resize", this.onWindowResize);
    window.addEventListener("pageshow", this.onPageShow);
    document.addEventListener("visibilitychange", this.onVisibilityChange);
    if (options.debug && typeof PerformanceObserver !== "undefined"
      && PerformanceObserver.supportedEntryTypes?.includes("longtask")) {
      this.longTaskObserver = new PerformanceObserver((list) => {
        this.counters.longTasks += list.getEntries().length;
        this.publishCounters();
      });
      this.longTaskObserver.observe({ entryTypes: ["longtask"] });
    }
    this.invalidate("size", "initial-layout");
  }

  invalidate(type: GeometryInvalidation, reason: string): void {
    if (this.disposed) return;
    this.pendingInvalidations.add(type);
    this.recordReason(reason);
    this.counters.fitRequested += type === "size" || type === "connection" ? 1 : 0;
    this.publishCounters();
    if (this.options.isGeometryLocked()) return;
    this.scheduleStableFlush();
  }

  onConnectionOpen(): void {
    this.forcePtySync = true;
    this.invalidate("connection", "websocket-open");
    this.invalidate("renderer", "websocket-open");
  }

  flushAfterComposition(onFlushed?: () => void): void {
    if (this.disposed) return;
    this.afterCompositionFlush = onFlushed;
    if (this.pendingInvalidations.size === 0) this.pendingInvalidations.add("size");
    this.recordReason("composition-end");
    this.scheduleStableFlush();
  }

  getDebugState(): Record<string, unknown> {
    return {
      fitGeneration: this.generation,
      pendingReasons: [...this.pendingReasons],
      pendingInvalidations: [...this.pendingInvalidations],
      geometryTaskQueued: this.geometryTaskQueued,
      counters: { ...this.counters },
    };
  }

  getCounters(): Readonly<TerminalPerfCounters> {
    return this.counters;
  }

  getDebugLog(): readonly Record<string, unknown>[] {
    return this.debugLog;
  }

  resetCounters(): void {
    for (const key of Object.keys(this.counters) as (keyof TerminalPerfCounters)[]) {
      this.counters[key] = 0;
    }
    this.publishCounters();
  }

  private scheduleStableFlush(): void {
    const generation = ++this.generation;
    window.cancelAnimationFrame(this.frame1);
    window.cancelAnimationFrame(this.frame2);
    window.cancelAnimationFrame(this.measureFrame);
    window.clearTimeout(this.settleTimer);
    this.frame1 = window.requestAnimationFrame(() => {
      this.frame1 = 0;
      this.frame2 = window.requestAnimationFrame(() => {
        this.frame2 = 0;
        this.settleTimer = window.setTimeout(() => this.prepareFlush(generation), SETTLE_MS);
      });
    });
  }

  private recordReason(reason: string): void {
    if (this.pendingReasons.size < MAX_PENDING_REASONS || this.pendingReasons.has(reason)) {
      this.pendingReasons.add(reason);
    } else {
      this.pendingReasons.add("multiple-events");
    }
  }

  private prepareFlush(generation: number): void {
    if (this.disposed || generation !== this.generation) return;
    if (this.options.isGeometryLocked()) return;
    const viewport = window.visualViewport;
    const viewportWidth = viewport?.width ?? window.innerWidth;
    const viewportHeight = viewport?.height ?? window.innerHeight;
    const viewportTop = viewport?.offsetTop ?? 0;
    const viewportLeft = viewport?.offsetLeft ?? 0;
    const positionChanged = changed(viewportTop, this.lastViewportTop)
      || changed(viewportLeft, this.lastViewportLeft);
    const viewportSizeChanged = changed(viewportWidth, this.lastViewportWidth)
      || changed(viewportHeight, this.lastViewportHeight);

    // DOM writeは一括し、次frameのDOM readと交互にしない。
    if (this.options.coarseMobile) {
      if (positionChanged) {
        this.options.root.style.left = `${viewportLeft}px`;
        this.options.root.style.top = `${viewportTop}px`;
      }
      if (viewportSizeChanged) {
        this.options.root.style.width = `${viewportWidth}px`;
        this.options.root.style.height = `${viewportHeight}px`;
      }
    }
    this.lastViewportWidth = viewportWidth;
    this.lastViewportHeight = viewportHeight;
    this.lastViewportTop = viewportTop;
    this.lastViewportLeft = viewportLeft;
    this.measureFrame = window.requestAnimationFrame(() => {
      this.measureFrame = 0;
      this.measureAndQueue(generation, viewportSizeChanged);
    });
  }

  private measureAndQueue(generation: number, viewportSizeChanged: boolean): void {
    if (this.disposed || generation !== this.generation || this.options.isGeometryLocked()) return;
    const needsSize = this.pendingInvalidations.has("size")
      || this.pendingInvalidations.has("connection")
      || viewportSizeChanged;
    const needsRenderer = this.pendingInvalidations.has("renderer");
    if (!needsSize && !needsRenderer) {
      this.pendingInvalidations.clear();
      this.pendingReasons.clear();
      this.finishCompositionFlush();
      return;
    }
    const rootRect = this.options.root.getBoundingClientRect();
    const headerRect = this.options.header.getBoundingClientRect();
    const bodyRect = this.options.body.getBoundingClientRect();
    const hostRect = this.options.host.getBoundingClientRect();
    const helperRect = this.options.helper.getBoundingClientRect();
    const screen = this.options.host.querySelector<HTMLElement>(".xterm-screen");
    const screenRect = screen?.getBoundingClientRect();
    this.counters.rectReads += screen ? 6 : 5;
    const validHost = document.visibilityState === "visible"
      && this.options.host.isConnected
      && Number.isFinite(hostRect.width)
      && Number.isFinite(hostRect.height)
      && hostRect.width >= 100
      && hostRect.height >= 80;
    if (!validHost) {
      this.counters.fitSkipped += 1;
      this.publishCounters();
      return;
    }

    const hostSizeChanged = changed(hostRect.width, this.lastHostWidth)
      || changed(hostRect.height, this.lastHostHeight);
    const shouldPropose = needsSize && (hostSizeChanged || this.lastHostWidth === 0 || this.forcePtySync);
    const dimensions = shouldPropose ? this.options.fitAddon.proposeDimensions() : undefined;
    const rendererInvalid = Boolean(screenRect) && (
      !Number.isFinite(screenRect!.width)
      || !Number.isFinite(screenRect!.height)
      || screenRect!.width <= 0
      || screenRect!.height <= 0
      || screenRect!.width > hostRect.width + 2
      || screenRect!.height > hostRect.height + 2
    );
    const layoutDelta = Math.abs(headerRect.height + bodyRect.height + helperRect.height - rootRect.height);
    const layoutInvalid = layoutDelta > 1.5
      || bodyRect.bottom > helperRect.top + 1
      || hostRect.bottom > helperRect.top + 1
      || Boolean(screenRect && screenRect.bottom > helperRect.top + 2);
    const reasons = [...this.pendingReasons];
    this.pendingInvalidations.clear();
    this.pendingReasons.clear();
    if (!dimensions && !needsRenderer && !this.forcePtySync) {
      this.counters.fitSkipped += 1;
      this.publishCounters();
      this.finishCompositionFlush();
      return;
    }
    this.queueGeometryTask({
      generation,
      dimensions,
      hostWidth: hostRect.width,
      hostHeight: hostRect.height,
      rendererInvalid: needsRenderer && rendererInvalid,
    });
    if (this.options.debug) {
      const entry = {
        reasons,
        generation,
        rootRect,
        headerRect,
        bodyRect,
        hostRect,
        helperRect,
        screenRect,
        layoutDelta,
        layoutInvalid,
        rendererInvalid,
        dimensions,
        counters: { ...this.counters },
      };
      this.debugLog.push(entry);
      if (this.debugLog.length > DEBUG_LOG_LIMIT) this.debugLog.shift();
      console.debug("[terminal-geometry]", entry);
    }
  }

  private queueGeometryTask(task: {
    generation: number;
    dimensions?: { cols: number; rows: number };
    hostWidth: number;
    hostHeight: number;
    rendererInvalid: boolean;
  }): void {
    if (this.geometryTaskQueued) {
      // 最新世代はpending invalidationとして次の単一flushへ統合する。
      this.pendingInvalidations.add("size");
      return;
    }
    this.geometryTaskQueued = true;
    this.counters.geometryTasksQueued += 1;
    this.counters.geometryTasksPending = 1;
    this.counters.maxGeometryTasksPending = Math.max(this.counters.maxGeometryTasksPending, 1);
    this.publishCounters();
    this.options.writeQueue.enqueueTask(() => {
      this.geometryTaskQueued = false;
      this.counters.geometryTasksPending = 0;
      if (this.disposed || task.generation !== this.generation || this.options.isGeometryLocked()) {
        this.pendingInvalidations.add("size");
        this.publishCounters();
        if (!this.disposed && !this.options.isGeometryLocked()) this.scheduleStableFlush();
        return;
      }
      const dimensions = task.dimensions;
      let ptySizeSent = false;
      this.lastHostWidth = task.hostWidth;
      this.lastHostHeight = task.hostHeight;
      if (dimensions && dimensions.cols >= 10 && dimensions.rows >= 3) {
        this.counters.fitExecuted += 1;
        const terminalSizeChanged = dimensions.cols !== this.options.terminal.cols
          || dimensions.rows !== this.options.terminal.rows;
        if (terminalSizeChanged) {
          const buffer = this.options.terminal.buffer.active;
          const isNormal = buffer.type === "normal";
          const wasAtBottom = buffer.viewportY >= buffer.baseY;
          const previousViewportY = buffer.viewportY;
          this.options.terminal.resize(dimensions.cols, dimensions.rows);
          this.counters.resizeExecuted += 1;
          if (isNormal) {
            if (wasAtBottom) this.options.terminal.scrollToBottom();
            else this.options.terminal.scrollToLine(previousViewportY);
          }
          if (this.options.sendPtyResize(dimensions.cols, dimensions.rows)) {
            this.counters.ptyResizeSent += 1;
            ptySizeSent = true;
          }
        }
      }
      if (!ptySizeSent && this.forcePtySync
        && this.options.sendPtyResize(this.options.terminal.cols, this.options.terminal.rows)) {
        this.counters.ptyResizeSent += 1;
      }
      this.forcePtySync = false;
      this.recoverRendererIfNeeded(task.rendererInvalid, task.generation);
      this.publishCounters();
      if (this.pendingInvalidations.size > 0) this.scheduleStableFlush();
      else this.finishCompositionFlush();
    }, "geometry");
  }

  private finishCompositionFlush(): void {
    const callback = this.afterCompositionFlush;
    if (!callback) return;
    this.afterCompositionFlush = undefined;
    window.cancelAnimationFrame(this.completionFrame);
    // resizeのrenderer更新後に、xterm自身へtextarea/cursor位置を再同期させる。
    this.completionFrame = window.requestAnimationFrame(() => {
      this.completionFrame = 0;
      if (!this.disposed) callback();
    });
  }

  private recoverRendererIfNeeded(invalid: boolean, generation: number): void {
    if (!invalid || this.options.isGeometryLocked()) return;
    const now = performance.now();
    if (this.lastRecoveryGeneration === generation || now - this.lastRecoveryAt < RECOVERY_COOLDOWN_MS) return;
    this.options.terminal.refresh(0, Math.max(0, this.options.terminal.rows - 1));
    this.counters.refreshExecuted += 1;
    this.lastRecoveryAt = now;
    this.lastRecoveryGeneration = generation;
  }

  private readonly onObservedResize = (): void => {
    this.counters.observerEvents += 1;
    this.invalidate("size", "resize-observer");
  };

  private readonly onViewportResize = (): void => {
    this.counters.viewportEvents += 1;
    this.invalidate("size", "visual-viewport-resize");
  };

  private readonly onViewportScroll = (): void => {
    this.counters.viewportEvents += 1;
    this.invalidate("position", "visual-viewport-scroll");
  };

  private readonly onWindowResize = (): void => this.invalidate("size", "window-resize");

  private readonly onPageShow = (): void => {
    this.invalidate("size", "pageshow");
    this.invalidate("renderer", "pageshow");
  };

  private readonly onVisibilityChange = (): void => {
    if (document.visibilityState !== "visible") return;
    this.options.resumeConnection();
    this.invalidate("size", "visibility-visible");
    this.invalidate("renderer", "visibility-visible");
  };

  private publishCounters(): void {
    if (!this.options.debug) return;
    const host = this.options.host;
    host.dataset.terminalFitRequested = String(this.counters.fitRequested);
    host.dataset.terminalFitExecuted = String(this.counters.fitExecuted);
    host.dataset.terminalResizeExecuted = String(this.counters.resizeExecuted);
    host.dataset.terminalRefreshExecuted = String(this.counters.refreshExecuted);
    host.dataset.terminalPtyResizeSent = String(this.counters.ptyResizeSent);
    host.dataset.terminalGeometryTasksPending = String(this.counters.geometryTasksPending);
    host.dataset.terminalGeometryTasksMax = String(this.counters.maxGeometryTasksPending);
    host.dataset.terminalLongTasks = String(this.counters.longTasks);
  }

  dispose(): void {
    this.disposed = true;
    this.generation += 1;
    this.observer.disconnect();
    window.cancelAnimationFrame(this.frame1);
    window.cancelAnimationFrame(this.frame2);
    window.cancelAnimationFrame(this.measureFrame);
    window.cancelAnimationFrame(this.completionFrame);
    window.clearTimeout(this.settleTimer);
    window.visualViewport?.removeEventListener("resize", this.onViewportResize);
    window.visualViewport?.removeEventListener("scroll", this.onViewportScroll);
    window.removeEventListener("resize", this.onWindowResize);
    window.removeEventListener("pageshow", this.onPageShow);
    document.removeEventListener("visibilitychange", this.onVisibilityChange);
    this.longTaskObserver?.disconnect();
    this.pendingInvalidations.clear();
    this.pendingReasons.clear();
    this.afterCompositionFlush = undefined;
  }
}
