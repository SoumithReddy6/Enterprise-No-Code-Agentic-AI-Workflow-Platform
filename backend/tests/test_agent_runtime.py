import json
import pytest
from backend.app.registry import Context,REGISTRY,LLMConfig
from backend.app.compiler import compile_workflow
from backend.tests.test_platform_graph import platform_flow

@pytest.mark.asyncio
async def test_agent_delegates_only_to_attached_specialist(monkeypatch):
    from backend.app import registry
    responses=iter(['{"action":"call","target":"helper","input":"Review this"}','A useful critique','{"action":"final","text":"Improved answer"}'])
    async def model(*args):return {'text':next(responses),'provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    events=[]
    async def emit(e):events.append(e)
    graph=compile_workflow(platform_flow(),message='Task',emit=emit).graph
    result=await graph.ainvoke({'values':{}})
    assert result['values']['out']['text']=='Improved answer'
    specialist=[e for e in events if e.get('node_id')=='helper']
    assert specialist[0]['status']=='running' and specialist[-1]['status']=='success'
    assert all(e['transient'] for e in specialist)

@pytest.mark.asyncio
async def test_agent_rejects_unattached_target(monkeypatch):
    from backend.app import registry
    async def model(*args):return {'text':'{"action":"call","target":"secret","input":"Read"}','provider':'demo'}
    monkeypatch.setattr(registry,'llm_node',model)
    with pytest.raises(ValueError,match='unattached'):
        await compile_workflow(platform_flow()).graph.ainvoke({'values':{}})

@pytest.mark.asyncio
async def test_ollama_hyperparameters_reach_native_api(monkeypatch):
    import httpx
    from backend.app import providers
    seen=[];original=httpx.AsyncClient
    def response(request):
        seen.append(json.loads(request.content));return httpx.Response(200,json={'message':{'content':'OK'}})
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(response)))
    from backend.app.registry import llm_node
    await llm_node({'prompt':'Q'},LLMConfig(provider='ollama',model='tiny',temperature=.3,top_p=.8,max_tokens=100),Context('',lambda _:''))
    assert seen[0]['options']=={'num_predict':100,'temperature':.3,'top_p':.8}
