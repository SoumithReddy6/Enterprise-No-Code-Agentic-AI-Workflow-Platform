"""Actual local and remote vector operations. No implicit embedding downloads."""
import json
import asyncio
from pathlib import Path
from urllib.parse import quote
import httpx

CAPABILITIES={
    'faiss':{'index_methods':['flat'],'modes':['similarity','keyword','hybrid','rrf'],'local':True},
    'chroma':{'index_methods':['hnsw'],'modes':['similarity','keyword','hybrid','rrf'],'local':True},
    'elasticsearch':{'index_methods':['dense_vector'],'modes':['similarity','keyword','hybrid','rrf'],'local':False},
    'pinecone':{'index_methods':['serverless'],'modes':['similarity','keyword','hybrid','rrf'],'local':False},
}

class FaissAdapter:
    def __init__(self,path,resource,connection=None):self.path=Path(path)
    async def upsert(self,key,chunks,vectors):
        return await asyncio.to_thread(self._upsert,key,chunks,vectors)
    def _upsert(self,key,chunks,vectors):
        import faiss,numpy as np
        self.path.mkdir(parents=True,exist_ok=True)
        data=np.asarray(vectors,dtype='float32');faiss.normalize_L2(data)
        index=faiss.IndexFlatIP(data.shape[1]);index.add(data)
        temp=self.path/(key+'.tmp');faiss.write_index(index,str(temp));temp.replace(self.path/(key+'.faiss'))
    async def remove(self,key):
        (self.path/(key+'.faiss')).unlink(missing_ok=True)
    async def search(self,query,rows,limit):
        return await asyncio.to_thread(self._search,query,rows,limit)
    def _search(self,query,rows,limit):
        import faiss,numpy as np
        q=np.asarray([query],dtype='float32');faiss.normalize_L2(q)
        # Each immutable segment maps positions to chunk.ordinal; publishing is a DB fence.
        groups={}
        for row in rows:groups.setdefault(row['index_key'],{})[row['ordinal']]=row['id']
        results=[]
        for key,allowed in groups.items():
            index=faiss.read_index(str(self.path/(key+'.faiss')))
            scores,indices=index.search(q,index.ntotal)
            results.extend((allowed[int(i)],float(score)) for i,score in zip(indices[0],scores[0]) if int(i) in allowed)
        return sorted(results,key=lambda x:-x[1])[:limit]

class ChromaAdapter:
    def __init__(self,path,resource,connection=None):self.path=str(path);self.name='resource_'+resource['id']
    def collection(self):
        import chromadb
        from chromadb.config import Settings
        return chromadb.PersistentClient(path=self.path,settings=Settings(anonymized_telemetry=False)).get_or_create_collection(self.name,metadata={'hnsw:space':'cosine'},embedding_function=None)
    async def upsert(self,key,chunks,vectors):
        return await asyncio.to_thread(self._upsert,key,chunks,vectors)
    def _upsert(self,key,chunks,vectors):
        c=self.collection()
        for start in range(0,len(chunks),100):
            batch=chunks[start:start+100]
            c.upsert(ids=[r['id'] for r in batch],embeddings=vectors[start:start+100],metadatas=[{'segment':key,'chunk_id':r['id']} for r in batch])
    async def remove(self,key):self.collection().delete(where={'segment':key})
    async def search(self,query,rows,limit):
        return await asyncio.to_thread(self._search,query,rows,limit)
    def _search(self,query,rows,limit):
        c=self.collection();results=[]
        # Bound filter payloads without truncating the search corpus.
        for start in range(0,len(rows),500):
            ids=[r['id'] for r in rows[start:start+500]]
            out=c.query(query_embeddings=[query],where={'chunk_id':{'$in':ids}},n_results=min(limit,len(ids)),include=['distances'])
            results.extend((id,1.-float(distance)) for id,distance in zip(out['ids'][0],out['distances'][0]))
        return sorted(results,key=lambda x:-x[1])[:limit]

class RemoteAdapter:
    def __init__(self,path,resource,connection):
        self.resource=resource;self.connection=connection
        self.endpoint=connection['endpoint'].rstrip('/')
    async def request(self,method,path,**kwargs):
        from .tool_service import public_addresses
        url=httpx.URL(self.endpoint+path)
        addresses=await asyncio.to_thread(public_addresses,url.host,url.port or (443 if url.scheme=='https' else 80))
        headers=self.headers();headers.update(kwargs.pop('headers',{}));headers['Host']=url.netloc.decode()
        try:
            async with httpx.AsyncClient(timeout=60,follow_redirects=False,trust_env=False) as client:
                async with client.stream(method,url.copy_with(host=addresses[0]),headers=headers,extensions={'sni_hostname':url.host},**kwargs) as response:
                    response.raise_for_status();data=bytearray()
                    async for block in response.aiter_bytes():
                        data.extend(block)
                        if len(data)>16*1024*1024:raise ValueError('Vector server response exceeds 16 MB')
                    return json.loads(data)
        except (httpx.HTTPError,ValueError):
            # Never persist exception URLs, authorization headers or credential material.
            raise ValueError('Vector server request failed; check the connection, index and permissions') from None

