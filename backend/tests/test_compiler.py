import pytest
from backend.app.models import Workflow
from backend.app.compiler import compile_workflow, validate_workflow


def sample():
    return {'version': 1, 'name': 'Greeting', 'nodes': [
        {'id': 'input', 'type': 'chat_input', 'position': {'x': 20, 'y': 50}},
        {'id': 'prompt', 'type': 'prompt', 'config': {'template': 'Hello {message}'}, 'inputs': {'message': 'input.message'}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'prompt.text'}},
    ], 'edges': [{'id': 'a', 'source': 'input', 'target': 'prompt'}, {'id': 'b', 'source': 'prompt', 'target': 'out'}]}

@pytest.mark.asyncio
async def test_executes_declared_bindings_and_preserves_canvas():
    raw = sample()
    workflow = Workflow.model_validate(raw)
    events = []
    async def emit(event): events.append(event)
    compiled = compile_workflow(workflow, lambda _: '', emit, 'Ada')
    result = await compiled.graph.ainvoke({'values': {}})
    assert result['values']['out']['text'] == 'Hello Ada'
    assert compiled.source == workflow.model_dump(mode='json')
    assert compiled.source['nodes'][0]['position'] == {'x': 20.0, 'y': 50.0}
    assert [e['status'] for e in events] == ['running', 'success'] * 3

@pytest.mark.parametrize('mutation,expected', [
    (lambda w: w['edges'].append({'id':'cycle', 'source':'out','target':'input'}), 'cycle'),
    (lambda w: w['nodes'][1]['inputs'].update(message='missing.text'), 'binding'),
    (lambda w: w['nodes'][1].update(type='shell'), 'Unknown node'),
    (lambda w: w['nodes'][1]['config'].update(api_key='secret'), 'configuration'),
    (lambda w: w['edges'].pop(), 'unreachable'),
])
def test_rejects_invalid_workflows(mutation, expected):
    raw = sample(); mutation(raw)
    errors = validate_workflow(Workflow.model_validate(raw))
    assert any(expected.lower() in e.lower() for e in errors), errors

@pytest.mark.asyncio
@pytest.mark.parametrize('message,chosen', [('yes please','yes'), ('no thanks','no')])
async def test_condition_executes_only_selected_branch(message, chosen):
    raw = {'version':1,'name':'Route','nodes':[
        {'id':'input','type':'chat_input'},
        {'id':'check','type':'condition','inputs':{'value':'input.message'},'config':{'contains':'yes'}},
        {'id':'yes','type':'response','inputs':{'text':'input.message'}},
        {'id':'no','type':'response','inputs':{'text':'input.message'}},
    ],'edges':[
        {'id':'a','source':'input','target':'check'},
        {'id':'b','source':'check','target':'yes','sourceHandle':'true'},
        {'id':'c','source':'check','target':'no','sourceHandle':'false'},
    ]}
    result = await compile_workflow(Workflow.model_validate(raw), lambda _: '', message=message).graph.ainvoke({'values':{}})
    assert chosen in result['values']
    assert ({'yes','no'} - {chosen}).pop() not in result['values']


def test_reserved_runtime_state_name_is_rejected_before_compilation():
    raw=sample()
    raw['nodes'][0]['id']='values'
    raw['nodes'][1]['inputs']['message']='values.message'
    raw['edges'][0]['source']='values'
    assert any('reserved' in e for e in validate_workflow(Workflow.model_validate(raw)))
