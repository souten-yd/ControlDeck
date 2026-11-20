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
    await expect(shell.locator('[data-composer-pill="plan"]')).toHaveCount(0);

    // Shift+Tab: Auto → 確認 → プラン
    await input.focus();
    await input.press("Shift+Tab");
    await expect.poll(() => modes).toEqual(["ask"]);
    await expect(shell.locator('[data-composer-pill="permission"]')).toContainText("確認");   // 画面に出てから次を押す
    await input.press("Shift+Tab");
    // プランの説明欄は出さない（2026-11-19 利用者「ごちゃごちゃしている。説明はいらない」）。枠の色・📋 の札・案内文で分かる
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
    await shell.locator("[data-assistant-plus]").click();
    await page.locator('[data-mode-option="chat"]').click();
    await build.click();
    await expect.poll(() => sent.length).toBe(2);
    expect(sent[1]).toMatchObject({ mode: "code", code_agent: "build", content: "このプランで実装してください。" });
    await expect(shell.locator('[data-composer-pill="mode"] [data-pill-label]')).toHaveText("Chat");   // 固定は変えない
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
  const card = shell.locator("[data-assistant-input-card]");
  await expect(card).toHaveAttribute("data-agent", "plan");
  await shell.locator("[data-assistant-input]").press("Shift+Tab");      // プラン → ビルド（Auto のまま）
  await expect(card).toHaveAttribute("data-agent", "build");
  await expect(shell.locator('[data-composer-pill="permission"]')).toContainText("Auto");
  expect(modes).toEqual([]);                                             // もともと Auto なので送り直さない
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-agent-option="plan"]').click();
  await shell.locator('[data-composer-pill="plan"] button').click();      // 札をタップするとビルドに戻る
  await expect(card).toHaveAttribute("data-agent", "build");
});


test("auto stays auto, and a fixed mode survives reopening per conversation", async ({ page, context }) => {
  // 2026-11-18 利用者「勝手に変わる。固定している場合は変わって欲しくない。開き直すと固定が外れないか」
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  const { sent } = await mock(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  const input = shell.locator("[data-assistant-input]");
  const pill = shell.locator('[data-composer-pill="mode"] [data-pill-label]');
  await input.fill("続きを直して");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  // 自動では OpenCode を選ばない（2026-11-20 利用者「OpenCode は明示的に選ぶ必要があるように」）
  expect(sent[0]).toMatchObject({ mode_choice: "auto" });
  expect(sent[0].mode).not.toBe("code");
  await expect(pill).toHaveCount(0);                                     // 自動のまま
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-mode-option="chat"]').click();
  await expect(pill).toHaveText("Chat");
  await page.reload();
  await expect(shell.getByText("作りました")).toBeVisible();
  await expect(pill).toHaveText("Chat");                                 // 開き直しても固定のまま
  await input.fill("続きを直して");                                        // 作業の語があっても固定を優先
  await expect(shell.locator("[data-mode-detected]")).toHaveCount(0);
  await expect(pill).toHaveText("Chat");
  await shell.getByRole("button", { name: "会話の履歴" }).click();
  await page.getByRole("button", { name: "新しいチャット", exact: true }).locator("visible=true").first().click();
  await expect(pill).toHaveCount(0);                                     // 新しい会話は自動から
});

