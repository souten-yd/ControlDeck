import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("creates and validates a Phase A project from Workflow without fake build UI", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  errors.length = 0;

  const workflowId = await page.evaluate(async () => {
    const response = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({
        name: "E2E Application Source",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", name: "入力", config: { mode: "manual", inputs: [{ key: "message", label: "メッセージ", type: "text", required: true }] }, position: { x: 80, y: 160 } },
            { id: "output", type: "output.render", name: "出力", config: { name: "answer", renderer: "text", value: "{{trigger.message}}", schema: { type: "string" } }, position: { x: 340, y: 160 } },
          ],
          edges: [{ source: "trigger", target: "output" }],
        },
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).id as number;
  });
  let projectId: number | null = null;
  try {
    await page.goto(`/workflows/${workflowId}`);
    await page.getByRole("button", { name: "More" }).click();
    await page.getByRole("menuitem", { name: "Open in App Studio" }).click();
    await expect(page.getByRole("heading", { name: "Create in App Studio" })).toBeVisible();
    await page.getByRole("button", { name: "現在のDraftから作成" }).click();
    await expect(page.getByRole("heading", { name: "E2E Application Source App" })).toBeVisible();
    projectId = Number(page.url().split("/").pop());
    await expect(page.getByText("Workflow IR")).toBeVisible();
    await expect(page.getByText("ページはまだありません")).toBeVisible();
    await expect(page.getByText("Source生成: 未実装")).toBeVisible();
    await expect(page.getByText("Build: 未実装")).toBeVisible();
    await expect(page.getByRole("button", { name: /ビルド|生成|公開/ })).toHaveCount(0);
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
    expect(errors).toEqual([]);
  } finally {
    await page.evaluate(async ({ projectId, workflowId }) => {
      const headers = { "X-Requested-With": "ControlDeck" };
      if (projectId) await fetch(`/api/v1/application-projects/${projectId}`, { method: "DELETE", credentials: "same-origin", headers });
      await fetch(`/api/v1/workflows/${workflowId}`, { method: "DELETE", credentials: "same-origin", headers });
    }, { projectId, workflowId });
  }
});
