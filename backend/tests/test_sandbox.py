"""The Python sandbox probe, readiness and preflight.

A fake docker executable on a controlled PATH stands in for Docker, so these tests run
the real probe - subprocesses, timeout, kill and coalescing - without depending on this
machine's Docker. Marker files beside the fake select its behaviour, and every call is
logged so probe counts can be asserted.
"""
import asyncio
import json
import os
import signal
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from backend.app import sandbox
from backend.app.main import create_app
from backend.app.models import Workflow

pytestmark = pytest.mark.real_sandbox

FAKE_DOCKER = r'''#!/bin/sh
dir="$(dirname "$0")"
echo "$1 $2" >> "$dir/calls"
if [ -f "$dir/sleep" ]; then echo $$ > "$dir/pid"; exec sleep "$(cat "$dir/sleep")"; fi
case "$1" in
  version) if [ -f "$dir/daemon_down" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi; echo 29.0;;
  image) if [ -f "$dir/image_missing" ]; then echo "Error: No such image" >&2; exit 1; fi; echo sha256:abc;;
  run) echo "$@" >> "$dir/argv"
       if [ -f "$dir/run_sleep" ]; then echo $$ > "$dir/pid"; exec sleep "$(cat "$dir/run_sleep")"; fi
       if [ -f "$dir/run_fails" ]; then echo "docker: Error response from daemon: OCI runtime create failed" >&2; exit 125; fi
       if [ -f "$dir/run_wrong" ]; then echo 2; exit 0; fi
       case "$*" in *"print(1)"*) echo 1;; *) cat > /dev/null; echo executed;; esac;;
  rm) ;;
esac
'''


class Docker:
    """Controls the fake docker on PATH."""
    def __init__(self, directory):
        self.dir = directory
    def set(self, name, value=''):
        (self.dir / name).write_text(str(value))
    def clear(self, name):
        (self.dir / name).unlink(missing_ok=True)
    def calls(self):
        path = self.dir / 'calls'
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def docker(tmp_path, monkeypatch):
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    (bin_dir / 'docker').write_text(FAKE_DOCKER); (bin_dir / 'docker').chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}:/usr/bin:/bin')
    monkeypatch.setattr(sandbox, 'PROBE', sandbox.SandboxProbe())
    return Docker(bin_dir)


