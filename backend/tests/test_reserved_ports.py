"""Port names starting with '_' are reserved for run metadata carried in a node's graph
value, such as the confirmed truncation a recovering node reports under '_truncation'.

The guarantee holds by construction: such a port cannot be declared, cannot be
registered, and cannot be bound, even if a definition is altered after registration.
"""
import pytest
from backend.app.compiler import validate_workflow
from backend.app.execution_policy import TRUNCATION_KEY
from backend.app.models import Workflow
from backend.app.registry import REGISTRY, RESERVED_PORT_PREFIX, NodeDefinition, StrictModel, check_port_names, register


async def handler(inputs, config, ctx): return {}


def definition(inputs=None, outputs=None, type='probe_reserved'):
    return NodeDefinition(type, 'Probe', 'Test', 'A node declaring a reserved port.',
                          inputs or {'input': 'string'}, outputs or {'text': 'string'}, StrictModel, handler)


def test_the_metadata_key_is_in_the_reserved_namespace():
    assert TRUNCATION_KEY.startswith(RESERVED_PORT_PREFIX)


@pytest.mark.parametrize('ports', [{'outputs': {TRUNCATION_KEY: 'string'}}, {'outputs': {'text': 'string', '_x': 'string'}},
                                   {'inputs': {TRUNCATION_KEY: 'string'}}])
def test_a_reserved_port_cannot_be_declared(ports):
    with pytest.raises(ValueError, match='reserved for run metadata'):
        definition(**ports)


def test_a_definition_altered_after_construction_cannot_be_registered():
    altered = definition()
    altered.outputs[TRUNCATION_KEY] = 'string'
    with pytest.raises(ValueError, match='reserved for run metadata'):
        register(altered)
    assert altered.type not in REGISTRY


def test_a_reserved_port_cannot_be_bound_even_on_an_altered_registered_definition(monkeypatch):
    """The auditor's probe: a node exposing _truncation wired to a Response node. It used
    to validate with no errors."""
    tool = REGISTRY['tool_python']
    monkeypatch.setitem(tool.outputs, TRUNCATION_KEY, 'string')
    workflow = Workflow.model_validate({'version': 1, 'name': 'Reserved', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'work', 'type': 'tool_python', 'inputs': {'input': 'input.message'},
         'config': {'code': 'print(input_text)', 'description': 'Echoes.'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': f'work.{TRUNCATION_KEY}'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'work'}, {'id': 'b', 'source': 'work', 'target': 'out'}]})
    errors = validate_workflow(workflow)
    assert any(f'invalid binding text = work.{TRUNCATION_KEY}' in e for e in errors), errors


def test_every_registered_definition_uses_only_public_port_names():
    for node_type, registered in REGISTRY.items():
        check_port_names(registered)
        assert not any(name.startswith(RESERVED_PORT_PREFIX) for name in [*registered.inputs, *registered.outputs]), node_type
