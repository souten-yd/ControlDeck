import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

function definition(operation: "set" | "get") {
  return {
    nodes: [
      { id: "t", type: "trigger", name: "開始", config: { mode: "manual" }, position: { x: 40, y: 160 } },
      { id: "cache", type: "data.cache", name: "期限付きキャッシュ", config: {
        operation, namespace: "api", key: "latest",
        ...(operation === "set" ? { value: { label: "mobile" }, ttl_seconds: 3600 } : {}),
      }, position: { x: 310, y: 160 } },
      { id: "out", type: "flow.return", name: "結果", config: {
        name: "result", renderer: "plain_text",
        value: operation === "set"
          ? "cache-stored={{cache.stored}}/size={{cache.size}}"
          : "cache-value={{cache.value.label}}/found={{cache.found}}/size={{cache.size}}",
      }, position: { x: 580, y: 160 } },
    ],
    edges: [{ source: "t", target: "cache" }, { source: "cache", target: "out" }],
  };
}

test("durable cache is shared by published runs and configurable on mobile", async ({ page }) => {
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
      body: JSON.stringify({ name: "E2E Durable Cache", definition: draft }),
    });
    if (!created.ok) throw new Error(await created.text());
    const id = (await created.json()).id as number;
    const published = await fetch(`/api/v1/workflows/${id}/publish`, {
      method: "POST", credentials: "same-origin", headers,
    });
    if (!published.ok) throw new Error(await published.text());
    return id;
  }, definition("set"));

  try {
    await page.goto(`/runner?workflow=${workflowId}`);
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("cache-stored=true/size=1", { exact: true })).toBeVisible();

    await page.evaluate(async ({ id, draft }) => {
      const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
      const updated = await fetch(`/api/v1/workflows/${id}`, {
        method: "PATCH", credentials: "same-origin", headers, body: JSON.stringify({ definition: draft }),
      });
      if (!updated.ok) throw new Error(await updated.text());
      const published = await fetch(`/api/v1/workflows/${id}/publish`, {
        method: "POST", credentials: "same-origin", headers,
      });
      if (!published.ok) throw new Error(await published.text());
    }, { id: workflowId, draft: definition("get") });
    await page.reload();
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("cache-value=mobile/found=true/size=1", { exact: true })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(`/workflows/${workflowId}`);
    await page.locator(".react-flow__node").filter({ hasText: "期限付きキャッシュ" }).click();
    await expect(page.locator("#node-config-cache-operation")).toHaveValue("get");
    await expect(page.locator("#node-config-cache-namespace")).toHaveValue("api");
    await expect(page.locator("#node-config-cache-key")).toHaveValue("latest");
    await page.setViewportSize({ width: 320, height: 720 });
    await expect(page.locator("#node-config-cache-operation")).toBeVisible();
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
