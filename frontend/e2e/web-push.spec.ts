// 目的:
// - 設定 → 通知 の「この端末で受け取る」で、ブラウザの購読をサーバーへ登録し、一覧に端末が出る。止めると消える。
// - Service Worker が push を受けたら通知を出し、押したら中の url（実行結果など）を開く。
//   push service は外にあるので、購読は差し替え、push は Chromium の DevTools から直接 Service Worker へ届ける。
// - push を受けたら、番号を添えて「受け取った」をサーバーへ返す（2026-11-21「届いていない気がする」）。
// 根拠: 2026-11-21 利用者の依頼（通知の片方向を塞ぐ）。本番は通知先が 0 件で、何も届いていなかった。
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

test("subscribes this device, shows it, and the service worker shows pushed notifications", async ({ page, context, browserName }) => {
  test.skip(!hasSession(), "E2E credentials required");
  test.skip(browserName !== "chromium", "push の配達は Chromium の DevTools でだけ試せる");
  await context.grantPermissions(["notifications"]);
  // 本物の push service へは行かない。受け手の鍵だけは本物の形にする（サーバーが形を確かめる）
  await context.addInitScript(() => {
    let current: unknown = null;
    const b64 = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    const make = async () => {
      const pair = await crypto.subtle.generateKey({ name: "ECDH", namedCurve: "P-256" }, true, ["deriveBits"]);
      const raw = new Uint8Array(await crypto.subtle.exportKey("raw", pair.publicKey));
      const auth = crypto.getRandomValues(new Uint8Array(16));
      const endpoint = `https://fcm.googleapis.com/fcm/send/e2e-${Date.now()}`;
      const sub = {
        endpoint,
        toJSON: () => ({ endpoint, keys: { p256dh: b64(raw), auth: b64(auth) } }),
        unsubscribe: async () => { current = null; return true; },
      };
      current = sub;
      return sub;
    };
    PushManager.prototype.getSubscription = async function () { return current as PushSubscription | null; };
    PushManager.prototype.subscribe = async function () { return (await make()) as unknown as PushSubscription; };
  });
  await establishSession(page, context);
  await page.goto("/settings/notifications");
  const panel = page.locator("[data-push-devices]");
  await expect(panel).toBeVisible();
  await panel.getByRole("button", { name: "この端末で受け取る" }).click();
  await expect(panel.locator("[data-push-state='on']")).toBeVisible();
  await expect(panel.locator("[data-push-device-list] li")).toHaveCount(1);
  await page.screenshot({ path: "test-results/web-push-settings.png" });

  // Service Worker へ push を届け、通知に渡す中身（題・本文・押したときの行き先）を確かめる。
  // headless の Chromium は通知を出せないので、Service Worker の中の showNotification を差し替えて控える。
  const worker = context.serviceWorkers()[0] ?? await context.waitForEvent("serviceworker");
  await worker.evaluate(() => {
    const scope = self as unknown as { registration: ServiceWorkerRegistration; __shown: unknown[] };
    scope.__shown = [];
    scope.registration.showNotification = async (title: string, options?: NotificationOptions) => {
      scope.__shown.push({ title, body: options?.body, tag: options?.tag, url: (options?.data as { url?: string })?.url });
    };
  });
  const cdp = await context.newCDPSession(page);
  const registrationId = new Promise<string>((resolve) => {
    cdp.on("ServiceWorker.workerRegistrationUpdated", (event) => {
      const found = event.registrations.find((item) => !item.isDeleted);
      if (found) resolve(found.registrationId);
    });
  });
  await cdp.send("ServiceWorker.enable");
  const origin = new URL(page.url()).origin;
  const deliver = async (data: string) => cdp.send("ServiceWorker.deliverPushMessage", { origin, registrationId: await registrationId, data });
  const receipt = page.context().waitForEvent("request", (request) => request.url().endsWith("/api/v1/push/receipts"));
  await deliver(JSON.stringify({ title: "Demo: test が完了しました", body: "実行 #3", url: "/project-lab?project=demo&run=3", tag: "project-run-3", rid: "e2e-receipt-1" }));
  // 受け取ったら番号を添えてサーバーへ返す（届いたかを画面で確かめるため）
  expect((await receipt).postDataJSON()).toEqual({ rid: "e2e-receipt-1", shown: true, error: "" });
  // 外への行き先は使わない（中のページへだけ）。読めない中身でも何か出す（iOS は出さないと購読を外す）
  await deliver(JSON.stringify({ title: "x", url: "//evil.example/" }));
  await deliver("not json");
  await expect.poll(() => worker.evaluate(() => (self as unknown as { __shown: unknown[] }).__shown)).toEqual([
    { title: "Demo: test が完了しました", body: "実行 #3", tag: "project-run-3", url: "/project-lab?project=demo&run=3" },
    { title: "x", body: "", url: "/" },
    { title: "ControlDeck", body: "not json", url: "/" },
  ]);

  // 止めると、サーバーの一覧からも消える
  await panel.getByRole("button", { name: "止める" }).click();
  await expect(panel.getByRole("button", { name: "この端末で受け取る" })).toBeVisible();
  await expect(panel.locator("[data-push-device-list]")).toHaveCount(0);

  for (const width of [320, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  }
});

test("opening a notification link selects the run in Project Lab", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials required");
  await establishSession(page, context);
  await page.goto("/project-lab?project=no-such-project&run=5");
  // 通知の印（query）は読んだら消す。戻る・再読み込みで同じ実行を開き直さない
  await expect.poll(() => new URL(page.url()).search).toBe("");
});
