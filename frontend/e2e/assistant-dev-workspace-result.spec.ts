// 目的: 作業場の会話に「作業場の結果」のカードが出る（変更の数と増減・開くと一覧・未コミットの印・確認の結果と主の操作）。
//       確認（テスト・旧「関門」）の状態は 1 つの札（未確認・確認中…・合格・不合格（n 件））で、
//       主の操作は状態で 1 つだけ: 未確認→確認してから取り込む（合格なら取り込みの確認へ）・
//       不合格→直させる（落ちた内容を同じ会話へ）。「それでも取り込む」は細かい表示（エキスパート）の中で管理者だけ。
//       320px でも横にはみ出さない。
// 根拠: 2026-11-22 利用者「作業場の会話に結果のカードと取り込むを足す」（開発の作業場 3 段目）。
//       2026-11-27 利用者「関門がわかりにくい。確認（テスト）にそろえて」。API は page.route で模擬する。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261122-1";
const REPO = `/data/sandboxes/${NAME}/work/repo`;
const PR = "https://github.com/example/repo/pull/7";

async function mock(page: Page) {
  const state = {
    dirty: true, check: null as null | { passed: boolean; stale: boolean },
    running: false, adopted: false, checks: 0, adopts: [] as Record<string, unknown>[],
    visualOnly: false, checkBodies: [] as unknown[], fixes: 0,
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
    base: "abc1234567", head: "def7654321", file_count: 3, added: 32, removed: 5, dirty: state.dirty,
    // 未コミットの変更も数に入れたうえでの内訳（x.py の未コミット +2 −1 と未追跡の new.ts）
    uncommitted_files: state.dirty ? 2 : 0,
    files: [{ path: "backend/app/x.py", added: 22, removed: 5, binary: false, untracked: false },
      { path: "docs/very/long/path/that/should/truncate/instead/of/overflowing/the/card/note.md", added: 10, removed: 0, binary: false, untracked: false },
      { path: "frontend/src/new.ts", added: 0, removed: 0, binary: false, untracked: true }],
    commits: [{ sha: "def7654321", author: "ControlDeck Sandbox", at: 1, subject: "x を直す" }], commit_count: 1,
    suggested_title: "ログイン画面の文言を直す",
    check: state.check ? { ...state.check, at: 1, head: "def7654321", items: state.check.passed
      ? [{ status: "ok", name: "テスト", message: "3 件" }]
      : state.visualOnly ? [{ status: "ok", name: "テスト", message: "3 件" }, { status: "ng", name: "画面の見た目", message: "重大 3 件" }]
      : [{ status: "ng", name: "テスト", message: "1 件落ちた" }] } : null,
    adopted: state.adopted ? { kind: "pr", branch: `ws/${NAME}`, pr_url: PR, head: "def7654321" } : null,
  } }));
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/check$`), (route) => {
    if (route.request().method() === "POST") {
      state.checks += 1;
      state.checkBodies.push(route.request().postDataJSON());
      state.running = true;
      setTimeout(() => { state.running = false; state.check = { passed: true, stale: false }; }, 1200);
      return route.fulfill({ status: 202, json: { job_id: "j9", already_running: false } });
    }
    return route.fulfill({ json: { check: state.check, running: state.running, job_id: state.running ? "j9" : null } });
  });
  await page.route(new RegExp(`/api/v1/opencode/workspaces/${NAME}/fix-check$`), (route) => {
    state.fixes += 1;
    return route.fulfill({ status: 202, json: {} });
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
    await expect(card).toContainText("3 ファイル +32 −5（うち未コミット 2）");
    // 4 つの状態の札: 未確認
    await expect(card.getByTestId("workspace-check-label")).toHaveText("未確認");
    const noOverflow = async () => expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await card.getByRole("button", { name: /作業場の結果/ }).click();
    await card.getByRole("button", { name: /変わったファイル 3 件を開く/ }).click();
    await expect(card.getByTestId("workspace-files")).toContainText("backend/app/x.py");
    await expect(card.getByTestId("workspace-files")).toContainText("frontend/src/new.ts");
    await expect(card.getByTestId("workspace-files")).toContainText("未追跡");
    // 未確認の主の操作は「確認してから取り込む」1 つ（説明も 1 行だけ）
    const checkAndAdopt = card.getByTestId("workspace-check-and-adopt");
    await expect(checkAndAdopt).toHaveText("確認してから取り込む");
    await expect(card.getByTestId("workspace-check-note")).toHaveText("取り込む前に、テストを流して壊れていないか確かめます");
    await noOverflow();
    await page.screenshot({ path: `test-results/workspace-result-${width}-before.png` });

    // 確認してから取り込む: 確認を流す（進み具合 → 合格）
    await checkAndAdopt.click();
    expect(state.checks).toBe(1);
    await expect(card.getByTestId("workspace-check-and-adopt")).toHaveText("確認中…");   // 4 つの状態の札: 確認中…
    await expect(card.getByTestId("workspace-check-note")).toContainText("テストを流しています");
    await expect(card.getByTestId("workspace-check-label")).toHaveText("合格", { timeout: 15_000 });
    // 合格後の主の操作は「取り込む」（確認してから取り込むは消える）。押せる大きさ（44px）を守る
    expect((await card.getByTestId("workspace-adopt").boundingBox())!.height).toBeGreaterThanOrEqual(43.5);
    // 合格したら取り込みの確認が自動で開く（合格の主の操作は「取り込む」）
    const dialog = page.getByRole("alertdialog");
    await expect(dialog).toContainText("GitHub に PR を作ります");
    await expect(dialog.getByRole("button", { name: "取り込む" })).toBeDisabled();
    // PR の題は候補（会話の題など）が入っていて書き換えられる（U18）
    await expect(dialog.getByTestId("adopt-title")).toHaveValue("ログイン画面の文言を直す");
    await dialog.getByTestId("adopt-title").fill("ログインの文言を直す");
    await dialog.getByRole("textbox", { name: /コミットメッセージ/ }).fill("x を直す");
    await page.screenshot({ path: `test-results/workspace-result-${width}-confirm.png` });
    await dialog.getByRole("button", { name: "取り込む" }).click();
    await expect(card.getByTestId("workspace-adopted")).toContainText("PR を作りました");
    await expect(card.getByTestId("workspace-adopted").getByRole("link")).toHaveAttribute("href", PR);
    expect(state.adopts).toEqual([{ confirm: true, force: false, commit_message: "x を直す", title: "ログインの文言を直す" }]);
    await noOverflow();
    await page.screenshot({ path: `test-results/workspace-result-${width}-adopted.png` });
  });
}

for (const width of [320, 1280]) {
  test(`workspace result card: failed check offers fixing, adopt-anyway is expert (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    if (width === 1280) await page.emulateMedia({ colorScheme: "dark" });
    await establishSession(page, context);
    const state = await mock(page);
    state.check = { passed: false, stale: false };
    await page.evaluate(() => { localStorage.setItem("cd-chat-conversation", "w1"); });
    await page.goto("/assistant");
    const card = page.locator('[data-testid="workspace-result"]');
    await card.getByRole("button", { name: /作業場の結果/ }).click();
    // 4 つの状態の札: 不合格（n 件）
    await expect(card.getByTestId("workspace-check-label")).toHaveText("不合格（1 件）");
    await expect(card.getByTestId("workspace-check-failures")).toContainText("テスト: 1 件落ちた");
    await expect(card.getByTestId("workspace-check-note")).toContainText("テストに落ちています");
    // 不合格の主の操作は「直させる」で、押すと落ちた内容を同じ会話へ送る
    const fix = card.getByTestId("workspace-fix-check");
    await expect(fix).toHaveText("直させる");
    expect((await fix.boundingBox())!.height).toBeGreaterThanOrEqual(43.5);
    // 「それでも取り込む」は細かい表示（エキスパート）の中
    const expert = card.getByTestId("workspace-expert");
    await expect(expert).toBeVisible();
    await expect(expert.getByTestId("workspace-adopt-anyway")).toBeHidden();   // 開く前は見えていない
    await expert.locator("summary").click();
    await expect(expert.getByTestId("workspace-adopt-anyway")).toBeVisible();
    await fix.click();
    await expect.poll(() => state.fixes).toBe(1);
    // 「それでも取り込む」も実際に動く: 確認（force）つきで POST /adopt される
    await expert.getByTestId("workspace-adopt-anyway").click();
    const dialog = page.getByRole("alertdialog", { name: "それでも取り込む" });
    await expect(dialog).toContainText("GitHub に PR を作ります");
    await expect(dialog).toContainText("不合格のまま取り込みます");
    await dialog.getByRole("textbox", { name: /コミットメッセージ/ }).fill("強制的に取り込む");
    await dialog.getByRole("button", { name: "それでも取り込む" }).click();
    await expect(card.getByTestId("workspace-adopted")).toContainText("PR を作りました");
    expect(state.adopts).toEqual([{ confirm: true, force: true, commit_message: "強制的に取り込む", title: "ログイン画面の文言を直す" }]);
    // 320/1280・ライト・ダークとも、横のはみ出しは DOM で確かめる（見た目の確認の model の目より確実）
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await page.screenshot({ path: `test-results/workspace-result-${width}-failed.png` });
  });
}

