"""Contract-2 editor examples through the real compiler, worker and read adapter.

Model responses and remote records are explicit fixtures; this tests enforcement,
not a claim about the model's interpretation accuracy.
"""
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet

from backend.app.compiler import validate_workflow
from backend.app.models import Workflow
from backend.app.registry import REGISTRY
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.app import approvals

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / 'examples/request-admission'
RECORDS = json.loads((EXAMPLES / 'reference-fixtures.json').read_text())


def load(name):
    workflow = Workflow.model_validate_json((EXAMPLES / (name + '.json')).read_text())
    for node in workflow.nodes:
        if node.type in ('agent', 'request_router', 'request_reader'):
            node.config.update(provider='ollama', model='fixture')
    return workflow


def test_browser_registry_fixture_matches_the_actual_definitions():
    fixture = json.loads((ROOT / 'frontend/tests/browser/request-nodes.json').read_text())
    assert fixture == json.loads(json.dumps([definition.public() for definition in REGISTRY.values()]))


def test_exported_examples_match_the_editor_builders():
    script = "import {createRequestWorkflow,requestTemplates} from './frontend/lib/request-routing.ts'; console.log(JSON.stringify(Object.fromEntries(requestTemplates.map(t=>[t.id,createRequestWorkflow(t.id)]))));"
    result = subprocess.run(['node', '--experimental-strip-types', '-e', script], cwd=ROOT, capture_output=True, text=True, check=True)
    for name, workflow in json.loads(result.stdout).items():
        assert json.loads((EXAMPLES / (name + '.json')).read_text()) == workflow


@pytest.mark.parametrize('name', ['qa', 'purchase', 'support', 'refund', 'yesno'])
def test_every_template_validates_with_an_authorized_model(name):
    assert validate_workflow(load(name)) == []


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    store = Store(f'sqlite:///{tmp_path}/examples.db', Fernet.generate_key())
    store.allow_model('ollama', 'fixture')
    worker = Worker(store)
    connection = worker.tools.save_connection({'name': 'Explicit example records', 'provider': 'jira', 'endpoint': 'https://records.example'})['id']
    answers, calls, reads = [], [], []
    async def chat(model, system, prompt, usage=None, **kwargs):
        calls.append(prompt)
        usage.update(prompt_tokens=5, completion_tokens=2)
        return answers.pop(0)
    monkeypatch.setattr('backend.app.providers.ollama_chat', chat)
    monkeypatch.setattr('backend.app.tool_service.public_addresses', lambda *args: ['93.184.216.34'])
    records = {}
    def read(request):
        reads.append(request)
        assert request.method == 'GET', 'example must never send an external write'
        return httpx.Response(200, json=records)
    worker.tools.transport = httpx.MockTransport(read)
    yield store, worker, connection, answers, calls, reads, records
    store.engine.dispose()


def model_reply(value):
    # Agents without attached tools return their answer directly; action/final
    # envelopes are only the tool-planning protocol.
    return value


FIELDS = {'qa': ['question'], 'purchase': ['item', 'quantity', 'unit_price', 'total'], 'support': ['summary', 'category'],
          'refund': ['order_id', 'amount'], 'yesno': ['record_id', 'eligible']}


async def run(runtime, name, original, values=None, type='request', reply='', answer=None):
    """values: the reader's suggested values (missing fields are null); answer: the QA agent's reply."""
    store, worker, connection, answers, calls, reads, records = runtime
    workflow = load(name)
    for node in workflow.nodes:
        if node.id == 'records': node.config['connection_id'] = connection
    if original.casefold() not in ('hello', 'hello!'):
        answers.append(json.dumps({'type': type, 'values': {f: (values or {}).get(f) for f in FIELDS[name]}, 'reply': reply}))
        if answer is not None: answers.append(model_reply(answer))
    saved = store.create_run(workflow.model_dump(mode='json'), original)
    await worker.execute(store.claim_next(worker.owner))
    return store.run(saved['id'])


