"""Release gate for a saved real-document generation evaluation; no model calls."""
import argparse
import json
import math
from pathlib import Path
import sys


def validate_report(report):
    """Reject unusable artifacts separately from measured quality failures."""
    if not isinstance(report,dict) or not isinstance(report.get('generation'),dict):
        raise ValueError('missing generation object')
    generation=report['generation'];summary=generation.get('summary');rows=generation.get('rows')
    if not isinstance(summary,dict) or not isinstance(rows,list):raise ValueError('missing summary or rows')
    for field in ('answer_substring_match_rate','false_abstention','heuristic_abstention_on_unanswerable','citation_rate'):
        value=summary.get(field)
        if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:raise ValueError(f'invalid {field}')
    for field in ('n','errors'):
        if type(summary.get(field)) is not int or summary[field]<0:raise ValueError(f'invalid {field}')
    for row in rows:
        if not isinstance(row,dict) or row.get('kind') not in ('verbatim','paraphrase','reasoning','unanswerable') or not isinstance(row.get('cited'),list) or any(not isinstance(v,str) for v in row['cited']):
            raise ValueError('invalid generation row')
    compliance=summary.get('contract_compliance_rate',{})
    if not isinstance(compliance,dict):raise ValueError('invalid contract compliance')
    for field in ('first_attempt','after_repair','failed'):
        value=compliance.get(field)
        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1):raise ValueError('invalid compliance rate')
    for field in ('attempted','unavailable','not_called'):
        if field in compliance and (type(compliance[field]) is not int or compliance[field]<0):raise ValueError('invalid compliance count')


def check(report,expected_count=53):
    validate_report(report)
    if type(expected_count) is not int or expected_count<1:raise ValueError('invalid expected count')
    generation=report['generation'];summary=generation['summary'];rows=generation['rows']
    # Operating point fixed before the 2026-09-21 fresh evaluation.
    # Source: evals/results/answerability-guarded-generation.json (Sep 18),
    # 53 cases: 33 answerable, 20 unanswerable. All four bars derive from
    # this one guarded configuration, not high-water marks from separate runs.
    # Regulatory-document use favors abstention over unsupported answers:
    # measured substring 28/33, abstention 15/20, false abstention 2/33,
    # citations 31/33. The requested .81/.70/.90 minima permit 27/33,
    # 14/20, and 30/33 respectively: one case of movement, not two.
    # IMPORTANT arithmetic exception: the requested .09 false-abstention cap
    # still permits only 2/33; 3/33 = .090909 (reported .091) exceeds it.
    # Keep that explicitly requested cap, rather than claim all four bars
    # allow one case. Do not retune any threshold after seeing fresh results.
    # These are substring/citation-label heuristics, not semantic safety proof;
    # the answerability guard is currently an offline evaluation option.
    checks={
        'complete_real_corpus': len(rows)==expected_count and summary.get('n')==expected_count,
        'first_attempt_compliance': (summary.get('contract_compliance_rate',{}).get('first_attempt') or 0)>=0.90,
        'answer_substring_match_rate': summary['answer_substring_match_rate']>=0.81,
        'false_abstention': summary['false_abstention']<=0.09,
        'abstention_on_unanswerable': summary['heuristic_abstention_on_unanswerable']>=0.70,
        'citation_rate': summary['citation_rate']>=0.90,
        'uncited_answerable_rows': sum(not r['cited'] for r in rows if r['kind']!='unanswerable')<=5,
        'no_generation_errors': summary['errors']==0,
    }
    compliance=summary.get('contract_compliance_rate',{})
    checks['compliance_measured']=compliance.get('unavailable',len(rows))==0 and compliance.get('attempted',0)>0
    return {'accepted':all(checks.values()),'checks':checks}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report',type=Path)
    parser.add_argument('--expected-count',type=int,default=53,help='Expected full suite size (default: expanded 53-question safety suite). Does not change quality thresholds.')
    args=parser.parse_args()
    try:
        result=check(json.loads(args.report.read_text()),args.expected_count)
    except (OSError,UnicodeError,json.JSONDecodeError,ValueError) as exc:
        print(f'not a generation report: {exc}',file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['accepted'] else 1)


if __name__=='__main__':main()
