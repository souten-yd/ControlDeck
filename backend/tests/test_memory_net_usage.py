"""RAM の正味の利用量。

目的: Home・履歴・警告の RAM の割合が、プログラムが映して使っているファイル（モデル）を含めた正味の量になること。
根拠: 2026-11-17 DS-XS（Maya）でモデル約 75GiB をメモリに映して RAM をほぼ使い切っていたのに、psutil の
percent（キャッシュを数えない）で 15% と出ていた（利用者「正味の利用量を出して欲しい」）。
"""
import io
from types import SimpleNamespace

MEMINFO = """MemTotal:       96304120 kB
MemFree:          584876 kB
MemAvailable:   81663120 kB
Buffers:           73112 kB
Cached:         81030376 kB
Shmem:             28276 kB
Mapped:         78878600 kB
SReclaimable:     954532 kB
"""


def test_mapped_model_files_count_as_used_but_idle_cache_does_not(monkeypatch):
    from app.monitoring import collector

    monkeypatch.setattr(collector, "open", lambda *a, **k: io.StringIO(MEMINFO), raising=False)
    mem = SimpleNamespace(used=14_000_000_000, percent=15.2)
    result = collector.net_memory(mem)
    # 96304120 - 584876 - 73112 - 954532 - (81030376 - 78878600 - 28276) = 92568100 kB
    assert result["used"] == 92_568_100 * 1024
    assert result["percent"] == 96.1
    assert result["mapped_files"] == 78_878_600 * 1024
    assert result["used_by_programs"] == 14_000_000_000


def test_falls_back_to_psutil_when_meminfo_is_unreadable(monkeypatch):
    from app.monitoring import collector

    def broken(*args, **kwargs):
        raise OSError("no proc")

    monkeypatch.setattr(collector, "open", broken, raising=False)
    result = collector.net_memory(SimpleNamespace(used=5, percent=1.0))
    assert result["used"] == 5 and result["percent"] == 1.0 and result["mapped_files"] is None
