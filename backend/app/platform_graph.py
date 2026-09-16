"""Separate executable flow edges from tool, specialist and store dependencies."""
from .models import Workflow

TOOL_TYPES={'tool_http','tool_email','tool_jira','tool_confluence','tool_github','tool_python','retrieve','query'}
VECTOR_TYPES={'vector_faiss','vector_chroma','vector_elasticsearch','vector_pinecone'}

def split_graph(workflow):
    from .registry import REGISTRY
    nodes={n.id:n for n in workflow.nodes};errors=[];deps={n.id:[] for n in workflow.nodes};attached=set();specialists={};stores={}
    flow_edges=[e for e in workflow.edges if e.kind=='flow']
    flow_ids={n.id for n in workflow.nodes if n.type in ('chat_input','manual_input')}
    for edge in flow_edges:flow_ids.update((edge.source,edge.target))
    for edge in workflow.edges:
        if edge.source not in nodes or edge.target not in nodes:
            if edge.kind!='flow':errors.append(f'Edge {edge.id} references a missing node.')
            continue
        source,target=nodes[edge.source],nodes[edge.target]
        if edge.kind=='flow':
            if source.type in VECTOR_TYPES or target.type in VECTOR_TYPES:errors.append('VectorDB nodes attach to retrieval nodes; they are not flow steps.')
            if edge.targetHandle not in (None,'input'):errors.append(f'{edge.id}: flow must enter the input port.')
            continue
        if edge.kind=='tool':
            if source.type not in TOOL_TYPES or target.type!='agent':errors.append('Tool connections must run from a tool or retrieval node to an Agent.')
            if edge.sourceHandle not in (None,'output') or edge.targetHandle not in (None,'tools'):errors.append('Tool connections must enter the left tools port.')
            owner,dependency=target.id,source.id
        elif edge.kind=='agent':
            if source.type!='agent' or target.type!='agent':errors.append('Specialist connections require two Agent nodes.')
            if edge.sourceHandle not in (None,'agents') or edge.targetHandle not in (None,'input'):errors.append('Specialist connections must start at the right agents port.')
            if target.id in specialists:errors.append(f'{target.id}: specialist must have exactly one parent.')
            specialists[target.id]=source.id;owner,dependency=source.id,target.id
        else:
            if source.type not in VECTOR_TYPES or target.type not in ('retrieve','query'):errors.append('Store connections must run from a VectorDB node to Retrieve or Query.')
            if edge.sourceHandle not in (None,'output','store') or edge.targetHandle not in (None,'store'):errors.append('Store connections must enter the retrieval store port.')
            stores[target.id]=stores.get(target.id,0)+1;owner,dependency=target.id,source.id
        if dependency in deps[owner]:errors.append(f'{edge.id}: duplicate resource attachment.')
        deps[owner].append(dependency);attached.add(dependency)
    for node in workflow.nodes:
        if node.type in ('retrieve','query'):
            if node.config.get('knowledge_base_id'):
                if stores.get(node.id,0) or node.config.get('storage_path'):errors.append(f'{node.id}: select a named knowledge base or a legacy store attachment, not both.')
            elif stores.get(node.id,0)!=1:errors.append(f'{node.id}: select a knowledge base.')
        if node.id in attached and node.id in flow_ids:errors.append(f'{node.id}: an attached callable cannot also be a flow step.')
        if node.id in attached and node.inputs:errors.append(f'{node.id}: attached callables receive input from their caller; clear flow bindings.')
        if node.id not in flow_ids:
            definition=REGISTRY.get(node.type)
            if not definition:errors.append(f'Unknown node type: {node.type}.');continue
            try:definition.config_model.model_validate(node.config)
            except ValueError:errors.append(f'{node.id}: invalid configuration. Check required settings.')
    visiting=set();visited=set()
    def visit(id,depth=0):
        if id in visiting:errors.append('Attachment cycle is not allowed.');return
        if id in visited:return
        if depth>100:errors.append('Too many attachment levels.');return
        visiting.add(id)
        for dep in deps.get(id,[]):visit(dep,depth+1)
        visiting.remove(id);visited.add(id)
    for id in sorted(flow_ids):visit(id)
    for id in nodes:
        if id not in visited:errors.append(f'{id}: node is unconnected or unreachable from the workflow.')
    flow=workflow.model_copy(update={'nodes':[n for n in workflow.nodes if n.id in flow_ids],'edges':flow_edges})
    return flow,deps,errors
