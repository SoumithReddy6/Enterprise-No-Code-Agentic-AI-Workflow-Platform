"""TA04: work the run token ceiling refused is not the workflow's chosen path.

A token refusal is typed, like an action-budget refusal, so a node that recovers from it
under on_error continue or route leaves the run truncated with a budget cause. An agent
that runs out of tokens with evidence makes no further call (decision D1 = B): a free-text
agent returns a source-checked budget abstention, and a structured agent fails typed.

Every model call goes through the real llm_node; only the external chat is substituted.
A fake llm_node never checks the ceiling, and that hid the agent defect.
"""
import asyncio
import json
import pytest
from backend.app import token_budget
from backend.app.compiler import compile_workflow
from backend.app.execution_policy import TRUNCATION_KEY
from backend.app.models import RetryPolicy, Workflow
from backend.app.worker import run_truncation
from backend.tests.test_agent_grounding import source
from backend.tests.test_agent_team_budget import grounded, team_flow
from backend.tests.test_agent_tools import calc_flow
from backend.tests.test_iteration import issues, jira_connection, loop_flow
from backend.tests.test_kb_services_integration import populated, services  # noqa: F401 (fixture)
from backend.tests.test_kb_workflow import graph
from backend.tests.test_token_budget import agent_with_retrieval

MODEL = {'provider': 'ollama', 'model': 'fixture'}
LIMIT = '10'
REASON = ('Run token budget exhausted (10 of 10 provider-reported tokens; raise RELAY_RUN_TOKEN_LIMIT). '
          'Token counts are not a monetary cost.')
BUDGET_TEXT = ('The run token budget ran out before an answer could be written. The sources collected '
               'so far are listed but were not used to answer.\n\nReason: ' + REASON)
REMOVED = 'Knowledge source was removed: doc'
SCHEMA = {'type': 'object', 'properties': {'amount': {'type': 'number'}}, 'required': ['amount']}
STRUCTURED = {'output_schema': {'output_schema': SCHEMA}, 'extraction': {'role': 'extraction'},
              'classification': {'role': 'classification'}}


def typed(exc):
    """The refusal type, checked by name so this module still imports on the baseline."""
    return type(exc).__name__ == 'TokenBudgetExhausted' and isinstance(exc, ValueError)


def chat(monkeypatch, replies, calls):
    """Substitute only the external chat. Each reply is (text, prompt tokens, completion
    tokens); authorization, the ceiling, accounting and persistence stay real."""
    from backend.app import providers
    queue = list(replies)
    async def ollama_chat(model, system, prompt, usage=None, **params):
        if not queue: raise AssertionError('unexpected provider call')
        text, prompt_tokens, completion_tokens = queue.pop(0)
        calls.append(prompt); providers.record_usage(usage, prompt_tokens, completion_tokens)
        if isinstance(text, BaseException): raise text
        return text
    monkeypatch.setattr(providers, 'ollama_chat', ollama_chat)


def kb_platform(verified, removed=False, retrievals=None, items=None):
    async def platform(action, *args):
        if action == 'kb_retrieve':
            if retrievals is not None: retrievals.append(args[1])
            return {'query': args[1], 'knowledge_base_id': 'kb1', 'version': 1, 'sources': [source(1)]}
        if action == 'record_vector_sources': return None
        if action == 'verify_vector_sources':
            verified.append(args[0])
            if removed: raise ValueError(REMOVED)
            return args[0]
        if action == 'tool':
            if args[0] == 'tool_jira': return {'text': '{}', 'items': items or [], 'truncated': False, 'warnings': []}
            return '4'
        raise AssertionError(action)
    return platform


async def execute(workflow, platform=None, message='go'):
    events = []
    async def emit(event): events.append(dict(event))
    result = await compile_workflow(workflow, message=message, platform_resolver=platform, emit=emit).graph.ainvoke({'values': {}})
    return result, events


def failed_event(events, node_id, nested=False):
    found = [e for e in events if e.get('node_id') == node_id and e.get('status') == 'failed'
             and bool(e.get('transient')) is nested]
    assert found, f'{node_id} has no failed event'
    return found[0]


def llm(id, **extra):
    return {'id': id, 'type': 'llm', 'inputs': {'prompt': 'input.message'}, 'config': dict(MODEL), **extra}


def respond(id, source='input.message'):
    return {'id': id, 'type': 'response', 'inputs': {'text': source}}


