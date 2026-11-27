"""A6.1 Part 3: a write after a Repeat with on_limit 'last' needs approval: true (V-A cases).

Design: docs/superpowers/specs/2026-11-16-bounded-loops-scenarios-design.md (revision 2,
approved; Decision 1). Under on_limit 'last' a Repeat can succeed with an answer that did not
meet its stop rule, so every write that can run after it, as a flow step or attached to one
(tools, tools behind specialists, For each bodies), must have the literal approval: true. Only
the Repeat's own error route is exempt: there the Repeat failed and returned no answer.
Validation tests assert exact messages.
"""
import threading

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.main import create_app
from backend.tests.test_repeat import document, errors, judge, loop, repeat, tool_edge, writer

UNSET = object()


def approval_needed(write, repeat_id='r'):
    return (f'{write}: a write after {repeat_id} needs approval: true, because {repeat_id} can return an answer '
            'that did not meet its stop rule (on_limit: last).')


def post(id, binding=None, approval=False):
    config = {'connection_id': 'sink', 'method': 'POST', 'enable_writes': True}
    if approval is not UNSET: config['approval'] = approval
    node = {'id': id, 'type': 'tool_http', 'config': config}
    if binding: node['inputs'] = {'input': binding}
    return node


def agent(id, binding):
    return {**writer(id), 'inputs': {'input': binding}}


def out(binding='r.text', id='out'):
    return {'id': id, 'type': 'response', 'inputs': {'text': binding}}


def flow(nodes, edges, on_limit='last', source='input.message', on_error=None):
    """input -> r (writer body, judge checker, the given on_limit) -> the given steps."""
    head = [repeat(source=source, on_error=on_error, on_limit=on_limit, max_iterations=2), writer(), judge()]
    return document([*head, *nodes], [loop('r', 'writer', 'body'), loop('r', 'judge', 'check'), *edges])


def direct(approval=False, on_limit='last'):
    """V-A1: input -> r -> send -> out."""
    return flow([post('send', 'r.text', approval), out()], [('input', 'r'), ('r', 'send'), ('send', 'out')], on_limit)


def attached(approval=False):
    """V-A4: input -> r -> agent -> out, with the POST `post` attached to `agent` as a tool."""
    return flow([agent('agent', 'r.text'), post('post', approval=approval), out('agent.text')],
                [('input', 'r'), ('r', 'agent'), ('agent', 'out'), tool_edge('post', 'agent')])


def specialist(approval=False):
    """V-A5: input -> r -> lead -> out; specialist `helper` of `lead` has the POST `post`."""
    return flow([agent('lead', 'r.text'), writer('helper'), post('post', approval=approval), out('lead.text')],
                [('input', 'r'), ('r', 'lead'), ('lead', 'out'),
                 {'source': 'lead', 'target': 'helper', 'kind': 'agent', 'sourceHandle': 'agents', 'targetHandle': 'input'},
                 tool_edge('post', 'helper')])


def batch(approval=False):
    """V-A6: input -> tickets (Jira read) -> r -> each (For each over tickets.items, body POST) -> out.
    The list comes from the Jira read: Repeat.text is a string, which For each refuses."""
    tickets = {'id': 'tickets', 'type': 'tool_jira', 'inputs': {'input': 'input.message'},
               'config': {'connection_id': 'c1', 'operation': 'search', 'jql': 'project = A'}}
    each = {'id': 'each', 'type': 'for_each', 'inputs': {'items': 'tickets.items'}, 'config': {'body': 'post', 'max_items': 10}}
    return flow([tickets, each, post('post', approval=approval), out()],
                [('input', 'tickets'), ('tickets', 'r'), ('r', 'each'), ('each', 'out'), loop('each', 'post', 'body')],
                source='tickets.text')


# --------------------------------------------------------------------------- refused

def test_va1_a_direct_write_with_approval_false_is_refused():
    assert errors(direct()) == [approval_needed('send')]


