// 目的: 作業場の会話に「作業場の結果」のカードが出る（変更の数と増減・開くと一覧・未コミットの印・関門の結果と流すボタン）。
//       関門が合格のときだけ「取り込む」が押せ、確認（本体は PR・未コミットならコミットメッセージ）の後に PR のリンクが出る。
//       関門の後に変更があれば押せない。320px でも横にはみ出さない。
// 根拠: 2026-11-22 利用者「作業場の会話に結果のカードと取り込むを足す」（開発の作業場 3 段目）。API は page.route で模擬する。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261122-1";
const REPO = `/data/sandboxes/${NAME}/work/repo`;
const PR = "https://github.com/example/repo/pull/7";

async function mock(page: Page) {
  const state = {
    dirty: true, check: null as null | { passed: boolean; stale: boolean },
    running: false, adopted: false, checks: 0, adopts: [] as Record<string, unknown>[],
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
    dirty: state.dirty, unpushed: 1, created_at: 1, last_used: Date.now() / 1000 - 60, repo_path: REPO }] } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/changes$`), (route) => route.fulfill({ json: {
    base: "abc1234567", head: "def7654321", file_count: 2, added: 30, removed: 4, dirty: state.dirty,
    files: [{ path: "backend/app/x.py", added: 20, removed: 4, binary: false, untracked: false },
      { path: "docs/very/long/path/that/should/truncate/instead/of/overflowing/the/card/note.md", added: 10, removed: 0, binary: false, untracked: false }],
    commits: [{ sha: "def7654321", author: "ControlDeck Sandbox", at: 1, subject: "x を直す" }], commit_count: 1,
    check: state.check ? { ...state.check, at: 1, head: "def7654321", items: state.check.passed
      ? [{ status: "ok", name: "テスト", message: "3 件" }] : [{ status: "ng", name: "テスト", message: "1 件落ちた" }] } : null,
    adopted: state.adopted ? { kind: "pr", branch: `ws/${NAME}`, pr_url: PR, head: "def7654321" } : null,
  } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/check$`), (route) => {
    if (route.request().method() === "POST") {
      state.checks += 1;
      state.running = true;
      setTimeout(() => { state.running = false; state.check = { passed: true, stale: false }; }, 1200);
      return route.fulfill({ status: 202, json: { job_id: "j9", already_running: false } });
    }
    return route.fulfill({ json: { check: state.check, running: state.running, job_id: state.running ? "j9" : null } });
  });
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/adopt$`), (route) => {
    state.adopts.push(route.request().postDataJSON() as Record<string, unknown>);
    state.adopted = true; state.dirty = false;
    return route.fulfill({ json: { kind: "pr", branch: `ws/${NAME}`, pr_url: PR } });
  });
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "w1", title: "作業場" },
    messages: [{ id: "u0", role: "user", content: "直して", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:00Z", meta: { mode_choice: "code", agent_choice: "build" } },
      { id: "a0", role: "assistant", content: "直しました", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:01Z",
        meta: { mode: "code", project_path: REPO, agent: "build" } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  return state;
}

for (const width of [320, 1280]) {
  test(`workspace result card: check then adopt creates a PR (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page);
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const card = shell.locator('[data-testid="workspace-result"]');
    await expect(card).toContainText("2 ファイル +30 −4");
    await expect(card).toContainText("未コミット");
    await expect(card.getByTestId("workspace-check-label")).toHaveText("関門は未実施");
    const noOverflow = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await card.getByRole("button", { name: /作業場の結果/ }).click();
    await card.getByRole("button", { name: /変わったファイル 2 件を開く/ }).click();
    await expect(card.getByTestId("workspace-files")).toContainText("backend/app/x.py");
    const adoptButton = card.getByRole("button", { name: "取り込む" });
    await expect(adoptButton).toBeDisabled();
    await noOverflow();
    await page.screenshot({ path: `test-results/workspace-result-${width}-before.png` });

    // 関門を流す（進み具合 → 合格）
    await card.getByRole("button", { name: "関門を流す" }).click();
    await expect(card.getByRole("button", { name: "関門を流しています…" }).first()).toBeVisible();
    await expect(card.getByTestId("workspace-check-label")).toHaveText("関門 合格", { timeout: 15_000 });
    expect(state.checks).toBe(1);
    await expect(adoptButton).toBeEnabled();
    for (const button of [adoptButton, card.getByRole("button", { name: "関門を流す" })]) {
      expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(43.5);
    }

    // 取り込む: 確認（本体は PR・未コミットはコミットメッセージ）
    await adoptButton.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("GitHub に PR を作ります");
    await expect(dialog.getByRole("button", { name: "取り込む" })).toBeDisabled();
    await dialog.getByRole("textbox").fill("x を直す");
    await page.screenshot({ path: `test-results/workspace-result-${width}-confirm.png` });
    await dialog.getByRole("button", { name: "取り込む" }).click();
    await expect(card.getByTestId("workspace-adopted")).toContainText("確認とマージは");
    await expect(card.getByTestId("workspace-adopted").getByRole("link")).toHaveAttribute("href", PR);
    expect(state.adopts).toEqual([{ confirm: true, commit_message: "x を直す" }]);
    await noOverflow();
    await page.screenshot({ path: `test-results/workspace-result-${width}-adopted.png` });
  });
}

test("workspace result card: failed check lists the failure and blocks adopt (dark, 320px)", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ colorScheme: "dark" });
  await establishSession(page, context);
  const state = await mock(page);
  state.check = { passed: false, stale: false };
  await page.evaluate(() => { localStorage.setItem("cd-chat-conversation", "w1"); });
  await page.goto("/assistant");
  const card = page.locator('[data-testid="workspace-result"]');
  await card.getByRole("button", { name: /作業場の結果/ }).click();
  await expect(card.getByTestId("workspace-check-label")).toHaveText("関門 不合格");
  await expect(card.getByTestId("workspace-check-failures")).toContainText("テスト: 1 件落ちた");
  await expect(card.getByRole("button", { name: "取り込む" })).toBeDisabled();
  await page.screenshot({ path: "test-results/workspace-result-320-dark-failed.png" });
});