def token_flow(policy, **second):
    """first spends the whole limit (8 + 2 of 10); second is refused and applies `policy`."""
    nodes = [{'id': 'input', 'type': 'chat_input'}, llm('first'), llm('second', on_error=policy, **second)]
    edges = [{'id': 'a', 'source': 'input', 'target': 'first'}, {'id': 'b', 'source': 'first', 'target': 'second'}]
    if policy == 'route':
        nodes += [respond('ok', 'second.text'), respond('fallback')]
        edges += [{'id': 'c', 'source': 'second', 'target': 'ok'},
                  {'id': 'd', 'source': 'second', 'target': 'fallback', 'sourceHandle': 'error'}]
    else:
        nodes.append(respond('out'))
        edges.append({'id': 'c', 'source': 'second', 'target': 'out'})
    return Workflow.model_validate({'version': 1, 'name': 'Tokens', 'nodes': nodes, 'edges': edges})


# --------------------------------------------------------------------------- T1-T6: plain LLM node

@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_t1_a_token_refused_node_that_recovers_marks_the_run_incomplete(monkeypatch, policy):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('one', 8, 2)], calls)
    workflow = token_flow(policy)
    result, _ = await execute(workflow)
    assert len(calls) == 1, 'the refused node must not reach the provider'
    if policy == 'route': assert 'fallback' in result['values'] and 'ok' not in result['values']
    verdict = run_truncation(workflow, result['values'])
    assert verdict['truncated'] is True and verdict['truncation_source'] == 'confirmed'
    assert verdict['truncation_reason'] == 'second did not run: ' + REASON


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_t2_the_recovered_event_keeps_the_typed_token_exhaustion(monkeypatch, policy):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    chat(monkeypatch, [('one', 8, 2)], [])
    _, events = await execute(token_flow(policy))
    failed = failed_event(events, 'second')
    assert failed['recovered'] is True and failed.get('budget_exhausted') is True
    assert failed['error'] == REASON


@pytest.mark.asyncio
async def test_t3_on_error_fail_still_fails_the_run_with_the_token_message(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    chat(monkeypatch, [('one', 8, 2)], [])
    with pytest.raises(ValueError) as exc:
        await execute(token_flow('fail'))
    assert str(exc.value) == REASON


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_t4_an_ordinary_provider_error_is_a_recovered_failure_not_truncation(monkeypatch, policy):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
    chat(monkeypatch, [('one', 8, 2), (ValueError('Ollama returned no text.'), 8, 2)], [])
    workflow = token_flow(policy)
    result, events = await execute(workflow)
    assert run_truncation(workflow, result['values'])['truncated'] is False
    failed = failed_event(events, 'second')
    assert failed['recovered'] is True and 'budget_exhausted' not in failed


@pytest.mark.asyncio
async def test_t12_a_token_refusal_is_not_retried(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('one', 8, 2)], calls)
    _, events = await execute(token_flow('continue', retry={'attempts': 2, 'base_delay': 0}))
    assert len(calls) == 1 and not [e for e in events if e.get('status') == 'retrying']


def durable(tmp_path, monkeypatch, name='tokens'):
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    tmp_path.mkdir(exist_ok=True)
    store = Store(f'sqlite:///{tmp_path}/{name}.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture', '', 'local')
    return store, Worker(store)


@pytest.mark.asyncio
@pytest.mark.parametrize('policy', ['continue', 'route'])
async def test_t5_the_durable_run_stores_a_budget_cause(tmp_path, monkeypatch, policy):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('one', 8, 2)], calls)
    store, worker = durable(tmp_path, monkeypatch)
    try:
        row = store.create_run(token_flow(policy).model_dump(mode='json'), 'go')
        await worker.execute(store.claim_next(worker.owner))
        run = store.run(row['id'])
        assert run['status'] == 'success' and len(calls) == 1
        assert run['truncated'] is True and run['truncation_source'] == 'confirmed'
        assert [(c['node_id'], c['kind'], c['source']) for c in run['truncation_causes']] == [('second', 'budget', 'confirmed')]
    finally: store.engine.dispose()


