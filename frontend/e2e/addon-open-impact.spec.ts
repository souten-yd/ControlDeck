import { test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

test("opening the add-on and the metrics socket", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);
  await page.setViewportSize({ width: 390, height: 780 });

  const ws: string[] = [];
  page.on("websocket", (socket) => {
    const name = socket.url().split("/").slice(-1)[0].slice(0, 30);
    ws.push(`open ${name}`);
    socket.on("close", () => ws.push(`close ${name}`));
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(4000);
  const badge = () => page.evaluate(() => document.body.innerText.includes("再接続中"));
  console.log("ホーム表示後 再接続中:", await badge());

  // 画面内の遷移（利用者と同じ操作）
  const link = page.getByRole("link", { name: /オーディオ|Audio/ }).first();
  if (await link.count()) await link.click();
  else await page.evaluate(() => history.pushState({}, "", "/x/sonic-forge/workspace"));
  for (const wait of [1000, 2000, 4000, 8000]) {
    await page.waitForTimeout(wait);
    console.log(`アドオン表示 ${wait}ms 後 再接続中: ${await badge()}`);
  }
  console.log("WebSocket:", JSON.stringify(ws));
});
