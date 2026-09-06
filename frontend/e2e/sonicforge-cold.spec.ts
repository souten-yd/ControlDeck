import { test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

test("opening the library right after the page appears", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);
  await page.setViewportSize({ width: 390, height: 780 });

  const log: string[] = [];
  page.on("response", (r) => {
    if (!r.url().includes("/addon-frame/sonic-forge/")) return;
    if (r.url().match(/\.(js|css|svg|png|woff2?)$/)) return;
    log.push(`${Date.now() % 100000} ${r.status()} ${r.url().split("/").slice(-1)[0].slice(0, 40)}`);
  });

  const opened = Date.now();
  await page.goto("/x/sonic-forge/workspace", { waitUntil: "domcontentloaded" });
  // frame が出たら即座にライブラリを押す（利用者と同じ操作）
  let frame;
  for (let i = 0; i < 60; i += 1) {
    frame = page.frames().find((f) => f.url().includes("/addon-frame/sonic-forge/"));
    if (frame) { try { if (await frame.locator("#library-grid").count()) break; } catch { /* まだ */ } }
    await page.waitForTimeout(200);
  }
  if (!frame) { console.log("frame なし"); return; }
  console.log(`frame まで ${Date.now() - opened}ms`);

  const tab = frame.getByRole("button", { name: /^Library$|^ライブラリ$/ }).first();
  await tab.click();
  const clicked = Date.now();
  const count = () => frame!.evaluate(() => document.querySelectorAll("#library-grid > *").length);
  for (let i = 0; i < 200; i += 1) {
    if (await count() > 0) break;
    await page.waitForTimeout(250);
  }
  console.log(`ライブラリ表示まで ${Date.now() - clicked}ms（${await count()} 件）`);
  for (const line of log.slice(0, 12)) console.log("  通信:", line);
});