@pytest.mark.asyncio
async def test_t6_resume_after_the_recovery_reaches_the_same_verdict_without_a_new_call(tmp_path, monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('one', 8, 2)], calls)
    baseline_store, baseline_worker = durable(tmp_path / 'a', monkeypatch)
    row = baseline_store.create_run(token_flow('continue').model_dump(mode='json'), 'go')
    await baseline_worker.execute(baseline_store.claim_next(baseline_worker.owner))
    baseline = baseline_store.run(row['id'])
    assert baseline['truncated'] is True

    calls.clear(); chat(monkeypatch, [('one', 8, 2)], calls)
    store, worker = durable(tmp_path / 'b', monkeypatch)
    original = store.worker_event
    def crash(id, owner, event):
        persisted = original(id, owner, event)
        if event.get('node_id') == 'second' and event.get('recovered'): raise asyncio.CancelledError()
        return persisted
    monkeypatch.setattr(store, 'worker_event', crash)
    row = store.create_run(token_flow('continue').model_dump(mode='json'), 'go')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(row['id'])['status'] != 'success'
    monkeypatch.setattr(store, 'worker_event', original)
    store.resume_run(row['id'])
    await worker.execute(store.claim_next(worker.owner))
    resumed = store.run(row['id'])
    assert resumed['status'] == 'success' and len(calls) == 1, 'second is refused again, not granted a new budget'
    for key in ('truncated', 'truncation_reason', 'truncation_source', 'output', 'truncation_causes'):
        assert resumed[key] == baseline[key], key
    baseline_store.engine.dispose(); store.engine.dispose()


# --------------------------------------------------------------------------- T8, T11: repairs keep the type

def query_flow(policy='continue'):
    return Workflow.model_validate({'version': 1, 'name': 'Query', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'ask', 'type': 'query', 'inputs': {'query': 'input.message'}, 'on_error': policy,
         'config': {'knowledge_base_id': 'kb1', 'mode': 'keyword', **MODEL}},
        respond('out')],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'ask'}, {'id': 'b', 'source': 'ask', 'target': 'out'}]})


@pytest.mark.asyncio
async def test_t8_a_query_whose_repair_is_refused_is_a_budget_failure(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('not json', 8, 2)], calls)
    workflow = query_flow()
    result, events = await execute(workflow, kb_platform([]))
    failed = failed_event(events, 'ask')
    assert len(calls) == 1
    assert failed.get('budget_exhausted') is True and failed['error'] == REASON
    assert 'Invalid grounded answer contract' not in failed['error']
    verdict = run_truncation(workflow, result['values'])
    assert verdict['truncation_reason'] == 'ask stopped after 1 attempt(s): ' + REASON


@pytest.mark.asyncio
async def test_t11_a_request_reader_repair_refusal_keeps_its_type(monkeypatch):
    from backend.app.registry import Context
    from backend.app.request_reader import RequestReaderConfig, read_request
    from backend.tests.test_request_reader import settings
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('not JSON', 8, 2)], calls)
    run = {'usage': {}, 'usage_by_invocation': {}}
    with pytest.raises(ValueError) as exc:
        await read_request('3 laptops at $450 each.', RequestReaderConfig.model_validate(settings()),
                           Context('', lambda _: '', node_id='reader', checkpoint_owner='reader', run=run))
    assert typed(exc.value) and str(exc.value) == REASON and len(calls) == 1


@pytest.mark.asyncio
async def test_t11b_a_request_router_repair_refusal_keeps_its_type(monkeypatch):
    from backend.app.registry import Context
    from backend.tests.test_request_router import config, router
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('not JSON', 8, 2)], calls)
    run = {'usage': {}, 'usage_by_invocation': {}}
    with pytest.raises(ValueError) as exc:
        await router().request_router_node({'message': 'Buy laptops'}, config(),
            Context('Buy laptops', lambda _: '', node_id='router', checkpoint_owner='router', run=run))
    assert typed(exc.value) and str(exc.value) == REASON and len(calls) == 1


# --------------------------------------------------------------------------- agents (D1 = B)

def agent_config(workflow, policy='fail', **config):
    """Set the agent's model and config. With policy 'route', a fallback response takes
    the error branch, so a typed failure is observable as a recovered node."""
    agent = next(n for n in workflow.nodes if n.id == 'agent')
    agent.config = {**MODEL, **config}; agent.on_error = policy
    if policy != 'route': return workflow
    document = workflow.model_dump(mode='json')
    document['nodes'].append(respond('fallback'))
    document['edges'].append({'id': 'err', 'source': 'agent', 'target': 'fallback', 'sourceHandle': 'error'})
    return Workflow.model_validate(document)


def plain_agent(policy='route', **config):
    """input -> agent (no tools, plain text input) -> out."""
    return agent_config(Workflow.model_validate({'version': 1, 'name': 'Plain', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'agent', 'type': 'agent', 'inputs': {'input': 'input.message'}, 'config': {'provider': 'demo'}},
        respond('out', 'agent.text')],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'agent'}, {'id': 'b', 'source': 'agent', 'target': 'out'}]}),
        policy, **config)


def assert_typed_failure(result, events, calls):
    failed = failed_event(events, 'agent')
    assert len(calls) == 1, 'no call after exhaustion'
    assert failed.get('budget_exhausted') is True and failed['error'] == REASON
    assert set(result['values']['agent']) == {TRUNCATION_KEY}, 'no business output, no prose'
    assert 'fallback' in result['values']


