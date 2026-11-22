import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

const SHA = "a".repeat(40);
const msg = (id: string, role: "user" | "assistant", content: string, meta: object = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta,
  created_at: "2026-11-23T00:00:00Z",
});

// 目的: 自動保存した回の「この時点に戻す」→「コードだけ」で、作業場の restore API がその sha で呼ばれる。
// 根拠: ROADMAP U15（Claude Code の /rewind と同じ考え）。LLM・箱は動かさず API を差し替える。
test("restore the code to the checkpoint of an autosaved turn", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/assistant");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  const calls: string[] = [];
  const messages = [msg("u1", "user", "直して"),
    msg("a1", "assistant", "直しました", { mode: "code", checkpoint: { sha: SHA, count: 2, workspace: "ws1" } })];
  await page.route(/\/api\/v1\/chat\/conversations\/orig\/messages$/, (route) =>
    route.fulfill({ json: { conversation: { id: "orig", title: "元の会話" }, messages } }));
  await page.route(/\/api\/v1\/opencode\/workspaces\/ws1\/restore$/, (route) => {
    calls.push(`restore:${(route.request().postDataJSON() as { sha: string }).sha}`);
    return route.fulfill({ json: { sha: SHA, before: "b".repeat(40), ref: "refs/controldeck/before-restore/1" } });
  });
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "orig"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("直しました")).toBeVisible();

  await shell.getByRole("button", { name: "メッセージの操作" }).last().click();
  await page.getByRole("menuitem", { name: /この時点に戻す/ }).click();
  const dialog = page.getByRole("alertdialog", { name: "この時点に戻す" });
  await expect(dialog.getByText("両方戻す")).toBeVisible();
  await expect(dialog.getByText("戻す前の状態も保存されるので、あとで戻せます。")).toBeVisible();
  await page.screenshot({ path: "test-results/assistant-restore-checkpoint-320.png" });
  await dialog.getByText("コードだけ戻す").click();
  await dialog.getByRole("button", { name: "戻す", exact: true }).click();
  await expect.poll(() => calls).toEqual([`restore:${SHA}`]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
});
