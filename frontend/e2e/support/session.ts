import { readFileSync } from "node:fs";
import type { BrowserContext, Page } from "@playwright/test";

/** e2e にログイン状態を与える。
 *
 * 通常は利用者の資格情報を使う。手元で調査するときはパスワードが無いこともあるので、
 * サーバー側で払い出したセッショントークンのファイルでも入れるようにしている。
 * どちらも無ければ呼び出し側で skip する。 */
export function hasSession(): boolean {
  return Boolean(process.env.CONTROL_DECK_E2E_TOKEN_FILE
    || (process.env.CONTROL_DECK_E2E_USER && process.env.CONTROL_DECK_E2E_PASSWORD));
}

export async function establishSession(page: Page, context: BrowserContext): Promise<void> {
  const tokenFile = process.env.CONTROL_DECK_E2E_TOKEN_FILE;
  if (tokenFile) {
    const url = process.env.CONTROL_DECK_E2E_URL ?? "http://127.0.0.1:8765";
    await context.addCookies([{
      name: "cd_session",
      value: readFileSync(tokenFile, "utf8").trim(),
      url,
      httpOnly: true,
      sameSite: "Lax",
    }]);
    return;
  }
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(process.env.CONTROL_DECK_E2E_USER!);
  await page.getByLabel("パスワード").fill(process.env.CONTROL_DECK_E2E_PASSWORD!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await page.waitForURL((current) => !current.pathname.startsWith("/login"));
}
