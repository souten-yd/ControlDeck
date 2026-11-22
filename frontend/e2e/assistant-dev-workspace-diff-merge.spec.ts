// 目的: 作業場の結果のカードの「変更」で、ファイルの一覧 → 行ごとの差分（追加は緑・削除は赤・行番号・横スクロール・打ち切りとバイナリの表示）が見え、
//       PR のリンクの横の「マージ」で前提の確認（関門・区分・マージできるか）→ 確認 → マージの結果が出る。L2 は強い警告と確認の文言の入力が要る。
//       前提を満たさないときは理由を出して押せない。320px でも横にはみ出さない。
// 根拠: 2026-11-22 利用者「マージの判断を Claude に頼らず、画面で行えるように」（開発の作業場 4 段目・L2）。API は page.route で模擬する。実際には何もマージしない。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261122-2";
const REPO = `/data/sandboxes/${NAME}/work/repo`;
const PR = "https://github.com/example/repo/pull/7";
const LONG = "const veryLongLine = \"" + "x".repeat(160) + "\";";
const DIFF = [
  "diff --git a/backend/app/x.py b/backend/app/x.py", "index 1..2 100644", "--- a/backend/app/x.py", "+++ b/backend/app/x.py",
  "@@ -3,4 +3,5 @@ def f():", " keep one", "-removed line", "+added line", `+${LONG}`, " keep two", "",
].join("\n");

