import random, time
from backend.app import provenance as p
from backend.app.models import Node
rng=random.Random(1)
KINDS=[('prompt',{}),('calculate',{'expression':'coalesce(a, 1)'}),('calculate',{'expression':'a > 1 or b','mode':'check'}),('retrieve',{}),('agent',{}),('request_reader',{}),('chat_input',{}),('tool_jira',{'operation':'search'})]
PORTS=['text','context','query','reading','p1','p2','p3','p4','p5','p6']
found=0;tried=0;t0=time.time()
while time.time()-t0<60 and found<3:
    n=1;ids=[f'n{i}' for i in range(n)];nodes={}
    for id in ids:
        k,c=rng.choice(KINDS)
        names=['a','b','query','value','message','x','y','z','w','v','u','t']
        inputs={nm:f'{rng.choice(ids)}.{rng.choice(PORTS)}' for nm in rng.sample(names,rng.randint(6,8))}
        nodes[id]=Node(id=id,type=k,inputs=inputs,config=c)
    refs={r for x in nodes.values() for r in x.inputs.values()}
    if len(refs) <= 4*n+1: continue
    tried+=1
    for ref in sorted(refs):
        for f in ('','a','question','result'):
            want=p.static_label(nodes,ref,f)
            got=p._static_walk(nodes,ref,f,{},{},True)[0]
            if want!=got:
                found+=1;print('DIFF',ref,f,want,got,{k:(v.type,v.inputs,v.config) for k,v in nodes.items()});break
        else: continue
        break
print('tried',tried,'found',found)
