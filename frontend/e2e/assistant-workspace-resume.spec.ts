// 目的: 作業場の「保存と中断・再開」の画面（2026-11-22）。
//       中断・止めた OpenCode の回のメニューに「続きから」が出て、押すと resume API が呼ばれる。
//       結果のカードに main の進み・ディスクの空きの注意・ぶつかったときの「OpenCode に直させる」ボタンが出る。
//       作業中の作業場を消すときは確認の文言が強くなる。320px でも横にはみ出さない。
// 根拠: 2026-11-22 利用者「作業場のコードはどういうタイミングで実際に保存されるのか。途中で停電や再起動した
//       場合はどうなるのか。中断して再開できるのか。…問題がないように実装してほしい」。API は page.route で模擬する。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261122-2";
const REPO = `/data/sandboxes/${NAME}/work/repo`;

async function mock(page: Page, conflicts: boolean) {
  const state = {
    resumes: [] as Record<string, unknown>[],
    asks: 0,
    deletes: 0,
  };
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
    dirty: false, unpushed: 0, created_at: 1, last_used: Date.now() / 1000 - 60, repo_path: REPO,
    target: "controldeck", target_label: "本体", capabilities: ["llm"], adopt: "pr", working: true }] } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/changes$`), (route) => route.fulfill({ json: {
    base: "abc1234567", head: "def7654321", file_count: 1, added: 10, removed: 0, dirty: false,
    files: [{ path: "backend/app/x.py", added: 10, removed: 0, binary: false, untracked: false }],
    commits: [{ sha: "def7654321", author: "ControlDeck Sandbox", at: 1, subject: "x を足す" }], commit_count: 1,
    check: null, adopted: null,
    behind: conflicts ? 0 : 3,
    merge_conflict: conflicts ? { conflicts: ["backend/app/x.py", "docs/y.md"], at: 1 } : null,
    disk_free_gib: 9.5, disk_low: true,
  } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/check$`), (route) =>
    route.fulfill({ json: { check: null, running: false, job_id: null } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/resolve-conflict$`), (route) => {
    state.asks += 1;
    return route.fulfill({ status: 202, json: { assistant_message_id: "ar9" } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "w1", title: "作業場" },
    messages: [{ id: "u0", role: "user", content: "直して", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:00Z", meta: { mode_choice: "code" } },
      { id: "a0", role: "assistant", content: "途中まで", thinking: "", status: "interrupted", job_id: null, model: "", error: "ControlDeck の再起動で中断しました", created_at: "2026-11-22T00:00:01Z",
        meta: { mode: "code", project_path: REPO, opencode_session: "ses_x", todos: [
          { status: "completed", content: "まず測る" }, { status: "in_progress", content: "残りを直す" }] } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/opencode\/resume$/, (route) => {
    state.resumes.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ status: 201, json: { user_message_id: "ur9", assistant_message_id: "ar9", job_id: "j9", status: "generating" } });
  });
  return state;
}

for (const width of [320, 1280]) {
  test(`interrupted workspace turn has a resume action that posts the message id (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page, false);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("途中まで")).toBeVisible();
    await expect(shell.getByText("回答が中断されました")).toBeVisible();

    // 「続きから」はメニューの並び（44px 以上）にあり、押すと中断の回の ID を送る
    await shell.locator('[data-assistant-message="interrupted"]').getByRole("button", { name: "メッセージの操作" }).click();
    const item = page.getByRole("menuitem", { name: /続きから/ });
    await expect(item).toBeVisible();
    const box = await item.boundingBox();
    expect(box!.height).toBeGreaterThanOrEqual(44);
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    await item.click();
    await expect.poll(() => state.resumes).toEqual([{ message_id: "a0" }]);
    await page.screenshot({ path: `test-results/workspace-resume-${width}.png` });
  });

  test(`workspace result card warns about main progress, disk and conflicts (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page, false);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
    await page.goto("/assistant");
    const card = page.locator('[data-testid="workspace-result"]');
    await card.getByRole("button", { name: /作業場の結果/ }).click();
    // main の進み（ぶつかりが無いときだけ）とディスクの注意
    await expect(card.getByTestId("workspace-behind")).toContainText("元の main が 3 件進んでいます");
    await expect(card.getByTestId("workspace-disk-low")).toContainText("ディスクの空きが少ない");
    await expect(card.getByTestId("workspace-disk-low")).toContainText("9.5 GiB");
    // 作業中の印（消すときの確認が強くなる）
    await expect(page.getByTestId("workspace-info")).toContainText("OpenCode が作業中");
    await expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/workspace-card-progress-${width}.png` });
    void state;
  });
}

test(`conflicts show the files and ask OpenCode to fix them (dark, 320px)`, async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ colorScheme: "dark" });
  await establishSession(page, context);
  const state = await mock(page, true);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
  await page.goto("/assistant");
  const card = page.locator('[data-testid="workspace-result"]');
  await card.getByRole("button", { name: /作業場の結果/ }).click();
  await expect(card.getByTestId("workspace-conflict")).toContainText("2 ファイルがぶつかりました");
  await expect(card.getByTestId("workspace-conflict")).toContainText("backend/app/x.py");
  await expect(card.getByTestId("workspace-behind")).toHaveCount(0);
  await card.getByRole("button", { name: "OpenCode に直させる" }).click();
  await expect.poll(() => state.asks).toBe(1);
  await page.screenshot({ path: "test-results/workspace-conflict-320-dark.png" });
});

test(`a resumed turn does not offer resume again (1280px)`, async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 1280, height: 800 });
  await establishSession(page, context);
  await mock(page, false);
  // 1 件は再開し終えた回（resumed）にする
  await page.unroute(/\/api\/v1\/chat\/conversations\/w1\/messages$/);
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "w1", title: "作業場" },
    messages: [
      { id: "u0", role: "user", content: "直して", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:00Z", meta: { mode_choice: "code" } },
      { id: "a0", role: "assistant", content: "途中まで", thinking: "", status: "interrupted", job_id: null, model: "", error: "ControlDeck の再起動で中断しました", created_at: "2026-11-22T00:00:01Z",
        meta: { mode: "code", project_path: REPO, opencode_session: "ses_x", resumed: true } },
    ],
  } }));
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await expect(shell.getByText("途中まで")).toBeVisible();
  await shell.locator('[data-assistant-message="interrupted"]').getByRole("button", { name: "メッセージの操作" }).click();
  await expect(page.getByRole("menuitem", { name: /ここから分岐/ })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /続きから/ })).toHaveCount(0);
});

test(`deleting a working workspace asks to stop it first (1280px)`, async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 1280, height: 800 });
  await establishSession(page, context);
  await mock(page, false);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
  await page.goto("/assistant");
  await page.getByRole("button", { name: `作業場 ${NAME} を消す` }).click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("OpenCode が今も作業中です");
  await expect(dialog).toContainText("止めてから消してください");
  await expect(dialog.getByRole("button", { name: "変更ごと消す" })).toBeVisible();
  await page.screenshot({ path: "test-results/workspace-delete-working.png" });
});
