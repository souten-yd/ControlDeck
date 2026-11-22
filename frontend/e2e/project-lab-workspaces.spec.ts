// 目的: Project Lab の一覧の上の「プロジェクト｜作業場」の切り替えと、作業場のカード（元・開発対象の札・
//       進み具合・ToDo・最後に動いた時刻・会話を開く）が正しく出る。AI アシスタントの作業フォルダの選択欄は
//       「CodeDEV のプロジェクト」と「作業場（一時）」に分かれ、「🧪 ＋ 新しい作業場（ControlDeck を直す・試す）」は選択欄の中の「➕ 新規作成」のすぐ下に出る（利用者の決定。選択欄の外の別のボタンは作らない）。
// 根拠: 2026-11-22 利用者「Project Lab で現在の進捗とかって確認できないんだったっけ…参照先がテンポラリーな
//       フォルダなら、今あるドロップダウンの一覧とは別にした方がいい」「入口が選択欄のいちばん下に隠れて分からない」。
//       API は模擬する（箱・OpenCode を動かさない）。320・1280、ライト・ダークの撮影もこの中で行う。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NOW = Date.now() / 1000;
const REPO_A = "/data/sandboxes/ws-a/work/repo";

function wsProgress(name: string, extra: object = {}) {
  return {
    name, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${name}`,
    dirty: false, unpushed: 0, created_at: NOW - 3600, last_used: NOW - 300,
    repo_path: `/data/sandboxes/${name}/work/repo`, target: "controldeck", target_label: "本体",
    capabilities: ["llm", "tools", "mirror"], adopt: "pr", progress: null, ...extra,
  };
}

async function mockLab(page: Page) {
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/project-lab\/projects$/, (route) => route.fulfill({ json: [] }));
  await page.route(/\/api\/v1\/project-lab\/settings$/, (route) =>
    route.fulfill({ json: { allow_external_preview: false, project_root: "/CodeDEV" } }));
  await page.route(/\/api\/v1\/opencode\/workspaces\/progress$/, (route) => route.fulfill({ json: {
    active: 1,
    workspaces: [
      wsProgress("ws-a", { progress: {
        state: "done", todo: { done: 2, total: 3 },
        last_active_at: Date.now() / 1000 - 300, conversation_id: "w1", title: "ボタンを直す",
      } }),
      wsProgress("ws-b", { progress: {
        state: "working", todo: { done: 1, total: 4 },
        last_active_at: Date.now() / 1000, conversation_id: "w2", title: "メニューを直す",
      } }),
    ],
  } }));
  await page.route(/\/api\/v1\/opencode\/workspaces\/[^/]+\/(changes|check|diff|merge)/, (route) => route.fulfill({ json: {
    base: "0000000", head: "1111111", file_count: 3, added: 7, removed: 1, dirty: true,
    uncommitted_files: 2,
    files: [{ path: "a.txt", added: 7, removed: 1, binary: false, untracked: false },
      { path: "b.txt", added: 0, removed: 0, binary: false, untracked: true },
      { path: "c.txt", added: 0, removed: 0, binary: false, untracked: true }],
    commits: [{ sha: "abc1234567", author: "t", at: NOW, subject: "直す" }], commit_count: 1,
    check: { passed: true, at: NOW, stale: false, head: "1111111", items: [] },
    adopted: null, merged: null, running: false, job_id: null,
  } }));
}

for (const [width, height] of [[1280, 900], [320, 700]] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`project lab switches to workspaces and shows progress (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      await mockLab(page);
      await page.goto("/project-lab");
      // モバイル（320px）の一覧は header の中から開く
      if (width < 768) {
        await page.getByRole("button", { name: "Project Lab" }).click();
      }
      // 動いている作業場があればタブに件数の印
      const workspacesTab = page.getByRole("button", { name: /^作業場・1$/ });
      await expect(workspacesTab).toBeVisible();
      await expect(page.getByRole("button", { name: "プロジェクト", exact: true })).toBeVisible();
      await workspacesTab.click();
      // 一覧: 進み具合の短い印と ToDo の n/m（320px の一覧は header の Popover の中）
      const rows = width < 768
        ? page.getByRole("dialog", { name: "作業場" }).getByTestId("workspace-row")
        : page.getByTestId("workspace-row");
      await expect(rows).toHaveCount(2);
      await expect(rows.first()).toContainText("ws-a");
      await expect(rows.first()).toContainText(/完了/);
      await expect(rows.first()).toContainText("2/3");
      await expect(rows.nth(1)).toContainText("作業中");
      await rows.first().click();
      // カード: 元・開発対象の札・進み具合・ToDo・最後に動いた時刻・会話を開く
      const card = page.getByTestId("workspace-card");
      await expect(card).toBeVisible();
      await expect(card).toContainText("ws-a");
      await expect(card).toContainText("作業場・本体");
      await expect(card).toContainText("元: ControlDeck 本体");
      await expect(page.getByTestId("workspace-progress-state")).toHaveText(/完了/);
      await expect(card).toContainText("ToDo 2/3");
      await expect(card).toContainText(/最後に動いた: /);
      await expect(card.getByRole("button", { name: /会話を開く/ })).toBeVisible();
      // 結果のカード（既存の部品）: 未コミットも数に入れた変更
      const result = page.getByTestId("workspace-result");
      await expect(result).toContainText("3 ファイル +7 −1（うち未コミット 2）");
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `test-results/project-lab-workspaces-${width}-${scheme}.png` });

      // 会話を開く: localStorage に会話 ID を書いて AI アシスタントへ
      await card.getByRole("button", { name: /会話を開く/ }).click();
      await expect(page).toHaveURL(/\/assistant/);
      expect(await page.evaluate(() => localStorage.getItem("cd-chat-conversation"))).toBe("w1");
    });
  }
}

