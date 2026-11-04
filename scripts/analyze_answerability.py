"""Offline relevance-score separation; never treat a missing score as zero or tune on holdout."""
import argparse
import json
import math
from pathlib import Path
import statistics


def score(row,field,aggregation):
    values=[p.get(field) for p in row.get('passages',[])]
    if not values or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
        raise ValueError(f'missing or invalid {field}: {row.get("question")}')
    return values[0] if aggregation=='first' else max(values)


def outcomes(rows,threshold):
    answerable=[v for r,v in rows if r['kind']!='unanswerable']
    unanswerable=[v for r,v in rows if r['kind']=='unanswerable']
    return {'answerable':len(answerable),'unanswerable':len(unanswerable),
            'false_abstention':sum(v<threshold for v in answerable)/len(answerable) if answerable else None,
            'unanswerable_rejection_rate':sum(v<threshold for v in unanswerable)/len(unanswerable) if unanswerable else None}


def analyze(rows,field,aggregation='first'):
    scored=[(r,score(r,field,aggregation)) for r in rows]
    calibration=[x for x in scored if x[0].get('split','calibration')!='holdout']
    holdout=[x for x in scored if x[0].get('split')=='holdout']
    values=sorted({v for _,v in calibration})
    candidates=sorted(set(values+[math.nextafter(v,math.inf) for v in values]))
    feasible=[];frontier=[]
    for threshold in candidates:
        result=outcomes(calibration,threshold)
        if result['false_abstention'] is not None and result['false_abstention']<=.091:
            frontier.append({'threshold':threshold,**result})
            if result['unanswerable_rejection_rate'] is not None and result['unanswerable_rejection_rate']>=.9:feasible.append(threshold)
    threshold=min(feasible) if feasible else None
    def distribution(kind):
        v=[s for r,s in scored if (r['kind']=='unanswerable')==kind]
        return {'n':len(v),'min':min(v) if v else None,'median':statistics.median(v) if v else None,'max':max(v) if v else None}
    return {'field':field,'aggregation':aggregation,'decision':'abstain when score < threshold',
            'distributions':{'answerable':distribution(False),'unanswerable':distribution(True)},
            'feasible_threshold_count':len(feasible),'selected_threshold':threshold,
            'best_rejection_with_false_abstention_at_most_0091':max(frontier,key=lambda r:r['unanswerable_rejection_rate'] or 0) if frontier else None,
            'calibration':outcomes(calibration,threshold) if threshold is not None else None,
            'holdout':outcomes(holdout,threshold) if threshold is not None else None,
            'limitation':'Exploratory relevance scores, not probabilities of support. Selection uses calibration labels only. Holdout contains negatives only; unseen-positive false abstention is unmeasured. Generation quality must be evaluated separately.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('report',type=Path);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();source=json.loads(args.report.read_text());result={'source_artifact':str(args.report),'modes':{}}
    for mode,data in source['retrieval'].items():
        result['modes'][mode]={}
        for field in ('score','rerank_score'):
            for aggregation in ('first','max'):
                key=f'{field}_{aggregation}'
                try:result['modes'][mode][key]=analyze(data['rows'],field,aggregation)
                except ValueError as exc:result['modes'][mode][key]={'unavailable':str(exc)}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
