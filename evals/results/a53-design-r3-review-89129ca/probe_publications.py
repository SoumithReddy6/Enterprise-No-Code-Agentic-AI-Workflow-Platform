"""Design-level schedule checks, not an implementation of Relay parallelism."""
import asyncio
import json
from copy import deepcopy
from pathlib import Path

from backend.app.accounting_state import AccountingDurability


async def schedule(*, caller_snapshot=False, acknowledge_current=False):
    durability = AccountingDurability(True)
    lock = asyncio.Lock()
    publication_sequence = 0
    newest = None
    entered = []
    commits = []
    requested = {name: asyncio.Event() for name in ('A', 'B', 'C')}
    a_entered, allow_commit, a_committed, allow_unlock = (asyncio.Event() for _ in range(4))
    writers = []

    def publish(actions):
        nonlocal publication_sequence, newest
        publication_sequence += 1
        newest = (publication_sequence, durability.generation, deepcopy({'actions': actions}))
        return newest

    async def writer(name, publication):
        requested[name].set()
        async with lock:
            entered.append(name)
            frozen = publication if caller_snapshot else newest
            if name == 'A':
                a_entered.set()
                await allow_commit.wait()
            commits.append({'writer': name, 'sequence': frozen[0], 'actions': frozen[2]['actions']})
            durability.acknowledge(durability.generation if acknowledge_current else frozen[1])
            if name == 'A':
                a_committed.set()
                await allow_unlock.wait()

    async def waiter(name, publication):
        task = asyncio.create_task(writer(name, publication))
        writers.append(task)
        return await asyncio.shield(task)

    durability.changed()
    a = asyncio.create_task(waiter('A', publish(1)))
    await a_entered.wait()
    b = asyncio.create_task(waiter('B', publish(2)))
    await requested['B'].wait()
    durability.changed()
    c = asyncio.create_task(waiter('C', publish(3)))
    await requested['C'].wait()
    assert entered == ['A'], entered
    c.cancel()
    try:
        await c
    except asyncio.CancelledError:
        pass
    assert entered == ['A'], entered
    allow_commit.set()
    await a_committed.wait()
    pending_after_a = durability.pending
    allow_unlock.set()
    await asyncio.gather(a, b, *writers)
    b_commit = next(row for row in commits if row['writer'] == 'B')
    return {'commits': commits, 'pending_after_a': pending_after_a,
            'b_commits_latest_publication': b_commit['sequence'] == 3,
            'cancelled_waiter_did_not_cancel_writer': entered == ['A', 'B', 'C']}


async def main():
    correct = await schedule()
    stale_caller = await schedule(caller_snapshot=True)
    wrong_ack = await schedule(acknowledge_current=True)
    assert correct['pending_after_a']
    assert correct['b_commits_latest_publication']
    assert correct['cancelled_waiter_did_not_cancel_writer']
    assert not stale_caller['b_commits_latest_publication']
    assert not wrong_ack['pending_after_a']
    result = {'scope': 'Publication/acknowledgement design simulation, using the actual AccountingDurability class; no database, Worker implementation, or provider call.',
              'revision_3_schedule': correct, 'caller_snapshot_negative_control': stale_caller,
              'current_generation_ack_negative_control': wrong_ack}
    Path(__file__).with_name('publication-results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    asyncio.run(asyncio.wait_for(main(), 10))
