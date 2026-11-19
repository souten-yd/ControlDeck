import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

const DAY = 86_400_000;
const FAILED_ERROR = "RuntimeProviderError: Strataモデルが未登録・未起動、または読み込みに失敗しました";

async function login(page: Page, path = "/assistant") {
  await page.goto(path);
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
}

const msg = (id: string, role: "user" | "assistant", content: string, extra: Record<string, unknown> = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {},
  created_at: "2026-11-17T00:00:00Z", ...extra,
});

/** 会話の一覧と中身は API を差し替えて用意する（LLM を動かさない）。 */
async function mockChat(page: Page) {
  const calls: string[] = [];
  const now = Date.now();
  let archived: { id: string; title: string; updated_at: string }[] = [];
  let conversations = [
    { id: "today1", title: "今日の献立の相談", updated_at: new Date(now).toISOString().replace("Z", "") },
    { id: "failed", title: "グランフロントの店", updated_at: new Date(now - 60_000).toISOString().replace("Z", "") },
    { id: "yest1", title: "昨日の買い物メモ", updated_at: new Date(now - DAY).toISOString().replace("Z", "") },
    { id: "week1", title: "週の振り返り", updated_at: new Date(now - 4 * DAY).toISOString().replace("Z", "") },
    { id: "old1", title: "先月の旅行計画", updated_at: new Date(now - 40 * DAY).toISOString().replace("Z", "") },
  ];
  const plan = { mode: "chat", reason: "通常の対話", steps: [], max_iterations: 1, decided_by: "rule" };
  const messages: Record<string, ReturnType<typeof msg>[]> = {
    failed: [
      msg("u1", "user", "グランフロントの店を教えて"),
      msg("a1", "assistant", "", { status: "failed", error: FAILED_ERROR, meta: { plan } }),
      msg("u2", "user", "もう一度お願い"),
      msg("a2", "assistant", "", { status: "canceled", error: "キャンセルされました", meta: { plan } }),
    ],
  };
  await page.route(/\/api\/v1\/chat\/conversations(\?archived=true)?$/, (route) => {
    if (route.request().method() === "POST") return route.fulfill({ status: 201, json: { id: "new1", title: "新しい会話", updated_at: new Date().toISOString() } });
    return route.fulfill({ json: route.request().url().endsWith("archived=true") ? archived : conversations });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/[^/]+\/messages$/, (route) => {
    const id = route.request().url().split("/").slice(-2)[0];
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return route.fulfill({ status: 404, json: { detail: "会話が見つかりません" } });
    return route.fulfill({ json: { conversation, messages: messages[id] ?? [msg(`${id}-u`, "user", `${conversation.title}の質問`), msg(`${id}-a`, "assistant", `${conversation.title}の回答`)] } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/[^/]+$/, (route) => {
    const id = route.request().url().split("/").pop()!;
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as { title?: string; archived?: boolean };
      if (body.archived !== undefined) {
        calls.push(`${body.archived ? "archive" : "unarchive"}:${id}`);
        const row = [...conversations, ...archived].find((item) => item.id === id)!;
        conversations = body.archived ? conversations.filter((item) => item.id !== id) : [row, ...conversations];
        archived = body.archived ? [row, ...archived] : archived.filter((item) => item.id !== id);
        return route.fulfill({ json: { ...row, archived: body.archived } });
      }
      const title = body.title!;
      calls.push(`rename:${id}:${title}`);
      conversations = conversations.map((item) => (item.id === id ? { ...item, title } : item));
      return route.fulfill({ json: conversations.find((item) => item.id === id) });
    }
    if (route.request().method() === "DELETE") {
      calls.push(`delete:${id}`);
      conversations = conversations.filter((item) => item.id !== id);
      return route.fulfill({ status: 204, body: "" });
    }
    return route.fallback();
  });
  await page.route(/\/api\/v1\/chat\/route$/, (route) => route.fulfill({ json: plan }));
  await page.route(/\/api\/v1\/chat\/conversations\/[^/]+\/send$/, (route) => {
    const body = route.request().postDataJSON() as { content: string; mode: string };
    calls.push(`send:${body.content}`);
    calls.push(`mode:${body.mode}`);
    return route.fulfill({ json: { assistant_message_id: "a-retry", user_message_id: "u-retry" } });
  });
  return calls;
}

const noOverflow = async (page: Page, width: number) =>
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);

for (const width of [1280, 320]) {
  test(`history list: groups, search, open, rename, delete, new chat (${width}px)`, async ({ page }) => {
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    const mobile = width < 1024;
    await page.setViewportSize({ width, height: 800 });
    await login(page);
    const calls = await mockChat(page);
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.locator("[data-assistant-title]")).toHaveText("新しいチャット");
    // 上の行の切替（select）と新規・削除ボタンは無い。履歴へ移した。
    await expect(shell.locator("[data-assistant-topbar] select")).toHaveCount(0);
    await expect(shell.getByRole("button", { name: "選択中の会話を削除" })).toHaveCount(0);

    const openHistory = async () => {
      if (!mobile) return page.locator("[data-assistant-history-sidebar]");
      const sheet = page.getByRole("dialog", { name: "会話の履歴" });
      if (!(await sheet.isVisible())) await shell.getByRole("button", { name: "会話の履歴" }).click();
      await expect(sheet).toBeVisible();
      return sheet;
    };
    let history = await openHistory();
    if (mobile) {
      // ☰ はアプリ全体のドロワーのまま、履歴は別のボタン。
      await expect(shell.getByRole("button", { name: "メニューを開く" })).toBeVisible();
      await expect(history).toHaveAttribute("aria-modal", "true");
    } else {
      await expect(history).toBeVisible();
    }

    // 日付ごとのまとまり
    await expect(history.locator("[data-history-group]")).toHaveCount(4);
    for (const [group, title] of [["今日", "今日の献立の相談"], ["昨日", "昨日の買い物メモ"], ["過去7日間", "週の振り返り"], ["それ以前", "先月の旅行計画"]] as const) {
      const section = history.getByRole("region", { name: group, exact: true });
      await expect(section.getByRole("heading", { name: group })).toBeVisible();
      await expect(section.getByRole("button", { name: title, exact: true })).toBeVisible();
    }

    // 検索（題名で絞り込む）
    const search = history.getByPlaceholder("会話を検索");
    await search.fill("買い物");
    await expect(history.locator("[data-history-row]")).toHaveCount(1);
    await expect(history.locator("[data-history-group]")).toHaveCount(1);
    await search.fill("どこにも無い題名");
    await expect(history.getByText("一致する会話はありません")).toBeVisible();
    await search.fill("");
    await expect(history.locator("[data-history-row]")).toHaveCount(5);

    // 開く: 題名が上の行に出て、いまの会話に印が付く
    await history.getByRole("button", { name: "昨日の買い物メモ", exact: true }).click();
    await expect(shell.locator("[data-assistant-title]")).toHaveText("昨日の買い物メモ");
    await expect(shell.getByText("昨日の買い物メモの回答")).toBeVisible();
    if (mobile) await expect(page.getByRole("dialog", { name: "会話の履歴" })).toHaveCount(0);
    history = await openHistory();
    await expect(history.locator('[aria-current="true"]')).toHaveText("昨日の買い物メモ");

    // 名前を変更（行の ⋯）
    const menuButton = history.getByRole("button", { name: "「週の振り返り」の操作" });
    if (!mobile) await history.locator('[data-history-row="week1"]').hover();
    const menuButtonBox = (await menuButton.boundingBox())!;
    expect(menuButtonBox.width).toBeGreaterThanOrEqual(44);
    expect(menuButtonBox.height).toBeGreaterThanOrEqual(44);
    await menuButton.click();
    const renameItem = page.getByRole("menuitem", { name: "名前を変更" });
    const itemBox = (await renameItem.boundingBox())!;
    expect(itemBox.height).toBeGreaterThanOrEqual(44);
    expect(itemBox.x).toBeGreaterThanOrEqual(0);
    expect(itemBox.x + itemBox.width).toBeLessThanOrEqual(width);
    await renameItem.click();
    const editor = history.getByLabel("「週の振り返り」の新しい名前");
    await editor.fill("今週のまとめ");
    await editor.press("Enter");
    await expect.poll(() => calls).toContain("rename:week1:今週のまとめ");
    await expect(history.getByRole("button", { name: "今週のまとめ", exact: true })).toBeVisible();

    // 削除（確認つき）
    if (!mobile) await history.locator('[data-history-row="old1"]').hover();
    await history.getByRole("button", { name: "「先月の旅行計画」の操作" }).click();
    await page.getByRole("menuitem", { name: "削除" }).click();
    await page.getByRole("button", { name: "削除する" }).click();
    await expect.poll(() => calls).toContain("delete:old1");
    await expect(history.locator('[data-history-row="old1"]')).toHaveCount(0);
    await expect(shell.locator("[data-assistant-title]")).toHaveText("昨日の買い物メモ");

    // 上の行の題名を押して名前を変更
    if (mobile) await page.keyboard.press("Escape");
    await shell.locator("[data-assistant-title]").click();
    const titleEditor = shell.getByLabel("会話名", { exact: true });
    await titleEditor.fill("買い物メモ（改）");
    await titleEditor.press("Enter");
    await expect.poll(() => calls).toContain("rename:yest1:買い物メモ（改）");
    await expect(shell.locator("[data-assistant-title]")).toHaveText("買い物メモ（改）");

    // 新しいチャット
    history = await openHistory();
    await history.getByRole("button", { name: "新しいチャット" }).click();
    await expect(shell.locator("[data-assistant-title]")).toHaveText("新しいチャット");
    await expect(shell.locator("[data-assistant-empty]")).toBeVisible();
    expect(await page.evaluate(() => localStorage.getItem("cd-chat-conversation"))).toBeNull();
    if (mobile) await expect(page.getByRole("dialog", { name: "会話の履歴" })).toHaveCount(0);

    if (!mobile) {
      // PC: 履歴の列は畳める。上の行のボタンで戻す。畳んだ状態は端末に覚える。
      await history.getByRole("button", { name: "履歴を閉じる" }).click();
      await expect(page.locator("[data-assistant-history-sidebar]")).toHaveCount(0);
      expect(await page.evaluate(() => localStorage.getItem("cd-assistant-history"))).toBe("min");
      await shell.getByRole("button", { name: "会話の履歴" }).click();
      await expect(page.locator("[data-assistant-history-sidebar]")).toBeVisible();
    }
    await noOverflow(page, width);
  });
}

for (const width of [1280, 320]) {
  test(`mode chip menu in the composer (${width}px)`, async ({ page }) => {
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    await page.setViewportSize({ width, height: 700 });
    await login(page);
    const calls = await mockChat(page);
    await page.route(/\/api\/v1\/meta$/, async (route) => {
      const response = await route.fetch();
      const meta = await response.json();
      meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
      return route.fulfill({ response, json: meta });
    });
    await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [] } }));
    await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    // 添付・機能・権限は左下の＋に 1 つにまとめた（2026-11-18 利用者の依頼: 並べると煩雑）
    const plus = shell.locator("[data-assistant-plus]");
    const detected = shell.locator("[data-mode-detected]");
    const modePill = shell.locator('[data-composer-pill="mode"]');
    await expect(detected).toContainText("自動");
    await expect(shell.getByRole("button", { name: /ファイルを添付/ })).toHaveCount(0);   // 単独のクリップは無い
    const plusBox = (await plus.boundingBox())!;
    expect(plusBox.height).toBeGreaterThanOrEqual(44);
    expect(plusBox.width).toBeGreaterThanOrEqual(44);

    // 自動判定は入力に合わせて表示が変わる
    const textarea = shell.locator("[data-assistant-input]");
    await textarea.fill("最新のGPUニュースを検索して");
    await expect(detected).toContainText("Web Search");
    await textarea.fill("");

    await plus.click();
    const menu = page.getByRole("menu", { name: "添付・機能・権限" });
    await expect(menu).toBeVisible();
    await expect(menu.getByRole("menuitem", { name: /ファイルを添付/ })).toBeVisible();
    // 検索系は「調査」1 つにまとめた（2026-11-17: 利用者「検索を改修したはずだがこの部分は変わらないのか」）
    for (const id of ["auto", "chat", "investigate", "gen", "run", "code"]) {
      await expect(menu.locator(`[data-mode-option="${id}"]`)).toBeAttached();
    }
    for (const id of ["web", "academic", "deep", "research"]) {
      await expect(menu.locator(`[data-mode-option="${id}"]`)).toHaveCount(0);
    }
    await expect(menu.locator('[data-mode-option="auto"]')).toHaveAttribute("aria-checked", "true");
    // OpenCode が使えるなら、機能の判定に関係なく権限を選べる（以前は OpenCode の判定中だけ出て、消えた）
    await expect(menu.getByRole("group", { name: "OpenCode の権限" }).locator('[data-permission-option="auto"]')).toHaveAttribute("aria-checked", "true");
    const menuBox = (await menu.boundingBox())!;
    expect(menuBox.x).toBeGreaterThanOrEqual(0);
    expect(menuBox.x + menuBox.width).toBeLessThanOrEqual(width);
    expect(menuBox.y).toBeGreaterThanOrEqual(0);
    expect(menuBox.y + menuBox.height).toBeLessThanOrEqual(plusBox.y);
    // 全部が 1 画面に収まる高さ（2026-11-18 利用者「項目が多すぎてはみ出る。高さを減らして」。前は約 1200px）
    expect(menuBox.height).toBeLessThanOrEqual(480);
    expect(await menu.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1);
    const option = menu.locator('[data-mode-option="investigate"]');
    await option.scrollIntoViewIfNeeded();
    expect((await option.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await option.click();
    await expect(menu).toHaveCount(0);
    await expect(modePill.locator("[data-pill-label]")).toHaveText("調査");
    // 深さは入力欄の下。「深い」を選んで送ると Deep Research になる
    const depth = shell.getByLabel("調べる深さ");
    await expect(depth).toHaveValue("auto");
    await depth.selectOption("deep");
    await textarea.fill("SSD の寿命について");
    await shell.getByRole("button", { name: "送信" }).click();
    await expect.poll(() => calls).toContain("mode:deep");
    // 札の × で自動判定に戻る
    await modePill.getByRole("button").click();                                 // 札はタップで外れる（× は無い）
    await expect(modePill).toHaveCount(0);
    await expect(detected).toBeVisible();

    // Escape で閉じる
    await plus.click();
    await expect(menu).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(menu).toHaveCount(0);

    // OpenCode を選ぶと、権限の札が＋の隣に出る。押すと切り替わる
    await plus.click();
    await menu.locator('[data-mode-option="code"]').click();
    await expect(modePill.locator("[data-pill-label]")).toHaveText("OpenCode");
    const permissionPill = shell.locator('[data-composer-pill="permission"]');
    await expect(permissionPill.locator("[data-pill-label]")).toHaveText("Auto");
    await expect(shell.getByPlaceholder("プロジェクト名（例: my-app）")).toBeVisible();

    for (const name of ["添付・機能・権限", "音声で入力", "送信"]) {
      const box = (await shell.getByRole("button", { name, exact: true }).boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
    }
    await noOverflow(page, width);
  });
}

