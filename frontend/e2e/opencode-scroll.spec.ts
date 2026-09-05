import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

const CSRF = { "X-Requested-With": "ControlDeck" };

/** tmux の中では buffer.active.type が normal のままなので、alternate かどうかで
 *  判断すると指でのスクロールが常に空振りする。mouse tracking の有無で決める。 */
for (const { label, command, wantsMouse } of [
  { label: "mouse を使う TUI", command: "seq 1 500 | less -R --mouse", wantsMouse: true },
  { label: "mouse を使わない TUI", command: "seq 1 500 | less -R", wantsMouse: false },
])
test(`scrolling: ${label}`, async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await establishSession(page, context);
  const created = await context.request.post("/api/v1/terminals", { headers: CSRF });
  const id: string = (await created.json()).id;

  try {
    await page.goto("/terminal", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1500);
    await page.locator("li").filter({ hasText: id.slice(0, 6) })
      .getByRole("button", { name: "Connect" }).click();
    await page.waitForTimeout(2500);

    // 先に端末へ focus を入れないと入力が届かない
    const screenEl = page.locator(".xterm-screen").first();
    await screenEl.click({ position: { x: 20, y: 20 } });
    await page.waitForTimeout(600);
    await page.keyboard.type(command);
    await page.keyboard.press("Enter");
    await page.waitForTimeout(3500);

    const screen = page.locator(".xterm-screen").first();
    const read = () => page.evaluate(() =>
      (document.querySelector(".xterm-screen") as HTMLElement)?.innerText.slice(0, 120) ?? "");
    const before = await read();
    console.log("less が起動したか:", before.includes("1") && !before.includes("$"));
    console.log("スクロール前:", JSON.stringify(before.split("\n")[0]));

    // 送信内容を覗く
    await page.evaluate(() => {
      (window as any).__sent = [];
      const original = WebSocket.prototype.send;
      WebSocket.prototype.send = function (data: any) {
        try { (window as any).__sent.push(String(data).slice(0, 120)); } catch { /* ignore */ }
        return original.call(this, data);
      };
    });
    const box = await screen.boundingBox();
    // 携帯と同じ指の操作。上へ払って先へ送る。
    const x = box!.x + box!.width / 2;
    const from = box!.y + box!.height * 0.75;
    await page.evaluate(async ({ x, from, height }) => {
      const target = document.querySelector(".xterm-screen") as HTMLElement;
      const make = (type: string, clientY: number) => {
        const touch = new Touch({ identifier: 1, target, clientX: x, clientY });
        return new TouchEvent(type, {
          touches: type === "touchend" ? [] : [touch],
          targetTouches: type === "touchend" ? [] : [touch],
          changedTouches: [touch], bubbles: true, cancelable: true,
        });
      };
      const wait = () => new Promise((r) => setTimeout(r, 60));
      target.dispatchEvent(make("touchstart", from));
      await wait();
      for (let step = 1; step <= 8; step += 1) {
        target.dispatchEvent(make("touchmove", from - (height * 0.05 * step)));
        await wait();
      }
      target.dispatchEvent(make("touchend", from - height * 0.4));
    }, { x, from, height: box!.height });
    await page.waitForTimeout(2500);
    console.log("送信:", JSON.stringify(await page.evaluate(() => (window as any).__sent.slice(-6))));
    const after = await read();
    console.log("スクロール後:", JSON.stringify(after.split("\n")[0]));
    const sent = await page.evaluate(() => (window as any).__sent as string[]);
    if (wantsMouse) {
      // 触った場所へ wheel を送り、アプリ自身の履歴が動くこと
      expect(sent.length, "アプリへ何も送っていない").toBeGreaterThan(0);
      expect(after, "指で動かしても表示が変わらない").not.toBe(before);
    } else {
      // 受け取らない相手へ送ると echo されて文字が重なる。送らないこと。
      expect(sent, "mouse を使わないアプリへ送ってはいけない").toEqual([]);
    }
  } finally {
    await context.request.delete(`/api/v1/terminals/${id}`, { headers: CSRF });
  }
});