def assert_budget_abstention(output, node_id='agent', truncations=None):
    assert output['text'] == BUDGET_TEXT
    sources = json.loads(output['sources'])
    assert [s['id'] for s in sources] == ['chunk1'] and not any(s['cited'] for s in sources)
    grounding = json.loads(output['grounding'])
    assert grounding['abstain'] is True and grounding['compliance'] == 'budget_exhausted'
    assert grounding['answer'] == '' and grounding['citations'] == [] and grounding['reason'] == REASON
    assert grounding['truncated'] is True and grounding['truncation_reason'] == REASON
    assert grounding['truncations'] == (truncations or [{'node_id': node_id, 'reason': REASON}])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', sorted(STRUCTURED))
async def test_t9_a_structured_agent_without_evidence_fails_typed_when_its_repair_is_refused(monkeypatch, kind):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('not json', 8, 2)], calls)
    result, events = await execute(plain_agent(**STRUCTURED[kind]), message='Total?')
    assert_typed_failure(result, events, calls)
    assert 'did not match the required structure' not in failed_event(events, 'agent')['error']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', sorted(STRUCTURED))
async def test_t9b_a_structured_agent_with_evidence_fails_typed_when_its_repair_is_refused(monkeypatch, kind):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('not json', 8, 2)], calls)
    result, events = await execute(agent_config(graph(), 'route', **STRUCTURED[kind]), kb_platform([]), 'What codename?')
    assert_typed_failure(result, events, calls)


@pytest.mark.asyncio
async def test_t9c_a_free_text_agent_whose_repair_is_refused_returns_the_budget_abstention(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('not json', 8, 2)], calls)
    result, _ = await execute(agent_config(graph()), kb_platform([]), 'What codename?')
    assert len(calls) == 1
    assert_budget_abstention(result['values']['agent'])


@pytest.mark.asyncio
async def test_t9d_a_free_text_agent_with_no_evidence_whose_repair_is_refused_keeps_the_no_evidence_abstention(monkeypatch):
    """Grounded (a retrieval tool is attached) but nothing was retrieved: the budget
    abstention would list no sources, so the no-evidence abstention applies, as at a step."""
    from backend.app.agent_runtime import NO_EVIDENCE
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [('not json', 8, 2)], calls)
    verified = []
    result, _ = await execute(agent_config(agent_with_retrieval()), kb_platform(verified), 'facts')
    output = result['values']['agent']
    assert len(calls) == 1 and verified == []
    assert output['text'] == NO_EVIDENCE + '\n\nReason: No evidence passages were retrieved.'
    assert json.loads(output['sources']) == []
    grounding = json.loads(output['grounding'])
    assert grounding['abstain'] is True and grounding['truncated'] is True and grounding['truncation_reason'] == REASON
    assert grounding['truncations'] == [{'node_id': 'agent', 'reason': REASON}]


@pytest.mark.asyncio
async def test_t10_a_free_text_agent_with_evidence_makes_no_call_after_exhaustion(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': 'q'}), 8, 2)], calls)
    workflow = agent_config(agent_with_retrieval())
    result, _ = await execute(workflow, kb_platform([]), 'facts')
    assert len(calls) == 1, 'B: no final call once tokens are known to be exhausted'
    output = result['values']['agent']
    assert_budget_abstention(output)
    assert 'could not find matching evidence' not in output['text']
    assert run_truncation(workflow, result['values'])['truncated'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('evidence', [True, False])
@pytest.mark.parametrize('kind', sorted(STRUCTURED))
async def test_t10b_a_structured_agent_fails_typed_at_a_step_with_or_without_evidence(monkeypatch, kind, evidence):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': '2+2'}), 8, 2)], calls)
    workflow = agent_with_retrieval() if evidence else calc_flow()
    result, events = await execute(agent_config(workflow, 'route', **STRUCTURED[kind]), kb_platform([]), 'facts')
    assert_typed_failure(result, events, calls)


@pytest.mark.asyncio
async def test_t10c_a_nested_free_text_specialist_returns_the_budget_abstention(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []
    chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'spec0', 'input': 'look it up'}), 1, 0),
                       (json.dumps({'action': 'call', 'target': 'kb0', 'input': 'query'}), 7, 2)], calls)
    workflow = team_flow(1)
    for node in workflow.nodes:
        if node.type == 'agent': node.config = {**node.config, **MODEL}
    verified = []
    result, events = await execute(workflow, kb_platform(verified), 'facts')
    assert len(calls) == 2, 'neither agent calls the model after exhaustion'
    child = [e for e in events if e.get('node_id') == 'spec0' and e.get('status') == 'success']
    assert len(child) == 1, 'the specialist succeeds with the budget abstention'
    assert_budget_abstention(child[0]['outputs'], 'spec0')
    parent = json.loads(result['values']['agent']['grounding'])
    assert parent['truncated'] is True and {'node_id': 'spec0', 'reason': REASON} in parent['truncations']
    assert len(verified) == 3, 'each budget abstention checks its sources once, then the Response (R01)'


