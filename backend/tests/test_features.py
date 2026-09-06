

def test_the_feature_list_is_not_probed_on_every_request(monkeypatch):
    """一覧は少しの間だけ覚えておく。

    status() は feature ごとに systemctl を起動する。7 件で subprocess が 10 回、
    実測 430ms かかっていた。/api/v1/meta がこれを呼び、画面は meta を待ってから
    描き始めるので、開くたびに 0.4 秒何も出ない時間ができていた。
    """
    from app.features import registry

    registry.invalidate_feature_cache()
    calls = []

    def counted(feature_id):
        calls.append(feature_id)
        return {"id": feature_id, "enabled": True}

    monkeypatch.setattr(registry, "status", counted)
    first = registry.list_features()
    second = registry.list_features()
    assert first == second
    assert len(calls) == len(registry.KNOWN_FEATURES), "2 回目も探りに行っている"

    # 呼び出し側が書き換えても、覚えた値が壊れないこと
    second[0]["enabled"] = False
    assert registry.list_features()[0]["enabled"] is True

    # 状態を変えたら次は探り直す
    registry.invalidate_feature_cache()
    registry.list_features()
    assert len(calls) == len(registry.KNOWN_FEATURES) * 2
