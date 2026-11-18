// 目的: OpenCode の作業中に、段階（プリフィル・思考・道具など）と時間が入力欄の下に出ること。アクションリストはある
//       ときだけ浮かび、タップで作業中・完了・時刻が見えること。道具を開くと開始・終了の時刻が見えること。
// 根拠: 2026-11-18 利用者「ずっと応答待ちと記載されている」「いつ終わるのかわからん」「項目の詳細に時間があれば」。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

test("an action list that was not updated says so", async ({ page, context }) => {
  // 2026-11-18 利用者「一番上がまだ走っている。実際はもっと進んでいるように見える」。本番はモデルが最初の 1 回しか更新していなかった
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  const now = Date.now() / 1000;
  const tools = Array.from({ length: 12 }, (_, i) => ({ id: `c${i}`, name: "edit", status: "completed", input: { path: "a.ts" }, output: "", started_at: now - 60 + i }));
  await page.route(/\/api\/v1\/chat\/conversations\/s1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "s1", title: "古いリスト" },
    messages: [{ id: "u", role: "user", content: "作って", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-18T00:00:00Z" },
      { id: "a", role: "assistant", content: "", thinking: "", status: "generating", job_id: "j", model: "", error: "", created_at: "2026-11-18T00:00:01Z",
        meta: { mode: "code", tools, timeline: tools.map((t) => ({ k: "u", id: t.id })),
          todos: [{ content: "雛形を作る", status: "in_progress", started_at: now - 600, updated_at: now - 600 }, { content: "検証する", status: "pending", updated_at: now - 600 }] } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/s1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: "a", held: false } }));
  await page.routeWebSocket(/\/chat\/messages\/a\/stream/, () => undefined);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "s1"));
  await page.goto("/assistant");
  const list = page.locator("[data-assistant-todos]");
  await expect(list).toContainText("未更新 · その後 12 操作");
  await list.getByRole("button").click();
  await expect(list.locator("[data-todo-stale]")).toContainText("実際はこの表示より進んでいる可能性");
});

