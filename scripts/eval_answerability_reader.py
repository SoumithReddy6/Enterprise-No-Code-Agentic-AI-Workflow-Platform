"""Offline extractive QA diagnostic at predeclared zero span/null margin threshold."""
import argparse
import hashlib
import json
from pathlib import Path
import time
from backend.app.kb.answerability import AnswerabilityReader,THRESHOLD,MAX_LENGTH,STRIDE,MAX_SPAN

def summarize(rows):
    valid=[r for r in rows if r['answerability']['status']=='scored']
    positives=[r for r in valid if r['kind']!='unanswerable']
    negatives=[r for r in valid if r['kind']=='unanswerable']
    accepted=sum(r['answerability']['answerable'] for r in positives)
    rejected=sum(not r['answerability']['answerable'] for r in negatives)
    rate=lambda n,d:round(n/d,4) if d else None
    return {'n':len(rows),'valid_outputs':len(valid),'errors':len(rows)-len(valid),'valid_output_rate':rate(len(valid),len(rows)),
            'answerable':len(positives),'unanswerable':len(negatives),'answerable_accepted':accepted,
            'false_abstentions':len(positives)-accepted,'false_abstention_rate':rate(len(positives)-accepted,len(positives)),
            'unanswerable_rejected':rejected,'unanswerable_rejection_rate':rate(rejected,len(negatives)),
            'false_acceptances':len(negatives)-rejected,'false_acceptance_rate':rate(len(negatives)-rejected,len(negatives)),
            'answerable_coverage':rate(len(positives),sum(r['kind']!='unanswerable' for r in rows)),
            'unanswerable_coverage':rate(len(negatives),sum(r['kind']=='unanswerable' for r in rows))}

def evaluate(rows,reader):
    results=[];start=time.monotonic()
    for row in rows:
        # Labels, split, expected facts, retrieval scores and generated answer are excluded.
        result=reader.score(row['question'],[{'text':p['text'],'citation':p.get('citation'),'filename':p.get('filename'),'page':p.get('page')} for p in row['passages']])
        results.append({**row,'answerability':result})
        print(row.get('id',row.get('index',row['question'])),result['status'],result['answerable'],result['margin'],flush=True)
    return {'complete':True,'offline_only':True,'runtime_influence_allowed':False,'model_manifest':reader.manifest,
            'protocol':{'threshold':THRESHOLD,'accept_rule':'maximum per-window (best context span logit sum minus CLS null logit sum) > threshold','window_tokens':MAX_LENGTH,'overlap_tokens':STRIDE,'max_span_tokens':MAX_SPAN,'threshold_selected_on_data':False},
            'elapsed_seconds':round(time.monotonic()-start,3),'summary':summarize(results),
            'by_split':{split:summarize([r for r in results if r.get('split','calibration')==split]) for split in sorted({r.get('split','calibration') for r in results})},
            'rows':results,'limitation':'Reader finds candidate spans, not proof of full answerability or support. Scores are not probabilities. Errors are unjudged, never accepted. Positive held-out coverage may be absent; span correctness is not independently labelled.'}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--model-dir',type=Path,required=True);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--mode',default='hybrid')
    args=parser.parse_args();raw=args.source.read_bytes();source=json.loads(raw)
    modes=source.get('retrieval',source)
    rows=modes[args.mode]['rows']
    report=evaluate(rows,AnswerabilityReader(args.model_dir));report['source_artifact']=str(args.source);report['source_sha256']=hashlib.sha256(raw).hexdigest();report['retrieval_mode']=args.mode
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'summary':report['summary'],'by_split':report['by_split']},indent=2))
