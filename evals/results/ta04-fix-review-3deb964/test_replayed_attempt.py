"""Independent red regressions for TA04's approved replay attempt classification."""
import pytest
from probe_edges import approval_resume


@pytest.mark.asyncio
async def test_an_approved_send_counts_as_work_without_a_second_debit():
    row = await approval_resume(expect_defect=False)
    assert row['action_counter'] == 1
    assert row['truncation_reason'].startswith('agent stopped after 1 attempt(s):'), \
        'R02: the approved POST was sent, so this Agent did run'


@pytest.mark.asyncio
async def test_a_loop_records_the_item_whose_approved_write_was_sent():
    row = await approval_resume(loop_mode=True, expect_defect=False)
    assert row['action_counter'] == 2  # One Jira read and one already-reserved write.
    assert row['loop_summary']['processed'] == 1, \
        'R02: an item sent its approved write but the loop reports zero processed items'
    assert row['loop_summary']['failed'] == 1
    assert row['loop_results'][0]['budget_halt'] == 'tokens'
    assert row['not_run_events'] == []
