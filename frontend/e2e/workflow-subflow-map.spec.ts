import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("maps typed items through one pinned published subflow at desktop and 320px", async ({ page }) => {
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

  const created = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const create = async (name: string, definition: unknown) => {
      const response = await fetch("/api/v1/workflows", {
        method: "POST", credentials: "same-origin", headers,
        body: JSON.stringify({ name, definition }),
      });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()).id as number;
    };
    const child = await create("E2E Map Child", {
      nodes: [
        { id: "trigger", type: "trigger", name: "Map入力", config: { mode: "manual" }, position: { x: 40, y: 150 } },
        { id: "wait", type: "util.wait", name: "項目別待機", config: { seconds: "{{trigger.item.delay}}" }, position: { x: 310, y: 150 } },
        { id: "result", type: "flow.return", name: "子結果", config: {
          name: "child_result", renderer: "plain_text",
          value: "name={{trigger.item.name}}; index={{trigger.index}}; message={{trigger.message}}",
        }, position: { x: 580, y: 150 } },
      ],
      edges: [{ source: "trigger", target: "wait" }, { source: "wait", target: "result" }],
    });
    const childPublish = await fetch(`/api/v1/workflows/${child}/publish`, {
      method: "POST", credentials: "same-origin", headers,
    });
    if (!childPublish.ok) throw new Error(await childPublish.text());
    const childVersion = (await childPublish.json()).version_id as number;
    const parent = await create("E2E Subflow Map", {
      nodes: [
        { id: "trigger", type: "trigger", name: "配列入力", config: {
          mode: "manual", inputs: [{
            key: "items", label: "処理する項目", type: "json_array", required: true,
            description: "JSON配列を入力", sample: [{ name: "A", delay: 0.1 }],
          }],
        }, position: { x: 40, y: 150 } },
        { id: "map", type: "flow.map", name: "公開SubflowへMap", config: {
          workflow_id: child, items: "{{trigger.items}}", parallel: 3,
          failure_policy: "collect", message: "map-{{map.item.name}}", timeout: 30,
        }, position: { x: 350, y: 150 } },
        { id: "result", type: "flow.return", name: "Map結果", config: {
          name: "mapped", title: "Map結果", renderer: "json", value: "{{map.results}}",
        }, position: { x: 680, y: 150 } },
      ],
      edges: [{ source: "trigger", target: "map" }, { source: "map", target: "result" }],
    });
    const parentPublish = await fetch(`/api/v1/workflows/${parent}/publish`, {
      method: "POST", credentials: "same-origin", headers,
    });
    if (!parentPublish.ok) throw new Error(await parentPublish.text());
    return { child, parent, childVersion };
  });

  try {
    await page.goto(`/workflows/${created.parent}`);
    await page.locator(".react-flow__node").filter({ hasText: "公開SubflowへMap" }).click();
    await expect(page.locator("#node-config-map-workflow_id")).toHaveValue(String(created.child));
    await expect(page.locator("#node-config-map-items")).toHaveValue("{{trigger.items}}");
    await expect(page.locator("#node-config-map-parallel")).toHaveValue("3");
    await expect(page.locator("#node-config-map-failure_policy")).toHaveValue("collect");
    await expect(page.locator("#node-config-map-message")).toHaveValue("map-{{map.item.name}}");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);

    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto(`/runner?workflow=${created.parent}`);
    const items = [
      { name: "slow", delay: 0.15 },
      { name: "fast", delay: 0.01 },
      { name: "middle", delay: 0.06 },
    ];
    await page.getByLabel("処理する項目").fill(JSON.stringify(items));
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("Map結果", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("pre").filter({ hasText: "name=slow; index=0; message=map-slow" })).toBeVisible();
    await expect(page.locator("pre").filter({ hasText: "name=fast; index=1; message=map-fast" })).toBeVisible();

    const execution = await page.evaluate(async (parent) => {
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const list = await fetch(`/api/v1/workflow-executions?workflow_id=${parent}&limit=1`, { credentials: "same-origin" });
        const rows = await list.json();
        if (rows[0]?.status === "SUCCEEDED") {
          return fetch(`/api/v1/workflow-executions/${rows[0].id}`, { credentials: "same-origin" }).then((response) => response.json());
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      throw new Error("subflow map did not finish");
    }, created.parent);
    const mapped = execution.context.map.output;
    expect(mapped.count).toBe(3);
    expect(mapped.succeeded).toBe(3);
    expect(mapped.failed).toBe(0);
    expect(mapped.all_succeeded).toBe(true);
    expect(mapped.target_version_id).toBe(created.childVersion);
    expect(mapped.results.map((item: { index: number }) => item.index)).toEqual([0, 1, 2]);
    expect(mapped.results.map((item: { item: { name: string } }) => item.item.name)).toEqual(["slow", "fast", "middle"]);
    expect(new Set(mapped.results.map((item: { execution_id: number }) => item.execution_id)).size).toBe(3);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async ({ parent, child }) => {
      for (const id of [parent, child]) {
        await fetch(`/api/v1/workflows/${id}`, {
          method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
        });
      }
    }, created);
  }
});
