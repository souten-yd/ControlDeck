import { expect, test } from "@playwright/test";

// Run against an isolated Vite dev server, never the installed API.
// navigator.language + languagechange are browser-input fixtures, not a claim
// that browser Settings were operated. The production React bridge is real.
for (const width of [1280, 320]) {
  test(`embedded locale changes preserve the opaque frame at ${width}px`, async ({ page }, testInfo) => {
    test.skip(!process.env.CONTROL_DECK_LOCALE_FIXTURE, "requires isolated Vite fixture server");
    await page.setViewportSize({ width, height: 700 });
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let handshakes = 0;
    await page.route(/\/api\/v1\//, async (route) => {
      if (new URL(route.request().url()).pathname === "/api/v1/addons/locale-fixture/bridge/handshake") {
        handshakes++;
        await route.fulfill({ json: { addon_id: "locale-fixture", view_id: "workspace",
          bridge_version: "1.0", session_nonce: "fixture-nonce", expires_in: 600, allowed_methods: [] } });
      } else {
        await route.abort();
      }
    });
    await page.route("**/addon-frame/locale-fixture/", (route) => route.fulfill({
      contentType: "text/html", body: `<!doctype html><html><body><input aria-label="Draft" value="kept"><script>
        const root = document.documentElement;
        root.dataset.load = crypto.randomUUID();
        root.dataset.origin = self.origin;
        root.dataset.locales = '[]';
        addEventListener('message', (event) => {
          if (event.data.type !== 'control-deck-host.connected') return;
          root.dataset.nonce = event.data.session_nonce;
          const port = event.ports[0];
          port.onmessage = ({data}) => {
            if (data.event === 'locale.changed') {
              root.lang = data.data.locale;
              root.dataset.locales = JSON.stringify([...JSON.parse(root.dataset.locales), data.data.locale]);
            }
            if (data.event === 'theme.changed') root.dataset.themeLocale = data.data.locale;
          };
          port.start();
        });
        parent.postMessage({type:'control-deck-addon.connect',bridge_version:'1.0'}, '*');
      </script></body></html>`,
    }));
    await page.route("**/__locale-fixture", (route) => route.fulfill({
      contentType: "text/html", body: `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div>
        <script type="module">
          import RefreshRuntime from '/@react-refresh';
          RefreshRuntime.injectIntoGlobalHook(window);
          window.$RefreshReg$ = () => {};
          window.$RefreshSig$ = () => (type) => type;
          window.__vite_plugin_react_preamble_installed__ = true;
          await import('/e2e/fixtures/addon-locale.tsx');
        </script></body></html>`,
    }));
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "language", { configurable: true, get: () => "ja-JP" });
    });
    await page.goto("/__locale-fixture");
    const frame = page.frameLocator("iframe");
    const html = frame.locator("html");
    await expect(html).toHaveAttribute("lang", "ja");
    await expect(html).toHaveAttribute("data-origin", "null");
    const load = await html.getAttribute("data-load");
    await frame.getByLabel("Draft").fill("unsaved input");
    for (const [language, locale] of [["en-US", "en"], ["ja-JP", "ja"], ["de-DE", "en"]]) {
      await page.evaluate((language) => {
        Object.defineProperty(navigator, "language", { configurable: true, get: () => language });
        window.dispatchEvent(new Event("languagechange"));
      }, language);
      await expect(html).toHaveAttribute("lang", locale);
      await expect(html).toHaveAttribute("data-theme-locale", locale);
      await expect(html).toHaveAttribute("data-load", load!);
      await expect(html).toHaveAttribute("data-nonce", "fixture-nonce");
      await expect(frame.getByLabel("Draft")).toHaveValue("unsaved input");
    }
    await expect(html).toHaveAttribute("data-locales", '["ja","en","ja","en"]');
    expect(handshakes).toBe(1);
    expect(errors).toEqual([]);
    expect(await page.evaluate(() => window.innerWidth)).toBe(width);
    await testInfo.attach("locale-preserved", { body: await page.screenshot(), contentType: "image/png" });
  });
}
