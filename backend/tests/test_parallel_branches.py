"""A5.3 Part 1: parallel branches with an explicit Join.

Design: docs/superpowers/specs/2026-10-24-parallel-branches-design.md (revision 3).
A node with 2-8 normal edges forks; every branch is a closed chain that ends at one Join;
branches are read-only; after the Join every branch output is bindable. Validation shapes
inside one test each assert their own message. Runtime tests use the real Worker and Store
with synthetic HTTP, and barriers instead of timing.
"""
import asyncio
import json

import httpx
import pytest
from langgraph.graph import END, START, StateGraph

from backend.app import providers
from backend.app.compiler import State, bindable_outputs, validate_workflow
from backend.app.models import Workflow

FORK_END = 'must all end at one Join'
MEET = 'Parallel branches meet only at a Join'
CLOSED = 'can be entered only from its fork and left only to its Join'
INSIDE = 'branches cannot stop, route, decide or fork'
WRITES = 'external writes belong after the Join'
MEMORY = 'an Agent that writes memory cannot run in a parallel branch'
WIDTH = 'A fork has 2–8 branches'
ONE_FORK = 'a Join waits for the branches of one fork'
NO_RECOVERY = 'a Join cannot recover or retry'


# --------------------------------------------------------------------------- builders

def prompt(id, source='input.message', **extra):
    return {'id': id, 'type': 'prompt', 'inputs': {'message': source}, 'config': {'template': '{message}'}, **extra}


def join(id='join', **extra):
    return {'id': id, 'type': 'join', **extra}


def response(source, id='out'):
    return {'id': id, 'type': 'response', 'inputs': {'text': source}}


def document(nodes, edges, contract=1):
    """A workflow from (source, target[, handle]) edge tuples; edge ids are generated."""
    built = []
    for i, edge in enumerate(edges):
        source, target, *rest = edge
        item = {'id': f'e{i}', 'source': source, 'target': target}
        if rest and isinstance(rest[0], str): item['sourceHandle'] = rest[0]
        if rest and isinstance(rest[-1], dict): item.update(rest[-1])
        built.append(item)
    return {'version': 1, 'contract': contract, 'name': 'Parallel', 'nodes': [{'id': 'input', 'type': 'chat_input'}, *nodes], 'edges': built}


def standard(a=None, b=None, out='b2.text'):
    """input -> fork -> a1 -> join ; fork -> b1 -> b2 -> join ; join -> out."""
    nodes = [prompt('fork'), a or prompt('a1', 'fork.text'), b or prompt('b1', 'fork.text'), prompt('b2', 'b1.text'), join(), response(out)]
    edges = [('input', 'fork'), ('fork', nodes[1]['id']), ('fork', nodes[2]['id']), (nodes[1]['id'], 'join'),
             (nodes[2]['id'], 'b2'), ('b2', 'join'), ('join', 'out')]
    return document(nodes, edges)


def errors(raw):
    return validate_workflow(Workflow.model_validate(raw))


def has(messages, text, node=None, shape=''):
    found = [m for m in messages if text in m and (node is None or m.startswith(f'{node}: ') or f' {node} ' in m)]
    assert found, f'{shape}: expected {text!r}{" for " + node if node else ""} in {messages}'


def condition(id, source):
    return {'id': id, 'type': 'condition', 'inputs': {'value': source}, 'config': {'contains': 'yes'}}


def write_tool(id):
    return {'id': id, 'type': 'tool_http', 'config': {'connection_id': 'sink', 'method': 'POST', 'enable_writes': True, 'approval': False}}


def agent(id, source='fork.text', **config):
    return {'id': id, 'type': 'agent', 'inputs': {'input': source} if source else {}, 'config': {'provider': 'demo', **config}}


# --------------------------------------------------------------------------- L01: LangGraph pins