test("a chat can be handed off to OpenCode from the plus menu in the same conversation", async ({ page }) => {
  // 2026-11-18 利用者の依頼: チャットで始めた会話の結果を、同じ会話の中で OpenCode に引き継ぐ
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await login(page);
  const calls = await mockChat(page);
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "today1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("今日の献立の相談の回答")).toBeVisible();

  await shell.locator("[data-assistant-plus]").click();
  const menu = page.getByRole("menu", { name: "添付・機能・権限" });
  const handoff = menu.locator('[data-plus-action="handoff"]');
  await expect(handoff).toContainText("OpenCode に引き継ぐ");
  expect((await handoff.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await handoff.click();
  await expect(shell.locator('[data-composer-pill="mode"] [data-pill-label]')).toHaveText("OpenCode");
  await expect(shell.locator("[data-assistant-input]")).toHaveValue("ここまでの会話の内容をもとに実装してください。");
  await expect(shell.locator('[data-composer-pill="permission"]')).toBeVisible();
  // OpenCode に切り替えた後は、引き継ぎの操作は出さない
  await shell.locator("[data-assistant-plus]").click();
  await expect(menu.locator('[data-plus-action="handoff"]')).toHaveCount(0);
  await page.keyboard.press("Escape");
  expect(calls.filter((c) => c.startsWith("send:"))).toHaveLength(0);
});

test("an archive is attached for OpenCode, switches to OpenCode and is sent with the message", async ({ page }) => {
  // 2026-11-18 利用者の依頼: ZIP・RAR などを添付して、OpenCode のプロジェクトへ展開して使う
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await login(page);
  await mockChat(page);
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/chat\/conversations\/[^/]+\/attachments$/, (route) => route.fulfill({
    status: 201, json: { kind: "archive", id: "0123456789abcdef.zip", name: "tool.zip", files: 12, bytes: 4096 } }));
  let sent: { mode?: string; attachments?: string[]; content?: string } | null = null;
  await page.route(/\/api\/v1\/chat\/conversations\/[^/]+\/send$/, (route) => {
    sent = route.request().postDataJSON();
    return route.fulfill({ json: { assistant_message_id: "a-code", user_message_id: "u-code", job_id: "j1" } });
  });
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "today1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("今日の献立の相談の回答")).toBeVisible();
  await shell.locator('input[type="file"]').setInputFiles({ name: "tool.zip", mimeType: "application/zip", buffer: Buffer.from("PK") });
  await expect(shell.getByText("12 ファイル・送ると展開")).toBeVisible();
  await expect(shell.locator("[data-mode-detected]")).toContainText("OpenCode");    // 自動のまま OpenCode へ回す
  await expect(shell.locator('[data-composer-pill="mode"]')).toHaveCount(0);
  // 文字を打たなくても送れる（中身の説明を頼む）
  await shell.getByRole("combobox").first().selectOption("app").catch(() => {});
  await shell.getByRole("button", { name: "送信" }).click();
  await expect.poll(() => sent?.mode).toBe("code");
  expect(sent!.attachments).toEqual(["0123456789abcdef.zip"]);
  expect(sent!.content).toContain("圧縮ファイル");
  await expect(shell.getByLabel("送った圧縮ファイル")).toContainText("tool.zip");
  await expect(shell.getByText("12 ファイル・送ると展開")).toHaveCount(0);
});

/** 自動判定が会話の流れを見て OpenCode へ回すか（2026-11-18 利用者の依頼: 引き継ぎ・調べて渡すをボタンなしで） */
async function sendWithAutoRouting(page: Page, { code, text, image = false }: { code: boolean; text: string; image?: boolean }) {
  await page.setViewportSize({ width: 390, height: 800 });
  await login(page);
  await mockChat(page);
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  if (code) {
    await page.route(/\/api\/v1\/chat\/conversations\/today1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "today1", title: "今日の献立の相談", updated_at: new Date().toISOString().replace("Z", "") },
      messages: [msg("u1", "user", "献立アプリを作って"),
        msg("a1", "assistant", "作りました", { meta: { mode: "code", opencode_session: "s1", tools: [] } })],
    } }));
  }
  if (image) {
    await page.route(/\/api\/v1\/chat\/conversations\/[^/]+\/attachments$/, (route) => route.fulfill({
      status: 201, json: { kind: "image", id: "0123456789abcdef.png", name: "shot.png" } }));
  }
  const state: { sent: { mode?: string; code_research?: boolean; attachments?: string[] } | null } = { sent: null };
  await page.route(/\/api\/v1\/chat\/conversations\/[^/]+\/send$/, (route) => {
    state.sent = route.request().postDataJSON();
    return route.fulfill({ json: { assistant_message_id: "a-x", user_message_id: "u-x", job_id: "j1" } });
  });
  await page.route(/\/api\/v1\/chat\/route$/, (route) => route.fulfill({ json: { mode: "chat", reason: "通常の対話", steps: [], max_iterations: 1, decided_by: "rule" } }));
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "today1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText(code ? "作りました" : "今日の献立の相談の回答")).toBeVisible();
  if (image) {
    await shell.locator('input[type="file"]').setInputFiles({ name: "shot.png", mimeType: "image/png", buffer: Buffer.from("x") });
  }
  await shell.locator("[data-assistant-input]").fill(text);
  const detected = await shell.locator("[data-mode-detected]").innerText();
  if (detected.includes("OpenCode")) await shell.getByRole("combobox").first().selectOption("app", { timeout: 2000 }).catch(() => {});
  await shell.getByRole("button", { name: "送信" }).click();
  await expect.poll(() => state.sent?.mode).toBeTruthy();
  return { sent: state.sent!, detected };
}

