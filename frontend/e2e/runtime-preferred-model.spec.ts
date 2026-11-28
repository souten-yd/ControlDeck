/** 目的: ランタイムごとの起動モデル（preferred_models）を「LLM 共通設定」から選んで保存でき、
 *        未準備のモデルは選べないこと。320px / 1280px で横にはみ出さないこと。
 *  根拠: ランタイム切替時に起動モデルを固定したいという利用者の要望の回帰確認。
 *  #24: 共通 UI の現在のランタイムカード名・コンボボックス名と一致しており変更不要（回帰のみ）。
 *  レビュー #25 / 2026-11-28: 目的と根拠を明示し、共通設定の回帰として実際に実行して通す
 *  （安易な skip はしない）。 */
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [320, 1280]) {
  test(`runtime startup preferences can be selected and saved at ${width}px`, async ({ page, context }) => {
    test.skip(!hasSession(), "E2E credentials required");
    await page.setViewportSize({ width, height: 900 });
    await establishSession(page, context);
    let snapshot: any;
    let saved: any;
    await page.route("**/api/v1/models/runtime-environment", async (route) => {
      if (!snapshot) {
        snapshot = await (await route.fetch()).json();
        snapshot.policy = { ...snapshot.policy, selected_runtime: "strata", selected_backend: "", preferred_models: {} };
        snapshot.runtimes = [
          { id: "strata", runtime: "strata", backend: "", label: "Strata", installed: true, available: true },
          { id: "llama.cpp:rocm", runtime: "llama.cpp", backend: "rocm", label: "llama.cpp", installed: true, available: true },
          { id: "maya-s", runtime: "maya-s", backend: "", label: "Maya-S", installed: true, available: true },
        ];
        snapshot.startup_models = [
          { runtime: "strata", alias: "strata-first", name: "Strata first", ready: true },
          { runtime: "strata", alias: "strata-second", name: "Strata second", ready: true },
          { runtime: "strata", alias: "unprepared", name: "Unprepared", ready: false },
          { runtime: "llama.cpp", alias: "llama-first", name: "Llama first", ready: true },
          { runtime: "llama.cpp", alias: "llama-second", name: "Llama second", ready: true },
          { runtime: "maya-s", alias: "maya", name: "Maya", ready: true },
        ];
      }
      await route.fulfill({ json: snapshot });
    });
    await page.route("**/api/v1/models/runtime-policy", async (route) => {
      saved = route.request().postDataJSON();
      snapshot.policy = saved;
      await route.fulfill({ json: snapshot });
    });
    await page.goto("/models");
    await page.getByRole("button", { name: "LLM 共通設定", exact: true }).click();
    const strataCard = page.getByRole("group", { name: "Strata のランタイム設定", exact: true });
    const llamaCard = page.getByRole("group", { name: "llama.cpp のランタイム設定", exact: true });
    const strata = strataCard.getByRole("combobox", { name: "Strata の起動するモデル", exact: true });
    const llama = llamaCard.getByRole("combobox", { name: "llama.cpp の起動するモデル", exact: true });
    await expect(strata).toHaveValue("");
    await expect(llama).toHaveValue("");
    await expect(strata.locator('option[value=""]')).toHaveText("自動");
    await strata.selectOption("strata-second");
    await llama.selectOption("llama-second");
    await expect(strata.locator('option[value="unprepared"]')).toHaveAttribute("disabled", "");
    await expect(page.getByRole("combobox", { name: "Maya-S の起動するモデル", exact: true })).toHaveCount(0);
    await expect(strataCard.getByRole("button")).toHaveAttribute("aria-pressed", "true");
    await expect(llamaCard.getByRole("button")).toHaveAttribute("aria-pressed", "false");
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect.poll(() => saved?.preferred_models?.strata).toBe("strata-second");
    expect(saved.preferred_models["llama.cpp"]).toBe("llama-second");
    expect(saved.selected_runtime).toBe("strata");
    await page.getByRole("button", { name: "LLM 共通設定", exact: true }).click();
    await expect(strata).toHaveValue("strata-second");
    await expect(llama).toHaveValue("llama-second");
    await strata.scrollIntoViewIfNeeded();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/runtime-preferred-model-${width}.png` });
  });
}