@pytest.mark.asyncio
async def test_t10d_action_budget_only_keeps_the_final_partial_answer_call(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '1000')
    monkeypatch.setenv('AGENT_RUN_BUDGET', '1')
    calls = []
    chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': 'q'}), 8, 2),
                       (grounded('Finding [S1]', ['S1']), 8, 2)], calls)
    result, _ = await execute(agent_config(agent_with_retrieval()), kb_platform([]), 'facts')
    assert len(calls) == 2
    output = result['values']['agent']
    assert output['text'] == 'Finding [S1]'
    grounding = json.loads(output['grounding'])
    assert grounding['truncated'] is True and 'AGENT_RUN_BUDGET' in grounding['truncation_reason']


@pytest.mark.asyncio
@pytest.mark.parametrize('site', ['step', 'repair'])
async def test_t14_a_removed_source_fails_the_budget_abstention(monkeypatch, site):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    if site == 'step':
        chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': 'q'}), 8, 2)], [])
        workflow, message = agent_config(agent_with_retrieval()), 'facts'
    else:
        chat(monkeypatch, [('not json', 8, 2)], [])
        workflow, message = agent_config(graph()), 'What codename?'
    with pytest.raises(ValueError) as exc:
        await execute(workflow, kb_platform([], removed=True), message)
    assert str(exc.value) == REMOVED, 'the source check failure is not converted to token exhaustion'


@pytest.mark.asyncio
async def test_t14_a_removed_source_fails_a_nested_specialist_budget_abstention(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'spec0', 'input': 'look it up'}), 1, 0),
                       (json.dumps({'action': 'call', 'target': 'kb0', 'input': 'query'}), 7, 2)], [])
    workflow = team_flow(1)
    for node in workflow.nodes:
        if node.type == 'agent': node.config = {**node.config, **MODEL}
    events = []
    async def emit(event): events.append(dict(event))
    with pytest.raises(ValueError) as exc:
        await compile_workflow(workflow, message='facts', platform_resolver=kb_platform([], removed=True),
                               emit=emit).graph.ainvoke({'values': {}})
    assert failed_event(events, 'spec0', nested=True)['error'] == REMOVED
    for event in events:
        if event.get('node_id') in ('agent', 'spec0') and event.get('status') == 'success':
            assert 'chunk1' not in event['outputs'].get('sources', ''), event['node_id']
    parent = [e for e in events if e.get('node_id') == 'agent' and e.get('status') == 'success']
    assert len(parent) == 1 and json.loads(parent[0]['outputs']['sources']) == []
    # R01: the shared registry still holds the removed passage, so the Response refuses.
    assert type(exc.value).__name__ == 'UnsafeExecutionError' and str(exc.value).startswith('out: ')
    assert not [e for e in events if e.get('node_id') == 'out' and e.get('status') == 'success']


@pytest.mark.asyncio
@pytest.mark.parametrize('site', ['step', 'repair'])
async def test_t14b_an_unchanged_source_is_checked_exactly_once(monkeypatch, site):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    if site == 'step':
        chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': 'q'}), 8, 2)], [])
        workflow, message = agent_config(agent_with_retrieval()), 'facts'
    else:
        chat(monkeypatch, [('not json', 8, 2)], [])
        workflow, message = agent_config(graph()), 'What codename?'
    verified = []
    result, _ = await execute(workflow, kb_platform(verified), message)
    assert_budget_abstention(result['values']['agent'])
    # Once by the abstention, once by the Response before it publishes (R01).
    assert len(verified) == 2 and [[s['id'] for s in batch] for batch in verified] == [['chunk1'], ['chunk1']]


@pytest.mark.asyncio
async def test_t15_a_valid_answer_that_spends_the_last_tokens_stays_complete(monkeypatch):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls = []; chat(monkeypatch, [(grounded('Bluebird [S1]', ['S1']), 8, 2)], calls)
    workflow = agent_config(graph())
    result, _ = await execute(workflow, kb_platform([]), 'What codename?')
    output = result['values']['agent']
    assert len(calls) == 1 and output['text'] == 'Bluebird [S1]'
    assert not json.loads(output['grounding']).get('truncated')
    assert run_truncation(workflow, result['values'])['truncated'] is False