test("a chat answer is handed to OpenCode by just asking to implement it", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const { sent, detected } = await sendWithAutoRouting(page, { code: false, text: "これを実装して" });
  expect(detected).toContain("OpenCode");
  expect(sent.mode).toBe("code");
  expect(Boolean(sent.code_research)).toBe(false);
});

test("a question that only mentions web apps stays in chat", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const { sent } = await sendWithAutoRouting(page, { code: false, text: "ウェブアプリとネイティブの違いは?" });
  expect(sent.mode).not.toBe("code");
});

test("in an OpenCode conversation, asking to look something up and apply it researches first", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const { sent, detected } = await sendWithAutoRouting(page, { code: true, text: "最新の SQLite の WAL の仕様を調べて反映して" });
  expect(detected).toContain("OpenCode");
  expect(sent.mode).toBe("code");
  expect(sent.code_research).toBe(true);
});

test("in an OpenCode conversation, an ordinary request stays with OpenCode", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const { sent } = await sendWithAutoRouting(page, { code: true, text: "テストも足しておいて" });
  expect(sent.mode).toBe("code");
  expect(Boolean(sent.code_research)).toBe(false);
});

test("in an OpenCode conversation, a plain question stays in OpenCode", async ({ page }) => {
  // 2026-11-18 本番: チャットへ回すと道具が無く、モデルが <tool_call> を文字で書いた。チャットは＋で選ぶ
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const { sent } = await sendWithAutoRouting(page, { code: true, text: "ありがとう、ところで WAL って何?" });
  expect(sent.mode).toBe("code");
});

