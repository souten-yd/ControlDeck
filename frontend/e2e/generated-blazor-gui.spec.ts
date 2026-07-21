import { expect, test } from "@playwright/test";

test("renders generated Blazor Entity UI at mobile and desktop widths", async ({ page }) => {
  test.skip(process.env.CONTROL_DECK_GENERATED_GUI_E2E !== "1", "generated GUI server is required");
  const apiKey = process.env.CONTROL_DECK_GENERATED_GUI_API_KEY;
  test.skip(!apiKey, "CONTROL_DECK_GENERATED_GUI_API_KEY is required");
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/home");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Application API key").fill(apiKey!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
  expect(await page.evaluate(() => document.cookie)).not.toContain("ControlDeckGeneratedSession");

  const csrfResponse = await page.evaluate(async () => (await fetch("/api/projects", {
    method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: "Blocked" }),
  })).status);
  expect(csrfResponse).toBe(401);
  browserErrors.length = 0;

  await page.getByRole("button", { name: "Add item" }).click();
  await page.getByLabel("name", { exact: true }).fill("Mobile Project");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("cell", { name: "Mobile Project" })).toBeVisible();
  const mobileRow = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "Mobile Project" }) });
  await mobileRow.getByText("More", { exact: true }).click();
  await mobileRow.getByRole("button", { name: "Edit" }).click();
  await page.getByLabel("name", { exact: true }).fill("Updated Project");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("cell", { name: "Updated Project" })).toBeVisible();

  await page.getByLabel("Message").fill("Mobile workflow result");
  await page.getByLabel("mode").selectOption("short");
  await page.getByLabel("count").fill("2");
  await page.getByLabel("ratio").fill("0.5");
  await expect(page.getByLabel("Enabled")).not.toBeChecked();
  await page.getByLabel("Options").fill('{"source":"mobile"}');
  await page.getByLabel("Items").fill('["one","two"]');
  await page.getByRole("button", { name: "Run workflow" }).click();
  await expect(page.getByText("Completed.", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Workflow result" })).toContainText("Mobile workflow result");

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/home");
    await expect(page.getByRole("heading", { level: 1, name: "Projects" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Updated Project" })).toBeVisible();
    const layout = await page.evaluate(() => ({
      viewport: window.innerWidth,
      document: document.documentElement.scrollWidth,
      body: document.body.scrollWidth,
      inputSafeAreaRule: [...document.styleSheets].some((sheet) =>
        [...sheet.cssRules].some((rule) => rule.cssText.includes("safe-area-inset-bottom")),
      ),
    }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
    expect(layout.body).toBeLessThanOrEqual(layout.viewport);
    expect(layout.inputSafeAreaRule).toBe(true);
  }
  page.once("dialog", (dialog) => dialog.accept());
  const updatedRow = page.getByRole("row").filter({ has: page.getByRole("cell", { name: "Updated Project" }) });
  await updatedRow.getByText("More", { exact: true }).click();
  await updatedRow.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByRole("cell", { name: "Updated Project" })).toHaveCount(0);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(browserErrors).toEqual([]);
});
