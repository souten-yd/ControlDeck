import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: import("@playwright/test").Page) {
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
}

test("shows the exact publish blocker instead of a generic 409", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await login(page);
  const workflowId = await page.evaluate(async () => {
    const response = await fetch("/api/v1/workflows", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({
        name: "E2E Publish Blocker",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", config: { mode: "manual" }, position: { x: 80, y: 160 } },
            { id: "wait", type: "util.wait", config: { seconds: 0 }, position: { x: 340, y: 160 } },
          ],
          edges: [{ source: "trigger", target: "wait" }],
        },
      }),
    });
    return (await response.json()).id as number;
  });
  try {
    await page.goto(`/workflows/${workflowId}`);
    await page.getByRole("button", { name: "確認・テストを開く" }).click();
    const preview = page.getByRole("complementary", { name: "確認・テスト" });
    await preview.getByRole("button", { name: "実行せず確認" }).click();
    await expect(preview.getByText("構造上は実行可能です")).toBeVisible();
    await expect(preview.getByText("このままでは公開できません")).toBeVisible();
    await expect(preview.getByText(/output\.render（推奨）/)).toBeVisible();
    await preview.getByRole("button", { name: "プレビューを閉じる" }).click();
    await page.getByRole("button", { name: "その他メニュー" }).click();
    await page.getByRole("menuitem", { name: "実行せず公開" }).click();
    await expect(page.getByText(/公開できません: 正式な最終出力ノードがありません/)).toBeVisible();
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, workflowId);
  }
});

test("integrates trigger input, safe preview, test result, and past input at 320px", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await login(page);
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
    await page.getByRole("button", { name: "確認・テストを開く" }).click();
    const preview = page.getByRole("complementary", { name: "確認・テスト" });
    await expect(preview).toBeVisible();
    await preview.getByLabel("質問 *").fill("ControlDeck preview");
    await preview.getByRole("button", { name: "実行せず確認" }).click();
    await expect(preview.getByText("構造上は実行可能です")).toBeVisible();
    await expect(preview.getByText("公開できます")).toBeVisible();
    await expect(preview.getByText("answer", { exact: true })).toBeVisible();

    await preview.getByRole("radio", { name: /下書きをテスト/ }).click();
    await preview.getByRole("button", { name: "確認してdraftをテスト", exact: true }).click();
    await expect(preview.getByText("公開できます")).toBeVisible();
    await expect(preview.getByText("テストに成功しました")).toBeVisible({ timeout: 10_000 });
    await expect(preview.getByText("回答: ControlDeck preview", { exact: true })).toBeVisible();

    await preview.getByRole("button", { name: "現在値を保存" }).click();
    await preview.getByLabel("テストケース名").fill("E2E 回帰ケース");
    await expect(preview.getByLabel("期待出力 JSON")).toContainText("ControlDeck preview");
    await preview.getByRole("button", { name: "保存", exact: true }).click();
    const regressionCase = preview.locator("article").filter({ hasText: "E2E 回帰ケース" });
    await expect(regressionCase).toBeVisible();
    await preview.getByRole("button", { name: "全1件を一括実行" }).click();
    await expect(regressionCase.getByText("成功", { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(regressionCase.getByText(/assertion 1\/1/)).toBeVisible();
    await regressionCase.getByRole("button", { name: "入力を読込" }).click();
    await expect(preview.getByLabel("質問 *")).toHaveValue("ControlDeck preview");

    const mobileLayout = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      previewRight: document.querySelector<HTMLElement>('[aria-label="確認・テスト"]')?.getBoundingClientRect().right,
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
        previewRight: document.querySelector<HTMLElement>('[aria-label="確認・テスト"]')?.getBoundingClientRect().right,
      }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport);
      expect(layout.body).toBeLessThanOrEqual(layout.viewport);
      expect(layout.previewRight).toBeLessThanOrEqual(layout.viewport);
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await preview.getByRole("button", { name: "プレビューを閉じる" }).click();

    await page.setViewportSize({ width: 320, height: 700 });
    await page.getByRole("button", { name: "検証して実行" }).click();
    const runInputs = page.getByRole("dialog", { name: "実行時の入力" });
    await runInputs.getByLabel("質問 *").fill("ControlDeck published run");
    await runInputs.getByRole("button", { name: "実行", exact: true }).click();
    const debugPanel = page.locator('[aria-label="実行デバッグパネル"]');
    await expect(debugPanel).toBeVisible();
    await expect.poll(async () => page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/workflows/${id}`, { credentials: "same-origin" });
      const workflow = await response.json() as { state: string; published_version: number | null };
      return workflow.state === "published" && workflow.published_version !== null;
    }, workflowId)).toBe(true);
    await debugPanel.getByRole("button", { name: "閉じる" }).click();
    await page.getByRole("button", { name: "その他メニュー" }).click();
    await page.getByRole("menuitem", { name: "実行履歴" }).click();
    const history = page.getByRole("dialog", { name: "実行履歴" });
    await expect(history).toBeVisible();
    await history.getByRole("button").filter({ hasText: "SUCCEEDED" }).first().click();
    const execution = page.getByRole("dialog", { name: /実行 #/ });
    await expect(execution.getByRole("button", { name: "現在のフローで再実行" })).toBeVisible();
    await expect(execution.getByRole("button", { name: "当時のフローで再実行" })).toBeVisible();
    const nodeObservation = execution.getByLabel("ノード実行の観測");
    await expect(nodeObservation.getByText("実行タイムライン")).toBeVisible();
    await nodeObservation.getByRole("button", { name: "answer SUCCEEDED" }).click();
    const answerDetail = nodeObservation.getByLabel("ノード answer の詳細");
    await answerDetail.getByText("実入力", { exact: true }).click();
    await expect(answerDetail.getByText(/upstream/)).toBeVisible();
    await answerDetail.getByText("実出力", { exact: true }).click();
    await expect(answerDetail.getByText(/"signal": "answer"/).last()).toBeVisible();
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
    await inspector.getByRole("tab", { name: "エラー", exact: true }).click();
    await inspector.getByRole("button", { name: /実行制御/ }).click();
    await inspector.getByText("ノードのtimeout（秒）").locator("..").locator('input[type="number"]').fill("2.5");
    await inspector.getByText("失敗したとき", { exact: true }).locator("..").locator("select").selectOption("branch");
    await expect(page.locator('.react-flow__handle[data-handleid="error"]')).toHaveCount(1);
    await expect(page.locator('.react-flow__handle[data-handleid="timeout"]')).toHaveCount(1);
    await expect(page.locator(".react-flow__node").filter({ hasText: "回答" }).getByText("時間切れ")).toBeVisible();
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
