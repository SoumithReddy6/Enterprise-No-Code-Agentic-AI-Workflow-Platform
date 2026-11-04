"""Independent, deterministic audit of phases 1-3; never changes real run history.

Model responses and the tool transport are deliberately controlled. These probes test
what the product permits, not what an unmodified live LLM naturally generates.
The durable probe uses a temporary SQLite database. No external requests are sent.
"""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.app.calculate import run as calculate
from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.models import Workflow
from backend.app import provenance as p

MODEL = {'provider': 'ollama', 'model': 'llama3.1:latest'}
STRUCTURED = {**MODEL, 'role': 'extraction', 'output_schema': {'type': 'object'}}
ROWS = []


def record(case, reproduced, observed, evidence_kind='controlled compiler execution'):
    ROWS.append({'case': case, 'defect_reproduced': bool(reproduced),
                 'evidence_kind': evidence_kind, 'observed': observed})


def flow(middle, decide=None, field='amount', operator='gt'):
    nodes = [{'id': 'input', 'type': 'chat_input'}, *middle]
    if decide:
        config = ({'operator': 'gt', 'compare_to': '1000', 'field': field} if operator == 'gt'
                  else {'operator': operator, 'field': field})
        nodes += [{'id': 'check', 'type': 'condition', 'inputs': {'value': decide}, 'config': config}]
        names = [n['id'] for n in nodes]
        edges = [{'id': f'e{i}', 'source': a, 'target': b} for i, (a, b) in enumerate(zip(names, names[1:]))]
        nodes += [{'id': 'yes', 'type': 'response', 'inputs': {'text': 'input.message'}},
                  {'id': 'no', 'type': 'response', 'inputs': {'text': 'input.message'}}]
        edges += [{'id': 'true', 'source': 'check', 'target': 'yes', 'sourceHandle': 'true'},
                  {'id': 'false', 'source': 'check', 'target': 'no', 'sourceHandle': 'false'}]
    else:
        nodes += [{'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}]
        names = [n['id'] for n in nodes]
        edges = [{'id': f'e{i}', 'source': a, 'target': b} for i, (a, b) in enumerate(zip(names, names[1:]))]
    wf = Workflow.model_validate({'name': 'Independent phase audit', 'nodes': nodes, 'edges': edges})
    assert not validate_workflow(wf), validate_workflow(wf)
    return wf


def agent(name='extract', source='input.message', config=None):
    return {'id': name, 'type': 'agent', 'inputs': {'input': source}, 'config': config or STRUCTURED}


async def execute(wf, message, answers=(), platform=None, completed=None):
    replies, events, calls = iter(answers), [], []
    async def model(*args, **kwargs):
        calls.append(True)
        kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
        return next(replies)
    async def emit(event):
        events.append(event)
    with patch('backend.app.providers.ollama_chat', model):
        values = (await compile_workflow(wf, lambda _: '', emit, message,
                  platform_resolver=platform, completed=completed).graph.ainvoke({'values': {}}))['values']
    decisions = {e['node_id']: e['decision'] for e in events if 'decision' in e}
    return values, events, decisions, len(calls)


async def main():
    baseline = flow([agent()], 'extract.text')
    _, _, decisions, _ = await execute(baseline, 'Hello!', ['{"amount":0}'])
    assert decisions['check']['would_review'] is True, 'baseline must detect the invented zero'

    # Ordinary prompt formatting adds an example, which is incorrectly searched as data.
    prompt = {'id': 'prompt', 'type': 'prompt', 'inputs': {'message': 'input.message'},
              'config': {'template': 'Example: {{"amount":0}}\nRequest: {message}'}}
    values, _, decisions, _ = await execute(flow([prompt, agent(source='prompt.text')], 'extract.text'),
                                           'Hello!', ['{"amount":0}'])
    record('R01_prompt_example_laundering', decisions['check']['label'] == 'quoted'
           and not decisions['check']['would_review'],
           {'prompt': values['prompt']['text'], 'decision': decisions['check']})

    # The mocked transport performs the same echo as PythonConfig's default print(input_text).
    async def echo(action, node_type, settings, text, identity):
        assert action == 'tool' and node_type == 'tool_python'
        return text + '\n'
    wf = flow([agent('draft', config=MODEL),
               {'id': 'echo', 'type': 'tool_python', 'inputs': {'input': 'draft.text'}, 'config': {}},
               agent(source='echo.text')], 'extract.text')
    values, _, decisions, _ = await execute(wf, 'Hello!', ['0', '{"amount":0}'], echo)
    record('R02_tool_echo_laundering', values['draft']['_provenance']['ports']['text'] == 'guessed'
           and values['echo']['_provenance']['ports']['text'] == 'source'
           and decisions['check']['label'] == 'quoted' and not decisions['check']['would_review'],
           {'labels': {n: values[n]['_provenance'] for n in ('draft', 'echo', 'extract')},
            'decision': decisions['check']}, 'controlled compiler and echo transport')

    # A literal dotted key overwrites the label for a different nested JSON value.
    values, _, decisions, _ = await execute(flow([agent()], 'extract.text', 'order.amount'),
        'Order monitors for $1200.', ['{"order":{"amount":0},"order.amount":1200}'])
    record('R03_field_path_collision', decisions['check']['label'] == 'quoted'
           and not decisions['check']['would_review'] and values['check']['branch'] == 'false',
           {'answer': values['extract']['text'], 'labels': values['extract']['_provenance'],
            'decision': decisions['check'], 'branch': values['check']['branch']})

    # The existing exact JSON parser gives 1e3 the value 1000, but the quote grammar finds 1.
    values, _, decisions, _ = await execute(baseline, '{"amount":1e3}', ['{"amount":1}'])
    record('R04_partial_number_token', decisions['check']['label'] == 'quoted'
           and not decisions['check']['would_review'],
           {'input': '{"amount":1e3}', 'extracted': values['extract']['text'], 'decision': decisions['check']})

    # Valid 31-digit addition and exact zero subtraction fail due to an UNUSED product.
    number = int('9' * 30)
    for expression in ('a + b', 'a - a'):
        try:
            actual = str(calculate(expression, json.dumps({'a': number, 'b': number}), 'compute')[0])
            error = None
        except ValueError as exc:
            actual, error = None, str(exc)
        expected = str(number + number) if '+' in expression else '0'
        record('R05_unused_operation_' + expression, error is not None,
               {'expression': expression, 'expected': expected, 'actual': actual, 'error': error}, 'real arithmetic evaluator')
    for expression, expected in (('(1 / 3) - (1 / 3)', '0'), ('round((1 / 3) * 3, 2)', '1.00')):
        try:
            actual, error = str(calculate(expression, '{}', 'compute')[0]), None
        except ValueError as exc:
            actual, error = None, str(exc)
        record('R05_fraction_intermediate_' + expression, error is not None,
               {'expression': expression, 'expected': expected, 'actual': actual, 'error': error},
               'real arithmetic evaluator; ordinary fractional intermediates')

    # Unary negation and abs silently round a valid 61-digit input.
    number = int('1234567890' * 6 + '1')
    for expression, original, expected in (('-a', number, -number), ('abs(a)', -number, number)):
        actual = calculate(expression, json.dumps({'a': original}), 'compute')[0]
        record('R06_silent_rounding_' + expression, actual != expected,
               {'expression': expression, 'expected': str(expected), 'actual': str(actual)}, 'real arithmetic evaluator')
    rounded = int(calculate('abs(a)', json.dumps({'a': -number}), 'compute')[0])
    wf = flow([{'id': 'calc', 'type': 'calculate', 'inputs': {'value': 'input.message'},
                'config': {'expression': 'abs(a) == b', 'mode': 'check'}}])
    values, _, decisions, _ = await execute(wf, json.dumps({'a': -number, 'b': rounded}))
    record('R06_wrong_check_trusted', rounded != number and decisions['calc']['passed'] is True
           and not decisions['calc']['would_review'],
           {'mathematically_equal': rounded == number, 'result': values['calc']['text'], 'decision': decisions['calc']})

    # Existing 4 KB limit makes shadow labeling terminate a valid, paid node.
    from backend.app.storage import Store
    from backend.app.worker import Worker
    from cryptography.fernet import Fernet
    answer = json.dumps({f'field{i}' + 'x' * 110: i for i in range(64)})
    with tempfile.TemporaryDirectory(prefix='relay-provenance-audit-') as temp:
        store = Store('sqlite:///' + str(Path(temp) / 'audit.db'), Fernet.generate_key())
        try:
            store.allow_model('ollama', 'llama3.1:latest')
            created = store.create_run(flow([agent()]).model_dump(mode='json'), 'Hello!')
            async def model(*args, **kwargs):
                kwargs['usage'].update(prompt_tokens=1, completion_tokens=1)
                return answer
            with patch('backend.app.providers.ollama_chat', model):
                worker = Worker(store)
                await worker.execute(store.claim_next(worker.owner))
            saved = store.run(created['id'])
            record('R07_shadow_size_failure', saved['status'] == 'failed'
                   and 'provenance record exceeds' in saved['error'],
                   {'status': saved['status'], 'error': saved['error'], 'answer_bytes': len(answer.encode()),
                    'success_nodes': [e.get('node_id') for e in saved['events'] if e.get('status') == 'success']},
                   'real worker with temporary SQLite and controlled provider')
        finally:
            store.engine.dispose()

    # A statically unreachable guess should not be called "always guessed".
    wf = flow([agent('draft', config=MODEL),
               {'id': 'calc', 'type': 'calculate', 'inputs': {'value': 'draft.text'},
                'config': {'expression': 'coalesce(1200, amount)'}}], 'calc.text', 'result')
    warnings = p.decision_warnings(wf)
    _, _, decisions, _ = await execute(wf, 'Hello!', ['{"amount":0}'])
    record('R08_static_runtime_disagreement', bool(warnings)
           and decisions['check']['label'] == 'calculated' and not decisions['check']['would_review'],
           {'warnings': warnings, 'decision': decisions['check']})

    # Cached success events lose the decision shown by the latest-event inspector.
    values, _, decisions, _ = await execute(baseline, 'Hello!', ['{"amount":0}'])
    _, events, restored, calls = await execute(baseline, 'Hello!', completed=values)
    cached = next(e for e in events if e.get('node_id') == 'check')
    record('R09_cached_decision_lost', decisions['check']['would_review'] and 'check' not in restored,
           {'original_decision': decisions['check'], 'cached_event': cached, 'model_calls_on_replay': calls})

    # Traversal overflow must not turn a present unknown value into honest absence.
    deep = 0
    for _ in range(40):
        deep = [deep]
    values, _, decisions, _ = await execute(flow([agent()], 'extract.text', 'items', 'empty'),
                                          'Hello!', [json.dumps({'items': deep})])
    record('R10_depth_overflow_is_absent', decisions['check']['label'] == 'absent'
           and not decisions['check']['would_review'] and values['check']['branch'] == 'false',
           {'nonempty_array_depth': 40, 'labels': values['extract']['_provenance'],
            'decision': decisions['check'], 'branch': values['check']['branch']})

    # Attached bodies skip the compiler decorator altogether; quoted per-item labels disappear.
    wf = Workflow.model_validate({'name': 'Quoted loop', 'nodes': [
        {'id': 'input', 'type': 'chat_input'},
        {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
         'config': {'connection_id': 'audit', 'operation': 'search'}},
        {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'work'}},
        {'id': 'work', 'type': 'agent', 'config': STRUCTURED},
        {'id': 'out', 'type': 'response', 'inputs': {'text': 'input.message'}}],
        'edges': [{'id': 'a', 'source': 'input', 'target': 'tickets'},
                  {'id': 'b', 'source': 'tickets', 'target': 'each'},
                  {'id': 'c', 'source': 'each', 'target': 'out'},
                  {'id': 'body', 'source': 'each', 'target': 'work', 'kind': 'loop',
                   'sourceHandle': 'body', 'targetHandle': 'input'}]})
    async def jira(action, node_type, settings, text, identity):
        return {'text': '{"issues":[]}', 'items': [{'amount': 1200}, {'amount': 1350}],
                'truncated': False, 'warnings': []}
    values, events, _, _ = await execute(wf, 'go', ['{"amount":1200}', '{"amount":1350}'], jira)
    bodies = [e for e in events if e.get('node_id') == 'work' and e.get('status') == 'success']
    record('R11_attached_labels_missing', len(bodies) == 2
           and all('_provenance' not in e['outputs'] for e in bodies)
           and values['each']['_provenance']['ports']['results'] == 'guessed',
           {'body_successes': len(bodies), 'labelled_bodies': sum('_provenance' in e['outputs'] for e in bodies),
            'loop_label': values['each']['_provenance'], 'item_rows': values['each']['results']})

    # The grounding port contains the model's answer, not solely platform-generated facts.
    wf = flow([agent('draft', config=MODEL), agent(source='draft.grounding')], 'extract.text')
    envelope = json.dumps({'question': 'What is the cost?', 'passages': [
        {'citation': 'S1', 'filename': 'catalogue.txt', 'page': 1,
         'text': 'The catalogue lists laptops but contains no prices.'}]})
    first = json.dumps({'answer': 'The amount is $0 [S1]', 'citations': ['S1'],
                        'abstain': False, 'reason': ''})
    values, _, decisions, _ = await execute(wf, envelope, [first, '{"amount":0}'])
    record('R12_grounding_answer_laundering', values['draft']['_provenance']['ports']['text'] == 'guessed'
           and values['draft']['_provenance']['ports']['grounding'] == 'source'
           and decisions['check']['label'] == 'quoted' and not decisions['check']['would_review'],
           {'draft_provenance': values['draft']['_provenance'], 'grounding': values['draft']['grounding'],
            'extract_provenance': values['extract']['_provenance'], 'decision': decisions['check']})

    result = {'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'scope': __doc__, 'cases': ROWS, 'reproduced': sum(r['defect_reproduced'] for r in ROWS)}
    (Path(__file__).parent / 'probe-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'cases': len(ROWS), 'reproduced': result['reproduced'],
                      'results': [{k: r[k] for k in ('case', 'defect_reproduced')} for r in ROWS]}, indent=2))
    return 0 if all(r['defect_reproduced'] for r in ROWS) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
