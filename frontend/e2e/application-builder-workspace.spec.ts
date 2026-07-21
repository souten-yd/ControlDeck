import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("App Builder uses a focused responsive workspace without losing draft edits", async ({ page }) => {
  test.setTimeout(120_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/applications");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  errors.length = 0;

  const ids = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const workflowResponse = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({
        name: "E6 Workspace UX",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", config: { mode: "manual" }, position: { x: 80, y: 80 } },
            { id: "output", type: "output.render", config: { name: "result", value: "Ready" }, position: { x: 280, y: 80 } },
          ],
          edges: [{ source: "trigger", target: "output" }],
        },
      }),
    });
    if (!workflowResponse.ok) throw new Error(await workflowResponse.text());
    const workflow = await workflowResponse.json() as { id: number };
    const projectResponse = await fetch(`/api/v1/workflows/${workflow.id}/application-projects`, {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ source: "draft", name: "E6 Workspace UX App" }),
    });
    if (!projectResponse.ok) throw new Error(await projectResponse.text());
    const project = await projectResponse.json() as { id: number; spec: Record<string, unknown> };
    const spec = {
      ...project.spec,
      application: { ...project.spec.application as object, authentication: "none", database: "sqlite" },
      pages: [{ id: "home", title: "Home", root: { id: "page-root", type: "layout.stack", properties: { gap: "md", direction: "vertical" }, children: [
        { id: "welcome", type: "display.text", properties: { text: "Focused workspace" }, children: [] },
        { id: "message-input", type: "input.text", properties: { label: "Message" }, binding: "state:message", events: { change: { action: "state-set", target: "message" } }, children: [] },
        { id: "run", type: "action.workflow-run", properties: { label: "Run", workflowBinding: "main", endpointId: "run", resultLabel: "Result" }, events: { success: { action: "state-set", target: "result" } }, children: [] },
        { id: "result", type: "display.markdown", properties: { value: "" }, binding: "state:result", children: [] },
        { id: "projects", type: "data.table", properties: { label: "Projects", columns: [{ key: "name", label: "Name" }], pageSize: 20 }, binding: "query:recentProjects", children: [] },
        { id: "api-items", type: "data.table", properties: { label: "API items", columns: [] }, binding: "query:apiItems", children: [] },
      ] } }],
      clientState: [{ id: "message", type: "string", initialValue: "Ready", nullable: false }, { id: "result", type: "object", initialValue: {}, nullable: false }],
      entities: [{ id: "Project", displayName: "Projects", fields: [{ id: "name", type: "string", maxLength: 120 }], crud: { enabled: true, operations: ["list"], basePath: "/api/entities/projects" } }],
      queries: [
        { id: "recentProjects", source: "entity", entityId: "Project", filters: [], sort: [], pagination: "offset", limit: 20, autoLoad: true, cachePolicy: "memory", staleTimeSeconds: 30 },
        { id: "apiItems", source: "api", endpointId: "list", input: { category: "recent" }, resultPath: "results", filters: [], sort: [], pagination: "none", limit: 20, autoLoad: true, cachePolicy: "network-only", staleTimeSeconds: 0 },
      ],
      targets: [{ id: "web", framework: "aspnet-blazor", platforms: ["web", "linux", "windows"] }],
      apiEndpoints: [
        { id: "run", method: "POST", path: "/api/run", workflowId: workflow.id, mode: "sync", authentication: "inherit", timeoutSeconds: 30, requestSchema: { type: "object", properties: {} }, responseSchema: { type: "object", properties: { result: { type: "string" } } } },
        {
          id: "list", method: "POST", path: "/api/items/query", workflowId: workflow.id,
          mode: "sync", authentication: "inherit", timeoutSeconds: 30,
          requestSchema: {
            type: "object", properties: { category: { type: "string" } },
            required: ["category"], additionalProperties: false,
          },
          responseSchema: {
            type: "object",
            properties: {
              results: {
                type: "array",
                items: {
                  type: "object",
                  properties: { name: { type: "string" }, rank: { type: "integer" } },
                  required: ["name", "rank"],
                },
              },
            },
            required: ["results"],
          },
        },
      ],
    };
    const updateResponse = await fetch(`/api/v1/application-projects/${project.id}`, {
      method: "PATCH", credentials: "same-origin", headers, body: JSON.stringify({ spec }),
    });
    if (!updateResponse.ok) throw new Error(await updateResponse.text());
    return { workflowId: workflow.id, projectId: project.id };
  });

  try {
    await page.goto(`/applications/${ids.projectId}`);
    const workspace = page.getByRole("navigation", { name: "Application workspace" });
    await expect(workspace.getByRole("button")).toHaveCount(4);
    await expect(workspace.getByRole("button", { name: /Create/ })).toHaveAttribute("aria-current", "page");
    await expect(page.getByRole("region", { name: "App Design Editor" })).toBeVisible();
    await expect(page.getByRole("region", { name: "Platform Advisor" })).toBeHidden();
    await expect(page.getByTestId("app-responsive-preview")).toContainText("Focused workspace");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);

    await page.getByRole("button", { name: "＋ Add" }).click();
    const addSheet = page.getByRole("dialog", { name: "Add to canvas" });
    await expect(addSheet).toBeVisible();
    await addSheet.getByRole("button", { name: /Text display/ }).click();
    await expect(page.getByTestId("app-responsive-preview")).toContainText("Text");
    await page.getByRole("button", { name: "Layers", exact: true }).click();
    const layers = page.getByRole("dialog", { name: "Layers" });
    await layers.getByRole("button", { name: "welcome display.text" }).click();
    await page.getByRole("button", { name: "Inspect" }).click();
    const inspector = page.getByRole("dialog", { name: "Inspector" });
    await expect(inspector.getByLabel("Property Text")).toHaveValue("Focused workspace");
    await inspector.getByRole("button", { name: "閉じる" }).click();

    await page.getByRole("tab", { name: "Data" }).click();
    await expect(page.getByRole("region", { name: "Query Editor" })).toBeVisible();
    await expect(page.getByLabel("Query ID", { exact: true })).toHaveValue("recentProjects");
    await page.getByRole("button", { name: "Add filter" }).click();
    await page.getByLabel("Filter 1 field").selectOption("name");
    await page.getByLabel("Filter 1 operator").selectOption("contains");
    await page.getByLabel("Filter 1 value").fill("active");
    await page.getByRole("button", { name: "Add sort" }).click();
    await page.getByLabel("Sort 1 field").selectOption("name");
    await page.getByLabel("Sort 1 direction").selectOption("desc");
    await expect(page.getByLabel("Query offset pagination")).toBeChecked();
    await page.getByLabel("Query maximum rows").fill("25");
    await page.getByRole("button", { name: /apiItems/ }).click();
    await expect(page.getByLabel("Query source")).toHaveValue("api");
    await expect(page.getByLabel("Query API endpoint")).toHaveValue("list");
    await expect(page.getByLabel("Query result path")).toHaveValue("results");
    await expect(page.getByLabel("Query API input")).toContainText('"category": "recent"');
    await page.getByRole("button", { name: /recentProjects/ }).click();
    await expect(page.getByRole("region", { name: "Client State Editor" })).toBeVisible();
    await page.getByRole("button", { name: "Add state" }).click();
    await expect(page.getByLabel("State 3 ID")).toHaveValue("state3");
    await workspace.getByRole("button", { name: /Target/ }).click();
    await expect(page.getByRole("region", { name: "Platform Advisor" })).toBeVisible();
    await workspace.getByRole("button", { name: /Create/ }).click();
    await page.getByRole("tab", { name: "Data" }).click();
    await expect(page.getByLabel("Query maximum rows")).toHaveValue("25");
    await expect(page.getByLabel("Filter 1 value")).toHaveValue("active");
    await expect(page.getByLabel("Sort 1 direction")).toHaveValue("desc");
    await expect(page.getByLabel("State 3 ID")).toHaveValue("state3");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("button", { name: "Saved" })).toBeDisabled();

    await workspace.getByRole("button", { name: /Export/ }).click();
    const generator = page.getByRole("region", { name: "Source Generator" });
    await expect(generator).toBeVisible();
    await generator.getByRole("button", { name: "Preview generated source" }).click();
    await expect(generator.getByText(/Ready · 21 files/)).toBeVisible();
    await expect.poll(() => page.evaluate(async (projectId) => {
      const response = await fetch(`/api/v1/application-projects/${projectId}/source-preview?target_id=web`, { credentials: "same-origin" });
      const payload = await response.json() as { phase?: string; generator?: { version?: string }; manifest?: { runtime?: { clientState?: string; queries?: string } } };
      return `${payload.phase}/${payload.generator?.version}/${payload.manifest?.runtime?.clientState}/${payload.manifest?.runtime?.queries}`;
    }, ids.projectId)).toBe("E7/1.0.0/browser-memory-typed/typed-entity-api-collection-filter-sort-pagination");
    await workspace.getByRole("button", { name: /Review/ }).click();
    await expect(page.getByText("Application IR", { exact: true })).toBeVisible();

    await page.setViewportSize({ width: 1280, height: 800 });
    await workspace.getByRole("button", { name: /Create/ }).click();
    await page.getByRole("tab", { name: "Canvas" }).click();
    await expect(page.getByText("Add", { exact: true })).toBeVisible();
    await expect(page.getByText("Inspector", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "＋ Add" })).toBeHidden();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    expect(errors).toEqual([]);
  } finally {
    await page.evaluate(async ({ projectId, workflowId }) => {
      const options = { method: "DELETE", credentials: "same-origin" as const, headers: { "X-Requested-With": "ControlDeck" } };
      await fetch(`/api/v1/application-projects/${projectId}`, options);
      await fetch(`/api/v1/workflows/${workflowId}`, options);
    }, ids);
  }
});

