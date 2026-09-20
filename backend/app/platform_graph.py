"""Separate executable flow edges from tool and specialist dependencies."""
from .models import Workflow

TOOL_TYPES={'tool_http','tool_email','tool_jira','tool_confluence','tool_github','tool_python','retrieve','query'}

def split_graph(workflow):
    from .registry import REGISTRY
    nodes={n.id:n for n in workflow.nodes};errors=[];deps={n.id:[] for n in workflow.nodes};attached=set();specialists={}
    flow_edges=[e for e in workflow.edges if e.kind=='flow']
    flow_ids={n.id for n in workflow.nodes if n.type in ('chat_input','manual_input')}
    for edge in flow_edges:flow_ids.update((edge.source,edge.target))
    for edge in workflow.edges:
        if edge.kind=='store':
            errors.append('This workflow used the retired VectorDB nodes. Create a named knowledge base and reconnect a Retrieve node before importing or replaying it.')
            continue
        if edge.source not in nodes or edge.target not in nodes:
            if edge.kind!='flow':errors.append(f'Edge {edge.id} references a missing node.')
            continue
        source,target=nodes[edge.source],nodes[edge.target]
        if edge.kind=='flow':
            if edge.targetHandle not in (None,'input'):errors.append(f'{edge.id}: flow must enter the input port.')
            continue
        if edge.kind=='tool':
            if source.type not in TOOL_TYPES or target.type!='agent':errors.append('Tool connections must run from a tool or retrieval node to an Agent.')
            if edge.sourceHandle not in (None,'output') or edge.targetHandle not in (None,'tools'):errors.append('Tool connections must enter the left tools port.')
            owner,dependency=target.id,source.id
        else:
            if source.type!='agent' or target.type!='agent':errors.append('Specialist connections require two Agent nodes.')
            if edge.sourceHandle not in (None,'agents') or edge.targetHandle not in (None,'input'):errors.append('Specialist connections must start at the right agents port.')
            if target.id in specialists:errors.append(f'{target.id}: specialist must have exactly one parent.')
            specialists[target.id]=source.id;owner,dependency=source.id,target.id
        if dependency in deps[owner]:errors.append(f'{edge.id}: duplicate resource attachment.')
        deps[owner].append(dependency);attached.add(dependency)
    for node in workflow.nodes:
        if node.type in ('retrieve','query') and not node.config.get('knowledge_base_id'):errors.append(f'{node.id}: select a knowledge base.')
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
