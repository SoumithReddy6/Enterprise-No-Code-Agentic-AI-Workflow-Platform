"""Model provider transports. Service addresses are operator config, never workflow inputs.

Public function signatures and ValueError compatibility are retained for existing callers.
Call with_retries explicitly around a chat coroutine if retries are desired.
"""

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import os
import random

import httpx

from .observability import journal


RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
MAX_RETRY_DELAY = 30.0
CAPABILITY_CONCURRENCY = 5


class ProviderBusy(ValueError):
    """Transient failure. Still a ValueError for existing application callers."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderResponseError(ValueError):
    """A provider returned an unsupported payload (not automatically retried)."""


def client(timeout):
    """Compatibility-preserving factory; callers own and close each client."""
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


def ollama_url():
    """Never accept this address from untrusted workflow configuration."""
    return os.environ.get('OLLAMA_BASE_URL', 'http://127.0.0.1:11434').rstrip('/')


def _retry_after(response):
    """Interpret Retry-After seconds or HTTP date; cap server-directed delays."""
    value = response.headers.get('Retry-After')
    if not value:
        return None
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return min(seconds, MAX_RETRY_DELAY)
    except (TypeError, ValueError):
        pass
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        seconds = (date - datetime.now(timezone.utc)).total_seconds()
        return min(max(seconds, 0.0), MAX_RETRY_DELAY)
    except (TypeError, ValueError, OverflowError):
        return None


def _raise_retryable(response, provider):
    if response.status_code in RETRYABLE_STATUS:
        raise ProviderBusy(
            f'{provider} returned HTTP {response.status_code}',
            retry_after=_retry_after(response),
        )


async def with_retries(call, attempts=3, base_delay=.5, provider=None, model=None):
    """Retry ProviderBusy only. Final failure remains a ValueError.

    Full jitter prevents synchronized clients from retrying together. Valid Retry-After
    values take precedence. Both delays are capped at MAX_RETRY_DELAY seconds.
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError('attempts must be a positive integer')
    if (isinstance(base_delay, bool) or not isinstance(base_delay, (int, float))
            or not math.isfinite(base_delay) or base_delay < 0):
        raise ValueError('base_delay must be a finite non-negative number')

    for attempt in range(attempts):
        try:
            return await call()
        except ProviderBusy as exc:
            if attempt == attempts - 1:
                journal(event='provider.failure', provider=provider, model=model,
                        attempt=attempt + 1, error_type=type(exc).__name__)
                # Keep the original public contract: outer callers receive ValueError.
                raise ValueError(f'{exc} (after {attempts} attempts)') from None
            limit = min(MAX_RETRY_DELAY, base_delay * 2 ** min(attempt, 30))
            delay = (exc.retry_after if exc.retry_after is not None
                     else random.uniform(0, limit))
            journal(event='provider.retry', provider=provider, model=model,
                    attempt=attempt + 1, delay_seconds=delay,
                    error_type=type(exc).__name__)
            await asyncio.sleep(delay)


def record_usage(usage, prompt_tokens, completion_tokens):
    """Accumulate valid provider token counts in the caller-owned usage dictionary."""
    if usage is None:
        return
    for key, value in (('prompt_tokens', prompt_tokens),
                       ('completion_tokens', completion_tokens)):
        if type(value) is int and value >= 0:
            usage[key] = usage.get(key, 0) + value


def _json_object(response, provider):
    try:
        body = response.json()
    except ValueError:
        raise ProviderResponseError(f'{provider} returned invalid JSON.') from None
    if not isinstance(body, dict):
        raise ProviderResponseError(f'{provider} returned an invalid response object.')
    return body


def _validate_chat_args(model, system, prompt, max_tokens, temperature, top_p):
    if not isinstance(model, str) or not model.strip():
        raise ValueError('Select a valid model.')
    if not isinstance(system, str) or not isinstance(prompt, str):
        raise ValueError('System instructions and prompt must be strings.')
    if type(max_tokens) is not int or max_tokens < 1:
        raise ValueError('max_tokens must be a positive integer.')
    for name, value in (('temperature', temperature), ('top_p', top_p)):
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError(f'{name} must be a finite number or None.')
    if top_p is not None and not 0 <= top_p <= 1:
        raise ValueError('top_p must be between 0 and 1.')
    if temperature is not None and temperature < 0:
        raise ValueError('temperature cannot be negative.')
    # Provider/model-specific upper temperature and token limits remain provider-enforced.


