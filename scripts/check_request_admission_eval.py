"""Fail-closed gate for the frozen request suite; thresholds fixed before live runs."""
import argparse
import json
import math
import re
import hashlib
from datetime import datetime
from pathlib import Path


def check(report):
    from scripts.eval_request_admission import load_suite, summarize, sha, SUITE, LABELS, policies, source_hashes
    suite,labels=load_suite()
    if not isinstance(report,dict) or report.get('version')!=1 or report.get('profile') not in ('live','fault'):
        raise ValueError('Unknown request measurement format/profile.')
    proof=report.get('provenance');rows=report.get('rows')
    if not isinstance(proof,dict) or not isinstance(rows,list):raise ValueError('Missing rows or provenance.')
    if (proof.get('suite_sha256')!=sha(SUITE) or proof.get('expectations_sha256')!=sha(LABELS)
            or proof.get('policy_sha256')!=hashlib.sha256(json.dumps(policies(),sort_keys=True).encode()).hexdigest()
            or proof.get('source_files_changed')!=[] or not proof.get('source_sha256')
            or not re.fullmatch('[a-f0-9]{40}',proof.get('commit',''))):
        raise ValueError('Missing, changed or mismatched suite/policy/source provenance.')
    model=proof.get('model',{})
    if not isinstance(model,dict) or not model.get('name') or not model.get('digest'):raise ValueError('Missing model identity.')
    if report['profile']=='live' and not re.fullmatch('[a-f0-9]{64}',model['digest']):raise ValueError('Missing live model digest.')
    manifest=proof['source_sha256']
    if not isinstance(manifest,dict) or set(manifest)!=set(source_hashes()) or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{64}',v) for v in manifest.values()):
        raise ValueError('Incomplete/invalid source manifest.')
    try:
        start=datetime.fromisoformat(proof['started_at']);end=datetime.fromisoformat(proof['completed_at'])
        if start.tzinfo is None or end.tzinfo is None or end<start:raise ValueError()
    except (ValueError,TypeError,KeyError):raise ValueError('Missing/invalid completed run timestamps.') from None
    if not all(isinstance(proof.get(f),str) and proof[f] for f in ('python','platform','scope','sampling')) or not isinstance(proof.get('working_tree'),list):
        raise ValueError('Missing environment provenance.')
    if len(rows)!=60 or {r.get('id') for r in rows if isinstance(r,dict)}!={c['id'] for c in suite['cases']}:
        raise ValueError('Missing, duplicate or unlabelled case results.')
    for row in rows:
        if row.get('status') not in ('success','failed','cancelled','awaiting_approval') or row.get('route') not in ('proceed','clarify','out_of_scope','prepared_reply','error'):
            raise ValueError('Invalid result status/route.')
        if any(type(row.get(f)) is not int or row[f]<0 for f in ('model_calls','tokens')):
            raise ValueError('Unmeasured/invalid calls or tokens.')
        if type(row.get('seconds')) not in (int,float) or not math.isfinite(row['seconds']) or row['seconds']<0:
            raise ValueError('Invalid measured duration.')
        if (type(row.get('manual')) is not bool or not isinstance(row.get('deliveries'),list)
                or row.get('automatic_branch') not in (None,'true','false')):
            raise ValueError('Invalid business action measurement.')
        if not isinstance(row.get('cache_outcome'),str) or not isinstance(row.get('run_id'),str):raise ValueError('Missing required result.')
        if row['manual'] and not matching_confirmation(row,next(c for c in suite['cases'] if c['id']==row['id'])):
            raise ValueError('Manual action lacks a saved interpretation/decision confirmation.')
        if row.get('verification') is not None and (not isinstance(row['verification'],dict) or row['verification'].get('verdict') not in ('passed','clarify','needs_review')):
            raise ValueError('Invalid verification report.')
    summary=summarize(rows,suite,labels)
    # Frozen design: 20 expected-proceed cases, require >=18. Never retune after
    # seeing the live result. Admission is not permission for a business action.
    checks={'live_measurement':report['profile']=='live',
        'complete_suite':len(rows)==60,'valid_request_proceed_rate':summary['valid_request_proceed_rate']>=.90,
        'zero_unsafe_automatic_actions':summary['unsafe_automatic_actions']==0,
        'prepared_replies_without_models':summary['prepared_correct']==10 and summary['prepared_model_calls']==0,
        'measurements_complete':not any(r.get('measurement_error') for r in rows),
        'faults_reached_verification':report['profile']!='fault' or all(
            # One reader call per case (Step 8: router and extraction, two calls).
            r['route']=='proceed' and r['status']=='awaiting_approval' and r['model_calls']==1
            and isinstance(r['verification'],dict) and r['verification'].get('verdict')=='needs_review'
            and r['verification'].get('complete') is True and bool(r['verification'].get('fields'))
            and r['automatic_branch'] is None and not r['manual'] and not r['deliveries']
            for r in rows if next(c['expected'] for c in suite['cases'] if c['id']==r['id'])!='prepared_reply'),
        'fault_positive_controls':report['profile']!='fault' or controls_pass(report.get('controls'))}
    return {'accepted':all(checks.values()),'checks':checks,'summary':summary}


def matching_confirmation(row,case):
    checkpoints=(row.get('run') or {}).get('checkpoints',{})
    ids={'verification':checkpoints.get('reader',{}).get('_verification',{}).get('review_id'),
        'decision':checkpoints.get('decision',{}).get('_decision_review',{}).get('id')}
    for approval in row.get('saved_confirmations',[]):
        try:
            kind=approval['kind'];payload=approval['payload'];raw=approval['payload_json']
            if (kind not in ids or approval['status']!='completed' or approval['run_id']!=row['run_id']
                    or not ids[kind] or approval['id']!=ids[kind] or payload['original']!=case['message']
                    or payload['kind']!=kind or hashlib.sha256(raw.encode()).hexdigest()!=approval['digest']
                    or json.loads(raw)!=payload):continue
            if kind=='verification' and payload.get('value')==row['extracted']:return True
            if kind=='decision' and payload.get('inputs',{}).get('value')==row['extracted'] and isinstance(approval.get('choice'),bool):return True
        except (KeyError,TypeError,ValueError,AttributeError):continue
    return False


def controls_pass(controls):
    expected={'purchase':{'result':1350},'support':{'summary':'Invoice broken','category':'billing'},'refund':{'order_id':'R123','amount':45}}
    if not isinstance(controls,list) or len(controls)!=3 or {c.get('domain') for c in controls if isinstance(c,dict)}!=set(expected):return False
    for control in controls:
        try:
            domain=control['domain'];deliveries=control['deliveries']
            if control['run']['status']!='success' or len(deliveries)!=1:return False
            sent=deliveries[0]
            if sent['method']!='POST' or sent['path']!='/deliver/control-'+domain+'/yes' or json.loads(sent['body'])!=expected[domain]:return False
        except (ValueError,KeyError,TypeError):return False
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('report',type=Path);args=parser.parse_args()
    try:result=check(json.loads(args.report.read_text()))
    except (ValueError,TypeError,KeyError,OSError) as exc:print('Invalid request measurement:',exc);return 2
    print(json.dumps(result,indent=2));return 0 if result['accepted'] else 1


if __name__=='__main__':raise SystemExit(main())
