// 目的: OpenCode のプラン（計画だけで何も変えない）とビルドを切り替えられ、プラン中はビルドでないことがはっきり分かること。
//       Shift+Tab は Auto → 確認 → プラン → Auto。プラン中は入力欄の上に帯・札「プラン」・枠の色・案内文。
//       プランで送ると code_agent=plan が渡り、回答に「プラン」の印と「このプランで作る」が出て、押すとビルドで送る。
// 根拠: 2026-11-18 利用者「プランの切り替えを追加し、プランの際はビルドモードではないことが明確にわかること」。
//       API と WebSocket は模擬する（OpenCode と LLM を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const msg = (id: string, role: "user" | "assistant", content: string, meta: object = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta,
  created_at: "2026-11-18T00:00:00Z",
});

async function mock(page: Page) {
  const sent: Record<string, unknown>[] = [];
  const modes: string[] = [];
  let seq = 0;
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  let current = "auto";   // サーバーと同じく、変えた権限を覚えて返す
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => {
    if (route.request().method() === "PUT") {
      current = (route.request().postDataJSON() as { mode: string }).mode;
      modes.push(current);
    }
    return route.fulfill({ json: { mode: current } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/p1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "p1", title: "プラン" },
    messages: [msg("u0", "user", "作って"), msg("a0", "assistant", "作りました", { mode: "code", project_path: "/CodeDEV/app", agent: "build" })],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/p1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.route(/\/api\/v1\/chat\/conversations\/p1\/send$/, (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    seq += 1;
    return route.fulfill({ status: 201, json: { user_message_id: `u${seq}`, assistant_message_id: `a${seq}`, job_id: `j${seq}`, status: "generating" } });
  });
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    ws.send(JSON.stringify({ type: "delta", content: "1. 画面を分ける\n2. テストを足す" }));
    setTimeout(() => { ws.send(JSON.stringify({ type: "done", status: "completed" })); void ws.close(); }, 200);
  });
  return { sent, modes };
}

for (const width of [320, 1280]) {
  test(`plan is clearly shown, sent as plan and can be built afterwards (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const { sent, modes } = await mock(page);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("作りました")).toBeVisible();
    const input = shell.locator("[data-assistant-input]");
    const card = shell.locator("[data-assistant-input-card]");
    await expect(card).toHaveAttribute("data-agent", "build");
    await expect(shell.locator("[data-plan-banner]")).toHaveCount(0);

    // Shift+Tab: Auto → 確認 → プラン
    await input.focus();
    await input.press("Shift+Tab");
    await expect.poll(() => modes).toEqual(["ask"]);
    await expect(shell.locator('[data-composer-pill="permission"]')).toContainText("確認");   // 画面に出てから次を押す
    await input.press("Shift+Tab");
    const banner = shell.locator("[data-plan-banner]");
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("計画だけを立てます");
    await expect(shell.locator('[data-composer-pill="plan"]')).toContainText("プラン");
    await expect(shell.locator('[data-composer-pill="permission"]')).toHaveCount(0);
    await expect(card).toHaveAttribute("data-agent", "plan");
    await expect(input).toHaveAttribute("placeholder", /プラン/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

    await input.fill("画面を作り直す計画を立てて");
    await input.press("Enter");
    await expect.poll(() => sent.length).toBe(1);
    expect(sent[0]).toMatchObject({ mode: "code", code_agent: "plan", code_project: "app" });
    await expect(shell.locator("[data-plan-answer]")).toContainText("ファイルは変えていません");
    const build = shell.locator("[data-build-from-plan]");
    await expect(build).toBeVisible();
    // 機能をチャットに変えていても、「このプランで作る」は OpenCode で送る（レビュー指摘）
    await shell.locator('[data-composer-pill="mode"]').getByRole("button", { name: /外す/ }).click().catch(() => undefined);
    await build.click();
    await expect.poll(() => sent.length).toBe(2);
    expect(sent[1]).toMatchObject({ mode: "code", code_agent: "build", content: "このプランで実装してください。" });
    await expect(banner).toHaveCount(0);
    await expect(card).toHaveAttribute("data-agent", "build");
  });
}

test("the plus menu switches between build and plan, and Shift+Tab leaves plan back to Auto", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  const { modes } = await mock(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-agent-option="plan"]').click();
  await expect(shell.locator("[data-plan-banner]")).toBeVisible();
  await shell.locator("[data-assistant-input]").press("Shift+Tab");      // プラン → ビルド（Auto のまま）
  await expect(shell.locator("[data-plan-banner]")).toHaveCount(0);
  await expect(shell.locator('[data-composer-pill="permission"]')).toContainText("Auto");
  expect(modes).toEqual([]);                                             // もともと Auto なので送り直さない
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-agent-option="plan"]').click();
  await shell.locator("[data-plan-banner]").getByRole("button", { name: "ビルドに切り替える" }).click();
  await expect(shell.locator("[data-plan-banner]")).toHaveCount(0);
});


test("research with plan goes to OpenCode: investigate first, then plan", async ({ page, context }) => {
  // 2026-11-18 利用者「OpenCode で調査を実行した結果でプランを作って実行するイメージ」
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  const { sent } = await mock(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-agent-option="plan"]').click();
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-mode-option="investigate"]').click();
  await expect(shell.locator("[data-plan-banner]")).toContainText("調べた結果をもとに計画します");
  const input = shell.locator("[data-assistant-input]");
  await input.fill("Plan15 の作り方を検討して");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", code_research: true, code_agent: "plan" });
});

test("the plus menu stays on a short screen, scrolls inside and follows the arrow keys", async ({ page, context }) => {
  // 2026-11-18 利用者「項目が多すぎてはみ出る。スライドも微妙にできない」
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 360 });
  await establishSession(page, context);
  await mock(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  await shell.locator("[data-assistant-plus]").click();
  const menu = page.getByRole("menu", { name: "添付・機能・権限" });
  expect((await menu.boundingBox())!.y).toBeGreaterThanOrEqual(0);
  await expect(menu.locator('[data-permission-option="auto"]')).toContainText("箱の中は確認なし");   // 選ぶ前に読める
  // 会話の欄の自動スクロールでは閉じない（生成中に触っている最中に消えていた）
  await shell.getByText("作りました").evaluate((el) => el.dispatchEvent(new Event("scroll")));
  await expect(menu).toBeVisible();
  await menu.hover();
  await page.mouse.wheel(0, 1000);
  await expect.poll(() => menu.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  await expect(menu).toBeVisible();                                      // 中のスクロールでは閉じない
  await expect(menu.locator('[data-permission-option="ask"]')).toBeInViewport();
  await menu.locator('[data-mode-option="auto"]').focus();
  await page.keyboard.press("ArrowRight");
  await expect(menu.locator('[data-mode-option="chat"]')).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.locator('[data-mode-option="run"]')).toBeFocused();    // 真下のタイル
});

test("a question during an OpenCode conversation stays in OpenCode", async ({ page, context }) => {
  // 2026-11-18 本番: 「〜できるか？」がチャットへ回り、道具の無いモデルが <tool_call> を文字で書いた
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  const { sent } = await mock(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  const input = shell.locator("[data-assistant-input]");
  await input.fill("3レベルインバータの波形をシミュレートできるか？");
  await expect(shell.locator("[data-mode-detected]")).toContainText("OpenCode");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", code_project: "app" });
});
