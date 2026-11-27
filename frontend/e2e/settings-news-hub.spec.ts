import { expect, test } from "@playwright/test";
import { establishSession, hasSession } from "./support/session";

/** 設定 → ニュースの「QNAP ニュースハブとつなぐ」: 出どころ・つなぐ（指紋の見比べ）・鍵を渡す・診断・つなぎを切る。API は page.route で模擬する（ハブへは通信しない）。
 *  根拠: 2026-11-25 利用者「ControlDeck に QNAP ニュースハブとつなぐ機能」。指紋を利用者が見比べるまで使い始めない／トークン・鍵を画面に出さない／320 で崩れない。 */
const hubSettings = {
  source: "local", sync_minutes: 5, kinds: ["items", "entities", "events"], channels: [], categories: [],
  features: { imagegen: false, reader: true, rag: true, expand_patent: false, expand_web: false, expand_rag: true }, jobs: { enabled: false, kinds: ["summarize_hq", "extract_retry", "image"], max_per_run: 5, interval_minutes: 15 },
};
const settings = {
  settings: {
    feed_refresh_minutes: 15, digest_daily: { enabled: true, time: "06:30" }, digest_weekly: { enabled: true, weekday: 0, time: "07:00" }, quiet: { enabled: false, start: "01:00", end: "05:00" },
    llm: { digest_model: "auto", gist_enabled: false, gist_model: "auto", digest_focus: "", prep_enabled: false, prep_model: "auto", prep_max_per_run: 100 }, rag: { enabled: true, fulltext: false, on_prune: "keep" },
    imagegen: { enabled: false, tier: "small", threads: 8, max_per_run: 30 }, hub: hubSettings,
  },
  weekdays: ["月", "火", "水", "木", "金", "土", "日"], interval_minutes: 60, imagegen_available: false, imagegen_tiers: [],
};
const empty = {
  linked: false, verified: false, url: "", name: "", my_fingerprint: "", hub_fingerprint: "", paired_at: "", verified_at: "", cursor: 0, latest: 0, source: "local", effective_source: "local",
  openrouter_key: true, imagegen_ready: false,
  sync: { last_at: "", last_ok_at: "", last_error: "", failures: 0, reachable: null, counts: {}, total: {} },
  jobs: { last_at: "", last_error: "", done: 0, failed: 0, rejected: 0, recent: [] }, secret: { sent_at: "", error: "" },
};
const HUB_FP = "1a2b-3c4d-5e6f-7a8b-9c0d";
const MY_FP = "aaaa-bbbb-cccc-dddd-eeee";