class ElasticsearchAdapter(RemoteAdapter):
    def headers(self):
        import base64
        c=self.connection
        auth='Basic '+base64.b64encode((c['username']+':'+c['secret']).encode()).decode() if c.get('username') else 'ApiKey '+c['secret']
        return {'Authorization':auth}
    @property
    def index(self):return '/'+quote(self.resource['index_name'],safe='')
    async def upsert(self,key,chunks,vectors):
        # Use an existing dense_vector index and validate its dimension contract.
        mapping=await self.request('GET',self.index+'/_mapping')
        props=next(iter(mapping.values()))['mappings'].get('properties',{})
        vector=props.get('vector',{})
        if vector.get('type')!='dense_vector' or vector.get('dims')!=len(vectors[0]) or vector.get('similarity','cosine')!='cosine':
            raise ValueError('Elasticsearch index requires vector dense_vector mapping with matching dimensions and cosine similarity')
        batch_size=max(1,min(100,100000//len(vectors[0])))
        for start in range(0,len(chunks),batch_size):
            lines=[]
            for chunk,vec in zip(chunks[start:start+batch_size],vectors[start:start+batch_size]):
                lines.extend([json.dumps({'index':{'_index':self.resource['index_name'],'_id':chunk['id']}}),json.dumps({'vector':vec,'text':chunk['text'],'resource_id':self.resource['id'],'chunk_id':chunk['id'],'segment':key})])
            out=await self.request('POST','/_bulk?refresh=wait_for',content=('\n'.join(lines)+'\n').encode(),headers={'Content-Type':'application/x-ndjson'})
            if out.get('errors'):raise ValueError('Elasticsearch rejected some chunks; check vector mapping and permissions')
    async def remove(self,key):
        await self.request('POST',self.index+'/_delete_by_query',json={'query':{'bool':{'filter':[{'term':{'resource_id':self.resource['id']}},{'term':{'segment':key}}]}}})
    async def search(self,query,rows,limit):
        results=[]
        for start in range(0,len(rows),500):
            out=await self.request('POST',self.index+'/_search',json={'size':limit,'knn':{'field':'vector','query_vector':query,'k':limit,'num_candidates':max(100,limit),'filter':{'ids':{'values':[r['id'] for r in rows[start:start+500]]}}}})
            results.extend((r['_id'],2*float(r['_score'])-1) for r in out['hits']['hits'])
        return sorted(results,key=lambda x:-x[1])[:limit]

class PineconeAdapter(RemoteAdapter):
    def headers(self):return {'Api-Key':self.connection['secret'],'X-Pinecone-API-Version':'2025-10'}
    async def upsert(self,key,chunks,vectors):
        stats=await self.request('POST','/describe_index_stats',json={})
        if stats.get('dimension')!=len(vectors[0]):raise ValueError('Pinecone index dimensions do not match embedding model')
        batch_size=max(1,min(100,100000//len(vectors[0])))
        for start in range(0,len(chunks),batch_size):
            await self.request('POST','/vectors/upsert',json={'namespace':self.resource['id'],'vectors':[{'id':c['id'],'values':v,'metadata':{'chunk_id':c['id'],'segment':key}} for c,v in zip(chunks[start:start+batch_size],vectors[start:start+batch_size])]})
        # Pinecone upserts are eventually consistent; do not publish partially visible files.
        for start in range(0,len(chunks),100):
            ids=[c['id'] for c in chunks[start:start+100]]
            for attempt in range(60):
                out=await self.request('GET','/vectors/fetch',params=[('namespace',self.resource['id'])]+[('ids',id) for id in ids])
                if set(ids)<=set(out.get('vectors',{})):break
                await asyncio.sleep(1)
            else:raise ValueError('Pinecone indexing did not become visible within 60 seconds; retry the file')
    async def remove(self,key):
        await self.request('POST','/vectors/delete',json={'namespace':self.resource['id'],'filter':{'segment':{'$eq':key}}})
    async def search(self,query,rows,limit):
        results=[]
        for start in range(0,len(rows),500):
            out=await self.request('POST','/query',json={'namespace':self.resource['id'],'vector':query,'topK':limit,'filter':{'chunk_id':{'$in':[r['id'] for r in rows[start:start+500]]}},'includeValues':False})
            results.extend((r['id'],float(r['score'])) for r in out.get('matches',[]))
        return sorted(results,key=lambda x:-x[1])[:limit]

ADAPTERS={'faiss':FaissAdapter,'chroma':ChromaAdapter,'elasticsearch':ElasticsearchAdapter,'pinecone':PineconeAdapter}
