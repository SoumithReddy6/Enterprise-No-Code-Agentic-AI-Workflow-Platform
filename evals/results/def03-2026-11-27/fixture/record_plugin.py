"""pytest plugin: record each workflow that a test passes to validate_workflow (DEF-03 fixture).

Load it with `-p record_plugin` and RECORD_OUT=<file>. It wraps compiler.validate_workflow
before the test modules import it. A call is recorded only when request_contract_errors is
the real function (some Switch tests replace it). `env` is 'future_nodes' when the
test_request_contract_validation fixture of that name replaced the guard declarations.
make_fixture.py replays every recording on the parent commit and keeps only the ones whose
replay gives the recorded errors, so a recording made under another patch drops out.
"""
import json
import os

from backend.app import compiler
from backend.app.registry import REGISTRY

ORIGINAL, CONTRACT = compiler.validate_workflow, compiler.request_contract_errors
RECORDS = []


def env():
    handler = getattr(REGISTRY.get('verify_extraction'), 'handler', None)
    return 'future_nodes' if getattr(handler, '__name__', '') == 'forbidden' else 'plain'


def recording(workflow):
    try:
        errors = ORIGINAL(workflow)
    except Exception as exc:  # The tests that expect a ValueError compare its text.
        if compiler.request_contract_errors is CONTRACT:
            RECORDS.append({'env': env(), 'workflow': workflow.model_dump(mode='json'), 'raised': f'{type(exc).__name__}: {exc}'})
        raise
    if compiler.request_contract_errors is CONTRACT:
        RECORDS.append({'env': env(), 'workflow': workflow.model_dump(mode='json'), 'errors': errors})
    return errors


compiler.validate_workflow = recording


def pytest_sessionfinish(session):
    with open(os.environ['RECORD_OUT'], 'w') as f:
        json.dump(RECORDS, f)
