"""TA06: a model call whose usage is missing is still counted, and its totals are marked incomplete.

Each call that reaches a provider gets one status: reported, partial (one count missing),
unreported (a response with no usable counts) or unanswered (dispatched, no response).
A call that fails before dispatch, or whose connection never opened, is not counted.
Known tokens still add, and nothing is estimated.

Store and Worker are real. Only HTTP is synthetic, through httpx.MockTransport on
providers.client, as test_providers.py does.
"""
import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from backend.app import providers, schema
from backend.app.operator_metrics import RunTokenRecord
from backend.app.operator_metrics import metrics as operator_metrics
from backend.app.storage import Store
from backend.app.worker import Worker

STATUS = ('calls', 'partial_calls', 'unreported_calls', 'unanswered_calls')
MODELS = {'ollama': 'fixture', 'claude': 'claude-fixture', 'openai': 'gpt-fixture'}


def answer(provider, prompt=None, completion=None, text='hello', usage='omit'):
    """A provider body. Ollama counts are top-level fields; Claude and OpenAI use `usage`."""
    if provider == 'ollama':
        body = {'message': {'content': text}}
        if prompt is not None: body['prompt_eval_count'] = prompt
        if completion is not None: body['eval_count'] = completion
        return body
    names = ('input_tokens', 'output_tokens') if provider == 'claude' else ('prompt_tokens', 'completion_tokens')
    body = ({'content': [{'type': 'text', 'text': text}]} if provider == 'claude'
            else {'choices': [{'message': {'content': text}}]})
    if usage != 'omit': body['usage'] = usage
    elif prompt is not None or completion is not None:
        body['usage'] = {k: v for k, v in zip(names, (prompt, completion)) if v is not None}
    return body


def http(monkeypatch, replies):
    """Each transport call takes the next reply: a body (200), an httpx.Response, or an exception."""
    seen, queue = [], list(replies)
    async def handle(request):
        seen.append(request.url.path)
        if not queue: raise AssertionError('unexpected HTTP request')
        reply = queue.pop(0)
        if isinstance(reply, type) and issubclass(reply, BaseException): reply = reply('synthetic', request=request) if issubclass(reply, httpx.RequestError) else reply()
        if isinstance(reply, BaseException): raise reply
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)
    transport = httpx.MockTransport(handle)
    monkeypatch.setattr(providers, 'client', lambda timeout: httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False))
    monkeypatch.setattr(providers, 'MAX_RETRY_DELAY', 0)
    return seen


def durable(tmp_path, provider='ollama'):
    store = Store(f'sqlite:///{tmp_path}/ta06.db', Fernet.generate_key())
    credential = ''
    if provider != 'ollama': credential = store.credential('fixture', 'fixture-secret', provider=provider)['id']
    store.allow_model(provider, MODELS[provider], credential)
    return store, Worker(store), credential


def flow(provider='ollama', credential='', count=1):
    """input -> m1 .. mN -> out, every model node on the same provider."""
    config = {'provider': provider, 'model': MODELS[provider], **({'credential_id': credential} if credential else {})}
    nodes = [{'id': 'input', 'type': 'chat_input'}]
    edges, source = [], 'input.message'
    for i in range(1, count + 1):
        nodes.append({'id': f'm{i}', 'type': 'llm', 'inputs': {'prompt': source}, 'config': dict(config)})
        edges.append({'id': f'e{i}', 'source': nodes[-2]['id'], 'target': f'm{i}'}); source = f'm{i}.text'
    nodes.append({'id': 'out', 'type': 'response', 'inputs': {'text': source}})
    edges.append({'id': 'out', 'source': f'm{count}', 'target': 'out'})
    return {'version': 1, 'name': 'TA06', 'nodes': nodes, 'edges': edges}


async def run(store, worker, document):
    row = store.create_run(document, 'go')
    await asyncio.wait_for(worker.execute(store.claim_next(worker.owner)), 30)
    return store.run(row['id'])


def counted(store, id):
    """Summed metric rows. A column the schema lacks reads as 0, so the baseline fails on values."""
    with Session(store.engine) as s:
        rows = list(s.scalars(select(RunTokenRecord).where(RunTokenRecord.run_id == id)))
    total = {field: sum(getattr(r, field, 0) or 0 for r in rows) for field in STATUS}
    total['tokens'] = (sum(r.prompt_tokens for r in rows), sum(r.completion_tokens for r in rows))
    return total


def expect(calls=0, partial=0, unreported=0, unanswered=0, tokens=(0, 0)):
    return {'calls': calls, 'partial_calls': partial, 'unreported_calls': unreported,
            'unanswered_calls': unanswered, 'tokens': tokens}


