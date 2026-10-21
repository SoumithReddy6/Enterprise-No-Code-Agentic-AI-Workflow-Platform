"""One node-aware route contract: every boundary asks the node, never only its type."""
import json
from pathlib import Path

import pytest
from pydantic import Field

from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.models import Node, StrictModel, Workflow
from backend.app.registry import REGISTRY, NodeDefinition, EmptyConfig

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'frontend/tests/fixtures/node-routes.json'


class RoutedConfig(StrictModel):
    routes: list[str] = Field(default_factory=lambda: ['left', 'right'], min_length=1, max_length=15)


async def routed(inputs, config, ctx):
    return {'branch': ctx.message}


@pytest.fixture
def configured(monkeypatch):
    """A test-only node whose routes come from its settings, as the Switch's will."""
    definition = NodeDefinition('test_routed', 'Routed', 'Control', '', {'value': 'string'}, {'branch': 'string'},
                                RoutedConfig, routed, routes_from_config=lambda config: (*config.routes, 'default'))
    monkeypatch.setitem(REGISTRY, definition.type, definition)
    return definition


def flow(routes=('left', 'right'), handles=None):
    handles = list(handles if handles is not None else (*routes, 'default'))
    nodes = [{'id': 'input', 'type': 'chat_input'},
             {'id': 'route', 'type': 'test_routed', 'inputs': {'value': 'input.message'}, 'config': {'routes': list(routes)}}]
    edges = [{'id': 'a', 'source': 'input', 'target': 'route'}]
    for handle in handles:
        nodes.append({'id': 'out_' + handle, 'type': 'response', 'inputs': {'text': 'input.message'}})
        edges.append({'id': 'e_' + handle, 'source': 'route', 'target': 'out_' + handle, 'sourceHandle': handle})
    return {'name': 'Routed', 'nodes': nodes, 'edges': edges}


def test_the_definition_only_route_function_is_gone():
    users = [p.name for p in (ROOT / 'backend/app').rglob('*.py') if 'control_routes' in p.read_text()]
    assert users == []


def test_static_routes_are_unchanged():
    from backend.app.request_contracts import node_routes
    assert node_routes(Node(id='c', type='condition', config={'contains': 'x'})) == ('true', 'false')
    assert node_routes(Node(id='r', type='request_reader')) == ('continue', 'reply')
    assert node_routes(Node(id='a', type='agent')) == ()
    assert node_routes(Node(id='u', type='unknown_type')) == ()


def test_configured_routes_come_from_the_node(configured):
    from backend.app.request_contracts import node_routes
    assert node_routes(Node(id='n', type='test_routed', config={'routes': ['a', 'b', 'c']})) == ('a', 'b', 'c', 'default')
    # Invalid settings give no routes; validation reports the settings error instead.
    assert node_routes(Node(id='n', type='test_routed', config={'routes': []})) == ()


@pytest.mark.parametrize('routes', [('left', 'left'), ('left', 'error'), ('9bad',), tuple(f'r{i}' for i in range(16))])
def test_invalid_configured_routes_are_refused(configured, routes):
    errors = validate_workflow(Workflow.model_validate(flow(routes)))
    assert any('route' in e for e in errors), errors


@pytest.mark.parametrize('kwargs,outputs', [
    ({'branch_routes': ('a', 'b'), 'routes_from_config': lambda c: ('a', 'b')}, {'branch': 'string'}),
    ({'routes_from_config': lambda c: ('a', 'b')}, {'text': 'string'}),
])
def test_a_type_declares_static_or_configured_routes_never_both(kwargs, outputs):
    with pytest.raises(ValueError):
        NodeDefinition('test_bad', 'Bad', 'Control', '', {'value': 'string'}, outputs, EmptyConfig, routed, **kwargs)


@pytest.mark.parametrize('handles,ok', [
    (('left', 'right', 'default'), True),
    (('left', 'right'), False),                      # default edge missing
    (('left', 'right', 'default', 'other'), False),  # undeclared handle
    (('left', 'left', 'right', 'default'), False),   # duplicate handle
])
def test_validation_needs_exactly_one_edge_for_each_configured_route(configured, handles, ok):
    errors = validate_workflow(Workflow.model_validate(flow(handles=handles)))
    assert (errors == []) == ok, errors


def test_invalid_settings_report_the_settings_not_a_route_error(configured):
    raw = flow();raw['nodes'][1]['config'] = {'routes': []}
    errors = validate_workflow(Workflow.model_validate(raw))
    assert errors and not any('next node' in e for e in errors), errors


@pytest.mark.asyncio
@pytest.mark.parametrize('message', ['left', 'right', 'default'])
async def test_compilation_follows_the_configured_route(configured, message):
    result = await compile_workflow(Workflow.model_validate(flow()), message=message).graph.ainvoke({'values': {}})
    assert 'out_' + message in result['values']
    assert not {'out_left', 'out_right', 'out_default'} - {'out_' + message} & set(result['values'])


@pytest.mark.asyncio
async def test_a_fresh_undeclared_branch_is_refused(configured):
    with pytest.raises(ValueError, match='outside its declared control routes'):
        await compile_workflow(Workflow.model_validate(flow()), message='other').graph.ainvoke({'values': {}})


@pytest.mark.asyncio
@pytest.mark.parametrize('branch', ['other', 'true', None])
async def test_a_saved_undeclared_branch_is_refused(configured, branch):
    completed = {'input': {'message': 'left'}, 'route': {'branch': branch}}
    with pytest.raises(ValueError, match='not a declared control route'):
        await compile_workflow(Workflow.model_validate(flow()), message='left', completed=completed).graph.ainvoke({'values': {}})


@pytest.mark.parametrize('kind', ['tool', 'loop', 'agent'])
def test_a_configured_control_cannot_be_attached(configured, kind):
    raw = flow();raw['edges'].append({'id': 'attachment', 'source': 'route', 'target': 'out_left', 'kind': kind})
    assert any('control' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


def test_the_frontend_route_fixture_matches_the_backend():
    """The editor draws handles from nodeRoutes; frontend/tests/routes.test.ts reads the same file."""
    from backend.app.request_contracts import node_routes
    fixture = json.loads(FIXTURE.read_text())
    assert fixture and all(list(node_routes(Node.model_validate(c['node']))) == c['routes'] for c in fixture)