# --------------------------------------------------------------------------- T7: loops

HALT = 'each stopped at item 2: Run token budget exhausted; raise RELAY_RUN_TOKEN_LIMIT. 1 accepted items did not run.'


def query_loop(on_item_error='continue', connection_id='c1', kb='kb1'):
    document = loop_flow(on_item_error=on_item_error, connection_id=connection_id).model_dump(mode='json')
    body = next(n for n in document['nodes'] if n['id'] == 'work')
    body.update(type='query', config={'knowledge_base_id': kb, 'mode': 'keyword', **MODEL})
    return Workflow.model_validate(document)


LOOP_REPLIES = [(grounded('Fact [S1]', ['S1']), 8, 2)]


@pytest.mark.asyncio
@pytest.mark.parametrize('on_item_error', ['continue', 'stop'])
async def test_t7_a_loop_refused_by_tokens_after_work_halts_with_the_budget_reason(monkeypatch, on_item_error):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls, retrievals = [], []
    chat(monkeypatch, LOOP_REPLIES, calls)
    workflow = query_loop(on_item_error)
    result, events = await execute(workflow, kb_platform([], retrievals=retrievals, items=issues(3)))
    each = result['values']['each']
    summary = json.loads(each['summary'])
    assert (len(retrievals), len(calls)) == (2, 1)
    assert [r['status'] for r in each['results']] == ['success', 'failed']
    assert each['results'][1]['budget_halt'] == 'tokens' and each['results'][1]['error'] == REASON
    assert summary['budget_exhausted'] is True and summary['processed'] == 2 and summary['failed'] == 1
    assert summary['stopped'] is False and summary['truncation_reason'] == HALT
    assert not [e for e in events if e.get('item_index') == 2 and e.get('node_id') == 'work']
    assert run_truncation(workflow, result['values'])['truncated'] is True


def test_t7c_compact_keeps_the_budget_halt_within_its_reserve():
    from backend.app import iteration
    for kind in ('tokens', 'action'):
        entry = {'index': iteration.MAX_ITEMS_CEILING - 1, 'status': 'failed', 'value': 'x', 'error': 'x',
                 'truncation_reason': 'x', 'budget_halt': kind}
        short = iteration.compact(entry)
        assert short['budget_halt'] == kind
        assert iteration.serialized_bytes(short) + 2 <= iteration.COMPACT_BYTES


def loop_setup(tmp_path, monkeypatch, services):
    from backend.app import tool_service
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from cryptography.fernet import Fernet
    monkeypatch.setattr(tool_service, 'public_addresses', lambda host, port: ['93.184.216.34'])
    tmp_path.mkdir()
    store = Store(f'sqlite:///{tmp_path}/loop.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture', '', 'local')
    worker = Worker(store); worker.kbs = services
    jira = json.dumps({'issues': [{'key': f'PROJ-{i}', 'fields': {
        'summary': f'What is the project codename {i}?', 'status': {'name': 'Open'},
        'assignee': {'displayName': 'Ana'}, 'updated': '2026-09-01T00:00:00Z'}} for i in range(3)]})
    async def prepared(_prepared, tenant_id='local'): return jira
    monkeypatch.setattr(worker.tools, 'execute_prepared', prepared)
    return store, worker


async def durable_loop(tmp_path, monkeypatch, services, kb, on_item_error, crash_at=None):
    calls, retrievals = [], []
    chat(monkeypatch, LOOP_REPLIES, calls)
    original_retrieve = type(services).retrieve
    async def counted(self, *args):
        retrievals.append(args[1]); return await original_retrieve(self, *args)
    monkeypatch.setattr(type(services), 'retrieve', counted)
    store, worker = loop_setup(tmp_path, monkeypatch, services)
    try:
        row = store.create_run(query_loop(on_item_error, jira_connection(store), kb['id']).model_dump(mode='json'), 'go')
        if crash_at is not None:
            original = store.worker_event
            def crash(id, owner, event):
                persisted = original(id, owner, event)
                if event.get('kind') == 'loop_item' and event.get('item_index') == crash_at: raise asyncio.CancelledError()
                return persisted
            monkeypatch.setattr(store, 'worker_event', crash)
            await worker.execute(store.claim_next(worker.owner))
            assert store.run(row['id'])['status'] != 'success'
            monkeypatch.setattr(store, 'worker_event', original)
            store.resume_run(row['id'])
        await worker.execute(store.claim_next(worker.owner))
        run = store.run(row['id'])
        assert run['status'] == 'success', run.get('error')
        return run, len(retrievals), len(calls)
    finally:
        monkeypatch.setattr(type(services), 'retrieve', original_retrieve)
        store.engine.dispose()


