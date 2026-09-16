import asyncio
import json
import pytest
import httpx
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from backend.app.storage import Store
from backend.app.tool_service import ToolService, ConnectionRecord, HTTPConfig, CONFIGS

@pytest.fixture
def service(tmp_path):
    return ToolService(Store('sqlite:///'+str(tmp_path/'tools.db'),Fernet.generate_key()))

def connection(service, provider='http', **kw):
    return service.save_connection({'name':'Example','provider':provider,'endpoint':'https://example.com','secret':'SECRET',**kw},'a')['id']

def test_encrypted_scoped_connections(service):
    id=connection(service)
    assert 'SECRET' not in json.dumps(service.connections('a'))
    with Session(service.store.engine) as s:
        assert 'SECRET' not in s.get(ConnectionRecord,id).encrypted
    with pytest.raises(ValueError):service.resolve(id,'b')
    with pytest.raises(KeyError):service.save_connection({'name':'changed','provider':'http','endpoint':'https://example.com'},'b',id)

def test_write_opt_in_and_config_secrets(service):
    id=connection(service)
    with pytest.raises(ValueError,match='enable_writes'):service.check(HTTPConfig(connection_id=id,method='POST'),'a')
    with pytest.raises(ValueError):HTTPConfig(api_key='bad')
    with pytest.raises(ValueError):service.check(CONFIGS['tool_github'](connection_id=id),'a')

@pytest.mark.asyncio
async def test_http_mock_and_redirects(service,monkeypatch):
    id=connection(service)
    monkeypatch.setattr('backend.app.tool_service.public_addresses',lambda host,port:['93.184.216.34'])
    seen=[]
    async def handler(request):
        seen.append(request)
        return httpx.Response(200,text='result')
    service.transport=httpx.MockTransport(handler)
    assert await service.execute('tool_http',{'connection_id':id,'path':'/items'},'hello','a')=='result'
    assert seen[0].url.host=='93.184.216.34'
    assert seen[0].headers['host']=='example.com'
    assert seen[0].headers['authorization']=='Bearer SECRET'

@pytest.mark.asyncio
async def test_private_network_denied(service):
    id=connection(service,endpoint='http://127.0.0.1:8000')
    with pytest.raises(ValueError,match='public'):await service.execute('tool_http',{'connection_id':id},'','a')

@pytest.mark.asyncio
async def test_python_isolation_and_cleanup(service,monkeypatch):
    calls=[]
    class Pipe:
        def write(self,data):pass
        async def drain(self):pass
        def close(self):pass
    class Proc:
        returncode=0
        def __init__(self):
            self.stdin=Pipe();self.stdout=asyncio.StreamReader();self.stderr=asyncio.StreamReader()
            self.stdout.feed_data(b'hello');self.stdout.feed_eof();self.stderr.feed_eof()
        async def wait(self):return 0
        async def communicate(self,data=None):return b'',b''
    async def fake(*args,**kwargs):calls.append(args);return Proc()
    monkeypatch.setattr(asyncio,'create_subprocess_exec',fake)
    assert await service.execute('tool_python',{},'hello','a')=='hello'
    command=calls[0]
    for flag in ('--network=none','--read-only','--cap-drop=ALL','--pids-limit=32','--memory=128m','--pull=never'):assert flag in command
    assert not any('volume' in x for x in command)
    assert calls[-1][1:3]==('rm','-f')

@pytest.mark.asyncio
@pytest.mark.parametrize('kind,provider,config,expected',[
 ('tool_github','github',{'repository':'org/repo','operation':'create_issue','title':'T','enable_writes':True},'/repos/org/repo/issues'),
 ('tool_jira','jira',{'operation':'comment','issue_key':'ABC-1','enable_writes':True},'/rest/api/3/issue/ABC-1/comment'),
 ('tool_confluence','confluence',{'operation':'create_page','space':'DOC','enable_writes':True},'/rest/api/content'),
])
async def test_remote_write_adapters_mock_only(service,monkeypatch,kind,provider,config,expected):
    id=connection(service,provider)
    monkeypatch.setattr('backend.app.tool_service.public_addresses',lambda *a:['93.184.216.34'])
    requests=[]
    async def handler(request):requests.append(request);return httpx.Response(201,json={'id':'1'})
    service.transport=httpx.MockTransport(handler)
    await service.execute(kind,{'connection_id':id,**config},'hello','a')
    assert requests[0].method=='POST' and requests[0].url.path==expected
    assert b'hello' in requests[0].content

@pytest.mark.asyncio
async def test_smtp_send_is_mocked(service,monkeypatch):
    id=connection(service,'email',endpoint='smtp.example.com',sender='from@example.com',username='user')
    monkeypatch.setattr('backend.app.tool_service.public_addresses',lambda *a:['93.184.216.34'])
    calls=[]
    class SMTP:
        def __init__(self,*a,**k):calls.append('connect')
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def ehlo(self):pass
        def starttls(self,**k):calls.append('tls')
        def login(self,*a):calls.append('login')
        def send_message(self,msg):calls.append(msg.get_content());return {}
    monkeypatch.setattr('backend.app.tool_service.smtplib.SMTP',SMTP)
    await service.execute('tool_email',{'connection_id':id,'to':'to@example.com','enable_writes':True},'hello','a')
    assert calls==['connect','tls','login','hello\n']

@pytest.mark.asyncio
@pytest.mark.parametrize('status,text,match',[(302,'redirect','HTTP 302'),(200,'x'*64001,'exceeds')])
async def test_response_bounds_redirect_denied(service,monkeypatch,status,text,match):
    id=connection(service)
    monkeypatch.setattr('backend.app.tool_service.public_addresses',lambda *a:['93.184.216.34'])
    service.transport=httpx.MockTransport(lambda req:httpx.Response(status,text=text,headers={'location':'http://127.0.0.1'}))
    with pytest.raises(ValueError,match=match):await service.execute('tool_http',{'connection_id':id},'','a')

def test_connection_api(service):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.app.tool_api import install_tool_routes
    app=FastAPI();install_tool_routes(app,service,lambda:'a');client=TestClient(app)
    result=client.post('/api/connections',json={'name':'A','provider':'http','endpoint':'https://example.com','secret':'SECRET'})
    assert result.status_code==200 and 'SECRET' not in result.text
    id=result.json()['id']
    assert client.put('/api/connections/'+id,json={'name':'B','provider':'http','endpoint':'https://example.com'}).status_code==200
    assert service.resolve(id,'a')['secret']=='SECRET'
    assert client.get('/api/connections').json()[0]['name']=='B'