def outcome(run_record, node='m1'):
    found = [e for e in run_record['events'] if e.get('node_id') == node and e.get('status') in ('success', 'failed', 'cancelled')]
    assert found, f'{node} has no outcome'
    return found[-1]


def status_of(event):
    usage = event.get('usage') or {}
    return {field: usage.get(field, 0) for field in STATUS}


# --------------------------------------------------------------------------- V01: original reproduction

@pytest.mark.asyncio
async def test_v01_three_responses_without_usage_are_counted(tmp_path, monkeypatch):
    """The finding: three valid responses with no counts, one-token ceiling. Soft ceiling unchanged."""
    monkeypatch.setenv('RELAY_RUN_TOKEN_LIMIT', '1')
    seen = http(monkeypatch, [answer('ollama')] * 3)
    store, worker, _ = durable(tmp_path)
    try:
        record = await run(store, worker, flow(count=3))
        assert record['status'] == 'success' and len(seen) == 3
        assert counted(store, record['id']) == expect(calls=3, unreported=3)
        rows = [r for r in operator_metrics(store)['tokens'] if r['model'] == 'fixture']
        assert len(rows) == 1 and rows[0]['calls'] == 3 and rows[0].get('unreported_calls') == 3
        assert record['accounting']['tokens_spent'] == 0, 'unknown usage is not estimated'
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- V02: one count or none

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['ollama-partial', 'claude-partial', 'openai-null'])
async def test_v02_missing_counts_are_marked_not_zeroed(tmp_path, monkeypatch, case):
    provider = case.split('-')[0]
    body = {'ollama-partial': answer('ollama', completion=7),
            'claude-partial': answer('claude', completion=7),
            'openai-null': answer('openai', usage=None)}[case]
    http(monkeypatch, [body])
    store, worker, credential = durable(tmp_path, provider)
    try:
        record = await run(store, worker, flow(provider, credential))
        assert record['status'] == 'success'
        wanted = expect(calls=1, unreported=1) if case == 'openai-null' else expect(calls=1, partial=1, tokens=(0, 7))
        assert counted(store, record['id']) == wanted
        assert status_of(outcome(record)) == {k: wanted[k] for k in STATUS}
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- V03: read timeouts

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['exhausted', 'then-zero'])
async def test_v03_read_timeouts(tmp_path, monkeypatch, case):
    """Exhausted: dispatched, never answered. then-zero: one call (not one per HTTP attempt), and zero is reported."""
    replies = [httpx.ReadTimeout] * 3 if case == 'exhausted' else [httpx.ReadTimeout, answer('ollama', 0, 0)]
    seen = http(monkeypatch, replies)
    store, worker, _ = durable(tmp_path)
    try:
        record = await run(store, worker, flow())
        assert len(seen) == len(replies)
        if case == 'exhausted':
            assert record['status'] == 'failed' and 'timed out' in record['error']
            assert counted(store, record['id']) == expect(unanswered=1)
            assert status_of(outcome(record)) == {'calls': 0, 'partial_calls': 0, 'unreported_calls': 0, 'unanswered_calls': 1}
        else:
            assert record['status'] == 'success'
            assert counted(store, record['id']) == expect(calls=1)
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- V04: cancellation after dispatch

@pytest.mark.asyncio
async def test_v04_cancellation_after_dispatch_is_unanswered_on_its_invocation(tmp_path, monkeypatch):
    seen = http(monkeypatch, [asyncio.CancelledError()])
    store, worker, _ = durable(tmp_path)
    try:
        record = await run(store, worker, flow(count=2))
        assert len(seen) == 1
        cancelled = outcome(record, 'm1')
        assert cancelled['status'] == 'cancelled'
        assert status_of(cancelled) == {'calls': 0, 'partial_calls': 0, 'unreported_calls': 0, 'unanswered_calls': 1}
        assert counted(store, record['id']) == expect(unanswered=1)
        assert not [e for e in record['events'] if e.get('node_id') == 'm2']
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- V05: no status

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['invalid-url', 'connect-error', 'connect-timeout'])
async def test_v05_nothing_reached_a_provider(tmp_path, monkeypatch, case):
    """Request preparation fails before dispatch; a connection that never opened delivered nothing."""
    replies = {'invalid-url': [], 'connect-error': [httpx.ConnectError], 'connect-timeout': [httpx.ConnectTimeout] * 3}[case]
    seen = http(monkeypatch, replies)
    if case == 'invalid-url': monkeypatch.setenv('OLLAMA_BASE_URL', 'http://127.0.0.1:abc')
    store, worker, _ = durable(tmp_path)
    try:
        record = await run(store, worker, flow())
        assert record['status'] == 'failed' and len(seen) == len(replies)
        assert counted(store, record['id']) == expect()
        assert 'usage' not in outcome(record)
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- V06: received, counts unusable

