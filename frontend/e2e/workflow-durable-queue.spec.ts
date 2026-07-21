import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

function definition(operation: "enqueue" | "dequeue") {
  return {
    nodes: [
      { id: "t", type: "trigger", name: "開始", config: { mode: "manual" }, position: { x: 40, y: 160 } },
      { id: "q", type: "data.queue", name: "永続キュー", config: {
        operation, queue: "jobs", ...(operation === "enqueue" ? { value: { label: "mobile" } } : {}),
      }, position: { x: 310, y: 160 } },
      { id: "out", type: "flow.return", name: "結果", config: {
        name: "result", renderer: "plain_text",
        value: operation === "enqueue" ? "queue-size={{q.size}}" : "queue-value={{q.value.label}}/remaining={{q.size}}",
      }, position: { x: 580, y: 160 } },
    ],
    edges: [{ source: "t", target: "q" }, { source: "q", target: "out" }],
  };
}

test("durable queue survives published runs and remains usable on mobile", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  const workflowId = await page.evaluate(async (draft) => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const created = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ name: "E2E Durable Queue", definition: draft }),
    });
    if (!created.ok) throw new Error(await created.text());
    const id = (await created.json()).id as number;
    const published = await fetch(`/api/v1/workflows/${id}/publish`, { method: "POST", credentials: "same-origin", headers });
    if (!published.ok) throw new Error(await published.text());
    return id;
  }, definition("enqueue"));

  try {
    await page.goto(`/runner?workflow=${workflowId}`);
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("queue-size=1", { exact: true })).toBeVisible();

    await page.evaluate(async ({ id, draft }) => {
      const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
      const updated = await fetch(`/api/v1/workflows/${id}`, {
        method: "PATCH", credentials: "same-origin", headers, body: JSON.stringify({ definition: draft }),
      });
      if (!updated.ok) throw new Error(await updated.text());
      const published = await fetch(`/api/v1/workflows/${id}/publish`, { method: "POST", credentials: "same-origin", headers });
      if (!published.ok) throw new Error(await published.text());
    }, { id: workflowId, draft: definition("dequeue") });
    await page.reload();
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("queue-value=mobile/remaining=0", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`/workflows/${workflowId}`);
    await page.locator(".react-flow__node").filter({ hasText: "永続キュー" }).click();
    await expect(page.locator("#node-config-q-operation")).toHaveValue("dequeue");
    await expect(page.locator("#node-config-q-queue")).toHaveValue("jobs");
    await page.setViewportSize({ width: 320, height: 720 });
    await expect(page.locator("#node-config-q-operation")).toBeVisible();
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
