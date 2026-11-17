"""Shared helpers for defect reproductions. Each test here is a strict xfail: it fails on the
current code for the reason its docstring states, and CI stays green until the fix lands."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture
def client(tmp_path, monkeypatch):
    from backend.app.main import create_app
    monkeypatch.setenv('VECTOR_DATA_DIR', str(tmp_path / 'vectors'))
    app = create_app(f'sqlite:///{tmp_path}/defects.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    with TestClient(app) as c:
        yield c


def script_model(monkeypatch, replies):
    """Replace the model with scripted replies. An Exception in the list is raised."""
    from backend.app import registry
    pending = iter(replies)

    async def model(inputs, config, ctx):
        reply = next(pending)
        if isinstance(reply, Exception):
            raise reply
        return {'text': reply, 'provider': 'demo'}
    monkeypatch.setattr(registry, 'llm_node', model)


def expire_lease(store, run_id):
    from backend.app.storage import JobRecord
    with Session(store.engine) as s:
        s.get(JobRecord, run_id).lease_until = 0
        s.commit()


def agent_write_flow(connection_id):
    """Input -> Agent (with one attached HTTP POST tool, no approval) -> Response."""
    from backend.app.models import Workflow
    return Workflow.model_validate({'version': 1, 'name': 'Agent write', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'provider': 'demo'}},
        {'id': 'post', 'type': 'tool_http', 'config': {'method': 'POST', 'enable_writes': True, 'approval': False,
                                                         'connection_id': connection_id, 'body': '{input}'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'}, {'id': 'b', 'source': 'agent', 'target': 'out'},
                  {'id': 't', 'source': 'post', 'target': 'agent', 'kind': 'tool', 'targetHandle': 'tools'}]})


def call(target, text):
    import json
    return json.dumps({'action': 'call', 'target': target, 'input': text})


def final(text):
    import json
    return json.dumps({'action': 'final', 'text': text})
