"""JavaScript が要るページの判定（W2）。固定の HTML の見本 15 件（tests/fixtures/js_pages）で評価する。

目的:
- 要るページ（空の根・noscript の案内・script だけ）は needs_js、本文がある（SSR 済み・短い通知・分析タグ付き）ページは要らないと判定すること。
  誤って「要る」にするとブラウザを無駄に起こし、誤って「要らない」にすると本文が空のまま返る。見本の全件で期待どおりであること。
- 初期 JSON（__NEXT_DATA__・window.__NUXT__・ld+json）に本文があれば、ブラウザを使わずそこから取ること。
  本文が既に十分あるページでは JSON を足さないこと。
根拠: docs/PLAN-web-collector.md §7 W2、HANDOFF「W2 で最初にやること」（OpenAlex は「needs JavaScript」の案内 254 字だけ）。通信なし。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.web_collector import extract, js_detect

FIXTURES = Path(__file__).parent / "fixtures" / "js_pages"
EXPECT = json.loads((FIXTURES / "expectations.json").read_text(encoding="utf-8"))


def _verdict(name: str) -> js_detect.Verdict:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    text = asyncio.run(extract.extract(html, "https://example.test/", "article"))["markdown"]
    return js_detect.assess(html, text)


@pytest.mark.parametrize("name", sorted(EXPECT))
def test_each_sample_is_classified_as_expected(name):
    verdict = _verdict(name)
    kind = EXPECT[name]["expect"]
    if kind == "needs":
        assert verdict.needs_js and not verdict.json_text, (name, verdict)
    elif kind == "static":
        assert not verdict.needs_js and not verdict.json_text, (name, verdict)
    else:
        assert not verdict.needs_js and len(verdict.json_text) >= js_detect.JSON_MIN_CHARS, (name, verdict)
        assert "gallium nitride" in verdict.json_text
        assert "buildId" not in verdict.json_text and "abc" not in verdict.json_text.split()


def test_accuracy_over_all_samples_is_perfect():
    """見本全体の正答率（要る / 静的 / JSON）。1 件でも外れたら基準を見直す。"""
    wrong = []
    for name, meta in EXPECT.items():
        v = _verdict(name)
        got = "json" if v.json_text else ("needs" if v.needs_js else "static")
        if got != meta["expect"]:
            wrong.append((name, got, meta["expect"]))
    assert not wrong


def test_html_inside_json_is_reduced_to_text():
    html = '<div id="__nuxt"></div><script>window.__NUXT__=' + json.dumps({"c": "<p>" + "Gallium nitride devices switch quickly and lose little. " * 12 + "</p>"}) + ";</script>"
    prose, source = js_detect.json_prose(html)
    assert source == "__NUXT__" and "<p>" not in prose and "Gallium nitride" in prose


def test_broken_or_huge_json_is_ignored():
    assert js_detect.json_prose('<script type="application/json">{broken</script>') == ("", "")
    assert js_detect.json_prose('<script>window.__NUXT__={"a":</script>') == ("", "")
