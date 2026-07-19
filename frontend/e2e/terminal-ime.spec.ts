import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

type PerfCounters = {
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
};

type ResizeAck = {
  type: "resize_ack";
  cols: number;
  rows: number;
  resizeGeneration: number;
  connectionGeneration: number;
  success: boolean;
};

declare global {
  interface Window {
    __controlDeckTerminalTest?: {
      invalidate: (type: "size" | "position" | "renderer" | "connection", reason: string) => void;
      counters: () => PerfCounters;
      resetCounters: () => void;
      isGeometryLocked: () => boolean;
      textareaCount: () => number;
      rows: () => number;
      cols: () => number;
      viewportY: () => number;
      baseY: () => number;
      resizeBarrierState: () => {
        active: boolean;
        acked: boolean;
        queuedChunks: number;
        counters: {
          started: number;
          ackAccepted: number;
          ackIgnored: number;
          inputQueued: number;
          inputReleased: number;
          timeoutReleased: number;
          maxQueuedChunks: number;
        };
      };
      resizeBarrierLog: () => readonly Record<string, unknown>[];
      terminalLog: () => readonly Record<string, unknown>[];
      captureRenderState: () => {
        visibleBufferRows: string[];
        domRows: { text: string }[];
        mismatchedRows: number[];
        textareaCount: number;
      };
      startBarrierForTest: (generation: number, cols: number, rows: number) => boolean;
      ackBarrierForTest: (ack: ResizeAck) => boolean;
      enqueuePtyFrameForTest: (data: string) => boolean;
      writeForTest: (data: string) => Promise<void>;
      sendInputForTest: (data: string) => void;
      enqueuePasteForTest: (text: string) => void;
      pasteState: () => { state: string; acknowledgedBytes: number; totalBytes: number };
      resetBarrierForTest: () => void;
      connectionGeneration: () => number;
      clientInstanceId: () => string;
      connectionState: () => {
        state: string;
        connectionGeneration: number;
        lastSequence: number;
      };
      connectionLog: () => readonly Record<string, unknown>[];
      historyReplayCounters: () => {
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
      };
      closeWebSocketForTest: () => void;
      setLastSequenceForTest: (sequence: number) => void;
      controllerListenerCount: number;
    };
  }
}

async function openTerminal(page: Page): Promise<void> {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.addInitScript(() => {
    localStorage.setItem("control-deck:terminal-geometry-debug", "1");
  });
  await page.goto("/terminal");
  await expect(page.getByLabel("ユーザー名")).toBeVisible();
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/terminal");
  await expect(page.getByRole("heading", { name: "Terminal" })).toBeVisible();
  await page.getByRole("button", { name: "新規セッション" }).click();
  await expect(page.locator("[data-terminal-root]")).toBeVisible();
  await expect(page.locator(".xterm-helper-textarea")).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => Boolean(window.__controlDeckTerminalTest))).toBe(true);
  await page.waitForTimeout(250);
}

test("mounts over HTTP when crypto.randomUUID is unavailable", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.addInitScript(() => {
    Object.defineProperty(globalThis.crypto, "randomUUID", {
      configurable: true,
      value: undefined,
    });
  });
  await page.setViewportSize({ width: 320, height: 700 });
  await openTerminal(page);

  const initialId = await page.evaluate(() => window.__controlDeckTerminalTest!.clientInstanceId());
  expect(initialId).toMatch(/^[A-Za-z0-9_-]{16,80}$/);
  await page.evaluate(() => window.__controlDeckTerminalTest!.closeWebSocketForTest());
  await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.connectionState().state), {
    timeout: 5_000,
  }).toBe("LIVE");
  expect(await page.evaluate(() => window.__controlDeckTerminalTest!.clientInstanceId())).toBe(initialId);
  expect(pageErrors).toEqual([]);

  const sessionId = await page.locator("[data-terminal-header] select").inputValue();
  await page.context().request.delete(`/api/v1/terminals/${sessionId}`, {
    headers: { "X-Requested-With": "ControlDeck" },
  });
});

const counters = (page: Page) => page.evaluate(() => ({ ...window.__controlDeckTerminalTest!.counters() }));

