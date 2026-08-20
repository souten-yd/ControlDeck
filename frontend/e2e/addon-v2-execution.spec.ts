import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const manifestPath = new URL("../../tools/fake-addon/control-deck-addon.json", import.meta.url);

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

async function api(page: Page, path: string, method = "GET", body?: unknown) {
  return page.evaluate(async ({ path, method, body }) => {
    const response = await fetch(`/api/v1${path}`, {
      method,
      credentials: "same-origin",
      headers: {
        "X-Requested-With": "ControlDeck",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    return { status: response.status, body: text ? JSON.parse(text) : null };
  }, { path, method, body });
}

test.afterEach(async ({ page }) => {
  await api(page, "/addons/fake-addon", "DELETE").catch(() => undefined);
});

test("executes remote Workflow, Agent, and scoped Context contributions", async ({ page, request }) => {
  test.setTimeout(120_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  const fakeHealth = await request.get("http://127.0.0.1:9130/health").catch(() => null);
  test.skip(!fakeHealth?.ok(), "the fake add-on service must be running on port 9130");

  const browserErrors: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => message.type() === "error" && browserErrors.push(message.text()));
  const manifest = JSON.parse(await readFile(manifestPath, "utf8")) as { host_capabilities: string[] };
  await login(page);
  await api(page, "/addons/fake-addon", "DELETE");
  expect((await api(page, "/addons", "POST", manifest)).status).toBe(201);
  expect((await api(page, "/addons/fake-addon/enable", "POST", {
    granted_capabilities: manifest.host_capabilities,
  })).status).toBe(200);
  // The idempotent pre-test DELETE may legitimately return 404; observe runtime errors after setup.
  browserErrors.length = 0;

  const discovered = await api(page, "/addons/execution-contributions");
  expect(discovered.status).toBe(200);
  expect(discovered.body.contributions.workflow_executors).toEqual(expect.arrayContaining([
    expect.objectContaining({ addon_id: "fake-addon", id: "fake.generate" }),
  ]));
  expect(discovered.body.contributions.agent_tools).toEqual(expect.arrayContaining([
    expect.objectContaining({ addon_id: "fake-addon", id: "fake.generate" }),
  ]));

  const nodeType = "addon.workflow:fake-addon:fake.generate";
  const created = await api(page, "/workflows", "POST", {
    name: "E2E Add-on execution",
    definition: {
      nodes: [
        { id: "trigger", type: "trigger", config: { mode: "manual" }, position: { x: 40, y: 140 } },
        { id: "remote", type: nodeType, config: { prompt: "{{trigger.message}}" }, position: { x: 310, y: 140 } },
        { id: "result", type: "signal.display", config: { signal: "result", value: "{{remote.ok}}" }, position: { x: 580, y: 140 } },
      ],
      edges: [
        { source: "trigger", target: "remote" },
        { source: "remote", target: "result" },
      ],
    },
  });
  expect(created.status).toBe(201);
  const workflowId = created.body.id as number;

  try {
    const dryRun = await api(page, `/workflows/${workflowId}/dry-run`, "POST", { input: { message: "preview" } });
    expect(dryRun.status).toBe(200);
    expect(dryRun.body.dry_run).toBe(true);

    const started = await api(page, `/workflows/${workflowId}/test`, "POST", { input: { message: "live" } });
    expect(started.status).toBe(200);
    await expect.poll(async () => {
      const detail = await api(page, `/workflow-executions/${started.body.execution_id}`);
      return detail.body.status;
    }).toBe("SUCCEEDED");
    const execution = await api(page, `/workflow-executions/${started.body.execution_id}`);
    expect(execution.body.context.remote.output.ok).toBe(true);
    expect(execution.body.context.remote.output.echo.input.prompt).toBe("live");

    const agent = await api(page, "/addons/fake-addon/agent-tools/fake.generate/invoke", "POST", {
      arguments: { prompt: "job-backed" },
    });
    expect(agent.status).toBe(200);
    expect(agent.body.asset_id).toBe(`job-result:${agent.body.job_id}`);
    expect(agent.body.output.content[0].text).toContain("fake-addon received");
    const job = await api(page, `/jobs/${agent.body.job_id}`);
    expect(job.body.status).toBe("succeeded");

    const context = await api(page, "/addons/fake-addon/context-actions/fake.inspect/invoke", "POST", {
      context_type: "file",
      resource_id: "/data1tb/ControlDeck/app/AGENTS.md",
      input: {},
    });
    expect(context.status).toBe(200);
    expect(context.body.context.resource_id).toMatch(/^grant:/);
    expect(JSON.stringify(context.body)).not.toContain("/data1tb/");

    await page.setViewportSize({ width: 320, height: 700 });
    await page.goto(`/workflows/${workflowId}`);
    await page.getByRole("button", { name: "ノードを追加" }).click();
    const library = page.getByRole("dialog", { name: "ノードを追加" });
    await library.getByLabel("ノードを検索").fill("Fake generate");
    await expect(library.getByText("Fake generate", { exact: true })).toBeVisible();
    await expect(library.getByText("拡張機能", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(320);
    await library.getByRole("button", { name: "閉じる" }).click();

    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto("/project-lab");
    const contextMenu = page.getByRole("button", { name: "拡張機能のコンテキストアクション" });
    await expect(contextMenu).toBeVisible();
    await contextMenu.click();
    await page.getByRole("menuitem", { name: /Inspect with fake add-on/ }).click();
    await expect(page.getByText("Inspect with fake add-onを実行しました")).toBeVisible();
    expect(browserErrors).toEqual([]);
  } finally {
    await api(page, `/workflows/${workflowId}`, "DELETE");
  }
});
