import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("creates and validates a Phase A project from Workflow without fake build UI", async ({ page }) => {
  test.setTimeout(120_000);
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
            { id: "condition", type: "condition.if", name: "分岐", config: { left: "{{trigger.message}}", op: "eq", right: "yes" }, position: { x: 260, y: 160 } },
            { id: "accepted", type: "var.set", name: "承認", config: { name: "choice", value: "accepted", retry_count: 1, retry_wait: 0, node_timeout: 30, on_error: "continue" }, position: { x: 440, y: 100 } },
            { id: "rejected", type: "var.set", name: "拒否", config: { name: "choice", value: "rejected" }, position: { x: 440, y: 220 } },
            { id: "merge", type: "control.merge", name: "合流", config: { mode: "wait_all", output_var: "merged" }, position: { x: 620, y: 160 } },
            { id: "template", type: "data.template", name: "整形", config: { data: "{{vars.merged}}", template: "{{data.value}}", output_format: "text" }, position: { x: 800, y: 160 } },
            { id: "loop", type: "control.loop", name: "反復", config: { mode: "count", count: 2, parallel: 2, output_var: "repeated" }, position: { x: 980, y: 160 } },
            { id: "loop-body", type: "var.set", name: "反復値", config: { name: "iteration", value: "{{loop.index}}" }, position: { x: 1160, y: 80 } },
            { id: "output", type: "output.render", name: "出力", config: { name: "answer", renderer: "text", value: "{{template.text}} / {{vars.repeated.total}}", schema: { type: "string" } }, position: { x: 1160, y: 220 } },
          ],
          edges: [
            { source: "trigger", target: "condition" },
            { source: "condition", sourceHandle: "true", target: "accepted" },
            { source: "condition", sourceHandle: "false", target: "rejected" },
            { source: "accepted", target: "merge" }, { source: "rejected", target: "merge" },
            { source: "merge", target: "template" }, { source: "template", target: "loop" },
            { source: "loop", sourceHandle: "body", target: "loop-body" },
            { source: "loop", sourceHandle: "done", target: "output" },
          ],
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
    const workspace = page.getByRole("navigation", { name: "Application workspace" });
    await workspace.getByRole("button", { name: /Review/ }).click();
    await expect(page.getByText("Workflow IR")).toBeVisible();
    await workspace.getByRole("button", { name: /Create/ }).click();
    await page.setViewportSize({ width: 1280, height: 800 });
    const editor = page.getByRole("region", { name: "App Design Editor" });
    await expect(editor.getByText("ページはまだありません")).toBeVisible();
    await editor.getByRole("button", { name: "Add Page" }).click();
    await editor.locator("summary").filter({ hasText: "Layers" }).click();
    await editor.getByRole("button", { name: /Text display/ }).click();
    await expect(editor.getByRole("button", { name: "display-text-1 display.text" })).toBeVisible();
    const textProperty = editor.getByLabel("Property Text");
    await textProperty.fill("Hello App Studio");
    await textProperty.blur();
    await expect(editor.getByText("Hello App Studio", { exact: true })).toBeVisible();
    await editor.getByRole("button", { name: "Undo" }).click();
    await expect(editor.getByText("Hello App Studio", { exact: true })).toHaveCount(0);
    await editor.getByRole("button", { name: "Redo" }).click();
    await expect(editor.getByText("Hello App Studio", { exact: true })).toBeVisible();
    await editor.getByLabel("Design preset").selectOption("terminal");
    await expect.poll(() => editor.getByTestId("app-responsive-preview").evaluate((element) => getComputedStyle(element).fontFamily)).toContain("monospace");
    await editor.getByText("Ready-made blocks", { exact: true }).click();
    await editor.getByRole("button", { name: "KPI Card", exact: true }).click();
    const kpiTemplate = page.getByRole("dialog", { name: "Configure KPI Card" });
    await kpiTemplate.getByLabel("Template parameter Metric label").fill("");
    await expect(kpiTemplate.getByRole("button", { name: "Insert template" })).toBeDisabled();
    await kpiTemplate.getByLabel("Template parameter Metric label").fill("CPU Load");
    await kpiTemplate.getByLabel("Template parameter Initial value").fill("42");
    await kpiTemplate.getByRole("button", { name: "Insert template" }).click();
    await expect(editor.getByText("CPU Load", { exact: true })).toBeVisible();
    await expect(editor.getByText("42", { exact: true })).toBeVisible();
    await editor.getByText("Page patterns", { exact: true }).click();
    await editor.getByRole("button", { name: "Dashboard", exact: true }).click();
    const dashboardTemplate = page.getByRole("dialog", { name: "Configure Dashboard" });
    await dashboardTemplate.getByLabel("Template parameter Title").fill("Operations Dashboard");
    await dashboardTemplate.getByLabel("Template parameter Metric label").fill("Active Jobs");
    await dashboardTemplate.getByLabel("Template parameter Chart label").fill("Traffic Trend");
    await dashboardTemplate.getByLabel("Template parameter Table label").fill("Recent Activity");
    await dashboardTemplate.getByRole("button", { name: "Insert template" }).click();
    await expect(editor.getByText("Operations Dashboard", { exact: true })).toBeVisible();
    await expect(editor.getByRole("button", { name: "layout-card-1 layout.card" })).toBeVisible();
    await expect(editor.getByRole("button", { name: "layout-stack-1 layout.stack" })).toBeVisible();
    await editor.getByRole("button", { name: "layout-grid-1 layout.grid" }).click();
    await editor.getByLabel("Property Responsive columns mobile").fill("2");
    await editor.getByRole("button", { name: "data-table-1 data.table" }).click();
    await editor.getByRole("button", { name: "Add column" }).click();
    await editor.getByLabel("Column 1 key").fill("name");
    await editor.getByLabel("Column 1 key").blur();
    await editor.getByLabel("Column 1 label").fill("Name");
    await editor.getByLabel("Column 1 label").blur();
    await editor.getByRole("button", { name: "chart-line-1 chart.line" }).click();
    await editor.getByRole("button", { name: "Add series" }).click();
    await editor.getByLabel("Series 1 key").fill("requests");
    await editor.getByLabel("Series 1 key").blur();
    await editor.getByLabel("Series 1 label").fill("Requests");
    await editor.getByLabel("Series 1 label").blur();
    await page.getByRole("tab", { name: "Data" }).click();
    await page.getByRole("button", { name: "Add state" }).click();
    await page.getByLabel("State 1 ID").fill("selectedValue");
    await page.getByRole("tab", { name: "Canvas" }).click();
    await editor.locator("summary").filter({ hasText: "Layers" }).click();
    await editor.getByRole("button", { name: /Text Input input/ }).click();
    await expect(editor.getByRole("button", { name: "input-text-1 input.text" })).toBeVisible();
    await editor.getByText("Data binding", { exact: false }).click();
    await editor.getByLabel("Binding source").selectOption("workflow-output");
    await editor.getByLabel("Binding reference").fill("answer");
    await editor.getByLabel("Binding reference").blur();
    await editor.getByText("Interactions", { exact: false }).click();
    await editor.getByLabel("Enable Change").check();
    await expect(editor.getByLabel("Change action")).toHaveValue("state-set");
    await editor.getByLabel("Change target").selectOption("selectedValue");
    const responsivePreview = editor.getByTestId("app-responsive-preview");
    const inputTree = editor.getByRole("button", { name: "input-text-1 input.text" });
    await expect(inputTree).toHaveAttribute("aria-keyshortcuts", "Alt+ArrowUp Alt+ArrowDown");
    await inputTree.focus();
    await expect.poll(() => inputTree.evaluate((element) => Number.parseFloat(getComputedStyle(element).outlineWidth))).toBeGreaterThanOrEqual(2);
    await inputTree.press("Alt+ArrowUp");
    const rootChildren = responsivePreview.locator('[data-component-id="page-root"] > [data-component-id]');
    await expect(rootChildren.nth(2)).toHaveAttribute("data-component-id", "input-text-1");
    await inputTree.press("Alt+ArrowDown");
    await expect(rootChildren.nth(3)).toHaveAttribute("data-component-id", "input-text-1");
    const previewInput = responsivePreview.locator('[data-component-id="input-text-1"] input');
    await previewInput.focus();
    await expect.poll(() => previewInput.evaluate((element) => Number.parseFloat(getComputedStyle(element).outlineWidth))).toBeGreaterThanOrEqual(2);
    const inputTarget = await previewInput.boundingBox();
    expect(inputTarget?.width).toBeGreaterThanOrEqual(44);
    expect(inputTarget?.height).toBeGreaterThanOrEqual(44);
    await editor.getByText("Tools", { exact: true }).click();
    await editor.getByRole("button", { name: "Accessibility Audit" }).click();
    const accessibilityAudit = page.getByRole("dialog", { name: "Accessibility Audit" });
    await expect(accessibilityAudit.getByText("Accessibility audit passed", { exact: true })).toBeVisible();
    await expect(accessibilityAudit.getByRole("region", { name: "Accessibility issues" })).toHaveCount(0);
    await accessibilityAudit.getByRole("button", { name: "閉じる" }).click();
    await editor.getByRole("button", { name: "mobile" }).click();
    await expect(responsivePreview.locator('[data-component-id="layout-grid-1"]')).toHaveAttribute("style", /repeat\(2/);
    await expect(responsivePreview.locator('[data-component-id="data-table-1"]')).toContainText("Name");
    await expect(responsivePreview.locator('[data-component-id="chart-line-1"]')).toContainText("Requests");
    for (const [state, message] of [["loading", "Loading preview…"], ["empty", "Empty dataset preview"], ["error", "Preview error · Retry or review the binding."], ["disabled", "Disabled preview"]] as const) {
      await editor.getByLabel("Preview state").selectOption(state);
      await expect(editor.getByTestId("app-responsive-preview")).toHaveAttribute("data-preview-state", state);
      await expect(editor.getByText(message, { exact: true })).toBeVisible();
    }
    await editor.getByLabel("Preview state").selectOption("default");
    await expect(responsivePreview).toHaveClass(/max-w-\[320px\]/);
    await page.getByRole("tab", { name: "Data" }).click();
    const initialEntityEditor = page.getByRole("region", { name: "Entity Editor" });
    await initialEntityEditor.getByLabel("New Entity ID").fill("Project");
    await initialEntityEditor.getByRole("button", { name: "Add", exact: true }).click();
    await page.getByRole("tab", { name: "Canvas" }).click();
    await editor.locator("summary").filter({ hasText: "Layers" }).click();
    await editor.getByRole("button", { name: "data-table-1 data.table" }).click();
    await editor.getByText("Data binding", { exact: false }).click();
    await editor.getByLabel("Binding source").selectOption("entity");
    await editor.getByLabel("Binding entity reference").selectOption("Project.name");
    await editor.getByRole("button", { name: "Save changes" }).click();
    await expect(editor.getByRole("button", { name: "Saved" })).toBeDisabled();
    await page.reload();
    const reloadedEditor = page.getByRole("region", { name: "App Design Editor" });
    await reloadedEditor.locator("summary").filter({ hasText: "Layers" }).click();
    await expect(page.getByText("Hello App Studio", { exact: true })).toBeVisible();
    await expect(page.getByText("CPU Load", { exact: true })).toBeVisible();
    await expect(page.getByText("Operations Dashboard", { exact: true })).toBeVisible();
    await expect(reloadedEditor.getByRole("img", { name: "Traffic Trend" })).toBeVisible();
    await expect(page.getByText("Recent Activity", { exact: true })).toBeVisible();
    await expect(reloadedEditor.getByLabel("Design preset")).toHaveValue("terminal");
    await expect(reloadedEditor.getByRole("button", { name: "layout-card-1 layout.card" })).toBeVisible();
    await reloadedEditor.getByRole("button", { name: "data-table-1 data.table" }).click();
    await expect(reloadedEditor.getByLabel("Column 1 key")).toHaveValue("name");
    await expect(reloadedEditor.getByLabel("Column 1 label")).toHaveValue("Name");
    await reloadedEditor.getByRole("button", { name: "input-text-1 input.text" }).click();
    await reloadedEditor.getByText("Data binding", { exact: false }).click();
    await expect(reloadedEditor.getByLabel("Binding source")).toHaveValue("workflow-output");
    await expect(reloadedEditor.getByLabel("Binding reference")).toHaveValue("answer");
    await reloadedEditor.getByText("Interactions", { exact: false }).click();
    await expect(reloadedEditor.getByLabel("Enable Change")).toBeChecked();
    await expect(reloadedEditor.getByLabel("Change action")).toHaveValue("state-set");
    await expect(reloadedEditor.getByLabel("Change target")).toHaveValue("selectedValue");
    await reloadedEditor.getByText("Tools", { exact: true }).click();
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
    const visualDiff = review.getByRole("region", { name: "Visual preview diff" });
    await expect(visualDiff).toBeVisible();
    await expect(visualDiff.getByTestId("visual-diff-before")).toContainText("Hello App Studio");
    await expect(visualDiff.getByTestId("visual-diff-after")).toContainText("Patched App Studio");
    await expect(visualDiff.getByTestId("visual-diff-before")).toHaveAttribute("aria-label", "Before mobile preview");
    await visualDiff.getByRole("button", { name: "desktop" }).click();
    await expect(visualDiff.getByTestId("visual-diff-after")).toHaveAttribute("aria-label", "After desktop preview");
    await review.getByRole("button", { name: "Apply selected changes" }).click();
    await expect(review).toBeHidden();
    await expect(page.getByText("Patched App Studio", { exact: true })).toBeVisible();
    await expect(page.getByText("skipped-card", { exact: true })).toHaveCount(0);

    await reloadedEditor.getByRole("button", { name: "display-text-1 display.text" }).click();
    await reloadedEditor.getByText("Advanced", { exact: true }).click();
    const locks = reloadedEditor.getByRole("group", { name: "AI redesign locks" });
    await locks.getByRole("checkbox", { name: "content" }).check();
    await reloadedEditor.getByRole("button", { name: "Save changes" }).click();
    await expect(reloadedEditor.getByRole("button", { name: "Saved" })).toBeDisabled();
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
    await workspace.getByRole("button", { name: /Review/ }).click();
    await page.getByLabel("LLM runtime integration").selectOption("external");
    await expect(page.getByLabel("External LLM provider")).toBeVisible();
    await page.getByLabel("External LLM provider").selectOption("lmstudio");
    await expect(page.getByLabel("LLM runtime integration")).toHaveValue("external");
    await expect(page.getByLabel("External LLM provider")).toHaveValue("lmstudio");
    await workspace.getByRole("button", { name: /Create/ }).click();
    await reloadedEditor.getByRole("button", { name: "AI Design" }).click();
    const aiDesign = page.getByRole("dialog", { name: "AI Design Proposals" });
    await expect(aiDesign.getByLabel("AI design request")).toBeVisible();
    await expect(aiDesign.getByRole("button", { name: "Generate 3 proposals" })).toBeDisabled();
    await aiDesign.getByRole("button", { name: "閉じる" }).click();
    await page.route("**/api/v1/application-projects/*/design-proposals", async (route) => {
      const proposals = (["simple", "balanced", "dense"] as const).map((direction, index) => ({
        id: `mock-${direction}`, direction, title: `${direction} visual`, summary: `${direction} proposal preview`, rationale: ["Visual comparison"],
        patches: [{ op: "replace", path: "/pages/0/root/children/0/properties/text", value: `${direction} proposal` }], warnings: [],
        preview: {
          valid: true, baseChecksum: "a".repeat(64), resultChecksum: String(index + 1).repeat(64), appliedPatches: [], diagnostics: [],
          patchedSpec: {
            schemaVersion: 1, application: { name: "VisualProposal", displayName: "Visual Proposal" }, theme: { preset: "control-deck-modern", tokens: {} },
            navigation: { type: "sidebar", items: [] }, pages: [{ id: "home", title: "Home", root: { id: "root", type: "layout.stack", properties: { gap: "md", direction: "vertical" }, children: [{ id: "proposal-text", type: "display.text", properties: { text: `${direction} proposal` }, children: [] }] } }],
            entities: [], apiEndpoints: [], backgroundJobs: [], workflows: [], permissions: [], targets: [{ id: "web", platforms: ["web"], framework: "aspnet-blazor" }],
          },
        },
      }));
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ proposals }) });
    });
    await reloadedEditor.getByRole("button", { name: "AI Design" }).click();
    const proposalDialog = page.getByRole("dialog", { name: "AI Design Proposals" });
    await expect(proposalDialog.getByLabel("Design model")).not.toHaveValue("");
    await proposalDialog.getByLabel("AI design request").fill("Compare three layouts");
    await proposalDialog.getByRole("button", { name: "Generate 3 proposals" }).click();
    const proposals = proposalDialog.getByRole("region", { name: "Design proposals" });
    await expect(proposals.getByRole("region", { name: "simple proposal mobile preview" })).toContainText("simple proposal");
    await expect(proposals.getByRole("region", { name: "balanced proposal mobile preview" })).toContainText("balanced proposal");
    await expect(proposals.getByRole("region", { name: "dense proposal mobile preview" })).toContainText("dense proposal");
    await proposalDialog.getByRole("button", { name: "desktop" }).click();
    await expect(proposals.getByRole("region", { name: "simple proposal desktop preview" })).toBeVisible();
    await proposalDialog.getByRole("button", { name: "閉じる" }).click();

    await workspace.getByRole("button", { name: /Target/ }).click();
    const advisor = page.getByRole("region", { name: "Platform Advisor" });
    await expect(advisor.getByLabel("Target platform web")).toBeChecked();
    await advisor.getByText("Requirements and preferences", { exact: true }).click();
    await advisor.getByLabel("Requirement Offline").check();
    await advisor.getByLabel("Preferred language").selectOption("csharp");
    await advisor.getByRole("button", { name: "Recommend platforms" }).click();
    const platformRecommendations = advisor.getByRole("region", { name: "Platform recommendations" });
    await expect(platformRecommendations.getByText("Top: aspnet-blazor", { exact: true })).toBeVisible();
    await expect(platformRecommendations.getByLabel("Select ASP.NET Core + Blazor")).toBeChecked();
    await advisor.getByRole("button", { name: "Preflight selected targets" }).click();
    const preflight = advisor.getByRole("region", { name: "Platform preflight" });
    await expect(preflight.getByText(/Generation: blocked/)).toBeVisible();
    await expect(preflight.getByText("GENERATOR_AUTH_ADAPTER_UNAVAILABLE", { exact: true }).first()).toBeVisible();
    await expect(preflight.getByText("GENERATOR_GUI_AUTH_UNAVAILABLE", { exact: true })).toHaveCount(0);
    await advisor.getByRole("button", { name: "Apply selected targets" }).click();
    await expect.poll(async () => page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/application-projects/${id}`, { credentials: "same-origin" });
      if (!response.ok) return 0;
      const project = await response.json() as { spec?: { targets?: unknown[] } };
      return project.spec?.targets?.length ?? 0;
    }, projectId)).toBe(1);

    await page.evaluate(async ({ id, workflowId }) => {
      const response = await fetch(`/api/v1/application-projects/${id}`, { credentials: "same-origin" });
      const project = await response.json() as { spec: Record<string, any> };
      project.spec.application.authentication = "api-key";
      project.spec.application.database = "sqlite";
      project.spec.targets = [{ id: "web", platforms: ["web", "linux", "windows"], framework: "aspnet-blazor" }];
      project.spec.entities = [{
        id: "Project", tableName: "projects", fields: [{ id: "name", type: "string", maxLength: 120 }],
        crud: { enabled: true, basePath: "/api/projects", operations: ["list", "read", "create", "update", "delete"] },
      }];
      project.spec.apiEndpoints = [{
        id: "run-sync", method: "POST", path: "/api/gui-run", workflowId, mode: "sync", authentication: "inherit", timeoutSeconds: 30,
        requestSchema: { type: "object", required: ["message"], properties: { message: { type: "string", title: "Message", minLength: 1, maxLength: 120 } }, additionalProperties: false },
        responseSchema: { type: "object", properties: { result: { type: "string" } } },
      }];
      project.spec.pages = [{ id: "home", title: "Projects", root: {
        id: "page-root", type: "layout.stack", properties: { gap: "md", direction: "vertical" }, children: [
          { id: "intro", type: "display.text", properties: { text: "Generated projects" }, children: [] },
          { id: "projects", type: "data.table", properties: { label: "Projects", columns: [{ key: "name", label: "Name" }], enableCreate: true, enableUpdate: true, enableDelete: true }, binding: "entity:Project", children: [] },
          { id: "run", type: "action.workflow-run", properties: { label: "Run workflow", workflowBinding: "main", endpointId: "run-sync", resultLabel: "Workflow result" }, children: [] },
        ],
      } }];
      const saved = await fetch(`/api/v1/application-projects/${id}`, {
        method: "PATCH", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
        body: JSON.stringify({ spec: project.spec }),
      });
      if (!saved.ok) throw new Error(await saved.text());
    }, { id: projectId, workflowId });
    await page.reload();
    const generatedEditor = page.getByRole("region", { name: "App Design Editor" });
    await generatedEditor.locator("summary").filter({ hasText: "Layers" }).click();
    await generatedEditor.getByRole("button", { name: "projects data.table" }).click();
    await expect(generatedEditor.getByLabel("Property Allow create")).toBeChecked();
    await expect(generatedEditor.getByLabel("Property Allow update")).toBeChecked();
    await expect(generatedEditor.getByLabel("Property Allow delete")).toBeChecked();
    await expect(generatedEditor.getByTestId("app-responsive-preview").getByRole("button", { name: "Add item" })).toBeVisible();
    await generatedEditor.getByRole("button", { name: "run action.workflow-run" }).click();
    await expect(generatedEditor.getByLabel("Property Workflow binding")).toHaveValue("main");
    await expect(generatedEditor.getByLabel("Property API endpoint ID (optional)")).toHaveValue("run-sync");
    await expect(generatedEditor.getByLabel("Property Result label")).toHaveValue("Workflow result");
    await expect(generatedEditor.getByTestId("app-responsive-preview").getByRole("button", { name: "Run workflow" })).toBeVisible();
    await workspace.getByRole("button", { name: /Export/ }).click();
    const guiGenerator = page.getByRole("region", { name: "Source Generator" });
    await guiGenerator.getByRole("button", { name: "Preview generated source" }).click();
    const guiPreview = guiGenerator.getByRole("region", { name: "Source generation preview" });
    await expect(guiPreview.getByText(/Ready · 21 files/)).toBeVisible();
    await expect(guiPreview.getByText(/Components\/Pages\/HomePage\.razor/)).toBeVisible();
    const guiDownloadPromise = page.waitForEvent("download");
    await guiPreview.getByRole("button", { name: "Generate source ZIP" }).click();
    expect((await guiDownloadPromise).suggestedFilename()).toBe("E2EApplicationSourceApp-aspnet-source.zip");

    await page.evaluate(async ({ id, workflowId }) => {
      const response = await fetch(`/api/v1/application-projects/${id}`, { credentials: "same-origin" });
      const project = await response.json() as { spec: Record<string, any> };
      project.spec.application.authentication = "api-key";
      project.spec.application.database = "sqlite";
      project.spec.pages = [];
      project.spec.targets = [{ id: "web", platforms: ["web", "linux", "windows"], framework: "aspnet-blazor" }];
      project.spec.entities = [{
        id: "Project", tableName: "projects",
        fields: [
          { id: "name", type: "string", maxLength: 120, unique: true },
          { id: "active", type: "boolean", hasDefault: true, default: true, indexed: true },
        ],
        crud: { enabled: true, basePath: "/api/projects", operations: ["create", "read", "list", "update", "delete"] },
      }];
      project.spec.apiEndpoints = [{
        id: "run-async", method: "POST", path: "/api/run", workflowId, mode: "async", authentication: "inherit", timeoutSeconds: 30,
        requestSchema: { type: "object", required: ["message"], properties: { message: { type: "string", minLength: 1 } }, additionalProperties: false },
        responseSchema: { type: "object", required: ["answer"], properties: { answer: { type: "string" } } },
      }];
      project.spec.backgroundJobs = [{
        id: "manual-run", workflowId, trigger: "manual", schedule: "", timeZone: "UTC",
        input: { message: "scheduled" }, enabled: true, timeoutSeconds: 30,
        concurrencyPolicy: "skip", catchUpPolicy: "run-once",
      }];
      const saved = await fetch(`/api/v1/application-projects/${id}`, {
        method: "PATCH", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
        body: JSON.stringify({ spec: project.spec }),
      });
      if (!saved.ok) throw new Error(await saved.text());
    }, { id: projectId, workflowId });
    await page.reload();
    await page.getByRole("tab", { name: "Data" }).click();
    const entityEditor = page.getByRole("region", { name: "Entity Editor" });
    await expect(entityEditor.getByRole("button", { name: /Project/ })).toBeVisible();
    await entityEditor.getByRole("button", { name: "Add field" }).click();
    await entityEditor.getByLabel("Field ID").last().fill("description");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Application Specを保存しました")).toBeVisible();
    await page.getByRole("navigation", { name: "Application workspace" }).getByRole("button", { name: /Export/ }).click();
    const aspnetGenerator = page.getByRole("region", { name: "Source Generator" });
    await expect(aspnetGenerator.getByText("ASP.NET API", { exact: true })).toBeVisible();
    await aspnetGenerator.getByRole("button", { name: "Preview generated source" }).click();
    const aspnetPreview = aspnetGenerator.getByRole("region", { name: "Source generation preview" });
    await expect(aspnetPreview.getByText(/Ready · 16 files/)).toBeVisible();
    const aspnetDownloadPromise = page.waitForEvent("download");
    await aspnetPreview.getByRole("button", { name: "Generate source ZIP" }).click();
    expect((await aspnetDownloadPromise).suggestedFilename()).toBe("E2EApplicationSourceApp-aspnet-source.zip");

    await page.getByRole("navigation", { name: "Application workspace" }).getByRole("button", { name: /Target/ }).click();
    await advisor.getByLabel("Target platform linux").check();
    await advisor.getByLabel("Target platform web").uncheck();
    await advisor.getByText("Requirements and preferences", { exact: true }).click();
    await advisor.getByLabel("Requirement Web reuse").uncheck();
    await advisor.getByLabel("Requirement Small package").check();
    await advisor.getByRole("button", { name: "Recommend platforms" }).click();
    await expect(platformRecommendations.getByText("Top: csharp-console", { exact: true })).toBeVisible();
    await expect(platformRecommendations.getByLabel("Select C# Console / Service")).toBeChecked();
    await advisor.getByRole("button", { name: "Preflight selected targets" }).click();
    await expect(preflight.getByText(/Generation: ready/)).toBeVisible();
    await advisor.getByRole("button", { name: "Apply selected targets" }).click();
    await expect.poll(async () => page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/application-projects/${id}`, { credentials: "same-origin" });
      if (!response.ok) return "";
      const project = await response.json() as { spec?: { targets?: Array<{ framework?: string }> } };
      return project.spec?.targets?.[0]?.framework ?? "";
    }, projectId)).toBe("csharp-console");

    await page.getByRole("navigation", { name: "Application workspace" }).getByRole("button", { name: /Export/ }).click();
    const sourceGenerator = page.getByRole("region", { name: "Source Generator" });
    await sourceGenerator.getByRole("button", { name: "Preview generated source" }).click();
    const sourcePreview = sourceGenerator.getByRole("region", { name: "Source generation preview" });
    await expect(sourcePreview.getByText(/Ready · 10 files/)).toBeVisible();
    await expect(sourcePreview.getByText("Source checksum", { exact: true })).toBeVisible();
    await expect(sourcePreview.getByText(/generation-manifest\.json/)).toBeVisible();
    const downloadPromise = page.waitForEvent("download");
    await sourcePreview.getByRole("button", { name: "Generate source ZIP" }).click();
    const sourceDownload = await downloadPromise;
    expect(sourceDownload.suggestedFilename()).toBe("E2EApplicationSourceApp-source.zip");

    await page.getByRole("navigation", { name: "Application workspace" }).getByRole("button", { name: /Review/ }).click();
    await expect(page.getByText(/Linux／Windows向けC# ConsoleまたはASP.NET Core source/)).toBeVisible();
    await expect(page.getByText(/Buildはまだ実行しません/)).toBeVisible();
    await expect(page.getByRole("button", { name: /ビルド|公開/ })).toHaveCount(0);
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
