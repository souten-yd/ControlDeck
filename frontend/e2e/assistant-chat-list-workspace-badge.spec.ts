// 目的: 会話の一覧に、作業場を使う OpenCode の会話の状態の小さな印（作業中・質問待ち・完了・中断）が出る。
//       作業場を使わない会話には印が無い。320px でも題が読め（横にはみ出さない）、ライト・ダークで印が読める。
// 根拠: 2026-11-23 ROADMAP「AI 開発環境の UX 改善」U9。Project Lab の作業場の一覧（#560）と同じ API
//       （/opencode/workspaces/progress）・中断は #561 の interrupted を使う。API は page.route で模擬する。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const STATES = [
  { name: "ws-a", conv: "c-work", title: "作業場の会話（作業中）とてもとても長い題名で一覧の幅を試す", state: "working", interrupted: false, mark: "作業中" },
  { name: "ws-b", conv: "c-wait", title: "質問待ちの会話", state: "waiting", interrupted: false, mark: "質問待ち" },
  { name: "ws-c", conv: "c-done", title: "完了した会話", state: "done", interrupted: false, mark: "完了" },
  { name: "ws-d", conv: "c-cut", title: "中断した会話", state: "stopped", interrupted: true, mark: "中断" },
];

async function mock(page: Page) {
  const now = new Date().toISOString().replace("Z", "");
  const conversations = [...STATES.map((s) => ({ id: s.conv, title: s.title, updated_at: now })),
    { id: "c-plain", title: "ふつうの会話", updated_at: now }];
  await page.route(/\/api\/v1\/meta$/, async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...(meta.enabled_features ?? []), "opencode-v2"];
    return route.fulfill({ response, json: meta });
  });
  await page.route(/\/api\/v1\/opencode\/projects$/, (route) => route.fulfill({ json: { root: "/CodeDEV", projects: [] } }));
  await page.route(/\/api\/v1\/chat\/opencode\/permission-mode$/, (route) => route.fulfill({ json: { mode: "auto" } }));
  const item = (s: (typeof STATES)[number]) => ({
    name: s.name, source: "controldeck", source_label: "ControlDeck 本体", origin: "", branch: `sandbox/${s.name}`,
    dirty: false, unpushed: 0, created_at: 1, last_used: 1, repo_path: `/data/sandboxes/${s.name}/work/repo`,
    progress: { state: s.state, todo: null, last_active_at: 1, interrupted: s.interrupted, conversation_id: s.conv, title: s.title } });
  await page.route(/\/api\/v1\/opencode\/workspaces$/, (route) => route.fulfill({ json: { workspaces: STATES.map(item) } }));
  await page.route(/\/api\/v1\/opencode\/workspaces\/progress$/, (route) => route.fulfill({ json: { workspaces: STATES.map(item), active: 2 } }));
  await page.route(/\/api\/v1\/chat\/conversations(\?archived=true)?$/, (route) => route.fulfill({
    json: route.request().url().endsWith("archived=true") ? [] : conversations }));
}

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`conversation list shows workspace state marks (${width}px, ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials required");
      await page.setViewportSize({ width, height: 800 });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      await mock(page);
      await page.goto("/assistant");
      let list = page.locator("[data-assistant-history-sidebar]");
      if (width < 1024) {
        await page.locator("[data-assistant-history-button]").click();
        list = page.getByRole("dialog").filter({ has: page.locator("[data-history-row]") });
      }
      for (const s of STATES) {
        const row = list.locator(`[data-history-row="${s.conv}"]`);
        await expect(row.locator("[data-workspace-mark]")).toHaveText(s.mark);
        // 題の欄が印に押しつぶされない（読める幅が残る）
        const box = await row.getByRole("button", { name: s.title, exact: true }).boundingBox();
        expect(box!.width).toBeGreaterThan(width === 320 ? 100 : 150);
      }
      await expect(list.locator('[data-history-row="c-plain"] [data-workspace-mark]')).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
      await page.waitForTimeout(500); // シートの開く動きが終わってから撮る
      await page.screenshot({ path: `test-results/chat-list-ws-badge-${width}-${scheme}.png` });
    });
  }
}
