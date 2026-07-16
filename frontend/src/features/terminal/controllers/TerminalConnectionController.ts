export type TerminalConnectionState =
  | "DISCONNECTED"
  | "CONNECTING"
  | "INITIAL_REPLAY"
  | "RESUMING"
  | "LIVE"
  | "CLOSED";

const DEBUG_LOG_LIMIT = 300;

export interface HistoryReplayCounters {
  historyReset: number;
  historyEnd: number;
  resumeReady: number;
  resumeResetRequired: number;
  replayFrames: number;
  replayBytes: number;
  websocketCreated: number;
  websocketOpened: number;
  websocketClosed: number;
  reconnectScheduled: number;
  reconnectStarted: number;
}

/** 接続/replay状態と、LIVE前のinput FIFOをReact state外で管理する。 */
export class TerminalConnectionController {
  private state: TerminalConnectionState = "DISCONNECTED";
  private connectionGeneration = 0;
  private lastSequence = 0;
  private startedAt = 0;
  private queuedInput: string[] = [];
  private queuedBytes = 0;
  private readonly log: Record<string, unknown>[] = [];
  private readonly counters: HistoryReplayCounters = {
    historyReset: 0,
    historyEnd: 0,
    resumeReady: 0,
    resumeResetRequired: 0,
    replayFrames: 0,
    replayBytes: 0,
    websocketCreated: 0,
    websocketOpened: 0,
    websocketClosed: 0,
    reconnectScheduled: 0,
    reconnectStarted: 0,
  };

  constructor(private readonly options: {
    debug: boolean;
    sendNow: (data: string) => void;
    snapshot: () => Record<string, unknown>;
  }) {}

  begin(connectionGeneration: number, mode: "initial" | "resume"): void {
    this.connectionGeneration = connectionGeneration;
    this.startedAt = performance.now();
    this.state = "CONNECTING";
    this.counters.websocketCreated += 1;
    if (mode === "resume") this.counters.reconnectStarted += 1;
    this.record("websocket-created", { attachMode: mode });
  }

  opened(connectionGeneration: number, mode: "initial" | "resume"): boolean {
    if (connectionGeneration !== this.connectionGeneration) return false;
    this.state = mode === "initial" ? "INITIAL_REPLAY" : "RESUMING";
    this.counters.websocketOpened += 1;
    this.record("websocket-open", { attachMode: mode });
    return true;
  }

  error(connectionGeneration: number): void {
    if (connectionGeneration === this.connectionGeneration) this.record("websocket-error");
  }

  closed(connectionGeneration: number, event: CloseEvent): boolean {
    if (connectionGeneration !== this.connectionGeneration) return false;
    this.state = "CLOSED";
    this.counters.websocketClosed += 1;
    this.record("websocket-close", {
      code: event.code,
      reason: event.reason,
      wasClean: event.wasClean,
    });
    return true;
  }

  reconnectScheduled(delay: number): void {
    this.counters.reconnectScheduled += 1;
    this.record("reconnect-scheduled", { delay });
  }

  historyReset(connectionGeneration: number): boolean {
    if (connectionGeneration !== this.connectionGeneration) return false;
    this.state = "INITIAL_REPLAY";
    this.counters.historyReset += 1;
    this.record("history-reset-received");
    return true;
  }

  historyFrame(byteLength: number): void {
    if (this.state !== "INITIAL_REPLAY") return;
    this.counters.replayFrames += 1;
    this.counters.replayBytes += byteLength;
    this.record("history-replay-frame", { byteLength });
  }

  resumeReady(connectionGeneration: number): boolean {
    if (connectionGeneration !== this.connectionGeneration || this.state !== "RESUMING") return false;
    this.counters.resumeReady += 1;
    this.record("resume-ready-received");
    return true;
  }

  resumeResetRequired(connectionGeneration: number): boolean {
    if (connectionGeneration !== this.connectionGeneration) return false;
    this.counters.resumeResetRequired += 1;
    this.record("resume-reset-required");
    return true;
  }

  markLive(connectionGeneration: number, sequence: number, event: "history-end-received" | "resume-end-received"): boolean {
    if (connectionGeneration !== this.connectionGeneration) return false;
    this.lastSequence = Math.max(this.lastSequence, sequence);
    this.state = "LIVE";
    if (event === "history-end-received") this.counters.historyEnd += 1;
    this.record(event, { durationMs: performance.now() - this.startedAt, sequence });
    const input = this.queuedInput;
    this.queuedInput = [];
    this.queuedBytes = 0;
    for (const data of input) this.options.sendNow(data);
    return true;
  }

  outputDrawn(connectionGeneration: number, sequence: number): void {
    if (connectionGeneration !== this.connectionGeneration) return;
    this.lastSequence = Math.max(this.lastSequence, sequence);
  }

  sendOrQueue(data: string): void {
    if (this.state === "LIVE") {
      this.options.sendNow(data);
      return;
    }
    const bytes = new TextEncoder().encode(data).byteLength;
    this.queuedInput.push(data);
    this.queuedBytes += bytes;
  }

  getState(): Record<string, unknown> {
    return {
      state: this.state,
      connectionGeneration: this.connectionGeneration,
      lastSequence: this.lastSequence,
      queuedInputChunks: this.queuedInput.length,
      queuedInputBytes: this.queuedBytes,
    };
  }

  getLastSequence(): number {
    return this.lastSequence;
  }

  setLastSequenceForTest(sequence: number): void {
    this.lastSequence = sequence;
  }

  getLog(): readonly Record<string, unknown>[] {
    return this.log;
  }

  getCounters(): HistoryReplayCounters {
    return { ...this.counters };
  }

  private record(event: string, details: Record<string, unknown> = {}): void {
    if (!this.options.debug) return;
    this.log.push({
      event,
      timestamp: performance.now(),
      connectionGeneration: this.connectionGeneration,
      ...this.options.snapshot(),
      ...details,
    });
    if (this.log.length > DEBUG_LOG_LIMIT) this.log.shift();
  }
}