test.describe("terminal mobile IME and geometry", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 700 });
    await openTerminal(page);
  });

  test.afterEach(async ({ page }) => {
    const sessionId = await page.locator("[data-terminal-header] select").inputValue().catch(() => "");
    if (sessionId) {
      await page.context().request.delete(`/api/v1/terminals/${sessionId}`, {
        headers: { "X-Requested-With": "ControlDeck" },
      });
    }
  });

  test("shows the close action at the right edge with a 44px touch target", async ({ page }) => {
    const layout = await page.evaluate(() => {
      const header = document.querySelector<HTMLElement>("[data-terminal-header]")!.getBoundingClientRect();
      const close = document.querySelector<HTMLElement>('[aria-label="ターミナルを閉じる"]')!.getBoundingClientRect();
      return { header: header.toJSON(), close: close.toJSON(), viewport: window.innerWidth };
    });
    expect(layout.close.width).toBeGreaterThanOrEqual(44);
    expect(layout.close.height).toBeGreaterThanOrEqual(44);
    expect(layout.close.right).toBeLessThanOrEqual(layout.viewport - 12);
    expect(layout.header.right - layout.close.right).toBe(12);
  });

  test("jumps and drags with the overlay history bar without opening terminal input", async ({ page }) => {
    const textarea = page.locator(".xterm-helper-textarea");
    await page.evaluate(async () => {
      const rows = Array.from({ length: 120 }, (_, index) => `TOUCH_HISTORY_${index + 1}`).join("\r\n");
      await window.__controlDeckTerminalTest!.writeForTest(`${rows}\r\n`);
    });
    await expect(page.locator(".xterm-rows")).toContainText("TOUCH_HISTORY_120");
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.baseY())).toBeGreaterThan(40);
    await textarea.evaluate((node: HTMLTextAreaElement) => node.blur());
    await expect.poll(() => page.evaluate(() => document.activeElement?.classList.contains("xterm-helper-textarea")))
      .toBe(false);

    const track = page.locator("[data-terminal-history-track]");
    await expect(track).toHaveCSS("pointer-events", "auto");
    const baseY = await page.evaluate(() => window.__controlDeckTerminalTest!.baseY());

    // track tapは対応する履歴位置へjumpする。
    await track.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const point = new Touch({
        identifier: 1,
        target: node,
        clientX: rect.right - 2,
        clientY: rect.top + rect.height * 0.25,
      });
      node.dispatchEvent(new TouchEvent("touchstart", {
        bubbles: true,
        cancelable: true,
        touches: [point],
        targetTouches: [point],
        changedTouches: [point],
      }));
      node.dispatchEvent(new TouchEvent("touchend", {
        bubbles: true,
        cancelable: true,
        touches: [],
        targetTouches: [],
        changedTouches: [point],
      }));
    });
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.viewportY()))
      .toBeLessThan(baseY / 2);
    expect(await page.evaluate(() => document.activeElement?.classList.contains("xterm-helper-textarea")))
      .toBe(false);

    // drag中は指位置へ連続追従し、下側へ移動できる。
    await track.evaluate((node) => {
      const rect = node.getBoundingClientRect();
      const start = new Touch({
        identifier: 2,
        target: node,
        clientX: rect.right - 2,
        clientY: rect.top + rect.height * 0.25,
      });
      const moved = new Touch({
        identifier: 2,
        target: node,
        clientX: rect.right - 2,
        clientY: rect.top + rect.height * 0.8,
      });
      node.dispatchEvent(new TouchEvent("touchstart", {
        bubbles: true,
        cancelable: true,
        touches: [start],
        targetTouches: [start],
        changedTouches: [start],
      }));
      node.dispatchEvent(new TouchEvent("touchmove", {
        bubbles: true,
        cancelable: true,
        touches: [moved],
        targetTouches: [moved],
        changedTouches: [moved],
      }));
      node.dispatchEvent(new TouchEvent("touchend", {
        bubbles: true,
        cancelable: true,
        touches: [],
        targetTouches: [],
        changedTouches: [moved],
      }));
    });
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.viewportY()))
      .toBeGreaterThan(baseY / 2);
    await expect(track).toHaveAttribute("data-active", "false");
    expect(await page.evaluate(() => document.activeElement?.classList.contains("xterm-helper-textarea")))
      .toBe(false);
    const overlayLayout = await page.evaluate(() => {
      const host = document.querySelector<HTMLElement>("[data-terminal-host]")!.getBoundingClientRect();
      const screen = document.querySelector<HTMLElement>(".xterm-screen")!.getBoundingClientRect();
      const track = document.querySelector<HTMLElement>("[data-terminal-history-track]")!;
      const nativeScrollbar = document.querySelector<HTMLElement>(
        ".terminal-xterm-host .xterm-scrollable-element > .scrollbar.vertical",
      );
      return {
        unusedRightWidth: host.right - screen.right,
        trackPosition: getComputedStyle(track).position,
        nativeScrollbarDisplay: nativeScrollbar ? getComputedStyle(nativeScrollbar).display : "missing",
      };
    });
    expect(overlayLayout.unusedRightWidth).toBeLessThan(12);
    expect(overlayLayout.trackPosition).toBe("absolute");
    expect(overlayLayout.nativeScrollbarDisplay).toBe("none");

    // overlay以外の通常tapは従来どおり入力へfocusできる。
    await page.locator("[data-terminal-host]").evaluate((host) => {
      const rect = host.getBoundingClientRect();
      const point = new Touch({
        identifier: 3,
        target: host,
        clientX: rect.left + rect.width / 2,
        clientY: rect.top + rect.height / 2,
      });
      host.dispatchEvent(new TouchEvent("touchstart", {
        bubbles: true,
        cancelable: true,
        touches: [point],
        targetTouches: [point],
        changedTouches: [point],
      }));
      host.dispatchEvent(new TouchEvent("touchend", {
        bubbles: true,
        cancelable: true,
        touches: [],
        targetTouches: [],
        changedTouches: [point],
      }));
    });
    await expect.poll(() => page.evaluate(() => document.activeElement?.classList.contains("xterm-helper-textarea")))
      .toBe(true);
  });

  test("delivers 100KB, 300KB and UTF-8 paste without loss", async ({ page }) => {
    test.setTimeout(90_000);
    const textarea = page.locator(".xterm-helper-textarea");
    const verify = async (payload: string, disruption?: "resize" | "reconnect") => {
      const normalized = payload.replace(/\r?\n/g, "\r");
      const bytes = Buffer.from(normalized);
      const hash = createHash("sha256").update(bytes).digest("hex");
      const command = `printf "\\033[?2004l"; python3 -c "import sys,tty,hashlib; tty.setraw(0); n=int(sys.stdin.buffer.read(10)); d=sys.stdin.buffer.read(n); print(chr(13)+chr(10)+'PASTE_RESULT:'+str(len(d))+':'+hashlib.sha256(d).hexdigest(),flush=True)"`;
      await textarea.pressSequentially(command, { delay: 1 });
      await textarea.press("Enter");
      await page.waitForTimeout(150);
      const barrier = disruption === "resize" ? await page.evaluate(() => {
        const hook = window.__controlDeckTerminalTest!;
        const resizeGeneration = 910_000;
        return { resizeGeneration, connectionGeneration: hook.connectionGeneration(),
          cols: hook.cols(), rows: hook.rows(),
          started: hook.startBarrierForTest(resizeGeneration, hook.cols(), hook.rows()) };
      }) : null;
      expect(barrier?.started ?? true).toBe(true);
      await page.evaluate(({ text, length }) => {
        window.__controlDeckTerminalTest!.enqueuePasteForTest(String(length).padStart(10, "0") + text);
      }, { text: normalized, length: bytes.length });
      if (disruption === "resize" && barrier) {
        for (let cycle = 0; cycle < 10; cycle += 1) {
          await page.setViewportSize({ width: 320, height: 430 });
          await page.setViewportSize({ width: 320, height: 700 });
        }
        await page.evaluate((value) => {
          const hook = window.__controlDeckTerminalTest!;
          hook.ackBarrierForTest({ type: "resize_ack", success: true, ...value });
          hook.enqueuePtyFrameForTest("\r");
        }, barrier);
      } else if (disruption === "reconnect") {
        await page.evaluate(() => window.__controlDeckTerminalTest!.closeWebSocketForTest());
        await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.connectionState().state), {
          timeout: 10_000,
        }).toBe("LIVE");
      }
      await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.pasteState().state), {
        timeout: 30_000,
      }).toBe("idle");
      await expect(page.locator(".xterm-rows")).toContainText(`PASTE_RESULT:${bytes.length}:${hash}`, { timeout: 15_000 });
    };
    await verify("ASCII_START_" + "a".repeat(100 * 1024) + "_ASCII_END", "resize");
    await verify("OVER_LIMIT_START_" + "b".repeat(300 * 1024) + "_OVER_LIMIT_END", "reconnect");
    await verify("日本語😀🧑‍💻_START_" + "あいうえお🌸".repeat(5_500) + "_UTF8_END");
  });

  test("coalesces keyboard geometry event bursts", async ({ page }) => {
    await page.evaluate(() => {
      const hook = window.__controlDeckTerminalTest!;
      hook.resetCounters();
      for (let i = 0; i < 10; i += 1) hook.invalidate("size", `visual-${i}`);
      for (let i = 0; i < 10; i += 1) hook.invalidate("size", `observer-${i}`);
      for (let i = 0; i < 5; i += 1) hook.invalidate("size", `window-${i}`);
      for (let i = 0; i < 10; i += 1) hook.invalidate("position", `scroll-${i}`);
    });
    await page.setViewportSize({ width: 320, height: 430 });
    await page.waitForTimeout(250);
    const result = await counters(page);
    const barrier = await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState());
    const acceptedAck = await page.evaluate(() => [...window.__controlDeckTerminalTest!.resizeBarrierLog()]
      .reverse().find((entry) => entry.event === "resize-ack-accepted"));
    const sizeProbe = await page.evaluate(() => [...window.__controlDeckTerminalTest!.terminalLog()]
      .reverse().find((entry) => entry.event === "size-probe-result"));
    const finalSize = await page.evaluate(() => ({
      rows: window.__controlDeckTerminalTest!.rows(),
      cols: window.__controlDeckTerminalTest!.cols(),
    }));
    if (process.env.CONTROL_DECK_E2E_REPORT === "1") {
      console.log("AP1_RESULT", JSON.stringify({ ...result, acceptedAck, sizeProbe }));
    }
    expect(result.fitRequested).toBeGreaterThanOrEqual(25);
    expect(result.fitExecuted).toBeLessThanOrEqual(1);
    expect(result.resizeExecuted).toBeLessThanOrEqual(1);
    expect(result.ptyResizeSent).toBeLessThanOrEqual(1);
    expect(result.refreshExecuted).toBe(0);
    expect(result.maxGeometryTasksPending).toBeLessThanOrEqual(1);
    expect(barrier.counters.started).toBe(1);
    expect(barrier.counters.ackAccepted).toBe(1);
    expect(acceptedAck).toBeTruthy();
    const diagnostics = acceptedAck?.diagnostics as { ptyRows?: number; ptyCols?: number } | undefined;
    expect(diagnostics?.ptyRows).toBe(finalSize.rows);
    expect(diagnostics?.ptyCols).toBe(finalSize.cols);
    const finalDiagnostics = sizeProbe?.diagnostics as {
      ptyRows?: number;
      ptyCols?: number;
      tmuxWindow?: string;
      tmuxClients?: string;
    } | undefined;
    expect(finalDiagnostics?.ptyRows).toBe(finalSize.rows);
    expect(finalDiagnostics?.ptyCols).toBe(finalSize.cols);
    expect(finalDiagnostics?.tmuxWindow).toBe(`${finalSize.cols}x${finalSize.rows}`);
    expect(finalDiagnostics?.tmuxClients).toContain(`${finalSize.cols}x${finalSize.rows}`);
  });

  test("keyboard open and close ten times keeps one websocket and no history replay", async ({ page }) => {
    const before = await page.evaluate(() => window.__controlDeckTerminalTest!.historyReplayCounters());
    for (let cycle = 0; cycle < 10; cycle += 1) {
      await page.setViewportSize({ width: 320, height: 430 });
      await page.waitForTimeout(90);
      await page.setViewportSize({ width: 320, height: 700 });
      await page.waitForTimeout(90);
    }
    const after = await page.evaluate(() => ({
      history: window.__controlDeckTerminalTest!.historyReplayCounters(),
      connection: window.__controlDeckTerminalTest!.connectionState(),
      xterms: document.querySelectorAll(".xterm").length,
      textareas: document.querySelectorAll(".xterm-helper-textarea").length,
    }));
    expect(after.history.websocketCreated).toBe(before.websocketCreated);
    expect(after.history.websocketClosed).toBe(before.websocketClosed);
    expect(after.history.historyReset).toBe(before.historyReset);
    expect(after.history.replayBytes).toBe(before.replayBytes);
    expect(after.connection.state).toBe("LIVE");
    expect(after.xterms).toBe(1);
    expect(after.textareas).toBe(1);
  });

  test("reconnect resumes only missing journal output without resetting scrollback", async ({ page }) => {
    const textarea = page.locator(".xterm-helper-textarea");
    await textarea.pressSequentially("printf 'RESUME-BEFORE\\n'; (sleep 0.2; printf 'RESUME-DURING\\n') &", { delay: 1 });
    await textarea.press("Enter");
    await expect(page.locator(".xterm-rows")).toContainText("RESUME-BEFORE");
    const before = await page.evaluate(() => window.__controlDeckTerminalTest!.historyReplayCounters());
    await page.evaluate(() => window.__controlDeckTerminalTest!.closeWebSocketForTest());
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.connectionState().state), {
      timeout: 5_000,
    }).toBe("LIVE");
    await expect(page.locator(".xterm-rows")).toContainText("RESUME-DURING");
    const after = await page.evaluate(() => ({
      history: window.__controlDeckTerminalTest!.historyReplayCounters(),
      bufferText: window.__controlDeckTerminalTest!.captureRenderState().visibleBufferRows.join("\n"),
    }));
    expect(after.history.websocketCreated).toBe(before.websocketCreated + 1);
    expect(after.history.resumeReady).toBe(before.resumeReady + 1);
    expect(after.history.historyReset).toBe(before.historyReset);
    expect(after.history.replayBytes).toBe(before.replayBytes);
    expect(after.bufferText.match(/RESUME-BEFORE/g)?.length).toBe(1);
    expect(after.bufferText.match(/RESUME-DURING/g)?.length).toBe(1);
  });

  test("journal range miss falls back to one bounded reset", async ({ page }) => {
    const before = await page.evaluate(() => window.__controlDeckTerminalTest!.historyReplayCounters());
    await page.evaluate(() => {
      window.__controlDeckTerminalTest!.setLastSequenceForTest(Number.MAX_SAFE_INTEGER - 1);
      window.__controlDeckTerminalTest!.closeWebSocketForTest();
    });
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.connectionState().state), {
      timeout: 5_000,
    }).toBe("LIVE");
    const after = await page.evaluate(() => window.__controlDeckTerminalTest!.historyReplayCounters());
    expect(after.resumeResetRequired).toBe(before.resumeResetRequired + 1);
    expect(after.historyReset).toBe(before.historyReset + 1);
    expect(after.historyEnd).toBe(before.historyEnd + 1);
  });

  test("full page reload creates a new client and performs initial replay", async ({ page }) => {
    const sessionId = await page.locator("[data-terminal-header] select").inputValue();
    await page.reload();
    const sessionRow = page.locator("li").filter({ hasText: `cdterm-${sessionId}` });
    await expect(sessionRow).toBeVisible();
    await sessionRow.getByRole("button", { name: "接続", exact: true }).click();
    await expect(page.locator("[data-terminal-root]")).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest?.connectionState().state)).toBe("LIVE");
    const countersAfterReload = await page.evaluate(() => window.__controlDeckTerminalTest!.historyReplayCounters());
    expect(countersAfterReload.websocketCreated).toBe(1);
    expect(countersAfterReload.historyReset).toBe(1);
    expect(countersAfterReload.historyEnd).toBe(1);
  });

  test("session switch never mixes the previous terminal history", async ({ page }) => {
    const firstSessionId = await page.locator("[data-terminal-header] select").inputValue();
    const textarea = page.locator(".xterm-helper-textarea");
    await textarea.pressSequentially("echo FIRST_SESSION_ONLY", { delay: 1 });
    await textarea.press("Enter");
    await expect(page.locator(".xterm-rows")).toContainText("FIRST_SESSION_ONLY");
    const created = await page.context().request.post("/api/v1/terminals", {
      headers: { "X-Requested-With": "ControlDeck" },
    });
    expect(created.ok()).toBe(true);
    const secondSession = await created.json() as { id: string };
    await page.reload();
    const secondRow = page.locator("li").filter({ hasText: `cdterm-${secondSession.id}` });
    await secondRow.getByRole("button", { name: "接続", exact: true }).click();
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest?.connectionState().state)).toBe("LIVE");
    const secondBuffer = await page.evaluate(() =>
      window.__controlDeckTerminalTest!.captureRenderState().visibleBufferRows.join("\n"));
    expect(secondBuffer).not.toContain("FIRST_SESSION_ONLY");
    await page.context().request.delete(`/api/v1/terminals/${firstSessionId}`, {
      headers: { "X-Requested-With": "ControlDeck" },
    });
  });

  test("blocks geometry during composition and flushes once", async ({ page }) => {
    const textarea = page.locator(".xterm-helper-textarea");
    const rootBefore = await page.locator("[data-terminal-root]").evaluate((node) => node.getAttribute("style"));
    await page.evaluate(() => window.__controlDeckTerminalTest!.resetCounters());
    await textarea.dispatchEvent("compositionstart", { data: "こ" });
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.isGeometryLocked())).toBe(true);
    await page.evaluate(() => {
      const hook = window.__controlDeckTerminalTest!;
      for (let i = 0; i < 100; i += 1) {
        hook.invalidate(i % 4 === 0 ? "position" : "size", `composition-${i}`);
      }
    });
    await page.setViewportSize({ width: 320, height: 440 });
    await page.waitForTimeout(200);
    const during = await counters(page);
    if (process.env.CONTROL_DECK_E2E_REPORT === "1") console.log("AP2_DURING", JSON.stringify(during));
    expect(during.fitExecuted).toBe(0);
    expect(during.resizeExecuted).toBe(0);
    expect(during.refreshExecuted).toBe(0);
    expect(during.ptyResizeSent).toBe(0);
    expect(during.maxGeometryTasksPending).toBeLessThanOrEqual(1);
    expect(await page.locator("[data-terminal-root]").evaluate((node) => node.getAttribute("style"))).toBe(rootBefore);

    await textarea.dispatchEvent("compositionend", { data: "こんにちは" });
    await page.waitForTimeout(300);
    const after = await counters(page);
    if (process.env.CONTROL_DECK_E2E_REPORT === "1") console.log("AP2_AFTER", JSON.stringify(after));
    expect(after.fitExecuted).toBe(1);
    expect(after.resizeExecuted).toBe(1);
    expect(after.ptyResizeSent).toBeLessThanOrEqual(1);
    expect(after.refreshExecuted).toBe(0);
    expect(await page.evaluate(() => window.__controlDeckTerminalTest!.textareaCount())).toBe(1);
    const textareaLayout = await page.evaluate(() => {
      const textarea = document.querySelector<HTMLTextAreaElement>(".xterm-helper-textarea")!;
      const textareaRect = textarea.getBoundingClientRect();
      const hostRect = document.querySelector<HTMLElement>("[data-terminal-host]")!.getBoundingClientRect();
      const style = getComputedStyle(textarea);
      return {
        position: style.position,
        transform: style.transform,
        insideHost: textareaRect.left >= hostRect.left - 1
          && textareaRect.right <= hostRect.right + 1
          && textareaRect.top >= hostRect.top - 1
          && textareaRect.bottom <= hostRect.bottom + 1,
      };
    });
    // SIGWINCH後のcursor moveによるxterm標準同期は許可し、ControlDeck独自のfixed/transformは使わない。
    expect(textareaLayout.position).toBe("absolute");
    expect(textareaLayout.transform).toBe("none");
    expect(textareaLayout.insideHost).toBe(true);
  });

  test("holds FIFO input until matching ACK and the following PTY write complete", async ({ page }) => {
    const setup = await page.evaluate(() => {
      const hook = window.__controlDeckTerminalTest!;
      const cols = hook.cols();
      const rows = hook.rows();
      const connectionGeneration = hook.connectionGeneration();
      const resizeGeneration = 900_001;
      return {
        cols,
        rows,
        connectionGeneration,
        resizeGeneration,
        started: hook.startBarrierForTest(resizeGeneration, cols, rows),
      };
    });
    expect(setup.started).toBe(true);
    await page.evaluate(() => {
      const hook = window.__controlDeckTerminalTest!;
      hook.sendInputForTest("printf '");
      hook.sendInputForTest("BARRIER_😀_ORDER_OK\\n'");
      hook.sendInputForTest("\r");
    });
    expect((await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState())).queuedChunks).toBe(3);

    const oldAccepted = await page.evaluate((values) => window.__controlDeckTerminalTest!.ackBarrierForTest({
      type: "resize_ack",
      cols: values.cols,
      rows: values.rows,
      resizeGeneration: values.resizeGeneration - 1,
      connectionGeneration: values.connectionGeneration,
      success: true,
    }), setup);
    expect(oldAccepted).toBe(false);
    expect((await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState())).active).toBe(true);

    const matchingAccepted = await page.evaluate((values) => window.__controlDeckTerminalTest!.ackBarrierForTest({
      type: "resize_ack",
      cols: values.cols,
      rows: values.rows,
      resizeGeneration: values.resizeGeneration,
      connectionGeneration: values.connectionGeneration,
      success: true,
    }), setup);
    expect(matchingAccepted).toBe(true);
    expect((await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState())).acked).toBe(true);
    await page.waitForTimeout(20);
    expect((await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState())).active).toBe(true);

    expect(await page.evaluate(() => window.__controlDeckTerminalTest!.enqueuePtyFrameForTest("\r"))).toBe(true);
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState().active)).toBe(false);
    await expect(page.locator(".xterm-rows")).toContainText("BARRIER_😀_ORDER_OK");
    const finalState = await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState());
    expect(finalState.counters.ackIgnored).toBeGreaterThanOrEqual(1);
    expect(finalState.counters.inputReleased).toBeGreaterThanOrEqual(3);
    expect(finalState.counters.maxQueuedChunks).toBeGreaterThanOrEqual(3);
  });

  test("requeues pending resize input on connection generation reset", async ({ page }) => {
    await page.evaluate(() => {
      const hook = window.__controlDeckTerminalTest!;
      hook.startBarrierForTest(900_002, hook.cols(), hook.rows());
      hook.sendInputForTest("echo REQUEUED_BARRIER_INPUT_OK\r");
      hook.resetBarrierForTest();
    });
    const state = await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState());
    expect(state.active).toBe(false);
    expect(state.queuedChunks).toBe(0);
    await expect(page.locator(".xterm-rows")).toContainText("REQUEUED_BARRIER_INPUT_OK");
  });

  test("does not create a resize barrier for position-only or unchanged geometry", async ({ page }) => {
    const before = await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState().counters.started);
    await page.evaluate(() => {
      const hook = window.__controlDeckTerminalTest!;
      hook.resetCounters();
      for (let index = 0; index < 20; index += 1) hook.invalidate("position", `position-only-${index}`);
      hook.invalidate("size", "same-size");
    });
    await page.waitForTimeout(250);
    const after = await page.evaluate(() => ({
      barrier: window.__controlDeckTerminalTest!.resizeBarrierState(),
      perf: window.__controlDeckTerminalTest!.counters(),
    }));
    expect(after.barrier.counters.started).toBe(before);
    expect(after.perf.resizeExecuted).toBe(0);
    expect(after.perf.ptyResizeSent).toBe(0);
  });

  test("keeps one placeholder in matching buffer and DOM rows", async ({ page }) => {
    const textarea = page.locator(".xterm-helper-textarea");
    await textarea.pressSequentially("printf 'Write tests for @filename\\n'", { delay: 1 });
    await textarea.press("Enter");
    await expect(page.locator(".xterm-rows")).toContainText("Write tests for @filename");
    await page.waitForTimeout(100);
    const snapshot = await page.evaluate(() => window.__controlDeckTerminalTest!.captureRenderState());
    const bufferCount = snapshot.visibleBufferRows.filter((row) => row.includes("Write tests for @filename")).length;
    const domCount = snapshot.domRows.filter((row) => row.text.includes("Write tests for @filename")).length;
    expect(bufferCount).toBe(1);
    expect(domCount).toBe(1);
    expect(snapshot.mismatchedRows).toEqual([]);
    expect(snapshot.textareaCount).toBe(1);
    expect(await page.locator(".xterm").count()).toBe(1);
  });

  test("keeps terminal screen above the single-line helper bar", async ({ page }) => {
    const layout = await page.evaluate(() => {
      const rect = (selector: string) => document.querySelector<HTMLElement>(selector)!.getBoundingClientRect();
      const root = rect("[data-terminal-root]");
      const header = rect("[data-terminal-header]");
      const body = rect("[data-terminal-body]");
      const host = rect("[data-terminal-host]");
      const helper = rect("[data-terminal-helper]");
      const screen = rect(".xterm-screen");
      const rows = [...document.querySelectorAll<HTMLElement>(".xterm-rows > div")].map((row) => {
        const rowRect = row.getBoundingClientRect();
        const style = getComputedStyle(row);
        return {
          top: rowRect.top,
          height: rowRect.height,
          transform: style.transform,
          lineHeight: style.lineHeight,
        };
      });
      const rowGaps = rows.slice(1).map((row, index) => row.top - rows[index].top);
      return {
        heightDelta: Math.abs(header.height + body.height + helper.height - root.height),
        bodyBottom: body.bottom,
        hostBottom: host.bottom,
        screenBottom: screen.bottom,
        helperTop: helper.top,
        helperHeight: helper.height,
        textareaCount: document.querySelectorAll(".xterm-helper-textarea").length,
        rowHeights: rows.map((row) => row.height),
        rowGaps,
        rowTransforms: rows.map((row) => row.transform),
        rootInlineTop: document.querySelector<HTMLElement>("[data-terminal-root]")!.style.top,
        rootInlineLeft: document.querySelector<HTMLElement>("[data-terminal-root]")!.style.left,
      };
    });
    expect(layout.heightDelta).toBeLessThanOrEqual(1.5);
    expect(layout.bodyBottom).toBeLessThanOrEqual(layout.helperTop + 1);
    expect(layout.hostBottom).toBeLessThanOrEqual(layout.helperTop + 1);
    expect(layout.screenBottom).toBeLessThanOrEqual(layout.helperTop + 2);
    expect(layout.helperHeight).toBe(40);
    expect(layout.textareaCount).toBe(1);
    expect(Math.max(...layout.rowHeights) - Math.min(...layout.rowHeights)).toBeLessThanOrEqual(0.01);
    expect(Math.max(...layout.rowGaps) - Math.min(...layout.rowGaps)).toBeLessThanOrEqual(0.01);
    expect(layout.rowTransforms.every((value) => value === "none")).toBe(true);
    expect(layout.rootInlineTop).toBe("");
    expect(layout.rootInlineLeft).toBe("");
  });

  test("keeps writes and controller resources bounded across ten keyboard cycles", async ({ page }) => {
    test.setTimeout(25_000);
    const textarea = page.locator(".xterm-helper-textarea");
    const historyBefore = await page.evaluate(() => window.__controlDeckTerminalTest!.historyReplayCounters());
    await page.evaluate(() => window.__controlDeckTerminalTest!.resetCounters());
    await textarea.pressSequentially("i=1; while [ $i -le 200 ]; do printf '\\rWorking %03d' $i; sleep .05; i=$((i+1)); done; echo; echo WORKING_DONE_200", { delay: 1 });
    await textarea.press("Enter");
    for (let i = 0; i < 10; i += 1) {
      await page.setViewportSize({ width: 320, height: 430 });
      await page.waitForTimeout(400);
      await page.setViewportSize({ width: 320, height: 700 });
      await page.waitForTimeout(400);
    }
    await expect(page.locator(".xterm-rows")).toContainText("WORKING_DONE_200", { timeout: 15_000 });
    await textarea.pressSequentially("echo INPUT_OK", { delay: 1 });
    await textarea.press("Enter");
    await expect(page.locator(".xterm-rows")).toContainText("INPUT_OK");
    const result = await counters(page);
    const barrier = await page.evaluate(() => window.__controlDeckTerminalTest!.resizeBarrierState());
    const historyAfter = await page.evaluate(() => window.__controlDeckTerminalTest!.historyReplayCounters());
    if (process.env.CONTROL_DECK_E2E_REPORT === "1") {
      console.log("AP3_RESULT", JSON.stringify({ ...result, barrier: barrier.counters, historyBefore, historyAfter }));
    }
    expect(result.maxGeometryTasksPending).toBeLessThanOrEqual(1);
    expect(result.refreshExecuted).toBe(0);
    expect(barrier.counters.started).toBe(result.ptyResizeSent);
    expect(barrier.counters.ackAccepted).toBe(result.ptyResizeSent);
    expect(barrier.counters.timeoutReleased).toBe(0);
    expect(historyAfter.websocketCreated).toBe(historyBefore.websocketCreated);
    expect(historyAfter.historyReset).toBe(historyBefore.historyReset);
    expect(historyAfter.replayBytes).toBe(historyBefore.replayBytes);
    expect(await page.evaluate(() => window.__controlDeckTerminalTest!.controllerListenerCount)).toBe(13);
    expect(await page.evaluate(() => window.__controlDeckTerminalTest!.textareaCount())).toBe(1);
  });

  test("optional ten-minute IME and remount soak", async ({ page }) => {
    test.skip(process.env.CONTROL_DECK_E2E_SOAK !== "1", "set CONTROL_DECK_E2E_SOAK=1 for the ten-minute soak");
    test.setTimeout(660_000);
    const textarea = page.locator(".xterm-helper-textarea");
    const errors: string[] = [];
    page.on("console", (message) => {
      if (message.type() === "error") errors.push(message.text());
    });
    await page.evaluate(() => window.__controlDeckTerminalTest!.resetCounters());
    const memoryBefore = await page.evaluate(() => {
      const perf = performance as Performance & { memory?: { usedJSHeapSize: number } };
      return perf.memory?.usedJSHeapSize;
    });
    const deadline = Date.now() + 600_000;
    let cycle = 0;
    while (Date.now() < deadline) {
      await textarea.dispatchEvent("compositionstart", { data: "こ" });
      await page.evaluate((index) => {
        const hook = window.__controlDeckTerminalTest!;
        for (let i = 0; i < 10; i += 1) hook.invalidate(i % 3 === 0 ? "position" : "size", `soak-${index}-${i}`);
      }, cycle);
      await textarea.dispatchEvent("compositionend", { data: `確認${cycle}` });
      await page.setViewportSize({ width: 320, height: 430 });
      await page.waitForTimeout(180);
      await page.setViewportSize({ width: 320, height: 700 });
      await page.waitForTimeout(180);
      await textarea.pressSequentially(`echo SOAK_${cycle}`, { delay: 1 });
      await textarea.press("Enter");
      if (cycle % 10 === 0) await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent("pageshow")));
      if (cycle > 0 && cycle % 20 === 0) {
        await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
        await expect(page.locator(".xterm-helper-textarea")).toHaveCount(0);
        await page.getByRole("button", { name: "接続" }).last().click();
        await expect(page.locator(".xterm-helper-textarea")).toHaveCount(1);
      }
      cycle += 1;
      await page.waitForTimeout(400);
    }
    await page.waitForTimeout(250);
    const result = await counters(page);
    const memoryAfter = await page.evaluate(() => {
      const perf = performance as Performance & { memory?: { usedJSHeapSize: number } };
      return perf.memory?.usedJSHeapSize;
    });
    if (process.env.CONTROL_DECK_E2E_REPORT === "1") {
      console.log("AP4_RESULT", JSON.stringify({ cycle, memoryBefore, memoryAfter, ...result }));
    }
    expect(result.geometryTasksPending).toBe(0);
    expect(result.maxGeometryTasksPending).toBeLessThanOrEqual(1);
    expect(result.refreshExecuted).toBe(0);
    expect(await page.evaluate(() => window.__controlDeckTerminalTest!.textareaCount())).toBe(1);
    expect(await page.evaluate(() => window.__controlDeckTerminalTest!.controllerListenerCount)).toBe(13);
    if (memoryBefore !== undefined && memoryAfter !== undefined) {
      expect(memoryAfter - memoryBefore).toBeLessThan(100 * 1024 * 1024);
    }
    expect(errors).toEqual([]);
  });
});