test("in an OpenCode conversation, an image goes to the chat that can see it", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const { sent } = await sendWithAutoRouting(page, { code: true, text: "この画面を直して", image: true });
  expect(sent.mode).not.toBe("code");
  expect(sent.attachments).toEqual(["0123456789abcdef.png"]);
});

for (const width of [320, 1280]) {
  test(`a long thinking trace opens from its heading and closes by tapping inside it at ${width}px`, async ({ page }) => {
    // 2026-11-18 利用者の依頼: 思考が長いと開きっぱなしで上へ戻るのが大変。開いた欄の中のタップでも閉じる
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    await page.setViewportSize({ width, height: 700 });
    await login(page);
    await mockChat(page);
    const longThinking = Array.from({ length: 120 }, (_, i) => `考えている ${i + 1} 行目。`).join("\n");
    // 思考の後ろに長い回答を置く（閉じても下に続きがあり、戻さなければ見出しが画面の外のまま）
    const longAnswer = Array.from({ length: 80 }, (_, i) => `回答の ${i + 1} 行目。`).join("\n\n");
    await page.route(/\/api\/v1\/chat\/conversations\/today1\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "today1", title: "今日の献立の相談", updated_at: new Date().toISOString().replace("Z", "") },
      messages: [msg("u1", "user", "献立を考えて"), msg("a1", "assistant", longAnswer, { thinking: longThinking })],
    } }));
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "today1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const head = shell.getByRole("button", { name: /思考プロセス/ });
    await expect(head).toHaveAttribute("aria-expanded", "false");          // 回答済みは閉じて出す
    await head.scrollIntoViewIfNeeded();
    await head.click();
    await expect(head).toHaveAttribute("aria-expanded", "true");
    const body = shell.locator("[data-thinking-body]");
    await expect(body).toContainText("120 行目");
    await body.getByText("考えている 110 行目。", { exact: false }).scrollIntoViewIfNeeded();
    await expect(head).not.toBeInViewport();                               // 長い思考の下の方にいる
    await body.getByText("考えている 110 行目。", { exact: false }).click();
    await expect(head).toHaveAttribute("aria-expanded", "false");
    await expect(body).toHaveCount(0);
    await expect(head).toBeInViewport();                                   // 見出しまで戻っている
    await head.click();
    await expect(head).toHaveAttribute("aria-expanded", "true");           // もう一度開ける
    await body.getByText("考えている 3 行目。", { exact: false }).dblclick();
    await page.waitForTimeout(400);
    await expect(head).toHaveAttribute("aria-expanded", "true");           // ダブルクリック（語の選択）では閉じない
    await head.click();
    await expect(head).toHaveAttribute("aria-expanded", "false");          // 見出しでも閉じる
    await noOverflow(page, width);
  });
}

