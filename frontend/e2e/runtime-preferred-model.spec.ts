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
    const strata = page.getByRole("combobox", { name: "Strata の優先起動モデル", exact: true });
    const llama = page.getByRole("combobox", { name: "llama.cpp の優先起動モデル", exact: true });
    await strata.selectOption("strata-second");
    await llama.selectOption("llama-second");
    await expect(strata.locator('option[value="unprepared"]')).toHaveAttribute("disabled", "");
    await expect(page.getByRole("combobox", { name: "Maya-S の優先起動モデル", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "保存", exact: true }).click();
    await expect.poll(() => saved?.preferred_models?.strata).toBe("strata-second");
    expect(saved.preferred_models["llama.cpp"]).toBe("llama-second");
    await page.getByRole("button", { name: "LLM 共通設定", exact: true }).click();
    await expect(strata).toHaveValue("strata-second");
    await expect(llama).toHaveValue("llama-second");
    await strata.scrollIntoViewIfNeeded();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: `/tmp/runtime-preferred-model-${width}.png` });
  });
}
