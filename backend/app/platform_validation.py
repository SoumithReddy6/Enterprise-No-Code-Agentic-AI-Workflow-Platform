"""Authoritative platform-resource checks shared by submission and workers."""
import json
from .platform_graph import VECTOR_TYPES

def platform_errors(store,workflow,tenant,checkpoints=None,vector_dependencies=None):
    from .registry import REGISTRY
    from .tool_service import ToolService
    from .vector_service import VectorService
    tools=ToolService(store);vectors=VectorService(store);errors=[]
    for node in workflow.nodes:
        if node.type not in REGISTRY:continue
        try:config=REGISTRY[node.type].config_model.model_validate(node.config)
        except ValueError:continue
        try:
            if node.type.startswith('tool_'):tools.check(config,tenant)
            elif node.type in VECTOR_TYPES:
                resource=vectors.resource(config.resource_id,tenant)
                if node.type!='vector_'+resource['backend']:raise ValueError('VectorDB backend does not match the selected resource.')
            if node.type in ('retrieve','query'):
                for edge in workflow.edges:
                    if edge.kind=='store' and edge.target==node.id:
                        source=next(n for n in workflow.nodes if n.id==edge.source)
                        resolve_vector(vectors,source.config.get('resource_id',''),config.storage_path,tenant)
            cached=(checkpoints or {}).get(node.id)
            if cached and node.type in ('retrieve','query') and not node.config.get('knowledge_base_id'):
                sources=json.loads(cached['sources'])
                vectors.verify_sources([{k:v for k,v in s.items() if k not in ('citation','cited')} for s in sources],tenant)
        except (KeyError,ValueError,TypeError,StopIteration):errors.append(f'{node.id}: resource, connection, permissions or saved sources are unavailable. Check this node configuration.')
    for owner,sources in (vector_dependencies or {}).items():
        if owner not in (checkpoints or {}):continue
        try:
            if not isinstance(sources,list) or len(sources)>120:raise ValueError('Invalid saved source dependencies.')
            legacy=[s for s in sources if 'knowledge_base_id' not in s]
            for offset in range(0,len(legacy),20):vectors.verify_sources(legacy[offset:offset+20],tenant)
        except (KeyError,ValueError,TypeError):errors.append(f'{owner}: saved sources are unavailable or removed. Start a new run with current evidence.')
    return errors

def resolve_vector(service,resource_id,path,tenant):
    resource=service.resource(resource_id,tenant)
    if not path or resource['storage_path']==path:return resource['id']
    matches=[r for r in service.resources(tenant) if r['backend']==resource['backend'] and r['connection_id']==resource['connection_id'] and r.get('index_name','')==resource.get('index_name','') and r['storage_path']==path]
    if len(matches)!=1:raise ValueError('Choose an existing storage path on the attached VectorDB connection.')
    return matches[0]['id']


async def kb_errors(services,workflow,tenant,checkpoints=None,dependencies=None):
    errors=[]
    for node in workflow.nodes:
        if node.type not in ('retrieve','query') or not node.config.get('knowledge_base_id'):continue
        try:
            await services.management.call('resolve',tenant,{'kb_id':node.config['knowledge_base_id']})
            cached=(checkpoints or {}).get(node.id)
            if cached:
                sources=json.loads(cached['sources'])
                await services.verify([{k:v for k,v in s.items() if k not in ('citation','cited')} for s in sources],tenant)
        except (KeyError,ValueError,TypeError):errors.append(f'{node.id}: knowledge base or saved sources are unavailable. Check indexing status and permissions.')
    for owner,sources in (dependencies or {}).items():
        if owner not in (checkpoints or {}):continue
        try:
            current=[s for s in sources if 'knowledge_base_id' in s]
            if current:await services.verify(current,tenant)
        except (KeyError,ValueError,TypeError):errors.append(f'{owner}: saved knowledge sources are unavailable or removed. Start a new run with current evidence.')
    return errors
