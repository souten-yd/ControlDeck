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


def test_the_ui_reads_the_declared_icon_in_both_places():
    """サイドバーだけ直して Quick Actions を忘れると、片方だけ Apps と同じ形の
    ままになる。実際にそうなっていた。"""
    layout = (FRONTEND / "layouts" / "AppLayout.tsx").read_text(encoding="utf-8")

    assert layout.count("addonIcon(contribution.icon)") == 2


def test_an_unknown_icon_never_falls_back_to_the_apps_glyph():
    """知らない名前を Apps の一覧アイコンに落とすと、Add-on が Apps と並んだ
    ときに見分けられなくなる。差し込まれた機能だと分かる形にする。"""
    icons = (FRONTEND / "components" / "icons.tsx").read_text(encoding="utf-8")
    fallback = icons[icons.index("export function addonIcon"):]

    assert "IconPlugin" in fallback
    assert "IconGrid" not in fallback
