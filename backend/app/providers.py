"""Ollama transport. Service addresses are operator config, never workflow inputs."""
import os
import httpx

def client(timeout):return httpx.AsyncClient(timeout=timeout,trust_env=False)
def ollama_url():return os.environ.get('OLLAMA_BASE_URL','http://127.0.0.1:11434').rstrip('/')

async def ollama_chat(model,system,prompt,temperature=None,top_p=None,max_tokens=2048):
    options={'num_predict':max_tokens}
    if temperature is not None:options['temperature']=temperature
    if top_p is not None:options['top_p']=top_p
    try:
        async with client(110) as http:
            response=await http.post(ollama_url()+'/api/chat',json={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':prompt}],'stream':False,'options':options})
            if response.status_code==404:raise ValueError(f'Ollama model {model} is not installed. Pull it in Ollama or choose an installed model.')
            response.raise_for_status()
            text=response.json()['message']['content']
            if not isinstance(text,str) or not text.strip():raise ValueError('Ollama returned no text. Choose a chat-capable model.')
            return text
    except httpx.ConnectError:raise ValueError('Cannot connect to Ollama. Start Ollama and check OLLAMA_BASE_URL.') from None
    except httpx.TimeoutException:raise ValueError('Ollama timed out. Try a smaller model or shorter input.') from None
    except (httpx.HTTPError,KeyError,TypeError):raise ValueError('Ollama request failed. Check that the selected model supports chat.') from None

async def ollama_models():
    try:
        async with client(5) as http:
            response=await http.get(ollama_url()+'/api/tags');response.raise_for_status()
            models=response.json()['models']
            return {'connected':True,'models':[{'name':m['name'],'size':m.get('size',0)} for m in models if isinstance(m.get('name'),str)]}
    except (httpx.HTTPError,KeyError,TypeError,ValueError):
        return {'connected':False,'models':[],'error':'Cannot list local models. Start Ollama and check OLLAMA_BASE_URL.'}

async def claude_chat(model,system,prompt,key,temperature=None,top_p=None,max_tokens=2048):
    """Claude's native Messages API; system instructions are a top-level field."""
    params={}
    if temperature is not None:params['temperature']=temperature
    if top_p is not None:params['top_p']=top_p
    try:
        async with client(60) as http:
            response=await http.post('https://api.anthropic.com/v1/messages',
                headers={'x-api-key':key,'anthropic-version':'2023-06-01'},
                json={'model':model,'system':system,'messages':[{'role':'user','content':prompt}],'max_tokens':max_tokens,**params})
            response.raise_for_status()
            blocks=response.json()['content']
            texts=[block['text'] for block in blocks if block['type']=='text']
            if not texts or any(not isinstance(text,str) for text in texts):raise ValueError('Claude returned no supported text response.')
            result='\n'.join(texts)
            if not result.strip():raise ValueError('Claude returned no text.')
            return result
    except httpx.HTTPStatusError as exc:
        raise ValueError(f'Claude request failed (HTTP {exc.response.status_code}). Check the credential, model and quota.') from None
    except (httpx.RequestError,KeyError,TypeError,ValueError) as exc:
        if isinstance(exc,ValueError) and str(exc).startswith('Claude returned'):raise
        raise ValueError('Claude request failed or returned an unsupported response.') from None