test("an OpenCode answer shows thinking split around its tools and reopening keeps the work folder", async ({ page }) => {
  // 2026-11-18 利用者: 思考が全部 1 つの窓に入っている（操作の前後で分かれるはず）／開き直すと作業フォルダが外れる
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 390, height: 900 });
  await login(page);
  await mockChat(page);
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [
    { name: "other", path: "/CodeDEV/other", git: true }, { name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/chat\/conversations\/today1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "today1", title: "今日の献立の相談", updated_at: new Date().toISOString().replace("Z", "") },
    messages: [msg("u1", "user", "直して"), msg("a1", "assistant", "直しました。", {
      thinking: "まず見る。a.py を直す。",
      meta: { mode: "code", project_path: "/CodeDEV/app", opencode_session: "s1",
        tools: [{ id: "call_1", name: "shell", status: "completed", input: { command: "ls" }, output: "a.py" },
          { id: "call_2", name: "edit", status: "completed", input: { filePath: "a.py" }, output: "" }],
        timeline: [{ k: "t", from: 0, to: 5 }, { k: "u", id: "call_1" }, { k: "t", from: 5, to: 14 },
          { k: "u", id: "call_2" }, { k: "x", from: 0, to: 6 }] },
    })],
  } }));
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "today1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("直しました。")).toBeVisible();
  const heads = shell.getByRole("button", { name: /思考プロセス/ });
  await expect(heads).toHaveCount(2);                       // 道具の前と後で別の窓
  for (const head of await heads.all()) await head.click();
  const text = await shell.locator("[data-assistant-messages]").innerText();
  const order = ["まず見る。", "ls", "a.py を直す。", "a.py", "直しました。"].map((s, i, all) => text.indexOf(s, i ? text.indexOf(all[i - 1]) : 0));
  expect(order.every((x) => x >= 0)).toBe(true);
  expect([...order].sort((a, b) => a - b)).toEqual(order);  // 届いた順
  // 作業フォルダはこの会話のプロジェクト（app）に戻っている
  await shell.locator("[data-assistant-plus]").click();
  await page.getByRole("menu", { name: "添付・機能・権限" }).locator('[data-mode-option="code"]').click();
  await expect(shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）")).toHaveValue("app");
});

test("failed and canceled answers show their status, the error and a retry", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 800 });
  await login(page);
  const calls = await mockChat(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "failed"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  const failed = shell.locator('[data-assistant-unsettled="failed"]');
  await expect(failed).toBeVisible();
  await expect(failed).toHaveAttribute("role", "alert");
  await expect(failed).toContainText("回答を生成できませんでした");
  await expect(failed).toContainText("Strataモデルが未登録・未起動、または読み込みに失敗しました");
  const canceled = shell.locator('[data-assistant-unsettled="canceled"]');
  await expect(canceled).toContainText("回答を停止しました");
  const box = (await failed.boundingBox())!;
  expect(box.x + box.width).toBeLessThanOrEqual(320);
  // 本文の無い失敗でも、削除はできる（コピーは出さない）
  await shell.locator('[data-assistant-message="failed"]').getByRole("button", { name: "メッセージの操作" }).click();
  await expect(page.getByRole("menuitem", { name: /削除/ })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /コピー/ })).toHaveCount(0);
  await page.keyboard.press("Escape");

  // 再試行は最後の回答だけ（途中の失敗から送り直すと、会話の末尾に古い質問が重なるため）
  await expect(failed.getByRole("button", { name: "再試行" })).toHaveCount(0);
  const retry = canceled.getByRole("button", { name: "再試行" });
  expect((await retry.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  // 入力欄に下書きがある間は出さない（下書きを巻き込まない）
  await shell.locator("textarea").first().fill("書きかけ");
  await expect(retry).toHaveCount(0);
  await shell.locator("textarea").first().fill("");
  await retry.click();
  await expect.poll(() => calls).toContain("send:もう一度お願い");
  await noOverflow(page, 320);
});

const SHELL_PAGES = ["/", "/apps", "/workflows", "/files", "/terminal", "/models", "/knowledge", "/logs", "/system", "/settings"];

