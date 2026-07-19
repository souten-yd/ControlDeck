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
