import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

const pages = [
  ["/", "Home"],
  ["/apps", "Apps"],
  ["/runner", "Play"],
  ["/workflows", "Workflows"],
  ["/applications", "App Studio"],
  ["/project-lab", "Project Lab"],
  ["/remote", "Remote Desktop"],
  ["/files", "Files"],
  ["/terminal", "Terminal"],
  ["/github", "GitHub"],
  ["/knowledge", "Knowledge"],
  ["/models", "Models"],
  ["/logs", "Logs"],
  ["/system", "System"],
  ["/settings", "Settings"],
] as const;

test("uses one page-title layout without horizontal overflow", async ({ page }) => {
  test.setTimeout(90_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  await page.getByRole("button", { name: "More" }).click();
  const quickActions = page.getByRole("dialog", { name: "Quick Actions" });
  await expect(quickActions.getByRole("button", { name: "AI Assistant", exact: true })).toBeVisible();
  await quickActions.getByRole("button", { name: "AI Assistant", exact: true }).click();
  await expect(page).toHaveURL(/\/assistant$/);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    for (const [path, title] of pages) {
      await page.goto(path);
      const heading = page.getByRole("heading", { level: 1, name: title, exact: true });
      await expect(heading).toBeVisible();
      const layout = await heading.evaluate((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return {
          fontSize: style.fontSize,
          lineHeight: style.lineHeight,
          top: rect.top,
          viewport: window.innerWidth,
          document: document.documentElement.scrollWidth,
          body: document.body.scrollWidth,
        };
      });
      expect(layout.fontSize).toBe("20px");
      expect(layout.lineHeight).toBe("28px");
      expect(layout.top).toBeGreaterThanOrEqual(0);
      expect(layout.document, `${path} document overflow`).toBeLessThanOrEqual(layout.viewport);
      expect(layout.body, `${path} body overflow`).toBeLessThanOrEqual(layout.viewport);
    }
  }
});

test("keeps Play below the logo with an iPhone standalone safe area", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/runner");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  await page.evaluate(() => {
    document.documentElement.classList.add("pwa-standalone");
    document.documentElement.style.setProperty("--cd-safe-area-top", "47px");
  });

  const bounds = await page.evaluate(() => {
    const logo = document.querySelector("header svg");
    const page = document.querySelector("main > div");
    if (!logo || !page) throw new Error("Play shell was not rendered");
    const logoRect = logo.getBoundingClientRect();
    const pageRect = page.getBoundingClientRect();
    return { logoBottom: logoRect.bottom, pageTop: pageRect.top };
  });
  expect(bounds.pageTop).toBeGreaterThanOrEqual(bounds.logoBottom);
});

test("configures up to six mobile destinations while keeping More fixed", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/settings");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/settings");

  const settings = page.getByRole("region", { name: "Bottom Navigation" });
  await expect(settings.getByText("5 / 6", { exact: true })).toBeVisible();
  await settings.getByRole("button", { name: /Workflows/ }).click();
  await expect(settings.getByText("6 / 6", { exact: true })).toBeVisible();
  await expect(settings.getByRole("button", { name: /Files/ })).toBeDisabled();

  for (let index = 0; index < 5; index += 1) {
    await settings.getByRole("button", { name: "Workflowsを上へ移動" }).click();
  }
  await page.reload();

  const navigation = page.getByRole("navigation", { name: "Main navigation" });
  await expect(navigation.locator("a")).toHaveCount(6);
  await expect(navigation.locator("a").first()).toContainText("Workflows");
  await expect(navigation.getByRole("button", { name: "More" })).toBeVisible();
  const overflow = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  expect(overflow.document).toBeLessThanOrEqual(overflow.viewport);
});