@pytest.mark.asyncio
async def test_an_unrelated_message_gets_the_model_reply_without_a_business_decision(runtime):
    runtime[6].update(RECORDS['purchase'])
    result = await run(runtime, 'purchase', 'Unrelated input', type='unrelated', reply='Please use another workflow.')
    assert result['status'] == 'success', result['error']
    assert len(runtime[4]) == 1 and result['output'] == 'Please use another workflow.'
    assert 'decision' not in result['checkpoints']


@pytest.mark.asyncio
async def test_prepared_whole_message_reply_makes_no_model_call(runtime):
    runtime[6].update(RECORDS['purchase'])
    result = await run(runtime, 'purchase', 'Hello!')
    assert result['status'] == 'success', result['error']
    # The record read runs before the reader, so it supplies reference data; no model call.
    assert not runtime[4] and len(runtime[5]) == 1
    assert result['output'].startswith('Hello!')


@pytest.mark.asyncio
@pytest.mark.parametrize('original,values,total,high', [
    ('{"item":"laptops","quantity":3,"unit_price":450}', {'item': 'laptops', 'quantity': 3, 'unit_price': 450}, 1350, True),
    ('3 laptops at $450 each.', {'item': 'laptops', 'quantity': 3, 'unit_price': 450}, 1350, True),
    ('laptops, $24 in total', {'item': 'laptops', 'total': 24}, 24, False),
])
async def test_purchase_with_a_matching_record_and_quoted_facts_calculates_in_code(runtime, original, values, total, high):
    runtime[6].update(RECORDS['purchase'])
    result = await run(runtime, 'purchase', original, values)
    assert result['status'] == 'success', result['error']
    assert json.loads(result['checkpoints']['calculate']['text']) == {'result': total}
    assert result['checkpoints']['decision']['branch'] == ('true' if high else 'false')
    assert len(runtime[4]) == 1 and len(runtime[5]) == 1
    assert not result.get('approvals')


@pytest.mark.asyncio
async def test_empty_reference_records_require_review_instead_of_fabricating_catalogue_data(runtime):
    runtime[6].update(RECORDS['unavailable'])
    result = await run(runtime, 'purchase', '3 laptops at $450 each.', {'item': 'laptops', 'quantity': 3, 'unit_price': 450})
    assert result['status'] == 'awaiting_approval', result['error']
    assert result['approvals'][0]['kind'] == 'verification'
    assert 'decision' not in result['checkpoints']
    assert not result.get('write_nodes')


