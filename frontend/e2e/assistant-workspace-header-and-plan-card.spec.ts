// 目的: (1) 作業場を選んだ OpenCode のヘッダの札は、会話のモデル（auto）ではなく作業場のモデルの並びの先頭
//       （opencode-go/glm-5.3 → 「GLM-5.3（作業場）」）を出すこと。(2) ファイルを変えていない回（プラン）の完了カードには
//       「レビューして」を出さず、ファイルを変えた回には出すこと。
// 根拠: 2026-11-23 の評価（ヘッダが auto のままで、どのモデルが動くか分からない・プランの回にレビューが出る）、ROADMAP U22。
//       API は page.route で模擬する（箱・OpenCode・LLM を動かさない）。
import { expect, test, type Page } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const NAME = "ws-20261123-2";
const REPO = `/data/sandboxes/${NAME}/work/repo`;
const msg = (id: string, role: "user" | "assistant", content: string, meta: object = {}) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta, created_at: "2026-11-23T00:00:00Z",
});

async function mock(page: Page, messages: object[]) {
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
    model_chain: ["opencode-go/glm-5.3", "opencode/hy4-flash-free", "controldeck/auto"] }] } }));
  await page.route(/\/api\/v1\/chat\/conversations\/p1\/messages$/, (route) => route.fulfill({ json: { conversation: { id: "p1", title: "プラン" }, messages } }));
  await page.route(/\/api\/v1\/chat\/conversations\/p1\/queue$/, (route) => route.fulfill({ json: { items: [], generating_message_id: null, held: false } }));
  await page.routeWebSocket(/\/chat\/messages\/[^/]+\/stream/, () => undefined);
  await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "p1"));
}

for (const width of [320, 1280]) {
  test(`header shows the workspace model; plan turn has no review button (${width}px)`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 800 });
    await establishSession(page, context);
    const change = { file: "src/a.tsx", additions: 1, deletions: 0, status: "modified" };
    const meta = { mode: "code", project_path: REPO, agent: "build" };
    await mock(page, [msg("u0", "user", "計画して", { mode_choice: "code", agent_choice: "plan" }),
      msg("a0", "assistant", "プランを作りました。", { ...meta, agent: "plan" }),
      msg("u1", "user", "作って", { mode_choice: "code", agent_choice: "build" }),
      msg("a1", "assistant", "作りました。", { ...meta, changes: [change] })]);
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    await expect(shell.getByText("プランを作りました。").first()).toBeVisible();
    await expect(shell.getByTitle(/GLM-5\.3（作業場）/)).toBeVisible();
    await expect(shell.getByTitle(/^auto/)).toHaveCount(0);
    const cards = shell.locator("[data-code-done]");
    await expect(cards).toHaveCount(2);
    await expect(cards.nth(0).locator("[data-review-ask]")).toHaveCount(0);   // プランの回
    await expect(cards.nth(1).locator("[data-review-ask]")).toBeVisible();     // ファイルを変えた回
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
  });
}
