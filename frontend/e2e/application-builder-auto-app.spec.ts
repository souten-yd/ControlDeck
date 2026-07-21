import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("auto-composes a runnable Workflow app before AI or manual refinement", async ({ page }) => {
  test.setTimeout(120_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const browserErrors: string[] = [];
  page.on("console", (message) => message.type() === "error" && browserErrors.push(message.text()));
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/applications");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  browserErrors.length = 0;

  const ids = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const workflowResponse = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({
        name: "Automatic Contract App",
        description: "Transform a message with typed options",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", config: { mode: "manual", inputs: [
              { key: "message", label: "Message", type: "text", required: true },
              { key: "count", label: "Count", type: "number" },
              { key: "enabled", label: "Enabled", type: "boolean" },
            ] } },
            { id: "output", type: "output.render", config: { name: "answer", renderer: "text", value: "{{trigger.message}}" } },
          ],
          edges: [{ source: "trigger", target: "output" }],
        },
      }),
    });
    if (!workflowResponse.ok) throw new Error(await workflowResponse.text());
    const workflow = await workflowResponse.json() as { id: number };
    const projectResponse = await fetch(`/api/v1/workflows/${workflow.id}/application-projects`, {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ source: "draft", name: "Automatic Contract App" }),
    });
    if (!projectResponse.ok) throw new Error(await projectResponse.text());
    const project = await projectResponse.json() as { id: number };
    return { workflowId: workflow.id, projectId: project.id };
  });

  try {
    await page.goto(`/applications/${ids.projectId}`);
    const advisor = page.getByRole("region", { name: "Workflow App Advisor" });
    await expect(advisor).toContainText("自動構成済み");
    await expect(advisor).toContainText("Message・Count・Enabled");
    const preview = page.getByTestId("app-responsive-preview");
    await expect(preview.getByLabel("Message")).toBeVisible();
    await expect(preview.getByLabel("Count")).toHaveAttribute("type", "number");
    await expect(preview.getByLabel("Enabled")).toBeVisible();
    await expect(preview).toContainText("answer");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);

    await advisor.getByRole("button", { name: "AIに再検討" }).click();
    const proposals = page.getByRole("dialog", { name: "AI Design Proposals" });
    await expect(proposals.getByLabel("AI design request")).toContainText("Workflowの入出力と動作を維持");
    await proposals.getByRole("button", { name: "閉じる" }).click();

    await preview.getByText("Transform a message with typed options").click();
    await page.getByRole("button", { name: "Inspect" }).click();
    const inspector = page.getByRole("dialog", { name: "Inspector" });
    await expect(inspector.getByLabel("Property Markdown")).toHaveValue("Transform a message with typed options");
    await inspector.getByRole("button", { name: "閉じる" }).click();

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.reload();
    await expect(page.getByRole("region", { name: "Workflow App Advisor" })).toBeVisible();
    await expect(page.getByTestId("app-responsive-preview").getByLabel("Message")).toBeVisible();
    await page.getByRole("region", { name: "Workflow App Advisor" }).getByRole("button", { name: "生成・動作確認へ" }).click();
    await expect(page.getByRole("region", { name: "Source Generator" })).toBeVisible();
    expect(browserErrors).toEqual([]);
  } finally {
    await page.evaluate(async ({ projectId, workflowId }) => {
      const headers = { "X-Requested-With": "ControlDeck" };
      await fetch(`/api/v1/application-projects/${projectId}`, { method: "DELETE", credentials: "same-origin", headers });
      await fetch(`/api/v1/workflows/${workflowId}`, { method: "DELETE", credentials: "same-origin", headers });
    }, ids);
  }
});