// 目的: 確認が「画面の見た目」だけで落ちたとき、管理者に「見た目は確かめた（外して流す）」が出て
//       （細かい表示の中）、skip_visual 付きで流し直せる。見た目以外も落ちているときは出ない。
// 根拠: 2026-11-22、画像を読むモデルの誤検出（重大 3 件。Opus が撮影を見て誤検出と判断）で、正しい変更が取り込めなかった。
test("workspace result card: visual-only failure can be skipped after a human check (320px)", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 320, height: 800 });
  await establishSession(page, context);
  const state = await mock(page);
  state.check = { passed: false, stale: false };
  state.visualOnly = true;
  await page.evaluate(() => { localStorage.setItem("cd-chat-conversation", "w1"); });
  await page.goto("/assistant");
  const card = page.locator('[data-testid="workspace-result"]');
  await card.getByRole("button", { name: /作業場の結果/ }).click();
  const expert = card.getByTestId("workspace-expert");
  await expert.locator("summary").click();
  const skip = card.getByTestId("workspace-check-skip-visual");
  await expect(skip).toBeVisible();
  expect((await skip.boundingBox())!.height).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: "test-results/workspace-result-320-visual-skip.png" });
  await skip.click();
  await expect.poll(() => state.checkBodies.length).toBe(1);
  expect(state.checkBodies[0]).toMatchObject({ skip_visual: true });
  await expect(card.getByTestId("workspace-check-label")).toHaveText("合格", { timeout: 15_000 });

  // 見た目以外も落ちていれば出ない
  state.visualOnly = false;
  state.check = { passed: false, stale: false };
  await page.reload();
  await page.locator('[data-testid="workspace-result"]').getByRole("button", { name: /作業場の結果/ }).click();
  await page.locator('[data-testid="workspace-result"]').getByTestId("workspace-expert").locator("summary").click();
  await expect(page.getByTestId("workspace-check-skip-visual")).toHaveCount(0);
});
