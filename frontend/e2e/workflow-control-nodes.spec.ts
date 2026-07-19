import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("merges branches and resumes a typed human approval at mobile width", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  const workflowId = await page.evaluate(async (approvalUser) => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const response = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({
        name: "E2E Approval Merge",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", name: "開始", config: { mode: "manual" }, position: { x: 40, y: 180 } },
            { id: "left", type: "util.now", name: "左結果", config: {}, position: { x: 260, y: 80 } },
            { id: "right", type: "util.now", name: "右結果", config: {}, position: { x: 260, y: 280 } },
            { id: "merge", type: "control.merge", name: "結果を合流", config: { mode: "wait_all" }, position: { x: 500, y: 180 } },
            { id: "approval", type: "human.approval", name: "公開承認", config: {
              message: "{{merge.count}}件の結果を承認しますか？", approver: approvalUser, approval_timeout_seconds: 30,
            }, position: { x: 740, y: 180 } },
            { id: "output", type: "output.render", name: "完了", config: {
              name: "result", renderer: "status", value: "approved={{approval.approved}}, count={{merge.count}}",
            }, position: { x: 980, y: 180 } },
          ],
          edges: [
            { source: "trigger", target: "left" }, { source: "trigger", target: "right" },
            { source: "left", target: "merge" }, { source: "right", target: "merge" },
            { source: "merge", target: "approval" }, { source: "approval", target: "output" },
          ],
        },
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    const id = (await response.json()).id as number;
    const publish = await fetch(`/api/v1/workflows/${id}/publish`, {
      method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
    });
    if (!publish.ok) throw new Error(await publish.text());
    return id;
  }, username!);

  try {
    await page.goto(`/workflows/${workflowId}`);
    await expect(page.locator(".react-flow__node").filter({ hasText: "結果を合流" })).toBeVisible();
    await expect(page.locator(".react-flow__node").filter({ hasText: "公開承認" }).getByText("✋").first()).toBeVisible();
    await page.getByRole("button", { name: "実行", exact: true }).click();
    const debug = page.getByLabel("実行デバッグパネル");
    await expect(debug).toBeVisible();
    await expect(debug.getByText("2件の結果を承認しますか？")).toBeVisible({ timeout: 10_000 });
    await expect(debug.getByText(`承認者: ${username}`)).toBeVisible();
    await debug.getByRole("button", { name: "承認して続行" }).click();
    await expect(page.getByText("承認しました", { exact: true })).toBeVisible();
    await expect(debug.getByText("成功", { exact: true }).first()).toBeVisible({ timeout: 10_000 });

    const execution = await page.evaluate(async (id) => {
      for (let attempt = 0; attempt < 50; attempt += 1) {
        const list = await fetch(`/api/v1/workflow-executions?workflow_id=${id}&limit=1`, { credentials: "same-origin" });
        const rows = await list.json();
        if (rows[0]?.status === "SUCCEEDED") {
          const detail = await fetch(`/api/v1/workflow-executions/${rows[0].id}`, { credentials: "same-origin" });
          return detail.json();
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      throw new Error("execution did not finish");
    }, workflowId);
    expect(execution.context.merge.output.count).toBe(2);
    expect(execution.context.approval.output.approved).toBe(true);
    expect(execution.context.output.output.value).toContain("count=2");

    const layout = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      panelRight: document.querySelector<HTMLElement>('[aria-label="実行デバッグパネル"]')!.getBoundingClientRect().right,
    }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    expect(layout.panelRight).toBeLessThanOrEqual(layout.viewport);
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, workflowId);
  }
});
