import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

test("long-running app remains readable without status or action overlap in dark mode", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("cd-theme", "dark");
    localStorage.setItem("cd-oled", "1");
  });
  await page.route("**/api/v1/apps", async (route) => {
    const response = await route.fetch();
    const apps = await response.json() as Array<Record<string, any>>;
    const target = apps.find((app) => app.name === "FrameDeck") ?? apps.find((app) => !app.system_managed);
    if (target) {
      target.name = "FrameDeck";
      target.runtime = {
        ...target.runtime,
        status: "RUNNING",
        uptime_seconds: 9_876_543,
        started_at: "2026-03-28T01:23:00+00:00",
        cpu_percent: 12.4,
        memory_bytes: 1_234_567_890,
      };
    }
    await route.fulfill({ response, json: apps });
  });

  await login(page);
  for (const viewport of [{ width: 1280, height: 800 }, { width: 320, height: 720 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/apps");
    const card = page.locator("[data-app-card]").filter({ hasText: "FrameDeck" }).first();
    await expect(card).toBeVisible();
    const visibleStatus = card.locator("span.text-xs:visible").filter({ hasText: /^実行中$/ });
    await expect(visibleStatus).toHaveCount(1);
    await expect(card.getByText(/稼働 114日 7時間/)).toBeVisible();
    await expect(card.getByText(/開始 3\/28 10:23/)).toBeVisible();

    const styles = await card.evaluate((element) => {
      const cardStyle = getComputedStyle(element);
      const runtime = element.querySelector<HTMLElement>("[data-app-runtime]")!;
      const status = Array.from(element.querySelectorAll<HTMLElement>("span")).find((item) => item.textContent?.trim() === "実行中")!;
      return {
        cardBackground: cardStyle.backgroundColor,
        cardColor: cardStyle.color,
        runtimeBackground: getComputedStyle(runtime).backgroundColor,
        runtimeColor: getComputedStyle(runtime).color,
        statusWhiteSpace: getComputedStyle(status).whiteSpace,
      };
    });
    expect(styles.cardBackground).not.toBe("rgba(0, 0, 0, 0)");
    expect(styles.cardColor).not.toBe("rgb(0, 0, 0)");
    expect(styles.runtimeBackground).toBe("rgba(0, 0, 0, 0)");
    expect(styles.runtimeColor).not.toBe("rgb(0, 0, 0)");
    expect(styles.statusWhiteSpace).toBe("nowrap");

    const geometry = await card.evaluate((element) => {
      const runtime = element.querySelector<HTMLElement>("[data-app-runtime]")!.getBoundingClientRect();
      const actions = element.querySelector<HTMLElement>("[data-app-actions]")!.getBoundingClientRect();
      return {
        runtimeTop: runtime.top,
        runtimeRight: runtime.right,
        actionsTop: actions.top,
        actionsLeft: actions.left,
        right: element.getBoundingClientRect().right,
        height: element.getBoundingClientRect().height,
      };
    });
    expect(Math.abs(geometry.runtimeTop - geometry.actionsTop)).toBeLessThanOrEqual(0.5);
    expect(geometry.runtimeRight).toBeLessThanOrEqual(geometry.actionsLeft);
    expect(geometry.right).toBeLessThanOrEqual(viewport.width + 0.5);
    expect(geometry.height).toBeLessThanOrEqual(150);
  }
});
