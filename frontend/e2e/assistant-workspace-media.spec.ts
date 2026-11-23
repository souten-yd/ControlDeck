// 目的: 作業場の画面の記録（撮影・操作テストの動画）が、(1) チャットの完了カードの下に縮小画像と動画で出る（押すと大きく・消された記録は「消しました」）、
//       (2) 作業場の結果のカードの「画面の記録」で一覧され、1 件ずつ消せる・まとめて消せる（確認つき）、
//       (3) 設定 → 開発（作業場）で全作業場の合計が見え、一斉に消せる（確認つき）・自動で消す設定が保存される。320px でも横にはみ出さない。
// 根拠: 2026-11-23 利用者「スクリーンショットや操作テストの動画をチャットに出し、容量をとるので後で個別・一斉に消せるように」（ROADMAP U28・U34）。
//       API は page.route で模擬する。画像は data URI の 1px の PNG、動画は 200 を返すだけ（再生内容は問わない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261123-1";
const REPO = `/data/sandboxes/${NAME}/work/repo`;
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

async function mock(page: Page) {
  const state = {
    items: [
      { path: ".controldeck/media/w320-light.png", kind: "image", size: 2048, mtime: Date.now() / 1000 - 30 },
      { path: ".controldeck/media/login-flow.webm", kind: "video", size: 3 * 1024 * 1024, mtime: Date.now() / 1000 - 60 },
      { path: "test-results/e2e/shot.png", kind: "image", size: 4096, mtime: Date.now() / 1000 - 600 },
    ] as { path: string; kind: string; size: number; mtime: number }[],
    deleted: [] as string[], deletedAll: 0, deletedEvery: 0, workflowPuts: [] as Record<string, unknown>[],
  };
  const total = () => state.items.reduce((sum, i) => sum + i.size, 0);
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  await page.route(/\/api\/v1\/opencode\/workspaces$/, (route) => route.fulfill({ json: { workspaces: [{
    name: NAME, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${NAME}`,
    dirty: false, unpushed: 0, created_at: 1, last_used: Date.now() / 1000 - 60, repo_path: REPO }] } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/changes$`), (route) => route.fulfill({ json: {
    base: "abc1234567", head: "def7654321", file_count: 1, added: 3, removed: 1, dirty: false, files: [], commits: [], commit_count: 0, check: null, adopted: null } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/check$`), (route) => route.fulfill({ json: { check: null, running: false, job_id: null } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/checkpoints$`), (route) => route.fulfill({ json: { checkpoints: [] } }));
  // 画面の記録: 一覧・配信・削除
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/media/file\\?`), (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") ?? "";
    if (route.request().method() === "DELETE") {
      state.deleted.push(path);
      const hit = state.items.find((i) => i.path === path);
      state.items = state.items.filter((i) => i.path !== path);
      return route.fulfill({ json: { count: 1, size: hit?.size ?? 0 } });
    }
    if (path.includes("gone")) return route.fulfill({ status: 404, json: { detail: "記録がありません" } });
    return route.fulfill(path.endsWith(".webm") ? { status: 200, contentType: "video/webm", body: Buffer.alloc(16) } : { status: 200, contentType: "image/png", body: PNG });
  });
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/media$`), (route) => {
    if (route.request().method() === "DELETE") {
      const done = { count: state.items.length, size: total() };
      state.items = []; state.deletedAll += 1;
      return route.fulfill({ json: done });
    }
    return route.fulfill({ json: { items: state.items, count: state.items.length, total_size: total() } });
  });
  await page.route(/\/api\/v1\/opencode\/workspaces-media$/, (route) => {
    if (route.request().method() === "DELETE") {
      const done = { count: state.items.length, size: total() };
      state.items = []; state.deletedEvery += 1;
      return route.fulfill({ json: done });
    }
    return route.fulfill({ json: { count: state.items.length, total_size: total(),
      workspaces: state.items.length ? [{ name: NAME, count: state.items.length, total_size: total() }] : [] } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "w1", title: "作業場" },
    messages: [{ id: "u0", role: "user", content: "画面を確かめて", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-23T00:00:00Z", meta: { mode_choice: "code", agent_choice: "build" } },
      { id: "a0", role: "assistant", content: "確かめました。\n\n## 要約\n画面は崩れていません", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-23T00:00:01Z",
        meta: { mode: "code", project_path: REPO, agent: "build", media: { workspace: NAME, items: [
          { path: ".controldeck/media/w320-light.png", kind: "image", size: 2048 },
          { path: ".controldeck/media/login-flow.webm", kind: "video", size: 3145728 },
          { path: ".controldeck/media/gone.png", kind: "image", size: 10 }] } } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  return state;
}

for (const width of [320, 1280]) {
  test(`workspace media in chat and result card (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const chat = shell.locator("[data-chat-media]");
    await expect(chat.getByText("画面の記録（3 件）")).toBeVisible();
    await expect(chat.locator('[data-media-item="image"] img').first()).toBeVisible();
    await expect(chat.locator('[data-media-item="video"] video')).toHaveAttribute("src", /login-flow\.webm/);
    await expect(chat.getByText("消しました（gone.png）")).toBeVisible();
    await chat.getByRole("button", { name: "w320-light.png を大きく見る" }).click();
    await expect(page.locator("[data-media-viewer] img")).toBeVisible();
    await page.getByRole("button", { name: "閉じる", exact: true }).click();
    await expect(page.locator("[data-media-viewer]")).toBeHidden();
    await page.screenshot({ path: `test-results/assistant-workspace-media-chat-${width}.png` });

    const card = shell.locator('[data-testid="workspace-result"]');
    await card.getByRole("button", { name: /作業場の結果/ }).click();
    const section = card.getByTestId("workspace-media");
    await expect(section).toContainText("画面の記録（3 件・");
    await expect(section.locator("[data-media-row]")).toHaveCount(3);
    await section.locator("[data-media-row]").nth(2).getByRole("button", { name: "消す" }).click();
    await expect.poll(() => state.deleted).toEqual(["test-results/e2e/shot.png"]);
    await expect(section.locator("[data-media-row]")).toHaveCount(2);
    await page.screenshot({ path: `test-results/assistant-workspace-media-result-${width}.png` });
    await section.getByRole("button", { name: "この作業場の記録をすべて消す" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "すべて消す" }).click();
    await expect.poll(() => state.deletedAll).toBe(1);
    await expect(section).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });

  test(`workspace media settings bulk delete (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    const state = await mock(page);
    const workflow: Record<string, unknown> = { auto_review: true, isolation: "box", isolation_effective: "box", roles: {}, auto_advisor: "off", workspace_media_auto_delete: false };
    await page.route("**/api/v1/opencode/workflow", async (route) => {
      if (route.request().method() === "PUT") { const body = route.request().postDataJSON(); state.workflowPuts.push(body); Object.assign(workflow, body); }
      await route.fulfill({ json: workflow });
    });
    await page.route("**/api/v1/opencode/status**", (route) => route.fulfill({ json: { feature: {}, runtimes: {}, active_runtime: "v2",
      settings: { base_url: "http://127.0.0.1:8000/v1", model: "m", project_path: "", runtime: "v2", workspace_min_free_gib: 15 } } }));
    await page.goto("/settings/dev");
    const box = page.locator("[data-media-settings]");
    await expect(box).toContainText("合計 3 件・");
    await expect(box).toContainText(NAME);
    await box.getByLabel("取り込み（PR）・マージの後に記録を自動で消す").click();
    await expect.poll(() => state.workflowPuts.at(-1)).toEqual({ workspace_media_auto_delete: true });
    await page.screenshot({ path: `test-results/assistant-workspace-media-settings-${width}.png`, fullPage: true });
    await box.getByRole("button", { name: "すべての作業場の記録を消す" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "すべて消す" }).click();
    await expect.poll(() => state.deletedEvery).toBe(1);
    await expect(box).toContainText("合計 0 件");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}
