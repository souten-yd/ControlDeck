// 目的: OpenCode の作業中に、段階（プリフィル・思考・道具など）と時間が入力欄の下に出ること。アクションリストはある
//       ときだけ浮かび、タップで作業中・完了・時刻が見えること。道具を開くと開始・終了の時刻が見えること。
//       2026-11-27: コンテキストの使用量（使っている量/全体）が行の右端に「数値＋その右の棒」で出て、
//       終わった後も次の送信まで残ること。320px でも棒は消えず（40px に縮む）、95% を超えたら赤になること。
// 根拠: 2026-11-18 利用者「ずっと応答待ちと記載されている」「いつ終わるのかわからん」「項目の詳細に時間があれば」。
//       2026-11-27 利用者「下の統計の行の右端に 5K/20K で常に出す。数値の右に棒。80% 琥珀・95% 赤」。
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
  await expect(list).toContainText("更新後 12 操作");
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

  test(`context usage sits at the right end and stays after the turn (${width}px)`, async ({ page, context }) => {
    // 2026-11-27 利用者「右下に 5K/20K で常に出す。数値の右に棒。生成が終わった後も最後の値を出したまま」
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    await page.route(/\/api\/v1\/chat\/conversations\/ctx1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "ctx1", title: "使用量" },
      messages: [{ id: "u", role: "user", content: "作って", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-27T00:00:00Z" },
        { id: "a", role: "assistant", content: "", thinking: "", status: "generating", job_id: "j", model: "", error: "", created_at: "2026-11-27T00:00:01Z", meta: { mode: "code" } }],
    } }));
    await page.route(/\/api\/v1\/chat\/conversations\/ctx1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: "a", held: false } }));
    // step の使用量から: 入力 4600 + その step の出力 599 → used_tokens 5199 / 20000（5.2K/20K）。
    // 終わりの統計も同じ値で届き、その後に終了のイベントで閉じる（再接続の空振りをさせない）
    const stats = { type: "stats", phase: "answer", detail: "", tok_per_sec: 5.5, gen_tokens: 599,
                    prompt_tokens: 4600, context_max: 20000, used_tokens: 5199 };
    await page.routeWebSocket(/\/chat\/messages\/a\/stream/, (ws) => {
      ws.send(JSON.stringify(stats));
      ws.send(JSON.stringify({ ...stats, phase: "done" }));
      ws.send(JSON.stringify({ type: "done", status: "completed", error: "" }));
      ws.close();
    });
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "ctx1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const badge = shell.locator('[data-gen-phase="done"]');
    await expect(badge).toBeVisible();
    await expect(badge).toContainText("完了");
    const ctx = badge.getByTestId("gen-context");
    await expect(ctx).toContainText("5.2K/20K");
    // 320px では左の秒・tok/s を省く（棒は消さない）
    if (width <= 420) await expect(badge.getByText("tok/s")).toBeHidden();
    else await expect(badge.getByText("tok/s")).toBeVisible();
    const boxes = await page.evaluate(() => {
      const ctx = document.querySelector("[data-testid='gen-context']")!;
      const value = ctx.querySelector("span")!;              // 数値（使っている量/全体）
      const bar = value.nextElementSibling as HTMLElement;   // 数値の右の棒
      const row = document.querySelector("[data-assistant-composer-status]")!.getBoundingClientRect();
      const valueBox = value.getBoundingClientRect();
      const barBox = bar.getBoundingClientRect();
      return { value: { right: valueBox.right }, bar: { left: barBox.left, right: barBox.right, width: barBox.width, height: barBox.height }, row: { right: row.right } };
    });
    // 棒は数値の右側。行の右端に寄っている（行の余白は 12px）
    expect(boxes.bar.left).toBeGreaterThan(boxes.value.right);
    expect(boxes.row.right - boxes.bar.right).toBeGreaterThanOrEqual(10);
    expect(boxes.row.right - boxes.bar.right).toBeLessThanOrEqual(14);
    // 棒は高さ 6px・幅 64px（320px では 40px）。溝も見えている
    expect(boxes.bar.height).toBeGreaterThanOrEqual(5.5);
    expect(boxes.bar.width).toBeGreaterThanOrEqual(width <= 420 ? 38 : 62);
    // 生成が終わった後も最後の値を出したまま（次の送信まで）
    await page.waitForTimeout(500);
    await expect(badge.getByTestId("gen-context")).toContainText("5.2K/20K");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/gen-context-${width}.png` });
  });
}

test("context usage turns red past 95% with the bar kept on a narrow screen", async ({ page, context }) => {
  // 2026-11-27 利用者「80% を超えたら琥珀、95% を超えたら赤（数値と棒の両方）」「幅が狭くても棒は消さない」
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await establishSession(page, context);
  await page.route(/\/api\/v1\/chat\/conversations\/ctx2\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "ctx2", title: "使用量2" },
    messages: [{ id: "u", role: "user", content: "作って", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-27T00:00:00Z" },
      { id: "a", role: "assistant", content: "", thinking: "", status: "generating", job_id: "j", model: "", error: "", created_at: "2026-11-27T00:00:01Z", meta: { mode: "code" } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/ctx2\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: "a", held: false } }));
  await page.routeWebSocket(/\/chat\/messages\/a\/stream/, (ws) => {
    // 19500 + 599 = 20099 / 20000 → 100% 強 → 赤（95% 超え）
    ws.send(JSON.stringify({ type: "stats", phase: "answer", detail: "", tok_per_sec: 5, gen_tokens: 599,
                             prompt_tokens: 19500, context_max: 20000, used_tokens: 20099 }));
  });
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "ctx2"));
  await page.goto("/assistant");
  const badge = page.locator("[data-assistant-shell]").locator('[data-gen-phase="answer"]');
  await expect(badge).toContainText("20K/20K");
  await expect(badge.getByText("tok/s")).toBeHidden();      // 幅が狭いときは左の秒・tok/s を省く
  const colors = await page.evaluate(() => {
    const ctx = document.querySelector("[data-testid='gen-context']")!;
    const value = ctx.querySelector("span")!;              // 数値
    const bar = value.nextElementSibling as HTMLElement;   // 数値の右の棒
    const fill = bar.firstElementChild as HTMLElement;
    return { value: value.className, fill: fill.className, bar: bar.getBoundingClientRect() };
  });
  expect(colors.value).toContain("text-red");
  expect(colors.fill).toContain("bg-red-500");
  expect(colors.bar.width).toBeGreaterThanOrEqual(38);       // 320px でも棒は消えない（40px）
  expect(colors.bar.left).toBeGreaterThan(0);
  await page.screenshot({ path: "test-results/gen-context-red-320.png" });
});
