import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** Settings →「API キー」の「調査（フォーラム・検索）」群: Reddit のキーを保存し、「つながりを確かめる」の結果が 1 行で出る。
 *  根拠: 2026-11-23 利用者「Reddit の API を登録するところを作って、入っていたらそれを使って検索できるように。API の登録のところにまとめて」。
 *  守ること: 群が出る・値は画面に出ない・Reddit に断られたときは「承認」と平易に言う・320px で横にはみ出さない。
 *  API は page.route でモックする（Reddit へは繋がない・本番のキーも変えない）。 */
const field = (name: string, label: string, configured: boolean, plain = false) => ({ name, label, configured, plain });

for (const width of [1280, 320]) {
  test(`Settings: 調査のキー（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    let saved = false;
    const state = () => ({
      groups: [{ id: "research", label: "調査（フォーラム・検索）", description: "AI アシスタントが Reddit・フォーラムを調べるときのキー" }],
      keys: [{
        id: "research:reddit", group: "research", name: "Reddit", note: "https://www.reddit.com/prefs/apps で「script」か「web app」を作る。承認が要る場合がある",
        signup: "https://www.reddit.com/prefs/apps", configured: saved, hint: saved ? "••••1234" : "", testable: true, used_by: "AI アシスタントの調査", manage: "",
        fields: [field("reddit_client_id", "Client ID", saved), field("reddit_client_secret", "Client Secret", saved),
                 field("reddit_username", "Reddit のユーザー名", saved, true)],
      }],
    });
    await page.route("**/api/v1/settings/keys**", async (route) => {
      const request = route.request();
      if (request.method() === "PUT") { saved = true; return route.fulfill({ json: state() }); }
      if (request.method() === "POST") return route.fulfill({ json: { ok: false, message: "Reddit に断られました。このアプリは Reddit の承認（API の利用の申請）が要る可能性があります" } });
      return route.fulfill({ json: state() });
    });
    await page.goto("/settings/keys");
    await expect(page.getByRole("heading", { name: "調査（フォーラム・検索）", exact: true })).toBeVisible();
    const card = page.locator("li", { hasText: "Reddit" }).first();
    await card.getByRole("button", { name: "設定" }).click();
    const secret = `reddit-e2e-${width}-secret`;
    await card.getByLabel("Client ID").fill("clientid1234");
    await card.getByLabel("Client Secret").fill(secret);
    await card.getByLabel("Reddit のユーザー名").fill("someone");
    await card.getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("Reddit のキーを保存しました")).toBeVisible();
    await expect(card.getByText("設定済み")).toBeVisible();
    await expect(page.getByText(secret)).toHaveCount(0);
    await card.getByRole("button", { name: "変更" }).click();
    await card.getByRole("button", { name: "つながりを確かめる" }).click();
    await expect(card.getByRole("status")).toContainText("承認");
    await page.screenshot({ path: `test-results/settings-keys-research-${width}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
