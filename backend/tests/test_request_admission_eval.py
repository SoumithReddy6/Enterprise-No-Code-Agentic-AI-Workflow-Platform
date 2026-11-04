"""Release evidence cannot pass by omitting failures or trusting summary counters."""
import copy
import json
import hashlib
from pathlib import Path

import pytest

from scripts import eval_request_admission as evaluation
from scripts.check_request_admission_eval import check
from scripts.check_request_admission_eval import controls_pass


def artifact():
    suite, labels = evaluation.load_suite()
    rows = []
    for case in suite['cases']:
        route = case['expected']
        rows.append({'id': case['id'], 'run_id': case['id'], 'status': 'success',
            'route': route, 'cache_outcome': 'local_fallback' if route == 'prepared_reply' else 'not_common',
            'model_calls': 0 if route == 'prepared_reply' else 1, 'tokens': 0 if route == 'prepared_reply' else 10,
            'seconds': .1, 'automatic_branch': None, 'manual': False, 'deliveries': [],
            'extracted': None, 'verification': None, 'error': None})
    proof=evaluation.provenance(suite, labels, 'fixture', 'a' * 64)
    proof['completed_at']=proof['started_at']
    return {'version': 1, 'profile': 'live', 'rows': rows,
        'summary': evaluation.summarize(rows, suite, labels),
        'provenance':proof}


def test_complete_control_passes_without_business_authority_from_admission():
    assert check(artifact())['accepted']


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown', 'missing_provenance',
    'changed_cases', 'changed_source', 'unmeasured_calls', 'negative_calls', 'nonfinite_duration', 'bad_status'])
def test_unusable_or_incomplete_measurements_are_refused(mutation):
    report = artifact()
    if mutation == 'missing': report['rows'].pop()
    if mutation == 'duplicate': report['rows'][-1] = copy.deepcopy(report['rows'][0])
    if mutation == 'unknown': report['rows'][0]['id'] = 'unlabelled'
    if mutation == 'missing_provenance': report.pop('provenance')
    if mutation == 'changed_cases': report['provenance']['suite_sha256'] = '0' * 64
    if mutation == 'changed_source': report['provenance']['source_files_changed'] = ['backend/app/compiler.py']
    if mutation == 'unmeasured_calls': report['rows'][0].pop('model_calls')
    if mutation == 'negative_calls': report['rows'][0]['model_calls'] = -1
    if mutation == 'nonfinite_duration': report['rows'][0]['seconds'] = float('nan')
    if mutation == 'bad_status': report['rows'][0]['status'] = 'unknown'
    with pytest.raises(ValueError): check(report)


def test_gate_derives_admission_rate_from_all_cases_not_a_claimed_summary():
    report = artifact()
    for row in report['rows'][:3]: row['route'] = 'clarify'
    # Fabricating a perfect summary must not hide 17/20 valid requests admitted.
    assert not check(report)['checks']['valid_request_proceed_rate']


def test_one_wrong_answer_on_an_automatic_business_branch_fails_the_gate():
    report = artifact()
    row = report['rows'][0]
    row.update(automatic_branch='false', extracted='{"item":"laptops","quantity":3,"unit_price":450}')
    result = check(report)
    assert not result['accepted'] and not result['checks']['zero_unsafe_automatic_actions']


def test_delivery_without_a_supported_business_decision_is_unsafe():
    report = artifact()
    report['rows'][-1]['deliveries'] = [{'method': 'POST', 'path': '/deliver', 'body': '{}'}]
    assert not check(report)['checks']['zero_unsafe_automatic_actions']


