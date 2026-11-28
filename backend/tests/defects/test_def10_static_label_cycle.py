"""DEF-10: static_label takes exponential time when the bindings form a cycle.

Found while fixing DEF-03. provenance.static_label keeps the path of references it is on and
calls a reference met again on that path guessed. That answer depends on how the walk came
there, so the DEF-03 memo keeps only answers that met no higher reference on the path. When the
first Prompt of a doubled chain also binds the last one, every answer meets the root and none is
kept: the walk is 2**n again. validate_workflow reports the binding as invalid, but the Switch
rule (compiler.switch_input_allowed) runs static_label first. Measured on 2026-11-27 after the
DEF-03 fix: n=14 0.020 s, n=16 0.081 s, n=18 0.331 s (x4 for each two nodes). The routes now
validate in a thread, so the event loop answers, but one request holds a thread and the CPU.
"""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROBE = textwrap.dedent('''
    from backend.app.models import Workflow
    from backend.app.compiler import validate_workflow
    from backend.tests.test_validation_cost import switch_chain
    raw = switch_chain(40, 'input.message')
    next(n for n in raw['nodes'] if n['id'] == 'p0')['inputs']['x'] = 'p39.text'
    validate_workflow(Workflow.model_validate(raw))
''')


@pytest.mark.xfail(strict=True, reason='DEF-10')
def test_validation_of_a_doubled_chain_with_a_binding_cycle_finishes_in_ten_seconds():
    try:
        subprocess.run([sys.executable, '-c', PROBE], timeout=10, check=True, capture_output=True, cwd=ROOT)
    except subprocess.TimeoutExpired:
        pytest.fail('DEF-10: validate_workflow did not finish in 10 s for a 40-Prompt chain with a binding cycle')