@pytest.mark.asyncio
async def test_l01_langgraph_behaviour_this_design_relies_on():
    """Pinned on langgraph 1.2.11; fails if an upgrade changes any of the three facts."""
    # A raising branch cancels a sibling that is still awaiting.
    cancelled = asyncio.Event()
    async def failing(state):
        await asyncio.sleep(0); raise ValueError('branch a failed')
    async def sibling(state):
        try: await asyncio.sleep(30)
        except asyncio.CancelledError: cancelled.set(); raise
        return {'values': {'b': {}}}
    graph = StateGraph(State)
    graph.add_node('fork', lambda state: {'values': {'fork': {}}})
    graph.add_node('a', failing); graph.add_node('b', sibling)
    graph.add_edge(START, 'fork'); graph.add_edge('fork', 'a'); graph.add_edge('fork', 'b')
    graph.add_edge('a', END); graph.add_edge('b', END)
    with pytest.raises(ValueError, match='branch a failed'):
        await asyncio.wait_for(graph.compile().ainvoke({'values': {}}), 10)
    assert cancelled.is_set()
    # A list edge runs the join once, after both; two plain edges run the target twice.
    for list_edge, expected in ((True, 1), (False, 2)):
        runs = []
        graph = StateGraph(State)
        graph.add_node('fork', lambda state: {'values': {'fork': {}}})
        graph.add_node('a', lambda state: {'values': {'a': {}}})
        graph.add_node('b1', lambda state: {'values': {'b1': {}}})
        graph.add_node('b2', lambda state: {'values': {'b2': {}}})
        graph.add_node('join', lambda state: runs.append(sorted(state['values'])) or {'values': {'join': {}}})
        graph.add_edge(START, 'fork'); graph.add_edge('fork', 'a'); graph.add_edge('fork', 'b1'); graph.add_edge('b1', 'b2')
        if list_edge: graph.add_edge(['a', 'b2'], 'join')
        else: graph.add_edge('a', 'join'); graph.add_edge('b2', 'join')
        graph.add_edge('join', END)
        await asyncio.wait_for(graph.compile().ainvoke({'values': {}}), 10)
        assert len(runs) == expected, (list_edge, runs)


# --------------------------------------------------------------------------- V01-V07: validation

def test_v01_refused_topologies():
    # Branches that meet at an ordinary node.
    meet = document([prompt('fork'), prompt('a1', 'fork.text'), prompt('b1', 'fork.text'), prompt('m', 'fork.text'), response('m.text')],
                    [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'm'), ('b1', 'm'), ('m', 'out')])
    has(errors(meet), MEET)
    # Branches that end at Responses instead of a Join.
    ends = document([prompt('fork'), prompt('a1', 'fork.text'), prompt('b1', 'fork.text'), response('a1.text', 'outa'), response('b1.text', 'outb')],
                    [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'outa'), ('b1', 'outb')])
    has(errors(ends), FORK_END, 'fork')
    # A53-R2-03: a Condition route enters the middle of a branch.
    side = document([condition('choose', 'input.message'), prompt('fork'), prompt('a1', 'input.message'), prompt('b1', 'fork.text'), join(), response('input.message')],
                    [('input', 'choose'), ('choose', 'fork', 'true'), ('choose', 'a1', 'false'), ('fork', 'a1'), ('fork', 'b1'),
                     ('a1', 'join'), ('b1', 'join'), ('join', 'out')])
    has(errors(side), CLOSED, 'a1')
    # A branch node that leaves the region other than to its Join.
    leaves = document([prompt('fork'), prompt('a1', 'fork.text'), prompt('b1', 'fork.text'), join(), prompt('m', 'fork.text'), response('m.text')],
                      [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'm'), ('b1', 'join'), ('join', 'm'), ('m', 'out')])
    has(errors(leaves), CLOSED, 'a1')


def test_v02_accepted_topologies():
    # Unequal lengths, then a second fork/Join region in sequence.
    sequence = document([prompt('fork'), prompt('a1', 'fork.text'), prompt('b1', 'fork.text'), prompt('b2', 'b1.text'), join('j1'),
                         prompt('g', 'a1.text'), prompt('c1', 'g.text'), prompt('d1', 'b2.text'), join('j2'), response('d1.text')],
                        [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'j1'), ('b1', 'b2'), ('b2', 'j1'),
                         ('j1', 'g'), ('g', 'c1'), ('g', 'd1'), ('c1', 'j2'), ('d1', 'j2'), ('j2', 'out')])
    assert errors(sequence) == []
    # An exclusive Condition merge after a completed Join.
    merge = document([prompt('fork'), prompt('a1', 'fork.text'), prompt('b1', 'fork.text'), join(), condition('choose', 'a1.text'),
                      prompt('yes', 'b1.text'), prompt('no', 'a1.text'), prompt('m', 'a1.text'), response('m.text')],
                     [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'choose'),
                      ('choose', 'yes', 'true'), ('choose', 'no', 'false'), ('yes', 'm'), ('no', 'm'), ('m', 'out')])
    assert errors(merge) == []