def test_va2_a_direct_write_with_approval_unset_is_refused():
    # Unset means "on for authenticated writes", decided per run; validation has no run.
    assert errors(direct(approval=UNSET)) == [approval_needed('send')]


def test_va3_a_write_two_steps_later_is_refused():
    raw = flow([agent('agent', 'r.text'), post('send', 'agent.text'), out('send.text')],
               [('input', 'r'), ('r', 'agent'), ('agent', 'send'), ('send', 'out')])
    assert errors(raw) == [approval_needed('send')]


def test_va4_a_write_attached_to_a_later_agent_is_refused_and_named():
    assert errors(attached()) == [approval_needed('post')]


def test_va5_a_write_behind_a_specialist_of_a_later_agent_is_refused_and_named():
    assert errors(specialist()) == [approval_needed('post')]


def test_va6_a_write_in_a_later_for_each_body_is_refused_and_named():
    assert errors(batch()) == [approval_needed('post')]


def test_va12_a_write_on_a_later_steps_error_route_is_refused():
    # Only the Repeat's own error edge is exempt: a later step's error handler can bind r.text.
    raw = flow([{**agent('agent', 'r.text'), 'on_error': 'route'}, post('send', 'r.text'), out('agent.text'), out('send.text', 'fallback')],
               [('input', 'r'), ('r', 'agent'), ('agent', 'out'), ('agent', 'send', 'error'), ('send', 'fallback')])
    assert errors(raw) == [approval_needed('send')]


# --------------------------------------------------------------------------- accepted

def test_va7_the_same_writes_with_approval_true_are_valid():
    for build in (direct, attached, specialist, batch):
        assert errors(build(approval=True)) == [], build.__doc__


def test_va8_on_limit_fail_needs_no_approval():
    assert errors(direct(on_limit='fail')) == []


def test_va9_a_write_before_the_repeat_needs_no_approval():
    raw = flow([post('send', 'input.message'), out()], [('input', 'send'), ('send', 'r'), ('r', 'out')], source='send.text')
    assert errors(raw) == []


def test_va10_a_write_only_on_the_repeats_own_error_route_needs_no_approval():
    # There the Repeat failed and returned no answer.
    raw = flow([out(), post('send', 'input.message'), out('send.text', 'fallback')],
               [('input', 'r'), ('r', 'out'), ('r', 'send', 'error'), ('send', 'fallback')], on_error='route')
    assert errors(raw) == []


def test_va13_an_imported_draft_with_a_cycle_after_the_repeat_terminates():
    # Validation also runs on imported drafts; the walk keeps a visited set.
    raw = flow([agent('a', 'r.text'), agent('b', 'a.text'), post('send', 'b.text'), out('b.text')],
               [('input', 'r'), ('r', 'a'), ('a', 'b'), ('b', 'a'), ('b', 'send'), ('send', 'out')])
    found = []
    worker = threading.Thread(target=lambda: found.append(errors(raw)), daemon=True)
    worker.start(); worker.join(10)
    assert found, 'validation did not finish'
    assert approval_needed('send') in found[0] and 'Workflow contains a cycle; use a Repeat node for a bounded loop.' in found[0]


# --------------------------------------------------------------------------- submission

def test_va11_submission_is_refused_with_the_same_message_and_creates_no_run(tmp_path):
    app = create_app(f'sqlite:///{tmp_path}/va11.db', Fernet.generate_key(), auth_enabled=False, embedded_worker=False)
    with TestClient(app) as client:
        # The API adds workspace checks (no model or connection is configured here), so the
        # rule's message is asserted to be present, and absent once the write has approval.
        validated = client.post('/api/validate', json=direct()).json()
        assert validated['valid'] is False and approval_needed('send') in validated['errors'], validated
        r = client.post('/api/runs', json={'workflow': direct(), 'message': 'Customer case'})
        assert r.status_code == 422 and approval_needed('send') in r.text, r.text
        assert client.get('/api/runs').json() == []
        approved = client.post('/api/validate', json=direct(approval=True)).json()
        assert approval_needed('send') not in approved['errors'], approved
