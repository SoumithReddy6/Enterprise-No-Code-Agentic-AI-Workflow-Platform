"""Run from backend/: python -m pytest tests/test_providers.py -q"""
import asyncio
import json


import httpx
import pytest

# The transport depends on the project's existing observability module.
# A standalone copy of these tests can use a lightweight substitute.
from backend.app import providers as p


@pytest.fixture
def mocked_http(monkeypatch):
    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(
            p, "client", lambda timeout: httpx.AsyncClient(
                transport=transport, timeout=timeout, trust_env=False
            ),
        )
    return install


def ollama_response(text="hello", **fields):
    return {"message": {"content": text}, "prompt_eval_count": 12,
            "eval_count": 7, **fields}


@pytest.mark.asyncio
async def test_ollama_success_and_usage(mocked_http):
    def handle(request):
        assert request.url.path == "/api/chat"
        data = json.loads(request.content)
        assert data["options"]["num_predict"] == 99
        assert data["options"]["temperature"] == 0.2
        assert data["stream"] is False
        return httpx.Response(200, json=ollama_response())
    mocked_http(handle)
    usage = {}
    assert await p.ollama_chat("llama", "sys", "prompt", temperature=0.2,
                               max_tokens=99, usage=usage) == "hello"
    assert usage == {"prompt_tokens": 12, "completion_tokens": 7}


@pytest.mark.asyncio
async def test_ollama_404_does_not_retry(mocked_http, monkeypatch):
    calls = 0
    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(404)
    mocked_http(handle)
    with pytest.raises(ValueError, match="not installed"):
        await p.with_retries(lambda: p.ollama_chat("absent", "s", "p"))
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_429_then_success_and_journals(mocked_http, monkeypatch):
    calls, events, delays = 0, [], []
    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429) if calls == 1 else httpx.Response(200, json=ollama_response())
    async def sleep(delay):
        delays.append(delay)
    mocked_http(handle)
    monkeypatch.setattr(p, "journal", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(p.asyncio, "sleep", sleep)
    assert await p.with_retries(lambda: p.ollama_chat("llama", "s", "p"),
                                provider="ollama", model="llama") == "hello"
    assert calls == 2
    assert len(delays) == 1 and 0 <= delays[0] <= 0.5
    assert events[0]["event"] == "provider.retry"


@pytest.mark.asyncio
async def test_retry_exhaustion_is_still_valueerror(mocked_http, monkeypatch):
    events, calls = [], 0
    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)
    async def sleep(_): pass
    mocked_http(handle)
    monkeypatch.setattr(p, "journal", lambda **kwargs: events.append(kwargs))
    monkeypatch.setattr(p.asyncio, "sleep", sleep)
    with pytest.raises(ValueError, match="after 3 attempts"):
        await p.with_retries(lambda: p.ollama_chat("llama", "s", "p"))
    assert calls == 3
    assert [item["event"] for item in events] == ["provider.retry", "provider.retry", "provider.failure"]


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts,base_delay", [(0, 0.5), (1, -1), (2.5, 0.5)])
async def test_invalid_retry_settings(attempts, base_delay):
    with pytest.raises(ValueError):
        await p.with_retries(lambda: asyncio.sleep(0), attempts=attempts,
                             base_delay=base_delay)


@pytest.mark.asyncio
async def test_ollama_malformed_responses_are_controlled(mocked_http):
    for response in [httpx.Response(200, content=b"not-json"),
                     httpx.Response(200, json={"message": None}),
                     httpx.Response(200, json={"message": {"content": "  "}})]:
        mocked_http(lambda _: response)
        with pytest.raises(ValueError):
            await p.ollama_chat("llama", "s", "p")


@pytest.mark.asyncio
async def test_claude_success_and_text_blocks(mocked_http):
    def handle(request):
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "secret"
        body = json.loads(request.content)
        assert body["system"] == "system"
        assert body["messages"] == [{"role": "user", "content": "prompt"}]
        return httpx.Response(200, json={"content": [{"type": "text", "text": "first"},
                                                      {"type": "tool_use", "id": "x"},
                                                      {"type": "text", "text": "second"}],
                                         "usage": {"input_tokens": 5, "output_tokens": 9}})
    mocked_http(handle)
    usage = {}
    assert await p.claude_chat("model", "system", "prompt", "secret", usage=usage) == "first\nsecond"
    assert usage == {"prompt_tokens": 5, "completion_tokens": 9}


@pytest.mark.asyncio
async def test_claude_auth_is_not_retried(mocked_http, monkeypatch):
    calls = 0
    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(401)
    mocked_http(handle)
    with pytest.raises(ValueError, match="HTTP 401"):
        await p.with_retries(lambda: p.claude_chat("m", "s", "p", "bad"))
    assert calls == 1


@pytest.mark.asyncio
async def test_claude_invalid_shapes_are_controlled(mocked_http):
    bad = [{"content": "not a list"}, {"content": [{"type": "text"}]},
           {"content": [{"type": "text", "text": "ok"}], "usage": [1]},
           {"content": [{"type": "text", "text": " "}]}]
    for body in bad:
        mocked_http(lambda request: httpx.Response(200, json=body))
        with pytest.raises(ValueError):
            await p.claude_chat("m", "s", "p", "key")


@pytest.mark.asyncio
async def test_retry_after_header_is_respected(mocked_http, monkeypatch):
    delays, calls = [], 0
    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "3"}) if calls == 1 else httpx.Response(200, json=ollama_response())
    async def sleep(delay): delays.append(delay)
    mocked_http(handle)
    monkeypatch.setattr(p.asyncio, "sleep", sleep)
    await p.with_retries(lambda: p.ollama_chat("m", "s", "p"))
    assert delays == [3.0]


def test_usage_ignores_invalid_counts():
    usage = {}
    p.record_usage(usage, True, -2)
    assert usage == {}
    p.record_usage(usage, 5, 2)
    p.record_usage(usage, 3, None)
    assert usage == {"prompt_tokens": 8, "completion_tokens": 2}


@pytest.mark.asyncio
async def test_models_discovery_filters_malformed_tags(mocked_http):
    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [None, {"name": "chat", "digest": "abc", "size": 10}, {"name": 3}]})
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", 1]})
        raise AssertionError(request.url.path)
    mocked_http(handle)
    result = await p.ollama_models()
    assert result["connected"] is True
    assert result["models"] == [{"name": "chat", "size": 10, "digest": "abc", "capabilities": ["completion"]}]
    assert p.supports(result["models"][0], "completion")
    assert not p.supports(result["models"][0], "embedding")
    assert p.supports({"capabilities": None}, "embedding")


@pytest.mark.asyncio
async def test_model_requires_installed_name_and_digest(mocked_http):
    def handle(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "embed", "digest": "xyz"}]})
        return httpx.Response(200, json={"capabilities": ["embedding"]})
    mocked_http(handle)
    assert (await p.ollama_model("embed"))["digest"] == "xyz"
    with pytest.raises(ValueError, match="installed"):
        await p.ollama_model("absent")