def test_v03_branches_cannot_stop_route_decide_or_fork():
    shapes = {
        'response': (response('fork.text', 'a1'), []),
        'condition': (condition('a1', 'fork.text'), ['true', 'false']),
        'switch': ({'id': 'a1', 'type': 'switch', 'inputs': {'value': 'fork.text'}, 'config': {'cases': [
            {'id': 'billing', 'label': 'Billing', 'when': {'field': 'category', 'operator': 'eq', 'compare_to': 'billing'}}]}}, ['billing', 'default']),
        'request_reader': ({'id': 'a1', 'type': 'request_reader', 'inputs': {'message': 'fork.text'}, 'config': {}}, ['continue', 'reply']),
        'calculate': ({'id': 'a1', 'type': 'calculate', 'inputs': {'value': 'fork.text'}, 'config': {'expression': '1 + 1'}}, []),
        'error route': (prompt('a1', 'fork.text', on_error='route'), ['error']),
        'nested fork': (prompt('a1', 'fork.text'), ['fork2']),
    }
    for name, (node, extra) in shapes.items():
        raw = standard(a=node)
        if name == 'response':
            raw['edges'] = [e for e in raw['edges'] if not (e['source'] == 'a1' and e['target'] == 'join')]
        for handle in extra:
            if handle == 'fork2':
                raw['nodes'].append(prompt('a2', 'a1.text')); raw['nodes'].append(response('a2.text', 'out2'))
                raw['edges'].append({'id': 'x-fork2', 'source': 'a1', 'target': 'a2'})
                raw['edges'].append({'id': 'x-fork2b', 'source': 'a2', 'target': 'out2'})
            elif handle == 'error':
                raw['nodes'].append(response('fork.text', 'recovered'))
                raw['edges'].append({'id': 'x-error', 'source': 'a1', 'target': 'recovered', 'sourceHandle': 'error'})
            else:
                raw['nodes'].append(response('fork.text', f'r-{handle}'))
                raw['edges'].append({'id': f'x-{handle}', 'source': 'a1', 'target': f'r-{handle}', 'sourceHandle': handle})
        has(errors(raw), INSIDE, 'a1', name)


def test_v04_external_writes_belong_after_the_join():
    # A write tool as the branch step.
    flow_write = standard(a={**write_tool('a1'), 'inputs': {'input': 'fork.text'}})
    has(errors(flow_write), WRITES, 'a1')
    # An Agent with an attached write tool.
    attached = standard(a=agent('a1'))
    attached['nodes'].append(write_tool('w'))
    attached['edges'].append({'id': 't-w', 'source': 'w', 'target': 'a1', 'kind': 'tool', 'targetHandle': 'tools'})
    has(errors(attached), WRITES, 'a1')
    # A specialist two levels down holds the write tool.
    deep = standard(a=agent('a1'))
    deep['nodes'] += [agent('s1', None), agent('s2', None), write_tool('w')]
    deep['edges'] += [{'id': 's-1', 'source': 'a1', 'target': 's1', 'kind': 'agent', 'sourceHandle': 'agents'},
                      {'id': 's-2', 'source': 's1', 'target': 's2', 'kind': 'agent', 'sourceHandle': 'agents'},
                      {'id': 't-w', 'source': 'w', 'target': 's2', 'kind': 'tool', 'targetHandle': 'tools'}]
    has(errors(deep), WRITES, 'a1')
    # A For each whose body writes.
    loop = standard(a={'id': 'a1', 'type': 'for_each', 'inputs': {'items': 'fork.text'}, 'config': {'body': 'w'}})
    loop['nodes'].append(write_tool('w'))
    loop['edges'].append({'id': 'l-w', 'source': 'a1', 'target': 'w', 'kind': 'loop', 'sourceHandle': 'body'})
    has(errors(loop), WRITES, 'a1')


