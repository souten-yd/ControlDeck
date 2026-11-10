from pathlib import Path
from types import SimpleNamespace
import time

import pytest

from app.models_mgmt.ai_gateway import AITarget, AITargetUnavailable
from app.models_mgmt import generation_provenance as provenance


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from app.models_mgmt import llama
    from app.applications import systemd
    model = tmp_path / 'private-model.gguf'; model.write_bytes(b'synthetic')
    binary = tmp_path / 'llama-server'; binary.write_bytes(b'synthetic-binary')
    target = AITarget('http://127.0.0.1:18080/v1', 'private-alias', True)
    instance = dict(alias=target.model, base_url=target.base_url, model_path=str(model), port=18080)
    monkeypatch.setattr(llama, 'list_instances', lambda: [instance])
    monkeypatch.setattr(llama, 'get_instance', lambda _: instance)
    monkeypatch.setattr(llama, 'server_path', lambda: binary)
    state = dict(status='RUNNING', pid=123)
    monkeypatch.setattr(systemd, 'query_status', lambda _: state)
    started = time.time() + 1
    argv = [str(binary), '--model', str(model), '--alias', target.model, '--port', '18080', '--host', '127.0.0.1']
    class Process:
        def create_time(self): return started
        def cmdline(self): return argv
        def exe(self): return str(binary)
        def uids(self): return SimpleNamespace(real=provenance.os.getuid())
        def net_connections(self, kind): return [SimpleNamespace(status='LISTEN', laddr=SimpleNamespace(ip='127.0.0.1', port=18080))]
    monkeypatch.setattr(provenance.psutil, 'Process', lambda _: Process())
    return target, model, binary, instance, state, argv, Process


def test_public_identity_contains_no_alias_path_or_pid(runtime):
    target, model, binary, *_ = runtime
    selected = provenance.configured(target)
    value = provenance.capture(target, selected)
    assert set(value) == {'execution_policy', 'revision_kind', 'model_ref', 'runtime_ref'}
    assert value['execution_policy'] == 'local_only'
    assert value['revision_kind'] == provenance.REVISION_KIND
    assert len(value['model_ref']) == len(value['runtime_ref']) == 64
    assert provenance.capture(target, selected) == value
    for private in (target.model, str(model), str(binary), target.base_url):
        assert private not in str(value)


def test_files_or_config_swapped_between_selection_and_inference_rejected(runtime):
    target, model, _, instance, *_ = runtime
    selected = provenance.configured(target)
    model.write_bytes(b'changed')
    with pytest.raises(AITargetUnavailable): provenance.capture(target, selected)
    instance['base_url'] = 'http://192.0.2.1:18080/v1'
    with pytest.raises(AITargetUnavailable): provenance.configured(target)


@pytest.mark.parametrize('field', ['model', 'binary', 'alias', 'port', 'owner', 'listener', 'stopped'])
def test_serving_process_must_match_selected_instance(runtime, monkeypatch, tmp_path, field):
    target, model, binary, _, state, argv, process = runtime
    if field == 'model': argv[2] = str(binary)
    if field == 'binary': monkeypatch.setattr(process, 'exe', lambda _: str(model))
    if field == 'alias': argv[4] = 'another-alias'
    if field == 'port': argv[6] = '18081'
    if field == 'owner': monkeypatch.setattr(process, 'uids', lambda _: SimpleNamespace(real=-1))
    if field == 'listener': monkeypatch.setattr(process, 'net_connections', lambda *a, **kw: [])
    if field == 'stopped': state['status'] = 'STOPPED'
    with pytest.raises(AITargetUnavailable): provenance.capture(target)


def test_file_modified_since_process_start_is_not_attested(runtime, monkeypatch):
    target, model, _, _, _, _, process = runtime
    monkeypatch.setattr(process, 'create_time', lambda _: model.stat().st_ctime - 5)
    with pytest.raises(AITargetUnavailable): provenance.capture(target)


@pytest.mark.parametrize('kind', ['projector', 'draft'])
def test_optional_projector_and_draft_are_part_of_identity(runtime, tmp_path, kind):
    target, _, _, instance, _, argv, _ = runtime
    before = provenance.capture(target)
    projector = tmp_path / 'mmproj.gguf'; projector.write_bytes(b'projector')
    flag = '--mmproj' if kind == 'projector' else '--spec-draft-model'
    instance['mmproj_path' if kind == 'projector' else 'spec_draft_model_path'] = str(projector)
    if kind == 'draft': instance['spec_type'] = 'draft-simple'
    with pytest.raises(AITargetUnavailable): provenance.capture(target)
    argv.extend([flag, str(projector)])
    after = provenance.capture(target)
    assert after['model_ref'] != before['model_ref']
