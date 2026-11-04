"""Independent path enumeration against S08's dataflow analysis; no model/network calls."""
import json
import random
from pathlib import Path
from backend.app.compiler import validate_workflow
from backend.app.models import Workflow

rng=random.Random(808)
checked=0;graphs=0;disagreements=[]
for case in range(1000):
    ids=['input',*[f'n{i}' for i in range(5)],'out']
    nodes=[{'id':'input','type':'chat_input'}]
    policies={id:rng.choice(['fail','route','continue']) for id in ids[1:-1]}
    for id in ids[1:-1]:
        nodes.append({'id':id,'type':'tool_python','inputs':{'input':'input.message'},
                      'config':{'code':'print(input_text)'},'on_error':policies[id]})
    nodes.append({'id':'out','type':'response','inputs':{'text':'input.message'}})
    edges=[{'id':'start','source':'input','target':'n0'}]
    for i,id in enumerate(ids[1:-1],1):
        targets=ids[i+1:]
        edges.append({'id':id+'ok','source':id,'target':rng.choice(targets)})
        if policies[id]=='route':edges.append({'id':id+'err','source':id,'target':rng.choice(targets),'sourceHandle':'error'})
    # Keep only reachable nodes, matching the supported single-trigger graph domain.
    reached=set();paths={id:[] for id in ids}
    def walk(id,available):
        reached.add(id);paths[id].append(set(available))
        for e in edges:
            if e['source']!=id:continue
            if e.get('sourceHandle')=='error':walk(e['target'],available)
            elif policies.get(id)=='continue':
                walk(e['target'],available);walk(e['target'],available|{id})
            else:walk(e['target'],available|{id})
    walk('input',set())
    raw={'version':1,'name':'Path oracle','nodes':[n for n in nodes if n['id'] in reached],
         'edges':[e for e in edges if e['source'] in reached]}
    base=Workflow.model_validate(raw)
    assert validate_workflow(base)==[],validate_workflow(base)
    graphs+=1
    for consumer in base.nodes[1:]:
        for source in base.nodes:
            candidate=base.model_copy(deep=True)
            target=next(n for n in candidate.nodes if n.id==consumer.id)
            port='message' if source.id=='input' else 'text'
            target.inputs={next(iter(target.inputs)):source.id+'.'+port}
            expected=all(source.id in p for p in paths[consumer.id])
            actual=not validate_workflow(candidate)
            checked+=1
            if actual!=expected:disagreements.append({'case':case,'source':source.id,'consumer':consumer.id,'expected':expected,'errors':validate_workflow(candidate)})
result={'seed':808,'graphs':graphs,'bindings_checked':checked,'disagreements':disagreements}
Path(__file__).with_name('path-results.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result));assert not disagreements
