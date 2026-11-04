"""A scenario report must fail closed; failures and skips cannot produce a green release."""
from scripts.check_production_scenarios import gate_passes


def test_gate_rejects_failed_and_unexecuted_scenarios():
    assert not gate_passes([])
    assert not gate_passes([{'status':'passed'},{'status':'failed'}])
    assert not gate_passes([{'status':'passed'},{'status':'skipped'}])
    assert not gate_passes([{'status':'passed'},{}])
    assert gate_passes([{'status':'passed'},{'status':'passed'}])
