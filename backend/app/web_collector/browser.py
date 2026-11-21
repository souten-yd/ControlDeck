"""Web 収集のブラウザ。**この PC の Google Chrome を使い、別のブラウザを取りに行かない**（2026-11-21 利用者「Chrome を
Playwright などで使っているので二重になっていないか」）。

- Playwright の既定（`chromium.launch()`）は、Playwright の版に合う Chromium を別に必要とする。本体の Playwright 1.62 は
  build 1234 を求めるが入っておらず、Workflow の「ブラウザ操作」が起動できなかった（2026-11-21 確認）。
- Chrome は毎回まっさらの一時プロファイルで起動する（`launch()` の既定）。利用者の Chrome のログイン・Cookie・履歴は使わない。
- 通信の検査（中継プロキシ）は W2 で足す（docs/PLAN-web-collector.md）。いまは開く URL を egress で確かめる。
"""
from __future__ import annotations

import shutil
import subprocess

NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def system_chrome() -> str:
    for name in NAMES:
        found = shutil.which(name)
        if found:
            return found
    return ""


def chrome_version() -> str:
    chrome = system_chrome()
    if not chrome:
        return ""
    try:
        out = subprocess.run([chrome, "--version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.stdout.strip()[:80]


async def launch(playwright):
    """Chrome を headless・一時プロファイル・Service Worker なしの前提で起動する（呼び出し側は new_context(service_workers="block")）。"""
    chrome = system_chrome()
    if not chrome:
        raise RuntimeError("この PC に Google Chrome（または Chromium）が見つかりません。Chrome を入れてください")
    return await playwright.chromium.launch(
        executable_path=chrome, headless=True,
        args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-background-networking",
              "--no-first-run", "--no-default-browser-check"])
