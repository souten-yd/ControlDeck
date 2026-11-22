"""開発のスキル（controldeck-development）が、作業の前に指示書（計画）を書かせること。

目的: 要望から `.controldeck/plan.md` に指示書を書き、プランのモードでは見せて止まる段が、スキルから消えないこと。
根拠: 2026-11-22 の試験で、成果を一番左右したのは指示書の質だった（Opus が書いた指示書なら、ローカル LLM でも GLM でも
25〜60 分で仕上がった）。Claude が要る部分を減らすため、指示書の型をスキルに入れた（利用者「Claude が要る部分の開発にまず注力」）。
"""
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "app" / "skills" / "bundled" / "controldeck-development" / "SKILL.md"


def test_skill_asks_for_a_plan_before_building():
    text = SKILL.read_text(encoding="utf-8")
    assert "要望から指示書（計画）を書く" in text and ".controldeck/plan.md" in text
    for part in ("目的", "まず読むもの", "作るもの", "守ること", "テスト", "完了の条件", "利用者に決めてもらうこと"):
        assert part in text, part
    assert "プランのモード" in text and "止まる" in text
    assert text.index("要望から指示書") < text.index("共通の進め方")
