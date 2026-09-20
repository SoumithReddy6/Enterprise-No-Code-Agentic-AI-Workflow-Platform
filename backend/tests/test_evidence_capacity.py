import json
import pytest
from backend.app.evidence_registry import MAX_EVIDENCE,remember_evidence,allocate_labels,prepare_checkpoint_labels,emit_evidence_notice
from backend.app.agent_runtime import evidence_from_outputs,ground_answer

def passages(start,count,cited=False):
    return [{'citation':f'S{i}','text':f'Fact {i}','cited':cited} for i in range(start,start+count)]

def test_overflow_preserves_cited_and_pinned_and_never_reuses_labels():
    run={'evidence':[]}
    remember_evidence(run,passages(1,MAX_EVIDENCE))
    evidence_from_outputs(run,{'text':'Fact [S1, S2]'})
    run['evidence_pins']={'S3':1}
    remember_evidence(run,passages(301,2))
    labels={p['citation'] for p in run['evidence']}
    assert len(labels)==300 and {'S1','S2','S3','S301','S302'}<=labels
    assert 'S4' not in labels and 'S5' not in labels
    assert allocate_labels(run,1)==['S303']
    text,sources=ground_answer('Fact [S1][S2]',run['evidence'])
    assert text=='Fact [S1][S2]' and sum(p['cited'] for p in sources)==2

def test_full_protected_registry_rejects_new_evidence():
    run={'evidence':[]}
    remember_evidence(run,passages(1,300,True))
    assert remember_evidence(run,passages(301,10))==[]
    assert len(run['evidence'])==300 and run['evidence_notice']=={'evicted':0,'rejected':10}

@pytest.mark.asyncio
async def test_notice_is_visible_and_drained():
    run={'evidence':[]};events=[]
    async def emit(event):events.append(event)
    remember_evidence(run,passages(1,310))
    await emit_evidence_notice(run,emit,'retrieve')
    await emit_evidence_notice(run,emit,'retrieve')
    assert len(events)==1 and events[0]['evicted']==10 and events[0]['transient']

def test_checkpoint_restores_cited_protection_and_high_water_mark():
    completed={'old':{'sources':json.dumps(passages(1,300))},'agent':{'sources':json.dumps(passages(1,1,True))},'latest':{'sources':json.dumps(passages(900,1))}}
    run={'evidence':[]};prepare_checkpoint_labels(run,completed)
    evidence_from_outputs(run,completed['old'])
    remember_evidence(run,passages(901,1))
    assert any(p['citation']=='S1' and p['cited'] for p in run['evidence'])
    assert allocate_labels(run,1)==['S902']
    assert len(run['evidence'])==300

def test_legacy_checkpoint_with_too_many_cited_passages_fails_explicitly():
    with pytest.raises(ValueError,match='cited-evidence limit'):
        prepare_checkpoint_labels({'evidence':[]},{'agent':{'sources':json.dumps(passages(1,301,True))}})
