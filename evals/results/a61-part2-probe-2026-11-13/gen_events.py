"""Record the events of a real two-iteration Repeat run (rejected, then accepted) and its workflow."""
import asyncio, json, sys
import pytest
from backend.tests import test_repeat as t
mp = pytest.MonkeyPatch()
t.Script(mp, writer=[t.final('draft one'), t.final('draft two')], judge=[t.final(t.verdict(False)), t.final(t.verdict(True, 'ok'))])
raw = t.standard()
values, exc, events = asyncio.run(t.run_mem(raw))
assert exc is None, exc
for i, e in enumerate(events): e.setdefault('seq', i + 1); e.setdefault('timestamp', '2026-11-13T00:00:%02dZ' % i)
json.dump({'workflow': raw, 'events': events, 'summary': values['r']['summary']}, open(sys.argv[1], 'w'), indent=1)
print(len(events), [(e.get('node_id'), e.get('status'), e.get('kind'), e.get('item_index')) for e in events]); print(values['r']['summary'])
