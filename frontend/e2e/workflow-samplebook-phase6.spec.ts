import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("previews a runnable differentiator sample and exposes complete node docs", async ({ page }) => {
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
  await page.goto("/workflows");

  const contracts = await page.evaluate(async () => {
    const [samplesResponse, nodesResponse] = await Promise.all([
      fetch("/api/v1/workflows/samples", { credentials: "same-origin" }),
      fetch("/api/v1/workflows/node-catalog", { credentials: "same-origin" }),
    ]);
    return { samples: await samplesResponse.json(), nodes: await nodesResponse.json() };
  });
  expect(contracts.samples.length).toBeGreaterThanOrEqual(19);
  expect(contracts.nodes.length).toBeGreaterThanOrEqual(62);
  for (const node of contracts.nodes) {
    expect(node.documentation.recipes.length).toBeGreaterThanOrEqual(2);
    expect(node.documentation.representative_errors.length).toBeGreaterThan(0);
  }

  await page.getByTitle("サンプルワークフロー集とノードリファレンス").click();
  const book = page.getByRole("dialog", { name: "サンプルブック" });
  await expect(book).toBeVisible();
  await book.getByRole("button", { name: "Workflow IDE" }).evaluate((button: HTMLButtonElement) => button.click());
  await book.getByRole("button", { name: /Regression Batch/ }).click();
  await expect(book.getByLabel("インストール前プレビュー")).toContainText("4 nodes");
  await book.getByText("型・入力例・期待結果").click();
  await expect(book.getByText("Typed input", { exact: true })).toBeVisible();
  await expect(book.getByText("Expected assertions", { exact: true })).toBeVisible();
  await book.getByText("Failure injection／Recovery").click();
  await expect(book.getByText(/同じtest batchを再実行/)).toBeVisible();
  const mobileLayout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  expect(mobileLayout.document).toBeLessThanOrEqual(mobileLayout.viewport);

  await book.getByRole("button", { name: "このサンプルをコピーして使う" }).click();
  await expect(page).toHaveURL(/\/workflows\/\d+$/);
  const workflowId = Number(new URL(page.url()).pathname.split("/").pop());

  try {
    const execution = await page.evaluate(async (id) => {
      const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
      const publish = await fetch(`/api/v1/workflows/${id}/publish`, {
        method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
      if (!publish.ok) throw new Error(await publish.text());
      const run = await fetch(`/api/v1/workflows/${id}/run`, {
        method: "POST", credentials: "same-origin", headers,
        body: JSON.stringify({ input: { items: [1, 2, 3, 4, 5] } }),
      });
      if (!run.ok) throw new Error(await run.text());
      const executionId = (await run.json()).execution_id;
      for (let attempt = 0; attempt < 100; attempt += 1) {
        const response = await fetch(`/api/v1/workflow-executions/${executionId}`, { credentials: "same-origin" });
        const detail = await response.json();
        if (!["QUEUED", "RUNNING", "WAITING"].includes(detail.status)) return detail;
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
      throw new Error("sample execution did not finish");
    }, workflowId);
    expect(execution.status).toBe("SUCCEEDED");
    expect(execution.outputs.batches.value).toEqual([[1, 2], [3, 4], [5]]);

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/workflows");
    await page.getByTitle("サンプルワークフロー集とノードリファレンス").click();
    const desktopBook = page.getByRole("dialog", { name: "サンプルブック" });
    await desktopBook.getByRole("button", { name: "ノードリファレンス" }).click();
    await desktopBook.getByRole("button", { name: /配列バッチ化/ }).click();
    for (const heading of ["使う場面", "使わない場面", "Secret", "Retry／Timeout／Error route", "代表Error", "性能／Cost", "Recipes"]) {
      await expect(desktopBook.getByText(heading, { exact: true })).toBeVisible();
    }
    await expect(desktopBook.getByText(/Migration:/)).toBeVisible();
    const desktopLayout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    expect(desktopLayout.document).toBeLessThanOrEqual(desktopLayout.viewport);
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, workflowId);
  }
});
