"""Metadata continuity for a managed Strata engine and its loopback API process."""
from __future__ import annotations

import json
import os
from pathlib import Path

import psutil

from app.models_mgmt import strata, strata_gateway
from app.models_mgmt.ai_gateway import AITarget, AITargetUnavailable, require_local_target
from app.models_mgmt.generation_provenance import ConfiguredIdentity, REVISION_KIND, _argument, _digest, _file


def configured(target: AITarget) -> ConfiguredIdentity:
    require_local_target(target)
    item = strata_gateway.get_instance(target.model)
    if item['family'] != 'qwen' or not item.get('activated'):
        raise AITargetUnavailable('Strata provenance requires an activated local Qwen model')
    model = Path(strata_gateway._validate_model_path(item['model_path']))
    components = strata._home() / '.local/share/strata/components'
    active = json.loads((components / '.updates/state.json').read_text())['engine']['active']
    engine = (components / 'engine' / active / 'strata').resolve(strict=True)
    if not engine.is_relative_to((components / 'engine').resolve()):
        raise AITargetUnavailable('Strata engine path is invalid')
    pack = strata_gateway.data_dir().resolve() / 'models/strata/packs' / item['model']
    files = {'--executable': _file(engine), '--native': _file(model, gguf=True)}
    if '-00001-of-' in model.name:
        prefix, count = model.name.rsplit('-00001-of-', 1)
        for index in range(2, int(count.removesuffix('.gguf')) + 1):
            files[f'--shard-{index}'] = _file(model.with_name(f'{prefix}-{index:05d}-of-{count}'), gguf=True)
    for name in ('dense.bin', 'experts.bin', 'index.txt', 'native_experts.txt'):
        files['pack:' + name] = _file(pack / name)
    if item.get('drafting', 'auto') != 'off':
        for name in ('dense.bin', 'dense.txt', 'experts.bin', 'draft_vocab.bin'):
            files['mtp:' + name] = _file(strata_gateway.data_dir().resolve() / 'models/strata/mtp/rt' / name)
    if item.get('vision', 'on') != 'off':
        files['--mmproj'] = _file(strata_gateway.data_dir().resolve() / 'models/strata/models/mmproj-Qwen3.8-Flash-Next-BF16.gguf', gguf=True)
    ordered = tuple(sorted(files.items()))
    settings = {key: item.get(key) for key in ('model', 'context', 'kv', 'vision', 'parallel', 'drafting')}
    return ConfiguredIdentity(_digest([REVISION_KIND, ordered, settings]), ordered, target.model, item['port'])


def capture(target: AITarget, expected: ConfiguredIdentity | None) -> dict[str, str]:
    current = configured(target)
    if expected is not None and current != expected:
        raise AITargetUnavailable('Strata configuration changed')
    state_root = strata._home() / '.local/state/strata'
    state = json.loads((state_root / 'runtime' / f'launch-cd-{target.model}.json').read_text())
    host = psutil.Process(int(state['host_pid']))
    processes = [host, *host.children(recursive=True)]
    unit = f'/control-deck-strata-{target.model}.service'
    for process in processes:
        if process.uids().real != os.getuid() or not any(
            line.endswith(unit) for line in Path(f'/proc/{process.pid}/cgroup').read_text().splitlines()
        ):
            raise AITargetUnavailable('Strata process is outside its managed unit')
    servers = [p for p in processes if '-m' in p.cmdline() and _argument(p.cmdline(), '-m') == 'serve.server']
    files = dict(current.files)
    engines = [p for p in processes if str(Path(p.exe()).resolve()) == files['--executable'][0]]
    if len(servers) != 1 or len(engines) != 1:
        raise AITargetUnavailable('Strata process identity cannot be verified')
    server, engine = servers[0], engines[0]
    argv = server.cmdline()
    config = state_root / 'launch' / f'cd-{target.model}.json'
    if (_argument(argv, '--host') != '127.0.0.1' or _argument(argv, '--port') != str(current.port)
            or _argument(argv, '--config') != str(config)):
        raise AITargetUnavailable('Strata API endpoint changed')
    if not any(c.status == psutil.CONN_LISTEN and c.laddr.ip == '127.0.0.1' and c.laddr.port == current.port
               for c in server.net_connections(kind='tcp')):
        raise AITargetUnavailable('Strata API process does not own the loopback endpoint')
    engine_args = engine.cmdline()
    item = strata_gateway.get_instance(target.model)
    if (_argument(engine_args, '--native') != files['--native'][0]
            or _argument(engine_args, '--pack') != str(Path(files['pack:index.txt'][0]).parent)
            or _argument(engine_args, '--max-context') != str(item['ctx_size'])
            or _argument(engine_args, '--kv') != item.get('kv', 'int8')):
        raise AITargetUnavailable('Strata engine model or settings changed')
    if any(max(metadata[-2:]) > int(engine.create_time() * 1_000_000_000) for metadata in files.values()):
        raise AITargetUnavailable('Strata model files changed after engine startup')
    identities = [(p.pid, p.create_time(), p.cmdline()) for p in (host, server, engine)]
    if configured(target) != current or any(psutil.Process(pid).create_time() != started for pid, started, _ in identities):
        raise AITargetUnavailable('Strata identity changed during inspection')
    return {'execution_policy': 'local_only', 'revision_kind': REVISION_KIND,
            'model_ref': current.model_ref, 'runtime_ref': _digest([identities, current.model_ref])}