test("desktop wheel, copy and remount keep one terminal instance", async ({ browser }) => {
  test.setTimeout(45_000);
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, hasTouch: false, isMobile: false });
  const page = await context.newPage();
  const consoleErrors: string[] = [];
  await openTerminal(page);
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  const sessionId = await page.locator("[data-terminal-header] select").inputValue();
  try {
    const textarea = page.locator(".xterm-helper-textarea");
    await textarea.pressSequentially("for i in $(seq 1 300); do echo DESKTOP_LINE_$i; done; echo DESKTOP_DONE", { delay: 1 });
    await textarea.press("Enter");
    await expect(page.locator(".xterm-rows")).toContainText("DESKTOP_DONE");
    const host = page.locator("[data-terminal-host]");
    await page.waitForTimeout(300);
    const before = await page.evaluate(() => window.__controlDeckTerminalTest!.baseY());
    await host.hover();
    await page.mouse.wheel(0, -700);
    await expect.poll(() => page.evaluate(() => window.__controlDeckTerminalTest!.viewportY())).toBeLessThan(before);

    await page.getByRole("button", { name: "コピー" }).first().click();
    await expect(page.getByRole("heading", { name: "コピー" })).toBeVisible();
    await expect(page.locator("textarea[readonly]")).toContainText("DESKTOP_DONE");
    await page.getByRole("button", { name: "閉じる", exact: true }).click();

    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    await expect(page.locator(".xterm-helper-textarea")).toHaveCount(0);
    await page.getByRole("button", { name: "接続" }).last().click();
    await expect(page.locator(".xterm-helper-textarea")).toHaveCount(1);
    expect(consoleErrors).toEqual([]);
  } finally {
    await context.request.delete(`/api/v1/terminals/${sessionId}`, {
      headers: { "X-Requested-With": "ControlDeck" },
    });
    await context.close();
  }
});
