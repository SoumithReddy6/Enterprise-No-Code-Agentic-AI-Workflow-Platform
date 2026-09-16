import json
import httpx
import pytest
from backend.app import providers

@pytest.mark.asyncio
async def test_ollama_uses_native_chat_without_credentials(monkeypatch):
    async def handler(request):
        body=json.loads(request.content)
        assert request.url.path=='/api/chat'
        assert body['model']=='tiny:latest'
        assert body['stream'] is False
        assert body['messages'][-1]['content']=='Hello'
        assert 'authorization' not in request.headers
        return httpx.Response(200,json={'message':{'content':'Local response'},'done':True})
    real_client=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:real_client(transport=httpx.MockTransport(handler)))
    assert await providers.ollama_chat('tiny:latest','System','Hello')=='Local response'

@pytest.mark.asyncio
async def test_ollama_missing_model_has_actionable_error(monkeypatch):
    real_client=httpx.AsyncClient
    monkeypatch.setattr(providers,'client',lambda timeout:real_client(transport=httpx.MockTransport(lambda _:httpx.Response(404,json={'error':'model missing'}))))
    with pytest.raises(ValueError,match='not installed'):
        await providers.ollama_chat('missing','System','Hello')
