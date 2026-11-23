/**
 * 目的: AI の記憶の画面（設定の一覧・編集・削除・守る・すべて消す）と、答えの下の「記憶を n 件使いました」、
 * AI 設定の「この会話で記憶を使わない／この会話を記憶に残さない」の切り替えを守る（PC 1280・モバイル 320）。
 * 守りの要点: 守られた記憶は印つきで出る・秘密の会話・特許から作った記憶は「守りを外す」が押せない・
 * 秘密でない会話の答えの下の記憶には守られた記憶が出ない（件数もサーバーが数えた normal だけ）。
 * 根拠: 2026-11-23 利用者「保護された会話や特許のアイデア関連の記憶はインターネット検索を保護されていない会話には露呈できない」。
 * API は page.route で差し替える（LLM・記憶の保存先を動かさない）。撮影は test-results/ へ。
 */
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

type Item = {
  id: number; text: string; kind: string; kind_label: string; protected: boolean; forced_protected: boolean;
  source_kind: string; source_id: string; created_at: number; last_used_at: number | null; use_count: number;
  pinned: boolean; pending: boolean; conversation_title: string | null; conversation_exists: boolean;
};
const item = (over: Partial<Item> & { id: number; text: string }): Item => ({
  kind: "fact", kind_label: "事実", protected: false, forced_protected: false, source_kind: "chat", source_id: "", created_at: 1795000000,
  last_used_at: null, use_count: 0, pinned: false, pending: false, conversation_title: null, conversation_exists: false, ...over,
});

async function mockMemory(page: Page) {
  const state = {
    prefs: { enabled: true, creation: "explicit", use_external: false, use_opencode: false },
    items: [
      item({ id: 1, text: "利用者は回答を箇条書きで短くまとめる文体を好む", kind: "preference", kind_label: "好み", last_used_at: 1795100000, source_id: "cv1", conversation_title: "文体の相談", conversation_exists: true }),
      item({ id: 2, text: "利用者は特許出願前の磁気浮上モータの新構造を検討している", kind: "work", kind_label: "仕事の文脈", protected: true, forced_protected: true, source_kind: "confidential", source_id: "cv2", conversation_title: "秘密の相談", conversation_exists: true }),
      item({ id: 3, text: "利用者は Linux のサーバーを自宅で運用している", pending: true }),
    ] as Item[],
    calls: [] as string[],
    flags: { no_use: false, no_save: false },
  };
  await page.route(/\/api\/v1\/memory\/settings$/, async (route) => {
    if (route.request().method() === "PUT") {
      state.prefs = { ...state.prefs, ...(route.request().postDataJSON() as object) };
      state.calls.push(`prefs:${JSON.stringify(route.request().postDataJSON())}`);
    }
    await route.fulfill({ json: { prefs: state.prefs, kinds: {} } });
  });
  await page.route(/\/api\/v1\/memory\/items(\/\d+)?$/, async (route) => {
    const req = route.request();
    const id = Number(req.url().split("/").pop());
    if (req.method() === "GET") return route.fulfill({ json: { items: state.items, limit: 500 } });
    if (req.method() === "DELETE" && Number.isNaN(id)) { const n = state.items.length; state.items = []; state.calls.push("clear"); return route.fulfill({ json: { deleted: n } }); }
    if (req.method() === "DELETE") { state.items = state.items.filter((m) => m.id !== id); state.calls.push(`delete:${id}`); return route.fulfill({ status: 204, body: "" }); }
    if (req.method() === "PATCH") {
      const body = req.postDataJSON() as Record<string, unknown>;
      state.calls.push(`patch:${id}:${JSON.stringify(body)}`);
      state.items = state.items.map((m) => (m.id !== id ? m : {
        ...m, ...(typeof body.text === "string" ? { text: body.text } : {}), ...(typeof body.protected === "boolean" ? { protected: body.protected } : {}),
        ...(body.approve ? { pending: false } : {}),
      }));
      return route.fulfill({ json: state.items.find((m) => m.id === id) });
    }
    return route.fulfill({ status: 405, body: "" });
  });
  return state;
}

