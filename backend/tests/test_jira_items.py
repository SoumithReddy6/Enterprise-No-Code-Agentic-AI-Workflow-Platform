import asyncio
import json
import pytest
from cryptography.fernet import Fernet
from backend.app.tool_service import ToolService
from backend.app.storage import Store
from backend.app.worker import Worker
from backend.app.registry import REGISTRY
from backend.app.compiler import type_warnings
from backend.app.models import Workflow


def issue(n=1, summary='Example'):
    return {'key':f'OPS-{n}','fields':{'summary':summary,'status':{'name':'Open'},'assignee':{'displayName':'Ada'},'updated':'2026-09-23','secret':'drop'}}


def normalize(payload,operation='search'):
    from backend.app.tool_service import jira_items
    return jira_items(json.dumps(payload),operation,'https://jira.example/team')


def test_search_stable_fields():
    result=normalize({'issues':[issue(i) for i in range(3)]})
    assert len(result['items'])==3 and not result['truncated']
    assert result['items'][0]=={'key':'OPS-0','summary':'Example','status':'Open','assignee':'Ada','updated':'2026-09-23','url':'https://jira.example/team/browse/OPS-0'}

@pytest.mark.parametrize('operation,payload,count',[('search',{'issues':[]},0),('get_issue',issue(),1),('create_issue',issue(),0),('comment',issue(),0)])
def test_operations(operation,payload,count):assert len(normalize(payload,operation)['items'])==count


def test_item_cap():
    result=normalize({'issues':[{'key':f'OPS-{i}'} for i in range(200)]})
    assert len(result['items'])==100 and result['truncated']

@pytest.mark.parametrize('payload',[{},None,[],{'issues':None},{'issues':[None,3,{}]}])
def test_malformed_response(payload):
    result=normalize(payload)
    assert result['items']==[] and result['warnings']


def test_size_budget_utf8_and_missing_fields():
    payload={'issues':[issue(i,'é'*1000) for i in range(20)]}
    text=json.dumps(payload,ensure_ascii=False)
    from backend.app.tool_service import jira_items
    result=jira_items(text,'search','https://jira.example')
    assert result['truncated']
    assert len(text.encode())+len(json.dumps(result['items'],ensure_ascii=False).encode())<=64000
    assert normalize({'key':'OPS-1'},'get_issue')['items'][0]['assignee'] is None


def workflow(connection,port='items'):
    return {'version':1,'name':'Jira items','nodes':[
      {'id':'input','type':'chat_input'},
      {'id':'jira','type':'tool_jira','config':{'connection_id':connection},'inputs':{'input':'input.message'}},
      {'id':'prompt','type':'prompt','config':{'template':'{message}'},'inputs':{'message':f'jira.{port}'}},
      {'id':'out','type':'response','inputs':{'text':'prompt.text'}}],
      'edges':[{'id':str(i),'source':a,'target':b} for i,(a,b) in enumerate([('input','jira'),('jira','prompt'),('prompt','out')])]}

@pytest.mark.asyncio
async def test_checkpoint_resume_and_legacy_text(tmp_path,monkeypatch):
    store=Store(f'sqlite:///{tmp_path}/jira.db',Fernet.generate_key())
    tools=ToolService(store)
    connection=tools.save_connection({'name':'Jira','provider':'jira','endpoint':'https://jira.example'})['id']
    raw=json.dumps({'issues':[issue()]},indent=2);calls=[]
    async def http(*args,**kw):calls.append(1);return raw
    monkeypatch.setattr(ToolService,'_http',http)
    doc=workflow(connection)
    warnings=type_warnings(Workflow.model_validate(doc))
    assert len(warnings)==1 and 'array<object>' in warnings[0] and 'conversion to text' in warnings[0]
    assert type_warnings(Workflow.model_validate(workflow(connection,'text')))==[]
    worker=Worker(store)
    original=REGISTRY['prompt'].handler
    async def fail(*args):raise ValueError('crash after Jira')
    monkeypatch.setattr(REGISTRY['prompt'],'handler',fail)
    run=store.create_run(doc,'query')
    await worker.execute(store.claim_next(worker.owner))
    saved=store.run(run['id']);assert saved['status']=='failed'
    event=next(e for e in saved['events'] if e.get('node_id')=='jira' and e['status']=='success')
    assert event['items_truncated'] is False and event['items_warnings']==[]
    assert saved['checkpoints']['jira']['text']==raw
    assert isinstance(saved['checkpoints']['jira']['items'],list)
    seen=[]
    async def prompt(inputs,config,ctx):seen.append(inputs['message']);return await original(inputs,config,ctx)
    monkeypatch.setattr(REGISTRY['prompt'],'handler',prompt)
    store.resume_run(run['id'])
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['status']=='success'
    assert seen==[saved['checkpoints']['jira']['items']] and len(calls)==1
    run=store.create_run(workflow(connection,'text'),'query')
    await worker.execute(store.claim_next(worker.owner))
    assert store.run(run['id'])['output']==raw

