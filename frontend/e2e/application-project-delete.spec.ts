import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

test("App Studio deletes a project through the secondary menu with confirmation", async ({ page }) => {
  test.setTimeout(60_000);
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/applications");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page.getByLabel("ユーザー名")).toBeHidden();

  const project = await page.evaluate(async () => {
    const response = await fetch("/api/v1/application-projects", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "ControlDeck" },
      body: JSON.stringify({ name: "Delete UX verification" }),
    });
    if (!response.ok) throw new Error(await response.text());
    return await response.json() as { id: number; name: string };
  });

  try {
    await page.goto("/applications");
    const card = page.getByRole("article").filter({ hasText: project.name });
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: `${project.name} menu` }).click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    const dialog = page.getByRole("alertdialog", { name: `「${project.name}」を削除しますか？` });
    await expect(dialog).toContainText("この操作は取り消せません");
    await dialog.getByRole("button", { name: "削除する" }).click();
    await expect(card).toBeHidden();
    await expect(page.getByText(`「${project.name}」を削除しました`)).toBeVisible();
    await expect.poll(() => page.evaluate(async (id) => (await fetch(`/api/v1/application-projects/${id}`)).status, project.id)).toBe(404);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBe(0);
  } finally {
    await page.evaluate(async (id) => {
      await fetch(`/api/v1/application-projects/${id}`, {
        method: "DELETE", credentials: "same-origin", headers: { "X-Requested-With": "ControlDeck" },
      });
    }, project.id);
  }
});
