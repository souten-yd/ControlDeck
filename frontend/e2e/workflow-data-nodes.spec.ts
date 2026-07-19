import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("deterministic data nodes execute and appear in the mobile library", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  const created = await page.evaluate(async () => {
    const definition = {
      nodes: [
        { id: "t", type: "trigger", name: "入力", config: { mode: "manual" }, position: { x: 0, y: 0 } },
        { id: "filter", type: "data.filter", name: "上位だけ", config: {
          input: "{{t.rows}}", field: "score", operator: "gte", value: 7,
          unique_by: "id", sort_by: "score", sort_order: "desc", limit: 2,
        }, position: { x: 240, y: 0 } },
        { id: "aggregate", type: "data.aggregate", name: "合計", config: {
          input: "{{filter.items}}", operation: "sum", field: "amount",
        }, position: { x: 480, y: 0 } },
        { id: "format", type: "data.template", name: "整形", config: {
          data: '{"kept":{{filter.count}},"sum":{{aggregate.result}}}',
          template: '{"summary":"kept={{data.kept}}, sum={{data.sum}}"}', output_format: "json",
        }, position: { x: 720, y: 0 } },
        { id: "out", type: "output.render", name: "出力", config: {
          name: "summary", renderer: "Markdown", value: "{{format.value.summary}}",
        }, position: { x: 960, y: 0 } },
      ],
      edges: [
        { source: "t", target: "filter" }, { source: "filter", target: "aggregate" },
        { source: "aggregate", target: "format" }, { source: "format", target: "out" },
      ],
    };
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const response = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ name: "E2E deterministic data", definition }),
    });
    if (!response.ok) throw new Error(await response.text());
    const workflow = await response.json();
    const started = await fetch(`/api/v1/workflows/${workflow.id}/test`, {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ input: { rows: [
        { id: "a", score: 7, amount: 10 }, { id: "b", score: 9, amount: 20 },
        { id: "b", score: 8, amount: 30 }, { id: "c", score: 4, amount: 100 },
      ] } }),
    });
    if (!started.ok) throw new Error(await started.text());
    return { workflowId: workflow.id as number, executionId: (await started.json()).execution_id as number };
  });

  try {
    await expect.poll(async () => page.evaluate(async (executionId) => {
      const response = await fetch(`/api/v1/workflow-executions/${executionId}`, { credentials: "same-origin" });
      return (await response.json()).status as string;
    }, created.executionId)).toBe("SUCCEEDED");
    const detail = await page.evaluate(async (executionId) => {
      const response = await fetch(`/api/v1/workflow-executions/${executionId}`, { credentials: "same-origin" });
      return response.json();
    }, created.executionId);
    expect(detail.outputs.summary.value).toBe("kept=2, sum=30.0");
    expect(detail.context.filter.output.original_count).toBe(4);

    await page.goto(`/workflows/${created.workflowId}`);
    await page.getByRole("button", { name: "ノードを追加" }).click();
    const library = page.getByRole("dialog", { name: "ノードを追加" });
    const search = library.getByLabel("ノードを検索");
    await search.fill("data.template");
    await expect(library.getByText("テンプレート整形", { exact: true })).toBeVisible();
    await search.fill("data.filter");
    await expect(library.getByText("配列フィルター", { exact: true })).toBeVisible();
    await search.fill("data.aggregate");
    await expect(library.getByText("配列集計", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  } finally {
    await page.evaluate(async (workflowId) => {
      await fetch(`/api/v1/workflows/${workflowId}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, created.workflowId);
  }
});
