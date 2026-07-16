import { expect, test } from "@playwright/test";
import {
  prepareTerminalPaste,
  TerminalInputController,
  type InputAck,
} from "../src/features/terminal/controllers/TerminalInputController";

Object.defineProperty(globalThis, "window", { configurable: true, value: globalThis });

test("keeps and ACKs a 300KB UTF-8 paste without queue limits", () => {
  let live = false;
  let generation = 1;
  const frames: { control: Record<string, unknown>; bytes: Uint8Array }[] = [];
  const controller = new TerminalInputController({
    canSend: () => live,
    connectionGeneration: () => generation,
    bufferedAmount: () => 0,
    sendFrame: (control, bytes) => {
      frames.push({ control, bytes });
      return true;
    },
    onProgress: () => undefined,
  });
  const text = `先頭😀${"日本語🌸".repeat(30_000)}末尾🧑‍💻`;
  const normalized = prepareTerminalPaste(text, false);
  const expected = new TextEncoder().encode(normalized);
  controller.enqueuePaste(text, normalized);
  expect(controller.getState()).toMatchObject({ state: "paused", totalBytes: expected.byteLength });

  live = true;
  controller.availabilityChanged();
  const received: number[] = [];
  while (frames.length) {
    const frame = frames.shift()!;
    received.push(...frame.bytes);
    const control = frame.control as unknown as InputAck & { byteLength: number };
    expect(frame.bytes.byteLength).toBeLessThanOrEqual(8192);
    controller.handleAck({
      type: "input_ack",
      inputSequence: Number(control.inputSequence),
      pasteId: Number(control.pasteId),
      chunkIndex: Number(control.chunkIndex),
      writtenBytes: frame.bytes.byteLength,
      connectionGeneration: generation,
    });
  }
  expect(new Uint8Array(received)).toEqual(expected);
  expect(controller.getState().state).toBe("idle");
});

test("reconnect resends the same unACKed sequence and ignores stale ACK", () => {
  let generation = 1;
  const frames: { control: Record<string, unknown>; bytes: Uint8Array }[] = [];
  const controller = new TerminalInputController({
    canSend: () => true,
    connectionGeneration: () => generation,
    bufferedAmount: () => 0,
    sendFrame: (control, bytes) => { frames.push({ control, bytes }); return true; },
    onProgress: () => undefined,
  });
  controller.enqueuePaste("payload", "payload");
  const first = frames.shift()!;
  generation = 2;
  controller.connectionChanged();
  const resent = frames.shift()!;
  expect(resent.control.inputSequence).toBe(first.control.inputSequence);
  expect(resent.bytes).toEqual(first.bytes);
  expect(controller.handleAck({
    type: "input_ack", inputSequence: Number(first.control.inputSequence), pasteId: 1,
    chunkIndex: 0, writtenBytes: first.bytes.byteLength, connectionGeneration: 1,
  })).toBe(false);
  expect(controller.handleAck({
    type: "input_ack", inputSequence: Number(resent.control.inputSequence), pasteId: 1,
    chunkIndex: 0, writtenBytes: resent.bytes.byteLength, connectionGeneration: 2,
  })).toBe(true);
  expect(controller.getState().state).toBe("idle");
});

test("matches xterm newline and bracketed paste semantics", () => {
  expect(prepareTerminalPaste("a\r\nb\nc", false)).toBe("a\rb\rc");
  expect(prepareTerminalPaste("a\n", true)).toBe("\x1b[200~a\r\x1b[201~");
});
