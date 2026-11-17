"""DEF-03: workflow validation takes exponential time on a chain with doubled bindings.

compiler.unchecked_model_input (compiler.py:478-502) walks every input of every node with
a per-path `seen` set, so a chain of n nodes that each bind the previous node twice costs
2**n calls. request_contract_errors calls it for each node (compiler.py:450). /api/validate
and /api/runs run this synchronously on the event loop (main.py:112-114,156-157), so one
authenticated request can stop the API process. Measured on 2026-11-16: n=16 0.32 s,
n=18 1.36 s, n=20 5.63 s (x4 per two nodes).
"""
import subprocess
import sys
import textwrap

import pytest

PROBE = textwrap.dedent('''
    from backend.app.models import Workflow
    from backend.app.compiler import validate_workflow
    nodes=[{'id':'input','type':'chat_input'}];edges=[];prev,port='input','message'
    for i in range(40):
        ref=f'{prev}.{port}'
        nodes.append({'id':f'p{i}','type':'prompt','inputs':{'message':ref,'x':ref},'config':{'template':'{message}'}})
        edges.append({'id':f'e{i}','source':prev,'target':f'p{i}'});prev,port=f'p{i}','text'
    nodes.append({'id':'out','type':'response','inputs':{'text':f'{prev}.text'}})
    edges.append({'id':'eo','source':prev,'target':'out'})
    validate_workflow(Workflow.model_validate({'version':1,'contract':2,'name':'chain','nodes':nodes,'edges':edges}))
''')


@pytest.mark.xfail(strict=True, reason='DEF-03')
def test_validation_of_a_42_node_workflow_finishes_in_ten_seconds():
    # 42 nodes is under the 100-node limit (models.py:43). The child is killed at the timeout.
    try:
        subprocess.run([sys.executable, '-c', PROBE], timeout=10, check=True, capture_output=True)
    except subprocess.TimeoutExpired:
        pytest.fail('DEF-03: validate_workflow did not finish in 10 s for a 42-node workflow')
