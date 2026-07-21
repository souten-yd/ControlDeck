import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("Terminal overview identifies the foreground program, directory, and live state", async ({ page }) => {
  test.setTimeout(60_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/terminal");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/terminal");
  await expect(page.getByRole("heading", { name: "Terminal" })).toBeVisible();
  await page.evaluate(() => localStorage.setItem("control-deck:terminal-geometry-debug", "1"));

  const sessionId = await page.evaluate(async () => {
    const response = await fetch("/api/v1/terminals", {
      method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json() as { id: string }).id;
  });

  try {
    await page.reload();
    const initialCard = page.getByRole("listitem").filter({ hasText: `#${sessionId}` });
    await expect(initialCard).toContainText("Shell ready");
    await expect(initialCard).toContainText("~");
    await expect(initialCard.getByRole("button", { name: /オートメーション設定$/ })).toContainText("🔧");
    await expect(initialCard.getByRole("button", { name: /セッションを削除$/ })).toContainText("🗑️");
    await expect(initialCard.getByRole("button", { name: /menu$/ })).toHaveCount(0);
    await initialCard.getByRole("button", { name: /セッションを削除$/ }).click();
    const deleteDialog = page.getByRole("alertdialog", { name: "セッションを終了しますか？" });
    await expect(deleteDialog).toBeVisible();
    await deleteDialog.getByRole("button", { name: "キャンセル" }).click();
    await initialCard.getByRole("button", { name: "Connect" }).click();
    await expect.poll(() => page.evaluate(() => Boolean((window as typeof window & { __controlDeckTerminalTest?: unknown }).__controlDeckTerminalTest))).toBe(true);
    await expect.poll(() => page.evaluate(() => {
      const target = window as typeof window & { __controlDeckTerminalTest?: { connectionState: () => { state?: unknown } } };
      return target.__controlDeckTerminalTest?.connectionState().state;
    }), { timeout: 10_000 }).toBe("LIVE");
    await page.evaluate(() => {
      const target = window as typeof window & { __controlDeckTerminalTest?: { sendInputForTest: (value: string) => void } };
      target.__controlDeckTerminalTest?.sendInputForTest("cd ~/ControlDeck && sleep 30\r");
    });
    await expect.poll(() => page.evaluate(async (id) => {
      const response = await fetch("/api/v1/terminals", { credentials: "same-origin" });
      const sessions = (await response.json() as { sessions: Array<{ id: string; program: string; cwd: string; workload: string }> }).sessions;
      const current = sessions.find((item) => item.id === id);
      return current ? `${current.program}|${current.cwd}|${current.workload}` : "missing";
    }, sessionId)).toBe("sleep|~/ControlDeck|running");
    await expect(page.getByText("Foreground sleep", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "ターミナルを閉じる" }).click();
    const runningCard = page.getByRole("listitem").filter({ hasText: `#${sessionId}` });
    await expect(runningCard).toContainText("sleep");
    await expect(runningCard).toContainText("~/ControlDeck");
    await expect(runningCard).toContainText("Foreground active");
    await expect(runningCard).toContainText("最終活動");
    await expect(runningCard).toContainText("作成");
    const mobileInfoLayout = await runningCard.evaluate((card) => {
      const status = card.querySelector<HTMLElement>("[data-terminal-status-row]")!.getBoundingClientRect();
      const meta = card.querySelector<HTMLElement>("[data-terminal-meta-row]")!.getBoundingClientRect();
      const dates = card.querySelector<HTMLElement>("[data-terminal-dates]")!.getBoundingClientRect();
      const connect = card.querySelector<HTMLElement>("[data-terminal-connect]")!.getBoundingClientRect();
      return { statusBottom: status.bottom, metaTop: meta.top, datesRight: dates.right, connectLeft: connect.left };
    });
    expect(mobileInfoLayout.statusBottom).toBeLessThanOrEqual(mobileInfoLayout.metaTop);
    expect(mobileInfoLayout.datesRight).toBeLessThanOrEqual(mobileInfoLayout.connectLeft);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/terminals/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
      localStorage.removeItem("control-deck:terminal-geometry-debug");
    }, sessionId);
  }
});
