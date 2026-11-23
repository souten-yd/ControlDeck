"""Add-on が名乗ったアイコンが、実際に画面まで届くか。

実測: Quick Actions の Add-on 項目が Apps と同じ一覧アイコンで描かれていて、
どちらを押せばよいのか分からなかった。manifest には icon が書いてあり、
payload にも載っていたが、UI がそれを読まずに固定の形を使っていた。

片側だけ直しても意味が無い経路なので、両端を見る。
"""

from __future__ import annotations

from pathlib import Path

from app.addons.schema import AddonManifestV2

FRONTEND = Path(__file__).parents[2] / "frontend" / "src"


def manifest(icon: str | None) -> AddonManifestV2:
    navigation: dict = {
        "id": "workspace", "label": {"en": "Example"}, "route": "/x/example/workspace",
    }
    if icon is not None:
        navigation["icon"] = icon
    return AddonManifestV2.model_validate({
        "api_version": "2", "id": "example", "name": "Example", "version": "1.0.0",
        "publisher": "tester",
        "runtime": {"kind": "external-service", "base_url": "http://127.0.0.1:9199"},
        "contributions": {"navigation": [navigation]},
    })


def test_the_declared_icon_survives_serialisation():
    """payload から落ちると、UI は何を描けばよいか知りようがない。"""
    value = manifest("images").contributions.navigation[0].model_dump(mode="json")

    assert value["icon"] == "images"


def test_an_addon_may_leave_the_icon_out():
    assert manifest(None).contributions.navigation[0].model_dump(mode="json")["icon"] is None


def test_the_ui_reads_the_declared_icon_wherever_addon_navigation_is_built():
    """以前はサイドバーと Quick Actions の 2 か所で項目を作っていて、片方だけ直すと
    Apps と同じ形のままになった。いまはメニューの候補（navigationEntries.tsx の
    useNavigationCandidates）の 1 か所で作り、ドロワー・PC のサイドバー・設定の「メニューの表示」が
    それを使う（2026-11-23 に AppLayout から移した）。項目を作る所が増えても、どれもが宣言されたアイコンを読むこと。"""
    layout = "".join((FRONTEND / name).read_text(encoding="utf-8")
                     for name in ("layouts/AppLayout.tsx", "navigationEntries.tsx"))

    builds = layout.count("addonNavigation.map(")
    assert builds >= 1
    assert layout.count("addonIcon(contribution.icon, contribution.addon_id)") == builds


def test_an_unknown_icon_never_falls_back_to_the_apps_glyph():
    """知らない名前を Apps の一覧アイコンに落とすと、Add-on が Apps と並んだ
    ときに見分けられなくなる。差し込まれた機能だと分かる形にする。"""
    icons = (FRONTEND / "components" / "icons.tsx").read_text(encoding="utf-8")
    fallback = icons[icons.index("export function addonIcon"):]

    assert "IconPlugin" in fallback
    assert "IconGrid" not in fallback


def test_first_party_addons_declared_icons_are_drawn():
    """SonicForge は "audio" を名乗る。名前空間に無いとプラグインの形に落ちて、
    以前の音の形が消えた（実測）。特許書類の形（patent）は本体の Patent 画面が使う。"""
    icons = (FRONTEND / "components" / "icons.tsx").read_text(encoding="utf-8")
    table = icons[icons.index("const ADDON_ICONS"):icons.index("export function addonIcon")]

    assert "audio: IconAudio" in table
    assert '"book-open": IconBookOpen' in table
    assert "patent: IconPatent" in table
