import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("runs a published workflow without exposing its canvas at mobile and desktop widths", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/runner");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  await page.goto("/");
  await page.getByRole("button", { name: "操作メニュー" }).click();
  await page.getByRole("button", { name: "ランナー", exact: true }).click();
  await expect(page).toHaveURL(/\/runner$/);

  const workflowId = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const created = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({
        name: "E2E 公開ランナー", description: "キャンバスを見せずに公開処理を利用します",
        definition: {
          nodes: [
            { id: "internal-trigger", type: "trigger", name: "内部トリガー", config: { mode: "manual", inputs: [
              { key: "question", label: "質問", type: "paragraph", required: true, placeholder: "質問を入力", sample: "ControlDeckとは？" },
            ] } },
            { id: "internal-output-node", type: "output.render", name: "内部出力ノード", config: {
              name: "answer", title: "回答", description: "公開された最終結果", renderer: "markdown", value: "公開回答: {{internal-trigger.question}}",
            } },
          ],
          edges: [{ source: "internal-trigger", target: "internal-output-node" }],
        },
      }),
    });
    if (!created.ok) throw new Error(await created.text());
    const id = (await created.json()).id as number;
    const published = await fetch(`/api/v1/workflows/${id}/publish`, {
      method: "POST", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
    });
    if (!published.ok) throw new Error(await published.text());
    return id;
  });

  try {
    await page.goto("/runner");
    await page.getByRole("button", { name: /E2E 公開ランナー/ }).last().click();
    await expect(page.getByRole("heading", { name: "E2E 公開ランナー" })).toBeVisible();
    await expect(page.locator(".react-flow")).toHaveCount(0);
    await expect(page.getByText("内部トリガー")).toHaveCount(0);
    await expect(page.getByText("内部出力ノード")).toHaveCount(0);
    await page.getByLabel("質問 *").fill("iPhone runner");
    await page.getByRole("button", { name: "公開版を実行" }).click();
    await expect(page.getByText("公開回答: iPhone runner", { exact: true })).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText("成功", { exact: true }).first()).toBeVisible();

    const recent = page.getByText(/#\d+ · 成功/).first().locator("../..");
    await recent.getByRole("button", { name: "入力を再利用" }).click();
    await expect(page.getByLabel("質問 *")).toHaveValue("iPhone runner");

    for (const viewport of [
      { width: 320, height: 700 }, { width: 390, height: 844 },
      { width: 768, height: 1024 }, { width: 1280, height: 800 },
    ]) {
      await page.setViewportSize(viewport);
      const layout = await page.evaluate(() => ({
        viewport: window.innerWidth,
        document: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
      }));
      expect(layout.document).toBeLessThanOrEqual(layout.viewport);
      expect(layout.body).toBeLessThanOrEqual(layout.viewport);
    }
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, workflowId);
  }
});
