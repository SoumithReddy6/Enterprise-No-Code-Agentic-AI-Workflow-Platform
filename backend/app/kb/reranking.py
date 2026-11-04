"""Optional, local-only cross-encoder. Model installation is an explicit operator step."""
import asyncio
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import threading

_lock=threading.Lock()

@lru_cache(maxsize=2)
def load(directory):
    import onnxruntime as ort
    from tokenizers import Tokenizer
    ort.disable_telemetry_events()
    root=Path(directory)
    try:
        manifest=json.loads((root/'manifest.json').read_text())
        for name in ('tokenizer.json','model_quantized.onnx'):
            if hashlib.sha256((root/name).read_bytes()).hexdigest()!=manifest['sha256'][name]:raise ValueError('Reranker checksum mismatch')
        tokenizer=Tokenizer.from_file(str(root/'tokenizer.json'))
        tokenizer.enable_truncation(max_length=512,strategy='only_second')
        tokenizer.enable_padding(pad_id=0,pad_token='[PAD]')
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        session=ort.InferenceSession(str(root/'model_quantized.onnx'),sess_options=options,providers=['CPUExecutionProvider'])
        return tokenizer,session
    except (OSError,KeyError,json.JSONDecodeError) as exc:
        raise ValueError('Local reranker is not installed. Run python -m scripts.install_reranker or select reranker=none.') from exc

def score_pairs(query,passages):
    import numpy as np
    if len(passages)>100:raise ValueError('Reranking is bounded to 100 candidates')
    directory=os.environ.get('KB_RERANKER_DIR',str(Path(os.environ.get('DATA_DIR','.data'))/'reranker'))
    with _lock:
        tokenizer,session=load(directory)
        # Reserve most of the 512-token pair budget for evidence.
        tokenizer.no_truncation()
        query=tokenizer.decode(tokenizer.encode(query,add_special_tokens=False).ids[:128])
        tokenizer.enable_truncation(max_length=512,strategy='only_second')
        scores=[]
        for start in range(0,len(passages),8):
            encoded=tokenizer.encode_batch([(query,p) for p in passages[start:start+8]])
            arrays={name:np.asarray([getattr(e,attr) for e in encoded],dtype=np.int64)
                    for name,attr in [('input_ids','ids'),('attention_mask','attention_mask'),('token_type_ids','type_ids')]}
            scores.extend(float(v) for v in session.run(None,{i.name:arrays[i.name] for i in session.get_inputs()})[0].reshape(-1))
    if len(scores)!=len(passages) or not all(math.isfinite(v) for v in scores):raise ValueError('Invalid reranker scores')
    return scores

async def rerank(query,sources):
    if not sources:return []
    scores=await asyncio.to_thread(score_pairs,query,[s['text'] for s in sources])
    return [{**s,'rerank_score':score} for score,s in sorted(zip(scores,sources),key=lambda pair:-pair[0])]