def test_manual_confirmation_is_separate_from_automatic_safety():
    report = artifact()
    case=next(c for c in evaluation.load_suite()[0]['cases'] if c['id']==report['rows'][0]['id'])
    payload={'kind':'verification','node_id':'reader','original':case['message'],'value':'{"invented":0}'}
    raw=json.dumps(payload)
    report['rows'][0].update(manual=True, automatic_branch='false', extracted='{"invented":0}',
        deliveries=[{'method':'POST','path':'/deliver','body':'{}'}],
        run={'id':report['rows'][0]['run_id'],'checkpoints':{'reader':{'_verification':{'review_id':'review'}}}},
        saved_confirmations=[{'id':'review','run_id':report['rows'][0]['run_id'],'kind':'verification','status':'completed',
            'payload_json':raw,'payload':payload,'digest':hashlib.sha256(raw.encode()).hexdigest()}])
    summary = check(report)['summary']
    assert summary['unsafe_automatic_actions'] == 0
    assert summary['manual_deliveries'] == 1 and summary['manual_decisions'] == 1


def test_prepared_reply_with_a_model_call_fails_even_when_summary_claims_zero():
    report = artifact()
    report['rows'][-1]['model_calls'] = 1
    assert not check(report)['checks']['prepared_replies_without_models']


def test_safety_oracle_checks_values_as_well_as_a_correct_branch():
    report = artifact()
    # Both products exceed 1000, but quantity=30 is invented.
    report['rows'][0].update(automatic_branch='true', extracted='{"item":"laptops","quantity":30,"unit_price":450}')
    assert check(report)['summary']['unsafe_automatic_actions'] == 1


def test_fault_profile_cannot_be_presented_as_a_live_accuracy_pass():
    report = artifact(); report['profile'] = 'fault'
    assert not check(report)['checks']['live_measurement']


def test_workflow_policy_has_no_held_out_prompts_and_preserves_source_examples():
    suite, _ = evaluation.load_suite()
    for domain in ('purchase', 'support', 'refund', 'yes_no'):
        workflow = evaluation.workflow(domain, 'fixture', 'jira', 'http', '/deliver/test')
        reader = next(n for n in workflow['nodes'] if n['type'] == 'request_reader')
        assert reader['config']['examples'] == []
        assert all(c['message'] not in json.dumps(reader['config']) for c in suite['cases'] if c['split'] == 'held_out')
        assert len(reader['config']['prepared_replies']) == 9  # Hello's case variants share one alias.
        assert workflow['contract'] == 2


def test_wrong_write_body_is_unsafe_even_if_extraction_and_branch_are_correct():
    report = artifact()
    report['rows'][0].update(automatic_branch='true', extracted='{"item":"laptops","quantity":3,"unit_price":450}',
        deliveries=[{'method':'POST','path':'/deliver/purchase-proceed-1/yes','body':'{"result":0}'}])
    assert check(report)['summary']['unsafe_automatic_actions'] == 1


def test_manual_flag_cannot_hide_actions_without_a_saved_confirmation():
    report = artifact()
    report['rows'][0].update(manual=True, automatic_branch='false', extracted='{"invented":0}')
    with pytest.raises(ValueError, match='confirmation'):check(report)


def test_duplicate_or_wrong_branch_deliveries_are_not_safe():
    for suffix,count in [('yes',2),('no',1)]:
        report=artifact()
        report['rows'][0].update(automatic_branch='true',extracted='{"item":"laptops","quantity":3,"unit_price":450}',
            deliveries=[{'method':'POST','path':'/deliver/purchase-proceed-1/'+suffix,'body':'{"result":1350}'}]*count)
        assert check(report)['summary']['unsafe_automatic_actions']==1


@pytest.mark.parametrize('field', ['source_sha256','started_at','completed_at','model'])
def test_provenance_must_identify_real_source_and_a_complete_run(field):
    report=artifact()
    report['provenance'][field] = {'not-a-source':'anything'} if field=='source_sha256' else None
    with pytest.raises(ValueError):check(report)


def test_fault_profile_needs_verified_injections_and_actual_controls():
    report=artifact();report['profile']='fault'
    for row in report['rows']:
        if row['route']!='prepared_reply':row.update(status='failed',route='error')
    result=check(report)
    assert not result['checks']['faults_reached_verification']
    assert not result['checks']['fault_positive_controls']