for (const width of [1280, 320]) {
  test(`ニュースハブ: つなぐ → 指紋の確認 → 診断（${width}px）`, async ({ page, context }) => {
    test.skip(!hasSession(), "ログイン情報がありません");
    await page.setViewportSize({ width, height: width === 320 ? 640 : 900 });
    await establishSession(page, context);
    let status = { ...empty } as Record<string, unknown>;
    let paired = "";
    let secrets = 0;
    let foundHubs: Record<string, string>[] = [];
    await page.route(/\/api\/v1\/news\/hub\/discover$/, (route) => route.fulfill({ json: { found: foundHubs, tried: ["http://127.0.0.1:8787", "255.255.255.255:8788（UDP）"] } }));
    await page.route(/\/api\/v1\/news\/settings$/, (route) => route.request().method() === "GET" ? route.fulfill({ json: settings }) : route.continue());
    await page.route(/\/api\/v1\/news\/hub$/, (route) => route.request().method() === "GET" ? route.fulfill({ json: status }) : route.fulfill({ json: (status = { ...empty }) }));
    await page.route(/\/api\/v1\/news\/hub\/pair$/, (route) => {
      paired = route.request().postData() ?? "";
      status = { ...empty, linked: true, url: "http://nas.local:8787", name: "ControlDeck", hub_fingerprint: HUB_FP, my_fingerprint: MY_FP, paired_at: "2026-11-25T01:00:00Z" };
      return route.fulfill({ json: status });
    });
    await page.route(/\/api\/v1\/news\/hub\/confirm$/, (route) => {
      status = { ...status, verified: true, verified_at: "2026-11-25T01:01:00Z", source: "auto", effective_source: "hub", cursor: 12, latest: 12,
        sync: { last_at: "2026-11-25T01:02:00Z", last_ok_at: "2026-11-25T01:02:00Z", last_error: "", failures: 0, reachable: true, counts: { items: 5, entities: 1, events: 1, removed: 0 }, total: {} },
        jobs: { last_at: "", last_error: "", done: 3, failed: 1, rejected: 0, recent: [{ at: "2026-11-25T01:02:00Z", kind: "summarize_hq", status: "done", error: "" }] } };
      return route.fulfill({ json: status });
    });
    await page.route(/\/api\/v1\/news\/hub\/secret$/, (route) => { secrets += 1; status = { ...status, secret: { sent_at: "2026-11-25T01:03:00Z", error: "" } }; return route.fulfill({ json: status }); });
    await page.goto("/settings/news?tab=hub");
    const card = page.getByRole("region", { name: "QNAP ニュースハブとつなぐ" });
    await expect(card.getByTestId("hub-effective")).toContainText("この PC が自分で集めています");      // 既定はローカル
    await expect(card.getByRole("radio", { name: "ローカル" })).toBeChecked();
    // 「探す」: 見つけたハブを一覧（名前・版・URL・指紋）で出し、選ぶと URL が入る（つなぐのは番号と指紋の見比べのまま）
    await card.getByRole("button", { name: "探す" }).click();
    await expect(card.getByTestId("hub-discover-none")).toContainText("試した場所: http://127.0.0.1:8787、255.255.255.255:8788（UDP）");
    foundHubs = [{ name: "QNAP News Hub", version: "0.1.1", url: "http://192.0.2.20:8787", fingerprint: HUB_FP, from: "192.0.2.20" }];
    await card.getByRole("button", { name: "探す" }).click();
    await expect(card.getByTestId("hub-discover-list")).toContainText(HUB_FP);
    await card.getByTestId("hub-discover-list").getByRole("button").first().click();
    await expect(card.getByLabel("ハブの URL")).toHaveValue("http://192.0.2.20:8787");
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    await card.getByLabel("ハブの URL").fill("http://nas.local:8787");
    await card.getByLabel("つなぐ番号").fill("12ab34");        // 数字以外は入らない
    await expect(card.getByLabel("つなぐ番号")).toHaveValue("1234");
    await card.getByLabel("つなぐ番号").fill("123456");
    await card.getByRole("button", { name: "つなぐ", exact: true }).click();
    await expect(card.getByTestId("hub-fingerprints")).toBeVisible();      // 両方の指紋を出し、見比べるまで使い始めない
    await expect(card.getByTestId("hub-fp-hub")).toHaveText(HUB_FP);
    await expect(card.getByTestId("hub-fp-mine")).toHaveText(MY_FP);
    expect(paired).toContain("123456");
    await expect(card.getByTestId("hub-send-secret")).toHaveCount(0);
    await card.scrollIntoViewIfNeeded();
    await page.waitForTimeout(6000);      // トースト（完了通知）が消えてから撮る（背景を隠した写真にしない）
    await page.screenshot({ path: `test-results/settings-news-hub-fingerprint-${width}.png` });
    await card.getByRole("button", { name: "同じ（使い始める）" }).click();
    await expect(card.getByTestId("hub-linked")).toContainText("つながっています");
    await card.getByTestId("fold-news-hub-diag").locator("summary").click();      // 診断は畳んである
    await expect(card.getByTestId("hub-diag-cursor")).toHaveText("12 / 12");
    await expect(card.getByTestId("hub-diag-jobs")).toContainText("成功 3・失敗 1");
    await card.getByTestId("hub-send-secret").click();
    await expect(card.getByTestId("hub-secret-note")).toContainText("最後に渡した時刻");
    expect(secrets).toBe(1);
    expect(await page.content()).not.toContain("sk-or");       // 鍵の値は画面に出ない
    await card.getByTestId("fold-news-hub-detail").locator("summary").click();      // 詳しい設定は畳んである
    await card.getByRole("checkbox", { name: /ハブの仕事を借りて処理する/ }).check();
    await expect(card.getByTestId("hub-jobs-note")).toContainText("GPU を使います");
    // 検索の語の拡張: RAG はオン・Web 調査と特許はオフが既定（測定の結果）。320px でも切れない
    await expect(card.getByTestId("hub-expand-settings")).toBeVisible();
    await expect(card.getByRole("checkbox", { name: /知識（RAG）の検索/ })).toBeChecked();
    await expect(card.getByRole("checkbox", { name: /Web 調査の検索/ })).not.toBeChecked();
    await expect(card.getByRole("checkbox", { name: /特許の検索/ })).not.toBeChecked();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    await card.getByRole("button", { name: "つなぎを切る" }).click();
    await expect(card.getByRole("alert")).toContainText("取り込んだ記事は残ります");
    await card.getByRole("button", { name: "やめる" }).click();
    await card.scrollIntoViewIfNeeded();
    await page.waitForTimeout(6000);      // トースト（完了通知）が消えてから撮る（背景を隠した写真にしない）
    await page.screenshot({ path: `test-results/settings-news-hub-linked-${width}.png` });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
  });
}

test("ニュースハブ: 同期の失敗が診断に出る・違う指紋は破棄できる", async ({ page, context }) => {
  test.skip(!hasSession(), "ログイン情報がありません");
  await page.setViewportSize({ width: 1280, height: 900 });
  await establishSession(page, context);
  const failing = { ...empty, linked: true, verified: false, url: "http://nas.local:8787", hub_fingerprint: HUB_FP, my_fingerprint: MY_FP };
  let status: Record<string, unknown> = failing;
  let denied = "";
  await page.route(/\/api\/v1\/news\/settings$/, (route) => route.request().method() === "GET" ? route.fulfill({ json: settings }) : route.continue());
  await page.route(/\/api\/v1\/news\/hub$/, (route) => route.fulfill({ json: status }));
  await page.route(/\/api\/v1\/news\/hub\/confirm$/, (route) => { denied = route.request().postData() ?? ""; status = { ...empty }; return route.fulfill({ json: status }); });
  await page.goto("/settings/news?tab=hub");
  const card = page.getByRole("region", { name: "QNAP ニュースハブとつなぐ" });
  await card.getByRole("button", { name: "違う（破棄する）" }).click();
  await expect(card.getByTestId("hub-pair-form")).toBeVisible();
  expect(denied).toContain("false");
  status = { ...failing, verified: true, sync: { ...empty.sync, last_error: "ハブが認証を断りました（ハブの画面で取り消された可能性があります）。つなぎ直してください", failures: 3, reachable: false } };
  await page.reload();
  await expect(card.getByTestId("hub-diag-error")).toContainText("認証を断りました");
  await card.getByTestId("fold-news-hub-diag").locator("summary").click();
  await expect(card.getByTestId("hub-diag-state")).toContainText("つながっています");
});