test("research with plan goes to OpenCode: investigate first, then plan", async ({ page, context }) => {
  // 2026-11-18 利用者「OpenCode で調査を実行した結果でプランを作って実行するイメージ」。
  // 2026-11-20 利用者「プランと検索を選んだら、検索の結果でプランを立てて OpenCode を実行する作りか。OpenCode へ自動で
  // 遷移しないだけだと不具合が出そう」: プランは明示の指定なので、調査と組み合わせても OpenCode で計画する
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
  // 書き込み先が分かり、選べる（2026-11-18 利用者「プロジェクトを選べない。どこに書き込まれるのか」）
  await expect(shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）")).toBeVisible();
  await expect(shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）")).toHaveValue("app");
  const input = shell.locator("[data-assistant-input]");
  await input.fill("Plan15 の作り方を検討して");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", code_research: true, code_agent: "plan" });
});

test("plan with auto plans in OpenCode, and leaving plan stops going to OpenCode", async ({ page, context }) => {
  // 2026-11-20: 自動の判定は OpenCode を選ばないが、プランを選んでいれば自動のままでも OpenCode で計画する（プランは明示の指定）
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
  const input = shell.locator("[data-assistant-input]");
  await input.fill("ログイン画面の作り直しを考えて");
  await expect(shell.locator("[data-mode-detected]")).toContainText("OpenCode");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", code_agent: "plan", mode_choice: "auto" });
  expect(Boolean(sent[0].code_research)).toBe(false);
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-agent-option="build"]').click();
  await input.fill("ログイン画面の作り直しを考えて");
  await expect(shell.locator("[data-mode-detected]")).not.toContainText("OpenCode");   // プランを外せば自動では回さない
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

test("with OpenCode chosen, a question stays in OpenCode", async ({ page, context }) => {
  // 2026-11-18 本番: 「〜できるか？」がチャットへ回り、道具の無いモデルが <tool_call> を文字で書いた。
  // 2026-11-20: OpenCode は明示的に選んだときだけ。選んでいれば質問も OpenCode で答える
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  const { sent } = await mock(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  const input = shell.locator("[data-assistant-input]");
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-mode-option="code"]').click();
  await input.fill("3レベルインバータの波形をシミュレートできるか？");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", code_project: "app" });
});


test("in auto, when the model hands the work to OpenCode the answer becomes an OpenCode answer", async ({ page, context }) => {
  // 2026-11-20 利用者「自動でも適切に OpenCode へ遷移する作りに。すぐ移る形で」。判断は会話の LLM（サーバー）。
  // 画面は自動のまま送り（OpenCode の会話なら作業フォルダも送る）、届いた handoff で OpenCode の回答に切り替える
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await establishSession(page, context);
  const { sent } = await mock(page);
  await page.unrouteAll({ behavior: "ignoreErrors" }).catch(() => undefined);
  const again = await mock(page);
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    ws.send(JSON.stringify({ type: "delta", content: "OpenCode に任せます。" }));
    setTimeout(() => ws.send(JSON.stringify({ type: "handoff", task: "テストを足す" })), 100);
    setTimeout(() => ws.send(JSON.stringify({ type: "tool", tool: { id: "t1", name: "write", status: "completed", title: "test_app.py" } })), 200);
    setTimeout(() => ws.send(JSON.stringify({ type: "delta", content: "テストを足しました。" })), 300);
    setTimeout(() => { ws.send(JSON.stringify({ type: "done", status: "completed" })); void ws.close(); }, 450);
  });
  void sent;
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  const input = shell.locator("[data-assistant-input]");
  await input.fill("テストも足しておいて");
  await expect(shell.locator("[data-mode-detected]")).not.toContainText("OpenCode");   // 画面の語では決めない
  await input.press("Enter");
  await expect.poll(() => again.sent.length).toBe(1);
  expect(again.sent[0]).toMatchObject({ mode_choice: "auto" });
  expect(again.sent[0].mode).not.toBe("code");
  const badge = shell.locator("[data-auto-handoff]");
  await expect(badge).toContainText("自動で OpenCode に任せました：テストを足す");
  await expect(shell.getByText("テストを足しました。")).toBeVisible();
  await expect(shell.getByText("OpenCode に任せます。")).toHaveCount(0);   // 引き継ぐ前の本文は消す
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
});

test("with OpenCode chosen, an attached image is sent to OpenCode, and a model that cannot read images says so", async ({ page, context }) => {
  // 2026-11-20 利用者「VLM を使う予定なので OpenCode にそのまま渡すのがいい。VLM が使えないものはモデルの判定で分かるので、
  // 使えませんと示して」。以前は画面が「画像は OpenCode に渡せません」と止めていた
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await establishSession(page, context);
  const { sent } = await mock(page);
  let canRead = true;
  await page.route(/\/api\/v1\/chat\/conversations\/p1\/attachments$/, (route) => route.fulfill({
    status: 201, json: { kind: "image", id: "0123456789abcdef.png", name: "shot.png" } }));
  await page.route(/\/api\/v1\/chat\/conversations\/p1\/send$/, (route) => {
    sent.push(route.request().postDataJSON() as Record<string, unknown>);
    return canRead
      ? route.fulfill({ status: 201, json: { user_message_id: "u9", assistant_message_id: "a9", job_id: "j9", status: "generating" } })
      : route.fulfill({ status: 422, json: { detail: "OpenCode のモデル（Maya DS-XS）は画像を読めません。画像を読めるモデルに切り替えるか、＋から Chat を選んでください" } });
  });
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("作りました")).toBeVisible();
  await shell.locator("[data-assistant-plus]").click();
  await page.locator('[data-mode-option="code"]').click();
  const attach = () => shell.locator('input[type="file"]').setInputFiles({ name: "shot.png", mimeType: "image/png", buffer: Buffer.from("x") });
  await attach();
  const input = shell.locator("[data-assistant-input]");
  await input.fill("この画面のずれを直して");
  await input.press("Enter");
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0]).toMatchObject({ mode: "code", attachments: ["0123456789abcdef.png"] });
  await expect(page.getByText("画像は OpenCode に渡せません")).toHaveCount(0);
  // 読めないモデル: サーバーの判定をそのまま見せる
  canRead = false;
  await expect(shell.getByRole("button", { name: "送信" })).toBeVisible();
  await page.waitForTimeout(400);
  await attach();
  await input.fill("これも見て");
  await input.press("Enter");
  await expect(page.getByText("画像を読めません").first()).toBeVisible();
  await expect(input).toHaveValue("これも見て");                          // 送れていない発言は入力へ戻す
  await expect(shell.locator(":not(textarea)", { hasText: /^これも見て$/ })).toHaveCount(0);   // 吹き出しは残さない
  await expect(shell.getByText("shot.png")).toBeVisible();                 // 画像も付いたまま（Chat を選べば送れる）
});
