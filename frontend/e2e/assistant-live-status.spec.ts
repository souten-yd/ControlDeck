// 目的: OpenCode の生成中に「いま: 作業中の項目 · 経過 · 段」が 1 行で出て 320px でも横にあふれないこと、道具の羅列が
//       段ごとに畳まれること、子エージェントの行に係・モデル・経過が出て 10 分動きが無ければ止める入口が出ること、
//       完了したら要約・ToDo の完了数・変更の数のカードが本文の下に出ること。
// 根拠: 2026-11-23 利用者の通し（評価 U4・U5・U6）。道具の羅列が長く今の作業が分からない、子が 1 時間近く動いても様子が
//       見えない、完了の要約が無く長い本文を読まないと分からなかった。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const user = { id: "u", role: "user", content: "作って", thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {}, created_at: "2026-11-23T00:00:00Z" };

async function open(page, context, id: string, assistant: Record<string, unknown>, generating: boolean) {
  await establishSession(page, context);
  await page.route(new RegExp(`/api/v1/chat/conversations/${id}/messages$`), (route) => route.fulfill({ json: {
    conversation: { id, title: "状態" },
    messages: [user, { id: "a", role: "assistant", thinking: "", model: "", error: "", created_at: "2026-11-23T00:00:01Z", job_id: generating ? "j" : null,
      status: generating ? "generating" : "completed", ...assistant }],
  } }));
  await page.route(new RegExp(`/api/v1/chat/conversations/${id}/queue$`), (route) => route.fulfill({ json: { items: [], generating_message_id: generating ? "a" : null, held: false } }));
  await page.routeWebSocket(/\/chat\/messages\/a\/stream/, () => undefined);
  await page.evaluate((cid) => localStorage.setItem("cd-chat-conversation", cid), id);
  await page.goto("/assistant");
}

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`now line, folded tools and subagent row while generating (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      const now = Date.now() / 1000;
      const tools = Array.from({ length: 12 }, (_, i) => ({ id: `c${i}`, name: "edit", status: "completed", input: { path: `src/a${i}.ts` }, output: "", started_at: now - 100 + i }));
      tools.push({ id: "t1", name: "subagent", status: "running", input: { subagent_type: "reviewer", description: "差分を確認" }, output: "", started_at: now - 3000,
        model: "controldeck/very-slow-local-model", last_active_at: now - 1500 } as never);
      await open(page, context, `l${width}${scheme}`, { content: "", meta: { mode: "code", tools, timeline: tools.map((t) => ({ k: "u", id: t.id })),
        todos: [{ content: "ルールを作る", status: "in_progress", started_at: now - 125, updated_at: now - 5 }, { content: "テスト", status: "pending", updated_at: now - 5 }] } }, true);
      const line = page.locator("[data-now-line]");
      await expect(line).toContainText(/いま:\s*ルールを作る/);
      await expect(line).toContainText(/2:\d\d/);
      await expect(line).toContainText("実装");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      const box = await line.boundingBox();
      expect(box!.height).toBeLessThan(24);
      await page.locator("[data-assistant-todos]").getByRole("button").click();
      await expect(page.locator("[data-todo-status='in_progress']")).toContainText("ルールを作る");
      await page.locator("[data-assistant-todos]").getByRole("button").first().click();
      await expect(page.locator("[data-code-steps-folded]")).toHaveCount(1);     // 13 件 → 8 件を畳み、残り 5 件は開いたまま
      const sub = page.locator("[data-subagent]");
      await expect(sub.locator("[data-subagent-role]")).toHaveText("reviewer");
      await expect(sub.locator("[data-subagent-model]")).toContainText("very-slow-local-model");
      await expect(sub.locator("[data-subagent-stalled]")).toContainText("止まっているかもしれません");
      await expect(sub.getByRole("button", { name: "止める" })).toBeVisible();
      await page.screenshot({ path: `test-results/live-status-${width}-${scheme}.png` });
    });

    test(`done card after an OpenCode turn (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      const content = "経緯を長く書きます。\n\n## 結果\n\nログイン画面の検証を直しました。テストは 12 件とも通りました。\n\n## 次\n\n別の話。";
      await open(page, context, `d${width}${scheme}`, { content, meta: { mode: "code", opencode_session: "ses_1",
        changes: [{ file: "src/login.tsx", additions: 3, deletions: 1, status: "modified" }],
        todos: [{ content: "直す", status: "completed" }, { content: "テスト", status: "completed" }, { content: "撮影", status: "cancelled" }] } }, false);
      const card = page.locator("[data-code-done]");
      await expect(card.locator("[data-code-done-summary]")).toHaveText(/ログイン画面の検証を直しました/);
      await expect(card.locator("[data-code-done-summary]")).not.toContainText("別の話");
      await expect(card).toContainText("ToDo 2/3 完了");
      await expect(card).toContainText("変更 1 ファイル");
      await expect(page.locator("[data-code-changes]")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/done-card-${width}-${scheme}.png` });
    });
  }
}