def test_v05a_memory_agents_cannot_run_in_a_branch():
    as_step = standard(a=agent('a1', role='memory'))
    has(errors(as_step), MEMORY, 'a1')
    specialist = standard(a=agent('a1'))
    specialist['nodes'].append(agent('s1', None, role='memory'))
    specialist['edges'].append({'id': 's-1', 'source': 'a1', 'target': 's1', 'kind': 'agent', 'sourceHandle': 'agents'})
    has(errors(specialist), MEMORY, 's1')
    body = standard(a={'id': 'a1', 'type': 'for_each', 'inputs': {'items': 'fork.text'}, 'config': {'body': 'm'}})
    body['nodes'].append(agent('m', None, role='memory'))
    body['edges'].append({'id': 'l-m', 'source': 'a1', 'target': 'm', 'kind': 'loop', 'sourceHandle': 'body'})
    has(errors(body), MEMORY, 'm')


def test_v05b_ordinary_agents_may_share_the_default_memory_key():
    raw = standard(a=agent('a1'), b=agent('b1'), out='a1.text')
    assert all(n['config'].get('memory_key', 'default') == 'default' for n in raw['nodes'] if n['type'] == 'agent')
    assert errors(raw) == []


def test_v06_a_fork_has_two_to_eight_branches():
    def wide(count):
        nodes = [prompt('fork')] + [prompt(f'p{i}', 'fork.text') for i in range(count)] + [join(), response('p0.text')]
        edges = [('input', 'fork')] + [('fork', f'p{i}') for i in range(count)] + [(f'p{i}', 'join') for i in range(count)] + [('join', 'out')]
        return document(nodes, edges)
    assert errors(wide(8)) == []
    has(errors(wide(9)), WIDTH)


def test_v07_join_rules():
    two_forks = document([prompt('f'), prompt('a1', 'f.text'), prompt('b1', 'f.text'), prompt('g', 'input.message'),
                          prompt('c1', 'g.text'), prompt('d1', 'g.text'), join(), response('a1.text')],
                         [('input', 'f'), ('f', 'a1'), ('f', 'b1'), ('a1', 'join'), ('b1', 'join'),
                          ('f', 'g'), ('g', 'c1'), ('g', 'd1'), ('c1', 'join'), ('d1', 'join'), ('join', 'out')])
    has(errors(two_forks), ONE_FORK, 'join')
    single = document([prompt('a1'), join(), response('a1.text')], [('input', 'a1'), ('a1', 'join'), ('join', 'out')])
    has(errors(single), ONE_FORK, 'join')
    for extra in ({'on_error': 'continue'}, {'retry': {'attempts': 1}}):
        raw = standard()
        raw['nodes'] = [{**n, **extra} if n['id'] == 'join' else n for n in raw['nodes']]
        has(errors(raw), NO_RECOVERY, 'join')


# --------------------------------------------------------------------------- B01-B03: binding

def test_b01_after_a_join_every_branch_output_is_bindable():
    raw = standard(out='a1.text')
    assert errors(raw) == []
    raw['nodes'] = [n if n['id'] != 'out' else response('b2.text') for n in raw['nodes']]
    assert errors(raw) == []
    offered = bindable_outputs(Workflow.model_validate(raw))
    assert {'a1.text', 'b1.text', 'b2.text', 'fork.text', 'input.message'} <= set(offered['out'])


def test_b02_a_branch_cannot_bind_its_sibling():
    sibling = standard(b=prompt('b1', 'a1.text'))
    has(errors(sibling), 'invalid binding message = a1.text', 'b1')
    assert errors(standard(b=prompt('b1', 'fork.text'))) == []
    offered = bindable_outputs(Workflow.model_validate(standard()))
    assert 'a1.text' not in offered['b2'] and 'fork.text' in offered['b2']


def test_b03_a_continue_node_in_a_branch_is_not_bindable_after_the_join():
    raw = standard(a=prompt('a1', 'fork.text', on_error='continue'), out='a1.text')
    assert errors(raw) == ["a1: on_error 'continue' is not allowed while out reads its output; use 'route' instead."]
    raw['nodes'] = [n if n['id'] != 'out' else response('b2.text') for n in raw['nodes']]
    assert errors(raw) == []


