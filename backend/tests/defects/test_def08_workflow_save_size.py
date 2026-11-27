"""DEF-08: saving a workflow checks the names of a node's settings, not their values.

main.py:81-88 (safe_draft) compares only the key names with the node's config model, and
main.py:122 stores the document. The config model's limits (for example the 20,000-character
system prompt, registry.py:22) are applied only when a run is submitted.
"""
import pytest


@pytest.mark.xfail(strict=True, reason='DEF-08')
def test_def08_a_saved_workflow_cannot_hold_a_setting_over_its_declared_limit(client):
    workflow = {'version': 1, 'name': 'Large', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'provider': 'demo', 'system': 'x' * 2_000_000}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'agent.text'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'}, {'id': 'b', 'source': 'agent', 'target': 'out'}]}
    response = client.post('/api/workflows', json=workflow)
    assert response.status_code == 422, f'DEF-08: a 2 MB setting with a 20,000-character limit was stored ({response.status_code})'
