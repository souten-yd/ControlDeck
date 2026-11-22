// 目的: AI アシスタントの OpenCode で「開発の作業場」を選べること。一覧に自分の作業場が出て、選ぶと作業場の印が出る。
//       「新しい作業場」で元を選んで作ると選ばれる。送ると作業場の作業ツリーのパスが code_project_path で渡る。
//       消す操作は確認つき（未保存の変更があれば警告し、確認の印つきで消す）。320px でも横にはみ出さない。
// 根拠: 2026-11-22 利用者「Claude なしで、AI アシスタントに要望を出せば OpenCode が開発を進められる状態に」
//       （開発の作業場 2 段目）。API と WebSocket は模擬する（箱・OpenCode を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const REPO = "/data/sandboxes/ws-20261122-1/work/repo";
const workspace = (name: string, extra: object = {}) => ({
  name, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${name}`,
  dirty: false, unpushed: 0, created_at: 1, last_used: Date.now() / 1000 - 300,
  repo_path: `/data/sandboxes/${name}/work/repo`, ...extra,
});

async function mock(page: Page) {
  const state = { list: [workspace("ws-20261122-1", { dirty: true })], sent: [] as Record<string, unknown>[], deletes: [] as string[] };
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }, { name: "memo", path: "/CodeDEV/memo", git: false }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/opencode\/workspaces(\/[^?]+)?(\?.*)?$/, (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      const made = workspace("ws-20261122-2");
      state.list = [made, ...state.list];
      return route.fulfill({ status: 201, json: made });
    }
    if (request.method() === "DELETE") {
      state.deletes.push(request.url());
      state.list = state.list.filter((w) => !request.url().includes(w.name));
      return route.fulfill({ json: { ok: true } });
    }
    if (/\/workspaces\/[^/?]+\/[^/?]+/.test(request.url())) {
      // 一覧ではない取得（変更・関門など）は一覧の形にしない（形の違う応答で結果のカードが落ちていた）。
      // 数も含めて本物の changes と同じ形にする（visual review: 欠けた応答で「undefined ファイル」と出た）
      return route.fulfill({ json: {
        base: "0000000", head: "1111111", file_count: 0, added: 0, removed: 0, dirty: false,
        uncommitted: false,
        files: [], commits: [], commit_count: 0, check: null, adopted: null, merged: null,
      } });
    }
    return route.fulfill({ json: { workspaces: state.list } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "w1", title: "作業場" },
    messages: [{ id: "u0", role: "user", content: "作って", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:00Z", meta: { mode_choice: "code", agent_choice: "build" } },
      { id: "a0", role: "assistant", content: "作りました", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:01Z",
        meta: { mode: "code", project_path: REPO.replace("ws-20261122-1", "ws-20261122-1"), agent: "build" } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/send$/, (route) => {
    state.sent.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ status: 201, json: { user_message_id: "u1", assistant_message_id: "a1", job_id: "j1", status: "generating" } });
  });
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, (ws) => {
    ws.send(JSON.stringify({ type: "delta", content: "了解" }));
    setTimeout(() => { ws.send(JSON.stringify({ type: "done", status: "completed" })); void ws.close(); }, 100);
  });
  return state;
}

for (const width of [320, 1280]) {
  test(`dev workspaces can be listed, created, used and deleted (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("作りました")).toBeVisible();
    // 会話が作業場で動いていたので、作業場が選ばれて印が出る
    const select = shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）");
    await expect(select).toHaveValue("ws:ws-20261122-1");
    await expect(shell.locator('[data-testid="workspace-info"] [data-testid="workspace-badge"]')).toBeVisible();
    await expect(shell.locator('[data-testid="workspace-info"]')).toContainText("未保存の変更あり");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/assistant-dev-workspace-${width}-selected.png` });

    // 送ると作業場のパスで渡る
    const input = shell.locator("[data-assistant-input]");
    await input.fill("直して");
    await input.press("Enter");
    await expect.poll(() => state.sent.length).toBe(1);
    expect(state.sent[0]).toMatchObject({ mode: "code", code_project: "", code_project_path: `/data/sandboxes/ws-20261122-1/work/repo` });

    // 新しい作業場（git でない CodeDEV のプロジェクトは元に出ない）
    await select.selectOption("__ws_new__");
    const create = shell.locator('[data-testid="workspace-create"]');
    await expect(create).toBeVisible();
    await expect(create.getByRole("option")).toHaveText(["ControlDeck 本体（origin/main）", "CodeDEV: app"]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/assistant-dev-workspace-${width}-create.png` });
    await create.getByRole("button", { name: "作る" }).click();
    await expect(select).toHaveValue("ws:ws-20261122-2");
    await expect(shell.locator('[data-testid="workspace-info"]')).not.toContainText("未保存");

    // 消す: 確認つき。変更の無い作業場は確認の印なし
    await shell.getByRole("button", { name: /作業場 ws-20261122-2 を消す/ }).click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("元のリポジトリには影響しません");
    await page.screenshot({ path: `test-results/assistant-dev-workspace-${width}-delete.png` });
    await dialog.getByRole("button", { name: "キャンセル" }).click();
    expect(state.deletes).toHaveLength(0);
    // 未保存の変更がある作業場は警告し、確認の印つきで消す
    await select.selectOption("ws:ws-20261122-1");
    await shell.getByRole("button", { name: /作業場 ws-20261122-1 を消す/ }).click();
    await expect(page.getByRole("alertdialog")).toContainText("保存されていない変更があります");
    await page.getByRole("button", { name: "変更ごと消す" }).click();
    await expect.poll(() => state.deletes.length).toBe(1);
    expect(state.deletes[0]).toContain("confirm=true");
    await expect(select).toHaveValue("__new__");
  });
}
