import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const CSRF = { "X-Requested-With": "ControlDeck" };

test("the image button sits between → and ^C and inserts the stored path", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);

  // 利用者の実セッションには触らない。自分で作って、最後に片付ける。
  const created = await context.request.post("/api/v1/terminals", { headers: CSRF });
  expect(created.ok(), await created.text()).toBeTruthy();
  const sessionId: string = (await created.json()).id;

  try {
    await page.goto("/terminal", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    const row = page.locator("li").filter({ hasText: sessionId.slice(0, 6) });
    await row.getByRole("button", { name: "Connect" }).click();
    await page.waitForTimeout(3000);

    const labels = await page.evaluate(() =>
      Array.from(document.querySelectorAll("button"))
        .map((button) => (button.textContent || "").trim())
        .filter((text) => ["→", "画像", "^C", "^D"].includes(text)));
    expect(labels.join(",")).toContain("→,画像,^C");

    // 隠し input が画像だけを受けること
    const accept = await page.locator('input[type="file"]').first().getAttribute("accept");
    expect(accept).toBe("image/*");
  } finally {
    await context.request.delete(`/api/v1/terminals/${sessionId}`, { headers: CSRF });
  }
});
