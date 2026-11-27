"""DEF-03 residual: a doubled chain whose first Prompt also binds the last one (a binding
cycle), into a Switch. Each walk is timed alone on the Switch's value. Run from the repository root."""
import json, sys, time
from backend.app.compiler import checked_dependency, unchecked_model_input, validate_workflow
from backend.app.models import Node, Workflow
from backend.app.provenance import static_label
from backend.tests.test_validation_cost import switch_chain

out = {}
for n in map(int, sys.argv[1:]):
    raw = switch_chain(n, 'input.message')
    first = next(x for x in raw['nodes'] if x['id'] == 'p0'); first['inputs']['x'] = f'p{n-1}.text'
    nodes = {x['id']: Node.model_validate(x) for x in raw['nodes']}
    row = {}
    for name, call in [('static_label', lambda: static_label(nodes, f'p{n-1}.text')),
                       ('checked_dependency', lambda: checked_dependency(f'p{n-1}.text', nodes)),
                       ('unchecked_model_input', lambda: unchecked_model_input(f'p{n-1}.text', nodes, {}, stop_at_sources=True)),
                       ('validate_workflow', lambda: validate_workflow(Workflow.model_validate(raw)))]:
        t = time.perf_counter(); call(); row[name] = round(time.perf_counter() - t, 4)
    out[n] = row
    print(n, row, flush=True)