@pytest.mark.asyncio
async def test_agent_observation_is_unchanged_but_event_has_items(monkeypatch):
    from backend.tests.test_agent_tools import calc_flow,scripted
    from backend.app.compiler import compile_workflow
    from backend.app.tool_service import jira_items
    flow=calc_flow();flow.nodes[2].type='tool_jira';flow.nodes[2].config={}
    raw=json.dumps({'issues':[issue()]},indent=2);seen=[];events=[]
    scripted(monkeypatch,['{"action":"call","target":"calc","input":"query"}','{"action":"final","text":"Done"}'],seen)
    async def platform(action,*args):
        if action=='tool':return {'text':raw,**jira_items(raw,'search','https://jira.example')}
        raise AssertionError(action)
    async def emit(e):events.append(e)
    await compile_workflow(flow,platform_resolver=platform,emit=emit).graph.ainvoke({'values':{}})
    assert json.dumps({'tool_result':raw}) in seen[1]
    success=next(e for e in events if e.get('node_id')=='calc' and e['status']=='success')
    assert success['outputs']['items'][0]['key']=='OPS-1'
    assert success['outputs']['text']==raw


def test_warning_api_saves_and_runs(tmp_path,monkeypatch):
    from backend.app.main import create_app
    from fastapi.testclient import TestClient
    app=create_app(f'sqlite:///{tmp_path}/api.db',Fernet.generate_key(),auth_enabled=False,embedded_worker=False)
    connection=ToolService(app.state.store).save_connection({'name':'Jira','provider':'jira','endpoint':'https://jira.example'})['id']
    async def http(*a,**kw):return json.dumps({'issues':[]})
    monkeypatch.setattr(ToolService,'_http',http)
    with TestClient(app) as client:
        catalog=client.get('/api/nodes').json()
        assert next(n for n in catalog if n['type']=='tool_jira')['outputs']=={'text':'string','items':'array<object>'}
        doc=workflow(connection)
        result=client.post('/api/validate',json=doc).json()
        assert result['valid'] and result['errors']==[] and len(result['warnings'])==1
        assert client.post('/api/workflows',json=doc).status_code==201
        response=client.post('/api/runs',json={'workflow':doc,'message':'query'})
        assert response.status_code==201
        worker=Worker(app.state.store)
        asyncio.run(worker.execute(app.state.store.claim_next(worker.owner)))
        assert client.get('/api/runs/'+response.json()['id']).json()['status']=='success'

@pytest.mark.parametrize('raw',['not-json','['*1100+']'*1100,'{"issues":[{"key":"\\ud800"}]}'])
def test_malformed_json_never_crashes(raw):
    from backend.app.tool_service import jira_items
    result=jira_items(raw,'search','https://jira.example')
    assert result['items']==[] and result['warnings']


def test_pagination_is_reported():
    assert normalize({'issues':[issue()],'nextPageToken':'next'})['truncated']
    assert normalize({'issues':[issue()],'total':10,'startAt':0})['truncated']

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['endpoint','delete'])
async def test_projection_uses_request_connection_snapshot(tmp_path,monkeypatch,change):
    from sqlalchemy.orm import Session
    from backend.app.tool_service import ConnectionRecord
    store=Store(f'sqlite:///{tmp_path}/race.db',Fernet.generate_key())
    tools=ToolService(store)
    connection=tools.save_connection({'name':'Jira','provider':'jira','endpoint':'https://original.example'})['id']
    async def http(*a,**kw):
        if change=='endpoint':tools.save_connection({'name':'Jira','provider':'jira','endpoint':'https://changed.example'},id=connection)
        else:
            with Session(store.engine) as s:s.delete(s.get(ConnectionRecord,connection));s.commit()
        return json.dumps({'issues':[issue()]})
    monkeypatch.setattr(ToolService,'_http',http)
    run=store.create_run(workflow(connection),'query');worker=Worker(store)
    await worker.execute(store.claim_next(worker.owner))
    result=store.run(run['id'])
    assert result['status']=='success'
    assert result['checkpoints']['jira']['items'][0]['url']=='https://original.example/browse/OPS-1'
