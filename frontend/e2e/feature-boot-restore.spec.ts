import { expect, test, type Request } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

for (const width of [1280, 320]) {
  for (const feature of [
    { id: "media-forge", name: "Media Forge", kind: "image" },
    { id: "sonic-forge", name: "SonicForge", kind: "audio" },
  ]) {
    test(`restored ${feature.id} settings and library (${width}px)`, async ({ page, context }) => {
      test.skip(!hasSession() || process.env.CONTROL_DECK_INSTALLED_FEATURES_E2E !== "1",
        "requires restored installed Add-ons with existing Library assets");
      test.setTimeout(60_000);
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      const assetRequests = new Set<Request>();
      let assetsChangedAt = Date.now();
      page.on("request", (request) => {
        if (new URL(request.url()).pathname.endsWith("/addon/v1/assets")) {
          assetRequests.add(request);
          assetsChangedAt = Date.now();
        }
      });
      const assetRequestFinished = (request: Request) => {
        if (assetRequests.delete(request)) assetsChangedAt = Date.now();
      };
      page.on("requestfinished", assetRequestFinished);
      page.on("requestfailed", assetRequestFinished);
      await page.setViewportSize({ width, height: 800 });
      await establishSession(page, context);
      await page.goto("/settings");
      const section = page.getByRole("heading", { name: "オプション機能" })
        .locator("xpath=ancestor::section[1]");
      const row = section.locator("div.rounded-xl.border").filter({ hasText: feature.name }).first();
      await expect(row).toContainText("利用できます");
      await expect(row.getByRole("button", { name: "無効化", exact: true })).toBeVisible();

      await page.goto(`/x/${feature.id}/workspace`);
      const embedded = page.frameLocator("iframe");
      await expect(embedded.locator("html")).toHaveAttribute("data-bridge", "ready", { timeout: 20_000 });
      await embedded.getByRole("button", { name: /^(Library|ライブラリ)$/ }).click();
      assetsChangedAt = Date.now();
      if (feature.kind === "image") {
        await expect.poll(async () => embedded.locator("img:visible").evaluateAll((images) =>
          images.filter((element) => {
            const image = element as HTMLImageElement;
            return image.complete && image.naturalWidth > 0;
          }).length)).toBeGreaterThan(0);
      } else {
        await embedded.getByRole("button", { name: /Play|再生/ }).first().waitFor();
        // Initial visibility and tab selection both refresh the Library. Let
        // those responses finish before starting a player in the rendered list.
        await expect.poll(() => assetRequests.size === 0 && Date.now() - assetsChangedAt > 300).toBe(true);
        // Library players are lazy and short previews can hide on completion.
        // Observe real playback progress rather than selecting a hidden player.
        await embedded.locator("html").evaluate((root) => {
          root.removeAttribute("data-boot-playback-progress");
          document.addEventListener("play", (event) => {
            if (event.target instanceof HTMLAudioElement) event.target.muted = true;
          }, true);
          document.addEventListener("timeupdate", (event) => {
            if (event.target instanceof HTMLAudioElement && event.target.currentTime > 0) {
              root.setAttribute("data-boot-playback-progress", "true");
            }
          }, true);
        });
        await embedded.getByRole("button", { name: /Play|再生/ }).first().click();
        await expect(embedded.locator("html")).toHaveAttribute("data-boot-playback-progress", "true", {
          timeout: 15_000,
        });
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
      expect(errors).toEqual([]);
    });
  }
}
