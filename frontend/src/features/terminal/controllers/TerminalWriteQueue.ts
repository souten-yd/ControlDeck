import type { Terminal } from "@xterm/xterm";

/** xterm parserへwrite/reset/resizeを受信順で渡し、task失敗後もqueueを継続する。 */
export class TerminalWriteQueue {
  private tail: Promise<void> = Promise.resolve();
  private disposed = false;

  constructor(
    private readonly terminal: Terminal,
    private readonly debug = false,
  ) {}

  enqueueWrite(data: string | Uint8Array): void {
    this.enqueueTask(
      () => new Promise<void>((resolve) => this.terminal.write(data, resolve)),
      "write",
    );
  }

  enqueueTask(task: () => void | Promise<void>, reason = "task"): void {
    this.tail = this.tail
      .then(async () => {
        if (this.disposed) return;
        await task();
      })
      .catch((error: unknown) => {
        // queueをreject状態のまま停止させない。通常時も実装errorは隠さない。
        console.error("[terminal-queue]", reason, error);
      });
  }

  enqueueReset(): void {
    this.enqueueTask(() => this.terminal.reset(), "reset");
  }

  async drain(): Promise<void> {
    await this.tail;
  }

  dispose(): void {
    this.disposed = true;
    if (this.debug) console.debug("[terminal-queue] disposed");
  }
}
