"""Authoritative platform-resource checks shared by submission and workers."""

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

async def kb_errors(services,workflow,tenant,checkpoints=None,dependencies=None,progress=None):
    """Knowledge bases must be searchable and every saved source must still be authorized before reuse.

    Reused work includes a saved Response and the items of a loop that is restored or resumed
    from its saved progress: their text is reused, so their saved sources are checked again."""
    from .platform_nodes import canonical,published_batches,source_list
    checkpoints=checkpoints or {};progress=progress or {};errors=[]
    for node in workflow.nodes:
        if node.type=='response' and checkpoints.get(node.id):
            try:
                for batch in published_batches(source_list(checkpoints[node.id]['sources'])):await services.verify(batch,tenant)
            except (KeyError,ValueError,TypeError):errors.append(f'{node.id}: saved knowledge sources are unavailable or removed. Start a new run with current evidence.')
            continue
        if node.type not in ('retrieve','query') or not node.config.get('knowledge_base_id'):continue
        try:
            await services.management.call('resolve',tenant,{'kb_id':node.config['knowledge_base_id']})
            cached=checkpoints.get(node.id)
            if cached:await services.verify(canonical(source_list(cached['sources'])),tenant)
        except (KeyError,ValueError,TypeError):errors.append(f'{node.id}: knowledge base or saved sources are unavailable. Check indexing status and permissions.')
    loops={node.id for node in workflow.nodes if node.type=='for_each'}
    def reused(owner):
        if owner in checkpoints:return True
        loop,_,index=owner.rpartition(':')  # A loop item's owner is '<loop id>:<index>' (iteration.item_owner).
        done=progress.get(loop)
        return loop in loops and index.isdigit() and (loop in checkpoints or isinstance(done,dict) and index in done)
    for owner,sources in (dependencies or {}).items():
        if not reused(owner):continue
        try:
            sources=source_list(sources)
            if len(sources)>120:raise ValueError('Invalid saved source dependencies.')
            if sources:await services.verify(sources,tenant)
        except (KeyError,ValueError,TypeError):errors.append(f'{owner}: saved knowledge sources are unavailable or removed. Start a new run with current evidence.')
    return errors
