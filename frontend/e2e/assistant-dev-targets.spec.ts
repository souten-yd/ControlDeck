// 目的: ＋メニューの「開発対象」（普通の開発／ローカル LLM を使うアプリ／ControlDeck 本体）と機能の一覧を確かめる。
//   作業場を作るときは選べて（作る API に target・capabilities が渡る）、作業場を選んでいるときは表示だけ。
//   「詳しく」で機能の一覧（gpu は「準備中」で押せない）。モードと権限は半分の幅で左右に並べた 1 行で、既存の動き（PUT permission-mode）は同じ。
//   メニューはコンパクト（320px で画面の高さの 60% 以下・押せる範囲 44px 以上・はみ出さない）。作業場の札に開発対象が出る。
// 根拠: 2026-11-22 利用者「開発対象を＋ボタンのところに記載して選択できるように。プラン/ビルド・権限もトグルで、
//   表示面積を小さくして＋メニューをコンパクトに」。API は page.route で模擬する（箱・OpenCode は動かさない）。
//   同日の追加依頼: モード・権限はフラットアイコンのみ（svg がある・aria-label が残る）、メニュー幅 288px（≤300px で確かめる）、
//   機能タイルはアイコンとラベルの横並び 1 行（中心 y が同じ）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const ws = (name: string, extra: object = {}) => ({
  name, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${name}`,
  dirty: false, unpushed: 0, created_at: 1, last_used: Date.now() / 1000 - 300,
  repo_path: `/data/sandboxes/${name}/work/repo`,
  target: "controldeck", target_label: "本体", capabilities: ["llm", "tools", "mirror"], adopt: "pr", ...extra,
});

async function mock(page: Page) {
  const state = {
    list: [ws("ws-20261122-1"), ws("ws-20261122-2", { target: "llm_app", target_label: "LLM アプリ", capabilities: ["llm", "tools"], adopt: "branch", source: "codedev:app", source_label: "CodeDEV: app" })],
    created: [] as Record<string, unknown>[], permissionPuts: [] as Record<string, unknown>[],
  };
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [{ name: "app", path: "/CodeDEV/app", git: true }] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => {
    if (route.request().method() === "PUT") state.permissionPuts.push(route.request().postDataJSON() as Record<string, unknown>);
    return route.fulfill({ json: { mode: "auto" } });
  });
  await page.route(/\/api\/v1\/opencode\/workspaces(\?.*)?$/, (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      state.created.push(body);
      const made = ws("ws-20261122-9", { target: body.target, capabilities: body.capabilities ?? ["tools"] });
      state.list = [made, ...state.list];
      return route.fulfill({ status: 201, json: made });
    }
    return route.fulfill({ json: { workspaces: state.list } });
  });
  await page.route(/\/api\/v1\/opencode\/workspaces\/[^/]+\/(changes|check)$/, (route) => route.fulfill({ status: 404, json: { detail: "none" } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/messages$/, (route) => route.fulfill({ json: {
    conversation: { id: "w1", title: "作業場" },
    messages: [{ id: "u0", role: "user", content: "作って", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:00Z", meta: { mode_choice: "code", agent_choice: "build" } },
      { id: "a0", role: "assistant", content: "作りました", thinking: "", status: "completed", job_id: null, model: "", error: "", created_at: "2026-11-22T00:00:01Z",
        meta: { mode: "code", project_path: "/data/sandboxes/ws-20261122-1/work/repo", agent: "build" } }],
  } }));
  await page.route(/\/api\/v1\/chat\/conversations\/w1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  return state;
}

for (const [width, height] of [[1280, 900], [320, 700]]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`plus menu shows the dev target, details and compact toggles (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      const state = await mock(page);
      await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
      await page.goto("/assistant");
      const shell = page.locator("[data-assistant-shell]");
      await expect(shell.getByText("作りました")).toBeVisible();
      const select = shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）");
      await expect(select).toHaveValue("ws:ws-20261122-1");
      // 作業場の札に開発対象が出る
      await expect(shell.locator('[data-testid="workspace-info"] [data-testid="workspace-badge"]')).toHaveText(/作業場・本体/);

      // 選んでいる作業場: 開発対象は表示だけ
      await shell.locator("[data-assistant-plus]").click();
      const menu = page.getByRole("menu", { name: "添付・機能・権限" });
      const group = menu.getByRole("group", { name: "開発対象" });
      await expect(group).toBeVisible();
      await expect(group.locator('[data-target-option="controldeck"]')).toHaveAttribute("aria-checked", "true");
      for (const id of ["app", "llm_app", "controldeck"]) await expect(group.locator(`[data-target-option="${id}"]`)).toBeDisabled();
      await expect(group.locator("[data-target-note]")).toContainText("PR");
      // コンパクト: 高さは画面の 60% 以下、押せる所は 44px 以上、はみ出さない
      const box = (await menu.boundingBox())!;
      // 会話の引き継ぎなど「そのときだけ出す操作」の行（各 44px）は数えない
      const extra = (await menu.locator("[data-plus-action]:not([data-plus-action=attach])").count()) * 44;
      expect(box.height - extra).toBeLessThanOrEqual(height * 0.6);
      expect(box.width).toBeLessThanOrEqual(300);   // 2026-11-22 横幅も小さく（1280px で旧 336px）
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(width);
      expect(await menu.evaluate((el) => el.scrollHeight - el.clientHeight)).toBeLessThanOrEqual(1);
      for (const button of await menu.locator("[data-plus-item]").all()) {
        if (!(await button.isVisible())) continue;
        expect((await button.boundingBox())!.height, await button.innerText()).toBeGreaterThanOrEqual(43.5);
      }
      // 2026-11-22: 機能タイルはアイコンとラベルの横並び 1 行（中心 y が同じ。前は縦積み 2 行）
      const [tileIcon, tileLabel] = await menu.locator('[data-mode-option="auto"]').locator("span").all();
      const iconBox = (await tileIcon.boundingBox())!, labelBox = (await tileLabel.boundingBox())!;
      expect(Math.abs(iconBox.y + iconBox.height / 2 - (labelBox.y + labelBox.height / 2))).toBeLessThanOrEqual(2);
      // 2026-11-22 の追試（ui-verifier）: タイルのラベルは「…」で省略しない（長い名前は tile の短縮名で出す）
      for (const b of await menu.locator("[data-mode-option] span:last-child").all())
        expect(await b.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), await b.innerText()).toBe(true);
      await page.screenshot({ path: `test-results/assistant-dev-targets-${width}-${scheme}-view.png` });
      // 詳しく: 機能の一覧（表示だけ。gpu は準備中）
      await group.locator("[data-target-detail]").click();
      const caps = group.locator("[data-target-capabilities]");
      await expect(caps.locator('[data-capability="llm"]')).toHaveAttribute("aria-checked", "true");
      await expect(caps.locator('[data-capability="mirror"]')).toHaveAttribute("aria-checked", "true");
      await expect(caps.locator('[data-capability="gpu"]')).toBeDisabled();
      await expect(caps.locator('[data-capability="gpu"]')).toContainText("準備中");
      await expect(caps.locator('[data-capability="llm"]')).toBeDisabled();
      await page.screenshot({ path: `test-results/assistant-dev-targets-${width}-${scheme}-view-detail.png` });
      await group.locator("[data-target-detail]").click();

      // モードと権限は半分の幅で左右に並べた 1 行（2026-11-22 利用者）。ボタンはフラットアイコンのみ。既存の動き（PUT permission-mode）は同じ
      const agentRow = menu.getByRole("group", { name: "OpenCode のモード" });
      const permRow = menu.getByRole("group", { name: "OpenCode の権限" });
      const a = (await agentRow.boundingBox())!, p = (await permRow.boundingBox())!;
      expect(Math.abs(a.y - p.y)).toBeLessThanOrEqual(2);          // 同じ行
      expect(a.x + a.width).toBeLessThanOrEqual(p.x + 1);          // 左がモード、右が権限
      expect(Math.max(a.height, p.height)).toBeLessThanOrEqual(64); // 1 行に収まる
      for (const b of await menu.locator("[data-agent-option], [data-permission-option]").all()) {
        expect((await b.boundingBox())!.height).toBeGreaterThanOrEqual(44);
        expect(await b.locator("svg").count()).toBeGreaterThan(0);    // 2026-11-22 フラットアイコンのみ
        expect(await b.getAttribute("aria-label")).toBeTruthy();     // アイコンだけでも名前が残る
      }
      await permRow.locator('[data-permission-option="ask"]').click();
      await expect.poll(() => state.permissionPuts.length).toBe(1);
      expect(state.permissionPuts[0]).toMatchObject({ mode: "ask" });
      await shell.locator("[data-assistant-plus]").click();
      await menu.locator('[data-agent-option="plan"]').click();
      await expect(shell.locator('[data-composer-pill="plan"]')).toBeVisible();
      await page.keyboard.press("Escape");

      // 別の作業場（LLM アプリ）に替えると表示が変わり、札も変わる
      await select.selectOption("ws:ws-20261122-2");
      await expect(shell.locator('[data-testid="workspace-info"] [data-testid="workspace-badge"]')).toHaveText(/作業場・LLM アプリ/);
      await shell.locator("[data-assistant-plus]").click();
      await expect(menu.locator('[data-target-option="llm_app"]')).toHaveAttribute("aria-checked", "true");
      await expect(menu.locator("[data-target-note]")).toContainText("ローカル LLM");
      await page.keyboard.press("Escape");

      // 作るとき: 選べる。元が本体のときだけ controldeck / mirror。作る API に target と機能が渡る
      await select.selectOption("__ws_new__");
      await shell.locator("[data-assistant-plus]").click();
      const create = menu.getByRole("group", { name: "開発対象" });
      await expect(create.locator('[data-target-option="app"]')).toBeEnabled();
      await create.locator('[data-target-option="app"]').click();
      await expect(create.locator("[data-target-note]")).toContainText("ローカル LLM は借りません");
      await create.locator("[data-target-detail]").click();
      await expect(create.locator('[data-capability="llm"]')).toHaveAttribute("aria-checked", "false");
      await create.locator('[data-capability="llm"]').click();
      await expect(create.locator('[data-capability="llm"]')).toHaveAttribute("aria-checked", "true");
      await page.screenshot({ path: `test-results/assistant-dev-targets-${width}-${scheme}-create.png` });
      await page.keyboard.press("Escape");
      await shell.locator('[data-testid="workspace-create"]').getByRole("button", { name: "作る" }).click();
      await expect.poll(() => state.created.length).toBe(1);
      expect(state.created[0]).toMatchObject({ source: "controldeck", target: "app" });
      expect((state.created[0].capabilities as string[]).sort()).toEqual(["llm", "tools"]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    });
  }
}

test("target controldeck and mirror cannot be chosen when the source is a CodeDEV project", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await page.setViewportSize({ width: 390, height: 800 });
  await establishSession(page, context);
  await mock(page);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "w1"));
  await page.goto("/assistant");
  const shell = page.locator("[data-assistant-shell]");
  await shell.getByLabel("作業フォルダ（OpenCode のプロジェクト）").selectOption("__ws_new__");
  await shell.getByLabel("作業場の元").selectOption("codedev:app");
  await shell.locator("[data-assistant-plus]").click();
  const menu = page.getByRole("menu", { name: "添付・機能・権限" });
  await expect(menu.locator('[data-target-option="controldeck"]')).toBeDisabled();
  await expect(menu.locator('[data-target-option="llm_app"]')).toHaveAttribute("aria-checked", "true");
  await menu.locator("[data-target-detail]").click();
  await expect(menu.locator('[data-capability="mirror"]')).toBeDisabled();
  await expect(menu.locator('[data-capability="mirror"]')).toContainText("ControlDeck 本体のときだけ");
});
