"""Deterministic extractive answerability reader for opt-in runtime and offline evaluation.

Scores question + retrieved evidence before answer generation. No network, prompts,
expected labels, or generated answers enter inference. The runtime adapter applies the fixed threshold 0 only when explicitly enabled.
"""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

MODEL='onnx-community/tinyroberta-squad2-ONNX'
REVISION='7c9f69b7e6228375169a4553bcfa6639152e3a69'
FILES={'model.onnx':'onnx/model_quantized.onnx','tokenizer.json':'tokenizer.json','config.json':'config.json'}
SHA256={'model.onnx':'4db70e7a019e32bf652fb9d30a5c24d18b6949fae2f1dd00d9fe6d43052b77cd','tokenizer.json':'7b62d797c95d1563d3467f48daad0eca3e150f2d587e7e687b557c9ef1b25473','config.json':'c2321a2d8d4945ef0f26abfbb4aa9dded2a7ce14aa35bb8daa2eb94765629538'}
MAX_LENGTH=384
STRIDE=128
MAX_SPAN=30
THRESHOLD=0.0

def best_span(start_logits,end_logits,sequence_ids,offsets,context,max_span_tokens=MAX_SPAN):
    """Max legal context-only span score minus CLS start+end score, per window."""
    n=len(start_logits)
    if not n or any(len(x)!=n for x in (end_logits,sequence_ids,offsets)):raise ValueError('Mismatched logits/encoding shapes')
    if max_span_tokens<1 or any(not math.isfinite(float(v)) for v in list(start_logits)+list(end_logits)):raise ValueError('Invalid logits or span limit')
    if sequence_ids[0] is not None:raise ValueError('CLS must be first special token')
    valid=[i for i in range(n) if sequence_ids[i]==1 and offsets[i][1]>offsets[i][0]]
    if not valid:raise ValueError('No context tokens')
    if any(not 0<=offsets[i][0]<offsets[i][1]<=len(context) for i in valid):raise ValueError('Invalid context offsets')
    allowed=set(valid);best=None
    for i in valid:
        for j in range(i,min(n,i+max_span_tokens)):
            if j not in allowed:break
            if offsets[j][1]<=offsets[i][0]:continue
            value=float(start_logits[i])+float(end_logits[j])
            if best is None or value>best[0]:best=(value,i,j)
    if best is None:raise ValueError('No legal answer span')
    value,i,j=best;null=float(start_logits[0])+float(end_logits[0]);margin=value-null
    return {'text':context[offsets[i][0]:offsets[j][1]],'start_char':offsets[i][0],'end_char':offsets[j][1],
            'start_token':i,'end_token':j,'span_score':value,'null_score':null,'margin':margin,'answerable':margin>THRESHOLD}

class AnswerabilityReader:
    def __init__(self,model_dir):
        import onnxruntime as ort
        from tokenizers import Tokenizer
        root=Path(model_dir);manifest=json.loads((root/'manifest.json').read_text())
        if manifest.get('model')!=MODEL or manifest.get('revision')!=REVISION or manifest.get('sha256')!=SHA256 or manifest.get('files')!=FILES:raise ValueError('Model manifest does not match pinned artifacts')
        for name in FILES:
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=SHA256[name]:raise ValueError(f'Checksum mismatch: {name}')
        config=json.loads((root/'config.json').read_text())
        if config['architectures']!=['RobertaForQuestionAnswering'] or config['bos_token_id']!=0:raise ValueError('Unexpected QA architecture/CLS mapping')
        self.manifest=manifest;self.tokenizer=Tokenizer.from_file(str(root/'tokenizer.json'))
        self.tokenizer.no_padding();self.tokenizer.no_truncation()
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1;options.execution_mode=ort.ExecutionMode.ORT_SEQUENTIAL
        self.session=ort.InferenceSession(str(root/'model.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        if {x.name for x in self.session.get_outputs()}!={'start_logits','end_logits'}:raise ValueError('Unexpected QA output mapping')

    def windows(self,question,context):
        # Build windows from the complete tokenization, without relying on overflow.
        self.tokenizer.no_truncation()
        if len(self.tokenizer.encode(question,add_special_tokens=False).ids)>64:raise ValueError('Question exceeds 64 tokens; no truncation permitted')
        encoded=self.tokenizer.encode(question,context)
        context_indices=[i for i,seq in enumerate(encoded.sequence_ids) if seq==1]
        if not context_indices:raise ValueError('No context tokens')
        first,last=context_indices[0],context_indices[-1]
        if context_indices!=list(range(first,last+1)):raise ValueError('Noncontiguous context token mapping')
        prefix=list(range(first));suffix=list(range(last+1,len(encoded.ids)))
        capacity=MAX_LENGTH-len(prefix)-len(suffix)
        if capacity<=STRIDE:raise ValueError('Question leaves insufficient context capacity')
        windows=[]
        for offset in range(0,len(context_indices),capacity-STRIDE):
            chunk=context_indices[offset:offset+capacity]
            indices=prefix+chunk+suffix
            windows.append(SimpleNamespace(**{name:[getattr(encoded,name)[i] for i in indices] for name in ('ids','attention_mask','type_ids','sequence_ids','offsets')}))
            if len(windows)>64:raise ValueError('Too many context windows; no partial score')
            if offset+capacity>=len(context_indices):break
        return windows

    def score(self,question,passages):
        import numpy as np
        try:
            if not isinstance(question,str) or not question.strip() or len(question)>2000:raise ValueError('Invalid question')
            if not isinstance(passages,list) or len(passages)>32:raise ValueError('Invalid or excessive passages')
            if not passages:return {'status':'scored','answerable':False,'margin':None,'reason':'no_evidence','candidates':[],'window_count':0,'threshold':THRESHOLD}
            texts=[]
            for passage in passages:
                text=passage.get('text')
                if not isinstance(text,str) or not text.strip():raise ValueError('Empty or invalid passage')
                texts.append(text)
            if sum(map(len,texts))>100000:raise ValueError('Evidence exceeds 100000 characters; no partial score')
            candidates=[]
            for passage_index,(passage,context) in enumerate(zip(passages,texts)):
                for window_index,encoding in enumerate(self.windows(question,context)):
                    if encoding.ids[0]!=0:raise ValueError('Unexpected CLS token')
                    available={'input_ids':encoding.ids,'attention_mask':encoding.attention_mask,'token_type_ids':encoding.type_ids}
                    feeds={i.name:np.asarray([available[i.name]],dtype=np.int64) for i in self.session.get_inputs()}
                    starts,ends=self.session.run(['start_logits','end_logits'],feeds)
                    if starts.shape!=(1,len(encoding.ids)) or ends.shape!=starts.shape:raise ValueError('Invalid output shape')
                    candidate=best_span(starts[0].tolist(),ends[0].tolist(),encoding.sequence_ids,encoding.offsets,context)
                    candidates.append({**candidate,'passage_index':passage_index,'citation':passage.get('citation'),'filename':passage.get('filename'),'page':passage.get('page'),'window_index':window_index,'window_tokens':len(encoding.ids)})
            best=max(candidates,key=lambda row:row['margin'])
            return {'status':'scored','answerable':best['margin']>THRESHOLD,'margin':best['margin'],'best_candidate':best,'candidates':candidates,'window_count':len(candidates),'threshold':THRESHOLD,'truncated':False,'runtime_influence_allowed':False}
        except Exception as exc:
            return {'status':'error','answerable':False,'margin':None,'error':str(exc)[:500],'candidates':[],'runtime_influence_allowed':False}
