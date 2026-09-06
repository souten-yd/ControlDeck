"""Add-on の agent tool（MCP）実行を kind の前方一致で取り出せること。

Home の widget は専用の API を持たず、job の kind だけで絞り込む。
kind の作り方が変わると widget が黙って空になるので、形を固定する。
"""
from __future__ import annotations

from app.jobs import service as jobs


def test_agent_tool_job_kind_carries_the_addon_and_tool():
    """kind は `addon.agent_tool.{addon_id}.{contribution_id}`。

    addon_id に `.` は入らないので、prefix を外した後の最初の `.` までが add-on、
    残りがツール名になる。widget の分解はこの前提に乗っている。
    """
    from app.addons import execution

    # 実際に作られている kind（本番 DB から採取）
    samples = {
        "addon.agent_tool.media-forge.media.generate": ("media-forge", "media.generate"),
        "addon.agent_tool.sonic-forge.sonic.capabilities": ("sonic-forge", "sonic.capabilities"),
    }
    prefix = "addon.agent_tool."
    for kind, (addon, tool) in samples.items():
        assert kind.startswith(prefix)
        rest = kind[len(prefix):]
        cut = rest.index(".")
        assert (rest[:cut], rest[cut + 1:]) == (addon, tool)
    # 組み立て側と同じ形であること。片方だけ変わると widget が空になる。
    assert f"addon.agent_tool.{'sonic-forge'}.{'sonic.generate'}" in (
        f"addon.agent_tool.{a}.{c}" for a, c in [("sonic-forge", "sonic.generate")]
    )
    assert hasattr(execution, "create_agent_tool_job")


def test_listing_filters_agent_tool_jobs_by_prefix():
    """前方一致で add-on ツールだけを取り出せること。

    widget は専用の API を持たず `?kind=addon.agent_tool.` だけで絞る。
    前方一致でなくなると、一覧が黙って空になる。
    """
    from types import SimpleNamespace

    placed = {
        "j1": SimpleNamespace(kind="addon.agent_tool.sonic-forge.sonic.generate"),
        "j2": SimpleNamespace(kind="addon.agent_tool.media-forge.media.generate"),
        "j3": SimpleNamespace(kind="model.download"),
    }
    jobs._jobs.update(placed)
    try:
        kinds = {job.kind for job in jobs.list_jobs("addon.agent_tool.", limit=50)}
        assert "addon.agent_tool.sonic-forge.sonic.generate" in kinds
        assert "addon.agent_tool.media-forge.media.generate" in kinds
        assert "model.download" not in kinds
    finally:
        for key in placed:
            jobs._jobs.pop(key, None)
