import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

const LONG = "const veryLongLine = '" + "x".repeat(160) + "';";
const CODE = `def hello():\n    print("こんにちは")\n${LONG}`;
const answer = `次のコードです。\n\n\`\`\`python\n${CODE}\n\`\`\`\n\n以上。`;

const msg = (id: string, role: "user" | "assistant", content: string) => ({
  id, role, content, thinking: "", status: "completed", job_id: null, model: "", error: "", meta: {},
  created_at: "2026-11-21T00:00:00Z",
});

for (const width of [1280, 320]) {
  test(`code block copy button copies the whole code (${width}px)`, async ({ page, context }, testInfo) => {
    test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.setViewportSize({ width, height: 800 });
    await page.goto("/assistant");
    await page.getByLabel("ユーザー名").fill(username!);
    await page.getByLabel("パスワード").fill(password!);
    await page.getByRole("button", { name: "ログイン", exact: true }).click();
    await expect(page.getByLabel("ユーザー名")).toBeHidden();
    await page.route(/\/api\/v1\/chat\/conversations\/code1\/messages$/, (route) =>
      route.fulfill({ json: { conversation: { id: "code1", title: "コード" }, messages: [msg("u1", "user", "書いて"), msg("a1", "assistant", answer)] } }));
    await page.evaluate(() => localStorage.setItem("cd-chat-conversation", "code1"));
    await page.goto("/assistant");
    const shell = page.locator("[data-assistant-shell]");
    const button = shell.getByRole("button", { name: "コードをコピー" });
    await expect(button).toBeVisible();

    // 押せる範囲は 44px 以上（見た目は小さい）。画面からはみ出さず、コードの文字に重ならない。
    const box = (await button.boundingBox())!;
    const tap = await button.evaluate((el) => {
      const r = el.getBoundingClientRect();
      const b = getComputedStyle(el, "::before");
      return { w: r.width + parseFloat(b.left) * -2, h: r.height + parseFloat(b.top) * -2 };
    });
    expect(tap.w).toBeGreaterThanOrEqual(40);
    expect(tap.h).toBeGreaterThanOrEqual(44);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    const preBox = (await shell.locator("pre").boundingBox())!;
    expect(box.y + box.height).toBeLessThanOrEqual(preBox.y + 1);

    // 横に長いコードを動かしても、ボタンは動かない
    await shell.locator("pre").evaluate((el) => { el.scrollLeft = 200; });
    const after = (await button.boundingBox())!;
    expect(Math.abs(after.x - box.x)).toBeLessThan(1);

    await button.click();
    await expect(shell.getByText("コピーしました", { exact: true })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(CODE);
    await page.screenshot({ path: testInfo.outputPath(`code-copy-${width}.png`) });
    await page.screenshot({ path: `test-results/code-copy-${width}.png` });

    await page.emulateMedia({ colorScheme: "dark" });
    await page.screenshot({ path: `test-results/code-copy-${width}-dark.png` });
    await expect(shell.getByText("コピーしました", { exact: true })).toBeHidden({ timeout: 4000 });
  });
}
