"""推奨スキルの導入・更新・無効化・削除。

スキルは OpenCode が読む手順書で、ControlDeck のデータ配下にだけ置く。利用者の
`~/.claude/skills` や `~/.config/opencode` へは書かない——そこは利用者自身のもので、
消し忘れると ControlDeck を使っていない OpenCode の挙動まで変えてしまう。
"""

from __future__ import annotations

import pytest

from app.skills import catalog, registry


@pytest.fixture()
def _root(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "data_dir", lambda: tmp_path)
    return tmp_path


def _bundled_id() -> str:
    return catalog.BUNDLED[0].id


def test_every_bundled_skill_ships_a_document():
    """同梱したのに SKILL.md が無いと、OpenCode は黙って無視する。"""
    for entry in catalog.BUNDLED:
        source = catalog.bundled_source(entry.id)
        assert source.is_dir(), entry.id
        assert registry.has_skill_document(source), entry.id


def test_bundled_documents_declare_a_matching_name():
    """frontmatter の name は folder 名と一致していないと読まれない。"""
    for entry in catalog.BUNDLED:
        text = (catalog.bundled_source(entry.id) / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        assert f"\nname: {entry.id}\n" in text
        # description が無いスキルは model へ出てこない。
        assert "\ndescription: " in text


def test_install_enable_disable_remove(_root):
    skill_id = _bundled_id()

    before = registry.status(skill_id)
    assert before["installed"] is False and before["enabled"] is False
    assert registry.enabled_paths() == []

    installed = registry.install(skill_id)
    assert installed["installed"] is True and installed["enabled"] is True
    assert len(registry.enabled_paths()) == 1

    off = registry.set_enabled(skill_id, False)
    assert off["installed"] is True and off["enabled"] is False
    # 無効化はファイルを消さない。もう一度有効にしても取り直さない。
    assert registry.enabled_paths() == []
    assert registry.has_skill_document(_root / "skills" / "versions" / skill_id / off["installed_version"])

    on = registry.set_enabled(skill_id, True)
    assert on["enabled"] is True and len(registry.enabled_paths()) == 1

    gone = registry.remove(skill_id)
    assert gone["installed"] is False
    assert not (_root / "skills" / "versions" / skill_id).exists()
    assert registry.enabled_paths() == []


def test_reinstall_keeps_a_deliberate_disable(_root):
    """入れ直しで、利用者が切っていたものを勝手に戻さない。"""
    skill_id = _bundled_id()
    registry.install(skill_id)
    registry.set_enabled(skill_id, False)
    assert registry.install(skill_id)["enabled"] is False


def test_a_missing_install_is_not_reported_as_installed(_root):
    """記録だけ残って実体が消えていたら、導入済みとは言わない。"""
    import shutil

    skill_id = _bundled_id()
    registry.install(skill_id)
    shutil.rmtree(_root / "skills" / "versions" / skill_id)
    assert registry.status(skill_id)["installed"] is False
    assert registry.enabled_paths() == []


def test_unknown_skills_are_refused(_root):
    with pytest.raises(registry.SkillError):
        registry.install("../../etc/passwd")


def test_external_skills_state_what_they_need():
    """実行環境が別に要るものは、導入する前にそれが分かること。"""
    for entry in catalog.EXTERNAL:
        assert entry.requires, entry.id
        # 動く先を指すと、同じ導入操作が日によって別物を持ってくる。
        assert len(entry.ref) == 40, entry.id


def test_the_runtime_config_only_carries_enabled_skills(_root, monkeypatch):
    from app.integrations.opencode import provider

    assert provider._skill_paths() == []
    registry.install(_bundled_id())
    assert len(provider._skill_paths()) == 1
