import { createHmac } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

function totpCode(base32: string): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const character of base32.replace(/=+$/, "").toUpperCase()) {
    bits += alphabet.indexOf(character).toString(2).padStart(5, "0");
  }
  const bytes = Buffer.alloc(Math.floor(bits.length / 8));
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = Number.parseInt(bits.slice(index * 8, index * 8 + 8), 2);
  }
  const counter = Math.floor(Date.now() / 1000 / 30);
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const digest = createHmac("sha1", bytes).update(message).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary = ((digest[offset] & 0x7f) << 24)
    | (digest[offset + 1] << 16)
    | (digest[offset + 2] << 8)
    | digest[offset + 3];
  return String(binary % 1_000_000).padStart(6, "0");
}

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).toHaveURL(/\/settings$/);
}

test("required TOTP policy confines navigation until enrollment", async ({ page }) => {
  const browserErrors: string[] = [];
  const sockets: string[] = [];
  page.on("pageerror", (error) => browserErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("websocket", (socket) => sockets.push(socket.url()));
  await login(page);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/apps");
    await expect(page).toHaveURL(/\/settings$/);
    await expect(page.getByText("二要素認証の設定が必要です")).toBeVisible();
    const layout = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
    expect(layout.document).toBeLessThanOrEqual(layout.viewport);
  }
  expect(sockets.some((url) => url.includes("/system/metrics/stream"))).toBe(false);
  const blocked = await page.request.get("/api/v1/apps");
  expect(blocked.status()).toBe(403);
  expect((await blocked.json()).detail).toBe("totp_setup_required");

  await page.setViewportSize({ width: 320, height: 700 });
  await page.getByRole("button", { name: "有効化" }).click();
  const dialog = page.getByRole("dialog", { name: "二要素認証の設定" });
  const secret = (await dialog.locator("code, p.font-mono").first().innerText()).trim();
  await dialog.getByPlaceholder("000000").fill(totpCode(secret));
  await dialog.getByRole("button", { name: "確認して有効化" }).click();
  const recovery = page.getByRole("dialog", { name: "リカバリーコード" });
  await expect(recovery).toBeVisible();
  await recovery.getByRole("button", { name: "保管しました" }).click();

  await expect(page.getByText("外観", { exact: true })).toBeVisible();
  await expect(page.getByText("ポリシーにより必須")).toBeVisible();
  await expect(page.getByRole("button", { name: "無効化" })).toHaveCount(0);
  expect((await page.request.get("/api/v1/apps")).status()).toBe(200);
  await expect.poll(() => sockets.some((url) => url.includes("/system/metrics/stream"))).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  expect(browserErrors).toEqual([]);
});
