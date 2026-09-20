"""Measure the existing judge against pre-annotated saved evidence; never gate runtime."""
import argparse
import asyncio
import json
from pathlib import Path
from scripts.eval_claims import judge_claims

def summarize(rows):
    valid=[r for r in rows if r['judgment']['status']=='judged' and r['judgment']['claims']]
    positives=[r for r in valid if r['expected']=='supported']
    negatives=[r for r in valid if r['expected']!='supported']
    def accepted(r):return all(c['verdict']=='supported' for c in r['judgment']['claims'])
    def rate(n,d):return round(n/d,4) if d else None
    positive_total=sum(r['expected']=='supported' for r in rows)
    negative_total=len(rows)-positive_total
    false_accepts=sum(accepted(r) for r in negatives);false_rejects=sum(not accepted(r) for r in positives)
    return {'n':len(rows),'valid_judgments':len(valid),'errors':len(rows)-len(valid),
            'contract_compliance_rate':rate(len(valid),len(rows)),
            'false_acceptances':false_accepts,'false_acceptance_denominator':len(negatives),'false_acceptance_rate':rate(false_accepts,len(negatives)),
            'false_rejections':false_rejects,'false_rejection_denominator':len(positives),'false_rejection_rate':rate(false_rejects,len(positives)),
            'supported_label_coverage':rate(len(positives),positive_total),'negative_label_coverage':rate(len(negatives),negative_total),
            'limitation':'Error/no-claims outputs are unjudged, never passes. Conditional error rates can look good at low coverage. Assistant labels require independent human review; claim extraction completeness is not proven.'}

async def evaluate(dataset,model,output=None):
    rows=[]
    for item in dataset['cases']:
        judgment=await judge_claims(item['answer'],item['passages'],model)
        rows.append({**item,'expected':item['label'],'judgment':judgment})
        print(item['id'],judgment['status'],[c['verdict'] for c in judgment['claims']],flush=True)
        if output:
            output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps({'complete':False,'model':model,'annotation':dataset['annotation'],'summary':summarize(rows),'rows':rows},indent=2))
    return {'complete':True,'model':model,'annotation':dataset['annotation'],'summary':summarize(rows),'rows':rows}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset',type=Path)
    parser.add_argument('--model',default='llama3.1:latest')
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.model.split(':',1)[0]=='llama3.1':parser.error('llama3.1 claim judge is retired after failed calibration; use scripts.eval_nli for offline classifier evaluation.')
    report=asyncio.run(evaluate(json.loads(args.dataset.read_text()),args.model,args.out))
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2))
    print(json.dumps(report['summary'],indent=2))

if __name__=='__main__':main()
