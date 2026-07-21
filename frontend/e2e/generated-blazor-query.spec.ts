import { expect, test } from "@playwright/test";

test("runs typed entity and API queries with safe filtering and pagination", async ({ page }) => {
  test.skip(process.env.CONTROL_DECK_GENERATED_GUI_E2E !== "1", "generated GUI server is required");
  const apiKey = process.env.CONTROL_DECK_GENERATED_GUI_API_KEY;
  test.skip(!apiKey, "CONTROL_DECK_GENERATED_GUI_API_KEY is required");

  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));
  let failFirstEntityQuery = true;
  await page.route("**/api/entities/projects?**", async (route) => {
    if (failFirstEntityQuery && route.request().method() === "GET") {
      failFirstEntityQuery = false;
      await route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"internal detail must not render"}' });
      return;
    }
    await route.continue();
  });

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/home");
  await page.getByLabel("Application API key").fill(apiKey!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Typed Query E6" })).toBeVisible();

  const projects = page.locator("#projects");
  const projectStatus = projects.locator(".table-status");
  await expect(projectStatus).toHaveText("Unable to load data. Select Refresh to try again.");
  await expect(projectStatus).not.toContainText("internal detail");
  await expect(projects.getByRole("table")).toHaveAttribute("aria-busy", "false");

  const items = page.locator("#items");
  await expect(items.locator(".table-status")).toHaveText("2 items.");
  await expect(items.getByRole("cell", { name: "API A" })).toBeVisible();
  await expect(items.getByRole("cell", { name: "API B" })).toBeVisible();
  errors.length = 0; // Initial session 401 and the deliberate query 503 are expected.

  const seedStatus = await page.evaluate(async () => {
    const rows = [
      { name: "A", rank: 1, active: true },
      { name: "B", rank: 3, active: true },
      { name: "C", rank: 2, active: true },
      { name: "<img src=x onerror=alert(1)> D", rank: 4, active: false },
    ];
    return Promise.all(rows.map(async (body) => (await fetch("/api/entities/projects", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "GeneratedApp" },
      body: JSON.stringify(body),
    })).status));
  });
  expect(seedStatus).toEqual([201, 201, 201, 201]);

  await projects.getByRole("button", { name: "Refresh" }).click();
  await expect(projectStatus).toHaveText("2 items.");
  await expect(projects.locator("tbody tr").nth(0)).toContainText("B3");
  await expect(projects.locator("tbody tr").nth(1)).toContainText("C2");
  await expect(projects).not.toContainText("onerror");

  await projects.getByRole("button", { name: "Next" }).click();
  await expect(projectStatus).toHaveText("1 item.");
  await expect(projects.getByRole("cell", { name: "A" })).toBeVisible();
  await expect(projects.getByRole("button", { name: "Next" })).toBeDisabled();
  await projects.getByRole("button", { name: "Previous" }).click();
  await expect(projects.locator("tbody tr").nth(0)).toContainText("B3");

  const invalidFilterStatus = await page.evaluate(async () => {
    const filter = encodeURIComponent(JSON.stringify([{ field: "name; DROP TABLE projects", operator: "eq", value: "x" }]));
    return (await fetch(`/api/entities/projects?limit=2&offset=0&filter=${filter}`, {
      credentials: "same-origin",
      headers: { "X-Requested-With": "GeneratedApp" },
    })).status;
  });
  expect(invalidFilterStatus).toBe(400);
  await expect(page.locator("tbody img")).toHaveCount(0);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    const overflow = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
    }));
    expect(overflow.document).toBeLessThanOrEqual(overflow.viewport);
    expect(overflow.body).toBeLessThanOrEqual(overflow.viewport);
  }
  expect(errors).toEqual([]);
});
