import json
import subprocess
import sys
from pathlib import Path
import pytest

SCRIPT=Path(__file__).resolve().parents[2]/'scripts/check_grounding_eval.py'

def valid_report():
    return {'generation':{'summary':{'n':38,'answer_substring_match_rate':.9,'false_abstention':0.,'heuristic_abstention_on_unanswerable':1.,'citation_rate':1.,'errors':0,'contract_compliance_rate':{'first_attempt':1.,'unavailable':0,'attempted':38}},'rows':[{'kind':'verbatim','cited':['S1']}]*33+[{'kind':'unanswerable','cited':[]}]*5}}

def run(tmp_path,payload):
    path=tmp_path/'report.json';path.write_text(json.dumps(payload))
    return subprocess.run([sys.executable,str(SCRIPT),str(path)],text=True,capture_output=True)

@pytest.mark.parametrize('payload',[{},[],{'retrieval':{}},{'generation':[]},{'generation':{'summary':{},'rows':[]}}])
def test_unusable_reports_exit_two_without_traceback(tmp_path,payload):
    result=run(tmp_path,payload)
    assert result.returncode==2
    assert 'not a generation report' in result.stderr
    assert 'Traceback' not in result.stderr

@pytest.mark.parametrize('key,value',[('citation_rate','1'),('false_abstention',float('nan')),('errors',True)])
def test_invalid_metric_types_are_unusable(tmp_path,key,value):
    report=valid_report();report['generation']['summary'][key]=value
    assert run(tmp_path,report).returncode==2

def test_acceptance_and_safety_rejection_have_distinct_exit_codes(tmp_path):
    report=valid_report();assert run(tmp_path,report).returncode==0
    report['generation']['summary']['heuristic_abstention_on_unanswerable']=.4
    result=run(tmp_path,report)
    assert result.returncode==1
    assert json.loads(result.stdout)['checks']['abstention_on_unanswerable'] is False

def test_missing_file_exits_two(tmp_path):
    result=subprocess.run([sys.executable,str(SCRIPT),str(tmp_path/'missing.json')],capture_output=True,text=True)
    assert result.returncode==2 and 'not a generation report' in result.stderr

def test_extended_suite_requires_explicit_expected_count(tmp_path):
    from scripts.check_grounding_eval import check
    report=valid_report();report['generation']['rows'] += [{'kind':'unanswerable','cited':[]}]*15
    report['generation']['summary']['n']=53
    assert not check(report)['accepted']
    assert check(report,expected_count=53)['accepted']
