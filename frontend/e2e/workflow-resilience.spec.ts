import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("batches input and enforces durable rate and circuit control at desktop and 320px", async ({ page }) => {
  test.setTimeout(45_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  const workflowId = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const definition = {
      nodes: [
        { id: "trigger", type: "trigger", name: "入力", config: { mode: "manual", inputs: [
          { key: "action", label: "操作", type: "select", required: true, options: "check\nfail", default: "check" },
          { key: "items", label: "処理項目", type: "json_array", required: true, sample: [1, 2, 3, 4, 5] },
        ] }, position: { x: 30, y: 180 } },
        { id: "is_fail", type: "condition.if", name: "失敗を記録するか", config: { left: "{{trigger.action}}", op: "eq", right: "fail" }, position: { x: 260, y: 180 } },
        { id: "failure", type: "control.circuit_breaker", name: "失敗を記録", config: { operation: "record_failure", scope: "e2e-vendor", failure_threshold: 1, recovery_seconds: 2 }, position: { x: 500, y: 40 } },
        { id: "failed", type: "flow.return", name: "OPEN結果", config: { name: "opened_result", title: "OPEN結果", value: "opened={{failure.state}}" }, position: { x: 760, y: 40 } },
        { id: "batch", type: "data.batch", name: "2件ずつバッチ化", config: { input: "{{trigger.items}}", batch_size: 2 }, position: { x: 500, y: 260 } },
        { id: "rate", type: "control.rate_limit", name: "共有レート制限", config: { scope: "e2e-vendor", max_calls: 1, window_seconds: 0.3, mode: "wait", max_wait_seconds: 2 }, position: { x: 740, y: 260 } },
        { id: "breaker", type: "control.circuit_breaker", name: "実行可否を確認", config: { operation: "check", scope: "e2e-vendor", failure_threshold: 1, recovery_seconds: 2 }, position: { x: 980, y: 260 } },
        { id: "success", type: "control.circuit_breaker", name: "成功を記録", config: { operation: "record_success", scope: "e2e-vendor", failure_threshold: 1, recovery_seconds: 2 }, position: { x: 1220, y: 170 } },
        { id: "allowed", type: "flow.return", name: "許可結果", config: { name: "allowed_result", title: "許可結果", value: "allowed={{breaker.state}}; batches={{batch.batch_count}}; waited={{rate.waited_seconds}}" }, position: { x: 1460, y: 170 } },
        { id: "blocked", type: "flow.return", name: "遮断結果", config: { name: "blocked_result", title: "遮断結果", value: "blocked={{breaker.state}}" }, position: { x: 1220, y: 350 } },
      ],
      edges: [
        { source: "trigger", target: "is_fail" },
        { source: "is_fail", target: "failure", branch: "true" },
        { source: "failure", target: "failed", branch: "allowed" },
        { source: "is_fail", target: "batch", branch: "false" },
        { source: "batch", target: "rate" },
        { source: "rate", target: "breaker" },
        { source: "breaker", target: "success", branch: "allowed" },
        { source: "success", target: "allowed", branch: "allowed" },
        { source: "breaker", target: "blocked", branch: "blocked" },
      ],
    };
    const created = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ name: "E2E Batch Rate Circuit", definition }),
    });
    if (!created.ok) throw new Error(await created.text());
    const id = (await created.json()).id as number;
    const published = await fetch(`/api/v1/workflows/${id}/publish`, { method: "POST", credentials: "same-origin", headers });
    if (!published.ok) throw new Error(await published.text());
    return id;
  });

  try {
    await page.goto(`/workflows/${workflowId}`);
    await page.locator(".react-flow__node").filter({ hasText: "2件ずつバッチ化" }).click();
    await expect(page.locator("#node-config-batch-input")).toHaveValue("{{trigger.items}}");
    await expect(page.locator("#node-config-batch-batch_size")).toHaveValue("2");
    await page.locator(".react-flow__node").filter({ hasText: "共有レート制限" }).dispatchEvent("click");
    await expect(page.locator("#node-config-rate-scope")).toHaveValue("e2e-vendor");
    await expect(page.locator("#node-config-rate-mode")).toHaveValue("wait");
    await page.locator(".react-flow__node").filter({ hasText: "実行可否を確認" }).dispatchEvent("click");
    await expect(page.locator("#node-config-breaker-operation")).toHaveValue("check");
    await expect(page.locator(".react-flow__node").filter({ hasText: "実行可否を確認" })).toContainText("許可");
    await expect(page.locator(".react-flow__node").filter({ hasText: "実行可否を確認" })).toContainText("遮断");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto(`/runner?workflow=${workflowId}`);
    await page.getByLabel("処理項目").fill("[1,2,3,4,5]");
    await page.getByLabel("操作").selectOption("check");
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText(/allowed=CLOSED; batches=3; waited=/)).toBeVisible({ timeout: 10_000 });

    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect.poll(async () => {
      return page.evaluate(async (id) => {
        const rows = await fetch(`/api/v1/workflow-executions?workflow_id=${id}&limit=1`, { credentials: "same-origin" }).then((r) => r.json());
        if (rows[0]?.status !== "SUCCEEDED") return 0;
        const run = await fetch(`/api/v1/workflow-executions/${rows[0].id}`, { credentials: "same-origin" }).then((r) => r.json());
        return Number(run.context.rate?.output?.waited_seconds || 0);
      }, workflowId);
    }).toBeGreaterThan(0.1);

    await page.getByLabel("操作").selectOption("fail");
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("opened=OPEN", { exact: true })).toBeVisible();
    await page.getByLabel("操作").selectOption("check");
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("blocked=OPEN", { exact: true })).toBeVisible({ timeout: 10_000 });
    await page.waitForTimeout(2_100);
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText(/allowed=HALF_OPEN; batches=3; waited=/)).toBeVisible({ timeout: 10_000 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, workflowId);
  }
});
