export const PASTE_CHUNK_BYTES = 8 * 1024;
const BUFFERED_AMOUNT_HIGH_WATER = 256 * 1024;
const ACK_TIMEOUT_MS = 15_000;

export type PasteState = "queued" | "sending" | "paused" | "completed" | "failed" | "cancelled";

export interface PasteProgress {
  id: number;
  state: PasteState;
  acknowledgedBytes: number;
  totalBytes: number;
  error?: string;
}

export interface PasteTransaction {
  id: number;
  originalText: string;
  normalizedText: string;
  utf8Bytes: Uint8Array;
  offset: number;
  state: PasteState;
}

interface InFlightChunk {
  inputSequence: number;
  pasteId: number;
  chunkIndex: number;
  start: number;
  end: number;
  final: boolean;
  connectionGeneration: number;
}

export interface InputAck {
  type: "input_ack";
  inputSequence: number;
  pasteId: number;
  chunkIndex: number;
  writtenBytes: number;
  connectionGeneration: number;
}

export interface InputError {
  type: "input_error";
  inputSequence: number;
  pasteId: number;
  chunkIndex: number;
  connectionGeneration: number;
  reason: string;
}

/** xterm標準pasteと同じ改行・bracketed paste変換。 */
export const prepareTerminalPaste = (text: string, bracketedPasteMode: boolean): string => {
  const normalized = text.replace(/\r?\n/g, "\r");
  return bracketedPasteMode ? `\x1b[200~${normalized}\x1b[201~` : normalized;
};

/** 長文pasteをACK単位で送る。通常キー入力とは独立し、同時未ACKは1 chunkに限定する。 */
export class TerminalInputController {
  private nextPasteId = 1;
  private nextInputSequence = 1;
  private queue: PasteTransaction[] = [];
  private inFlight?: InFlightChunk;
  private retryChunk?: InFlightChunk;
  private ackTimer?: number;
  private disposed = false;

  constructor(private readonly options: {
    canSend: () => boolean;
    connectionGeneration: () => number;
    bufferedAmount: () => number;
    sendFrame: (control: Record<string, unknown>, bytes: Uint8Array) => boolean;
    onProgress: (progress: PasteProgress | null) => void;
    record?: (event: string, details: Record<string, unknown>) => void;
  }) {}

  enqueuePaste(originalText: string, normalizedText: string): number {
    const transaction: PasteTransaction = {
      id: this.nextPasteId++,
      originalText,
      normalizedText,
      utf8Bytes: new TextEncoder().encode(normalizedText),
      offset: 0,
      state: "queued",
    };
    this.queue.push(transaction);
    let hash = 0x811c9dc5;
    for (const byte of transaction.utf8Bytes) hash = Math.imul(hash ^ byte, 0x01000193);
    this.record("paste-enqueued", {
      pasteId: transaction.id, characterLength: originalText.length,
      byteLength: transaction.utf8Bytes.byteLength, hash: (hash >>> 0).toString(16).padStart(8, "0"),
      firstMasked: originalText ? "***" : "", lastMasked: originalText ? "***" : "",
    });
    this.report(transaction);
    this.pump();
    return transaction.id;
  }

  connectionChanged(): void {
    if (this.inFlight) {
      this.clearAckTimer();
      this.record("paste-chunk-paused", { ...this.inFlight, reason: "connection-changed" });
      this.retryChunk = this.inFlight;
      this.inFlight = undefined;
    }
    const current = this.queue[0];
    if (current && current.state !== "failed") current.state = "paused";
    this.report(current);
    this.pump();
  }

  availabilityChanged(): void {
    this.pump();
  }

  handleAck(ack: InputAck): boolean {
    const chunk = this.inFlight;
    if (!chunk || ack.connectionGeneration !== chunk.connectionGeneration
      || ack.inputSequence !== chunk.inputSequence || ack.pasteId !== chunk.pasteId
      || ack.chunkIndex !== chunk.chunkIndex || ack.writtenBytes !== chunk.end - chunk.start) {
      this.record("paste-ack-ignored", { ...ack });
      return false;
    }
    this.clearAckTimer();
    this.inFlight = undefined;
    this.retryChunk = undefined;
    const current = this.queue[0];
    if (!current || current.id !== chunk.pasteId) return false;
    current.offset = chunk.end;
    this.record("paste-chunk-acknowledged", {
      ...chunk, writtenBytes: ack.writtenBytes, cumulativeBytes: current.offset,
      totalBytes: current.utf8Bytes.byteLength,
    });
    if (current.offset === current.utf8Bytes.byteLength) {
      current.state = "completed";
      this.report(current);
      this.queue.shift();
    }
    this.pump();
    return true;
  }