@pytest.fixture
def spawned(monkeypatch):
    """Every process the probe starts. A hung-docker test checks these handles rather than a
    pid the fake writes, because a timeout can fire before the fake shell runs its first
    line - the probe is then correct, and a pid file would simply be missing."""
    processes = []
    original = asyncio.create_subprocess_exec
    async def record(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append((args[1:], process))
        return process
    monkeypatch.setattr(sandbox.asyncio, 'create_subprocess_exec', record)
    return processes


def killed_and_reaped(processes):
    """The docker subcommands that were killed, once every started process is confirmed gone."""
    assert processes, 'the probe started docker'
    for _, process in processes:
        assert process.returncode is not None, 'every process the probe started has been reaped'
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
    return [args[0] for args, process in processes if process.returncode == -signal.SIGKILL]


def probe_now():
    sandbox.PROBE = sandbox.SandboxProbe()
    return asyncio.run(sandbox.status())


# --------------------------------------------------------------------------- the probe

def test_a_healthy_sandbox_is_ok(docker):
    assert probe_now() == {'status': 'ok'}
    assert docker.calls() == ['version --format', 'image inspect', 'run --rm']


def test_a_missing_cli_is_reported(tmp_path, monkeypatch):
    empty = tmp_path / 'empty'; empty.mkdir()
    # Only the empty directory: CI runners install a real docker in /usr/bin.
    monkeypatch.setenv('PATH', str(empty))
    monkeypatch.setattr(sandbox, 'PROBE', sandbox.SandboxProbe())
    assert probe_now() == {'status': 'unavailable', 'reason': 'cli_missing'}


def test_a_stopped_daemon_is_reported(docker):
    docker.set('daemon_down')
    assert probe_now() == {'status': 'unavailable', 'reason': 'daemon_unavailable'}
    assert docker.calls() == ['version --format'], 'the image is not inspected when the daemon is down'


def test_a_missing_image_is_reported(docker):
    docker.set('image_missing')
    assert probe_now() == {'status': 'unavailable', 'reason': 'image_missing'}


def test_a_hung_docker_times_out_and_is_stopped(docker, spawned, monkeypatch):
    # The budget only has to outlast starting a process; the fake then sleeps for 30 seconds.
    monkeypatch.setattr(sandbox, 'PROBE_SECONDS', 1.0)
    docker.set('sleep', 30)
    assert probe_now() == {'status': 'unavailable', 'reason': 'probe_timeout'}
    assert killed_and_reaped(spawned) == ['version'], 'the hung docker process was killed, not left running'


def test_concurrent_callers_share_one_probe_and_results_are_cached(docker, monkeypatch):
    docker.set('sleep', 0.2)
    async def burst():
        sandbox.PROBE = sandbox.SandboxProbe()
        first = await asyncio.gather(*(sandbox.status() for _ in range(10)))
        cached = await sandbox.status()
        return first, cached
    monkeypatch.setattr(sandbox, 'PROBE_SECONDS', 2)
    first, cached = asyncio.run(burst())
    assert len({json.dumps(r) for r in first}) == 1 and cached == first[0]
    assert len(docker.calls()) == 3, f'one probe (version, image, smoke run), not ten: {docker.calls()}'


def test_recovery_is_noticed_once_the_cache_expires(docker, monkeypatch):
    docker.set('daemon_down')
    async def sequence():
        sandbox.PROBE = sandbox.SandboxProbe()
        down = await sandbox.status()
        docker.clear('daemon_down')
        still_cached = await sandbox.status()
        monkeypatch.setattr(sandbox, 'CACHE_SECONDS', 0)
        return down, still_cached, await sandbox.status()
    down, cached, recovered = asyncio.run(sequence())
    assert down['reason'] == 'daemon_unavailable' and cached == down and recovered == {'status': 'ok'}


# --------------------------------------------------------------------------- readiness

def ready_app(tmp_path, monkeypatch, required):
    from types import SimpleNamespace
    class Healthy:
        async def health(self): return {'status': 'ok'}
    if required is not None: monkeypatch.setenv('PYTHON_SANDBOX_REQUIRED', required)
    app = create_app(f'sqlite:///{tmp_path}/ready.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False,
                     knowledge_services=SimpleNamespace(management=Healthy(), index=Healthy()))
    app.state.store.worker_seen('worker')
    return app


@pytest.mark.parametrize('required,code', [(None, 200), ('false', 200), ('true', 503)])
def test_an_unavailable_sandbox_fails_readiness_only_when_required(tmp_path, monkeypatch, docker, required, code):
    docker.set('daemon_down')
    with TestClient(ready_app(tmp_path, monkeypatch, required)) as client:
        result = client.get('/api/ready')
    assert result.status_code == code
    assert result.json()['checks']['python_sandbox'] == {'status': 'unavailable', 'required': required == 'true', 'reason': 'daemon_unavailable'}
    assert result.json()['checks']['database']['status'] == 'ok'


def test_a_healthy_sandbox_reads_ok_when_required(tmp_path, monkeypatch, docker):
    with TestClient(ready_app(tmp_path, monkeypatch, 'true')) as client:
        result = client.get('/api/ready')
    assert result.status_code == 200 and result.json()['checks']['python_sandbox'] == {'status': 'ok', 'required': True}


def test_an_invalid_setting_fails_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHON_SANDBOX_REQUIRED', 'sometimes')
    with pytest.raises(ValueError, match='PYTHON_SANDBOX_REQUIRED must be true or false'):
        create_app(f'sqlite:///{tmp_path}/bad.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)


# --------------------------------------------------------------------------- preflight

def flow(python=True):
    worker = {'id': 'work', 'type': 'tool_python', 'inputs': {'input': 'input.message'},
              'config': {'code': 'print(input_text)', 'description': 'Echoes.'}}
    return {'version': 1, 'name': 'Preflight', 'nodes': [{'id': 'input', 'type': 'chat_input'}, worker,
            {'id': 'out', 'type': 'response', 'inputs': {'text': 'work.text'}}],
            'edges': [{'id': 'a', 'source': 'input', 'target': 'work'}, {'id': 'b', 'source': 'work', 'target': 'out'}]}


def test_a_python_workflow_is_refused_before_a_run_exists_while_others_still_run(tmp_path, monkeypatch, docker):
    docker.set('daemon_down')
    app = create_app(f'sqlite:///{tmp_path}/submit.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    with TestClient(app) as client:
        refused = client.post('/api/runs', json={'workflow': flow(True), 'message': 'hi'})
        assert refused.status_code == 422
        assert any('Python sandbox is unavailable (daemon_unavailable)' in e and 'Docker is not running' in e for e in refused.json()['detail'])
        assert client.get('/api/runs').json() == []
        validated = client.post('/api/validate', json=flow(True)).json()
        assert validated['valid'] is False and any('Start Docker' in e for e in validated['errors'])
        from backend.tests.test_compiler import sample  # a known-valid workflow without a Python tool
        ordinary = client.post('/api/runs', json={'workflow': sample(), 'message': 'hi'})
        assert ordinary.status_code == 201, ordinary.text


def test_execution_refuses_a_python_workflow_if_docker_stopped_after_submission(tmp_path, monkeypatch, docker):
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/exec.db', Fernet.generate_key())
    worker = Worker(store)
    executed = []
    async def execute(*args, **kwargs): executed.append(args); return 'ok'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    row = store.create_run(Workflow.model_validate(flow(True)).model_dump(mode='json'), 'hi')
    docker.set('daemon_down')
    asyncio.run(worker.execute(store.claim_next(worker.owner)))
    run = store.run(row['id'])
    assert run['status'] == 'failed' and 'Docker is not running' in run['error']
    assert executed == [] and not any(e.get('node_id') == 'work' for e in run['events'])


def test_python_workflows_are_accepted_again_after_recovery(tmp_path, monkeypatch, docker):
    docker.set('daemon_down')
    monkeypatch.setattr(sandbox, 'CACHE_SECONDS', 0)
    app = create_app(f'sqlite:///{tmp_path}/recover.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    with TestClient(app) as client:
        assert client.post('/api/runs', json={'workflow': flow(True), 'message': 'hi'}).status_code == 422
        docker.clear('daemon_down')
        assert client.post('/api/runs', json={'workflow': flow(True), 'message': 'hi'}).status_code == 201
        assert client.get('/api/ready').json()['checks']['python_sandbox']['status'] == 'ok'


# --------------------------------------------------------------------------- the smoke container

@pytest.mark.parametrize('failure', ['run_fails', 'run_wrong'])
def test_a_container_that_cannot_run_python_is_reported(docker, failure):
    """version and image inspect can succeed while Docker still refuses the container."""
    docker.set(failure)
    assert probe_now() == {'status': 'unavailable', 'reason': 'container_failed'}


def test_a_hung_smoke_container_is_stopped_and_removed(docker, spawned, monkeypatch):
    # Version, image inspect and the run start before the budget may expire.
    monkeypatch.setattr(sandbox, 'PROBE_SECONDS', 2.0)
    docker.set('run_sleep', 30)
    assert probe_now() == {'status': 'unavailable', 'reason': 'probe_timeout'}
    assert killed_and_reaped(spawned) == ['run'], 'the hung run was killed'
    assert 'rm -f' in docker.calls(), 'the container the CLI may have started is removed'


def test_the_probe_runs_the_container_exactly_as_the_executor_does(docker):
    """Same image, security flags and limits: only the name and the program differ."""
    from backend.app.registry import REGISTRY
    from backend.app.tool_service import ToolService, PythonConfig
    probe_now()
    tools = ToolService.__new__(ToolService); tools.max_output = 64000
    assert asyncio.run(tools._python(PythonConfig(code='print(input_text)', description='x'), 'hi')).strip() == 'executed'
    probe_args, executor_args = [line.split() for line in (docker.dir / 'argv').read_text().splitlines()]
    def configuration(args):
        name = args.index('--name')
        return args[:name] + args[name + 2:args.index(sandbox.IMAGE) + 1]
    assert configuration(probe_args) == configuration(executor_args)
    assert '--network=none' in configuration(probe_args) and '--cap-drop=ALL' in configuration(probe_args)


# --------------------------------------------------------------------------- freshness and resume

def test_the_worker_never_executes_on_a_cached_success(tmp_path, monkeypatch, docker):
    """The audit's probe: ok is cached, the daemon stops, and execution must still refuse."""
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/fresh.db', Fernet.generate_key())
    worker = Worker(store)
    executed = []
    async def execute(*args, **kwargs): executed.append(args); return 'ok'
    monkeypatch.setattr(worker.tools, 'execute', execute)
    async def scenario():
        sandbox.PROBE = sandbox.SandboxProbe()
        assert await sandbox.status() == {'status': 'ok'}
        docker.set('daemon_down')
        assert await sandbox.status() == {'status': 'ok'}, 'still cached for readiness and validation'
        row = store.create_run(Workflow.model_validate(flow(True)).model_dump(mode='json'), 'hi')
        await worker.execute(store.claim_next(worker.owner))
        return row['id']
    run = store.run(asyncio.run(scenario()))
    assert run['status'] == 'failed' and 'Docker is not running' in run['error'] and executed == []
    assert docker.calls().count('version --format') == 2, 'the worker probed afresh'


def test_resume_is_refused_and_leaves_the_run_unchanged(tmp_path, monkeypatch, docker):
    from sqlalchemy.orm import Session
    from backend.app.storage import JobRecord
    app = create_app(f'sqlite:///{tmp_path}/resume.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    store = app.state.store
    row = store.create_run(Workflow.model_validate(flow(True)).model_dump(mode='json'), 'hi')
    store.claim_next('worker'); store.finish_run(row['id'], 'worker', 'failed', error='earlier failure')
    before = store.run(row['id'])
    with Session(store.engine) as s: job_before = s.get(JobRecord, row['id']).status
    docker.set('daemon_down')
    with TestClient(app) as client:
        refused = client.post(f"/api/runs/{row['id']}/resume")
    assert refused.status_code == 422 and any('Docker is not running' in e for e in refused.json()['detail'])
    after = store.run(row['id'])
    assert (after['status'], after['error'], len(after['events'])) == (before['status'], before['error'], len(before['events']))
    with Session(store.engine) as s: assert s.get(JobRecord, row['id']).status == job_before != 'queued'
