"""Authoritative platform-resource checks shared by submission and workers."""
import json

def platform_errors(store,workflow,tenant):
    """Tool nodes must reference a usable workspace connection with the consent their operation needs."""
    from .registry import REGISTRY
    from .tool_service import ToolService
    tools=ToolService(store);errors=[]
    for node in workflow.nodes:
        if not node.type.startswith('tool_') or node.type not in REGISTRY:continue
        try:config=REGISTRY[node.type].config_model.model_validate(node.config)
        except ValueError:continue  # Graph validation reports malformed configuration.
        try:tools.check(config,tenant)
        except (KeyError,ValueError,TypeError):errors.append(f'{node.id}: connection, permissions or operation settings are unavailable. Check this node configuration.')
    return errors

async def kb_errors(services,workflow,tenant,checkpoints=None,dependencies=None):
    """Knowledge bases must be searchable and every saved source must still be authorized before reuse."""
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
            if not isinstance(sources,list) or len(sources)>120:raise ValueError('Invalid saved source dependencies.')
            if sources:await services.verify(sources,tenant)
        except (KeyError,ValueError,TypeError):errors.append(f'{owner}: saved knowledge sources are unavailable or removed. Start a new run with current evidence.')
    return errors
