import { expect, test, type Route } from "@playwright/test";
import { openOpenCode } from "./support/opencode";
import { establishSession, hasSession } from "./support/session";

/** OpenCode の「結果」: 一覧 → 詳細 → 画面写真の拡大、を 320px で通す。
 *
 * 報告は AI がセッション中に作るものなので、API は page.route で差し替える。
 * 本物のデータには触れない（削除も送らない）。 */
const IMAGE = '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="800"><rect width="320" height="800" fill="#888"/></svg>';
const json = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const now = Math.floor(Date.now() / 1000);

const summary = {
  id: "r-ui", kind: "ui_verify", title: "設定画面の UI 検証", verdict: "fail", target: "http://localhost:5173/settings",
  created_at: now - 120, session: "tui-e2e", counts: { critical: 0, major: 1, minor: 1, info: 0 },
  sources: 0, checks: 4, thumbnail: "w320-light.png",
};
const research = {
  id: "r-research", kind: "research", title: "Playwright MCP の origin 制限", verdict: "partial", target: "",
  created_at: now - 7200, session: "tui-e2e", counts: { critical: 0, major: 0, minor: 0, info: 0 },
  sources: 2, checks: 0, thumbnail: null,
};
const detail = {
  ...summary, summary: "320px で保存ボタンがはみ出します。ほかの幅は問題ありません。",
  checks: [
    { viewport: "320x800", theme: "light", screenshot: "w320-light.png", status: "fail", note: "保存ボタンが右にはみ出す" },
    { viewport: "320x800", theme: "dark", screenshot: "w320-dark.png", status: "pass", note: "" },
    { viewport: "1280x800", theme: "light", screenshot: "w1280-light.png", status: "pass", note: "" },
    { viewport: "768x1024", theme: "light", screenshot: "gone.png", status: "skip", note: "撮れなかった" },
  ],
  findings: [
    { severity: "minor", title: "余白が狭い", detail: "", location: "", viewport: "320x800", screenshot: null },
    { severity: "major", title: "保存ボタンがはみ出す", detail: "横スクロールが出ます", location: "frontend/src/pages/Settings.tsx:120", viewport: "320x800", screenshot: "w320-light.png" },
  ],
  sources: [], console_errors: ["TypeError: x is undefined"], failed_requests: ["GET /api/v1/missing 404"],
  unverified: ["再接続の表示"], missing_images: ["gone.png"],
};
const researchDetail = {
  ...research, summary: "origin 制限は安全境界ではない。", checks: [], findings: [],
  sources: [
    { title: "Playwright MCP README", url: "https://example.com/readme", accessed_at: "2026-11-15T03:00:00Z", published_at: "", version: "0.0.84", claim: "origin 制限は安全境界ではない", stance: "supports", primary: true },
    { title: "古いブログ記事", url: "https://example.com/blog", accessed_at: "2026-11-15T03:05:00Z", published_at: "2025-01-10", version: "", claim: "origin 制限で十分", stance: "contradicts", primary: false },
  ],
  console_errors: [], failed_requests: [], unverified: [], missing_images: [],
};

test("結果の一覧から詳細を開き、画面写真を拡大して前後に送れる（320px）", async ({ page, context }) => {
  test.skip(!hasSession(), "E2E credentials are required");
  await page.setViewportSize({ width: 320, height: 700 });
  await page.route("**/api/v1/opencode/reports?*", (route) => json(route, { reports: [research, summary] }));
  await page.route("**/api/v1/opencode/reports/r-ui", (route) => json(route, detail));
  await page.route("**/api/v1/opencode/reports/r-research", (route) => json(route, researchDetail));
  await page.route("**/api/v1/opencode/reports/r-ui/images/*", (route) =>
    route.fulfill({ status: 200, contentType: "image/svg+xml", body: IMAGE }));
  await establishSession(page, context);
  await openOpenCode(page);

  // 新しい順に並び、判定が文字で読める。
  const rows = page.getByRole("button", { name: /の結果「.*」を開く/ });
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("設定画面の UI 検証");
  await expect(rows.first()).toContainText("要修正");
  await expect(rows.first()).toContainText("重要 1");
  await expect(rows.nth(1)).toContainText("一部未確認");

  await rows.first().click();
  const sheet = page.getByRole("dialog", { name: "UI 検証の結果" });
  await expect(sheet).toBeVisible();
  await expect(sheet.getByRole("group", { name: "幅 320px" })).toBeVisible();
  // 指摘は重大度順（重要 → 軽微）。
  const findings = sheet.locator("section", { hasText: "指摘" }).locator("li");
  await expect(findings.first()).toContainText("保存ボタンがはみ出す");
  await expect(sheet.getByText("確かめられなかったこと")).toBeVisible();
  await expect(sheet.getByText(/取り込めなかった画面写真/)).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await sheet.getByRole("button", { name: "幅 320px・ライト の画面写真を拡大" }).click();
  const lightbox = page.getByRole("dialog", { name: "画面写真の拡大表示" });
  await expect(lightbox).toBeVisible();
  await expect(lightbox).toContainText("1 / 3");
  await expect(lightbox.getByRole("img", { name: "幅 320px・ライト の画面写真" })).toBeVisible();
  await lightbox.getByRole("button", { name: "次の画面写真" }).click();
  await expect(lightbox).toContainText("2 / 3");
  await page.keyboard.press("ArrowLeft");
  await expect(lightbox).toContainText("1 / 3");

  // Esc は拡大表示だけを閉じ、詳細は残る。
  await page.keyboard.press("Escape");
  await expect(lightbox).toBeHidden();
  await expect(sheet).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(sheet).toBeHidden();

  // 精密調査: 反証がひと目で分かり、出典は別タブで開く。
  await rows.nth(1).click();
  const researchSheet = page.getByRole("dialog", { name: "精密調査の結果" });
  await expect(researchSheet.getByText("結論に反する根拠が")).toBeVisible();
  await expect(researchSheet.getByText("一次資料")).toBeVisible();
  const link = researchSheet.getByRole("link", { name: "https://example.com/blog" });
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
});
