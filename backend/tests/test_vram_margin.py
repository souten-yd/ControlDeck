"""Common headroom survives saves and reaches the native engine interfaces."""
import json

import pytest
from pydantic import ValidationError

from app.models_mgmt import runtime_policy as rp


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, '_path', lambda: tmp_path / 'policy.json')
    rp.save_policy(rp.RuntimePolicy(vram_margin_gib=3))
    return tmp_path


def test_validation_and_rounding(policy):
    assert rp.vram_margin_mib() == 3072
    for value in [-1, 257, float('inf'), float('nan')]:
        with pytest.raises(ValidationError):
            rp.RuntimePolicy(vram_margin_gib=value)
    rp.save_policy(rp.RuntimePolicy(vram_margin_gib=0.0001))
    assert rp.vram_margin_mib() == 1
    rp.save_policy(rp.RuntimePolicy(vram_margin_gib=0))
    assert rp.vram_margin_mib() == 0


def test_margin_save_does_not_stop_or_reconfigure_running_models(policy, monkeypatch, admin_client):
    from app.models_mgmt import amd_gpu

    def forbidden(*a, **kw):
        pytest.fail('margin-only save must not alter a running runtime')

    async def environment():
        return {'policy': rp.get_policy().model_dump()}

    monkeypatch.setattr(rp, 'apply_selection', forbidden)
    monkeypatch.setattr(amd_gpu, 'apply_profile', forbidden)
    monkeypatch.setattr(rp, 'environment', environment)
    response = admin_client.put('/api/v1/models/runtime-policy', json={'vram_margin_gib': 4},
                                headers={'X-Requested-With': 'ControlDeck'})
    assert response.status_code == 200, response.text
    assert rp.vram_margin_mib() == 4096
    assert admin_client.put('/api/v1/models/runtime-policy', json={'vram_margin_gib': -1},
                           headers={'X-Requested-With': 'ControlDeck'}).status_code == 422
    assert rp.vram_margin_mib() == 4096


@pytest.mark.parametrize('family,flag,value', [('glm', '--vram-reserve-mib', '3072'),
    ('qwen', '--vram-reserve-mib', '3072'), ('deepseek', '--reserve-mib', '3072'),
    ('minimax', '--reserve-gb', '3')])
def test_maya_native_flags_and_decreasing_margin(policy, family, flag, value):
    from app.models_mgmt import maya_s

    source = policy / 'maya-original.json'
    original = {'args': ['--max-context', '8192', flag, '0.5' if family == 'minimax' else '700'],
                'env': {'KEEP': 'yes'}}
    source.write_text(json.dumps(original))
    record = {'alias': 'test', 'config': source, 'family': family}
    target = maya_s._apply_vram_margin(record)
    cfg = json.loads(target.read_text())
    assert cfg['args'][-2:] == [flag, value]
    assert cfg['env'] == original['env']
    assert target == source
    assert cfg["control_deck_vram_margin"]["original"] == original["args"][-1]
    rp.save_policy(rp.RuntimePolicy(vram_margin_gib=2))
    cfg = json.loads(maya_s._apply_vram_margin(record).read_text())
    assert cfg['args'][-1] == ('2' if family == 'minimax' else '2048')


def test_duplicate_flags_larger_reserve_and_invalid_values(policy):
    assert rp.with_margin_arg(['--x', 'y', '--reserve=4096', '--reserve', '700'], '--reserve') == [
        '--x', 'y', '--reserve', '4096']
    for args in [['--reserve'], ['--reserve', 'nan'], ['--reserve=-1']]:
        with pytest.raises(ValueError):
            rp.with_margin_arg(args, '--reserve')


def test_radiance_uses_common_minimum_without_changing_model_config(policy):
    from app.models_mgmt import radiance

    cfg = {'model_path': '/models/test.rad', 'port': 8100, 'gpu_headroom_mib': 700,
           'context': 8192, 'max_num_seqs': 1, 'kv_cache': 'bf16'}
    assert 'gpu-headroom-mib: 3072' in radiance.config_yaml('test', cfg)
    assert cfg['gpu_headroom_mib'] == 700
    cfg['gpu_headroom_mib'] = 4096
    assert 'gpu-headroom-mib: 4096' in radiance.config_yaml('test', cfg)