def outcome(run):
    each = run['checkpoints']['each']
    return {'results': each['results'], 'summary': json.loads(each['summary']),
            'actions': run['accounting']['action_budget']['counter'],
            'verdict': (run['truncated'], run['truncation_reason'], run['truncation_source'])}


@pytest.mark.asyncio
@pytest.mark.parametrize('on_item_error', ['continue', 'stop'])
async def test_t7b_resume_after_the_halted_item_is_saved_repeats_no_work(tmp_path, monkeypatch, services, on_item_error):
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    kb, _, _ = await populated(services)
    fresh, fresh_retrievals, fresh_calls = await durable_loop(tmp_path / 'fresh', monkeypatch, services, kb, on_item_error)
    resumed, retrievals, calls = await durable_loop(tmp_path / 'resumed', monkeypatch, services, kb, on_item_error, crash_at=1)
    assert (fresh_retrievals, fresh_calls) == (2, 1)
    assert (retrievals, calls) == (2, 1), 'resume must not retrieve for an item after the halt'
    assert outcome(resumed) == outcome(fresh)
    summary = outcome(fresh)['summary']
    assert summary['budget_exhausted'] is True and summary['truncation_reason'] == HALT


@pytest.mark.asyncio
async def test_t7c_a_compacted_halt_survives_crash_and_resume(tmp_path, monkeypatch, services):
    from backend.app import iteration
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    # Room for exactly item 0 and item 1 in compact form plus the reserve for item 2, so
    # both entries, the marked one included, are stored compact.
    size = lambda entry: iteration.serialized_bytes(iteration.compact(entry))
    room = 2 + size({'index': 0, 'status': 'success', 'value': 'x'}) + 2 \
        + size({'index': 1, 'status': 'failed', 'error': 'x', 'budget_halt': 'tokens'}) + iteration.COMPACT_BYTES
    monkeypatch.setattr(iteration, 'HARD_RESULT_BYTES', room)
    kb, _, _ = await populated(services)
    fresh, fresh_retrievals, fresh_calls = await durable_loop(tmp_path / 'fresh', monkeypatch, services, kb, 'continue')
    resumed, retrievals, calls = await durable_loop(tmp_path / 'resumed', monkeypatch, services, kb, 'continue', crash_at=1)
    halted = outcome(fresh)['results'][1]
    assert halted['details_omitted'] is True and halted['budget_halt'] == 'tokens'
    assert (fresh_retrievals, fresh_calls) == (retrievals, calls) == (2, 1)
    assert outcome(resumed) == outcome(fresh)
    reason = outcome(fresh)['summary']['truncation_reason']
    assert reason.startswith(HALT), 'the reason comes from the marker kind, not the omitted error text'


# --------------------------------------------------------------------------- R02: approved work
# An approved write runs on resume against the reservation made before the pause, so it
# makes no new debit. It is still work: an attempt that sent it, or that replays its stored
# result after a crash, did run. The write is never sent or charged twice.

POST = {'method': 'POST', 'path': '/messages', 'body': '{"text":"{input}"}', 'enable_writes': True, 'approval': True}


def approved_flow(worker, loop):
    connection = worker.tools.save_connection({'name': 'sink', 'provider': 'http', 'endpoint': 'https://example.com'})
    workflow = calc_flow()
    workflow.nodes[2].type = 'tool_http'
    workflow.nodes[2].config = {'connection_id': connection['id'], **POST}
    if not loop: return agent_config(workflow, 'route', output_schema=SCHEMA)
    jira = worker.tools.save_connection({'name': 'jira', 'provider': 'jira', 'endpoint': 'https://jira.example.com',
                                         'username': 'bot', 'secret': 'fixture'})
    document = loop_flow(connection_id=jira['id']).model_dump(mode='json')
    next(n for n in document['nodes'] if n['id'] == 'work').update(type='agent', config={**MODEL, 'output_schema': SCHEMA})
    document['nodes'].append(workflow.nodes[2].model_dump(mode='json'))
    document['edges'].append({'id': 'tool', 'source': 'calc', 'target': 'work', 'kind': 'tool', 'targetHandle': 'tools'})
    return Workflow.model_validate(document)


