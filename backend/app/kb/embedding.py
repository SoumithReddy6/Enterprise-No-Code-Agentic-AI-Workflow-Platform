"""Pinned local embeddings with bounded transport and no model downloads."""
import asyncio
import math
import httpx
from ..providers import ollama_url

class Embeddings:
    def __init__(self,concurrency=1):self.limit=asyncio.Semaphore(concurrency)
    async def fingerprint(self,model):
        if not model:return ''
        try:
            async with httpx.AsyncClient(timeout=10,trust_env=False) as http:
                r=await http.get(ollama_url()+'/api/tags');r.raise_for_status()
                item=next((x for x in r.json()['models'] if x.get('name')==model),None)
                if not item or not isinstance(item.get('digest'),str) or not item['digest']:raise ValueError('Select an installed Ollama embedding model.')
                return item['digest']
        except (httpx.HTTPError,KeyError,TypeError):raise ValueError('Cannot verify the Ollama embedding model. Start Ollama and refresh models.') from None
    async def embed(self,model,texts,digest):
        if not texts:return []
        if not model:raise ValueError('This knowledge base has no embedding model. Choose keyword search or configure embeddings and rebuild.')
        if await self.fingerprint(model)!=digest:raise ValueError('The embedding model changed. Rebuild the knowledge base before semantic search.')
        async with self.limit:
            try:
                async with httpx.AsyncClient(timeout=120,trust_env=False) as http:
                    r=await http.post(ollama_url()+'/api/embed',json={'model':model,'input':texts,'truncate':False});r.raise_for_status();vectors=r.json()['embeddings']
            except (httpx.HTTPError,KeyError,TypeError):raise ValueError('Embedding request failed. Check model capability, chunk size and Ollama availability.') from None
        if not isinstance(vectors,list) or len(vectors)!=len(texts):raise ValueError('Invalid embedding result count')
        dims=len(vectors[0]) if vectors and isinstance(vectors[0],list) else 0
        if not 1<=dims<=65536 or any(not isinstance(v,list) or len(v)!=dims or not any(v) or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in v) for v in vectors):raise ValueError('Invalid embedding vectors')
        if await self.fingerprint(model)!=digest:raise ValueError('The embedding model changed during processing. Retry with a stable model.')
        return vectors