def test_llama_fit_margin_and_cpu_only(policy, monkeypatch):
    from app.models_mgmt import llama

    cfg = {**llama.DEFAULT_INSTANCE, 'alias': 'margin', 'model_path': '/models/test.gguf'}
    monkeypatch.setattr(llama, 'get_instance', lambda _: cfg)
    monkeypatch.setattr(llama, '_help_text_sync', lambda: '--fit-target --load-mode')
    monkeypatch.setattr(llama, 'is_installed', lambda: True)
    text = llama._unit_content('margin')
    import shlex
    args = shlex.split(next(line.split('=', 1)[1] for line in text.splitlines() if line.startswith('ExecStart=')))
    assert args[args.index('--fit-target') + 1] == '3072'
    assert args[args.index('--n-gpu-layers') + 1] == 'auto'
    cfg['n_gpu_layers'] = 0
    assert '--fit-target' not in llama._unit_content('margin')
    cfg['n_gpu_layers'] = 999
    monkeypatch.setattr(llama, '_help_text_sync', lambda: '')
    with pytest.raises(RuntimeError, match='fit-target'):
        llama._unit_content('margin')


@pytest.mark.parametrize('family,key', [('qwen', 'vram-reserve-mib'), ('unsloth', 'vram-reserve-mib'), ('deepseek41', 'reserve-mib')])
def test_strata_reserve_flag_for_each_engine(policy, family, key):
    from app.models_mgmt import strata_gateway

    cfg = {'family': family, 'engine_settings': {key: '700'}}
    assert strata_gateway.launch_engine_settings(cfg)[key] == '3072'
    cfg['engine_settings'][key] = '4096'
    assert strata_gateway.launch_engine_settings(cfg)[key] == '4096'


def test_llama_fixed_layers_fail_instead_of_silently_ignoring_margin(policy, monkeypatch):
    from app.models_mgmt import llama

    monkeypatch.setattr(llama, 'get_instance', lambda _: {**llama.DEFAULT_INSTANCE,
        'alias': 'fixed', 'model_path': '/models/test.gguf', 'n_gpu_layers': 32})
    monkeypatch.setattr(llama, '_help_text_sync', lambda: '--fit-target')
    monkeypatch.setattr(llama, 'is_installed', lambda: True)
    with pytest.raises(RuntimeError, match='固定層数'):
        llama._unit_content('fixed')
    assert '--fit-target' not in llama._unit_content('fixed', margin_mib=0)


def test_llama_persisted_boot_units_receive_margin_without_restart(policy, monkeypatch):
    from app.models_mgmt import llama
    from app.applications import systemd as sd

    monkeypatch.setattr(llama, 'is_installed', lambda: True)
    monkeypatch.setattr(llama, 'list_instances', lambda: [{'alias': 'chat', 'model_path': '/m'}])
    monkeypatch.setattr(llama, '_unit_content', lambda alias, *, margin_mib: f'margin={margin_mib}')
    writes = []
    monkeypatch.setattr(sd, 'write_unit', lambda *args: writes.append(args))
    monkeypatch.setattr(sd, 'restart', lambda *a: pytest.fail('must not restart'))
    llama.sync_vram_margin(4096)
    assert writes == [(llama.unit_name('chat'), 'margin=4096')]


def test_same_displayed_default_in_legacy_policy_refreshes_units(policy, monkeypatch, admin_client):
    from app.models_mgmt import llama

    (policy / 'policy.json').write_text('{}')
    calls = []
    monkeypatch.setattr(llama, 'sync_vram_margin', lambda value: calls.append(value))
    async def environment():
        return {'policy': rp.get_policy().model_dump()}
    monkeypatch.setattr(rp, 'environment', environment)
    response = admin_client.put('/api/v1/models/runtime-policy', json={'vram_margin_gib': 3},
                               headers={'X-Requested-With': 'ControlDeck'})
    assert response.status_code == 200, response.text
    assert calls == [3072]


def test_maya_duplicate_original_reserves_and_native_edits_are_preserved(policy):
    from app.models_mgmt import maya_s

    path = policy / 'maya.json'
    path.write_text(json.dumps({'args': ['--reserve-mib', '700', '--reserve-mib=4096']}))
    record = {'alias': 'test', 'family': 'deepseek', 'config': path}
    maya_s._apply_vram_margin(record)
    rp.save_policy(rp.RuntimePolicy(vram_margin_gib=2))
    maya_s._apply_vram_margin(record)
    cfg = json.loads(path.read_text())
    assert cfg['args'][-1] == '4096'
    cfg['args'][-1] = '5120'
    cfg['args'] += ['--max-context', '65536']
    path.write_text(json.dumps(cfg))
    maya_s._apply_vram_margin(record)
    cfg = json.loads(path.read_text())
    assert cfg['args'][-1] == '5120'
    assert cfg['control_deck_vram_margin']['original'] == '5120'
    assert '--max-context' in cfg['args']
