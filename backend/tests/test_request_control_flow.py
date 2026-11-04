"""Exercise the declared seam using test-only handlers, never production placeholders."""
import pytest
from backend.app.models import Workflow
from backend.app.registry import REGISTRY, NodeDefinition, EmptyConfig
from backend.app.compiler import compile_workflow, validate_workflow, type_warnings


async def control(inputs, config, ctx):
    return {'branch': 'continue' if ctx.message == 'continue' else 'reply', 'text': inputs['message']}


@pytest.fixture
def declared_control(monkeypatch):
    definition = NodeDefinition('test_control', 'Control', 'Control', '', {'message': 'string'},
                                {'branch': 'string', 'text': 'string'}, EmptyConfig, control,
                                branch_routes=('continue', 'reply'), optional_inputs={'reference': 'object'})
    monkeypatch.setitem(REGISTRY, definition.type, definition)
    return definition


def raw_flow():
    return {'name': 'Declared routes', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'guard', 'type': 'test_control', 'inputs': {'message': 'input.message'}},
        {'id': 'accepted', 'type': 'response', 'inputs': {'text': 'guard.text'}},
        {'id': 'reply', 'type': 'response', 'inputs': {'text': 'guard.text'}},
    ], 'edges': [{'id': 'a', 'source': 'input', 'target': 'guard'},
                {'id': 'b', 'source': 'guard', 'target': 'accepted', 'sourceHandle': 'continue'},
                {'id': 'c', 'source': 'guard', 'target': 'reply', 'sourceHandle': 'reply'}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('message,selected', [('continue', 'accepted'), ('stop', 'reply')])
async def test_routes_are_driven_by_declaration_not_node_type(declared_control, message, selected):
    workflow = Workflow.model_validate(raw_flow())
    assert validate_workflow(workflow) == []
    events = []
    async def emit(event):events.append(event)
    result = await compile_workflow(workflow, message=message, emit=emit).graph.ainvoke({'values': {}})
    assert result['values'][selected]['text'] == message
    assert ({'accepted', 'reply'} - {selected}).isdisjoint(result['values'])
    assert [e['status'] for e in events] == ['running', 'success']*3


@pytest.mark.parametrize('mutation', [
    lambda w: w['edges'].pop(),
    lambda w: w['edges'].append({'id': 'extra', 'source': 'guard', 'target': 'reply', 'sourceHandle': 'continue'}),
    lambda w: w['edges'][1].update(sourceHandle='other'),
    lambda w: w['nodes'][1].update(on_error='route'),
    lambda w: w['nodes'][1].update(on_error='continue'),
])
def test_declared_controls_require_exact_routes_and_no_empty_recovery(declared_control, mutation):
    raw = raw_flow(); mutation(raw)
    assert validate_workflow(Workflow.model_validate(raw))


def test_optional_binding_is_checked_and_warned_but_not_required(declared_control):
    raw = raw_flow()
    assert validate_workflow(Workflow.model_validate(raw)) == []
    raw['nodes'][1]['inputs']['reference'] = 'input.message'
    assert validate_workflow(Workflow.model_validate(raw)) == []
    assert len(type_warnings(Workflow.model_validate(raw))) == 1
    raw['nodes'][1]['inputs']['reference'] = 'reply.text'
    assert any('invalid binding reference' in e for e in validate_workflow(Workflow.model_validate(raw)))
    raw['nodes'][1]['inputs']['reference'] = 'input._provenance'
    assert any('invalid binding reference' in e for e in validate_workflow(Workflow.model_validate(raw)))
    raw['nodes'][1]['inputs']['unlisted'] = 'input.message'
    assert any('input' in e for e in validate_workflow(Workflow.model_validate(raw)))


@pytest.mark.parametrize('routes,outputs,optional', [
    (('continue', 'continue'), {'branch': 'string'}, {}),
    (('continue', 'reply'), {}, {}),
    (('continue', 'error'), {'branch': 'string'}, {}),
    ((), {'text': 'string'}, {'_verification': 'object'}),
    ((), {'text': 'string'}, {'message': 'string'}),
])
def test_inconsistent_definitions_are_refused(routes, outputs, optional):
    with pytest.raises(ValueError):
        NodeDefinition('test_bad', 'Bad', 'Control', '', {'message': 'string'}, outputs, EmptyConfig, control,
                       branch_routes=routes, optional_inputs=optional)


def test_registration_rechecks_mutable_optional_ports(declared_control):
    from backend.app.registry import register
    declared_control.type = 'test_mutated'
    declared_control.optional_inputs['_metadata'] = 'object'
    with pytest.raises(ValueError):register(declared_control)
    assert 'test_mutated' not in REGISTRY


@pytest.mark.parametrize('kind', ['tool', 'loop', 'agent'])
def test_control_cannot_be_attached(declared_control, kind):
    raw = raw_flow()
    raw['edges'].append({'id': 'attachment', 'source': 'guard', 'target': 'accepted', 'kind': kind})
    assert any('control' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


def test_condition_public_contract_and_optional_defaults():
    from backend.app.request_contracts import route_contract
    public = REGISTRY['condition'].public()
    assert tuple(public['branch_routes']) == route_contract(REGISTRY['condition']) == ('true', 'false')
    assert public['optional_inputs'] == {}


@pytest.mark.asyncio
async def test_unknown_runtime_branch_fails_before_success_or_downstream(declared_control):
    async def wrong(*args):return {'branch': 'not_declared', 'text': 'wrong'}
    declared_control.handler = wrong
    events = []
    async def emit(event):events.append(event)
    with pytest.raises(ValueError, match='branch'):
        await compile_workflow(Workflow.model_validate(raw_flow()), emit=emit).graph.ainvoke({'values': {}})
    assert not any(e['node_id'] == 'guard' and e['status'] == 'success' for e in events)
    assert not any(e['node_id'] in ('accepted', 'reply') for e in events)


@pytest.mark.asyncio
async def test_invalid_cached_branch_is_refused_before_downstream(declared_control):
    events = []
    async def emit(event):events.append(event)
    with pytest.raises(ValueError, match='branch'):
        await compile_workflow(Workflow.model_validate(raw_flow()), emit=emit,
            completed={'guard': {'branch': 'unknown', 'text': 'stale'}}).graph.ainvoke({'values': {}})
    assert not any(e['node_id'] in ('accepted', 'reply') for e in events)


@pytest.mark.parametrize('handle', ['continue', 'reply'])
def test_error_recovery_outputs_remain_unavailable_on_each_control_route(declared_control, handle):
    raw = raw_flow()
    raw['nodes'].insert(1, {'id': 'work', 'type': 'prompt', 'inputs': {'message': 'input.message'}, 'on_error': 'route'})
    raw['edges'][0]['target'] = 'work'
    raw['edges'] += [{'id': 'ok', 'source': 'work', 'target': 'guard', 'sourceHandle': 'output'},
                     {'id': 'error', 'source': 'work', 'target': 'guard', 'sourceHandle': 'error'}]
    consumer = 'accepted' if handle == 'continue' else 'reply'
    next(n for n in raw['nodes'] if n['id'] == consumer)['inputs']['text'] = 'work.text'
    assert any('possible error path' in e for e in validate_workflow(Workflow.model_validate(raw)))
