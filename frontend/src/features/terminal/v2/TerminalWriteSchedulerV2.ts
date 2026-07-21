import type { Terminal } from "@xterm/xterm";

const WRITE_SLICE_BYTES = 32 * 1024;

/**
 * V2の受信順序境界。大きなsnapshotをxtermへ一括投入せず、
 * parser callbackごとにbrowser taskへ制御を返す。
 */
export class TerminalWriteSchedulerV2 {
  private tail: Promise<void> = Promise.resolve();
  private disposed = false;

  constructor(private readonly terminal: Terminal) {}

  write(data: string | Uint8Array, onComplete?: () => void): void {
    const slices = typeof data === "string"
      ? [data]
      : Array.from(
          { length: Math.max(1, Math.ceil(data.byteLength / WRITE_SLICE_BYTES)) },
          (_, index) => data.slice(index * WRITE_SLICE_BYTES, (index + 1) * WRITE_SLICE_BYTES),
        );
    this.enqueue(async () => {
      for (let index = 0; index < slices.length; index += 1) {
        await new Promise<void>((resolve) => this.terminal.write(slices[index], resolve));
        if (index + 1 < slices.length) {
          await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
        }
      }
      onComplete?.();
    });
  }

  reset(): void {
    this.enqueue(() => this.terminal.reset());
  }

  task(callback: () => void | Promise<void>): void {
    this.enqueue(callback);
  }

  async drain(): Promise<void> {
    await this.tail;
  }

  dispose(): void {
    this.disposed = true;
  }

  private enqueue(callback: () => void | Promise<void>): void {
    this.tail = this.tail.then(async () => {
      if (!this.disposed) await callback();
    }).catch((error: unknown) => {
      console.error("[terminal-v2-write]", error);
    });
  }
}
