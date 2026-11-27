"""Run only the third (Repeat) lab of the scenario gate from the current directory's code.
Usage: python repeat_lab.py <out dir>. Writes rows.json (every row's result)."""
import json, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from scripts.check_production_scenarios import Lab  # noqa: E402
from scripts.scenarios.repeat_loops import TOKEN_LIMIT, LoopModels, models_for_lab, repeat_scenarios  # noqa: E402

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); rows = []
with tempfile.TemporaryDirectory(prefix='relay-repeat-mutant-') as temp:
    models = LoopModels(); lab = Lab(temp, out)
    lab.env['OLLAMA_BASE_URL'] = models.url; lab.env['RELAY_RUN_TOKEN_LIMIT'] = str(TOKEN_LIMIT)
    try:
        lab.boot()
        for model in models_for_lab():
            assert lab.client.post('/api/models', json={'provider': 'ollama', 'model': model}).status_code == 201
        for id, phase, name, fn in repeat_scenarios(lab, models):
            stamp = time.monotonic()
            try: fn(); row = {'id': id, 'status': 'passed'}
            except Exception as exc: row = {'id': id, 'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'[:600]}
            row['seconds'] = round(time.monotonic() - stamp, 1); rows.append(row); print(id, row['status'], flush=True)
    except Exception as exc:
        rows.append({'id': 'LAB', 'status': 'failed', 'error': repr(exc)[:600]})
    finally:
        lab.close(); models.close()
(out / 'rows.json').write_text(json.dumps(rows, indent=1) + '\n')
