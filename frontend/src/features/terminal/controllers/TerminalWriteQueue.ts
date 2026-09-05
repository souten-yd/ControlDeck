import type { Terminal } from "@xterm/xterm";

const WRITE_SLICE_BYTES = 32 * 1024;

/** xterm parserへwrite/reset/resizeを受信順で渡し、task失敗後もqueueを継続する。 */
export class TerminalWriteQueue {
  private tail: Promise<void> = Promise.resolve();
  private disposed = false;

  constructor(
    private readonly terminal: Terminal,
    private readonly debug = false,
  ) {}

  enqueueWrite(data: string | Uint8Array, onComplete?: () => void): void {
    // 大きな snapshot を一括で渡すと、parser が回りきるまで main thread が戻らず
    // 画面が固まる。再接続時の履歴はまさにそれで、待っている間の描画が飛ぶ。
    // 32KiB ずつに割って、境目で browser へ制御を返す（V2 の scheduler と同じ）。
    const slices: (string | Uint8Array)[] =
      typeof data === "string" || data.byteLength <= WRITE_SLICE_BYTES
        ? [data]
        : Array.from(
            { length: Math.ceil(data.byteLength / WRITE_SLICE_BYTES) },
            (_, index) => data.slice(index * WRITE_SLICE_BYTES, (index + 1) * WRITE_SLICE_BYTES),
          );
    this.enqueueTask(
      async () => {
        for (let index = 0; index < slices.length; index += 1) {
          await new Promise<void>((resolve) => this.terminal.write(slices[index], resolve));
          if (index + 1 < slices.length) {
            await new Promise<void>((resolve) => window.setTimeout(resolve, 0));
          }
        }
        onComplete?.();
      },
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
