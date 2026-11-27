"""Make backend/tests/fixtures/validation_answers_88d4d8b.json on the parent commit 88d4d8b.

Run it from the repository root with the parent commit's backend/app and the new test module
(the tests were written before the fix):

  RECORD_OUT=<scratch>/recorded.json PYTHONPATH=.:evals/results/def03-2026-11-27/fixture \\
    .venv/bin/python -m pytest -p record_plugin backend/tests/test_request_contract_validation.py backend/tests/test_switch.py -q
  PYTHONPATH=. .venv/bin/python evals/results/def03-2026-11-27/fixture/make_fixture.py <scratch>/recorded.json

Three sources:
1. Every workflow that test_request_contract_validation.py and test_switch.py validate
   (record_plugin.py). Each one is replayed here in its environment (plain, or under the
   future_nodes declarations); a recording whose replay gives other errors is dropped,
   because it was made under some other patch. The fixture keeps the replay's answers.
2. Every workflow in examples/.
3. 600 random graphs of 1 to 7 nodes (seed 3), with binding cycles, attachment cycles,
   missing nodes and the node kinds the walks treat differently. For these the fixture keeps
   each walk's answer for each reference, without a memo (the parent has none).
"""
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.models import Workflow
from backend.tests import test_request_contract_validation as contract_tests
from backend.tests.test_validation_cost import FIXTURE, graph_answers, workflow_answers

ROOT = Path(__file__).resolve().parents[4]


def canonical(raw):
    return json.dumps(raw, sort_keys=True)


def recorded_entries(path):
    entries, seen, dropped = [], set(), 0
    for i, record in enumerate(json.loads(Path(path).read_text())):
        key = record['env'] + canonical(record['workflow'])
        if key in seen:continue
        seen.add(key)
        with pytest.MonkeyPatch.context() as mp:
            if record['env'] == 'future_nodes':contract_tests.future_nodes.__wrapped__(mp)
            answers = workflow_answers(record['workflow'])
        want = {'value': record['errors']} if 'errors' in record else {'raised': record['raised']}
        if answers['errors'] != want:dropped += 1;continue
        entries.append({'source': f'recorded/{i}', 'env': record['env'], 'workflow': record['workflow'], 'answers': answers})
    return entries, dropped


def example_entries():
    entries, skipped = [], []
    for path in sorted((ROOT / 'examples').rglob('*.json')):
        raw = json.loads(path.read_text())
        try:Workflow.model_validate(raw)
        except Exception:skipped.append(str(path.relative_to(ROOT)));continue
        entries.append({'source': str(path.relative_to(ROOT)), 'env': 'plain', 'workflow': raw, 'answers': workflow_answers(raw)})
    return entries, skipped


KINDS = [
    ('chat_input', {}), ('prompt', {'template': '{a}'}), ('response', {}), ('condition', {}),
    ('calculate', {'expression': 'amount * 2'}), ('calculate', {'expression': '600 * 2'}),
    ('calculate', {'expression': 'coalesce(a, 1)'}), ('calculate', {'expression': 'a > 1 or b', 'mode': 'check'}),
    ('calculate', {'expression': '(('}),
    ('agent', {}), ('agent', {'role': 'extraction'}), ('agent', {'output_schema': {'type': 'object'}}),
    ('llm', {}), ('query', {}), ('request_reader', {}), ('request_router', {}), ('verify_extraction', {}),
    ('tool_jira', {'operation': 'search'}), ('tool_jira', {'operation': 'create_issue', 'enable_writes': True}),
    ('tool_github', {}), ('tool_http', {'method': 'GET'}), ('retrieve', {}),
    ('for_each', {'body': '?'}), ('for_each', {}),
]
PORTS = ['text', 'message', 'summary', 'results', 'context', 'query', 'reply', 'reading', 'grounding', 'sources', 'items']
INPUTS = ['a', 'b', 'query', 'value', 'input', 'message']


def random_graph(rng):
    ids = [f'n{i}' for i in range(rng.randint(1, 7))]
    targets = ids + ['ghost']
    nodes = []
    for id in ids:
        kind, config = rng.choice(KINDS)
        config = dict(config)
        if config.get('body') == '?':config['body'] = rng.choice(targets)
        inputs = {name: f'{rng.choice(targets)}.{rng.choice(PORTS)}' for name in rng.sample(INPUTS, rng.randint(0, 3))}
        if rng.random() < .05:inputs['bare'] = rng.choice(ids)  # a reference without a port
        nodes.append({'id': id, 'type': kind, 'inputs': inputs, 'config': config})
    deps = {id: rng.sample(targets, rng.randint(0, 2)) for id in ids if rng.random() < .4}
    refs = sorted({r for n in nodes for r in n['inputs'].values()} | {f'{id}.text' for id in ids} | {'ghost.text'})
    return {'nodes': nodes, 'deps': deps, 'refs': refs, 'cyclic': cyclic(nodes, deps)}


def cyclic(nodes, deps):
    edges = {n['id']: {r.partition('.')[0] for r in n['inputs'].values()} | set(deps.get(n['id'], [])) for n in nodes}
    state = {}
    def visit(id):
        if state.get(id) == 1:return True
        if state.get(id) == 2 or id not in edges:return False
        state[id] = 1
        found = any(visit(child) for child in edges[id])
        state[id] = 2
        return found
    return any(visit(id) for id in edges)


def main(recorded):
    commit = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    changed = subprocess.run(['git', 'diff', '--name-only', 'HEAD', '--', 'backend/app'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    if commit != '88d4d8b' or changed:raise SystemExit(f'run on 88d4d8b with an unchanged backend/app (HEAD {commit}, changed {changed})')
    workflows, dropped = recorded_entries(recorded)
    examples, skipped = example_entries()
    rng = random.Random(3)
    graphs = []
    for _ in range(600):
        graph = random_graph(rng)
        graph['answers'] = graph_answers(graph, shared=False)
        graphs.append(graph)
    FIXTURE.write_text(json.dumps({'commit': commit, 'workflows': workflows + examples, 'graphs': graphs}, sort_keys=True) + '\n')
    print(json.dumps({'recorded_kept': len(workflows), 'recorded_dropped': dropped, 'examples': len(examples), 'examples_skipped': skipped,
                      'plain': sum(e['env'] == 'plain' for e in workflows + examples), 'future_nodes': sum(e['env'] == 'future_nodes' for e in workflows),
                      'graphs': len(graphs), 'cyclic_graphs': sum(g['cyclic'] for g in graphs)}, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
