"""S08: a node reached after a failure must not bind the failed node's outputs.

A routed node that fails recovers with no outputs, then follows its error edge. It did run,
so it dominates the error branch, but nothing it declares exists there. Validation tracks
the two guarantees separately: which nodes ran on every path, and whose outputs exist on
every path. Only the second makes a binding valid.
"""
import pytest
from backend.app.compiler import compile_workflow, validate_workflow
from backend.app.tool_service import TransientToolError
from backend.app.models import Workflow
from backend.tests.test_api import client  # noqa: F401  (fixture)
from backend.tests.test_node_error_policy import platform, route_flow, run

UNAVAILABLE = 'unavailable on a possible error path'


def tool(id, source='input.message', **extra):
    return {'id': id, 'type': 'tool_python', 'inputs': {'input': source},
            'config': {'code': 'print(input_text)', 'description': 'Echoes.'}, **extra}


def respond(id, source):
    return {'id': id, 'type': 'response', 'inputs': {'text': source}}


def document(nodes, edges):
    return {'version': 1, 'name': 'Recovery', 'nodes': [{'id': 'input', 'type': 'chat_input'}, *nodes],
            'edges': [{'id': f'e{i}', **edge} for i, edge in enumerate(edges)]}


def workflow(nodes, edges):
    return Workflow.model_validate(document(nodes, edges))


def binding_errors(workflow):
    return [e for e in validate_workflow(workflow) if 'invalid binding' in e]


def error_branch_reads(source):
    """The S08 scenario: work routes, and its error branch reads `source`."""
    return document([tool('work', on_error='route'), respond('ok', 'work.text'), respond('fallback', source)],
                    [{'source': 'input', 'target': 'work'}, {'source': 'work', 'target': 'ok'},
                     {'source': 'work', 'target': 'fallback', 'sourceHandle': 'error'}])


# --------------------------------------------------------------------------- the defect

def test_error_branch_cannot_bind_the_failed_nodes_output():
    errors = binding_errors(Workflow.model_validate(error_branch_reads('work.text')))
    assert len(errors) == 1, errors
    assert errors[0].startswith('fallback: invalid binding text = work.text')
    assert UNAVAILABLE in errors[0]


def test_the_diagnostic_describes_a_possibility_not_a_certainty():
    """Validation cannot know the node will fail, only that a path exists where it did."""
    [error] = binding_errors(Workflow.model_validate(error_branch_reads('work.text')))
    assert 'possible' in error and 'fails' in error
    assert 'failed' not in error


def test_normal_branch_may_bind_the_output():
    assert validate_workflow(Workflow.model_validate(error_branch_reads('input.message'))) == []
    assert 'ok' in {n.id for n in route_flow().nodes} and validate_workflow(route_flow()) == []


def test_error_branch_may_bind_outputs_from_before_the_failure():
    assert binding_errors(route_flow()) == []  # fallback reads input.message


# --------------------------------------------------------------------------- joins

def test_join_after_both_branches_cannot_bind_the_routed_output():
    """Each path may run different nodes; after the join only what every path produced exists."""
    nodes = [tool('work', on_error='route'), tool('shape', 'work.text'), tool('apology'), respond('out', 'work.text')]
    edges = [{'source': 'input', 'target': 'work'},
             {'source': 'work', 'target': 'shape'}, {'source': 'work', 'target': 'apology', 'sourceHandle': 'error'},
             {'source': 'shape', 'target': 'out'}, {'source': 'apology', 'target': 'out'}]
    [error] = binding_errors(workflow(nodes, edges))
    assert error.startswith('out: invalid binding text = work.text') and UNAVAILABLE in error


def test_join_does_not_blame_a_node_that_only_one_branch_ran():
    """`shape` ran on one path only: that is ordinary non-dominance, not an error path."""
    nodes = [tool('work', on_error='route'), tool('shape', 'work.text'), tool('apology'), respond('out', 'shape.text')]
    edges = [{'source': 'input', 'target': 'work'},
             {'source': 'work', 'target': 'shape'}, {'source': 'work', 'target': 'apology', 'sourceHandle': 'error'},
             {'source': 'shape', 'target': 'out'}, {'source': 'apology', 'target': 'out'}]
    [error] = binding_errors(workflow(nodes, edges))
    assert 'guaranteed upstream' in error and UNAVAILABLE not in error


def success_and_error_converge(source, error_first=False):
    """One routed node whose normal and error edges both reach the same consumer."""
    pair = [{'source': 'work', 'target': 'out'}, {'source': 'work', 'target': 'out', 'sourceHandle': 'error'}]
    return workflow([tool('work', on_error='route'), respond('out', source)],
                    [{'source': 'input', 'target': 'work'}, *(pair[::-1] if error_first else pair)])


@pytest.mark.parametrize('error_first', [False, True])
def test_success_and_error_edges_into_one_consumer_stay_distinct(error_first):
    """Collapsing the two edges to their shared parent would re-create the defect. Both
    edge orders are checked, so keeping either the first or the last edge per parent fails."""
    [error] = binding_errors(success_and_error_converge('work.text', error_first))
    assert error.startswith('out: invalid binding text = work.text') and UNAVAILABLE in error
    assert validate_workflow(success_and_error_converge('input.message', error_first)) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('fail_times', [0, 1])
