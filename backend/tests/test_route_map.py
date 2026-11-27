"""情報の伝達ルートの地図（backend/app/route_map.py）がコードの実際と食い違わないこと。

目的: 新しい AI・道具・通知・外部への出口・tmux への送り込みを足したのに地図へ書き忘れると落とし、
docs/ROUTE-MAP.md が地図のデータと一致していることを守る。地図に無いルートを足すと関門で落ちる。
根拠: 2026-11-27 利用者「情報伝達のルートを調査して明確にし、新たに AI を追加したり機能を開発する際は
そのルートマップを保守する方法で開発を進めれば迷わないのではないか」。
"""
import re
from pathlib import Path

from app import route_map

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "backend" / "app"
FIX = "backend/app/route_map.py に足して ./deck.sh route-map で docs/ROUTE-MAP.md を作り直してください"


def _missing(kind: str, names) -> str:
    return f"地図に無いルートがあります: {kind} {sorted(names)}。{FIX}"


def _sources() -> dict[str, str]:
    return {p.relative_to(REPO).as_posix(): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(APP.rglob("*.py")) if p != APP / "route_map.py"}


def _callers_of(pattern: str) -> set[str]:
    regex = re.compile(pattern)
    return {path for path, text in _sources().items() if regex.search(text)}


def _owners(channel: str) -> set[str]:
    return {path for route in route_map.ROUTES if route.channel == channel for path in route.owner}


def test_map_data_is_well_formed():
    assert route_map.validate() == []


def test_owner_files_exist_and_ids_are_unique():
    ids = [route.id for route in route_map.ROUTES]
    assert len(ids) == len(set(ids)), f"id が重なっています: {sorted({i for i in ids if ids.count(i) > 1})}"
    gone = [f"{route.id}: {path}" for route in route_map.ROUTES for path in route.owner if not (REPO / path).is_file()]
    assert not gone, f"owner のファイルがありません（移動・削除したら地図を直す）: {gone}"


def test_every_gateway_caller_is_on_the_map():
    from app.models_mgmt import cloud

    mapped = {name for route in route_map.ROUTES for name in route.callers}
    assert not set(cloud.CALLERS) - mapped, _missing("ゲートウェイの呼び出し元", set(cloud.CALLERS) - mapped)
    assert not mapped - set(cloud.CALLERS), f"地図にだけある呼び出し元です（コードから消えた？）: {sorted(mapped - set(cloud.CALLERS))}"


def test_every_mcp_tool_is_on_the_map():
    from app.addons import agent_mcp

    actual = {value for name, value in vars(agent_mcp).items()
              if name.endswith("_TOOL") and isinstance(value, str) and value.startswith("control_deck.")}
    mapped = {name for route in route_map.ROUTES for name in route.tools}
    assert actual, "MCP の道具の定数が見つかりません（agent_mcp.py の *_TOOL の命名が変わった？）"
    assert not actual - mapped, _missing("MCP の道具", actual - mapped)
    assert not mapped - actual, f"地図にだけある道具です: {sorted(mapped - actual)}"


def test_sandbox_allowed_tools_are_known_tools():
    from app.maintenance import sandbox_gateway

    mapped = {name for route in route_map.ROUTES for name in route.tools}
    assert not set(sandbox_gateway.MCP_ALLOWED_TOOLS) - mapped, _missing("箱から呼べる道具", set(sandbox_gateway.MCP_ALLOWED_TOOLS) - mapped)


def test_every_notification_kind_is_on_the_map():
    from app.alerts import events

    actual = {kind.id for kind in events.KINDS}
    mapped = {name for route in route_map.ROUTES for name in route.kinds}
    assert not actual - mapped, _missing("通知の種類", actual - mapped)
    assert not mapped - actual, f"地図にだけある通知の種類です: {sorted(mapped - actual)}"


def test_every_external_egress_file_is_an_egress_owner():
    callers = _callers_of(r"(?<!def )\bensure_external_allowed\(") | _callers_of(r"\bguarded_(client|transport)\(")
    callers -= {"backend/app/security/offline.py"}
    missing = callers - _owners("egress")
    assert not missing, _missing("外部への出口（ensure_external_allowed・guarded_client を使うファイル）", missing)


def test_every_tmux_injection_file_is_a_tmux_owner():
    callers = _callers_of(r"(?<!def )\binject_input\(")
    missing = callers - _owners("tmux")
    assert not missing, _missing("tmux への送り込み（inject_input を呼ぶファイル）", missing)


def test_route_map_doc_matches_the_data():
    doc = route_map.DOC
    assert doc.is_file(), f"docs/ROUTE-MAP.md がありません。./deck.sh route-map で作ってください"
    assert doc.read_text(encoding="utf-8") == route_map.render(), \
        "docs/ROUTE-MAP.md が backend/app/route_map.py と食い違っています。./deck.sh route-map で作り直してください"
