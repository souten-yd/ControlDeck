import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;
const activeStatuses = ["queued", "preparing", "generating", "restoring", "building", "testing", "canceling"];

test("App Studio builds and tests generated source in an isolated systemd user unit", async ({ page }) => {
  test.setTimeout(180_000);
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

  const capability = await page.evaluate(async () => {
    const response = await fetch("/api/v1/application-builder/capabilities", { credentials: "same-origin" });
    if (!response.ok) throw new Error(await response.text());
    return await response.json() as { buildAvailable: boolean; build: { available: boolean; sdkPath: string | null; network: string } };
  });
  expect(capability.buildAvailable).toBe(true);
  expect(capability.build).toMatchObject({ available: true, network: "denied" });
  expect(capability.build.sdkPath).toContain("dotnet-8.0.423/dotnet");

  const ids = await page.evaluate(async () => {
    const headers = { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" };
    const workflowResponse = await fetch("/api/v1/workflows", {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({
        name: "B3 isolated build verification",
        definition: {
          nodes: [
            { id: "trigger", type: "trigger", config: { mode: "manual" } },
            { id: "output", type: "output.render", config: { name: "result", value: "Build verified" } },
          ],
          edges: [{ source: "trigger", target: "output" }],
        },
      }),
    });
    if (!workflowResponse.ok) throw new Error(await workflowResponse.text());
    const workflow = await workflowResponse.json() as { id: number };
    const projectResponse = await fetch(`/api/v1/workflows/${workflow.id}/application-projects`, {
      method: "POST", credentials: "same-origin", headers,
      body: JSON.stringify({ source: "draft", name: "B3 Isolated Build App" }),
    });
    if (!projectResponse.ok) throw new Error(await projectResponse.text());
    const project = await projectResponse.json() as { id: number; spec: Record<string, unknown> };
    const spec = {
      ...project.spec,
      application: { ...project.spec.application as object, authentication: "none" },
      targets: [
        { id: "console", framework: "csharp-console", platforms: ["linux", "windows"] },
        { id: "web", framework: "aspnet-blazor", platforms: ["linux", "windows"] },
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
    await workspace.getByRole("button", { name: /Export/ }).click();
    const buildPanel = page.getByRole("region", { name: "Isolated build" });
    await expect(buildPanel).toContainText("systemd user");
    await expect(buildPanel).toContainText("Network");
    await expect(buildPanel).toContainText("Denied");
    await expect(buildPanel.getByRole("button", { name: "Build & test", exact: true })).toBeEnabled();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);

    await buildPanel.getByRole("button", { name: "Build & test", exact: true }).click();
    const buildCard = buildPanel.getByRole("article").first();
    await expect(buildCard).toContainText(/Build #\d+/);
    await expect(buildCard.getByRole("progressbar")).toBeVisible();
    const buildId = Number((await buildCard.getByText(/Build #\d+/).first().textContent())?.match(/\d+/)?.[0]);
    expect(buildId).toBeGreaterThan(0);

    const completed = await expect.poll(async () => page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/application-builds/${id}`, { credentials: "same-origin" });
      if (!response.ok) return { status: `http-${response.status}`, artifacts: [] as Array<{ kind: string; id: number }> };
      const build = await response.json() as { status: string; artifacts: Array<{ kind: string; id: number }> };
      return { status: build.status, artifacts: build.artifacts };
    }, buildId), { timeout: 120_000, intervals: [500, 800, 1200] }).toMatchObject({ status: "completed" });
    void completed;

    await expect(buildCard).toContainText("Completed", { timeout: 10_000 });
    await expect(buildCard.getByRole("link", { name: /Source ZIP/ })).toBeVisible();
    const detail = await page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/application-builds/${id}`, { credentials: "same-origin" });
      return await response.json() as { artifacts: Array<{ id: number; kind: string; checksum: string }> };
    }, buildId);
    expect(detail.artifacts.some((item) => item.kind === "binary")).toBe(true);
    const source = detail.artifacts.find((item) => item.kind === "source")!;
    expect(source.checksum).toMatch(/^[0-9a-f]{64}$/);
    const artifactResponse = await page.request.get(`/api/v1/application-builds/${buildId}/artifacts/${source.id}`);
    expect(artifactResponse.status()).toBe(200);
    expect(artifactResponse.headers()["x-content-type-options"]).toBe("nosniff");
    expect((await artifactResponse.body()).subarray(0, 2).toString()).toBe("PK");

    await buildCard.getByRole("button", { name: `Build #${buildId} actions` }).click();
    await page.getByRole("menuitem", { name: "Show logs" }).click();
    await expect(buildCard).toContainText("Generated source self-test passed");
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);

    await buildCard.getByRole("button", { name: `Build #${buildId} actions` }).click();
    await page.getByRole("menuitem", { name: "Delete build" }).click();
    const dialog = page.getByRole("alertdialog", { name: `Build #${buildId}を削除` });
    await expect(dialog).toContainText("元に戻せません");
    await dialog.getByRole("button", { name: "Buildを削除" }).click();
    await expect(buildCard).toBeHidden();
    await expect.poll(async () => (await page.request.get(`/api/v1/application-builds/${buildId}`)).status()).toBe(404);

    // Exercise the real systemctl stop path while restore/build is active.
    await expect(buildPanel.getByRole("button", { name: "Build & test", exact: true })).toBeEnabled();
    await buildPanel.getByRole("button", { name: "Build & test", exact: true }).click();
    const cancelledCard = buildPanel.getByRole("article").first();
    await expect(cancelledCard.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
    const cancelledId = Number((await cancelledCard.getByText(/Build #\d+/).first().textContent())?.match(/\d+/)?.[0]);
    await cancelledCard.getByRole("button", { name: "Cancel", exact: true }).click();
    await expect(cancelledCard).toContainText("Cancelled", { timeout: 15_000 });
    await cancelledCard.getByRole("button", { name: `Build #${cancelledId} actions` }).click();
    await page.getByRole("menuitem", { name: "Delete build" }).click();
    await page.getByRole("alertdialog", { name: `Build #${cancelledId}を削除` }).getByRole("button", { name: "Buildを削除" }).click();
    await expect(cancelledCard).toBeHidden();

    const webBuild = await page.evaluate(async (projectId) => {
      const response = await fetch(`/api/v1/application-projects/${projectId}/builds`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
        body: JSON.stringify({ targetId: "web", timeoutSeconds: 900 }),
      });
      if (!response.ok) throw new Error(await response.text());
      return await response.json() as { id: number };
    }, ids.projectId);
    const webResult = await expect.poll(async () => page.evaluate(async (id) => {
      const response = await fetch(`/api/v1/application-builds/${id}`, { credentials: "same-origin" });
      if (!response.ok) return { status: `http-${response.status}`, framework: "", binary: false };
      const build = await response.json() as { status: string; framework: string; artifacts: Array<{ kind: string }> };
      return { status: build.status, framework: build.framework, binary: build.artifacts.some((item) => item.kind === "binary") };
    }, webBuild.id), { timeout: 120_000, intervals: [500, 800, 1200] }).toEqual({
      status: "completed", framework: "aspnet-blazor", binary: true,
    });
    void webResult;
    const webLogs = await page.request.get(`/api/v1/application-builds/${webBuild.id}/logs`);
    expect(webLogs.ok()).toBe(true);
    expect((await webLogs.json() as { logs: string }).logs).toContain("Generated source self-test passed");
    expect((await page.request.delete(`/api/v1/application-builds/${webBuild.id}`, {
      headers: { "X-Requested-With": "ControlDeck" },
    })).status()).toBe(204);
    expect(browserErrors).toEqual([]);
  } finally {
    await page.evaluate(async ({ projectId, workflowId, active }) => {
      const headers = { "X-Requested-With": "ControlDeck" };
      const buildsResponse = await fetch(`/api/v1/application-projects/${projectId}/builds`, { credentials: "same-origin" });
      if (buildsResponse.ok) {
        const builds = await buildsResponse.json() as Array<{ id: number; status: string }>;
        for (const build of builds) {
          if (active.includes(build.status)) {
            await fetch(`/api/v1/application-builds/${build.id}/cancel`, { method: "POST", credentials: "same-origin", headers });
          }
          await fetch(`/api/v1/application-builds/${build.id}`, { method: "DELETE", credentials: "same-origin", headers });
        }
      }
      await fetch(`/api/v1/application-projects/${projectId}`, { method: "DELETE", credentials: "same-origin", headers });
      await fetch(`/api/v1/workflows/${workflowId}`, { method: "DELETE", credentials: "same-origin", headers });
    }, { ...ids, active: activeStatuses });
  }
});