# --------------------------------------------------------------------------- R01-R06: runtime

class Models:
    """Ollama chat keyed by the node's system text. A gated key waits for its release; a
    barrier key waits until every barrier key has started, which a sequential run cannot do."""
    def __init__(self, monkeypatch, tokens=None, fail=(), gated=(), barrier=()):
        self.calls, self.tokens, self.fail = [], dict(tokens or {}), set(fail)
        self.started = {key: asyncio.Event() for key in {*gated, *barrier}}
        self.release = {key: asyncio.Event() for key in gated}
        self.barrier = tuple(barrier)
        async def chat(model, system, prompt, usage=None, **kwargs):
            self.calls.append(system)
            if system in self.started: self.started[system].set()
            if system in self.barrier:
                await asyncio.wait_for(asyncio.gather(*(self.started[key].wait() for key in self.barrier)), 10)
            if system in self.release: await self.release[system].wait()
            if system in self.fail: raise ValueError(f'{system} failed')
            if usage is not None: usage.update(prompt_tokens=self.tokens.get(system, 5), completion_tokens=0)
            return f'answer {system}'
        monkeypatch.setattr(providers, 'ollama_chat', chat)


def model(id, source, key, **extra):
    return {'id': id, 'type': 'llm', 'inputs': {'prompt': source}, 'config': {'provider': 'ollama', 'model': 'fixture', 'system': key}, **extra}


@pytest.fixture
def lab(tmp_path):
    from cryptography.fernet import Fernet
    from backend.app.storage import Store
    from backend.app.worker import Worker
    store = Store(f'sqlite:///{tmp_path}/parallel.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture', '')
    yield store, Worker(store)
    store.engine.dispose()


def events(store, id, node, status=None):
    return [e for e in store.run(id)['events'] if e.get('node_id') == node and (status is None or e['status'] == status)]


async def until(predicate, seconds=10):
    """A hang guard around an observable condition, not a timing assumption."""
    async def poll():
        while not predicate(): await asyncio.sleep(.01)
    await asyncio.wait_for(poll(), seconds)


def outcome(run_record, node):
    found = [e for e in run_record['events'] if e.get('node_id') == node and e.get('status') in ('success', 'failed', 'cancelled')]
    assert found, f'{node} has no outcome'
    return found[-1]


async def start(store, worker, raw, message='go'):
    row = store.create_run(raw, message)
    return row['id'], asyncio.create_task(asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30))


def two_models(a='a', b='b', out='a1.text', **extra):
    return document([prompt('fork'), model('a1', 'fork.text', a, **extra.get('a1', {})), model('b1', 'fork.text', b), join(), response(out)],
                    [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'out')])


@pytest.mark.asyncio
async def test_r01_branches_run_concurrently_and_the_join_waits_for_the_last(lab, monkeypatch):
    store, worker = lab
    models = Models(monkeypatch, gated=('a', 'b'), barrier=('a', 'b'))
    id, task = await start(store, worker, two_models())
    await asyncio.wait_for(asyncio.gather(models.started['a'].wait(), models.started['b'].wait()), 10)
    assert events(store, id, 'join') == []
    models.release['a'].set()
    await until(lambda: events(store, id, 'a1', 'success'))
    assert events(store, id, 'join') == [], 'the Join started before branch b finished'
    models.release['b'].set()
    await task
    result = store.run(id)
    assert result['status'] == 'success', result.get('error')
    assert len(events(store, id, 'join', 'success')) == 1 and len(events(store, id, 'out', 'success')) == 1


@pytest.mark.asyncio
async def test_r02_unequal_branches_run_the_join_once(lab, monkeypatch):
    store, worker = lab
    Models(monkeypatch)
    raw = document([prompt('fork'), model('a1', 'fork.text', 'a'), model('b1', 'fork.text', 'b'), model('b2', 'b1.text', 'b2'), join(), response('b2.text')],
                   [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'join'), ('b1', 'b2'), ('b2', 'join'), ('join', 'out')])
    id, task = await start(store, worker, raw); await task
    result = store.run(id)
    assert result['status'] == 'success', result.get('error')
    assert len(events(store, id, 'join', 'success')) == 1, events(store, id, 'join')
    assert len(events(store, id, 'out', 'success')) == 1 and result['output'] == 'answer b2'