for (const viewport of [{ width: 390, height: 844 }, { width: 320, height: 700 }]) {
  test(`standalone iPhone: top bar and composer stay inside the screen (${viewport.width}px)`, async ({ page }) => {
    test.setTimeout(90_000);
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    const SAFE_TOP = 59;
    const SAFE_BOTTOM = 34;
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
    });
    // Chromium は Safe Area を持たないので、CSS 変数の境界から iPhone の値を入れる。
    const applySafeArea = () => page.evaluate(([top, bottom]) => {
      document.documentElement.style.setProperty("--cd-safe-area-top", `${top}px`);
      document.documentElement.style.setProperty("--cd-safe-area-bottom", `${bottom}px`);
    }, [SAFE_TOP, SAFE_BOTTOM]);
    await page.setViewportSize(viewport);
    await login(page);
    await mockChat(page);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "today1"));
    await page.goto("/assistant");
    await applySafeArea();
    await expect(page.locator("html")).toHaveClass(/pwa-standalone/);
    // iOS 26 の上端のぼかし対策: 不透明で全幅の帯が上端にあり、押された判定を受ける
    const strip = await page.locator("[data-status-strip]").evaluate((el) => {
      const r = el.getBoundingClientRect();
      const style = getComputedStyle(el);
      return { top: r.top, width: r.width, height: r.height, position: style.position, background: style.backgroundColor, hit: document.elementFromPoint(innerWidth / 2, 1) === el };
    });
    expect(strip).toMatchObject({ top: 0, width: viewport.width, position: "fixed", hit: true });
    expect(strip.height).toBe(SAFE_TOP);
    expect(strip.background).not.toMatch(/rgba\(.*, 0\)|transparent/);
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("今日の献立の相談の回答")).toBeVisible();

    const layout = await page.evaluate(() => {
      const rect = (selector: string) => document.querySelector(selector)!.getBoundingClientRect().toJSON() as DOMRect;
      const topbar = document.querySelector<HTMLElement>("[data-assistant-topbar]")!;
      const style = getComputedStyle(topbar);
      return {
        height: window.innerHeight,
        root: rect("#root"),
        rootPosition: getComputedStyle(document.getElementById("root")!).position,
        topbar: rect("[data-assistant-topbar]"),
        topbarBackground: style.backgroundColor,
        topbarBackdrop: style.backdropFilter,
        menu: rect('[data-assistant-topbar] [aria-label="メニューを開く"]'),
        title: rect("[data-assistant-title]"),
        messages: rect("[data-assistant-messages]"),
        composer: rect("[data-assistant-composer]"),
        card: rect("[data-assistant-input-card]"),
        status: rect("[data-assistant-composer-status]"),
        scrollY: window.scrollY,
        scrollWidth: document.documentElement.scrollWidth,
      };
    });
    // 殻は画面へ直接貼ってあり、文書の高さに切られない。
    expect(layout.rootPosition).toBe("fixed");
    expect(layout.root.top).toBe(0);
    expect(layout.root.bottom).toBe(layout.height);
    expect(layout.scrollY).toBe(0);
    // 上: 上の行は画面の上端から始まり、操作と題名はステータスバーの下。面は不透明。
    expect(layout.topbar.top).toBe(0);
    expect(layout.menu.top).toBeGreaterThanOrEqual(SAFE_TOP);
    expect(layout.title.top).toBeGreaterThanOrEqual(SAFE_TOP);
    expect(layout.topbar.bottom).toBe(SAFE_TOP + 56 + 1);
    expect(layout.topbarBackground).not.toMatch(/\/|rgba|transparent/); // 透けていない
    expect(layout.topbarBackdrop).toBe("none");
    expect(layout.messages.top).toBeGreaterThanOrEqual(layout.topbar.bottom);
    // 下: 入力カードは画面の中にあり、ホームバー（下端から約 13px）に重ならない。
    expect(layout.composer.bottom).toBe(layout.height);
    expect(layout.card.bottom).toBeLessThanOrEqual(layout.height - SAFE_BOTTOM);
    expect(layout.status.bottom).toBeLessThanOrEqual(layout.height - 13);
    expect(layout.card.top).toBeGreaterThanOrEqual(layout.messages.bottom);
    expect(layout.scrollWidth).toBeLessThanOrEqual(viewport.width);

    // 会話を動かしても、上の行と入力欄は動かない。
    await page.locator("[data-assistant-messages]").evaluate((element) => { element.scrollTop = element.scrollHeight; });
    const after = await page.evaluate(() => ({
      top: document.querySelector("[data-assistant-topbar]")!.getBoundingClientRect().top,
      bottom: document.querySelector("[data-assistant-composer]")!.getBoundingClientRect().bottom,
    }));
    expect(after.top).toBe(0);
    expect(after.bottom).toBe(layout.height);

    // 履歴のシートも Safe Area の中。
    await shell.getByRole("button", { name: "会話の履歴" }).click();
    const sheet = page.getByRole("dialog", { name: "会話の履歴" });
    await expect(sheet).toBeVisible();
    expect((await sheet.getByRole("button", { name: "履歴を閉じる" }).boundingBox())!.y).toBeGreaterThanOrEqual(SAFE_TOP);
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);

    // ほかの画面: 上の行は上端から、操作はステータスバーの下、中身の下端にホームバーの余白。
    for (const path of SHELL_PAGES) {
      await page.goto(path);
      await applySafeArea();
      await expect(page.locator("[data-shell-topbar]")).toBeVisible();
      const shellLayout = await page.evaluate(() => {
        const topbar = document.querySelector("[data-shell-topbar]")!.getBoundingClientRect();
        const menu = document.querySelector('[data-shell-topbar] [aria-label="メニューを開く"]')!.getBoundingClientRect();
        const main = document.querySelector("main")!;
        const root = document.getElementById("root")!.getBoundingClientRect();
        return {
          topbarTop: topbar.top, topbarBottom: topbar.bottom, menuTop: menu.top, menuBottom: menu.bottom,
          mainTop: main.getBoundingClientRect().top, mainBottom: main.getBoundingClientRect().bottom,
          mainPaddingBottom: Number.parseFloat(getComputedStyle(main).paddingBottom),
          rootTop: root.top, rootBottom: root.bottom, height: window.innerHeight,
          scrollWidth: document.documentElement.scrollWidth,
        };
      });
      expect(shellLayout.rootTop, path).toBe(0);
      expect(shellLayout.rootBottom, path).toBe(shellLayout.height);
      expect(shellLayout.topbarTop, path).toBe(0);
      expect(shellLayout.menuTop, path).toBeGreaterThanOrEqual(SAFE_TOP);
      expect(shellLayout.menuBottom, path).toBeLessThanOrEqual(shellLayout.topbarBottom);
      expect(shellLayout.mainTop, path).toBe(shellLayout.topbarBottom);
      expect(shellLayout.mainBottom, path).toBe(shellLayout.height);
      expect(shellLayout.mainPaddingBottom, path).toBe(SAFE_BOTTOM);
      expect(shellLayout.scrollWidth, path).toBeLessThanOrEqual(viewport.width);
    }
  });
}

