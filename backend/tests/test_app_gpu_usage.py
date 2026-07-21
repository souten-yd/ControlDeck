from __future__ import annotations

from pathlib import Path

from app.applications import gpu_usage


def _write_fdinfo(root: Path, pid: int, *, engine_ns: int, vram_kib: int) -> None:
    directory = root / str(pid) / "fdinfo"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "7").write_text(
        "\n".join([
            "pos:\t0",
            "drm-driver:\tamdgpu",
            "drm-pdev:\t0000:03:00.0",
            "drm-client-id:\t42",
            f"drm-engine-gfx:\t{engine_ns} ns",
            f"drm-resident-vram:\t{vram_kib} KiB",
            f"drm-memory-vram:\t{vram_kib * 2} KiB",
        ]),
        encoding="utf-8",
    )


def test_parse_drm_fdinfo_prefers_resident_vram_and_engine_time():
    sample = gpu_usage.parse_fdinfo(
        "drm-driver: amdgpu\ndrm-client-id: 9\ndrm-engine-gfx: 123 ns\n"
        "drm-resident-vram: 8 MiB\ndrm-memory-vram: 16 MiB\n",
        fallback_id="4",
    )
    assert sample is not None
    assert sample.engine_ns == 123
    assert sample.vram_bytes == 8 * 1024**2
    assert sample.client_id == "amdgpu:9"


def test_collect_drm_usage_uses_bounded_deltas_and_process_tree(tmp_path):
    gpu_usage.clear_cache()
    _write_fdinfo(tmp_path, 101, engine_ns=1_000_000_000, vram_kib=1024)
    first_gpu, first_vram = gpu_usage.collect({101, 202}, proc_root=tmp_path, sampled_at=10)
    assert first_gpu is None
    assert first_vram == 1024**2

    # React/APIの近接再取得でゼロsampleを上書きしない。
    near_gpu, _ = gpu_usage.collect({101}, proc_root=tmp_path, sampled_at=10.1)
    assert near_gpu is None

    _write_fdinfo(tmp_path, 101, engine_ns=1_500_000_000, vram_kib=2048)
    second_gpu, second_vram = gpu_usage.collect({101, 202}, proc_root=tmp_path, sampled_at=11)
    assert second_gpu == 50.0
    assert second_vram == 2 * 1024**2


def test_collect_returns_zero_for_app_without_drm_clients(tmp_path):
    directory = tmp_path / "303" / "fdinfo"
    directory.mkdir(parents=True)
    (directory / "1").write_text("pos:\t0\nflags:\t0100000\n", encoding="utf-8")
    assert gpu_usage.collect({303}, proc_root=tmp_path, sampled_at=20) == (0.0, 0)


def test_collect_counts_inherited_drm_client_once_across_process_tree(tmp_path):
    gpu_usage.clear_cache()
    _write_fdinfo(tmp_path, 401, engine_ns=1_000_000_000, vram_kib=1024)
    _write_fdinfo(tmp_path, 402, engine_ns=1_000_000_000, vram_kib=1024)

    first_gpu, first_vram = gpu_usage.collect(
        {401, 402}, proc_root=tmp_path, sampled_at=30, scope_id="app-1:401",
    )
    assert first_gpu is None
    assert first_vram == 1024**2

    _write_fdinfo(tmp_path, 401, engine_ns=1_500_000_000, vram_kib=2048)
    _write_fdinfo(tmp_path, 402, engine_ns=1_500_000_000, vram_kib=2048)
    second_gpu, second_vram = gpu_usage.collect(
        {401, 402}, proc_root=tmp_path, sampled_at=31, scope_id="app-1:401",
    )
    assert second_gpu == 50.0
    assert second_vram == 2 * 1024**2
