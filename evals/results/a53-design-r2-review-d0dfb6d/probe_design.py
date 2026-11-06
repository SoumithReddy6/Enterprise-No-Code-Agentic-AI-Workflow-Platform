"""A5.3 r2 design probes. No Relay parallel implementation or source edit.

The mirror policy is a literal model of the proposal. Actions and persistence use
the real policy and Store. Raw LangGraph checks isolate the needed topology rule.
"""
import asyncio
import json
import logging
import operator
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.accounting_state import AccountingDurability
from backend.app.execution_policy import ExecutionIdentity, execute_with_policy
from backend.app.models import RetryPolicy
from backend.tests.test_accounting_durability import claimed


async def mirror_case(directory, sequence_control):
    directory.mkdir()
    store, id, owner = claimed(directory)
    durability = AccountingDurability(True)
    state = {'action_budget': {'remaining': 10, 'counter': 0, 'ceiling': 10},
             'usage': {}, 'usage_by_invocation': {}, 'durability': durability}
    mirror, published, publication_sequence = None, [], 0
    async def persist(snapshot):
        nonlocal mirror, publication_sequence
        generation = durability.generation
        publication_sequence += 1
        published.append({'model_generation': generation, 'sequence': publication_sequence,
                          'counter': snapshot['action_budget']['counter']})
        version = publication_sequence if sequence_control else generation
        if mirror is None or version > mirror[0]:
            mirror = (version, deepcopy(snapshot))
        assert store.reserve_accounting(id, owner, mirror[1])
        return True
    async def emit(_event):
        pass
    async def read():
        return {'text': 'read result'}
    state['persist_accounting'] = persist
    try:
        for name in ('branch-a', 'branch-b'):
            result = await execute_with_policy(read, ExecutionIdentity(name, name, name),
                RetryPolicy(), state, emit, action_budget=state['action_budget'], charge_first_attempt=True)
            assert result.status == 'success'
        saved = store.run(id)['accounting']['action_budget']
        assert state['action_budget']['counter'] == 2
        assert saved['counter'] == (2 if sequence_control else 1)
        return {'control': sequence_control, 'publications': published,
                'runtime_counter': 2, 'stored_counter': saved['counter'],
                'stored_remaining': saved['remaining'], 'model_generation': durability.generation}
    finally:
        store.engine.dispose()


async def commit_barrier_case():
    lock = asyncio.Lock()
    entered, published, release, committed = (asyncio.Event() for _ in range(4))
    order = []
    async def writer_a():
        async with lock:
            entered.set()
            await release.wait()
            order.append('A')
    async def writer_b():
        published.set()
        async with lock:
            order.append('B')
            committed.set()
    a = asyncio.create_task(writer_a())
    await entered.wait()
    b = asyncio.create_task(writer_b())
    try:
        await published.wait()
        before = committed.is_set()
        assert before is False
    finally:
        release.set()
        await asyncio.gather(a, b)
    assert order == ['A', 'B']
    return {'B_published_while_A_held': True, 'B_committed_while_A_held': before,
            'commit_order': order}


class State(TypedDict):
    take_fork: bool
    trace: Annotated[list[str], operator.add]


async def side_entry_case(take_fork):
    builder = StateGraph(State)
    for name in ('choose', 'fork', 'a', 'b', 'join', 'out'):
        builder.add_node(name, lambda state, name=name: {'trace': [name]})
    builder.add_edge(START, 'choose')
    builder.add_conditional_edges('choose', lambda state: 'fork' if state['take_fork'] else 'a')
    builder.add_edge('fork', 'a')
    builder.add_edge('fork', 'b')
    builder.add_edge(['a', 'b'], 'join')
    builder.add_edge('join', 'out')
    builder.add_edge('out', END)
    result = await builder.compile().ainvoke({'take_fork': take_fork, 'trace': []})
    assert ('out' in result['trace']) is take_fork
    return {'take_fork': take_fork, 'trace': result['trace'], 'join_ran': 'join' in result['trace'],
            'answer_ran': 'out' in result['trace'], 'graph_returned': True}


async def main():
    logging.getLogger('relay.journal').setLevel(logging.CRITICAL)
    with tempfile.TemporaryDirectory(prefix='a53-r2-review-') as directory:
        root = Path(directory)
        mirror = await mirror_case(root / 'strict-generation', False)
        control = await mirror_case(root / 'publication-sequence', True)
    barrier = await asyncio.wait_for(commit_barrier_case(), 5)
    topology = [await asyncio.wait_for(side_entry_case(flag), 5) for flag in (True, False)]
    result = {'literal_model_generation_policy': mirror, 'publication_sequence_control': control,
              'valid_commit_barrier': barrier, 'raw_langgraph_side_entry': topology}
    Path(__file__).with_name('probe-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