test("E7 source preview keeps Secret names opaque and blocks unauthenticated side effects", async ({ page }) => {
  test.setTimeout(60_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/applications");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  errors.length = 0;

  const ids = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const workflowResponse = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({
        name: "E7 bounded side effects",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", config: { mode: "manual" } },
            { id: "request", type: "http.request", config: {
              method: "POST", url: "https://example.com/api/items",
              headers: { Authorization: "Bearer {{secrets.API_TOKEN}}" },
              body: '{"credential":"{{secrets.BODY_SECRET}}"}', expected_status: 200,
            } },
            { id: "write", type: "file.write", config: { path: "results/latest.json", content: "{{request.body}}", append: false } },
            { id: "output", type: "output.render", config: { name: "result", value: "{{request.body}}" } },
          ],
          edges: [
            { source: "trigger", target: "request" }, { source: "request", target: "write" },
            { source: "write", target: "output" },
          ],
        },
      }),
    });
    if (!workflowResponse.ok) throw new Error(await workflowResponse.text());
    const workflow = await workflowResponse.json() as { id: number };
    const projectResponse = await fetch(`/api/v1/workflows/${workflow.id}/application-projects`, {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ source: "draft", name: "E7 source boundary" }),
    });
    if (!projectResponse.ok) throw new Error(await projectResponse.text());
    const project = await projectResponse.json() as { id: number; spec: Record<string, unknown> };
    const spec = {
      ...project.spec,
      application: { ...project.spec.application as object, authentication: "none" },
      targets: [{ id: "web", framework: "aspnet-blazor", platforms: ["web", "linux", "windows"] }],
      apiEndpoints: [{
        id: "run", method: "POST", path: "/api/run", workflowId: workflow.id,
        mode: "sync", authentication: "inherit", timeoutSeconds: 30,
      }],
    };
    let response = await fetch(`/api/v1/application-projects/${project.id}`, {
      method: "PATCH", credentials: "same-origin", headers, body: JSON.stringify({ spec }),
    });
    if (!response.ok) throw new Error(await response.text());
    response = await fetch(`/api/v1/application-projects/${project.id}/source-preview?target_id=web`, { credentials: "same-origin" });
    const blocked = await response.json() as { ready: boolean; diagnostics: Array<{ code: string }> };
    if (blocked.ready || !blocked.diagnostics.some((item) => item.code === "GENERATOR_SIDE_EFFECT_AUTH_REQUIRED")) {
      throw new Error("unauthenticated side-effect source was not blocked");
    }
    (spec.application as Record<string, unknown>).authentication = "api-key";
    response = await fetch(`/api/v1/application-projects/${project.id}`, {
      method: "PATCH", credentials: "same-origin", headers, body: JSON.stringify({ spec }),
    });
    if (!response.ok) throw new Error(await response.text());
    return { workflowId: workflow.id, projectId: project.id };
  });

  try {
    await page.goto(`/applications/${ids.projectId}`);
    await page.getByRole("navigation", { name: "Application workspace" }).getByRole("button", { name: /Export/ }).click();
    const generator = page.getByRole("region", { name: "Source Generator" });
    await expect(generator.getByText("Deterministic Source Generator · B2.5/E7")).toBeVisible();
    await generator.getByRole("button", { name: "Preview generated source" }).click();
    await expect(generator.getByText(/Ready · 15 files/)).toBeVisible();
    const runtime = await page.evaluate(async (projectId) => {
      const response = await fetch(`/api/v1/application-projects/${projectId}/source-preview?target_id=web`, { credentials: "same-origin" });
      const text = await response.text();
      const payload = JSON.parse(text) as {
        phase: string; generator: { version: string };
        manifest: { runtime: { secretEnvironment: string[]; workflowSideEffects: string[]; auditRoot: string; fileRoot: string } };
      };
      return { text, phase: payload.phase, version: payload.generator.version, runtime: payload.manifest.runtime };
    }, ids.projectId);
    expect(runtime.text).not.toContain("API_TOKEN");
    expect(runtime.text).not.toContain("BODY_SECRET");
    expect(runtime.phase).toBe("E7");
    expect(runtime.version).toBe("1.0.0");
    expect(runtime.runtime.secretEnvironment).toEqual(["CONTROLDECK_SECRET_001", "CONTROLDECK_SECRET_002"]);
    expect(runtime.runtime.workflowSideEffects).toEqual(["external", "write"]);
    expect(runtime.runtime.auditRoot).toBe("CONTROLDECK_APP_AUDIT_ROOT");
    expect(runtime.runtime.fileRoot).toBe("CONTROLDECK_APP_WORK_ROOT");
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    expect(errors).toEqual([]);
  } finally {
    await page.evaluate(async ({ projectId, workflowId }) => {
      const options = { method: "DELETE", credentials: "same-origin" as const, headers: { "X-Requested-With": "ControlDeck" } };
      await fetch(`/api/v1/application-projects/${projectId}`, options);
      await fetch(`/api/v1/workflows/${workflowId}`, options);
    }, ids);
  }
});
