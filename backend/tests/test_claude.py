import json
import httpx
import pytest
from backend.app import providers
from backend.app.registry import LLMConfig, Context, llm_node

@pytest.mark.asyncio
async def test_claude_native_messages_and_text_blocks(monkeypatch):
    async def handler(request):
        assert str(request.url)=='https://api.anthropic.com/v1/messages'
        assert request.headers['x-api-key']=='secret'
        assert request.headers['anthropic-version']=='2023-06-01'
        body=json.loads(request.content)
        assert body['system']=='System'
        assert body['messages']==[{'role':'user','content':'Hello'}]
        assert body['max_tokens']==2048
        return httpx.Response(200,json={'content':[{'type':'text','text':'First'},{'type':'text','text':'Second'}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(handler)))
    result=await llm_node({'prompt':'Hello'},LLMConfig(provider='claude',model='test-model',credential_id='key',system='System'),Context('',lambda _:'secret'))
    assert result=={'text':'First\nSecond','provider':'claude'}

@pytest.mark.asyncio
@pytest.mark.parametrize('status,body',[(401,{'error':'secret'}),(200,{'content':[]}), (200,{'content':[{'type':'text','text':None}]})])
async def test_claude_errors_do_not_expose_response_or_key(monkeypatch,status,body):
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(lambda _:httpx.Response(status,json=body))))
    with pytest.raises(ValueError) as exc:await providers.claude_chat('test','System','Hi','secret')
    assert 'secret' not in str(exc.value)
