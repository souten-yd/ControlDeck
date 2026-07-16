const BARRIER_TIMEOUT_MS = 125;
const DEBUG_LOG_LIMIT = 300;

export interface TerminalResizeAck {
  type: "resize_ack";
  cols: number;
  rows: number;
  resizeGeneration: number;
  connectionGeneration: number;
  success: boolean;
  diagnostics?: Record<string, unknown>;
}

export interface ResizeBarrierCounters {
  started: number;
  ackAccepted: number;
  ackIgnored: number;
  inputQueued: number;
  inputReleased: number;
  timeoutReleased: number;
  overflowReleased: number;
  maxQueuedChunks: number;
  maxQueuedBytes: number;
}

interface ActiveResize {
  resizeGeneration: number;
  connectionGeneration: number;
  cols: number;
  rows: number;
  acked: boolean;
  queuedInput: string[];
  queuedBytes: number;
  timeout: number;
}

export interface ResizeFrameToken {
  resizeGeneration: number;
  connectionGeneration: number;
}

/** PTY resizeのACKとACK後初回描画が揃うまでinputを受信単位でFIFO保持する。 */
export class TerminalResizeBarrier {
  private disposed = false;
  private connectionGeneration = 0;
  private active?: ActiveResize;
  private readonly debugLog: Record<string, unknown>[] = [];
  private readonly counters: ResizeBarrierCounters = {
    started: 0,
    ackAccepted: 0,
    ackIgnored: 0,
    inputQueued: 0,
    inputReleased: 0,
    timeoutReleased: 0,
    overflowReleased: 0,
    maxQueuedChunks: 0,
    maxQueuedBytes: 0,
  };

  constructor(private readonly options: {
    sendNow: (data: string) => void;
    requeue: (data: string) => void;
    onAckAccepted: (resizeGeneration: number, cols: number, rows: number) => void;
    onSettled: (resizeGeneration: number, reason: string) => void;
    debug: boolean;
  }) {}

  resetConnection(connectionGeneration: number): void {
    this.requeueActive("connection-reset");
    this.connectionGeneration = connectionGeneration;
    this.record("connection-reset", { connectionGeneration });
  }

  startResize(resizeGeneration: number, connectionGeneration: number, cols: number, rows: number): boolean {
    if (this.disposed || connectionGeneration !== this.connectionGeneration || this.active) return false;
    const timeout = window.setTimeout(() => {
      const active = this.active;
      if (!active || active.resizeGeneration !== resizeGeneration
        || active.connectionGeneration !== connectionGeneration) return;
      this.counters.timeoutReleased += 1;
      this.release(active.acked ? "timeout-after-ack" : "timeout-before-ack");
    }, BARRIER_TIMEOUT_MS);
    this.active = {
      resizeGeneration,
      connectionGeneration,
      cols,
      rows,
      acked: false,
      queuedInput: [],
      queuedBytes: 0,
      timeout,
    };
    this.counters.started += 1;
    this.record("resize-barrier-start", { resizeGeneration, connectionGeneration, cols, rows });
    return true;
  }

  handleAck(ack: TerminalResizeAck): boolean {
    const active = this.active;
    const valid = Boolean(active)
      && ack.success === true
      && ack.connectionGeneration === this.connectionGeneration
      && ack.connectionGeneration === active!.connectionGeneration
      && ack.resizeGeneration === active!.resizeGeneration
      && ack.cols === active!.cols
      && ack.rows === active!.rows;
    if (!valid) {
      this.counters.ackIgnored += 1;
      this.record("resize-ack-ignored", {
        resizeGeneration: ack.resizeGeneration,
        connectionGeneration: ack.connectionGeneration,
        cols: ack.cols,
        rows: ack.rows,
        success: ack.success,
      });
      if (active && ack.success === false
        && ack.resizeGeneration === active.resizeGeneration
        && ack.connectionGeneration === active.connectionGeneration) {
        this.release("ack-failure");
      }
      return false;
    }
    active!.acked = true;
    this.counters.ackAccepted += 1;
    this.options.onAckAccepted(active!.resizeGeneration, active!.cols, active!.rows);
    this.record("resize-ack-accepted", {
      resizeGeneration: ack.resizeGeneration,
      connectionGeneration: ack.connectionGeneration,
      diagnostics: ack.diagnostics,
    });
    return true;
  }

