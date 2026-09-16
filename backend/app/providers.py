"""Model provider transports. Service addresses are operator config, never workflow inputs."""
import asyncio
import logging
import os
import httpx

log=logging.getLogger('relay.provider')
RETRYABLE_STATUS={429,500,502,503,504,529}

class ProviderBusy(ValueError):
    """A transient provider failure (rate limit, overload, timeout) worth retrying."""

def client(timeout):return httpx.AsyncClient(timeout=timeout,trust_env=False)
def ollama_url():return os.environ.get('OLLAMA_BASE_URL','http://127.0.0.1:11434').rstrip('/')

async def with_retries(call,attempts=3,base_delay=.5):
    """Exponential backoff for transient failures only; configuration and model errors surface immediately."""
    for attempt in range(attempts):
        try:return await call()
        except ProviderBusy as exc:
            if attempt==attempts-1:raise ValueError(f'{exc} (after {attempts} attempts)') from None
            delay=base_delay*2**attempt
            log.warning('provider retry attempt=%d delay=%.1fs reason=%s',attempt+1,delay,exc)
            await asyncio.sleep(delay)

def record_usage(usage,prompt_tokens,completion_tokens):
    """Fill the caller's usage dict in place; tokens are reported on the node event, never in outputs."""
    if usage is None:return
    if isinstance(prompt_tokens,int):usage['prompt_tokens']=usage.get('prompt_tokens',0)+prompt_tokens
    if isinstance(completion_tokens,int):usage['completion_tokens']=usage.get('completion_tokens',0)+completion_tokens

async def ollama_chat(model,system,prompt,temperature=None,top_p=None,max_tokens=2048,usage=None):
    options={'num_predict':max_tokens}
    if temperature is not None:options['temperature']=temperature
    if top_p is not None:options['top_p']=top_p
    try:
        async with client(110) as http:
            response=await http.post(ollama_url()+'/api/chat',json={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],'stream':False,'options':options})
            if response.status_code==404:raise ValueError(f'Ollama model {model} is not installed. Pull it in Ollama or choose an installed model.')
            if response.status_code in RETRYABLE_STATUS:raise ProviderBusy(f'Ollama returned HTTP {response.status_code}')
            response.raise_for_status()
            body=response.json();text=body['message']['content']
            if not isinstance(text,str) or not text.strip():raise ValueError('Ollama returned no text. Choose a chat-capable model.')
            record_usage(usage,body.get('prompt_eval_count'),body.get('eval_count'))
            return text
    except httpx.ConnectError:raise ValueError('Cannot connect to Ollama. Start Ollama and check OLLAMA_BASE_URL.') from None
    except httpx.TimeoutException:raise ValueError('Ollama timed out. Try a smaller model or shorter input.') from None
    except (httpx.HTTPError,KeyError,TypeError):raise ValueError('Ollama request failed. Check that the selected model supports chat.') from None

async def ollama_capabilities(http,name):
    """Capabilities from /api/show ('completion', 'embedding', ...); None on servers that predate the field."""
    try:
        response=await http.post(ollama_url()+'/api/show',json={'model':name});response.raise_for_status()
        capabilities=response.json().get('capabilities')
        return [c for c in capabilities if isinstance(c,str)] if isinstance(capabilities,list) else None
    except (httpx.HTTPError,ValueError,AttributeError):return None

def supports(model,capability):
    """Unknown capability lists never block a model; only a known list without the capability does."""
    return model.get('capabilities') is None or capability in model['capabilities']

async def ollama_models():
    try:
        async with client(5) as http:
            response=await http.get(ollama_url()+'/api/tags');response.raise_for_status()
            tags=[m for m in response.json()['models'] if isinstance(m.get('name'),str)]
            capabilities=await asyncio.gather(*(ollama_capabilities(http,m['name']) for m in tags))
            return {'connected':True,'models':[{'name':m['name'],'size':m.get('size',0),'digest':m.get('digest','') if isinstance(m.get('digest'),str) else '','capabilities':c} for m,c in zip(tags,capabilities)]}
    except (httpx.HTTPError,KeyError,TypeError,ValueError):
        return {'connected':False,'models':[],'error':'Cannot list local models. Start Ollama and check OLLAMA_BASE_URL.'}

async def ollama_model(name):
    """One installed model's digest and capabilities, or a ValueError the operator can act on."""
    discovery=await ollama_models()
    if not discovery['connected']:raise ValueError('Cannot verify the Ollama embedding model. Start Ollama and refresh models.')
    model=next((m for m in discovery['models'] if m['name']==name),None)
    if not model or not model['digest']:raise ValueError('Select an installed Ollama embedding model.')
    return model

async def claude_chat(model,system,prompt,key,temperature=None,top_p=None,max_tokens=2048,usage=None):
    """Claude's native Messages API; system instructions are a top-level field."""
    params={}
    if temperature is not None:params['temperature']=temperature
    if top_p is not None:params['top_p']=top_p
    try:
        async with client(60) as http:
            response=await http.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key':key,'anthropic-version':'2023-06-01'},
                json={'model':model,'system':system,'messages':[{'role':'user','content':prompt}],'max_tokens':max_tokens,**params})
            if response.status_code in RETRYABLE_STATUS:raise ProviderBusy(f'Claude returned HTTP {response.status_code}')
            response.raise_for_status()
            body=response.json();blocks=body['content']
            texts=[block['text'] for block in blocks if block['type']=='text']
            if not texts or any(not isinstance(text,str) for text in texts):raise ValueError('Claude returned no supported text response.')
            result='\n'.join(texts)
            if not result.strip():raise ValueError('Claude returned no text.')
            record_usage(usage,(body.get('usage') or {}).get('input_tokens'),(body.get('usage') or {}).get('output_tokens'))
            return result
    except ProviderBusy:raise
    except httpx.TimeoutException:raise ProviderBusy('Claude request timed out') from None
    except httpx.HTTPStatusError as exc:
        raise ValueError(f'Claude request failed (HTTP {exc.response.status_code}). Check the credential, model and quota.') from None
    except (httpx.RequestError,KeyError,TypeError,ValueError) as exc:
        if isinstance(exc,ValueError) and str(exc).startswith('Claude returned'):raise
        raise ValueError('Claude request failed or returned an unsupported response.') from None
