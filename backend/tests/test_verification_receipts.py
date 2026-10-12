import copy
import importlib
import json

import pytest

from backend.app.request_contracts import FieldResult, VerificationReport


def receipts():
    return importlib.import_module('backend.app.verification_receipts')


def report(status='passed'):
    return VerificationReport(verdict='passed' if status == 'passed' else 'needs_review', complete=True,
        fields=[FieldResult(field='/amount', status=status, reason='source_checked' if status == 'passed' else 'origin_unconfirmed')])


def record(review=None, result=None):
    return receipts().make_receipt('run1', 'verify', 'original', '{"amount":0}', 'a'*64, result or report(), review)


def matches(saved, **changes):
    return receipts().receipt_matches(saved, **{'run_id':'run1','original':'original','value':'{"amount":0}',
        'policy_digest':'a'*64, **changes})


def test_pass_receipt_binds_exact_input_value_policy_and_run():
    value = record();assert receipts().checked_receipt(value) == value and matches(value)
    assert value['v'] == 1 and value['node_id'] == 'verify'
    assert receipts().VERIFICATION_KEY == '_verification'


@pytest.mark.parametrize('changes', [{'run_id':'run2'}, {'original':'original '}, {'value':'{"amount":1}'},
    {'value':'{ "amount":0}'}, {'policy_digest':'b'*64}])
def test_changed_values_contexts_and_another_run_invalidate_receipt(changes):
    assert not matches(record(), **changes)


def test_human_confirmation_only_authorizes_displayed_interpretation():
    pending = record(result=report('unverified'))
    assert not matches(pending)
    confirmed = record(review='approval123', result=report('unverified'))
    assert matches(confirmed) and confirmed['report']['fields'][0]['status'] == 'unverified'
    assert not matches(confirmed, value='{"amount":999}')
    assert not matches(confirmed, run_id='other')


def test_clarification_cannot_be_turned_into_confirmation():
    invalid = VerificationReport(verdict='clarify', complete=True,
        fields=[FieldResult(field='/amount', status='absent', reason='required_absent')])
    with pytest.raises(ValueError):record(review='approval123', result=invalid)


@pytest.mark.parametrize('damage', ['extra', 'version', 'digest', 'fields', 'duplicate', 'review_type', 'run_type', 'report_type', 'report_digest'])
def test_malformed_restore_never_becomes_a_grant(damage):
    value = copy.deepcopy(record())
    if damage == 'extra':value['_other'] = {}
    elif damage == 'version':value['v'] = True
    elif damage == 'digest':value['value_digest'] = []
    elif damage == 'fields':value['report']['fields'][0]['field'] = 'amount'
    elif damage == 'duplicate':value['report']['fields'] *= 2
    elif damage == 'review_type':value['review_id'] = {}
    elif damage == 'run_type':value['run_id'] = []
    elif damage == 'report_type':value['report'] = []
    else:value['report']['fields'][0]['reason'] = 'changed'
    with pytest.raises(ValueError):receipts().checked_receipt(value)
    assert not matches(value)


def test_overflow_is_an_incomplete_non_grant_and_never_truncates_to_pass(monkeypatch):
    module = receipts();monkeypatch.setattr(module, 'MAX_RECEIPT_BYTES', 1024)
    result = VerificationReport(verdict='passed', complete=True, fields=[
        FieldResult(field='/'+str(i), status='passed', reason='x'*150) for i in range(10)])
    value = module.make_receipt('run1', 'verify', 'original',
        json.dumps({str(i): i for i in range(10)}), 'a'*64, result)
    assert len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()) <= 1024
    assert value['report']['verdict'] == 'needs_review' and not value['report']['complete']
    assert not matches(value) and any(f['reason'] == 'receipt_limit' for f in value['report']['fields'])


@pytest.mark.parametrize('args', [('','verify','original','value','a'*64),
    ('run','_verify','original','value','a'*64),('run','verify','original','value','not-a-hash')])
def test_constructor_requires_bounded_well_formed_scope(args):
    with pytest.raises(ValueError):receipts().make_receipt(*args, report())


def test_consumer_checks_the_expected_verification_node():
    assert matches(record(), node_id='verify')
    assert not matches(record(), node_id='different')


def test_empty_passed_report_is_invalid_in_contract_constructor_and_restore():
    with pytest.raises(ValueError):VerificationReport(verdict='passed', complete=True, fields=[])
    mutated = report();mutated.fields.clear()
    with pytest.raises(ValueError):record(result=mutated)
    saved = record();saved['report']['fields'] = []
    saved['report_digest'] = receipts().digest(json.dumps(saved['report'], sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    with pytest.raises(ValueError):receipts().checked_receipt(saved)
    assert not matches(saved)


def test_receipt_constructor_refuses_partial_or_unrelated_passed_coverage():
    for text in ['{"amount":0,"unseen":999}', '{"other":0}', 'not JSON']:
        with pytest.raises(ValueError):receipts().make_receipt('run1','verify','original',text,'a'*64,report())
