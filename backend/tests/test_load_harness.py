"""The load harness (scripts/load) measures honestly and cannot touch the user's data.

Spec: docs/specs/load-baseline-harness.md, tests H01-H07. H08 (a real smoke cell) is not
here: it starts processes and belongs to a slow job.
"""
import json

import pytest

from backend.app.compiler import validate_workflow
from backend.app.models import Workflow
from scripts.load import measure, report, stack, workflows


@pytest.mark.parametrize('name', sorted(workflows.WORKFLOWS))
def test_the_load_workflows_are_valid(name):
    builder, _ = workflows.WORKFLOWS[name]
    assert validate_workflow(Workflow.model_validate(builder())) == []


def test_w20_has_twenty_nodes_and_one_model_call():
    nodes = workflows.w20()['nodes']
    assert len(nodes) == 20
    assert [n['id'] for n in nodes if n['type'] == 'agent'] == ['agent']
    assert workflows.WORKFLOWS['w20'][1] == 1


def test_percentiles_use_nearest_rank():
    values = list(range(1, 201))
    assert (measure.percentile(values, 50), measure.percentile(values, 95), measure.percentile(values, 99)) == (100, 190, 198)
    assert measure.percentile([], 95) is None


def test_stack_refuses_a_directory_without_the_marker(tmp_path):
    with pytest.raises(RuntimeError, match='isolation marker'):
        stack.check_isolated(tmp_path, f'sqlite:///{tmp_path}/relay.db')


def test_stack_refuses_a_database_not_named_for_load(tmp_path):
    (tmp_path / stack.MARKER).touch()
    with pytest.raises(RuntimeError, match='relay_load_'):
        stack.check_isolated(tmp_path, 'postgresql+psycopg://relay:x@127.0.0.1:55432/relay')
    stack.check_isolated(tmp_path, 'postgresql+psycopg://relay:x@127.0.0.1:55432/relay_load_001')


def test_stack_refuses_a_sqlite_file_outside_the_isolated_directory(tmp_path):
    (tmp_path / stack.MARKER).touch()
    with pytest.raises(RuntimeError, match='inside the isolated'):
        stack.check_isolated(tmp_path, 'sqlite:////tmp/other/relay.db')


def run_row(status='success', claims=1, error=''):
    stamps = ['2026-11-16T00:00:01+00:00', '2026-11-16T00:00:31+00:00'][:claims]
    return {'created': '2026-11-16T00:00:00+00:00', 'status': status, 'finished': '2026-11-16T00:00:33+00:00',
            'error': error, 'claims': stamps}


def test_summary_counts_failures_and_reclaims():
    result = measure.summarize({'a': run_row(), 'b': run_row('failed', error='boom'), 'c': run_row(claims=2)}, 0, 1)
    assert result['completed'] == 2
    assert result['statuses'] == {'failed': 1, 'success': 2}
    assert result['errors'] == {'boom': 1}
    assert result['reclaims'] == 1
    assert result['drain_seconds'] == 32.0  # First claim to last finish.
    assert result['claim_to_finish']['p50'] == 2.0  # From the last claim of the run.


def test_a_missing_counter_is_unmeasured_not_zero(tmp_path):
    cell = {'database': 'sqlite', 'workers': 1, 'workflow': 'w3', 'latency_ms': 0, 'runs': 10, 'repeat': 1,
            'runs_per_min': 100.0, 'drain_seconds': 6.0, 'claim_to_finish': {'p50': 0.1, 'p95': 0.2, 'p99': 0.2},
            'commits_per_run': None, 'rows_updated_per_run': None, 'reclaims': 0, 'flags': []}
    (tmp_path / 'results.json').write_text(json.dumps({
        'commit': 'c', 'source_commit': 's', 'dirty': False, 'command': 'x', 'mode': 'm', 'relay_settings': {},
        'machine': {'platform': 'p', 'cpu_count': 1, 'memory_gb': None, 'python': '3'},
        'database': {'sqlite': '3', 'postgres': None, 'postgres_settings': {'container': 'none'}}, 'cells': [cell]}))
    report.write_summary(tmp_path)
    row = next(line for line in (tmp_path / 'summary.md').read_text().splitlines() if line.startswith('| sqlite'))
    assert row.count('unmeasured') == 2 and ' 0.0 ' not in row
    assert 'not a capacity claim' in (tmp_path / 'summary.md').read_text()