for (const width of [1280, 320]) {
  test(`memory settings: list, edit, protect, delete, clear (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 700 : 900 });
    await establishSession(page, context);
    const state = await mockMemory(page);
    await page.goto("/settings/memory");
    await expect(page.getByRole("heading", { name: "使い方", exact: true })).toBeVisible();
    const items = page.locator("[data-memory-item]");
    await expect(items).toHaveCount(2);                                       // 承認待ちは別の枠
    await expect(page.locator("[data-memory-pending] li")).toHaveCount(1);
    await expect(page.locator("[data-memory-protect-note]")).toContainText("秘密の会話の中でしか使いません");
    // 守られた印・外せない・作った会話へのリンク・最後に使った時
    const secret = items.nth(1);
    await expect(secret.locator("[data-memory-protected]")).toContainText("外せません");
    await expect(secret.getByRole("button", { name: "守りを外す" })).toBeDisabled();
    const plain = items.first();          // 編集中は本文が textarea の値になり、文字では絞れない
    await expect(plain.getByRole("link", { name: /作った会話: 文体の相談/ })).toHaveAttribute("href", "/assistant?conversation=cv1");
    await expect(plain).toContainText("最後に使った時");
    await page.screenshot({ path: `test-results/chat-memory-settings-${width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

    // 設定: 外部の AI の会話にも使う（既定は切）
    const external = page.getByLabel("外部の AI の会話にも記憶を使う");
    await expect(external).not.toBeChecked();
    await external.click();
    await expect.poll(() => state.calls.some((c) => c.includes('"use_external":true'))).toBe(true);
    await page.getByLabel("作らない").click();
    await expect.poll(() => state.calls.some((c) => c.includes('"creation":"off"'))).toBe(true);

    // 編集・守る・承認・削除
    await plain.getByRole("button", { name: "編集" }).click();
    await plain.getByLabel("記憶の本文").fill("利用者は結論を先に書く文体を好む");
    await plain.getByRole("button", { name: "保存" }).click();
    await expect(items.filter({ hasText: "結論を先に書く" })).toHaveCount(1);
    const edited = items.first();
    await edited.getByRole("button", { name: "守る", exact: true }).click();
    await expect(edited.locator("[data-memory-protected]")).toBeVisible();
    await page.locator("[data-memory-pending]").getByRole("button", { name: "残す" }).click();
    await expect(items).toHaveCount(3);
    await edited.getByRole("button", { name: "削除" }).click();
    await expect(items).toHaveCount(2);
    await page.getByRole("button", { name: "すべて消す" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "すべて消す" }).click();
    await expect(items).toHaveCount(0);
    expect(state.calls).toContain("clear");
  });

  test(`answer shows the memories used; toggles are per conversation (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 700 : 900 });
    await establishSession(page, context);
    const flagCalls: object[] = [];
    let flags = { no_use: false, no_save: false };
    const msg = (id: string, role: string, content: string, meta: object = {}) => ({
      id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta, created_at: "2026-11-23T00:00:00Z" });
    await page.route(/\/api\/v1\/chat\/conversations\/cvm\/messages$/, (route) => route.fulfill({ json: {
      conversation: { id: "cvm", title: "記憶の会話", confidential: false },
      messages: [msg("u1", "user", "文体の希望を踏まえて要約して"), msg("a1", "assistant", "結論から書きます。", { memory: { count: 1, ids: [1] }, memory_created: { created: 1, pending: 0 } })],
    } }));
    let usedQuery = "";
    await page.route(/\/api\/v1\/memory\/used\?.*/, (route) => {
      usedQuery = route.request().url();
      return route.fulfill({ json: { items: [{ id: 1, text: "利用者は回答を箇条書きで短くまとめる文体を好む", kind: "preference", kind_label: "好み", protected: false }] } });
    });
    await page.route(/\/api\/v1\/memory\/conversations\/cvm\/flags$/, async (route) => {
      if (route.request().method() === "PUT") { flagCalls.push(route.request().postDataJSON()); flags = { ...flags, ...(route.request().postDataJSON() as object) }; }
      await route.fulfill({ json: flags });
    });
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "cvm"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const used = shell.locator("[data-memory-used]");
    await expect(used).toContainText("記憶を 1 件使いました");
    await expect(shell.locator("[data-memory-created]")).toContainText("記憶に残しました（1 件）");
    await used.getByRole("button", { name: /記憶を 1 件使いました/ }).click();
    await expect(used.locator("[data-memory-used-list]")).toContainText("箇条書きで短くまとめる");
    expect(usedQuery).toContain("conv_id=cvm");                              // 会話の権限はサーバーが見る（秘密でなければ守られた記憶は返らない）
    await expect(used.locator("[data-memory-used-list]")).not.toContainText("磁気浮上モータ");
    await page.screenshot({ path: `test-results/chat-memory-answer-${width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);

    // 会話ごとの切り替え
    await shell.getByRole("button", { name: "AI設定を開く" }).click();
    const noUse = shell.getByLabel("この会話で記憶を使わない");
    const noSave = shell.getByLabel("この会話を記憶に残さない");
    await noUse.click();
    await expect.poll(() => flagCalls.length).toBe(1);
    expect(flagCalls[0]).toEqual({ no_use: true });
    await noSave.click();
    await expect.poll(() => flagCalls.length).toBe(2);
    expect(flagCalls[1]).toEqual({ no_save: true });
    await expect(noUse).toBeChecked();
    await expect(noSave).toBeChecked();
    await page.screenshot({ path: `test-results/chat-memory-toggles-${width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}
