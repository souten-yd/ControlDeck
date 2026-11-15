import type { Page, Route } from "@playwright/test";

/** OpenCode が入っていない環境（./deck.sh e2e の検証用インスタンス）で OpenCode 画面を開く。
 *
 * OpenCode の API と画面の route は機能が有効なときだけ出る。ここでは meta の
 * enabled_features に opencode-v2 を足し、画面が最初に読む API を差し替え、client 側で
 * /opencode-v2 へ移る。報告など spec ごとの API は呼び出し側が先に page.route で足す。 */
export async function openOpenCode(page: Page, workflow?: unknown): Promise<void> {
  const json = (route: Route, body: unknown) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route("**/api/v1/meta", async (route) => {
    const response = await route.fetch();
    const meta = await response.json();
    meta.enabled_features = [...new Set([...(meta.enabled_features ?? []), "opencode-v2"])];
    await route.fulfill({ response, json: meta });
  });
  await page.route("**/api/v1/opencode/status*", (route) => json(route, {
    feature: { id: "opencode-v2", installed: true, enabled: true, version: "2.3.0" },
    settings: { base_url: "http://127.0.0.1:8765/api/v1/llm/v1", model: "auto", project_path: "", use_gateway: true, runtime: "v2" },
    active_runtime: "v2", runtimes: {},
  }));
  await page.route("**/api/v1/opencode/projects", (route) => json(route, { root: "~/CodeDEV", projects: [] }));
  if (workflow !== undefined) {
    await page.route("**/api/v1/opencode/workflow", (route) => json(route, workflow));
  }
  await page.goto("/", { waitUntil: "load" });
  await page.evaluate(() => {
    window.history.pushState({}, "", "/opencode-v2");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await page.waitForURL("**/opencode-v2");
}