async function mock(page: Page, opts: { tier: "L1" | "L2"; problems?: string[] }) {
  const state = { merges: [] as Record<string, unknown>[], merged: false };
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
    dirty: false, unpushed: 1, created_at: 1, last_used: Date.now() / 1000 - 60, repo_path: REPO }] } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/changes$`), (route) => route.fulfill({ json: {
    base: "abc1234567", head: "def7654321", file_count: 3, added: 30, removed: 4, dirty: false,
    files: [], commits: [{ sha: "def7654321", author: "ControlDeck Sandbox", at: 1, subject: "x を直す" }], commit_count: 1,
    check: { passed: true, stale: false, at: 1, head: "def7654321", items: [{ status: "ok", name: "テスト", message: "3 件" }] },
    adopted: { kind: "pr", branch: `ws/${NAME}`, pr_url: PR, head: "def7654321" },
    merged: state.merged ? { at: 1, by: "e2e-admin", pr_url: PR, tier: opts.tier } : null,
  } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/check$`), (route) => route.fulfill({ json: { check: null, running: false, job_id: null } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/diff(\\?.*)?$`), (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") ?? "";
    const files = [{ path: "backend/app/x.py", added: 2, removed: 1, binary: false },
      { path: "docs/very/long/path/that/should/truncate/instead/of/overflowing/the/card/note.md", added: 10, removed: 0, binary: false },
      { path: "frontend/public/pic.png", added: 0, removed: 0, binary: true }];
    const file = !path ? null : path === "backend/app/x.py"
      ? { path, binary: false, truncated: false, diff: DIFF, size: DIFF.length }
      : path.endsWith(".png") ? { path, binary: true, truncated: false, diff: "", size: 0 }
        : { path, binary: false, truncated: true, diff: "@@ -0,0 +1,2 @@\n+one\n+two\n", size: 900000 };
    return route.fulfill({ json: { base: "abc1234567", head: "def7654321", files, file, limit: 262144,
      uncommitted: { count: 1, files: [{ path: "wip.txt", status: "untracked" }] } } });
  });
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/merge$`), (route) => {
    if (route.request().method() === "POST") {
      state.merges.push(route.request().postDataJSON() as Record<string, unknown>);
      state.merged = true;
      return route.fulfill({ json: { at: 1, by: "e2e-admin", pr_url: PR, tier: opts.tier, head: "def7654321", guarded: [] } });
    }
    const problems = opts.problems ?? [];
    return route.fulfill({ json: {
      ready: problems.length === 0, problems, tier: opts.tier, phrase: "", file_count: 3,
      guarded: opts.tier === "L2" ? [{ path: "backend/app/security/deps.py", reasons: ["認証・権限"] }] : [],
      check: { passed: true, at: 1 }, merged: null,
      pr: { number: 7, url: PR, state: problems.length ? "CLOSED" : "OPEN", mergeable: "MERGEABLE", head: "def7654321" },
    } });
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

async function openCard(page: Page) {
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
  await page.goto("/assistant");
  const card = page.locator('[data-assistant-shell] [data-testid="workspace-result"]');
  await card.getByRole("button", { name: /作業場の結果/ }).click();
  return card;
}
const noOverflow = async (page: Page) => expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

for (const width of [320, 1280]) {
  test(`workspace diff: file list then line diff, binary and truncation (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    await mock(page, { tier: "L1" });
    const card = await openCard(page);
    await card.getByTestId("workspace-diff-toggle").click();
    const list = card.getByTestId("workspace-diff-files");
    await expect(list).toContainText("backend/app/x.py");
    await expect(card.getByTestId("workspace-diff-uncommitted")).toContainText("取り込む前にコミットされます");
    await noOverflow(page);
    await page.screenshot({ path: `test-results/workspace-diff-${width}-list.png` });
    await list.getByRole("button", { name: /backend\/app\/x\.py/ }).click();
    const lines = card.getByTestId("workspace-diff-lines");
    await expect(lines.locator('[data-diff="add"]')).toHaveCount(2);
    await expect(lines.locator('[data-diff="del"]')).toHaveCount(1);
    await expect(lines.locator('[data-diff="add"]').first()).toContainText("added line");
    expect(await lines.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true); // 長い行は横スクロール
    await noOverflow(page);
    await page.screenshot({ path: `test-results/workspace-diff-${width}-lines.png` });
    await card.getByRole("button", { name: /ファイルの一覧へ/ }).click();
    await list.getByRole("button", { name: /pic\.png/ }).click();
    await expect(card.getByTestId("workspace-diff-binary")).toContainText("バイナリのファイル");
    await card.getByRole("button", { name: /ファイルの一覧へ/ }).click();
    await list.getByRole("button", { name: /note\.md/ }).click();
    await expect(card.getByTestId("workspace-diff-truncated")).toContainText("打ち切りました");
    await noOverflow(page);
  });

  test(`workspace merge: L1 confirms then merges without a phrase (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page, { tier: "L1" });
    const card = await openCard(page);
    await expect(card.getByTestId("workspace-adopted").getByRole("link")).toHaveAttribute("href", PR);
    const open = card.getByTestId("workspace-merge-open");
    expect((await open.boundingBox())!.height).toBeGreaterThanOrEqual(43.5);
    await open.click();
    const dialog = page.getByRole("alertdialog");
    await expect(dialog.getByTestId("workspace-merge-tier")).toContainText("L1");
    await expect(dialog).toContainText("関門: 合格");
    await expect(dialog.getByTestId("workspace-merge-l2")).toHaveCount(0);
    await page.screenshot({ path: `test-results/workspace-merge-${width}-l1.png` });
    await dialog.getByRole("button", { name: "マージする" }).click();
    await expect(dialog.getByTestId("workspace-merge-done")).toContainText("次の Release で本番に入ります");
    expect(state.merges).toEqual([{ confirm: true, acknowledge_l2: false }]);
    await noOverflow(page);
    await page.screenshot({ path: `test-results/workspace-merge-${width}-done.png` });
  });

  test(`workspace merge: L2 shows a strong warning and is approved by a tap (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const state = await mock(page, { tier: "L2" });
    const card = await openCard(page);
    await card.getByTestId("workspace-merge-open").click();
    const dialog = page.getByRole("alertdialog");
    const l2 = dialog.getByTestId("workspace-merge-l2");
    await expect(l2).toContainText("この変更は L2（認証・権限・秘密・外部送信・移行・更新と隔離の仕組み）に当たります");
    await expect(l2).toContainText("backend/app/security/deps.py");
    // 文言の入力は無く、「L2 を確かめてマージ」を押すだけで承認する（2026-11-23 利用者「ボタンタップで承認」）
    await expect(dialog.getByTestId("workspace-merge-phrase")).toHaveCount(0);
    const go = dialog.getByRole("button", { name: "L2 を確かめてマージ" });
    await expect(go).toBeEnabled();
    expect((await go.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await page.screenshot({ path: `test-results/workspace-merge-${width}-l2.png` });
    await go.click();
    await expect(dialog.getByTestId("workspace-merge-done")).toBeVisible();
    expect(state.merges).toEqual([{ confirm: true, acknowledge_l2: true }]);
    await noOverflow(page);
  });
}

test("workspace merge: unmet preconditions show the reason and cannot be pressed (dark, 320px)", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await page.emulateMedia({ colorScheme: "dark" });
  await establishSession(page, context);
  const state = await mock(page, { tier: "L1", problems: ["PR は開いていません（CLOSED）"] });
  const card = await openCard(page);
  await card.getByTestId("workspace-merge-open").click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog.getByTestId("workspace-merge-problems")).toContainText("PR は開いていません");
  await expect(dialog.getByRole("button", { name: "マージする" })).toBeDisabled();
  expect(state.merges).toEqual([]);
  await page.screenshot({ path: "test-results/workspace-merge-320-dark-blocked.png" });
});
