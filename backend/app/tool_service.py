"""Encrypted workspace connections and bounded, explicitly configured tool adapters."""
import asyncio
import base64
import ipaddress
import json
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Literal
from urllib.parse import quote, urlsplit
import httpx
from pydantic import Field
from sqlalchemy import Column, String, Text, select
from sqlalchemy.orm import Session
from .models import StrictModel
from .storage import Base, new_id

# 408/425/429 and 5xx are the provider asking for another attempt.
RETRYABLE_TOOL_STATUS=frozenset({408,425,429,500,502,503,504,529})

class UncertainWriteError(ValueError):
    """An external mutation may have completed; automatic retries must stop."""

class TransientToolError(ValueError):
    """Infrastructure failed, not the request. Only this class is eligible for node retries.

    A syntax error, a permission denial or a contract violation would fail identically on
    a second attempt, so they stay plain ValueErrors and surface immediately.
    """

class ConnectionRecord(Base):
    __tablename__='tool_connections'
    id=Column(String(64),primary_key=True)
    tenant_id=Column(String(64),nullable=False,index=True)
    encrypted=Column(Text,nullable=False)

TENANT_MODELS=[ConnectionRecord]
class ConnectionConfig(StrictModel):
    name:str=Field(min_length=1,max_length=120)
    provider:Literal['http','email','jira','confluence','github','elasticsearch','pinecone']
    endpoint:str=Field(min_length=1,max_length=2048)
    username:str=Field(default='',max_length=300)
    secret:str=Field(default='',max_length=8192)
    port:int=Field(default=587,ge=1,le=65535)
    sender:str=Field(default='',max_length=300)

class ToolConfig(StrictModel):
    connection_id:str=''
    enable_writes:bool=False
    approval:bool|None=Field(default=None,description="Require human approval before sending. Unset defaults on for authenticated writes.")
    description:str=Field(default='',max_length=500)  # Shown to an agent that may call this tool.
class HTTPConfig(ToolConfig):
    method:Literal['GET','POST','PUT','PATCH','DELETE']='GET'
    path:str=Field(default='/',max_length=2000)
    body:str=Field(default='',max_length=64000)
class EmailConfig(ToolConfig):
    to:str=Field(default='',max_length=1000)
    subject:str=Field(default='Agent message',max_length=300)
    body:str=Field(default='{input}',max_length=64000)
class JiraConfig(ToolConfig):
    operation:Literal['search','get_issue','create_issue','comment']='search'
    project:str=''
    issue_key:str=''
    jql:str=''
class ConfluenceConfig(ToolConfig):
    operation:Literal['search','get_page','create_page']='search'
    space:str=''
    page_id:str=''
    title:str=''
    query:str=''
class GithubConfig(ToolConfig):
    operation:Literal['get_issue','list_issues','create_issue','comment']='list_issues'
    repository:str=''
    issue_number:int=Field(default=1,ge=1)
    title:str=''
class PythonConfig(StrictModel):
    code:str=Field(default='print(input_text)',min_length=1,max_length=32000)
    timeout_seconds:int=Field(default=10,ge=1,le=30)
    description:str=Field(default='',max_length=500)  # Tell calling agents what input_text must contain.
CONFIGS={'tool_http':HTTPConfig,'tool_email':EmailConfig,'tool_jira':JiraConfig,'tool_confluence':ConfluenceConfig,'tool_github':GithubConfig,'tool_python':PythonConfig}