@pytest.mark.asyncio
async def test_r03_a_failed_branch_fails_the_run_and_cancels_its_sibling(lab, monkeypatch):
    store, worker = lab
    models = Models(monkeypatch, fail=('a',), gated=('b',))
    id, task = await start(store, worker, two_models())
    await asyncio.wait_for(models.started['b'].wait(), 10)
    await task
    result = store.run(id)
    assert result['status'] == 'failed' and result['error'] == 'a failed'
    assert outcome(result, 'a1')['status'] == 'failed' and outcome(result, 'a1')['error'] == 'a failed'
    assert outcome(result, 'b1')['status'] == 'cancelled'
    assert models.calls.count('b') == 1 and events(store, id, 'join') == []


@pytest.mark.asyncio
async def test_r04_a_continue_branch_failure_lets_the_join_run(lab, monkeypatch):
    store, worker = lab
    Models(monkeypatch, fail=('a',))
    id, task = await start(store, worker, two_models(out='b1.text', a1={'on_error': 'continue'})); await task
    result = store.run(id)
    assert result['status'] == 'success' and result['output'] == 'answer b'
    assert outcome(result, 'a1')['status'] == 'failed'
    assert len(events(store, id, 'join', 'success')) == 1


@pytest.mark.asyncio
async def test_r05_admission_limit_admits_one_call_per_running_branch(lab, monkeypatch):
    store, worker = lab
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '100')
    models = Models(monkeypatch, tokens={'a': 80, 'b': 80, 'after': 10}, barrier=('a', 'b'))
    raw = document([prompt('fork'), model('a1', 'fork.text', 'a'), model('b1', 'fork.text', 'b'), join(), model('after', 'a1.text', 'after'), response('after.text')],
                   [('input', 'fork'), ('fork', 'a1'), ('fork', 'b1'), ('a1', 'join'), ('b1', 'join'), ('join', 'after'), ('after', 'out')])
    id, task = await start(store, worker, raw); await task
    result = store.run(id)
    assert sorted(models.calls) == ['a', 'b'], 'both branch calls are admitted at spend 0; the call after the Join is refused'
    assert result['accounting']['tokens_spent'] == 160
    assert result.get('token_limit') == {'policy': 'admission', 'limit': 100, 'spent': 160, 'over': 60}
    refused = outcome(result, 'after')
    assert refused['status'] == 'failed' and refused.get('budget_exhausted') is True and result['status'] == 'failed'


@pytest.mark.asyncio
async def test_r06_contract_2_fork_on_the_request_readers_continue_route(lab, monkeypatch):
    """The reader is a control node and never forks itself; a node on its continue route does."""
    from backend.tests.test_request_reader import reading, settings
    store, worker = lab
    answers = [reading(item='chair', quantity=2, unit_price=10)]
    async def chat(model, system, prompt, usage=None, **kwargs):
        if usage is not None: usage.update(prompt_tokens=5, completion_tokens=2)
        return answers.pop(0)
    monkeypatch.setattr(providers, 'ollama_chat', chat)
    raw = document([{'id': 'reader', 'type': 'request_reader', 'inputs': {'message': 'input.message'}, 'config': settings()},
                    response('reader.reply', 'replied'), prompt('fork', 'reader.text'), prompt('p1', 'reader.text'), prompt('p2', 'reader.text'),
                    join(), response('p1.text')],
                   [('input', 'reader'), ('reader', 'replied', 'reply'), ('reader', 'fork', 'continue'), ('fork', 'p1'), ('fork', 'p2'),
                    ('p1', 'join'), ('p2', 'join'), ('join', 'out')], contract=2)
    assert errors(raw) == []
    id, task = await start(store, worker, raw, 'I need 2 chair at 10 each'); await task
    result = store.run(id)
    assert result['status'] == 'success', result.get('error')
    assert json.loads(result['output'])['item'] == 'chair'
    assert len(events(store, id, 'join', 'success')) == 1
