import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 主な画面のアクセシビリティを axe-core で測る（WCAG 2.1 A / AA）。
 *
 * 落とすのは impact が critical のものだけ。serious 以下は結果に添付して数を出す——
 * 既存の画面には直していない指摘があり、全部を一度に落とすと検査そのものが使われなく
 * なる。直したら閾値を上げる（docs/PLAN-opencode-dev-environment.md 第 1 段階 D）。
 *
 * 検証用インスタンスで走らせる: ./deck.sh e2e a11y.spec.ts */
const PAGES = ["/", "/opencode", "/models", "/settings/features", "/terminal", "/files"];
const WIDTHS = [320, 1280];

for (const width of WIDTHS) {
  for (const path of PAGES) {
    test(`a11y ${width}px ${path}`, async ({ page, context }, info) => {
      test.skip(!hasSession(), "E2E credentials are required");
      test.setTimeout(90_000);
      await page.setViewportSize({ width, height: 800 });
      await establishSession(page, context);
      // 画面は状態を poll し続けるので networkidle は来ない。読み込みのあと描画が落ち着くまで待つ。
      await page.goto(path, { waitUntil: "load" });
      await page.waitForTimeout(1500);
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
        .analyze();
      const rows = results.violations.map((v) => ({
        id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
        targets: v.nodes.slice(0, 5).map((n) => n.target.join(" ")),
      }));
      await info.attach("axe.json", { body: JSON.stringify(rows, null, 2), contentType: "application/json" });
      const count = (impact: string) => rows.filter((r) => r.impact === impact).length;
      info.annotations.push({
        type: "axe",
        description: `critical ${count("critical")} / serious ${count("serious")} / moderate ${count("moderate")} / minor ${count("minor")}`,
      });
      // 横スクロールが出ないこと（320px の約束）。
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, "横スクロールが出ている").toBeLessThanOrEqual(1);
      expect(rows.filter((r) => r.impact === "critical"), JSON.stringify(rows, null, 1)).toEqual([]);
    });
  }
}
