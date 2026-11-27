"""DEF-03 timing: validate_workflow (and the /api/validate warnings) on 100-node workflows, best
of 5, and the walk call counts at n = 40 and 80. Run from the repository root."""
import json, time
from backend.app.models import Workflow
from backend.app.compiler import validate_workflow, type_warnings
from backend.app.provenance import decision_warnings
from backend.tests.test_validation_cost import plain_chain, switch_chain

def best(raw):
    workflow, times = Workflow.model_validate(raw), []
    for _ in range(5):
        t = time.perf_counter(); validate_workflow(workflow); type_warnings(workflow); decision_warnings(workflow); times.append(time.perf_counter() - t)
    return len(raw['nodes']), round(min(times), 4), round(max(times), 4)

rows = {'plain_chain(98)': best(plain_chain(98)), 'switch_chain(92, input.message)': best(switch_chain(92, 'input.message')),
        'switch_chain(92, reader.text)': best(switch_chain(92, 'reader.text'))}
print(json.dumps({k: {'nodes': n, 'best_s': b, 'worst_s': w} for k, (n, b, w) in rows.items()}, indent=1))
