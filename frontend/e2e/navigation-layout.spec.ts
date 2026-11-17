import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

const pages = [
  ["/", "Home"],
  ["/apps", "Apps"],
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
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  // 下部ナビは無い。左上の ☰ で開くドロワーが唯一の主なナビ。
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "More" })).toHaveCount(0);
  const menuButton = page.getByRole("button", { name: "メニューを開く" });
  const menuBox = (await menuButton.boundingBox())!;
  expect(menuBox.width).toBeGreaterThanOrEqual(44);
  expect(menuBox.height).toBeGreaterThanOrEqual(44);
  expect(menuBox.x).toBeLessThan(16);
  await menuButton.click();
  const drawer = page.getByRole("dialog", { name: "メニュー" });
  await expect(drawer).toHaveAttribute("aria-modal", "true");
  await expect(drawer.locator('[aria-current="page"]')).toHaveText("Home");
  // 同じ行き先は 1 回だけ並ぶ。
  await expect(drawer.getByRole("link", { name: "AI Assistant", exact: true })).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(drawer).toHaveCount(0);
  await expect(menuButton).toBeFocused();
  await menuButton.click();
  await drawer.getByRole("link", { name: "AI Assistant", exact: true }).click();
  await expect(page).toHaveURL(/\/assistant$/);
  await expect(drawer).toHaveCount(0);
  // アシスタントも同じ ☰ から同じドロワーを開く。
  await page.getByRole("button", { name: "メニューを開く" }).click();
  await expect(drawer.locator('[aria-current="page"]')).toHaveText("AI Assistant");
  await page.keyboard.press("Escape");

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
      // 題名は殻の上の行（☰ の右）に 1 つだけ出る。
      await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
      expect(await heading.evaluate((element) => !!element.closest("[data-shell-topbar]"))).toBe(true);
      expect(layout.fontSize).toBe("18px");
      expect(layout.lineHeight).toBe("28px");
      expect(layout.top).toBeGreaterThanOrEqual(0);
      expect(layout.document, `${path} document overflow`).toBeLessThanOrEqual(layout.viewport);
      expect(layout.body, `${path} body overflow`).toBeLessThanOrEqual(layout.viewport);
    }
  }
});

test("keeps Workflows below the logo with an iPhone standalone safe area", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  await page.evaluate(() => {
    document.documentElement.classList.add("pwa-standalone");
    document.documentElement.style.setProperty("--cd-safe-area-top", "47px");
  });

  const bounds = await page.evaluate(() => {
    const logo = document.querySelector("header svg");
    const page = document.querySelector("main > div");
    if (!logo || !page) throw new Error("Workflows shell was not rendered");
    const logoRect = logo.getBoundingClientRect();
    const pageRect = page.getBoundingClientRect();
    return { logoBottom: logoRect.bottom, pageTop: pageRect.top };
  });
  expect(bounds.pageTop).toBeGreaterThanOrEqual(bounds.logoBottom);
});

test("migrates the saved Play destination to Workflows", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.addInitScript(() => {
    localStorage.setItem("cd-mobile-navigation-v1", JSON.stringify(["/", "/apps", "/runner", "/terminal", "/assistant"]));
  });
  await page.goto("/workflows");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "メニューを開く" }).click();
  const pinned = page.getByRole("dialog", { name: "メニュー" }).getByRole("group", { name: "よく使う" });
  await expect(pinned.getByRole("link", { name: "Workflows" })).toBeVisible();
  await expect(pinned.getByRole("link", { name: "Play" })).toHaveCount(0);
  await expect(pinned.getByRole("link")).toHaveCount(5);
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("cd-mobile-navigation-v1") ?? "[]"));
  expect(stored).not.toContain("/runner");
  expect(stored).toContain("/workflows");
});

test("pins up to six favorites at the top of the drawer", async ({ page }) => {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/settings");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン", exact: true }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();
  await page.goto("/settings/appearance");

  const settings = page.getByRole("region", { name: "よく使う機能" });
  await expect(settings.getByText("5 / 6", { exact: true })).toBeVisible();
  await settings.getByRole("button", { name: /Files/ }).click();
  await expect(settings.getByText("6 / 6", { exact: true })).toBeVisible();
  await expect(settings.getByRole("button", { name: /GitHub/ })).toBeDisabled();

  for (let index = 0; index < 2; index += 1) {
    await settings.getByRole("button", { name: "Workflowsを上へ移動" }).click();
  }
  await page.reload();

  await page.getByRole("button", { name: "メニューを開く" }).click();
  const drawer = page.getByRole("dialog", { name: "メニュー" });
  const pinned = drawer.getByRole("group", { name: "よく使う" });
  await expect(pinned.getByRole("link")).toHaveCount(6);
  await expect(pinned.getByRole("link").first()).toContainText("Workflows");
  // 固定した機能は、まとまりの側には重ねて出さない。
  await expect(drawer.getByRole("link", { name: "Workflows", exact: true })).toHaveCount(1);
  const overflow = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
  expect(overflow.document).toBeLessThanOrEqual(overflow.viewport);
});
