import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("diagnoses, previews and selectively applies a workflow patch at 320 and 1280px", async ({ page }) => {
  test.setTimeout(60_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  const workflowId = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const create = await fetch("/api/v1/workflows", { method: "POST", credentials: "same-origin", headers, body: JSON.stringify({
      name: "E2E Project Intelligence",
      definition: {
        nodes: [
          { id: "trigger", type: "trigger", name: "開始", config: { mode: "manual", inputs: [{ key: "message", label: "Message", type: "text", sample: "hello" }] }, position: { x: 20, y: 140 } },
          { id: "wait", type: "util.wait", name: "短すぎるTimeout", config: { seconds: 0.5, node_timeout: 0.1 }, position: { x: 270, y: 140 } },
          { id: "result", type: "flow.return", name: "結果", config: { name: "answer", value: "done" }, position: { x: 520, y: 140 } },
          { id: "route", type: "ai.route", name: "Runtime自動選択", config: { strategy: "balanced", min_context: 0, min_free_vram_mb: 0, allow_unavailable: false }, position: { x: 270, y: 360 } },
        ], edges: [{ source: "trigger", target: "wait" }, { source: "wait", target: "result" }],
      },
    }) });
    if (!create.ok) throw new Error(await create.text());
    const id = (await create.json()).id as number;
    const publish = await fetch(`/api/v1/workflows/${id}/publish`, { method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } });
    if (!publish.ok) throw new Error(await publish.text());
    const run = await fetch(`/api/v1/workflows/${id}/run`, { method: "POST", credentials: "same-origin", headers, body: "{}" });
    if (!run.ok) throw new Error(await run.text());
    const executionId = (await run.json()).execution_id as number;
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const live = await fetch(`/api/v1/workflow-executions/${executionId}/live`, { credentials: "same-origin" });
      const data = await live.json();
      if (!["QUEUED", "RUNNING", "WAITING"].includes(data.status)) {
        if (data.status !== "TIMED_OUT" && data.status !== "FAILED") throw new Error(`unexpected status ${data.status}`);
        return id;
      }
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    throw new Error("execution did not fail in time");
  });

  try {
    await page.goto(`/workflows/${workflowId}`);
    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: "Project Intelligence" }).click();
    const panel = page.getByRole("dialog", { name: "Project Intelligence" });
    await expect(panel).toBeVisible();
    await expect(panel.getByText("失敗を診断")).toBeVisible();
    await panel.getByRole("button", { name: "ローカル診断" }).click();
    const timeoutProposal = panel.getByRole("article").filter({ hasText: "timeoutを段階的に延長" });
    await expect(timeoutProposal).toBeVisible();
    await expect(timeoutProposal.getByText("操作差分を確認")).toBeVisible();
    await timeoutProposal.getByRole("button", { name: "この案を適用" }).click();
    await expect(page.getByText("選択した修正案を適用しました", { exact: true })).toBeVisible();
    await panel.getByRole("button", { name: "Baselineテスト" }).click();
    await expect(page.getByText(/Baselineテスト 1件を準備しました/)).toBeVisible();
    const mobileLayout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    expect(mobileLayout.document).toBeLessThanOrEqual(mobileLayout.viewport);
    await panel.getByRole("button", { name: "閉じる" }).click();

    const saved = await page.evaluate(async (id) => (await fetch(`/api/v1/workflows/${id}`, { credentials: "same-origin" })).json(), workflowId);
    expect(saved.definition.nodes.find((node: { id: string }) => node.id === "wait").config.node_timeout).toBeGreaterThan(30);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await page.locator(".react-flow__node").filter({ hasText: "Runtime自動選択" }).click();
    await expect(page.locator("#node-config-route-strategy")).toHaveValue("balanced");
    await page.getByRole("dialog", { name: "AI Runtime Route" }).getByRole("button", { name: "閉じる" }).click();
    await page.getByRole("button", { name: "Intelligence" }).click();
    const desktopPanel = page.getByRole("dialog", { name: "Project Intelligence" });
    await expect(desktopPanel.getByLabel("診断endpoint").locator("option")).not.toHaveCount(1);
    const desktopLayout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    expect(desktopLayout.document).toBeLessThanOrEqual(desktopLayout.viewport);
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, { method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" } });
    }, workflowId);
  }
});
