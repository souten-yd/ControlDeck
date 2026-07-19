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
    const editor = page.getByRole("region", { name: "App Design Editor" });
    await expect(editor.getByText("ページはまだありません")).toBeVisible();
    await editor.getByRole("button", { name: "Add Page" }).click();
    await editor.getByRole("button", { name: /Text display/ }).click();
    await expect(editor.getByRole("button", { name: "display-text-1 display.text" })).toBeVisible();
    const properties = editor.getByLabel("Properties JSON");
    await properties.fill('{"text":"Hello App Studio"}');
    await properties.blur();
    await expect(editor.getByText("Hello App Studio", { exact: true })).toBeVisible();
    await editor.getByRole("button", { name: "Undo" }).click();
    await expect(editor.getByText("Hello App Studio", { exact: true })).toHaveCount(0);
    await editor.getByRole("button", { name: "Redo" }).click();
    await expect(editor.getByText("Hello App Studio", { exact: true })).toBeVisible();
    await editor.getByRole("button", { name: "mobile" }).click();
    await expect(editor.getByTestId("app-responsive-preview")).toHaveClass(/max-w-\[320px\]/);
    await editor.getByRole("button", { name: "Save" }).click();
    await expect(editor.getByRole("button", { name: "Save" })).toBeDisabled();
    await page.reload();
    await expect(page.getByText("Hello App Studio", { exact: true })).toBeVisible();
    const reloadedEditor = page.getByRole("region", { name: "App Design Editor" });
    await reloadedEditor.getByRole("button", { name: "Review Patch" }).click();
    const review = page.getByRole("dialog", { name: "Review Spec Patch" });
    await review.getByLabel("JSON Patch proposal").fill(JSON.stringify([
      { op: "replace", path: "/pages/0/root/children/0/properties/text", value: "Patched App Studio" },
      { op: "add", path: "/pages/0/root/children/-", value: { id: "skipped-card", type: "layout.card", children: [] } },
    ]));
    await review.getByRole("button", { name: "Load proposal" }).click();
    const patchOperations = review.getByRole("region", { name: "Patch operations" });
    await expect(patchOperations.getByRole("checkbox")).toHaveCount(2);
    await patchOperations.getByRole("checkbox").nth(1).uncheck();
    await expect(review.getByText("1 / 2", { exact: true })).toBeVisible();
    await review.getByRole("button", { name: "Preview selected" }).click();
    await expect(review.getByRole("region", { name: "Patch preview" })).toBeVisible();
    await review.getByRole("button", { name: "Apply selected changes" }).click();
    await expect(review).toBeHidden();
    await expect(page.getByText("Patched App Studio", { exact: true })).toBeVisible();
    await expect(page.getByText("skipped-card", { exact: true })).toHaveCount(0);

    await reloadedEditor.getByRole("button", { name: "display-text-1 display.text" }).click();
    const locks = reloadedEditor.getByRole("group", { name: "AI redesign locks" });
    await locks.getByRole("checkbox", { name: "content" }).check();
    await reloadedEditor.getByRole("button", { name: "Save" }).click();
    await expect(reloadedEditor.getByRole("button", { name: "Save" })).toBeDisabled();
    await reloadedEditor.getByRole("button", { name: "Review Patch" }).click();
    const lockedReview = page.getByRole("dialog", { name: "Review Spec Patch" });
    await lockedReview.getByLabel("JSON Patch proposal").fill(JSON.stringify([
      { op: "replace", path: "/pages/0/root/children/0/properties/text", value: "Blocked App Studio" },
    ]));
    await lockedReview.getByRole("button", { name: "Load proposal" }).click();
    await lockedReview.getByRole("button", { name: "Preview selected" }).click();
    await expect(lockedReview.getByText("PATCH_LOCK_VIOLATION", { exact: false })).toBeVisible();
    await expect(lockedReview.getByRole("button", { name: "Apply selected changes" })).toBeDisabled();
    await lockedReview.getByRole("button", { name: "閉じる" }).click();
    await expect(page.getByText("Patched App Studio", { exact: true })).toBeVisible();
    await expect(page.getByText("Blocked App Studio", { exact: true })).toHaveCount(0);
    await page.getByLabel("LLM runtime integration").selectOption("external");
    await expect(page.getByLabel("External LLM provider")).toBeVisible();
    await page.getByLabel("External LLM provider").selectOption("lmstudio");
    await expect(page.getByLabel("LLM runtime integration")).toHaveValue("external");
    await expect(page.getByLabel("External LLM provider")).toHaveValue("lmstudio");
    await reloadedEditor.getByRole("button", { name: "AI Design" }).click();
    const aiDesign = page.getByRole("dialog", { name: "AI Design Proposals" });
    await expect(aiDesign.getByLabel("AI design request")).toBeVisible();
    await expect(aiDesign.getByRole("button", { name: "Generate 3 proposals" })).toBeDisabled();
    await aiDesign.getByRole("button", { name: "閉じる" }).click();
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