@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['claude-usage-list', 'openai-usage-list', 'openai-http-401', 'ollama-invalid-json'])
async def test_v06_received_response_without_usable_counts_is_unreported(tmp_path, monkeypatch, case):
    provider = case.split('-')[0]
    reply = {'claude-usage-list': answer('claude', usage=[]),
             'openai-usage-list': answer('openai', usage=[]),
             'openai-http-401': httpx.Response(401, json={'error': {'message': 'bad key'}}),
             'ollama-invalid-json': httpx.Response(200, content=b'{not json')}[case]
    seen = http(monkeypatch, [reply])
    store, worker, credential = durable(tmp_path, provider)
    try:
        record = await run(store, worker, flow(provider, credential))
        error = {'claude-usage-list': 'invalid usage metadata', 'openai-usage-list': 'invalid usage metadata',
                 'openai-http-401': 'HTTP 401', 'ollama-invalid-json': 'invalid JSON'}[case]
        assert record['status'] == 'failed' and error in record['error'] and len(seen) == 1
        failed = outcome(record)
        assert failed['status'] == 'failed'
        assert status_of(failed) == {'calls': 1, 'partial_calls': 0, 'unreported_calls': 1, 'unanswered_calls': 0}
        assert counted(store, record['id']) == expect(calls=1, unreported=1)
    finally: store.engine.dispose()


# --------------------------------------------------------------------------- V07: migration and metrics

def test_v07_migration_adds_zero_counters_and_metrics_sum_them(tmp_path):
    """Control written for the change: revision 0006 does not exist on the baseline."""
    url = f'sqlite:///{tmp_path}/0006.db'
    engine = create_engine(url)
    new = {'partial_calls', 'unreported_calls', 'unanswered_calls'}
    columns = lambda conn: {c['name'] for c in inspect(conn).get_columns('run_token_usage')}
    at = datetime.now(timezone.utc).isoformat()  # Store startup rebuilds created_at from the run data
    with engine.begin() as conn:
        schema.upgrade(conn, '0005_admission_metrics')
        conn.execute(text("INSERT INTO runs (id,tenant_id,data,created_at,status,name,citation_counter,grounded,abstained,truncated,truncation_source) "
                          "VALUES ('r','local',:data,:at,'success','r',0,0,0,0,'')"), {'at': at, 'data': json.dumps({'created_at': at, 'status': 'success'})})
        conn.execute(text("INSERT INTO run_token_usage VALUES ('r',0,'ollama','fixture',10,5,2)"))
    with engine.begin() as conn:
        schema.upgrade(conn)
        assert new <= columns(conn)
        assert conn.execute(text('SELECT partial_calls, unreported_calls, unanswered_calls FROM run_token_usage')).one() == (0, 0, 0)
        conn.execute(text("INSERT INTO run_token_usage (run_id,seq,provider,model,prompt_tokens,completion_tokens,calls,partial_calls,unreported_calls,unanswered_calls) "
                          "VALUES ('r',1,'ollama','fixture',0,7,2,1,1,1), ('r',2,'claude','c',3,4,1,0,0,2)"))
    engine.dispose()
    store = Store(url, Fernet.generate_key())
    try:
        rows = {r['model']: r for r in operator_metrics(store)['tokens']}
        assert {k: rows['fixture'][k] for k in ('prompt_tokens', 'completion_tokens', 'calls', *new)} == {
            'prompt_tokens': 10, 'completion_tokens': 12, 'calls': 4, 'partial_calls': 1, 'unreported_calls': 1, 'unanswered_calls': 1}
        assert rows['c']['unanswered_calls'] == 2 and rows['c']['calls'] == 1
    finally: store.engine.dispose()
    engine = create_engine(url)
    from alembic import command
    with engine.begin() as conn:
        command.downgrade(schema._config(conn), '0005_admission_metrics')
        assert not new & columns(conn)
        assert conn.execute(text('SELECT count(*), sum(prompt_tokens), sum(calls) FROM run_token_usage')).one() == (3, 13, 5)
    with engine.begin() as conn:
        schema.upgrade(conn)
        assert schema.current_revision(conn) == schema.head_revision() and new <= columns(conn)
    engine.dispose()
