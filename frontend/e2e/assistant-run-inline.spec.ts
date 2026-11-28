// 目的: 返事のコマンドの「▶ 実行」が、画面の中（確認のシートの実行の枠）で走り、出力・状態・止める・閉じるが出ること、
//       sudo がパスワードを求めたらポップアップで受け、入力欄をすぐ空にして POST の本文だけに載せること
//       （API は模擬。パスワードが画面・URL・保存に残らない）。320px と 1280px・ダークとライトを撮る。
// 根拠: 2026-11-28 利用者「コンソールコマンドをユーザ側で実行できるように。管理者パスワードが必要ならポップアップで
//       入力できるように。LLM が実行できないコマンドもコンソールへ移動せずに実行したい」。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const SECRET = "e2e-Secret-pw-123";
const msg = (id: string, role: "user" | "assistant", content: string, meta: Record<string, unknown> = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta,
  created_at: "2026-11-28T00:00:00Z",
});

async function open(page: Page) {
  const calls = { run: [] as unknown[], input: [] as unknown[], stop: 0, del: 0 };
  let polls = 0;
  let sent = false;
  await page.route(/\/api\/v1\/chat\/conversations\/ri1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "ri1", title: "実行" },
    messages: [msg("u1", "user", "更新して"), msg("a1", "assistant", "```bash\nsudo apt update\n```", { mode: "chat" })],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/ri1\/queue$/, (route) =>
    route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.route(/\/api\/v1\/terminals\/run$/, (route) => {
    calls.run.push(route.request().postDataJSON());
    return route.fulfill({ status: 201, json: { session_id: "term9" } });
  });
  await page.route(/\/api\/v1\/terminals\/run\/term9$/, (route) => {
    polls += 1;
    const base = "$ sudo apt update\n";
    if (sent) return route.fulfill({ json: { output: base + "取得:1 http://example\n完了", done: true, exit_code: 0, password_prompt: false } });
    if (polls < 2) return route.fulfill({ json: { output: base, done: false, exit_code: null, password_prompt: false } });
    return route.fulfill({ json: { output: base + "[sudo] password for tester:", done: false, exit_code: null, password_prompt: true } });
  });
  await page.route(/\/api\/v1\/terminals\/run\/term9\/input$/, (route) => {
    calls.input.push(route.request().postDataJSON()); sent = true;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(/\/api\/v1\/terminals\/run\/term9\/stop$/, (route) => { calls.stop += 1; return route.fulfill({ json: { ok: true } }); });
  await page.route(/\/api\/v1\/terminals\/term9$/, (route) => { calls.del += 1; return route.fulfill({ json: { ok: true } }); });
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "ri1"));
  await page.goto("/assistant");
  await expect(page.locator("[data-assistant-shell]")).toBeVisible();
  return calls;
}

for (const [width, scheme] of [[320, "dark"], [1280, "light"], [320, "light"], [1280, "dark"]] as const) {
  test(`inline run: output, password popup, finish (${width}px ${scheme})`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.emulateMedia({ colorScheme: scheme });
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const calls = await open(page);

    await page.locator('[data-testid="code-run"]').first().click();
    const sheet = page.getByRole("dialog", { name: "ターミナルで実行" });
    await sheet.getByTestId("run-command-confirm").click();       // sudo 入り: 1 回目は用意
    await sheet.getByTestId("run-command-confirm").click();
    await expect.poll(() => calls.run.length).toBe(1);
    await expect(sheet.getByTestId("run-status")).toContainText("実行中");

    // パスワードのポップアップ
    const pw = page.getByRole("alertdialog", { name: "管理者のパスワード" });
    await expect(pw).toBeVisible();
    await expect(pw).toContainText("保存しません");
    const input = pw.getByTestId("run-password");
    expect(await input.getAttribute("type")).toBe("password");
    expect(await input.getAttribute("name")).toBeNull();
    expect(await input.getAttribute("autocomplete")).toBe("off");
    await input.fill(SECRET);
    await page.screenshot({ path: `test-results/assistant-run-inline-prompt-${width}-${scheme}.png` });
    await pw.getByRole("button", { name: "送る" }).click();
    await expect.poll(() => calls.input.length).toBe(1);
    expect(calls.input[0]).toEqual({ secret: SECRET });
    await expect(pw).toBeHidden();
    // 値は画面にも URL にも残らない
    expect(await page.content()).not.toContain(SECRET);
    expect(page.url()).not.toContain(SECRET);

    await expect(sheet.getByTestId("run-status")).toContainText("終了（コード 0）");
    await expect(sheet.getByTestId("run-output")).toContainText("完了");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/assistant-run-inline-${width}-${scheme}.png` });
    await sheet.getByTestId("run-close").click();
    await expect.poll(() => calls.del).toBe(1);
    await expect(sheet).toBeHidden();
  });
}

test("inline run: cancelling the password popup sends Ctrl-C", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  const calls = await open(page);
  await page.locator('[data-testid="code-run"]').first().click();
  const sheet = page.getByRole("dialog", { name: "ターミナルで実行" });
  await sheet.getByTestId("run-command-confirm").click();
  await sheet.getByTestId("run-command-confirm").click();
  const pw = page.getByRole("alertdialog", { name: "管理者のパスワード" });
  await expect(pw).toBeVisible();
  await pw.getByRole("button", { name: "やめる" }).click();
  await expect.poll(() => calls.stop).toBe(1);
  expect(calls.input).toHaveLength(0);
});
