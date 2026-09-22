import json
import httpx
import pytest
from backend.app import providers
from backend.app.registry import LLMConfig, Context, llm_node

@pytest.mark.asyncio
async def test_openai_chat_completions_request_shape(monkeypatch):
    async def handler(request):
        assert str(request.url)=='https://api.openai.com/v1/chat/completions'
        assert request.headers['authorization']=='Bearer secret'
        body=json.loads(request.content)
        assert body['model']=='test-model'
        assert body['messages']==[{'role':'system','content':'System'},{'role':'user','content':'Hello'}]
        # OpenAI takes max_completion_tokens, never max_tokens.
        assert body['max_completion_tokens']==2048 and 'max_tokens' not in body
        return httpx.Response(200,json={'choices':[{'message':{'content':'Answer'}}],
                                        'usage':{'prompt_tokens':11,'completion_tokens':7}})
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(handler)))
    result=await llm_node({'prompt':'Hello'},LLMConfig(provider='openai',model='test-model',credential_id='key',system='System'),Context('',lambda _:'secret'))
    assert result=={'text':'Answer','provider':'openai'}

@pytest.mark.asyncio
async def test_openai_records_token_usage(monkeypatch):
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(
        lambda _:httpx.Response(200,json={'choices':[{'message':{'content':'Answer'}}],
                                          'usage':{'prompt_tokens':11,'completion_tokens':7}}))))
    usage={}
    assert await providers.openai_chat('test','System','Hi','secret',usage=usage)=='Answer'
    assert usage=={'prompt_tokens':11,'completion_tokens':7}

@pytest.mark.asyncio
@pytest.mark.parametrize('status,body',[(401,{'error':'secret'}),(200,{'choices':[]}),
                                        (200,{'choices':[{'message':{'content':None}}]}),
                                        (200,{'choices':[{'message':{'content':'  '}}]})])
async def test_openai_errors_do_not_expose_response_or_key(monkeypatch,status,body):
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(lambda _:httpx.Response(status,json=body))))
    with pytest.raises(ValueError) as exc:await providers.openai_chat('test','System','Hi','secret')
    assert 'secret' not in str(exc.value)

@pytest.mark.asyncio
@pytest.mark.parametrize('status',sorted(providers.RETRYABLE_STATUS))
async def test_openai_transient_statuses_are_retryable(monkeypatch,status):
    original=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:original(transport=httpx.MockTransport(
        lambda _:httpx.Response(status,headers={'Retry-After':'2'},json={'error':'busy'}))))
    with pytest.raises(providers.ProviderBusy) as exc:await providers.openai_chat('test','System','Hi','secret')
    assert exc.value.retry_after==2

@pytest.mark.asyncio
async def test_openai_requires_a_credential():
    with pytest.raises(ValueError) as exc:await providers.openai_chat('test','System','Hi','  ')
    assert 'credential' in str(exc.value)

@pytest.mark.asyncio
async def test_openai_validates_arguments_before_any_request():
    with pytest.raises(ValueError):await providers.openai_chat('','System','Hi','secret')
    with pytest.raises(ValueError):await providers.openai_chat('test','System','Hi','secret',top_p=2)
    with pytest.raises(ValueError):await providers.openai_chat('test','System','Hi','secret',temperature=-1)
    with pytest.raises(ValueError):await providers.openai_chat('test','System','Hi','secret',max_tokens=0)
