/**
 * 目的: AI アシスタントの上の行（「auto」のボタンの右）にある「秘密の会話」の鍵のボタンを守る。
 * 新しい会話で押す→確認→OK で閉じた鍵・入力欄の 🔒・送ると confidential:true で作られる／既存の会話で押すと API が呼ばれ閉じた鍵／
 * キャンセルは何も変えない／閉じた鍵は説明だけで解除の操作が出ない／320px で上の行の要素が重ならずはみ出さない（ライト・ダーク）。
 * 根拠: 2026-11-21 利用者「Web 検索などの通信ロックは重要なので、分かりやすいところに置きたい。右上の『auto』の右側に、鍵が開いているマークを」。
 * 撮影: E2E_SHOT_DIR（無ければ playwright の出力先）。
 */
import { expect, test } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

for (const [label, width, height] of [["pc-1280", 1280, 800], ["mobile-320", 320, 700]] as const) {
  for (const scheme of ["light", "dark"] as const) {
    test(`secret lock button in the top bar (${label}, ${scheme})`, async ({ page }, testInfo) => {
      test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
      const shot = async (name: string) => {
        const dir = process.env.E2E_SHOT_DIR || testInfo.outputDir;
        await page.screenshot({ path: `${dir}/secret-lock-topbar-${label}-${scheme}-${name}.png` });
      };
      const noOverflow = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
      await page.emulateMedia({ colorScheme: scheme });
      await page.setViewportSize({ width, height });
      await page.goto("/assistant");
      await page.getByLabel("ユーザー名").fill(username!);
      await page.getByLabel("パスワード").fill(password!);
      await page.getByRole("button", { name: "ログイン", exact: true }).click();
      await expect(page.getByLabel("ユーザー名")).toBeHidden();
      const shell = page.locator("[data-assistant-shell]");
      const topbar = shell.locator("[data-assistant-topbar]");
      const openLock = shell.getByRole("button", { name: "この会話を秘密にする（通信のロック）" });
      const closedLock = shell.getByRole("button", { name: "秘密の会話（通信のロック中。解除できません）" });
      const pill = shell.locator('[data-composer-pill="secret"]');
      const model = shell.getByRole("button", { name: /AI設定を(開く|閉じる)/ });

      // 上の行の要素が重ならない・はみ出さない（鍵は auto のボタンの右）
      const checkBar = async () => {
        const boxes = await topbar.locator(":scope > div > *").evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, w: r.width }; }).filter((b) => b.w > 0));
        const sorted = [...boxes].sort((a, b) => a.l - b.l);
        for (let i = 1; i < sorted.length; i++) expect(sorted[i].l).toBeGreaterThanOrEqual(sorted[i - 1].r - 0.5);
        for (const b of boxes) expect(b.r).toBeLessThanOrEqual(width + 0.5);
        expect(await noOverflow()).toBe(true);
        const m = (await model.boundingBox())!, k = (await shell.locator("[data-secret-lock]").boundingBox())!;
        expect(k.x).toBeGreaterThanOrEqual(m.x + m.width - 0.5);
        expect(k.width).toBeGreaterThanOrEqual(40);
      };

      // (a) 新しい会話: 開いた鍵 → キャンセル(c) → OK で閉じた鍵・🔒、送ると confidential:true で作られる
      await page.evaluate(() => localStorage.removeItem("cd-chat-conversation"));
      await page.goto("/assistant");
      await expect(openLock).toBeVisible();
      await expect(pill).toHaveCount(0);
      await checkBar();
      await shot("1-open");
      await openLock.click();
      const dialog = page.getByRole("alertdialog");
      await expect(dialog).toContainText("解除できません");
      await shot("2-confirm");
      await dialog.getByRole("button", { name: "キャンセル" }).click();
      await expect(openLock).toBeVisible();
      await expect(pill).toHaveCount(0);
      await openLock.click();
      await page.getByRole("alertdialog").getByRole("button", { name: "秘密にする" }).click();
      await expect(closedLock).toBeVisible();
      await expect(pill).toBeVisible();
      await checkBar();
      await shot("3-closed-new");
      // (d) 閉じた鍵は説明だけ。解除の操作は出ない
      await closedLock.click();
      await expect(page.getByRole("alertdialog")).toHaveCount(0);
      await expect(shell.getByRole("button", { name: /解除する|秘密をやめる|秘密にしない/ })).toHaveCount(0);
      // 設定のシートの表示もそろう（チェックが入っている）
      await model.click();
      await expect(shell.getByLabel("秘密の会話として始める")).toBeChecked();
      // 一度ロックしたら、送る前でもチェックは外せない（確認で「解除できません」と伝えているため）
      await expect(shell.getByLabel("秘密の会話として始める")).toBeDisabled();
      await model.click();
      // 送ると confidential:true で作られる
      const created = page.waitForRequest((r) => r.method() === "POST" && /\/chat\/conversations$/.test(r.url()));
      await shell.locator("[data-assistant-input]").fill("こんにちは");
      await shell.locator("[data-assistant-input]").press("Enter");
      expect(JSON.parse((await created).postData() || "{}").confidential).toBe(true);
      await expect(closedLock).toBeVisible();
      await expect(shell.getByRole("button", { name: "止める" })).toBeHidden({ timeout: 60_000 });   // 送信が落ち着いてから次へ

      // (b) 既存の会話: 開いた鍵 → OK で API が呼ばれ閉じた鍵
      const id = await page.evaluate(async () => {
        const r = await fetch("/api/v1/chat/conversations", { method: "POST", credentials: "same-origin", body: JSON.stringify({}),
          headers: { "X-Requested-With": "ControlDeck", "Content-Type": "application/json" } });
        return (await r.json()).id as string;
      });
      await page.evaluate((v) => localStorage.setItem("cd-chat-conversation", v), id);
      await page.goto("/assistant");
      await expect(openLock).toBeVisible();
      const called = page.waitForRequest((r) => r.method() === "POST" && r.url().endsWith(`/chat/conversations/${id}/confidential`));
      await openLock.click();
      await page.getByRole("alertdialog").getByRole("button", { name: "秘密にする" }).click();
      await called;
      await expect(closedLock).toBeVisible();
      await expect(pill).toBeVisible();
      await checkBar();
      await shot("4-closed-existing");
      await page.reload();
      await expect(closedLock).toBeVisible();
    });
  }
}
