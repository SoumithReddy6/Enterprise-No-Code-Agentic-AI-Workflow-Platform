"""Bounded run-local evidence; cited and actively used passages are never evicted."""
import json
import re

MAX_EVIDENCE=300
LABEL=re.compile(r'^S([1-9][0-9]*)$')

def prepare_checkpoint_labels(run,completed):
    reserved=set()
    for outputs in (completed or {}).values():
        try:sources=json.loads(outputs.get('sources','[]'))
        except (ValueError,TypeError):continue
        if not isinstance(sources,list):continue
        for source in sources:
            if not isinstance(source,dict):continue
            label=source.get('citation','');match=LABEL.fullmatch(label) if isinstance(label,str) else None
            if match:run['citation_counter']=max(run.get('citation_counter',0),int(match[1]))
            if source.get('cited') and match:reserved.add(label)
    if len(reserved)>MAX_EVIDENCE:raise ValueError('Checkpoint exceeds the cited-evidence limit; start a new run with fewer retrieved passages.')
    run['reserved_citations']=reserved

def allocate_labels(run,count):
    if run is None:return [f'S{i+1}' for i in range(count)]
    start=run.get('citation_counter',0)
    for source in run.get('evidence',[]):
        match=LABEL.fullmatch(source['citation'])
        if match:start=max(start,int(match[1]))
    run['citation_counter']=start+count
    return [f'S{start+i+1}' for i in range(count)]

def remember_evidence(run,sources):
    if not isinstance(sources,list):return []
    if run is None:return sources
    registry=run.setdefault('evidence',[]);admitted=[]
    reserved=run.get('reserved_citations',set());pins=run.get('evidence_pins',{})
    for source in sources:
        if not isinstance(source,dict) or not isinstance(source.get('citation'),str):continue
        label=source['citation'];match=LABEL.fullmatch(label)
        if not match:continue
        run['citation_counter']=max(run.get('citation_counter',0),int(match[1]))
        existing=next((p for p in registry if p['citation']==label),None)
        if existing is not None:
            existing['cited']=bool(existing.get('cited') or source.get('cited') or label in reserved)
            admitted.append(existing);continue
        if len(registry)>=MAX_EVIDENCE:
            victim=next((p for p in registry if not p.get('cited') and p['citation'] not in reserved and not pins.get(p['citation'])),None)
            notice=run.setdefault('evidence_notice',{'evicted':0,'rejected':0})
            if victim is None:
                notice['rejected']+=1;continue
            registry.remove(victim);notice['evicted']+=1
        entry={**source,'cited':bool(source.get('cited') or label in reserved)}
        registry.append(entry);admitted.append(entry)
    # A batch larger than capacity can evict its own earliest admissions.
    retained={p['citation'] for p in registry}
    return [p for p in admitted if p['citation'] in retained]

async def emit_evidence_notice(run,emit,node_id):
    notice=run.pop('evidence_notice',None) if run is not None else None
    if notice:
        await emit({'kind':'evidence_eviction','node_id':node_id,'status':'warning','transient':True,**notice,
                    'reason':f"Evidence capacity {MAX_EVIDENCE}: evicted {notice['evicted']} uncited passages; rejected {notice['rejected']} new passages because remaining evidence is protected."})


def source_high_water(outputs):
    try:sources=json.loads(outputs.get('sources','[]'))
    except (ValueError,TypeError,AttributeError):return 0
    if not isinstance(sources,list):return 0
    high=0
    for source in sources:
        label=source.get('citation','') if isinstance(source,dict) else ''
        match=LABEL.fullmatch(label) if isinstance(label,str) else None
        if match:high=max(high,int(match[1]))
    return high
