"""TA08 on main: an older accounting snapshot committed after a newer one (temporary SQLite only)."""
import sys,tempfile,threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from cryptography.fernet import Fernet
from backend.app.storage import Store
from backend.app.accounting_state import AccountingUnavailable
def snap(counter,tokens,usage):
    return {'action_budget':{'counter':counter,'remaining':10-counter,'ceiling':10},'tokens_spent':tokens,'usage':usage,'usage_by_invocation':{}}
def race(store,run,owner,method,old,new):
    release=threading.Event();ready=threading.Event();out={}
    def delayed():
        ready.set();release.wait(10)
        try:out['old']=getattr(store,method)(run,owner,old)
        except AccountingUnavailable as e:out['old']='refused: '+str(e)
    with ThreadPoolExecutor(2) as p:
        f=p.submit(delayed);ready.wait(10)
        out['new']=p.submit(getattr(store,method),run,owner,new).result(10)
        release.set();f.result(10)
    out['final']=store.run(run)['accounting'];return out
with tempfile.TemporaryDirectory() as t:
    store=Store('sqlite:///'+t+'/p.db',Fernet.generate_key())
    wf={'version':1,'name':'p','nodes':[{'id':'input','type':'chat_input'},{'id':'out','type':'response','inputs':{'text':'input.message'}}],'edges':[{'id':'a','source':'input','target':'out'}]}
    for method in ('reserve_accounting','record_accounting'):
        run=store.create_run(wf,'x')['id'];owner='o'+method;assert store.claim_next(owner,120)['id']==run
        store.reserve_accounting(run,owner,snap(0,0,{}))
        a={'a':{'prompt_tokens':10,'completion_tokens':0}};ab={**a,'b':{'prompt_tokens':20,'completion_tokens':0}}
        r1=race(store,run,owner,method,snap(1,10,a),snap(2,30,ab))
        print(method,'lower tokens :',r1['old'],'| final tokens',r1['final']['tokens_spent'],'actions',r1['final']['action_budget']['counter'])
        r2=race(store,run,owner,method,snap(2,30,ab),snap(3,30,ab))
        print(method,'equal tokens :',r2['old'],'| final tokens',r2['final']['tokens_spent'],'actions',r2['final']['action_budget']['counter'],'(newer was 3)')
    store.engine.dispose()
