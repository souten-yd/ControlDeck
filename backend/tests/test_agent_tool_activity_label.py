"""Add-on ツールの実行中表示が、何をしているか分かる語になること。

kind（addon.agent_tool.media-forge.media.generate）だけでは、画像を作っている
のか動画なのか分からない。種別は引数にしか無いので、job を作る時に拾う。
拾えないと画面には「media-forge: media.generate」としか出ず、利用者からは
何が起きているのか分からない。
"""
from __future__ import annotations

import pytest

from app.addons.execution import agent_tool_activity


@pytest.mark.parametrize(
    ("contribution_id", "arguments", "expected"),
    [
        # MediaForge は引数の operation で決まる。同じ media.generate でも中身が違う。
        ("media.generate", {"operation": "image.generate"}, "Generating image"),
        ("media.generate", {"operation": "video.generate"}, "Generating video"),
        ("media.generate", {"operation": "image.edit"}, "Editing image"),
        # SonicForge は引数の task。
        ("sonic.generate", {"task": "music.generate"}, "Generating music"),
        ("sonic.generate", {"task": "speech.tts.synthesize"}, "Generating speech"),
        ("sonic.generate", {"task": "audio.sfx.generate"}, "Generating sound effect"),
        # 3D は引数に種別が出ないので contribution_id で決める。
        ("media.scene.create", {}, "Generating 3D scene"),
        ("media.scene.export", {"scene_id": "x"}, "Exporting 3D asset"),
        ("sonic.transcribe", {}, "Transcribing audio"),
    ],
)
def test_activity_is_readable(contribution_id, arguments, expected):
    assert agent_tool_activity(contribution_id, arguments) == expected


def test_unknown_tool_has_no_label():
    """割り出せないものに嘘の名前を付けない。呼び出し側が従来の表記へ落とす。"""
    assert agent_tool_activity("addon.something.new", {"operation": "unheard.of"}) is None


def test_label_is_english_only():
    """画面の中で言葉を混ぜない。add-on の label もツール名も英語である。"""
    from app.addons import execution

    labels = [
        *execution._AGENT_TOOL_ACTIVITY.values(),
        *execution._AGENT_TOOL_FALLBACK.values(),
    ]
    assert labels, "ラベルが空になっている"
    for label in labels:
        assert label.isascii(), label