test("drawer and settings are flat on a phone", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await login(page, "/");
  await page.getByRole("button", { name: "メニューを開く" }).click();
  const drawer = page.getByRole("dialog", { name: "メニュー" });
  // 「システム」というまとまりの中に「System」がある、という入れ子に見える並びをやめた。
  await expect(drawer.getByRole("group", { name: "システム" })).toHaveCount(0);
  const admin = drawer.getByRole("group", { name: "管理" });
  for (const name of ["Logs", "System", "Settings"]) await expect(admin.getByRole("link", { name, exact: true })).toBeVisible();
  // どの行も行き先へ直接行く（押して開くだけのまとまりは無い）。
  await expect(drawer.locator("nav button")).toHaveCount(0);
  await admin.getByRole("link", { name: "System", exact: true }).click();
  await expect(page).toHaveURL(/\/system$/);
  await expect(drawer).toHaveCount(0);

  // Settings: 携帯でもカテゴリの一覧だけの画面を挟まず、すぐ中身が出る。
  await page.getByRole("button", { name: "メニューを開く" }).click();
  await drawer.getByRole("link", { name: "Settings", exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("heading", { level: 2, name: "アカウント" })).toBeVisible();
  const categories = page.getByRole("navigation", { name: "設定のカテゴリ" });
  await expect(categories.getByRole("link", { name: "アカウント" })).toHaveAttribute("aria-current", "page");
  await expect(categories.getByRole("link", { name: "システム", exact: true })).toHaveCount(0);
  const chip = categories.getByRole("link", { name: "更新とバックアップ" });
  await chip.scrollIntoViewIfNeeded();
  expect((await chip.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  await chip.click();
  await expect(page).toHaveURL(/\/settings\/system$/);
  await expect(page.getByRole("heading", { level: 2, name: "更新とバックアップ" })).toBeVisible();
  await expect(categories).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
});

// 根拠: iPhone のホーム画面アプリは再読み込み後に WebView 自体が上の Safe Area 分（59pt）短くなり、
// 描画はその高さで終わる（2026-11-17 の実機の診断: screen 932 / innerHeight 873）。殻を実寸に広げた #428 は
// 逆効果で入力欄が切れた。殻は報告された高さ、下の Safe Area は 0（ホームバーは描画領域の外）。
test("standalone iPhone: shell fits the shortened web view and drops the bottom safe area", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
    Object.defineProperty(window.screen, "width", { configurable: true, get: () => 440 });
    Object.defineProperty(window.screen, "height", { configurable: true, get: () => 956 });
    // 上の帯に画面を広げている（black-translucent）状態の Safe Area
    // 文書の要素ができてから（アプリのスクリプトより先）に入れる
    document.addEventListener("readystatechange", () => document.documentElement.style.setProperty("--cd-safe-area-top", "62px"), { once: true });
  });
  await page.setViewportSize({ width: 440, height: 894 }); // 956 - 62（縮んだ報告）
  // 診断: 画面の寸法が送られる（下が切れる件を実機の値で追うため）
  const sent: Array<{ page: string; marks: Record<string, number> }> = [];
  await page.route("**/api/v1/diagnostics/client", (route) => {
    sent.push(route.request().postDataJSON());
    return route.fulfill({ status: 204, body: "" });
  });
  await login(page);
  await page.goto("/assistant");
  await expect(page.locator("[data-assistant-shell]")).toBeVisible();
  const state = await page.evaluate(() => ({
    root: getComputedStyle(document.getElementById("root")!).height,
    shrunk: document.documentElement.dataset.viewportShrunk,
  }));
  expect(state).toEqual({ root: "894px", shrunk: "62" });
  // 下の Safe Area は 0（入力欄の下に余白を足さない）
  expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--cd-safe-area-bottom").trim())).toBe("0px");
  await expect.poll(() => sent.find((r) => r.page === "viewport")?.marks.root_h, { timeout: 8000 }).toBe(894);
  const viewport = sent.find((r) => r.page === "viewport")!.marks;
  expect(viewport).toMatchObject({ screen_h: 956, inner_h: 894, shrunk: 62 });
  // 入力欄は描ける範囲（報告された高さ）の中に収まる
  expect(viewport.composer_bottom).toBeGreaterThan(0);
  expect(viewport.composer_bottom).toBeLessThanOrEqual(894);
  // iOS が後から満寸へ広げても window の resize が来ないことがある（実機 2026-11-17）。表示の切り替えなどで合わせ直す
  await page.evaluate(() => {
    const w = window as unknown as { __innerHeight?: PropertyDescriptor };
    w.__innerHeight = Object.getOwnPropertyDescriptor(window, "innerHeight")
      ?? Object.getOwnPropertyDescriptor(Object.getPrototypeOf(window), "innerHeight");
    Object.defineProperty(window, "innerHeight", { configurable: true, get: () => 956 });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => page.evaluate(() => getComputedStyle(document.getElementById("root")!).height)).toBe("956px");
  await page.evaluate(() => {
    const w = window as unknown as { __innerHeight?: PropertyDescriptor };
    // 上書きを外して元の（実際の高さを返す）定義へ戻す
    if (w.__innerHeight) Object.defineProperty(window, "innerHeight", { ...w.__innerHeight, configurable: true });
    else delete (window as unknown as { innerHeight?: number }).innerHeight;
  });
  // 短くなっていないとき（満寸）は Safe Area をそのまま使う
  await page.setViewportSize({ width: 440, height: 956 });
  // キーボード（報告される高さが 956 → 520 に縮む）: 文書の最低の高さを外し、スクロールしない・入力欄が見える範囲に
  // （実機 2026-11-17: 縮んだ高さより文書が長く、iOS が 357 スクロールして画面全体が上へずれた）
  await page.setViewportSize({ width: 440, height: 520 });
  await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("cd-viewport-keyboard"))).toBe(true);
  const keyboard = await page.evaluate(() => ({
    scrollY: window.scrollY,
    rootHeight: getComputedStyle(document.getElementById("root")!).height,
    composerBottom: document.querySelector("[data-assistant-input-card]")!.getBoundingClientRect().bottom,
    topbarTop: document.querySelector("[data-assistant-topbar]")!.getBoundingClientRect().top,
  }));
  expect(keyboard.scrollY).toBe(0);
  expect(keyboard.rootHeight).toBe("520px");
  expect(keyboard.topbarTop).toBeGreaterThanOrEqual(0);
  expect(keyboard.composerBottom).toBeLessThanOrEqual(520);
  expect(keyboard.composerBottom).toBeGreaterThan(400);
  await page.setViewportSize({ width: 440, height: 956 });
  await expect.poll(() => page.evaluate(() => document.documentElement.classList.contains("cd-viewport-keyboard"))).toBe(false);
  await expect.poll(() => page.evaluate(() => document.documentElement.style.getPropertyValue("--cd-safe-area-bottom"))).toBe("");
});