  handleError(error: InputError): boolean {
    const chunk = this.inFlight;
    if (!chunk || error.connectionGeneration !== chunk.connectionGeneration
      || error.inputSequence !== chunk.inputSequence || error.pasteId !== chunk.pasteId
      || error.chunkIndex !== chunk.chunkIndex) return false;
    this.clearAckTimer();
    this.retryChunk = chunk;
    this.inFlight = undefined;
    const current = this.queue[0];
    if (!current) return false;
    current.state = "failed";
    this.report(current, "PTYへの書込みに失敗しました");
    return true;
  }

  cancelCurrent(): void {
    const current = this.queue.shift();
    if (!current) return;
    this.clearAckTimer();
    this.inFlight = undefined;
    this.retryChunk = undefined;
    current.state = "cancelled";
    this.report(current);
    this.pump();
  }

  retryCurrent(): void {
    const current = this.queue[0];
    if (!current || current.state !== "failed") return;
    current.state = "queued";
    this.pump();
  }

  getState(): Record<string, unknown> {
    const current = this.queue[0];
    return {
      pasteId: current?.id,
      state: current?.state ?? "idle",
      acknowledgedBytes: current?.offset ?? 0,
      totalBytes: current?.utf8Bytes.byteLength ?? 0,
      queuedTransactions: this.queue.length,
      inFlight: this.inFlight ? { ...this.inFlight } : null,
      retryChunk: this.retryChunk ? { ...this.retryChunk } : null,
    };
  }

  dispose(): void {
    this.disposed = true;
    this.clearAckTimer();
    this.inFlight = undefined;
    this.retryChunk = undefined;
    this.queue = [];
  }

  private pump = (): void => {
    if (this.disposed || this.inFlight) return;
    const current = this.queue[0];
    if (!current) return;
    if (!this.options.canSend() || this.options.bufferedAmount() > BUFFERED_AMOUNT_HIGH_WATER) {
      current.state = "paused";
      this.report(current);
      return;
    }
    if (current.utf8Bytes.byteLength === 0) {
      current.state = "completed";
      this.report(current);
      this.queue.shift();
      this.pump();
      return;
    }
    const start = current.offset;
    const end = Math.min(start + PASTE_CHUNK_BYTES, current.utf8Bytes.byteLength);
    const chunkIndex = Math.floor(start / PASTE_CHUNK_BYTES);
    const connectionGeneration = this.options.connectionGeneration();
    const retry = this.retryChunk;
    const inputSequence = retry?.inputSequence ?? this.nextInputSequence++;
    const chunk: InFlightChunk = retry
      ? { ...retry, connectionGeneration }
      : { inputSequence, pasteId: current.id, chunkIndex, start, end,
        final: end === current.utf8Bytes.byteLength, connectionGeneration };
    const bytes = current.utf8Bytes.slice(start, end);
    const sent = this.options.sendFrame({
      type: "input",
      inputSequence,
      pasteId: current.id,
      chunkIndex,
      final: chunk.final,
      byteLength: bytes.byteLength,
      connectionGeneration,
    }, bytes);
    if (!sent) {
      this.retryChunk = chunk;
      current.state = "paused";
      this.report(current);
      return;
    }
    this.retryChunk = undefined;
    this.inFlight = chunk;
    current.state = "sending";
    this.record("paste-chunk-sent", { ...chunk, byteLength: bytes.byteLength });
    this.report(current);
    this.ackTimer = window.setTimeout(() => {
      if (this.inFlight?.inputSequence !== inputSequence) return;
      this.inFlight = undefined;
      this.retryChunk = chunk;
      current.state = "failed";
      this.record("paste-chunk-failed", { ...chunk, reason: "ack-timeout" });
      this.report(current, "ACKがタイムアウトしました");
    }, ACK_TIMEOUT_MS);
  };

  private clearAckTimer(): void {
    if (this.ackTimer !== undefined) window.clearTimeout(this.ackTimer);
    this.ackTimer = undefined;
  }

  private report(transaction?: PasteTransaction, error?: string): void {
    if (!transaction) {
      this.options.onProgress(null);
      return;
    }
    this.options.onProgress({
      id: transaction.id,
      state: transaction.state,
      acknowledgedBytes: transaction.offset,
      totalBytes: transaction.utf8Bytes.byteLength,
      error,
    });
  }

  private record(event: string, details: Record<string, unknown>): void {
    this.options.record?.(event, details);
  }
}