@pytest.mark.asyncio
@pytest.mark.parametrize('type', ['request', 'unrelated'])
async def test_screenshot_input_with_invented_fields_cannot_select_the_low_value_branch(runtime, type):
    runtime[6].update(RECORDS['purchase'])
    result = await run(runtime, 'purchase', 'How do AI workflows work?', {'item': 'laptops', 'quantity': 1, 'unit_price': 0}, type=type)
    assert result['status'] == 'awaiting_approval', result['error']
    assert 'decision' not in result['checkpoints']
    review = result['approvals'][0]
    approvals.decide(runtime[0], result['id'], 'local', review['id'], review['digest'], 'reject')
    assert runtime[0].run(result['id'])['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_missing_required_fact_returns_clarification_without_a_business_decision(runtime):
    runtime[6].update(RECORDS['unavailable'])
    result = await run(runtime, 'purchase', 'Buy some laptops', {'item': 'laptops'})
    assert result['status'] == 'success', result['error']
    assert result['output'] == 'This request needs more details: total or quantity and unit price. Send it again with these details.'
    assert 'decision' not in result['checkpoints']


@pytest.mark.asyncio
async def test_yes_no_prose_requires_review_even_if_both_boolean_values_exist_in_other_records(runtime):
    runtime[6].update(RECORDS['yesno'])
    result = await run(runtime, 'yesno', 'Is record MEMBER-1 eligible?', {'record_id': 'MEMBER-1', 'eligible': False})
    assert result['status'] == 'awaiting_approval', result['error']
    assert 'decision' not in result['checkpoints']


@pytest.mark.asyncio
@pytest.mark.parametrize('category,branch', [('billing','true'),('technical','false')])
async def test_support_draft_routes_exact_source_fields_without_creating_a_ticket(runtime, category, branch):
    value = {'summary':'Invoice download is broken','category':category}
    result = await run(runtime, 'support', json.dumps(value), value)
    assert result['status'] == 'success', result['error']
    assert result['checkpoints']['decision']['branch'] == branch
    assert not runtime[5] and len(runtime[4]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('amount,branch', [(24,'true'),(101,'false')])
async def test_refund_draft_checks_order_membership_but_never_claims_payment_authorization(runtime, amount, branch):
    runtime[6].update(RECORDS['refund'])
    value = {'order_id':'ORDER-1','amount':amount}
    result = await run(runtime, 'refund', json.dumps(value), value)
    assert result['status'] == 'success', result['error']
    assert result['checkpoints']['decision']['branch'] == branch
    assert 'no refund has been issued' in result['output'].lower()
    assert len(runtime[5]) == 1 and len(runtime[4]) == 1


@pytest.mark.asyncio
async def test_qa_template_accepts_narrative_answers_without_business_authority(runtime):
    result = await run(runtime, 'qa', 'How do AI workflows work?', {'question': 'How do AI workflows work?'},
                       answer='A workflow connects steps into a process.')
    assert result['status'] == 'success', result['error']
    assert result['output'] == 'A workflow connects steps into a process.'
    assert not runtime[5] and len(runtime[4]) == 2


@pytest.mark.asyncio
async def test_qa_template_replies_to_a_message_with_no_question(runtime):
    result = await run(runtime, 'qa', 'ok', type='unclear', reply='What would you like to know?')
    assert result['status'] == 'success', result['error']
    # No values and no "unrelated" reading: code asks for the required fact, not the model.
    assert result['output'] == 'This request needs more details: question. Send it again with these details.'
    assert len(runtime[4]) == 1


def test_the_purchase_demo_needs_no_connection_and_keeps_every_value_check():
    demo = json.loads((EXAMPLES / 'purchase-demo.json').read_text())
    template = json.loads((EXAMPLES / 'purchase.json').read_text())
    assert validate_workflow(Workflow.model_validate(demo)) == []
    assert not any(n['type'].startswith('tool_') for n in demo['nodes'])
    reader = next(n for n in demo['nodes'] if n['type'] == 'request_reader')['config']
    expected = next(n for n in template['nodes'] if n['type'] == 'request_reader')['config']
    # Only the record membership rule, the purpose and the model differ from the template.
    assert reader['field_rules'] == [r for r in expected['field_rules'] if r['kind'] != 'reference_member']
    for key in ('expected_schema', 'required_fields', 'required_any', 'cross_checks', 'prepared_replies'):
        assert reader[key] == expected[key], key


@pytest.mark.asyncio
@pytest.mark.parametrize('message,values,status,output', [
    ('3 laptops at $450 each.', {'item': 'laptops', 'quantity': 3, 'unit_price': 450}, 'success', 'Checked purchase exceeds $1,000'),
    ('2 chairs at $300 each.', {'item': 'chairs', 'quantity': 2, 'unit_price': 300}, 'success', 'Checked purchase is $1,000 or less'),
    ('Buy some laptops.', {'item': 'laptops'}, 'success', 'This request needs more details: total or quantity and unit price.'),
    ('3 laptops at $450, total $1,000.', {'item': 'laptops', 'quantity': 3, 'unit_price': 450, 'total': 1000}, 'awaiting_approval', ''),
    ('Hello', None, 'success', 'Hello! Send your request'),
])
async def test_the_purchase_demo_runs_each_branch_without_connections(runtime, message, values, status, output):
    store, worker, _, answers, calls, reads, _ = runtime
    workflow = json.loads((EXAMPLES / 'purchase-demo.json').read_text())
    next(n for n in workflow['nodes'] if n['type'] == 'request_reader')['config']['model'] = 'fixture'
    if values is not None:
        answers.append(json.dumps({'type': 'request', 'values': {f: values.get(f) for f in FIELDS['purchase']}, 'reply': ''}))
    saved = store.create_run(workflow, message)
    await worker.execute(store.claim_next(worker.owner))
    result = store.run(saved['id'])
    assert result['status'] == status, result['error']
    assert result['output'].startswith(output) and not reads and len(calls) == (values is not None)
