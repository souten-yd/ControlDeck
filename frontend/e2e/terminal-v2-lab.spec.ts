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
  openCopy: () => void;
  close: () => void;
};

test("uses V2 only for this tab's dedicated Lab session and resumes its local history", async ({ page }) => {
  test.setTimeout(60_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/terminal?terminalLab=v2");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/terminal?terminalLab=v2");
  runtimeErrors.length = 0;

  const before = await page.evaluate(async () => {
    const response = await fetch("/api/v1/terminals", { credentials: "same-origin" });
    return (await response.json()).sessions.map((session: { id: string }) => session.id) as string[];
  });
  await expect(page.getByRole("status").filter({ hasText: "Terminal V2 Lab" })).toBeVisible();
  await page.getByRole("button", { name: "V2検証セッション" }).click();

  const root = page.locator("[data-terminal-root][data-terminal-engine='v2']");
  await expect(root).toBeVisible();
  await expect(root.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
  const createdId = await page.getByLabel("セッションを切替").inputValue();
  const ownedIds = [createdId];
  expect(before).not.toContain(createdId);
  const contract = await page.evaluate(async (id) => {
    const response = await fetch("/api/v1/terminals", { credentials: "same-origin" });
    return (await response.json()).sessions.find((session: { id: string }) => session.id === id);
  }, createdId);
  expect(contract.engine).toBe("v2-lab");

  try {
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

    const initial = await root.evaluate((element) => ({
      replayMs: Number((element as HTMLElement).dataset.terminalV2ReplayMs),
      replayBytes: Number((element as HTMLElement).dataset.terminalV2ReplayBytes),
      viewport: innerWidth,
      documentWidth: document.documentElement.scrollWidth,
      rect: element.getBoundingClientRect().toJSON(),
      textareas: element.querySelectorAll(".xterm-helper-textarea").length,
    }));
    expect(initial.replayMs).toBeLessThan(4_000);
    expect(initial.replayBytes).toBeGreaterThanOrEqual(0);
    expect(initial.documentWidth).toBeLessThanOrEqual(initial.viewport);
    expect(initial.rect.left).toBeGreaterThanOrEqual(0);
    expect(initial.rect.right).toBeLessThanOrEqual(initial.viewport + 1);
    expect(initial.textareas).toBe(1);

    await page.evaluate(() => {
      const api = (window as typeof window & { __controlDeckTerminalV2Test?: V2TestApi }).__controlDeckTerminalV2Test;
      if (!api) throw new Error("V2 test API is unavailable");
      api.close();
    });
    await expect(root.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_HISTORY_120");

    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    await page.reload();
    const ownCard = page.locator("li").filter({ hasText: `#${createdId}` });
    await ownCard.getByRole("button", { name: "Connect" }).click();
    await expect(page.locator("[data-terminal-root][data-terminal-engine='v2']")).toBeVisible();
    await expect(page.getByText("Live", { exact: true })).toBeVisible({ timeout: 4_000 });
    await expect(page.locator("[data-terminal-host] .xterm-rows")).toContainText("V2_HISTORY_120");

    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    await page.getByRole("button", { name: "V2検証セッション" }).click();
    const secondId = await page.getByLabel("セッションを切替").inputValue();
    ownedIds.push(secondId);
    expect(secondId).not.toBe(createdId);
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