async def approved_send(tmp_path, monkeypatch, loop=False, crash=None):
    """Plan a write (8 + 2 tokens, the whole limit), pause, approve, resume: the write is
    sent and the next step is refused. crash(event) stops the resumed worker after that
    event is saved; the run is then resumed once more."""
    import httpx
    from backend.app import approvals, tool_service
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', LIMIT)
    calls, deliveries = [], []
    chat(monkeypatch, [(json.dumps({'action': 'call', 'target': 'calc', 'input': 'hello'}), 8, 2)], calls)
    monkeypatch.setattr(tool_service, 'public_addresses', lambda *_: ['93.184.216.34'])
    def sink(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'issues': [{'key': f'P-{i}', 'fields': {'summary': f'Issue {i}',
                'status': {'name': 'Open'}, 'assignee': None, 'updated': ''}} for i in range(2)]})
        deliveries.append(json.loads(request.content)); return httpx.Response(200, text='sent')
    store, worker = durable(tmp_path, monkeypatch, 'approval')
    try:
        worker.tools.transport = httpx.MockTransport(sink)
        row = store.create_run(approved_flow(worker, loop).model_dump(mode='json'), 'send')
        await worker.execute(store.claim_next(worker.owner))
        paused = store.run(row['id'])
        assert paused['status'] == 'awaiting_approval' and not deliveries
        approval = paused['approvals'][0]
        approvals.decide(store, row['id'], 'local', approval['id'], approval['digest'], 'approve')
        if crash is not None:
            original = store.worker_event
            def stop(id, owner, event):
                persisted = original(id, owner, event)
                if crash(event): raise asyncio.CancelledError()
                return persisted
            monkeypatch.setattr(store, 'worker_event', stop)
            await worker.execute(store.claim_next(worker.owner))
            assert store.run(row['id'])['status'] != 'success'
            monkeypatch.setattr(store, 'worker_event', original)
            store.resume_run(row['id'])
        await worker.execute(store.claim_next(worker.owner))
        saved = store.run(row['id'])
        assert saved['status'] == 'success', saved.get('error')
        assert (len(calls), len(deliveries)) == (1, 1), 'one model call and one POST, never repeated'
        return saved
    finally: store.engine.dispose()


def graph_verdict(saved):
    return (saved['truncated'], saved['truncation_reason'], saved['accounting']['action_budget']['counter'],
            [(c['node_id'], c['kind']) for c in saved['truncation_causes']])


def loop_verdict(saved):
    each = saved['checkpoints']['each']
    return (json.loads(each['summary']), each['results'], saved['accounting']['action_budget']['counter'],
            [e for e in saved['events'] if e.get('status') == 'not_run'])


@pytest.mark.asyncio
async def test_r02_an_agent_that_sent_an_approved_write_did_run(tmp_path, monkeypatch):
    saved = await approved_send(tmp_path, monkeypatch)
    truncated, reason, actions, causes = graph_verdict(saved)
    assert truncated is True and reason == 'agent stopped after 1 attempt(s): ' + REASON
    assert actions == 1, 'the approved write uses its reservation; it is not charged again'
    assert causes == [('agent', 'budget')]


@pytest.mark.asyncio
async def test_r02_a_loop_records_the_item_whose_approved_write_was_sent(tmp_path, monkeypatch):
    saved = await approved_send(tmp_path, monkeypatch, loop=True)
    summary, results, actions, not_run = loop_verdict(saved)
    assert summary['processed'] == 1 and summary['failed'] == 1 and summary['budget_exhausted'] is True
    assert results[0]['budget_halt'] == 'tokens' and results[0]['status'] == 'failed'
    assert actions == 2, 'one Jira read and one write, reserved once'
    assert not_run == []


@pytest.mark.asyncio
async def test_r02_resume_that_replays_the_stored_write_reaches_the_same_verdict(tmp_path, monkeypatch):
    """Crash after the recovered event: the frame replays, the write comes back as its
    stored result (no second POST, no new charge), and the verdict is the same."""
    fresh = await approved_send(tmp_path / 'fresh', monkeypatch)
    resumed = await approved_send(tmp_path / 'resumed', monkeypatch,
                                  crash=lambda e: e.get('node_id') == 'agent' and e.get('recovered'))
    assert graph_verdict(resumed) == graph_verdict(fresh)


@pytest.mark.asyncio
async def test_r02_a_loop_resumed_after_the_send_records_the_same_item(tmp_path, monkeypatch):
    """Crash right after the write's success event, before the item result is saved: the
    replay returns the stored write result and the item is recorded as in a fresh run."""
    fresh = await approved_send(tmp_path / 'fresh', monkeypatch, loop=True)
    resumed = await approved_send(tmp_path / 'resumed', monkeypatch, loop=True,
                                  crash=lambda e: e.get('node_id') == 'calc' and e.get('status') == 'success')
    assert loop_verdict(resumed) == loop_verdict(fresh)
