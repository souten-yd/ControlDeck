import { expect, test } from "@playwright/test";
import { openOpenCode } from "./support/opencode";
import { establishSession, hasSession } from "./support/session";

/** OpenCode 設定の「役割とモデル」。
 * 目的: 役割（計画・開発・調査・簡易検証・最終検証・評価）ごとにモデルを選べ、同じ段のローカルが 2 つになる選択は
 * その場で理由が出て保存できないこと。最終検証＝開発の注意が出ること。320・1280、ライト・ダークで読めること。
 * 根拠: 2026-11-22 利用者の依頼（ローカルのモデルは GPU で同時に 1 つしか動かせない）。
 * 検査はサーバー（/opencode/roles/check）が持つので、ここは検証用インスタンスの本物の API を通す。 */
const workflow = {
  superpowers: false, adversarial_review: false, auto_review: true, auto_advisor: "off", advisor_model: "",
  developer_agent: false, developer_model: "", roles: {}, ui_verify: false, ui_verify_allow_external: false,
  research_tool: true, research_external: false, context7: false, github_cli: false, isolation: "box",
  models: [{ alias: "maya-s", runtime: "llama.cpp" }, { alias: "qwen-flash-next", runtime: "llama.cpp" }],
  cloud_models: [{ id: "cloud:openrouter/x/y", name: "OpenRouter / x/y", provider: "openrouter" }],
  cloud_blocked: false, installed: {}, features: {}, browser: false, review_tool: false,
  github: { installed: false, configured: false }, isolation_effective: "box",
};

for (const width of [320, 1280]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`役割とモデル: 同じ段のローカル 2 つは保存できない (${width}px ${scheme})`, async ({ page, context }) => {
      test.skip(!hasSession(), "E2E credentials are required");
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme: scheme });
      await establishSession(page, context);
      // 検証用インスタンスは OpenCode の API を持たない。検査はサーバー側（pytest が確かめる）と同じ決まりの模擬で返す。
      await page.route("**/api/v1/opencode/roles/check", async (route) => {
        const roles = route.request().postDataJSON().roles as Record<string, string>;
        const stages = [["build", "research", "quick_check"], ["final_check", "evaluate"]];
        const reason = "同時に動きうる役割なので、ローカルは同じモデルにしてください（GPU で同時に 1 つしか動かせません）";
        const local = (m?: string) => Boolean(m) && !m!.startsWith("cloud:");
        let error = "";
        for (const names of stages) {
          const set = new Set(names.map((n) => roles[n]).filter(local));
          if (set.size > 1) error = `${names[0] === "build" ? "② 作る" : "③ 確かめる"}の ${[...set].join(" と ")}: ${reason}`;
        }
        const notes = roles.final_check && roles.final_check === roles.build ? ["最終検証が開発と同じモデルです。別の目で見られません"] : [];
        await route.fulfill({ json: { error, notes, effective: {} } });
      });
      await openOpenCode(page, workflow);
      await page.getByRole("button", { name: "OpenCode 設定を開く" }).click();
      const sheet = page.getByRole("dialog");
      const group = sheet.getByRole("heading", { name: "役割とモデル" });
      await group.scrollIntoViewIfNeeded();

      const select = (stage: string, role: string) => sheet.getByLabel(`${stage} ${role}のモデル`);
      for (const [stage, role] of [["① 計画", "計画"], ["② 作る", "開発"], ["② 作る", "調査"], ["② 作る", "簡易検証"],
        ["③ 確かめる", "最終検証"], ["③ 確かめる", "評価"]]) {
        const box = await select(stage, role).boundingBox();
        expect(box!.height, `${role} の選択は 44px 以上`).toBeGreaterThanOrEqual(44);
      }
      const save = sheet.getByRole("button", { name: "保存", exact: true });
      await expect(save).toBeEnabled();

      // 段②: 開発と調査に別のローカル → 理由が出て保存できない
      await select("② 作る", "開発").selectOption("maya-s");
      await select("② 作る", "調査").selectOption("qwen-flash-next");
      const alert = sheet.getByRole("alert").filter({ hasText: "同時に 1 つしか動かせません" });
      await expect(alert).toBeVisible();
      await expect(alert).toContainText("同時に動きうる役割なので、ローカルは同じモデルにしてください");
      await expect(save).toBeDisabled();
      await alert.evaluate((el) => el.scrollIntoView({ block: "center" }));
      await page.screenshot({ path: `test-results/roles-conflict-${width}-${scheme}.png` });

      // 外部のモデルは同じ段に混ぜてよい（調査を外部へ）。保存できる
      await select("② 作る", "調査").selectOption("cloud:openrouter/x/y");
      await expect(alert).toHaveCount(0);
      await expect(save).toBeEnabled();

      // 段をまたぐ載せ替えは許す（計画は別のローカル）。最終検証＝開発の注意
      await select("① 計画", "計画").selectOption("qwen-flash-next");
      await select("③ 確かめる", "最終検証").selectOption("maya-s");
      await expect(sheet.getByRole("note").filter({ hasText: "別の目で見られません" })).toBeVisible();
      await expect(save).toBeEnabled();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await select("③ 確かめる", "最終検証").evaluate((el) => el.scrollIntoView({ block: "center" }));
      await page.screenshot({ path: `test-results/roles-warning-${width}-${scheme}.png` });
      await group.evaluate((el) => el.scrollIntoView({ block: "start" }));
      await page.screenshot({ path: `test-results/roles-panel-${width}-${scheme}.png` });
    });
  }
}
