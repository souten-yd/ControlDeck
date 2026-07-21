import { expect, test } from "@playwright/test";

test("keeps generated typed client state in memory and updates safe consumers", async ({ page }) => {
  test.skip(process.env.CONTROL_DECK_GENERATED_GUI_E2E !== "1", "generated GUI server is required");
  const apiKey = process.env.CONTROL_DECK_GENERATED_GUI_API_KEY;
  test.skip(!apiKey, "CONTROL_DECK_GENERATED_GUI_API_KEY is required");
  const errors: string[] = [];
  page.on("console", (message) => message.type() === "error" && errors.push(message.text()));
  page.on("pageerror", (error) => errors.push(error.message));

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/home");
  await page.getByLabel("Application API key").fill(apiKey!);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Typed State" })).toBeVisible();
  errors.length = 0;

  const sharedInput = page.getByLabel("Shared query");
  await expect(sharedInput).toHaveValue("Initial query");
  await expect(page.locator("#query-copy")).toHaveText("Initial query");
  await sharedInput.fill("<img src=x onerror=alert(1)> Edited");
  await expect(page.locator("#query-copy")).toHaveText("<img src=x onerror=alert(1)> Edited");
  await expect(page.locator("#query-copy img")).toHaveCount(0);

  const success = page.locator('form[data-endpoint="/api/run"]');
  await success.getByLabel("query").fill("Stored result");
  await success.getByRole("button", { name: "Run and store result" }).click();
  await expect(success.getByText("Completed.", { exact: true })).toBeVisible();
  await expect(page.locator("#result")).toHaveText('{"result":"Stored result"}');

  const failure = page.locator('form[data-endpoint="/api/fail"]');
  await failure.getByLabel("query").fill("Expected failure");
  await failure.getByRole("button", { name: "Run expected failure" }).click();
  await expect(failure.getByText("Request failed (500).", { exact: true })).toBeVisible();
  await expect(page.locator("#failure")).toHaveText('{"status":500,"message":"Request failed."}');
  errors.length = 0; // The deliberate HTTP 500 is expected; subsequent UI work must stay clean.

  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  const overflow = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  expect(overflow.document).toBeLessThanOrEqual(overflow.viewport);
  expect(overflow.body).toBeLessThanOrEqual(overflow.viewport);

  await page.reload();
  await expect(page.getByLabel("Shared query")).toHaveValue("Initial query");
  await expect(page.locator("#result")).toHaveText('{"result":"Not run"}');
  await expect(page.locator("#failure")).toHaveText("{}");
  expect(errors).toEqual([]);
});