  /** WebSocket上でACKより後に受信したPTY frameだけへtokenを付ける。 */
  captureFrameAfterAck(): ResizeFrameToken | null {
    const active = this.active;
    if (!active?.acked) return null;
    return {
      resizeGeneration: active.resizeGeneration,
      connectionGeneration: active.connectionGeneration,
    };
  }

  completePtyFrame(token: ResizeFrameToken): boolean {
    const active = this.active;
    if (!active || !active.acked
      || token.resizeGeneration !== active.resizeGeneration
      || token.connectionGeneration !== active.connectionGeneration) return false;
    this.record("first-pty-write-complete", { ...token });
    this.release("pty-write-complete");
    return true;
  }

  sendOrQueue(data: string): void {
    const active = this.active;
    if (!active) {
      this.options.sendNow(data);
      return;
    }
    const bytes = new TextEncoder().encode(data).byteLength;
    active.queuedInput.push(data);
    active.queuedBytes += bytes;
    this.counters.inputQueued += 1;
    this.counters.maxQueuedChunks = Math.max(this.counters.maxQueuedChunks, active.queuedInput.length);
    this.counters.maxQueuedBytes = Math.max(this.counters.maxQueuedBytes, active.queuedBytes);
    this.record("input-queued", {
      resizeGeneration: active.resizeGeneration,
      chunks: active.queuedInput.length,
      bytes: active.queuedBytes,
    });
  }

  isActive = (): boolean => Boolean(this.active);

  abort(reason: string): void {
    this.release(reason);
  }

  getState(): Record<string, unknown> {
    const active = this.active;
    return {
      connectionGeneration: this.connectionGeneration,
      active: Boolean(active),
      resizeGeneration: active?.resizeGeneration,
      cols: active?.cols,
      rows: active?.rows,
      acked: active?.acked ?? false,
      queuedChunks: active?.queuedInput.length ?? 0,
      queuedBytes: active?.queuedBytes ?? 0,
      counters: { ...this.counters },
    };
  }

  getDebugLog(): readonly Record<string, unknown>[] {
    return this.debugLog;
  }

  private release(reason: string): void {
    const active = this.active;
    if (!active) return;
    window.clearTimeout(active.timeout);
    this.active = undefined;
    for (const data of active.queuedInput) this.options.sendNow(data);
    this.counters.inputReleased += active.queuedInput.length;
    this.record("resize-barrier-release", {
      resizeGeneration: active.resizeGeneration,
      connectionGeneration: active.connectionGeneration,
      reason,
      releasedChunks: active.queuedInput.length,
    });
    this.options.onSettled(active.resizeGeneration, reason);
  }

  private requeueActive(reason: string): void {
    const active = this.active;
    if (!active) return;
    window.clearTimeout(active.timeout);
    this.active = undefined;
    for (const data of active.queuedInput) this.options.requeue(data);
    this.record("resize-barrier-requeue", {
      resizeGeneration: active.resizeGeneration, connectionGeneration: active.connectionGeneration,
      reason, requeuedChunks: active.queuedInput.length,
    });
  }

  private discardActive(reason: string): void {
    const active = this.active;
    if (!active) return;
    window.clearTimeout(active.timeout);
    this.active = undefined;
    this.record("resize-barrier-discard", {
      resizeGeneration: active.resizeGeneration,
      connectionGeneration: active.connectionGeneration,
      reason,
      discardedChunks: active.queuedInput.length,
    });
  }

  private record(event: string, details: Record<string, unknown>): void {
    if (!this.options.debug) return;
    this.debugLog.push({ event, timestamp: performance.now(), ...details });
    if (this.debugLog.length > DEBUG_LOG_LIMIT) this.debugLog.shift();
  }

  dispose(): void {
    this.disposed = true;
    this.discardActive("dispose");
  }
}
