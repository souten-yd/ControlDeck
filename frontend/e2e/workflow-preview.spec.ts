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

    await page.setViewportSize({ width: 320, height: 700 });
    await page.getByRole("button", { name: "その他メニュー" }).click();
    await page.getByRole("menuitem", { name: "実行履歴" }).click();
    const history = page.getByRole("dialog", { name: "実行履歴" });
    await expect(history).toBeVisible();
    await history.getByRole("button").filter({ hasText: "SUCCEEDED" }).first().click();
    const execution = page.getByRole("dialog", { name: /実行 #/ });
    await expect(execution.getByRole("button", { name: "現在のフローで再実行" })).toBeVisible();
    await expect(execution.getByRole("button", { name: "当時のフローで再実行" })).toBeVisible();
    await expect(execution.getByText("answer", { exact: true })).toBeVisible();
    const replayLayout = await execution.evaluate((element) => ({
      right: element.getBoundingClientRect().right,
      documentWidth: document.documentElement.scrollWidth,
      viewport: window.innerWidth,
    }));
    expect(replayLayout.right).toBeLessThanOrEqual(replayLayout.viewport);
    expect(replayLayout.documentWidth).toBeLessThanOrEqual(replayLayout.viewport);
    await execution.getByRole("button", { name: "閉じる" }).click();
    await page.getByRole("dialog", { name: "実行履歴" }).getByRole("button", { name: "閉じる" }).click();

    await page.setViewportSize({ width: 390, height: 844 });

    // 直近実行のcacheを使った単体実行、固定データ、部分実行を同じインスペクタで扱える。
    await page.locator(".react-flow__node").filter({ hasText: "回答" }).click();
    let inspector = page.getByRole("dialog", { name: "信号表示" });
    await inspector.getByRole("tab", { name: "実行", exact: true }).click();
    await inspector.getByRole("button", { name: "このノードだけ実行", exact: true }).click();
    await expect(inspector.getByText("✓ 実行可能な設定")).toBeVisible();
    await inspector.getByRole("button", { name: "出力を固定" }).click();
    await expect(inspector.getByRole("button", { name: "📌 固定を解除" })).toBeVisible();
    await expect(page.locator(".react-flow__node").filter({ hasText: "回答" }).getByText("📌 固定")).toBeVisible();
    await inspector.getByRole("button", { name: "このノードまで実行" }).click();
    await expect(page.getByText(/このノードまで実行を開始しました/)).toBeVisible();
    await expect(inspector.getByRole("button", { name: "このノードから再実行" })).toBeEnabled();
    await inspector.getByRole("button", { name: "📌 固定を解除" }).click();
    await inspector.getByRole("button", { name: "閉じる" }).click();

    // 接続線は太い透明hit areaから選択でき、端点の付け替え案内と削除を同じtoolbarへ集約する。
    const flowNodes = page.locator(".react-flow__node");
    const sourceBox = await flowNodes.nth(0).boundingBox();
    const targetBox = await flowNodes.nth(1).boundingBox();
    expect(sourceBox).not.toBeNull();
    expect(targetBox).not.toBeNull();
    await page.mouse.click(
      (sourceBox!.x + sourceBox!.width + targetBox!.x) / 2,
      sourceBox!.y + sourceBox!.height / 2,
    );
    const edgeToolbar = page.getByRole("toolbar", { name: "接続線の操作" });
    await expect(edgeToolbar).toBeVisible();
    await expect(edgeToolbar.getByText("端の丸をドラッグして付け替え")).toBeVisible();
    await expect(page.locator(".react-flow__edgeupdater")).toHaveCount(2);
    await edgeToolbar.getByRole("button", { name: "選択した接続線を削除" }).click();
    await expect(page.locator(".react-flow__edge")).toHaveCount(0);

    await page.locator(".react-flow__node").filter({ hasText: "回答" }).click();
    inspector = page.getByRole("dialog", { name: "信号表示" });
    await expect(inspector).toBeVisible();
    const settingsRect = await inspector.boundingBox();
    expect(settingsRect).not.toBeNull();
    for (const tab of ["設定", "入力", "出力", "実行", "エラー", "詳細"]) {
      await expect(inspector.getByRole("tab", { name: tab, exact: true })).toBeVisible();
    }
    await inspector.getByRole("tab", { name: "出力", exact: true }).click();
    await expect(inspector.getByText("出力 schema")).toBeVisible();
    const outputRect = await inspector.boundingBox();
    expect(outputRect).not.toBeNull();
    expect(Math.abs(outputRect!.y - settingsRect!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(outputRect!.height - settingsRect!.height)).toBeLessThanOrEqual(1);
    await inspector.getByRole("tab", { name: "詳細", exact: true }).click();
    await expect(inspector.getByText("node ID")).toBeVisible();
    const handleHitArea = await page.locator(".workflow-node-handle").first().evaluate((node) => ({
      inset: getComputedStyle(node, "::after").inset,
    }));
    expect(handleHitArea.inset).toBe("-16px");
    await inspector.getByRole("button", { name: "このノードを削除" }).click();
    await expect(inspector).toBeHidden();
    await expect(page.locator(".react-flow__node")).toHaveCount(1);
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
