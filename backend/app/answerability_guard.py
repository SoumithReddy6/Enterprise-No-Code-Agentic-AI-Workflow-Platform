"""Opt-in, local retrieval guard. Unavailable readers skip rather than stop runs."""
import asyncio
import os
from pathlib import Path
import threading
from .kb.answerability import AnswerabilityReader, MODEL, REVISION, THRESHOLD, FILES
from .observability import journal

_lock=threading.Lock()
_cached=None
_warned=set()

def _score(question,passages):
    global _cached
    root=Path(os.getenv('ANSWERABILITY_MODEL_DIR','.data/models/answerability')).expanduser()
    with _lock:  # Tokenizer state is mutable; loading and scoring are serialized.
        try:
            signature=(str(root.resolve()),tuple((name,(root/name).stat().st_mtime_ns,(root/name).stat().st_size) for name in ('manifest.json',*FILES)))
            if _cached is None or _cached[0]!=signature:
                _cached=None
                _cached=(signature,AnswerabilityReader(root))
            result=_cached[1].score(question,passages)
            if result.get('status')!='scored' or type(result.get('answerable')) is not bool:
                raise RuntimeError('Reader could not score the complete input')
            return {'enabled':True,'status':'scored','decision':'allow' if result['answerable'] else 'abstain',
                    'margin':result['margin'],'threshold':THRESHOLD,'model':MODEL,'revision':REVISION,
                    'abstention_source':'answerability_guard' if not result['answerable'] else None}
        except FileNotFoundError:reason='weights_missing'
        except (ValueError,KeyError):reason='integrity_error'
        except Exception:reason='reader_error'
        if reason not in _warned:
            _warned.add(reason)
            journal(event='answerability.unavailable',reason_code=reason)
        return {'enabled':True,'status':'skipped','decision':'skip','reason':reason,'abstention_source':None}

async def check(question,passages,node_id=None):
    decision=await asyncio.to_thread(_score,question,passages)
    journal(event='answerability.decision',node_id=node_id,status=decision['status'],action=decision['decision'],
            reason_code=decision.get('reason'),abstention_source=decision.get('abstention_source'))
    return decision

def from_outputs(outputs):
    """Only retrieval/query outputs identify actual reader decisions, not agents."""
    import json
    try:
        raw=json.loads(outputs.get('context','{}'))
        value=raw.get('answerability')
        if value is None:value=json.loads(outputs.get('grounding','{}')).get('retrieval_answerability')
        return value if isinstance(value,dict) and value.get('decision') in ('allow','abstain','skip') else None
    except (ValueError,TypeError,AttributeError):return None