async def ollama_chat(model, system, prompt, temperature=None, top_p=None,
                      max_tokens=2048, usage=None):
    """Ollama /api/chat transport; returns text, accumulating usage separately."""
    _validate_chat_args(model, system, prompt, max_tokens, temperature, top_p)
    options = {'num_predict': max_tokens}
    if temperature is not None:
        options['temperature'] = temperature
    if top_p is not None:
        options['top_p'] = top_p
    try:
        async with client(110) as http:
            response = await http.post(
                ollama_url() + '/api/chat',
                json={'model': model,
                      'messages': [{'role': 'system', 'content': system},
                                   {'role': 'user', 'content': prompt}],
                      'stream': False, 'options': options},
            )
            if response.status_code == 404:
                raise ValueError(f'Ollama model {model} is not installed. Pull it in Ollama or choose an installed model.')
            _raise_retryable(response, 'Ollama')
            response.raise_for_status()
            body = _json_object(response, 'Ollama')
            message = body.get('message')
            if not isinstance(message, dict):
                raise ProviderResponseError('Ollama returned no message object.')
            text = message.get('content')
            if not isinstance(text, str) or not text.strip():
                raise ProviderResponseError('Ollama returned no text. Choose a chat-capable model.')
            record_usage(usage, body.get('prompt_eval_count'), body.get('eval_count'))
            return text
    except httpx.ConnectError:
        raise ValueError('Cannot connect to Ollama. Start Ollama and check OLLAMA_BASE_URL.') from None
    except httpx.TimeoutException:
        raise ProviderBusy('Ollama timed out. Try a smaller model or shorter input.') from None
    except httpx.HTTPError:
        raise ValueError('Ollama request failed. Check that the selected model supports chat.') from None


async def ollama_capabilities(http, name):
    """Return capabilities from /api/show, or None if unknown/unavailable."""
    try:
        response = await http.post(ollama_url() + '/api/show', json={'model': name})
        response.raise_for_status()
        capabilities = _json_object(response, 'Ollama').get('capabilities')
        return [c for c in capabilities if isinstance(c, str)] if isinstance(capabilities, list) else None
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return None


def supports(model, capability):
    """Unknown capability list is permissive for older Ollama servers."""
    capabilities = model.get('capabilities')
    return capabilities is None or capability in capabilities


async def ollama_models():
    """List installed models and discover capabilities with bounded concurrency."""
    try:
        async with client(5) as http:
            response = await http.get(ollama_url() + '/api/tags')
            response.raise_for_status()
            raw_models = _json_object(response, 'Ollama').get('models')
            if not isinstance(raw_models, list):
                raise ProviderResponseError('Ollama model listing has no models list.')
            tags = [m for m in raw_models if isinstance(m, dict)
                    and isinstance(m.get('name'), str) and m['name'].strip()]
            semaphore = asyncio.Semaphore(CAPABILITY_CONCURRENCY)

            async def limited_capabilities(name):
                async with semaphore:
                    return await ollama_capabilities(http, name)

            capabilities = await asyncio.gather(
                *(limited_capabilities(m['name']) for m in tags)
            )
            return {'connected': True, 'models': [
                {'name': m['name'],
                 'size': m.get('size', 0),
                 'digest': m.get('digest', '') if isinstance(m.get('digest'), str) else '',
                 'capabilities': c}
                for m, c in zip(tags, capabilities)
            ]}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return {'connected': False, 'models': [],
                'error': 'Cannot list local models. Start Ollama and check OLLAMA_BASE_URL.'}


async def ollama_model(name):
    """Find an installed model with a digest; does not establish embedding support."""
    discovery = await ollama_models()
    if not discovery['connected']:
        raise ValueError('Cannot verify the Ollama embedding model. Start Ollama and refresh models.')
    model = next((m for m in discovery['models'] if m['name'] == name), None)
    if not model or not model['digest']:
        raise ValueError('Select an installed Ollama embedding model.')
    return model


