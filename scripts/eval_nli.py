"""Offline-only whole-answer NLI calibration. No generated JSON, runtime imports or downloads."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import time
from scripts.install_nli import MODEL, REVISION, FILES, LABELS, SHA256

VERDICTS=('contradicted','supported','insufficient_evidence')

def prepare_pairs(question,answer,passages):
    if not isinstance(question,str) or not question.strip():raise ValueError('Question context required')
    if not isinstance(answer,str) or len(question)+len(answer)>50000:raise ValueError('Invalid or oversized answer')
    evidence={}
    for source in passages:
        label=source.get('citation');text=source.get('text')
        if not isinstance(label,str) or not re.fullmatch(r'S\d+',label) or label in evidence:raise ValueError('Invalid or duplicate citation')
        if not isinstance(text,str) or not text.strip():raise ValueError('Empty or invalid passage')
        evidence[label]=text
    labels=list(dict.fromkeys(re.findall(r'\[(S\d+)\]',answer)))
    if not labels or any(label not in evidence for label in labels):raise ValueError('Missing or unknown citation')
    clean=re.sub(r'\[S\d+\]','',answer).strip()
    if not clean:raise ValueError('Empty answer')
    hypothesis=f'Question: {question.strip()}\nAnswer: {clean}'
    if sum(len(evidence[label]) for label in labels)+len(hypothesis)>50000:raise ValueError('Input exceeds 50000 characters')
    return [{'citation':label,'premise':evidence[label],'hypothesis':hypothesis} for label in labels]

def classify_logits(logits):
    if len(logits)!=3 or any(not math.isfinite(float(x)) for x in logits):raise ValueError('Invalid three-class logits')
    exps=[math.exp(float(x)-max(logits)) for x in logits];total=sum(exps)
    probabilities=[x/total for x in exps]
    return {'verdict':VERDICTS[max(range(3),key=probabilities.__getitem__)],
            'scores':dict(zip(VERDICTS,probabilities))}

class NLIJudge:
    def __init__(self,model_dir):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        manifest=json.loads((model_dir/'manifest.json').read_text())
        if manifest['model']!=MODEL or manifest['revision']!=REVISION or manifest['files']!=FILES:raise ValueError('Unexpected model manifest')
        if manifest['sha256']!=SHA256:raise ValueError('Manifest does not match pinned checksums')
        for name in FILES:
            if hashlib.sha256((model_dir/name).read_bytes()).hexdigest()!=manifest['sha256'][name]:raise ValueError(f'Model checksum mismatch: {name}')
        if json.loads((model_dir/'config.json').read_text())['id2label']!=LABELS:raise ValueError('Unexpected label mapping')
        self.manifest=manifest
        self.tokenizer=Tokenizer.from_file(str(model_dir/'tokenizer.json'))
        self.tokenizer.no_truncation();self.tokenizer.no_padding()
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        self.session=ort.InferenceSession(str(model_dir/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])

    def judge(self,question,answer,passages):
        import numpy as np
        try:
            pairs=prepare_pairs(question,answer,passages);results=[]
            for pair in pairs:
                encoding=self.tokenizer.encode(pair['premise'],pair['hypothesis'])
                if len(encoding.ids)>512:raise ValueError(f'Pair exceeds 512 tokens ({len(encoding.ids)}); no truncation performed')
                available={'input_ids':encoding.ids,'attention_mask':encoding.attention_mask,'token_type_ids':encoding.type_ids}
                feeds={i.name:np.asarray([available[i.name]],dtype=np.int64) for i in self.session.get_inputs()}
                logits=self.session.run(None,feeds)[0]
                if logits.shape!=(1,3):raise ValueError('Unexpected logits shape')
                results.append({**pair,'token_count':len(encoding.ids),**classify_logits(logits[0].tolist())})
            verdicts=[r['verdict'] for r in results]
            verdict='contradicted' if 'contradicted' in verdicts else 'supported' if 'supported' in verdicts else 'insufficient_evidence'
            return {'status':'judged','verdict':verdict,'pairs':results,'review_required':True,'atomic_claim_completeness':'not_measured'}
        except Exception as exc:
            return {'status':'error','verdict':None,'pairs':[],'error':str(exc)[:500],'review_required':True}

def summarize(rows):
    valid=[r for r in rows if r['judgment']['status']=='judged' and r['judgment'].get('verdict') in VERDICTS]
    positives=[r for r in valid if r['expected']=='supported'];negatives=[r for r in valid if r['expected']!='supported']
    rate=lambda n,d:round(n/d,4) if d else None
    fa=sum(r['judgment']['verdict']=='supported' for r in negatives)
    fr=sum(r['judgment']['verdict']!='supported' for r in positives)
    confusion={label:{pred:0 for pred in VERDICTS} for label in VERDICTS}
    for row in valid:confusion[row['expected']][row['judgment']['verdict']]+=1
    return {'n':len(rows),'valid_judgments':len(valid),'errors':len(rows)-len(valid),
            'contract_compliance_rate':rate(len(valid),len(rows)),
            'validity_threshold_met':bool(rows) and len(valid)/len(rows)>=.9,'runtime_influence_allowed':False,
            'false_acceptances':fa,'false_acceptance_denominator':len(negatives),'false_acceptance_rate':rate(fa,len(negatives)),
            'false_rejections':fr,'false_rejection_denominator':len(positives),'false_rejection_rate':rate(fr,len(positives)),
            'coverage_by_label':{v:rate(sum(r['expected']==v for r in valid),sum(r['expected']==v for r in rows)) for v in VERDICTS},
            'three_class_accuracy':rate(sum(r['expected']==r['judgment']['verdict'] for r in valid),len(valid)),
            'confusion_matrix_expected_rows_predicted_columns':confusion,
            'limitation':'Validity is not accuracy. Errors are unjudged, never passes. Assistant labels are not human gold; whole-answer NLI does not prove atomic claim completeness or scope recovery.'}

def evaluate(dataset,judge):
    start=time.monotonic();rows=[]
    for item in dataset['cases']:
        judgment=judge.judge(item['question'],item['answer'],item['passages'])
        rows.append({**item,'expected':item['label'],'judgment':judgment})
        print(item['id'],judgment['status'],judgment['verdict'],judgment.get('error',''),flush=True)
    return {'complete':True,'offline_only':True,'model_manifest':judge.manifest,'annotation':dataset['annotation'],
            'elapsed_seconds':round(time.monotonic()-start,3),'summary':summarize(rows),'rows':rows}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset',type=Path);parser.add_argument('--model-dir',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();raw=args.dataset.read_bytes()
    report=evaluate(json.loads(raw),NLIJudge(args.model_dir));report['dataset_sha256']=hashlib.sha256(raw).hexdigest()
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,indent=2))
    print(json.dumps(report['summary'],indent=2))
