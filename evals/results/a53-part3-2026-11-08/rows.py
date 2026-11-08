"""Run only the A5.3 fork rows (S22-S24) in one Lab, from the current directory's tree."""
import json, sys, tempfile, time
from pathlib import Path
from scripts.check_production_scenarios import Lab
from scripts.scenarios.parallel_branches import parallel_scenarios
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); rows = []
with tempfile.TemporaryDirectory(prefix='relay-a53p3-rows-') as temp:
    lab = Lab(temp, out)
    try:
        lab.boot()
        for id, phase, name, fn in parallel_scenarios(lab):
            t = time.monotonic()
            try: row = {'id': id, 'status': 'passed', 'details': fn()}
            except Exception as exc: row = {'id': id, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:900]}
            row['seconds'] = round(time.monotonic() - t, 1); rows.append(row); print(json.dumps(row), flush=True)
        (out / 'runs.json').write_text(json.dumps([lab.client.get('/api/runs/' + r['id']).json() for r in lab.client.get('/api/runs').json()], indent=1))
    finally: lab.close()
(out / 'rows.json').write_text(json.dumps(rows, indent=2))
