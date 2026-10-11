import pytest
from backend.app.compiler import validate_workflow
from backend.app.models import Workflow
from backend.app.registry import REGISTRY, NodeDefinition
from backend.tests.test_workflow_integrity import client_for
from backend.tests.test_compiler import sample


@pytest.fixture
def future_nodes(monkeypatch):
    from backend.app.request_contracts import GreetingCacheConfig, RequestRouterConfig, ExtractionCheckConfig
    async def forbidden(*args):raise AssertionError('These test-only declarations must never execute.')
    for name, model, inputs, outputs, optional in [
        ('greeting_cache', GreetingCacheConfig, {'message': 'string'}, ['branch', 'reply', 'receipt'], {}),
        ('request_router', RequestRouterConfig, {'message': 'string'}, ['branch', 'reply', 'decision'], {}),
        ('verify_extraction', ExtractionCheckConfig, {'original': 'string', 'value': 'string'}, ['text', 'branch', 'reply', 'report'], {'reference': 'string'}),
    ]:
        monkeypatch.setitem(REGISTRY, name, NodeDefinition(name, name, 'Control', '', inputs,
            {key: 'string' for key in outputs}, model, forbidden, branch_routes=('continue', 'reply'), optional_inputs=optional))


def guarded():
    nodes = [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'cache', 'type': 'greeting_cache', 'inputs': {'message': 'input.message'}},
        {'id': 'router', 'type': 'request_router', 'inputs': {'message': 'input.message'}, 'config': {'purpose': 'Handle orders'}},
        {'id': 'extract', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'role': 'extraction', 'provider': 'demo'}},
        {'id': 'verify', 'type': 'verify_extraction', 'inputs': {'original': 'input.message', 'value': 'extract.text'},
         'config': {'expected_schema': {'type': 'object'}, 'required_fields': ['/item'], 'field_rules': [{'field': '/item', 'kind': 'quoted'}]}},
        {'id': 'send', 'type': 'tool_http', 'inputs': {'input': 'verify.text'}, 'config': {'method': 'POST', 'connection_id': 'desk', 'path': '/orders', 'body': '{input}', 'approval': False}},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'send.text'}},
    ]
    edges = [{'id': 'input-cache', 'source': 'input', 'target': 'cache'},
             {'id': 'cache-router', 'source': 'cache', 'target': 'router', 'sourceHandle': 'continue'},
             {'id': 'router-extract', 'source': 'router', 'target': 'extract', 'sourceHandle': 'continue'},
             {'id': 'extract-verify', 'source': 'extract', 'target': 'verify'},
             {'id': 'verify-send', 'source': 'verify', 'target': 'send', 'sourceHandle': 'continue'},
             {'id': 'send-out', 'source': 'send', 'target': 'out'}]
    for source in ['cache', 'router', 'verify']:
        nodes.append({'id': source+'Reply', 'type': 'response', 'inputs': {'text': source+'.reply'}})
        edges.append({'id': source+'-reply', 'source': source, 'target': source+'Reply', 'sourceHandle': 'reply'})
    return {'name': 'Guarded', 'contract': 2, 'nodes': nodes, 'edges': edges}


def test_contract_two_entry_and_verified_path_are_valid(future_nodes):
    assert validate_workflow(Workflow.model_validate(guarded())) == []


