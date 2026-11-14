"""R3 check for design rev 2: is a Repeat loop_item's item_result still an object after observed()?

Run from backend/: ../.venv/bin/python ../evals/results/a61-part2-probe-2026-11-13/observed_check.py
"""
import json, sys
sys.path.insert(0, '.')
from app.storage import observed
from app.iteration import PREVIEW_CHARS
print('PREVIEW_CHARS', PREVIEW_CHARS)
for n in (300, 1900, 3000):
    for met in (True, False):
        event = {'kind': 'loop_item', 'status': 'info', 'node_id': 'r', 'item_index': 1, 'transient': True,
                 'item_result': {'index': 1, 'status': 'success', 'value': {'text': '"\\' * n},
                                 'verdict': {'pass': met, 'notes': '"\\' * (n // 2)}, 'met': met}}
        out = observed(event)
        result = out.get('item_result')
        print(json.dumps({'draft_chars': 2 * n, 'met': met, 'item_result_type': type(result).__name__,
                          'met_readable': isinstance(result, dict) and isinstance(result.get('met'), bool),
                          'preview_of': out.get('preview_of')}))
