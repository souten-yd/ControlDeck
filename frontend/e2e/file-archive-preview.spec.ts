import { expect, test, type Page, type Request } from "@playwright/test";

const username = process.env.CONTROL_DECK_E2E_USER;
const password = process.env.CONTROL_DECK_E2E_PASSWORD;

async function login(page: Page) {
  test.skip(!username || !password, "CONTROL_DECK_E2E_USER/PASSWORD are required");
  await page.goto("/login");
  await page.getByLabel("ユーザー名").fill(username!);
  await page.getByLabel("パスワード").fill(password!);
  await page.getByRole("button", { name: "ログイン" }).click();
  await expect(page).not.toHaveURL(/\/login/);
}

const entries = [
  { name: "project", path: "/mock/project", is_dir: true, is_symlink: false, size: 0, mtime: 1_700_000_000, hidden: false },
  { name: "manual.pdf", path: "/mock/manual.pdf", is_dir: false, is_symlink: false, size: 4096, mtime: 1_700_000_000, hidden: false },
  { name: "sound.mp3", path: "/mock/sound.mp3", is_dir: false, is_symlink: false, size: 8192, mtime: 1_700_000_000, hidden: false },
  { name: "movie.mp4", path: "/mock/movie.mp4", is_dir: false, is_symlink: false, size: 16384, mtime: 1_700_000_000, hidden: false },
  { name: "bundle.zip", path: "/mock/bundle.zip", is_dir: false, is_symlink: false, size: 2048, mtime: 1_700_000_000, hidden: false },
];

test("archives safely and previews PDF/audio/video in responsive surfaces", async ({ page }) => {
  let archiveRequest: Request | null = null;
  await page.route("**/api/v1/files/roots", (route) => route.fulfill({ json: ["/mock"] }));
  await page.route("**/api/v1/files/list?**", (route) => route.fulfill({ json: { path: "/mock", entries } }));
  await page.route("**/api/v1/files/archive", async (route) => {
    archiveRequest = route.request();
    await route.fulfill({ json: { ok: true, path: "/mock/project.tar.gz", entries: 2, bytes: 4, format: "tar.gz" } });
  });
  await page.route("**/api/v1/files/preview?**", (route) => {
    const path = new URL(route.request().url()).searchParams.get("path") ?? "";
    const contentType = path.endsWith(".pdf") ? "application/pdf" : path.endsWith(".mp3") ? "audio/mpeg" : "video/mp4";
    return route.fulfill({ status: 200, contentType, body: path.endsWith(".pdf") ? "%PDF-1.4\n%%EOF" : "preview" });
  });
  await login(page);

  for (const viewport of [{ width: 320, height: 700 }, { width: 1280, height: 800 }]) {
    await page.setViewportSize(viewport);
    await page.goto("/files?path=%2Fmock");
    await expect(page.getByText("manual.pdf", { exact: true })).toBeVisible();

    await page.getByText("manual.pdf", { exact: true }).click();
    const pdfDialog = page.getByRole("dialog", { name: "manual.pdf" });
    await expect(pdfDialog.getByTitle("manual.pdf PDF preview")).toBeVisible();
    const pdfBox = await pdfDialog.boundingBox();
    expect(pdfBox).not.toBeNull();
    if (viewport.width >= 640) expect(pdfBox!.x).toBeGreaterThanOrEqual(viewport.width - 721);
    else expect(pdfBox!.y).toBeGreaterThan(40);
    await pdfDialog.getByRole("button", { name: "閉じる" }).click();

    await page.getByText("sound.mp3", { exact: true }).click();
    const audioDialog = page.getByRole("dialog", { name: "sound.mp3" });
    await expect(audioDialog.getByLabel("sound.mp3 audio preview")).toBeVisible();
    await audioDialog.getByRole("button", { name: "閉じる" }).click();

    await page.getByText("movie.mp4", { exact: true }).click();
    const videoDialog = page.getByRole("dialog", { name: "movie.mp4" });
    await expect(videoDialog.getByLabel("movie.mp4 video preview")).toBeVisible();
    await videoDialog.getByRole("button", { name: "閉じる" }).click();

    await page.getByLabel("bundle.zip のメニュー").click();
    await page.getByText("Extract", { exact: true }).click();
    const extractDialog = page.getByRole("dialog", { name: "展開" });
    await expect(extractDialog.getByLabel("アーカイブ展開先")).toHaveValue("/mock/bundle");
    await expect(extractDialog).toContainText("既存項目は上書きしません");
    await extractDialog.getByRole("button", { name: "キャンセル" }).click();

    await page.getByLabel("project のメニュー").click();
    await page.getByText("Compress", { exact: true }).click();
    const archiveDialog = page.getByRole("dialog", { name: "圧縮" });
    await archiveDialog.getByLabel("アーカイブ形式").selectOption("tar.gz");
    await expect(archiveDialog.getByLabel("アーカイブ保存先")).toHaveValue("/mock/project.tar.gz");
    await archiveDialog.getByRole("button", { name: "圧縮する" }).click();
    await expect(archiveDialog).toBeHidden();

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
  }

  expect(archiveRequest).not.toBeNull();
  expect(archiveRequest!.postDataJSON()).toEqual({
    source: "/mock/project", destination: "/mock/project.tar.gz", format: "tar.gz",
  });
});
