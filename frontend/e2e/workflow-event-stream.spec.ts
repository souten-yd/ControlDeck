import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: import("@playwright/test").Page) {
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
}

test("Execution Debugger follows the durable event stream at mobile and desktop widths", async ({ page }) => {
  test.setTimeout(45_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  const streams: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  page.on("request", (request) => {
    if (request.url().includes("/workflow-executions/") && request.url().includes("/stream")) {
      streams.push(request.url());
    }
  });

  await page.setViewportSize({ width: 320, height: 700 });
  await login(page);
  runtimeErrors.length = 0; // 未ログイン状態の /auth/me 401 は想定内。
  const created = await page.evaluate(async () => {
    const response = await fetch("/api/v1/workflows", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({
        name: "E2E Durable Event Stream",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", name: "開始", config: { mode: "manual" }, position: { x: 60, y: 140 } },
            { id: "wait", type: "util.wait", name: "イベント待機", config: { seconds: 2 }, position: { x: 330, y: 140 } },
            { id: "result", type: "signal.display", name: "完了", config: { signal: "done", value: "streamed" }, position: { x: 600, y: 140 } },
          ],
          edges: [
            { id: "e1", source: "trigger", target: "wait" },
            { id: "e2", source: "wait", target: "result" },
          ],
        },
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    return await response.json() as { id: number };
  });

  try {
    const executionId = await page.evaluate(async (workflowId) => {
      const response = await fetch(`/api/v1/workflows/${workflowId}/test`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
        body: JSON.stringify({ input: {} }),
      });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json() as { execution_id: number }).execution_id;
    }, created.id);

    await page.goto(`/workflows/${created.id}`);
    await page.getByRole("button", { name: "実行情報" }).click();
    const debuggerPanel = page.getByLabel("実行デバッグパネル");
    await expect(debuggerPanel).toBeVisible();
    await expect(debuggerPanel.getByRole("status")).toContainText(/ライブ|接続中/);
    await expect.poll(() => streams.some((url) => url.includes(`/workflow-executions/${executionId}/stream`))).toBe(true);
    await expect.poll(async () => page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/workflow-executions/${id}`, { credentials: "same-origin" });
      return (await response.json() as { status: string }).status;
    }, executionId)).toBe("SUCCEEDED");
    await expect(debuggerPanel.getByText("成功", { exact: true }).first()).toBeVisible({ timeout: 10_000 });

    for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
      await page.setViewportSize(viewport);
      const layout = await debuggerPanel.evaluate((element) => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
        left: element.getBoundingClientRect().left,
        right: element.getBoundingClientRect().right,
      }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport);
      expect(layout.left).toBeGreaterThanOrEqual(0);
      expect(layout.right).toBeLessThanOrEqual(layout.viewport);
    }
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (workflowId) => {
      await fetch(`/api/v1/workflows/${workflowId}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, created.id);
  }
});
