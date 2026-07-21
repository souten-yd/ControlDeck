import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("uses recommended settings and inserts a typed upstream variable at the cursor on mobile", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const runtimeErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && runtimeErrors.push(message.text()));
  page.on("pageerror", (error) => runtimeErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  runtimeErrors.length = 0;

  const workflowId = await page.evaluate(async () => {
    const response = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({
        name: "E2E Guided Config",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", name: "調査入力", config: { mode: "manual", inputs: [{ key: "topic", label: "調査テーマ", type: "text", sample: "local LLM" }] }, position: { x: 40, y: 160 } },
            { id: "research", type: "research.deep", name: "詳細調査", config: { topic: "prefix suffix" }, position: { x: 310, y: 160 } },
            { id: "output", type: "output.render", name: "結果", config: { name: "report", renderer: "markdown", value: "{{research.report}}" }, position: { x: 580, y: 160 } },
          ],
          edges: [{ source: "trigger", target: "research" }, { source: "research", target: "output" }],
        },
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    return (await response.json()).id as number;
  });

  try {
    await page.goto(`/workflows/${workflowId}`);
    await page.locator(".react-flow__node").filter({ hasText: "詳細調査" }).click();
    await expect(page.getByText("迷ったら推奨設定で開始")).toBeVisible();
    const recommendationSurface = page.locator("[data-recommended-settings]");
    const surfaceStyle = await recommendationSurface.evaluate((element) => {
      const style = getComputedStyle(element);
      return { background: style.backgroundColor, border: style.borderTopColor };
    });
    expect(surfaceStyle.background).not.toBe("rgba(0, 0, 0, 0)");
    expect(surfaceStyle.background).not.toBe("rgb(255, 255, 255)");
    expect(surfaceStyle.border).not.toBe(surfaceStyle.background);
    const recommendationButton = page.getByRole("button", { name: "推奨値を適用" });
    const buttonStyle = await recommendationButton.evaluate((element) => {
      const style = getComputedStyle(element);
      return { background: style.backgroundColor, color: style.color };
    });
    expect(buttonStyle.background).not.toBe(buttonStyle.color);
    expect(buttonStyle.color).toBe("rgb(255, 255, 255)");
    await recommendationButton.click();
    await expect(page.getByText("推奨:", { exact: false }).first()).toBeVisible();
    await page.getByText("このノードの使い方・推奨理由・構成例").click();
    await expect(page.getByText("最短手順:", { exact: false })).toBeVisible();

    const topic = page.locator("#node-config-research-topic");
    await expect(topic).toHaveValue("prefix suffix");
    await topic.evaluate((element) => {
      const input = element as HTMLInputElement;
      input.focus();
      input.setSelectionRange(7, 7);
    });
    await topic.locator("xpath=..").getByRole("button", { name: /変数を挿入/ }).click();
    await page.getByLabel("上流変数を検索").fill("topic");
    await expect(page.getByText("直前ノード")).toBeVisible();
    await expect(page.getByText("text", { exact: true }).first()).toBeVisible();
    await page.getByTitle("{{trigger.topic}}").click();
    await expect(topic).toHaveValue("prefix {{trigger.topic}}suffix");

    await page.setViewportSize({ width: 320, height: 700 });
    const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    expect(runtimeErrors).toEqual([]);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/workflows/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, workflowId);
  }
});
