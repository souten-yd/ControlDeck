import { expect, test } from "@playwright/test";
import { createHash } from "node:crypto";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

type V2TestApi = {
  send: (data: string) => void;
  paste: (data: string) => void;
  pasteState: () => { state: string };
  scrollLines: (lines: number) => void;
  viewportY: () => number;
  baseY: () => number;
  metrics: () => {
    replayMs: number;
    replayWriteMs: number;
    replayPaintMs: number;
    replayBytes: number;
    replayChunks: number;
    echoP95Ms: number;
    echoMaxMs: number;
    echoSamples: number;
    scrollP95Ms: number;
    scrollMaxMs: number;
    scrollSamples: number;
    resizeCount: number;
    reconnectCount: number;
    rows: number;
    cols: number;
  };
  resetEchoMetrics: () => void;
  openCopy: () => void;
  close: () => void;
};

test("uses V2 only for this tab's dedicated Lab session and resumes its local history", async ({ page }) => {
  test.setTimeout(60_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    // WebKitは未対応のviewport hintを仕様どおり無視するが、console error分類で通知する。
    if (message.text() === 'Viewport argument key "interactive-widget" not recognized and ignored.') return;
    runtimeErrors.push(message.text());
  });
  page.on("pageerror", (error) => runtimeErrors.push(error.message));

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/terminal?terminalLab=v2");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/terminal?terminalLab=v2");
  runtimeErrors.length = 0;

  const ownedIds: string[] = [];
  try {
    const before = await page.evaluate(async () => {
      const response = await fetch("/api/v1/terminals", { credentials: "same-origin" });
      return (await response.json()).sessions.map((session: { id: string }) => session.id) as string[];
    });
    await expect(page.getByRole("status").filter({ hasText: "Terminal V2 Lab" })).toBeVisible();
    const firstCreateResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/api/v1/terminals")
      && new URL(response.url()).searchParams.get("engine") === "v2-lab");
    await page.getByRole("button", { name: "V2検証セッション" }).click();
    const createdId = String((await (await firstCreateResponse).json() as { id: string }).id);
    ownedIds.push(createdId);
    expect(before).not.toContain(createdId);

    const root = page.locator("[data-terminal-root][data-terminal-engine='v2']");
    await expect(root).toBeVisible();
    await expect(root.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
    await expect(page.getByLabel("セッションを切替")).toHaveValue(createdId);
    const contract = await page.evaluate(async (id) => {
      const response = await fetch("/api/v1/terminals", { credentials: "same-origin" });
      return (await response.json()).sessions.find((session: { id: string }) => session.id === id);
    }, createdId);
    expect(contract.engine).toBe("v2-lab");

    const marker = `V2_LAB_${Date.now()}`;
    await page.evaluate((value) => {
      const api = (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test;
      if (!api) throw new Error("V2 test API is unavailable");
      api.send(`printf '${value}\\n'\r`);
    }, marker);
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText(marker);

    const helperCommand = `python3 -c "import sys,tty; print('V2_HELPER_READY',flush=True); tty.setraw(0); d=sys.stdin.buffer.read(20); print(chr(13)+chr(10)+'V2_HELPER_HEX:'+d.hex(),flush=True)"`;
    await page.evaluate((command) => {
      (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test!.send(`${command}\r`);
    }, helperCommand);
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_HELPER_READY");
    for (const key of ["Enter", "Esc", "Tab", "↑", "↓", "←", "→", "^C", "^D", "^Z", "^L"]) {
      await page.getByRole("button", { name: key, exact: true }).evaluate((button: HTMLButtonElement) => button.click());
    }
    await page.getByRole("button", { name: "Ctrl", exact: true }).evaluate((button: HTMLButtonElement) => button.click());
    await page.locator(".xterm-helper-textarea").press("a");
    const helperBytes = Buffer.from("\r\x1b\t\x1b[A\x1b[B\x1b[D\x1b[C\x03\x04\x1a\x0c\x01", "binary");
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText(`V2_HELPER_HEX:${helperBytes.toString("hex")}`);

    const payload = `V2_PASTE_START_${"日本語🌸".repeat(8_000)}_V2_PASTE_END`;
    const payloadBytes = Buffer.from(payload);
    const payloadHash = createHash("sha256").update(payloadBytes).digest("hex");
    const pasteCommand = `python3 -c "import sys,tty,hashlib; print('V2_PASTE_READY',flush=True); tty.setraw(0); n=int(sys.stdin.buffer.read(10)); d=sys.stdin.buffer.read(n); print(chr(13)+chr(10)+'V2_PASTE_RESULT:'+str(len(d))+':'+hashlib.sha256(d).hexdigest(),flush=True)"`;
    await page.evaluate((command) => {
      (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test!.send(`${command}\r`);
    }, pasteCommand);
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_PASTE_READY");
    await page.evaluate(({ text, bytes }) => {
      (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test!.paste(String(bytes).padStart(10, "0") + text);
    }, { text: payload, bytes: payloadBytes.length });
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.pasteState().state), { timeout: 30_000 }).toBe("idle");
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText(`V2_PASTE_RESULT:${payloadBytes.length}:${payloadHash}`);

    await page.evaluate(() => {
      (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test!.openCopy();
    });
    const copyDialog = page.getByRole("dialog", { name: "コピー" });
    await expect(copyDialog).toBeVisible();
    await expect(copyDialog.locator("textarea")).toContainText("V2_PASTE_RESULT");
    await copyDialog.getByRole("button", { name: "閉じる" }).click();

    await page.getByRole("button", { name: "Automation settings" }).click();
    const automation = page.getByRole("dialog", { name: "Terminal snippets and automation" });
    await expect(automation).toBeVisible();
    await expect(automation.getByRole("navigation", { name: "Snippet sections" })).toBeVisible();
    await automation.getByRole("button", { name: "閉じる" }).click();

    await page.evaluate(() => {
      const api = (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test!;
      api.send("for i in $(seq 1 120); do printf 'V2_HISTORY_%03d\\n' $i; done\r");
    });
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_HISTORY_120");
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.baseY())).toBeGreaterThan(0);
    const liveEdge = await page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.baseY());
    await page.evaluate(() => {
      (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test!.scrollLines(-20);
    });
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.viewportY())).toBeLessThan(liveEdge);
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.metrics().scrollSamples)).toBeGreaterThan(0);

    await page.evaluate(() => {
      const api = (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test;
      if (!api) throw new Error("V2 test API is unavailable");
      api.close();
    });
    await expect(root.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_HISTORY_120");
    await expect.poll(() => page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.metrics().reconnectCount)).toBe(1);

    await page.evaluate(() => {
      const api = (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test!;
      api.send("stty raw -echo; printf 'V2_ECHO_READY\\r\\n'; for i in $(seq 0 19); do dd bs=1 count=1 2>/dev/null; printf '\\r\\nV2_ECHO_%s\\r\\n' \"$i\"; done; stty sane\r");
    });
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_ECHO_READY");
    await page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.resetEchoMetrics());
    for (let sample = 0; sample < 20; sample += 1) {
      await page.evaluate(() => (
        window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
      ).__controlDeckTerminalV2Test!.send("x"));
      await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText(`V2_ECHO_${sample}`);
    }

    const initial = await root.evaluate((element) => ({
      replayMs: Number((element as HTMLElement).dataset.terminalV2ReplayMs),
      replayBytes: Number((element as HTMLElement).dataset.terminalV2ReplayBytes),
      viewport: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      rect: element.getBoundingClientRect().toJSON(),
      textareas: element.querySelectorAll(".xterm-helper-textarea").length,
    }));
    const initialMetrics = await page.evaluate(() => (
      window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
    ).__controlDeckTerminalV2Test!.metrics());
    expect(initial.replayMs).toBeLessThan(4_000);
    expect(initial.replayBytes).toBeGreaterThanOrEqual(0);
    expect(initial.documentWidth).toBeLessThanOrEqual(initial.viewport);
    expect(initial.rect.left).toBeGreaterThanOrEqual(0);
    expect(initial.rect.right).toBeLessThanOrEqual(initial.viewport + 1);
    expect(initial.textareas).toBe(1);
    expect(initialMetrics.replayWriteMs).toBeGreaterThanOrEqual(0);
    expect(initialMetrics.replayPaintMs).toBeGreaterThanOrEqual(0);
    expect(initialMetrics.replayChunks).toBeGreaterThanOrEqual(0);
    expect(initialMetrics.echoSamples).toBe(20);
    expect(initialMetrics.echoP95Ms).toBeLessThan(50);
    expect(initialMetrics.echoMaxMs).toBeLessThan(250);
    expect(initialMetrics.scrollP95Ms).toBeLessThan(100);
    expect(initialMetrics.scrollMaxMs).toBeLessThan(100);

    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    await page.reload();
    const ownCard = page.locator("li").filter({ hasText: `#${createdId}` });
    await ownCard.getByRole("button", { name: "Connect" }).click();
    await expect(page.locator("[data-terminal-root][data-terminal-engine='v2']")).toBeVisible();
    await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_ECHO_19");

    const viewportCases = [
      { width: 320, height: 700 },
      { width: 390, height: 844 },
      { width: 768, height: 1024 },
      { width: 1280, height: 800 },
    ];
    for (let index = 0; index < viewportCases.length; index += 1) {
      const viewport = viewportCases[index];
      if (index > 0) {
        await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
        await page.setViewportSize(viewport);
        await page.locator("li").filter({ hasText: `#${createdId}` }).getByRole("button", { name: "Connect" }).click();
        await expect(page.locator("[data-terminal-root][data-terminal-engine='v2']")).toBeVisible();
        await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
      }

      const viewportMarker = `V2_VIEWPORT_${viewport.width}_${viewport.height}`;
      await page.evaluate((value) => {
        (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi })
          .__controlDeckTerminalV2Test!.send(`printf '${value}\\n'\r`);
      }, viewportMarker);
      await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText(viewportMarker);

      const layout = await page.locator("[data-terminal-root][data-terminal-engine='v2']").evaluate((element) => {
        const rootElement = element as HTMLElement;
        const visual = window.visualViewport;
        const controls = [...rootElement.querySelectorAll<HTMLElement>("button, select")]
          .filter((control) => {
            const style = getComputedStyle(control);
            const rect = control.getBoundingClientRect();
            return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
          })
          .map((control) => ({ label: control.getAttribute("aria-label") || control.textContent, height: control.getBoundingClientRect().height }));
        const helper = rootElement.querySelector<HTMLElement>("[data-terminal-helper]");
        const rect = rootElement.getBoundingClientRect();
        return {
          viewportWidth: visual?.width ?? innerWidth,
          viewportHeight: visual?.height ?? innerHeight,
          viewportLeft: visual?.offsetLeft ?? 0,
          viewportTop: visual?.offsetTop ?? 0,
          documentWidth: document.documentElement.scrollWidth,
          rect: rect.toJSON(),
          helperVisible: helper ? getComputedStyle(helper).display !== "none" && helper.getBoundingClientRect().height > 0 : false,
          textareas: rootElement.querySelectorAll(".xterm-helper-textarea").length,
          controls,
        };
      });
      expect(layout.documentWidth).toBeLessThanOrEqual(viewport.width);
      expect(layout.rect.left).toBeGreaterThanOrEqual(layout.viewportLeft - 1);
      expect(layout.rect.right).toBeLessThanOrEqual(layout.viewportLeft + layout.viewportWidth + 1);
      expect(layout.rect.bottom).toBeLessThanOrEqual(layout.viewportTop + layout.viewportHeight + 1);
      expect(layout.textareas).toBe(1);
      expect(layout.helperVisible).toBe(viewport.width < 768);
      expect(layout.controls.length).toBeGreaterThan(0);
      expect(layout.controls.every((control) => control.height >= 44)).toBe(true);

      const metrics = await page.evaluate(() => (
        window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
      ).__controlDeckTerminalV2Test!.metrics());
      expect(metrics.replayMs).toBeLessThan(4_000);
      expect(metrics.echoP95Ms).toBeLessThan(50);
      expect(metrics.echoMaxMs).toBeLessThan(250);
      expect(metrics.rows).toBeGreaterThanOrEqual(3);
      expect(metrics.cols).toBeGreaterThanOrEqual(10);

      if (viewport.width === 390) {
        const beforeResize = metrics.resizeCount;
        await page.setViewportSize({ width: 390, height: 430 });
        await expect.poll(() => page.evaluate(() => (
          window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }
        ).__controlDeckTerminalV2Test!.metrics().resizeCount)).toBeGreaterThan(beforeResize);
        const keyboardLayout = await page.locator("[data-terminal-root][data-terminal-engine='v2']").evaluate((element) => {
          const visual = window.visualViewport;
          const helper = element.querySelector<HTMLElement>("[data-terminal-helper]")!;
          return {
            rootBottom: element.getBoundingClientRect().bottom,
            helperBottom: helper.getBoundingClientRect().bottom,
            viewportBottom: (visual?.offsetTop ?? 0) + (visual?.height ?? innerHeight),
            textareas: element.querySelectorAll(".xterm-helper-textarea").length,
          };
        });
        expect(keyboardLayout.rootBottom).toBeLessThanOrEqual(keyboardLayout.viewportBottom + 1);
        expect(keyboardLayout.helperBottom).toBeLessThanOrEqual(keyboardLayout.viewportBottom + 1);
        expect(keyboardLayout.textareas).toBe(1);
        await page.setViewportSize(viewport);
      }
    }

    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    const secondCreateResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/api/v1/terminals")
      && new URL(response.url()).searchParams.get("engine") === "v2-lab");
    await page.getByRole("button", { name: "V2検証セッション" }).click();
    const secondId = String((await (await secondCreateResponse).json() as { id: string }).id);
    ownedIds.push(secondId);
    expect(secondId).not.toBe(createdId);
    await expect(page.getByLabel("セッションを切替")).toHaveValue(secondId);
    await page.getByLabel("セッションを切替").selectOption(createdId);
    await expect(page.locator("[data-terminal-root][data-terminal-engine='v2']")).toBeVisible();
    await expect(page.getByLabel("セッションを切替")).toHaveValue(createdId);
    await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (ids) => {
      for (const id of ids) await fetch(`/api/v1/terminals/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, ownedIds);
  }
});
