import { expect, test } from "@playwright/test";

test("uses generated fixed success and error page navigation at 320px", async ({ page }) => {
  test.skip(process.env.CONTROL_DECK_GENERATED_GUI_E2E !== "1", "generated GUI server is required");
  const apiKey = process.env.CONTROL_DECK_GENERATED_GUI_API_KEY;
  test.skip(!apiKey, "CONTROL_DECK_GENERATED_GUI_API_KEY is required");

  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/home");
  await page.getByLabel("Application API key").fill(apiKey!);
  await page.getByRole("button", { name: "Sign in" }).click();

  const successForm = page.locator('form[data-endpoint="/api/run-success"]');
  await successForm.getByLabel("Message").fill("Navigate safely");
  await successForm.getByRole("button", { name: "Run and open result" }).click();
  await expect(page).toHaveURL(/\/done$/);
  await expect(page.getByRole("heading", { level: 1, name: "Done" })).toBeVisible();

  await page.goto("/home");
  const errorForm = page.locator('form[data-endpoint="/api/run-error"]');
  await errorForm.getByLabel("Message").fill("Expected schema failure");
  await errorForm.getByRole("button", { name: "Run and handle error" }).click();
  await expect(page).toHaveURL(/\/errors$/);
  await expect(page.getByRole("heading", { level: 1, name: "Errors" })).toBeVisible();

  const overflow = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth }));
  expect(overflow.document).toBeLessThanOrEqual(overflow.viewport);
  expect(overflow.body).toBeLessThanOrEqual(overflow.viewport);
});