// 根拠: 2026-11-17 の依頼「会話履歴は左右のスワイプでアーカイブか削除。離すと適用し、連続的に操作できる」。
test("history rows swipe right to archive and left to delete, with undo", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 1280, height: 800 });
  await login(page);
  const calls = await mockChat(page);
  await page.goto("/assistant");
  const history = page.locator("[data-assistant-history]").first();
  await expect(history.getByText("週の振り返り")).toBeVisible();
  const swipe = async (title: string, dx: number) => {
    const box = (await history.locator(`[data-swipe-row="${title}"]`).boundingBox())!;
    const y = box.y + box.height / 2;
    const x = box.x + box.width / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    for (let i = 1; i <= 8; i += 1) await page.mouse.move(x + (dx * i) / 8, y);
    await page.mouse.up();
  };
  // 右へ: アーカイブ（確認なし）→ 続けて左へ: 削除
  await swipe("週の振り返り", 160);
  await expect.poll(() => calls).toContain("archive:week1");
  await expect(history.locator('[data-swipe-row="週の振り返り"]')).toHaveCount(0);
  await swipe("先月の旅行計画", -160);
  await expect(history.locator('[data-swipe-row="先月の旅行計画"]')).toHaveCount(0);
  expect(calls).not.toContain("delete:old1");                 // 「元に戻す」の間は消さない
  await page.getByRole("button", { name: "元に戻す" }).last().click();
  await expect(history.getByText("先月の旅行計画")).toBeVisible();
  await page.waitForTimeout(6500);
  expect(calls).not.toContain("delete:old1");                 // 取り消したので消えない
  // 少しだけ引いたときは何もしない（戻る）
  await swipe("昨日の買い物メモ", 40);
  await expect(history.getByText("昨日の買い物メモ")).toBeVisible();
  expect(calls.filter((c) => c.includes("yest1"))).toEqual([]);
  // アーカイブした会話は下に出て、右へスワイプで戻せる
  await history.getByRole("button", { name: "アーカイブした会話" }).click();
  await expect(history.getByRole("list", { name: "アーカイブした会話" }).getByText("週の振り返り")).toBeVisible();
  await swipe("週の振り返り", 160);
  await expect.poll(() => calls).toContain("unarchive:week1");
  // 左へで消したものは時間が経つと本当に消える
  await swipe("先月の旅行計画", -160);
  await expect.poll(() => calls, { timeout: 9000 }).toContain("delete:old1");
});

// 根拠: 2026-11-17 の実機では起動直後も描画範囲が 59 短かった（black-translucent）。上の帯に画面を広げない
// black にしたとき（上の Safe Area 0）は、描画範囲が下端まで届くので、下の Safe Area を外さない。
test("standalone iPhone: with the status bar outside the page the bottom safe area stays", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "standalone", { configurable: true, value: true });
    Object.defineProperty(window.screen, "width", { configurable: true, get: () => 430 });
    Object.defineProperty(window.screen, "height", { configurable: true, get: () => 932 });
    // 文書の要素ができてから（アプリのスクリプトより先）に入れる
    document.addEventListener("readystatechange", () => document.documentElement.style.setProperty("--cd-safe-area-top", "0px"), { once: true });
  });
  await page.setViewportSize({ width: 430, height: 873 }); // 932 - 59（帯の下から置かれた高さ）
  await login(page);
  await page.goto("/assistant");
  await expect(page.locator("[data-assistant-shell]")).toBeVisible();
  const state = await page.evaluate(() => ({
    root: getComputedStyle(document.getElementById("root")!).height,
    shrunk: document.documentElement.dataset.viewportShrunk,
    bottomOverride: document.documentElement.style.getPropertyValue("--cd-safe-area-bottom"),
    statusBar: document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]')?.getAttribute("content"),
  }));
  expect(state).toEqual({ root: "873px", shrunk: "", bottomOverride: "", statusBar: "black" });
});