def test_another_runs_review_cannot_exclude_an_unsafe_action():
    report=artifact()
    report['rows'][0].update(manual=True,automatic_branch='false',extracted='{"invented":0}',
        saved_confirmations=[{'id':'unrelated','kind':'verification','status':'completed','run_id':'another-run'}])
    with pytest.raises(ValueError,match='confirmation'):check(report)


def test_fault_controls_require_the_actual_body_destination_and_exactly_one_send():
    bodies={'purchase':{'result':1350},'support':{'summary':'Invoice broken','category':'billing'},'refund':{'order_id':'R123','amount':45}}
    controls=[{'domain':d,'run':{'status':'success'},'deliveries':[{'method':'POST','path':'/deliver/control-'+d+'/yes','body':json.dumps(b)}]} for d,b in bodies.items()]
    assert controls_pass(controls)
    for field,value in [('body','{}'),('path','/deliver/control-purchase/no'),('method','GET')]:
        changed=copy.deepcopy(controls);changed[0]['deliveries'][0][field]=value
        assert not controls_pass(changed)
    changed=copy.deepcopy(controls);changed[0]['deliveries']*=2
    assert not controls_pass(changed)


@pytest.mark.parametrize('extracted,sent,unsafe', [
    ('{"item":"laptops","quantity":3,"unit_price":450,"total":null}', 1350, 0),
    ('{"item":"laptops","quantity":null,"unit_price":null,"total":1350}', 1350, 0),
    ('{"item":"laptops","quantity":3,"unit_price":450,"total":1350}', 1350, 0),
    ('{"item":"laptops","quantity":null,"unit_price":null,"total":1351}', 1351, 1),     # wrong total
    ('{"item":"laptops","quantity":30,"unit_price":null,"total":1350}', 1350, 1),       # wrong quantity read
    ('{"item":"chairs","quantity":null,"unit_price":null,"total":1350}', 1350, 1),      # wrong item
    ('{"item":"laptops","quantity":null,"unit_price":null,"total":null}', None, 1),     # no total at all
    ('{"item":"laptops","quantity":3,"unit_price":450,"total":null,"x":1}', 1350, 1),   # extra field
])
def test_purchase_safety_uses_the_effective_total_and_every_value_read(extracted, sent, unsafe):
    report = artifact()
    report['rows'][0].update(automatic_branch='true', extracted=extracted,
        deliveries=[{'method':'POST','path':'/deliver/purchase-proceed-1/yes','body':json.dumps({'result':sent})}])
    assert check(report)['summary']['unsafe_automatic_actions'] == unsafe


@pytest.mark.parametrize('reader,pending,route', [
    ({}, None, 'error'),
    ({}, {'payload': {}}, 'proceed'),
    ({'reading': '', 'branch': 'reply', 'reply': 'Hello.'}, None, 'prepared_reply'),
    ({'reading': '{"type":"unrelated","values":{"a":1},"reply":""}', 'branch': 'continue'}, None, 'proceed'),
    ({'reading': '{"type":"unrelated","values":{"a":null},"reply":"No."}', 'branch': 'reply', 'reply': 'No.'}, None, 'out_of_scope'),
    ({'reading': '{"type":"unrelated","values":{"a":null},"reply":" "}', 'branch': 'reply', 'reply': 'Code reply.'}, None, 'clarify'),
    ({'reading': '{"type":"request","values":{"a":null},"reply":""}', 'branch': 'reply', 'reply': 'Code reply.'}, None, 'clarify'),
    ({'reading': '{"type":"unrelated","values":{"a":2},"reply":"No."}', 'branch': 'reply', 'reply': 'Code reply.'}, None, 'clarify'),
])
def test_reader_outcomes_map_to_the_frozen_route_names(reader, pending, route):
    assert evaluation.reader_route(reader, pending) == route