for (const [width, height] of [[1280, 900], [320, 700]] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`assistant folder select separates codedev and workspaces (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      // 選択欄と選択の値（__ws_new__）は変えない（E2E が使っている）——並びと入口だけ変える
      await page.route(/\/api\/v1\/meta$/, async (route) => {
        const response = await route.fetch();
        const meta = await response.json();
        meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
        return route.fulfill({ response, json: meta });
      });
      await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
      await page.route(/\/api\/v1\/opencode\/workspaces(\?.*)?$/, (route) => route.fulfill({ json: { workspaces: [
        { name: "ws-a", source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: "sandbox/ws-a",
          dirty: false, unpushed: 0, created_at: NOW - 3600, last_used: NOW - 300, repo_path: REPO_A,
          target: "controldeck", target_label: "本体", capabilities: ["llm", "tools"], adopt: "pr" },
      ] } }));
      await page.route(/\/api\/v1\/opencode\/workspaces\/[^/]+\/(changes|check)$/, (route) =>
        route.fulfill({ status: 404, json: { detail: "none" } }));
      await page.route(/\/api\/v1\/chat\/conversations\/w1\/messages$/, (route) => route.fulfill({ json: {
        conversation: { id: "w1", title: "作業場" },
        messages: [{ id: "u0", role: "user", content: "直して", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:00Z", meta: { mode_choice: "code", agent_choice: "build" } },
          { id: "a0", role: "assistant", content: "直しました", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:01Z",
            meta: { mode: "code", project_path: REPO_A, agent: "build" } }],
      } }));
      await page.route(/\/api\/v1\/chat\/conversations\/w1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));

      await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
      await page.goto("/assistant");
      const shell = page.locator("[data-assistant-shell]");
      await expect(shell.getByText("直しました")).toBeVisible();
      const select = shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）");
      // 選択の組: CodeDEV のプロジェクト（残る）と作業場（一時）が分かれる
      const groups = await select.locator("optgroup").evaluateAll((els) => els.map((el) => el.getAttribute("label")));
      expect(groups).toEqual(["CodeDEV のプロジェクト", "作業場（一時）"]);
      await expect(select.selectOption({ label: "🧪 ws-a（ControlDeck 本体・本体）" })).resolves.toBeTruthy();
      await expect(select).toHaveValue("ws:ws-a");
      // 入口は選択欄の中の「➕ 新規作成」のすぐ下（外の別のボタンは無い）。その下に optgroup
      const values = await select.locator(":scope > option").evaluateAll((els) => els.map((el) => (el as HTMLOptionElement).value));
      expect(values).toEqual(["__new__", "__ws_new__"]);
      await expect(select.locator(":scope > option").nth(1)).toHaveText("🧪 ＋ 新しい作業場（ControlDeck を直す・試す）");
      await expect(shell.getByTestId("workspace-new-button")).toHaveCount(0);
      const selectBox = (await select.boundingBox())!;
      expect(selectBox.height).toBeGreaterThanOrEqual(36);
      await select.selectOption("__ws_new__");
      await expect(shell.locator('[data-testid="workspace-create"]')).toBeVisible();
      await page.screenshot({ path: `test-results/assistant-workspace-entry-${width}-${scheme}.png` });
      // 既存の選択の値と動き（__ws_new__ → 作成欄）はそのまま
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    });
  }
}