for (const width of [320, 1280]) {
  test(`changes open as a diff and the latest turn can be reverted (${width}px)`, async ({ page, context }) => {
    // 2026-11-18 利用者の依頼: OpenCode の差分・取り消しを AI アシスタントから使えるように
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    let reverted = 0;
    const change = { file: "src/very/long/path/to/the/component/name.tsx", additions: 2, deletions: 1, status: "modified" };
    await page.route(/\/api\/v1\/chat\/conversations\/d1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "d1", title: "差分" },
      messages: [{ id: "u", role: "user", content: "直して", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-18T00:00:00Z" },
        { id: "a", role: "assistant", content: "直しました", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-18T00:00:01Z",
          meta: { mode: "code", opencode_session: "ses_1", changes: [change] } }],
    } }));
    await page.route(/\/api\/v1\/chat\/conversations\/d1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
    await page.route(/\/opencode\/changes\?/, (route) => route.fulfill({ json: { files: [{ ...change, patch: "diff --git a b\n@@ -1 +1,2 @@\n-古い行\n+新しい行\n+追加した長い行".concat("あ".repeat(200)) }] } }));
    await page.route(/\/opencode\/revert$/, (route) => { reverted += 1; return route.fulfill({ json: { reverted: true } }); });
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "d1"));
    await page.goto("/assistant");
    const card = page.locator("[data-code-changes]");
    await expect(card).toContainText("変更 1 ファイル");
    await card.getByRole("button", { name: /変更 1 ファイル/ }).click();
    await card.getByRole("button", { name: /name\.tsx/ }).click();
    await expect(card.locator("[data-code-patch]")).toContainText("新しい行");
    await expect(card.locator("[data-code-patch]")).not.toContainText("diff --git");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await card.locator("[data-code-revert]").click();
    await page.getByRole("button", { name: "取り消す", exact: true }).click();
    await expect(card).toContainText("取り消した変更");
    await expect(card.locator("[data-code-revert]")).toHaveCount(0);
    expect(reverted).toBe(1);
  });

  test(`text stays between the work steps and steps share one card (${width}px)`, async ({ page, context }) => {
    // 2026-11-18 利用者「チャットウィンドウの視認性を向上させて」。段階ごとの文が 1 段落につながっていた
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const first = "🎉表示を確認します:";                                      // 絵文字があっても位置がずれない
    const last = "3相とも正しいです。";
    const notice = "（許可されなかったため、ここで止めました）";                // 並びに載らない追記は最後に出す
    await page.route(/\/api\/v1\/chat\/conversations\/r1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "r1", title: "読みやすさ" },
      messages: [{ id: "u", role: "user", content: "確認して", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-18T00:00:00Z" },
        { id: "a", role: "assistant", content: first + last + notice, thinking: "考える", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-18T00:00:01Z",
          meta: { mode: "code", tools: [{ id: "c", name: "shell", status: "completed", input: { command: "ls" }, output: "a" }],
            timeline: [{ k: "x", from: 0, to: first.length }, { k: "t", from: 0, to: 3 }, { k: "u", id: "c" }, { k: "x", from: first.length, to: first.length + last.length }] } }],
    } }));
    await page.route(/\/api\/v1\/chat\/conversations\/r1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "r1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const texts = shell.locator("[data-code-text]");
    await expect(texts).toHaveCount(2);
    await expect(texts.nth(0)).toContainText(first);
    await expect(texts.nth(1)).toContainText(last);
    await expect(texts.nth(1)).not.toContainText(notice);
    await expect(shell.getByText(notice)).toBeVisible();
    const card = shell.locator("[data-code-steps]");
    await expect(card).toHaveCount(1);                                       // 思考と道具は同じカード
    await expect(card.locator("[data-thinking]")).toHaveCount(1);
    await expect(card.getByRole("button", { name: /Bash/ })).toBeVisible();
    const cardTop = (await card.boundingBox())!.y;
    expect((await texts.nth(0).boundingBox())!.y).toBeLessThan(cardTop);
    expect((await texts.nth(1).boundingBox())!.y).toBeGreaterThan(cardTop);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });

  test(`progress, action list and tool times are shown (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const now = Date.now() / 1000;
    const todos = [{ content: "設計する", status: "completed", started_at: now - 90, completed_at: now - 30 },
      { content: "波形の計算を実装する", status: "in_progress", started_at: now - 30 }, { content: "確認する", status: "pending" }];
    await page.route(/\/api\/v1\/chat\/conversations\/g1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "g1", title: "進み具合" },
      messages: [{ id: "u", role: "user", content: "作って", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-18T00:00:00Z" },
        { id: "a", role: "assistant", content: "", thinking: "", status: "generating", job_id: "j", model: "", error: "", created_at: "2026-11-18T00:00:01Z",
          meta: { mode: "code", todos, timeline: [{ k: "u", id: "c" }], tools: [{ id: "c", name: "shell", status: "completed", input: { command: "ls" }, output: "a", started_at: now - 12, ended_at: now - 9 }] } }],
    } }));
    await page.route(/\/api\/v1\/chat\/conversations\/g1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: "a", held: false } }));
    await page.routeWebSocket(/\/chat\/messages\/a\/stream/, (ws) => {
      ws.send(JSON.stringify({ type: "stats", phase: "prefill", detail: "プロンプトを読み込んでいます", tok_per_sec: 0, gen_tokens: 0 }));
    });
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "g1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.locator('[data-gen-phase="prefill"]')).toContainText("プリフィル中");
    const list = shell.locator("[data-assistant-todos]");
    await expect(list).toContainText("1/3");
    await expect(list).toContainText("波形の計算を実装する");
    await list.getByRole("button").click();
    await expect(list.locator('[data-todo-status="completed"]')).toContainText("完了（1 分 0 秒）");
    await expect(list.locator('[data-todo-status="in_progress"]')).toContainText("開始");
    await shell.getByRole("button", { name: /Bash/ }).click();
    await expect(shell.locator("[data-tool-time]")).toContainText("3.0 秒");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}
