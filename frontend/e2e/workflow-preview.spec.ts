import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("integrates trigger input, safe preview, test result, and past input at 320px", async ({ page }) => {
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
    const response = await fetch("/api/v1/workflows", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({
        name: "E2E Preview Workspace",
        definition: {
          nodes: [
            {
              id: "trigger",
              type: "trigger",
              name: "入力",
              config: {
                mode: "manual",
                inputs: [{ key: "question", label: "質問", type: "paragraph", required: true, placeholder: "質問を入力" }],
              },
              position: { x: 80, y: 160 },
            },
            {
              id: "answer",
              type: "signal.display",
              name: "回答",
              config: { signal: "answer", value: "回答: {{trigger.question}}" },
              position: { x: 340, y: 160 },
            },
          ],
          edges: [{ id: "e1", source: "trigger", target: "answer" }],
        },
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).id as number;
  });

  try {
    await page.goto(`/workflows/${workflowId}`);
    await page.getByRole("button", { name: "実行プレビューを開く" }).click();
    const preview = page.getByRole("complementary", { name: "実行プレビュー" });
    await expect(preview).toBeVisible();
    await preview.getByLabel("質問 *").fill("ControlDeck preview");
    await preview.getByRole("button", { name: "安全プレビューを実行" }).click();
    await expect(preview.getByText("実行可能な定義です")).toBeVisible();
    await expect(preview.getByText("answer", { exact: true })).toBeVisible();

    await preview.getByRole("radio", { name: /通常テスト実行/ }).click();
    await preview.getByRole("button", { name: "テスト実行", exact: true }).click();
    await expect(preview.getByText("テストに成功しました")).toBeVisible({ timeout: 10_000 });
    await expect(preview.getByText("回答: ControlDeck preview", { exact: true })).toBeVisible();

    const mobileLayout = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      previewRight: document.querySelector<HTMLElement>('[aria-label="実行プレビュー"]')?.getBoundingClientRect().right,
    }));
    expect(mobileLayout.document).toBeLessThanOrEqual(mobileLayout.viewport);
    expect(mobileLayout.body).toBeLessThanOrEqual(mobileLayout.viewport);
    expect(mobileLayout.previewRight).toBeLessThanOrEqual(mobileLayout.viewport);

    await preview.getByLabel("過去実行の入力を読み込む").selectOption({ index: 1 });
    await expect(preview.getByLabel("質問 *")).toHaveValue("ControlDeck preview");

    for (const viewport of [
      { width: 390, height: 844 },
      { width: 768, height: 1024 },
      { width: 1280, height: 800 },
    ]) {
      await page.setViewportSize(viewport);
      const layout = await page.evaluate(() => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
        previewRight: document.querySelector<HTMLElement>('[aria-label="実行プレビュー"]')?.getBoundingClientRect().right,
      }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport);
      expect(layout.body).toBeLessThanOrEqual(layout.viewport);
      expect(layout.previewRight).toBeLessThanOrEqual(layout.viewport);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await preview.getByRole("button", { name: "プレビューを閉じる" }).click();
    await page.locator(".react-flow__node").filter({ hasText: "回答" }).click();
    const inspector = page.getByRole("dialog", { name: "信号表示" });
    await expect(inspector).toBeVisible();
    for (const tab of ["設定", "入力", "出力", "実行", "エラー", "詳細"]) {
      await expect(inspector.getByRole("tab", { name: tab, exact: true })).toBeVisible();
    }
    await inspector.getByRole("tab", { name: "出力", exact: true }).click();
    await expect(inspector.getByText("出力 schema")).toBeVisible();
    await inspector.getByRole("tab", { name: "詳細", exact: true }).click();
    await expect(inspector.getByText("node ID")).toBeVisible();
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, {
        method: "DELETE",
        credentials: "same-origin",
        headers: { "X-Requested-With": "ControlDeck" },
      });
    }, workflowId);
  }
});
