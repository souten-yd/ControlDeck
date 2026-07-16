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
      cursorX: () => number;
      cursorY: () => number;
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
  await expect(page.getByRole("heading", { name: "ターミナル" })).toBeVisible();
  await page.getByRole("button", { name: "新規セッション" }).click();
  await expect(page.locator("[data-terminal-root]")).toBeVisible();
  await expect(page.locator(".xterm-helper-textarea")).toHaveCount(1);
  await expect.poll(() => page.evaluate(() => Boolean(window.__controlDeckTerminalTest))).toBe(true);
  await page.waitForTimeout(250);
}

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
    if (process.env.CONTROL_DECK_E2E_REPORT === "1") console.log("AP1_RESULT", JSON.stringify(result));
    expect(result.fitRequested).toBeGreaterThanOrEqual(25);
    expect(result.fitExecuted).toBeLessThanOrEqual(1);
    expect(result.resizeExecuted).toBeLessThanOrEqual(1);
    expect(result.ptyResizeSent).toBeLessThanOrEqual(1);
    expect(result.refreshExecuted).toBe(0);
    expect(result.maxGeometryTasksPending).toBeLessThanOrEqual(1);
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
    const settledLayout = await page.evaluate(() => {
      const textarea = document.querySelector<HTMLTextAreaElement>(".xterm-helper-textarea")!.getBoundingClientRect();
      const host = document.querySelector<HTMLElement>("[data-terminal-host]")!.getBoundingClientRect();
      const helper = document.querySelector<HTMLElement>("[data-terminal-helper]")!.getBoundingClientRect();
      const screen = document.querySelector<HTMLElement>(".xterm-screen")!.getBoundingClientRect();
      const rows = window.__controlDeckTerminalTest!.rows();
      const expectedTop = screen.top + window.__controlDeckTerminalTest!.cursorY() * screen.height / rows;
      return { textareaTop: textarea.top, textareaBottom: textarea.bottom, expectedTop, hostBottom: host.bottom, helperTop: helper.top };
    });
    expect(Math.abs(settledLayout.textareaTop - settledLayout.expectedTop)).toBeLessThanOrEqual(0.5);
    expect(settledLayout.textareaBottom).toBeLessThanOrEqual(settledLayout.hostBottom);
    expect(settledLayout.textareaBottom).toBeLessThanOrEqual(settledLayout.helperTop);
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
      return {
        heightDelta: Math.abs(header.height + body.height + helper.height - root.height),
        bodyBottom: body.bottom,
        hostBottom: host.bottom,
        screenBottom: screen.bottom,
        helperTop: helper.top,
        helperHeight: helper.height,
        textareaCount: document.querySelectorAll(".xterm-helper-textarea").length,
      };
    });
    expect(layout.heightDelta).toBeLessThanOrEqual(1.5);
    expect(layout.bodyBottom).toBeLessThanOrEqual(layout.helperTop + 1);
    expect(layout.hostBottom).toBeLessThanOrEqual(layout.helperTop + 1);
    expect(layout.screenBottom).toBeLessThanOrEqual(layout.helperTop + 2);
    expect(layout.helperHeight).toBe(40);
    expect(layout.textareaCount).toBe(1);
  });

  test("keeps writes and controller resources bounded across ten keyboard cycles", async ({ page }) => {
    test.setTimeout(25_000);
    const textarea = page.locator(".xterm-helper-textarea");
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
    if (process.env.CONTROL_DECK_E2E_REPORT === "1") console.log("AP3_RESULT", JSON.stringify(result));
    expect(result.maxGeometryTasksPending).toBeLessThanOrEqual(1);
    expect(result.refreshExecuted).toBe(0);
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