async def test_converging_edges_run_either_way(fail_times):
    resolver, _ = platform(fail_times=fail_times, error=TransientToolError('unreachable'))
    result = await run(success_and_error_converge('input.message'), resolver, [])
    assert result['values']['out']['text'] == 'hello'
    assert result['values']['work'] == ({} if fail_times else {'text': 'ok'})


# --------------------------------------------------------------------------- nesting and other policies

def test_nested_routes_keep_earlier_successes_available():
    """After b's error edge, a's output exists (a succeeded to get there); b's does not."""
    def nested(source):
        return workflow([tool('a', on_error='route'), tool('b', 'a.text', on_error='route'),
                         respond('a_failed', 'input.message'), respond('ok', 'b.text'), respond('b_failed', source)],
                        [{'source': 'input', 'target': 'a'},
                         {'source': 'a', 'target': 'b'}, {'source': 'a', 'target': 'a_failed', 'sourceHandle': 'error'},
                         {'source': 'b', 'target': 'ok'}, {'source': 'b', 'target': 'b_failed', 'sourceHandle': 'error'}])
    assert validate_workflow(nested('a.text')) == []
    [error] = binding_errors(nested('b.text'))
    assert error.startswith('b_failed: invalid binding text = b.text') and UNAVAILABLE in error


def test_inherited_availability_crosses_an_error_edge():
    """available[p] carries over an error edge; only p's own outputs are withheld."""
    nodes = [tool('first'), tool('work', 'first.text', on_error='route'),
             respond('ok', 'work.text'), respond('fallback', 'first.text')]
    edges = [{'source': 'input', 'target': 'first'}, {'source': 'first', 'target': 'work'},
             {'source': 'work', 'target': 'ok'}, {'source': 'work', 'target': 'fallback', 'sourceHandle': 'error'}]
    assert validate_workflow(workflow(nodes, edges)) == []


def test_continue_reader_is_reported_once_with_its_fix():
    """continue recovers with no outputs too; the existing rule already names the fix."""
    errors = validate_workflow(workflow([tool('work', on_error='continue'), respond('out', 'work.text')],
                                        [{'source': 'input', 'target': 'work'}, {'source': 'work', 'target': 'out'}]))
    assert len(errors) == 1 and "'continue' is not allowed" in errors[0] and "use 'route'" in errors[0]


def test_continue_without_readers_still_validates():
    assert validate_workflow(workflow([tool('work', on_error='continue'), respond('out', 'input.message')],
                                      [{'source': 'input', 'target': 'work'}, {'source': 'work', 'target': 'out'}])) == []


def condition_flow(on_error='fail', source='work.text'):
    return workflow([tool('work'), {'id': 'check', 'type': 'condition', 'inputs': {'value': 'work.text'},
                                    'config': {'contains': 'yes'}, 'on_error': on_error},
                     respond('yes', source), respond('no', source)],
                    [{'source': 'input', 'target': 'work'}, {'source': 'work', 'target': 'check'},
                     {'source': 'check', 'target': 'yes', 'sourceHandle': 'true'},
                     {'source': 'check', 'target': 'no', 'sourceHandle': 'false'}])


def test_condition_branches_still_bind_upstream_outputs():
    assert validate_workflow(condition_flow()) == []
    assert validate_workflow(condition_flow(source='check.branch')) == []


@pytest.mark.parametrize('policy', ['continue', 'route'])
def test_a_condition_cannot_recover_without_a_branch(policy):
    """Both validated before, then crashed with KeyError 'branch' when the condition failed."""
    errors = validate_workflow(condition_flow(on_error=policy))
    assert any(f"a condition cannot use on_error '{policy}'" in e for e in errors), errors


# --------------------------------------------------------------------------- every entry point

def test_the_invalid_binding_is_rejected_before_anything_runs(client):  # noqa: F811
    raw = error_branch_reads('work.text')
    validated = client.post('/api/validate', json=raw).json()
    assert validated['valid'] is False and any(UNAVAILABLE in e for e in validated['errors'])

    submitted = client.post('/api/runs', json={'workflow': raw, 'message': 'hello'})
    assert submitted.status_code == 422 and any(UNAVAILABLE in e for e in submitted.json()['detail'])
    # No run row means the worker never received it: no node, tool or model call can occur.
    assert client.get('/api/runs').json() == []

    # Saving keeps drafts editable by design (test_drafts_can_be_saved_but_not_run); the
    # saved document is still refused when submitted.
    saved = client.post('/api/workflows', json=raw)
    assert saved.status_code == 201
    stored = client.get(f"/api/workflows/{saved.json()['id']}").json()['workflow']
    assert client.post('/api/runs', json={'workflow': stored, 'message': 'hello'}).status_code == 422
    assert client.get('/api/runs').json() == []


def test_compiling_directly_does_not_bypass_validation():
    with pytest.raises(ValueError) as exc:
        compile_workflow(Workflow.model_validate(error_branch_reads('work.text')), message='hello')
    assert UNAVAILABLE in str(exc.value)