def public_addresses(host,port):
    """Validate every DNS answer and pin HTTP connections to a validated address."""
    try:addresses=list(dict.fromkeys(r[4][0] for r in socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
    except OSError:raise ValueError('Connection hostname could not be resolved') from None
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise ValueError('Connections must resolve only to public addresses; private, loopback and metadata networks are denied')
    return addresses

def is_write(node_type,config):
    c=config.model_dump() if hasattr(config,'model_dump') else config
    return node_type=='tool_email' or (node_type=='tool_http' and c.get('method','GET')!='GET') or c.get('operation') in ('create_issue','comment','create_page')

def jira_items(text,operation,endpoint):
    """Add a bounded projection without changing the legacy response text.

    Text plus nonempty serialized items share 64 KB. Empty collections and
    fixed diagnostic metadata add only constant framing overhead.
    """
    result={'items':[],'truncated':False,'warnings':[]}
    if operation not in ('search','get_issue'):return result
    try:data=json.loads(text)
    except (ValueError,TypeError,RecursionError):
        result['warnings']=['Jira response was not valid JSON; items is empty.'];return result
    rows=data.get('issues') if operation=='search' and isinstance(data,dict) else [data] if operation=='get_issue' else None
    if not isinstance(rows,list):
        result['warnings']=['Jira response has no issues array; items is empty.'];return result
    budget=max(0,64000-len(text.encode('utf-8')))
    size=2
    def usable_string(value):
        if not isinstance(value,str):return False
        try:value.encode('utf-8');return True
        except UnicodeEncodeError:return False
    for row in rows:
        if not isinstance(row,dict) or not usable_string(row.get('key')) or not row['key']:
            if not result['warnings']:result['warnings'].append('Jira response contains malformed issues; invalid entries were skipped.')
            continue
        if len(result['items'])>=100:result['truncated']=True;break
        fields=row.get('fields') if isinstance(row.get('fields'),dict) else {}
        def field(name,nested=None):
            value=fields.get(name)
            if nested:value=value.get(nested) if isinstance(value,dict) else None
            return value if usable_string(value) else None
        item={'key':row['key'],'summary':field('summary'),'status':field('status','name'),
              'assignee':field('assignee','displayName'),'updated':field('updated'),
              'url':endpoint.rstrip('/')+'/browse/'+quote(row['key'],safe='')}
        added=len(json.dumps(item,ensure_ascii=False).encode('utf-8'))+(2 if result['items'] else 0)
        if size+added>budget:result['truncated']=True;break
        result['items'].append(item);size+=added
    if operation=='search' and isinstance(data,dict):
        total=data.get('total');start=data.get('startAt',0)
        if data.get('nextPageToken') or data.get('isLast') is False or (isinstance(total,int) and isinstance(start,int) and total>start+len(rows)):
            result['truncated']=True
    return result

class ToolService:
    max_output=64000
    def __init__(self,store):
        self.store=store
        self.transport=None

    def resolve(self,id,tenant_id='local'):
        with Session(self.store.engine) as s:
            row=s.get(ConnectionRecord,id)
            if not row or row.tenant_id!=tenant_id:raise ValueError('Connection unavailable in this workspace')
            try:return json.loads(self.store.cipher.decrypt(row.encrypted.encode()))
            except Exception:raise ValueError('Connection cannot be decrypted; restore the encryption key') from None

    def _public(self,id,data):return {k:v for k,v in data.items() if k!='secret'}|{'id':id,'has_secret':bool(data.get('secret'))}
    def connections(self,tenant_id='local'):
        with Session(self.store.engine) as s:ids=list(s.scalars(select(ConnectionRecord.id).where(ConnectionRecord.tenant_id==tenant_id)))
        return [self._public(id,self.resolve(id,tenant_id)) for id in ids]

    def save_connection(self,data,tenant_id='local',id=None):
        c=ConnectionConfig.model_validate(data)
        if c.provider=='email':
            if not re.fullmatch(r'[A-Za-z0-9.-]+',c.endpoint):raise ValueError('SMTP endpoint must be a hostname')
        else:
            u=urlsplit(c.endpoint)
            if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError('Endpoint must be an HTTP(S) URL without credentials, query or fragment')
        with Session(self.store.engine) as s:
            row=s.get(ConnectionRecord,id) if id else None
            if id and (not row or row.tenant_id!=tenant_id):raise KeyError(id)
            if row:
                old=json.loads(self.store.cipher.decrypt(row.encrypted.encode()))
                if old['provider']!=c.provider:raise ValueError('Connection provider cannot be changed')
                if not c.secret:c.secret=old.get('secret','')
            else:row=ConnectionRecord(id=new_id(),tenant_id=tenant_id);s.add(row)
            data=c.model_dump();row.encrypted=self.store.cipher.encrypt(json.dumps(data).encode()).decode();s.commit()
            return self._public(row.id,data)

    def check(self,config,tenant_id='local'):
        if isinstance(config,PythonConfig):return
        node_type=next((t for t,cls in CONFIGS.items() if isinstance(config,cls)),None)
        if not node_type:raise ValueError('Unknown tool configuration')
        connection=self.resolve(config.connection_id,tenant_id)
        if connection['provider']!=node_type.removeprefix('tool_'):raise ValueError('Connection provider does not match this tool')
        if is_write(node_type,config) and not config.enable_writes:raise ValueError('This operation requires explicit enable_writes consent')
        if isinstance(config,HTTPConfig):
            if not config.path.startswith('/') or config.path.startswith('//') or '\\' in config.path or '#' in config.path:raise ValueError('HTTP path must be relative to the connection endpoint')
        if isinstance(config,GithubConfig) and not re.fullmatch(r'[\w.-]+/[\w.-]+',config.repository):raise ValueError('GitHub repository must be owner/repository')
        if isinstance(config,JiraConfig):
            if config.operation in ('get_issue','comment') and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*-[0-9]+',config.issue_key):raise ValueError('Choose a valid Jira issue key')
            if config.operation=='create_issue' and not config.project:raise ValueError('Jira create_issue requires a project key')
        if isinstance(config,ConfluenceConfig):
            if config.operation=='get_page' and not config.page_id.isdigit():raise ValueError('Confluence get_page requires a numeric page ID')
            if config.operation=='create_page' and not config.space:raise ValueError('Confluence create_page requires a space key')
        if isinstance(config,EmailConfig):
            if not config.to or not connection['sender']:raise ValueError('Email requires recipients and a connection sender')
            if any('\n' in x or '\r' in x for x in (config.to,config.subject,connection['sender'])):raise ValueError('Email headers cannot contain newlines')

    async def _http(self,c,method,path,body=None,params=None):
        u=httpx.URL(c['endpoint'].rstrip('/')+path)
        addresses=await asyncio.to_thread(public_addresses,u.host,u.port or (443 if u.scheme=='https' else 80))
        headers={'Host':u.netloc.decode(),'Accept':'application/json'}
        if c['secret']:
            headers['Authorization']=('Basic '+base64.b64encode((c['username']+':'+c['secret']).encode()).decode()) if c['username'] else 'Bearer '+c['secret']
        pinned=u.copy_with(host=addresses[0])
        try:
            async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False,transport=self.transport) as client:
                async with client.stream(method,pinned,headers=headers,json=body,params=params,extensions={'sni_hostname':u.host}) as response:
                    if response.status_code in RETRYABLE_TOOL_STATUS:raise TransientToolError(f'Tool provider returned HTTP {response.status_code}')
                    if response.status_code>=300:raise ValueError(f'Tool provider returned HTTP {response.status_code}')
                    output=bytearray()
                    async for block in response.aiter_bytes():
                        output.extend(block)
                        if len(output)>self.max_output:raise ValueError('Tool response exceeds 64000 bytes; narrow the request')
                    return output.decode('utf-8',errors='replace')
        except (httpx.TimeoutException,httpx.ConnectError,httpx.ReadError,httpx.WriteError,httpx.PoolTimeout):
            raise TransientToolError('Tool request did not complete; the provider was unreachable or timed out') from None
        except httpx.HTTPError:raise ValueError('Tool HTTP request failed; check connection and provider availability') from None

    def prepare(self,node_type,config_dict,input_text,tenant_id='local'):
        if node_type not in CONFIGS:raise ValueError('Unknown tool type')
        config=CONFIGS[node_type].model_validate(config_dict);self.check(config,tenant_id)
        if len(input_text.encode())>64000:raise ValueError('Tool input exceeds 64000 bytes')
        if node_type=='tool_python':raise ValueError('Python does not use external request preparation')
        c=self.resolve(config.connection_id,tenant_id)
        if node_type=='tool_email':
            payload={'transport':'smtp','method':'SEND','url':f"smtp://{c['endpoint']}:{c['port']}",'sender':c['sender'],'recipient':config.to,'subject':config.subject,'body':config.body.replace('{input}',input_text)}
            return {'connection_id':config.connection_id,'connection':c,'payload':payload}
        method='GET';body=None;params=None
        if node_type=='tool_http':
            method=config.method;path=config.path
            rendered=config.body.replace('{input}',input_text)
            if rendered:
                try:body=json.loads(rendered)
                except ValueError:body={'input':rendered}
        elif node_type=='tool_github':
            path='/repos/'+config.repository+'/issues'
            if config.operation=='get_issue':path+='/'+str(config.issue_number)
            elif config.operation=='list_issues':params={'per_page':30,'state':'open'}
            elif config.operation=='create_issue':method='POST';body={'title':config.title or input_text[:100],'body':input_text}
            else:path+='/'+str(config.issue_number)+'/comments';method='POST';body={'body':input_text}
        elif node_type=='tool_jira':
            path='/rest/api/3/issue'
            if config.operation=='search':path='/rest/api/3/search/jql';params={'jql':config.jql or input_text,'maxResults':20}
            elif config.operation=='get_issue':path+='/'+quote(config.issue_key,safe='')
            elif config.operation=='create_issue':
                method='POST';body={'fields':{'project':{'key':config.project},'summary':input_text[:200],'issuetype':{'name':'Task'}}}
            else:
                path+='/'+quote(config.issue_key,safe='')+'/comment';method='POST';body={'body':{'type':'doc','version':1,'content':[{'type':'paragraph','content':[{'type':'text','text':input_text}]}]}}
        else:
            path='/rest/api/content'
            if config.operation=='search':path='/rest/api/content/search';params={'cql':config.query or input_text,'limit':20}
            elif config.operation=='get_page':path+='/'+quote(config.page_id,safe='');params={'expand':'body.storage'}
            else:
                from html import escape
                method='POST';body={'type':'page','title':config.title or input_text[:100],'space':{'key':config.space},'body':{'storage':{'value':'<p>'+escape(input_text)+'</p>','representation':'storage'}}}
        payload={'transport':'http','method':method,'url':str(httpx.Request(method,c['endpoint'].rstrip('/')+path,params=params).url),'body':body}
        return {'connection_id':config.connection_id,'connection':c,'payload':payload,'path':path,'params':params}

    def validate_prepared(self,prepared,tenant_id='local'):
        if self.resolve(prepared['connection_id'],tenant_id)!=prepared['connection']:
            raise ValueError('The connection changed after approval was requested. Start a new run to review the updated destination.')

    async def execute_prepared(self,prepared,tenant_id='local'):
        self.validate_prepared(prepared,tenant_id)
        c=prepared['connection'];payload=prepared['payload']
        if payload['transport']=='smtp':
            config=EmailConfig(to=payload['recipient'],subject=payload['subject'],body=payload['body'])
            return await asyncio.to_thread(self._email,c,config,'',True)
        return await self._http(c,payload['method'],prepared['path'],payload['body'],prepared['params'])

    async def execute(self,node_type,config_dict,input_text,tenant_id='local'):
        if len(input_text.encode())>64000:raise ValueError('Tool input exceeds 64000 bytes')
        if node_type=='tool_python':return await self._python(PythonConfig.model_validate(config_dict),input_text)
        return await self.execute_prepared(self.prepare(node_type,config_dict,input_text,tenant_id),tenant_id)

    def _email(self,c,config,text,prepared=False):
        addresses=public_addresses(c['endpoint'],c['port'])
        message=EmailMessage();message['From']=c['sender'];message['To']=config.to;message['Subject']=config.subject;message.set_content(config.body if prepared else config.body.replace('{input}',text))
        # Pin the socket while retaining the original hostname for TLS verification.
        class PinnedSMTP(smtplib.SMTP):
            def _get_socket(self,host,port,timeout):return socket.create_connection((addresses[0],port),timeout)
        try:
            with PinnedSMTP(c['endpoint'],c['port'],timeout=15) as smtp:
                smtp.ehlo();smtp.starttls(context=ssl.create_default_context());smtp.ehlo()
                if c['username']:smtp.login(c['username'],c['secret'])
                refused=smtp.send_message(message)
                if refused:raise ValueError('SMTP rejected one or more recipients; verify delivery before retrying')
        except (OSError,smtplib.SMTPException):raise ValueError('SMTP delivery failed; verify delivery before retrying') from None
        return 'Email accepted by SMTP server.'

    async def _python(self,config,text):
        name='relay-tool-'+new_id()
        command=['docker','run','--rm','--pull=never','--name',name,'--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges','--pids-limit=32','--memory=128m','--cpus=0.5','--ulimit','fsize=65536:65536','--user=65534:65534','--tmpfs','/tmp:rw,noexec,nosuid,size=16m','-i','python:3.12-alpine','python','-I','-c',"import json,sys,resource;resource.setrlimit(resource.RLIMIT_CPU,(5,5));p=json.load(sys.stdin);input_text=p['input'];exec(compile(p['code'],'<tool>','exec'))"]
        process=None
        try:
            process=await asyncio.create_subprocess_exec(*command,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            # Docker pipe output is bounded by a concurrent reader, never communicate() on untrusted output.
            async def read(stream):
                data=bytearray()
                while True:
                    chunk=await stream.read(4096)
                    if not chunk:return bytes(data)
                    data.extend(chunk)
                    if len(data)>self.max_output:raise ValueError('Python output exceeds 64000 bytes')
            async def run():
                process.stdin.write(json.dumps({'input':text,'code':config.code}).encode());await process.stdin.drain();process.stdin.close()
                out,err=await asyncio.gather(read(process.stdout),read(process.stderr));await process.wait();return out,err
            out,err=await asyncio.wait_for(run(),config.timeout_seconds)
            if process.returncode:raise ValueError('Python container failed: '+err.decode(errors='replace')[:1000])
            return out.decode(errors='replace')
        except FileNotFoundError:raise ValueError('Python tools require Docker and a locally installed python:3.12-alpine image; no host execution fallback') from None
        except asyncio.TimeoutError:raise ValueError('Python tool exceeded its time limit') from None
        finally:
            if process:
                cleanup=await asyncio.create_subprocess_exec('docker','rm','-f',name,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                try:await asyncio.wait_for(cleanup.communicate(),5)
                except asyncio.TimeoutError:cleanup.kill()
