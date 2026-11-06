"""Design-review experiments only; no application implementation or remote services."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tempfile
import threading
from typing import Annotated, TypedDict

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from cryptography.fernet import Fernet
from langgraph.graph import StateGraph, START, END
from backend.app.agent_memory import MemoryService
from backend.app.registry import Context, LLMConfig, llm_node
from backend.app import providers, token_budget
from backend.app.storage import Store

class State(TypedDict):
    values: Annotated[dict, lambda a,b:{**a,**b}]

async def langgraph_probes():
    started=asyncio.Event();cancelled=asyncio.Event()
    async def failing(state):
        await started.wait();raise ValueError('branch failed')
    async def sibling(state):
        started.set()
        try:await asyncio.Event().wait()
        except asyncio.CancelledError:cancelled.set();raise
    graph=StateGraph(State)
    graph.add_node('fork',lambda s:{'values':{}})
    graph.add_node('a',failing);graph.add_node('b',sibling)
    graph.add_edge(START,'fork');graph.add_edge('fork','a');graph.add_edge('fork','b')
    graph.add_edge('a',END);graph.add_edge('b',END)
    try:await asyncio.wait_for(graph.compile().ainvoke({'values':{}}),5)
    except ValueError as exc:assert str(exc)=='branch failed'
    assert cancelled.is_set()
    counts={}
    for mode in ['list_join','plain_fan_in']:
        seen=[];graph=StateGraph(State)
        graph.add_node('fork',lambda s:{'values':{}})
        graph.add_node('a',lambda s:{'values':{'a':True}})
        graph.add_node('b1',lambda s:{'values':{'b1':True}})
        graph.add_node('b2',lambda s:{'values':{'b2':True}})
        def join(state):seen.append(sorted(state['values']));return {'values':{}}
        graph.add_node('join',join)
        graph.add_edge(START,'fork');graph.add_edge('fork','a');graph.add_edge('fork','b1');graph.add_edge('b1','b2')
        if mode=='list_join':graph.add_edge(['a','b2'],'join')
        else:graph.add_edge('a','join');graph.add_edge('b2','join')
        graph.add_edge('join',END)
        await asyncio.wait_for(graph.compile().ainvoke({'values':{}}),5)
        counts[mode]={'join_calls':len(seen),'inputs_seen':seen}
    assert counts['list_join']['join_calls']==1 and counts['plain_fan_in']['join_calls']==2
    return {'sibling_cancelled':True,**counts}

async def token_formula_probe():
    """Apply the draft's reservation formula, then call the real usage accounting seam.

    The transport is a fixture. The draft reservation logic is a model, not implemented
    application behavior. Provider counts stay within the declared output cap.
    """
    original=providers.ollama_chat
    both=asyncio.Event();calls=0;reserved=0;ceiling=1000;run={}
    async def fixture(model,system,prompt,usage=None,**params):
        nonlocal calls
        assert params['max_tokens']==300
        calls+=1
        if calls==2:both.set()
        await both.wait()
        usage.update(prompt_tokens=600,completion_tokens=100)
        return 'answer'
    async def one(name):
        nonlocal reserved
        cap=300
        assert token_budget.tokens_spent(run)+reserved+cap<=ceiling
        reserved+=cap
        ctx=Context('',lambda _: '',node_id=name,checkpoint_owner=name,run=run)
        await llm_node({'prompt':'fixture input'},LLMConfig(provider='ollama',model='fixture',max_tokens=cap),ctx)
        reserved-=cap
    providers.ollama_chat=fixture
    try:await asyncio.wait_for(asyncio.gather(one('a'),one('b')),5)
    finally:providers.ollama_chat=original
    actual=token_budget.tokens_spent(run)
    assert actual==1400 and actual>ceiling
    return {'draft_formula_admitted_calls':calls,'ceiling':ceiling,'reserved_per_call':300,
            'reported_input_tokens_per_call':600,'reported_output_tokens_per_call':100,
            'actual_total_tokens':actual}

async def reservation_contention_probe():
    started=asyncio.Event();cancelled=asyncio.Event();reserved=0
    async def admitted(state):
        nonlocal reserved
        reserved+=600;started.set()
        try:await asyncio.Event().wait()
        except asyncio.CancelledError:cancelled.set();raise
    async def refused(state):
        await started.wait()
        assert reserved+600>1000
        raise ValueError('draft fail-fast reservation exhausted')
    graph=StateGraph(State)
    graph.add_node('fork',lambda s:{'values':{}})
    graph.add_node('a',admitted);graph.add_node('b',refused)
    graph.add_edge(START,'fork');graph.add_edge('fork','a');graph.add_edge('fork','b')
    graph.add_edge('a',END);graph.add_edge('b',END)
    try:await asyncio.wait_for(graph.compile().ainvoke({'values':{}}),5)
    except ValueError as exc:assert str(exc)=='draft fail-fast reservation exhausted'
    assert started.is_set() and cancelled.is_set()
    return {'ceiling':1000,'per_call_reservation':600,
            'first_branch_started':True,'first_branch_cancelled_by_second_refusal':True}

def storage_probes(directory):
    store=Store('sqlite:///'+str(directory/'review.db'),Fernet.generate_key())
    try:
        workflow={'version':1,'name':'review','nodes':[{'id':'input','type':'chat_input'},
            {'id':'out','type':'response','inputs':{'text':'input.message'}}],
            'edges':[{'id':'a','source':'input','target':'out'}]}
        row=store.create_run(workflow,'synthetic review')
        owner='review-owner';assert store.claim_next(owner,lease_seconds=120)['id']==row['id']
        barrier=threading.Barrier(8)
        def checkpoint(index):
            barrier.wait(timeout=10)
            assert store.worker_event(row['id'],owner,{'node_id':f'branch{index}','status':'success','outputs':{'text':str(index)}})
        with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(checkpoint,range(8)))
        found=store.run(row['id'])['checkpoints'];assert all(f'branch{i}' in found for i in range(8))
        old={'action_budget':{'counter':1,'remaining':9,'ceiling':10},'tokens_spent':10,'usage':{'a':{'prompt_tokens':10,'completion_tokens':0}},'usage_by_invocation':{}}
        new={'action_budget':{'counter':2,'remaining':8,'ceiling':10},'tokens_spent':30,'usage':{'a':{'prompt_tokens':10,'completion_tokens':0},'b':{'prompt_tokens':20,'completion_tokens':0}},'usage_by_invocation':{}}
        accounting={}
        for method in ['reserve_accounting','record_accounting']:
            release_old=threading.Event();old_ready=threading.Event()
            def delayed_old():
                old_ready.set();assert release_old.wait(timeout=10)
                getattr(store,method)(row['id'],owner,old)
            with ThreadPoolExecutor(max_workers=2) as pool:
                future=pool.submit(delayed_old);assert old_ready.wait(timeout=10)
                pool.submit(getattr(store,method),row['id'],owner,new).result(timeout=10)
                assert store.run(row['id'])['accounting']['tokens_spent']==30
                release_old.set();future.result(timeout=10)
            saved=store.run(row['id'])['accounting'];assert saved==old
            accounting[method]={'new_snapshot_tokens':30,'final_tokens':saved['tokens_spent'],
                                'new_snapshot_actions':2,'final_actions':saved['action_budget']['counter']}
        memory=MemoryService(store);reads=[json.loads(memory.read('shared','local')) for _ in range(2)]
        assert reads==[[],[]]
        barrier=threading.Barrier(2)
        def write_memory(index):barrier.wait(timeout=10);memory.write('shared',f'input{index}',f'answer{index}','local')
        with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(write_memory,range(2)))
        messages=json.loads(memory.read('shared','local'))
        assert {m['input'] for m in messages}=={'input0','input1'}
        return {'concurrent_checkpoint_count':len(found),'stale_accounting':accounting,
                'same_key_memory':{'initial_context_lengths':[len(r) for r in reads],
                                   'retained_messages':len(messages),'lost_update':False}}
    finally:store.engine.dispose()

async def main():
    design=ROOT/'docs/superpowers/specs/2026-10-24-parallel-branches-design.md'
    before=hashlib.sha256(design.read_bytes()).hexdigest()
    result={'langgraph_version':version('langgraph'),'langgraph':await langgraph_probes(),
            'draft_token_formula':await token_formula_probe(),
            'reservation_contention':await reservation_contention_probe()}
    with tempfile.TemporaryDirectory(prefix='relay-a53-design-review-') as temp:
        result['sqlite_storage']=await asyncio.to_thread(storage_probes,Path(temp))
    result['design_unchanged']=hashlib.sha256(design.read_bytes()).hexdigest()==before
    assert result['design_unchanged']
    (Path(__file__).parent/'results.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':asyncio.run(main())