def test_contract_two_requires_entry_chain():
    raw = sample(); raw['contract'] = 2
    assert any('entry' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


@pytest.mark.parametrize('mutate,expected', [
    (lambda w: w['nodes'][2]['inputs'].update(message='cache.reply'), 'original'),
    (lambda w: w['nodes'][3]['inputs'].update(input='router.decision'), 'original'),
    (lambda w: w['nodes'][4]['inputs'].update(original='extract.text'), 'original'),
    (lambda w: w['nodes'][5]['inputs'].update(input='extract.text'), 'verified'),
    (lambda w: w['edges'][-1].update(target='send'), 'reply'),
    (lambda w: w['nodes'][4]['config'].update(field_rules=[{'field': '/item', 'kind': 'reference_member', 'reference_list': '/products', 'reference_key': '/name'}]), 'reference'),
    (lambda w: w['nodes'][4]['inputs'].update(reference='extract.text'), 'independent'),
    (lambda w: w['nodes'][5]['config'].update(accept_guessed=True), 'configuration'),
])
def test_contract_two_rejects_structural_bypasses(future_nodes, mutate, expected):
    raw = guarded(); mutate(raw)
    assert any(expected in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


def test_router_acceptance_alone_never_authorises_write(future_nodes):
    raw = guarded()
    raw['nodes'] = [n for n in raw['nodes'] if n['id'] not in ('extract', 'verify', 'verifyReply')]
    raw['edges'] = [e for e in raw['edges'] if e['source'] not in ('extract', 'verify')]
    next(e for e in raw['edges'] if e['source'] == 'router' and e['sourceHandle'] == 'continue')['target'] = 'send'
    next(n for n in raw['nodes'] if n['id'] == 'send')['inputs']['input'] = 'input.message'
    assert any('verification' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


def test_error_route_cannot_bypass_verification(future_nodes):
    raw = guarded()
    raw['nodes'][3]['on_error'] = 'route'
    raw['edges'].append({'id': 'skip-check', 'source': 'extract', 'target': 'send', 'sourceHandle': 'error'})
    raw['nodes'][5]['inputs']['input'] = 'input.message'
    assert any('verification' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


@pytest.mark.parametrize('specialist', [False, True])
def test_attached_writes_before_verification_are_rejected(future_nodes, specialist):
    raw = guarded()
    raw['nodes'].append({'id': 'writeTool', 'type': 'tool_http', 'config': {'method': 'POST', 'connection_id': 'desk', 'path': '/', 'body': '{input}'}})
    owner = 'extract'
    if specialist:
        raw['nodes'].append({'id': 'specialist', 'type': 'agent'})
        raw['edges'].append({'id': 'delegate', 'source': 'extract', 'target': 'specialist', 'kind': 'agent'})
        owner = 'specialist'
    raw['edges'].append({'id': 'attached-write', 'source': 'writeTool', 'target': owner, 'kind': 'tool'})
    assert any('verification' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


def test_model_derived_condition_cannot_skip_verification(future_nodes):
    raw = guarded()
    raw['nodes'][4] = {'id': 'verify', 'type': 'condition', 'inputs': {'value': 'extract.text'}, 'config': {'contains': 'yes'}}
    for e in raw['edges']:
        if e['source'] == 'verify':e['sourceHandle'] = 'true' if e['target'] == 'send' else 'false'
    raw['nodes'][5]['inputs']['input'] = 'input.message'
    next(n for n in raw['nodes'] if n['id'] == 'verifyReply')['inputs']['text'] = 'input.message'
    assert any('verification' in e.lower() for e in validate_workflow(Workflow.model_validate(raw)))


def test_invalid_guarded_draft_can_save_but_submission_creates_no_run(tmp_path):
    raw = sample(); raw['contract'] = 2
    with client_for(tmp_path) as client:
        assert client.post('/api/workflows', json=raw).status_code == 201
        assert not client.post('/api/validate', json=raw).json()['valid']
        assert client.post('/api/runs', json={'workflow': raw, 'message': 'Hello'}).status_code == 422
        assert client.get('/api/runs').json() == []


@pytest.mark.parametrize('value', [None, 1, 'rules', {}, [False]])
def test_malformed_rule_configuration_is_a_validation_error_not_a_crash(future_nodes, value):
    raw = guarded(); raw['nodes'][4]['config']['field_rules'] = value
    assert any('configuration' in e for e in validate_workflow(Workflow.model_validate(raw)))


def test_narrative_qa_has_no_business_check_requirement(future_nodes):
    raw = guarded()
    raw['nodes'] = [n for n in raw['nodes'] if n['id'] not in ('verify', 'verifyReply', 'send')]
    raw['nodes'][3]['config'] = {'role': 'reasoner', 'provider': 'demo'}
    next(n for n in raw['nodes'] if n['id'] == 'out')['inputs']['text'] = 'extract.text'
    raw['edges'] = [e for e in raw['edges'] if e['source'] not in ('verify', 'send')]
    next(e for e in raw['edges'] if e['source'] == 'extract')['target'] = 'out'
    assert validate_workflow(Workflow.model_validate(raw)) == []


@pytest.mark.parametrize('body_type', ['agent', 'query'])
@pytest.mark.parametrize('consumer', ['condition', 'calculate'])
def test_attached_model_results_cannot_authorise_decisions(future_nodes, body_type, consumer):
    raw = guarded()
    raw['nodes'] = [n for n in raw['nodes'] if n['id'] in ('input', 'cache', 'router', 'cacheReply', 'routerReply')]
    body_config = {'role': 'classification', 'provider': 'demo'} if body_type == 'agent' else {'knowledge_base_id': 'policies', 'provider': 'demo'}
    raw['nodes'] += [
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'}, 'config': {'operation': 'search', 'connection_id': 'desk', 'jql': 'project=DEMO'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'body'}},
        {'id': 'body', 'type': body_type, 'config': body_config},
    ]
    raw['edges'] = [e for e in raw['edges'] if e['source'] != 'router' or e['sourceHandle'] != 'continue']
    raw['edges'] += [{'id': 'router-tickets', 'source': 'router', 'target': 'tickets', 'sourceHandle': 'continue'},
                     {'id': 'tickets-each', 'source': 'tickets', 'target': 'each'},
                     {'id': 'each-body', 'source': 'each', 'target': 'body', 'kind': 'loop'},
                     {'id': 'each-check', 'source': 'each', 'target': 'check'}]
    if consumer == 'condition':
        raw['nodes'] += [{'id': 'check', 'type': 'condition', 'inputs': {'value': 'each.results'}, 'config': {'contains': 'approved'}},
                         {'id': 'yes', 'type': 'response', 'inputs': {'text': 'input.message'}},
                         {'id': 'no', 'type': 'response', 'inputs': {'text': 'input.message'}}]
        raw['edges'] += [{'id': 'yes', 'source': 'check', 'target': 'yes', 'sourceHandle': 'true'},
                         {'id': 'no', 'source': 'check', 'target': 'no', 'sourceHandle': 'false'}]
    else:
        raw['nodes'] += [{'id': 'check', 'type': 'calculate', 'inputs': {'value': 'each.results'}, 'config': {'mode': 'check', 'expression': 'approved == 1'}},
                         {'id': 'out', 'type': 'response', 'inputs': {'text': 'check.text'}}]
        raw['edges'].append({'id': 'out', 'source': 'check', 'target': 'out'})
    errors = validate_workflow(Workflow.model_validate(raw))
    assert any(e.startswith('check:') and 'verification' in e for e in errors), errors