async def claude_chat(model, system, prompt, key, temperature=None, top_p=None,
                      max_tokens=2048, usage=None):
    """Claude native Messages API; system is a top-level field."""
    _validate_chat_args(model, system, prompt, max_tokens, temperature, top_p)
    if not isinstance(key, str) or not key.strip():
        raise ValueError('Configure a Claude API credential.')
    params = {}
    if temperature is not None:
        params['temperature'] = temperature
    if top_p is not None:
        params['top_p'] = top_p
    try:
        async with client(60) as http:
            response = await http.post(
                'https://api.anthropic.com/v1/messages',
                headers={'x-api-key': key, 'anthropic-version': '2023-06-01'},
                json={'model': model, 'system': system,
                      'messages': [{'role': 'user', 'content': prompt}],
                      'max_tokens': max_tokens, **params},
            )
            _raise_retryable(response, 'Claude')
            response.raise_for_status()
            body = _json_object(response, 'Claude')
            blocks = body.get('content')
            if not isinstance(blocks, list):
                raise ProviderResponseError('Claude returned no supported text response.')
            texts = []
            for block in blocks:
                if not isinstance(block, dict):
                    raise ProviderResponseError('Claude returned an unsupported content block.')
                if block.get('type') == 'text':
                    text = block.get('text')
                    if not isinstance(text, str):
                        raise ProviderResponseError('Claude returned no supported text response.')
                    texts.append(text)
            if not texts or not '\n'.join(texts).strip():
                raise ProviderResponseError('Claude returned no text.')
            usage_data = body.get('usage')
            if usage_data is not None and not isinstance(usage_data, dict):
                raise ProviderResponseError('Claude returned invalid usage metadata.')
            usage_data = usage_data or {}
            record_usage(usage, usage_data.get('input_tokens'), usage_data.get('output_tokens'))
            return '\n'.join(texts)
    except httpx.TimeoutException:
        raise ProviderBusy('Claude request timed out') from None
    except httpx.HTTPStatusError as exc:
        raise ValueError(f'Claude request failed (HTTP {exc.response.status_code}). Check the credential, model and quota.') from None
    except httpx.RequestError:
        raise ValueError('Claude request failed or returned an unsupported response.') from None


async def openai_chat(model, system, prompt, key, temperature=None, top_p=None,
                      max_tokens=2048, usage=None):
    """OpenAI Chat Completions; system is the first message and tokens are max_completion_tokens."""
    _validate_chat_args(model, system, prompt, max_tokens, temperature, top_p)
    if not isinstance(key, str) or not key.strip():
        raise ValueError('Choose an OpenAI credential in the LLM settings.')
    params = {}
    if temperature is not None:
        params['temperature'] = temperature
    if top_p is not None:
        params['top_p'] = top_p
    try:
        async with client(60) as http:
            response = await http.post(
                'https://api.openai.com/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}'},
                json={'model': model,
                      'messages': [{'role': 'system', 'content': system},
                                   {'role': 'user', 'content': prompt}],
                      'max_completion_tokens': max_tokens, **params},
            )
            _raise_retryable(response, 'OpenAI')
            response.raise_for_status()
            body = _json_object(response, 'OpenAI')
            choices = body.get('choices')
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                raise ProviderResponseError('OpenAI returned no supported text response.')
            message = choices[0].get('message')
            if not isinstance(message, dict):
                raise ProviderResponseError('OpenAI returned an unsupported message payload.')
            text = message.get('content')
            if not isinstance(text, str):
                raise ProviderResponseError('Model did not return text.')
            if not text.strip():
                raise ProviderResponseError('OpenAI returned no text.')
            usage_data = body.get('usage')
            if usage_data is not None and not isinstance(usage_data, dict):
                raise ProviderResponseError('OpenAI returned invalid usage metadata.')
            usage_data = usage_data or {}
            record_usage(usage, usage_data.get('prompt_tokens'), usage_data.get('completion_tokens'))
            return text
    except httpx.TimeoutException:
        raise ProviderBusy('OpenAI request timed out') from None
    except httpx.HTTPStatusError as exc:
        raise ValueError(f'OpenAI request failed (HTTP {exc.response.status_code}). Check the credential, model and quota.') from None
    except httpx.RequestError:
        raise ValueError('The model request failed or returned an unsupported response.') from None
