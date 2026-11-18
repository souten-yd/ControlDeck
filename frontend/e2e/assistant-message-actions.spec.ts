import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

const msg = (id: string, role: "user" | "assistant", content: string) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {},
  created_at: "2026-11-17T00:00:00Z",
});

/** 会話の中身は API を差し替えて用意する（LLM を動かさない）。 */
async function mockConversation(page: Page) {
  const calls: string[] = [];
  const conversations: Record<string, ReturnType<typeof msg>[]> = {
    orig: [msg("u1", "user", "最初の質問"), msg("a1", "assistant", "最初の回答"), msg("u2", "user", "次の質問"), msg("a2", "assistant", "次の回答")],
  };
  await page.route(/\/api\/v1\/chat\/conversations\/(orig|fork1)\/messages$/, (route) => {
    const id = route.request().url().includes("/fork1/") ? "fork1" : "orig";
    return route.fulfill({ json: { conversation: { id, title: id === "orig" ? "元の会話" : "分岐: 元の会話" }, messages: conversations[id] } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/orig\/fork$/, (route) => {
    const body = route.request().postDataJSON() as { message_id: string; include: boolean };
    calls.push(`fork:${body.message_id}:${body.include}`);
    const all = conversations.orig;
    const index = all.findIndex((m) => m.id === body.message_id);
    conversations.fork1 = all.slice(0, body.include ? index + 1 : index);
    return route.fulfill({ status: 201, json: { id: "fork1", title: "分岐: 元の会話", attachments: [], documents_copied: false, opencode_context_reset: false } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/orig\/messages\/[^/]+$/, (route) => {
    calls.push(`delete:${route.request().url().split("/").pop()}`);
    return route.fulfill({ status: 204, body: "" });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/orig\/attachments$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1500)); // 「保存中…」を見せる
    return route.fulfill({ json: { kind: "image", id: "img1.png", name: "a.png" } });
  });
  return calls;
}

for (const width of [1280, 320]) {
  test(`message menu forks, deletes and edits (${width}px)`, async ({ page }) => {
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/assistant");
    await page.getByLabel("ユーザー名").fill(username!);
    await page.getByLabel("パスワード").fill(password!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page.getByLabel("ユーザー名")).toBeHidden();
    const calls = await mockConversation(page);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "orig"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("次の回答")).toBeVisible();

    // 削除: 自分の発言を消すと回答も消える（確認つき）
    const menus = shell.getByRole("button", { name: "メッセージの操作" });
    await expect(menus).toHaveCount(4);
    await menus.nth(2).click();
    const deleteItem = page.getByRole("menuitem", { name: /削除/ });
    await expect(deleteItem).toBeVisible();
    const box = await deleteItem.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await deleteItem.click();
    await page.getByRole("button", { name: "削除する" }).click();
    await expect(shell.getByText("次の質問")).toHaveCount(0);
    await expect(shell.getByText("次の回答")).toHaveCount(0);
    expect(calls).toContain("delete:u2");

    // 分岐: 回答の「ここから分岐」で新しい会話に移る
    await shell.getByRole("button", { name: "メッセージの操作" }).nth(1).click();
    await page.getByRole("menuitem", { name: /ここから分岐/ }).click();
    await expect.poll(() => calls).toContain("fork:a1:true");
    await expect.poll(() => page.evaluate(() => localStorage.getItem("cd-chat-conversation"))).toBe("fork1");

    // 編集: その場で書き直す欄が開き、取消で戻る
    await shell.getByRole("button", { name: "メッセージの操作" }).first().click();
    await page.getByRole("menuitem", { name: /編集して送り直す/ }).click();
    const editor = shell.getByLabel("発言を書き直す");
    await expect(editor).toHaveValue("最初の質問");
    await shell.getByRole("button", { name: "取消" }).click();
    await expect(editor).toHaveCount(0);

    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  });
}

test("attachment chip shows upload progress", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.goto("/assistant");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await mockConversation(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "orig"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");
  await shell.locator('input[type="file"]').setInputFiles({ name: "a.png", mimeType: "image/png", buffer: png });
  const bar = shell.getByRole("progressbar", { name: /a\.png.*送信中/ });
  await expect(bar).toBeVisible();
  await expect(bar).toContainText(/保存中|%/);
  await expect(bar).toHaveCount(0, { timeout: 5000 });
  await expect(shell.getByRole("button", { name: "a.pngを取り消す" })).toBeVisible();
});

for (const [width, height] of [[320, 640], [430, 932], [1280, 800]] as const) {
  test(`the menu of the last message stays above the composer and its items can be pressed (${width}px)`, async ({ page }) => {
    // 2026-11-18 利用者: 最後の自分の発言の「⋯」を押すと、メニューが下の入力欄に隠れて「編集」を押せなかった
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    await page.setViewportSize({ width, height });
    await page.goto("/assistant");
    await page.getByLabel("ユーザー名").fill(username!);
    await page.getByLabel("パスワード").fill(password!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page.getByLabel("ユーザー名")).toBeHidden();
    await mockConversation(page);
    // 最後が自分の発言で、会話の欄のいちばん下にある
    await page.route(/\/api\/v1\/chat\/conversations\/orig\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "orig", title: "元の会話" },
      messages: [...Array.from({ length: 6 }, (_, i) => [msg(`u${i}`, "user", `質問 ${i}`), msg(`a${i}`, "assistant", `回答 ${i}\n\n`.repeat(4))]).flat(),
        msg("ulast", "user", "インダクタンスの最小値を1マイクロヘンリーにしてまた電圧の最大値は")],
    } }));
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "orig"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("インダクタンスの最小値", { exact: false })).toBeVisible();
    const last = shell.getByRole("button", { name: "メッセージの操作" }).last();
    await last.scrollIntoViewIfNeeded();
    // 開いているときは押さない（押すと閉じる）。会話の欄が動いても、メニューは閉じずにボタンに付いて動く
    await expect(async () => {
      if ((await last.getAttribute("aria-expanded")) !== "true") await last.click();
      await expect(page.getByRole("menu", { name: "メッセージの操作" })).toBeVisible({ timeout: 1000 });
    }).toPass({ timeout: 10_000 });
    const menu = page.getByRole("menu", { name: "メッセージの操作" });
    const composer = (await shell.locator("[data-assistant-composer]").boundingBox())!;
    const box = (await menu.boundingBox())!;
    expect(box.y + box.height).toBeLessThanOrEqual(composer.y);             // 入力欄より上に収まる
    const button = (await last.boundingBox())!;
    expect(box.y + box.height <= button.y || box.y >= button.y + button.height).toBe(true);   // ボタンに重ならない
    // Tab で閉じてボタンへ戻る
    await page.keyboard.press("Tab");
    await expect(menu).toHaveCount(0);
    await expect(last).toBeFocused();
    await last.click();
    await page.getByRole("menuitem", { name: /編集して送り直す/ }).click();   // 押せる（隠れていない）
    await expect(shell.getByLabel("発言を書き直す")).toHaveValue(/インダクタンスの最小値/);
  });
}
