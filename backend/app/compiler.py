"""Validate supported graph semantics, then compile deterministically to LangGraph."""
from dataclasses import dataclass,replace
from typing import Annotated, TypedDict
import asyncio
import inspect
import time
from langgraph.graph import StateGraph, START, END
from pydantic import ValidationError
from .models import Workflow
from .registry import REGISTRY, Context, validate_template


def _validate_flow(workflow: Workflow) -> list[str]:
    errors = []
    nodes = {n.id: n for n in workflow.nodes}
    if len(nodes) != len(workflow.nodes): errors.append('Node IDs must be unique.')
    if len({e.id for e in workflow.edges}) != len(workflow.edges): errors.append('Edge IDs must be unique.')
    incoming = {id: [] for id in nodes}
    outgoing = {id: [] for id in nodes}
    for edge in workflow.edges:
        if edge.source not in nodes or edge.target not in nodes:
            errors.append(f'Edge {edge.id} references a missing node.'); continue
        incoming[edge.target].append(edge.source)
        outgoing[edge.source].append(edge)
    triggers = [n.id for n in workflow.nodes if n.type in ('chat_input', 'manual_input')]
    if len(triggers) != 1: errors.append('Use exactly one chat input or manual trigger.')
    for node in workflow.nodes:
        if node.id == 'values': errors.append('Node ID values is reserved by the execution runtime.')
        definition = REGISTRY.get(node.type)
        if not definition:
            errors.append(f'Unknown node type: {node.type}.'); continue
        try:
            config = definition.config_model.model_validate(node.config)
            if node.type == 'prompt': validate_template(config.template)
            if node.type in ('llm','grounded_answer','agent','query') and config.provider in ('openai','claude') and not config.credential_id:
                errors.append(f'{node.id}: choose a credential for {config.provider}.')
        except (ValidationError, ValueError):
            errors.append(f'{node.id}: invalid configuration. Check required fields and template syntax; inline credentials are not allowed.')
        if set(node.inputs) != set(definition.inputs):
            errors.append(f'{node.id}: required inputs are {", ".join(definition.inputs) or "none"}.')
        links = outgoing[node.id]
        if node.type in ('chat_input', 'manual_input') and incoming[node.id]:
            errors.append(f'{node.id}: trigger cannot have incoming edges.')
        if node.type == 'response':
            if links: errors.append(f'{node.id}: response must be a terminal node.')
        elif node.type == 'condition':
            if len(links) != 2 or {e.sourceHandle for e in links} != {'true', 'false'}:
                errors.append(f'{node.id}: condition needs one true and one false edge.')
        elif len(links) != 1:
            errors.append(f'{node.id}: connect exactly one next node; parallel branches are not supported yet.')
        if node.type != 'condition' and any(e.sourceHandle not in (None, 'output') for e in links):
            errors.append(f'{node.id}: invalid source handle.')
    # Kahn ordering also catches disconnected cycles.
    degree = {id: len(parents) for id, parents in incoming.items()}
    queue = sorted(id for id, d in degree.items() if d == 0)
    order = []
    while queue:
        id = queue.pop(0); order.append(id)
        for edge in outgoing[id]:
            degree[edge.target] -= 1
            if degree[edge.target] == 0: queue.append(edge.target); queue.sort()
    if len(order) != len(nodes): errors.append('Workflow contains a cycle; loops are not supported yet.')
    reachable = set()
    def visit(id):
        if id in reachable: return
        reachable.add(id)
        for edge in outgoing[id]: visit(edge.target)
    if len(triggers) == 1: visit(triggers[0])
    for id in nodes:
        if id not in reachable: errors.append(f'{id}: node is unreachable from the trigger.')
    # A binding must be available along every possible path to the consumer.
    dominators = {}
    for id in order:
        parents = incoming[id]
        common = set.intersection(*(dominators.get(p, set()) | {p} for p in parents)) if parents else set()
        dominators[id] = common
        for name, ref in nodes[id].inputs.items():
            source, sep, port = ref.partition('.')
            source_def = REGISTRY.get(nodes[source].type) if source in nodes else None
            if not sep or source not in common or not source_def or port not in source_def.outputs:
                errors.append(f'{id}: invalid binding {name} = {ref}; use a declared output from a guaranteed upstream node.')
    if not any(n.type == 'response' for n in workflow.nodes): errors.append('Add a response output node.')
    return errors


def validate_workflow(workflow: Workflow) -> list[str]:
    from .platform_graph import split_graph
    flow,_,errors=split_graph(workflow)
    if len({n.id for n in workflow.nodes})!=len(workflow.nodes):errors.append('Node IDs must be unique.')
    if len({e.id for e in workflow.edges})!=len(workflow.edges):errors.append('Edge IDs must be unique.')
    return errors+_validate_flow(flow)


def merge_values(left: dict, right: dict):
    return {**left, **right}

class State(TypedDict):
    values: Annotated[dict[str, dict], merge_values]

@dataclass
class CompiledWorkflow:
    graph: object
    source: dict

async def silent(event):
    pass


def compile_workflow(workflow: Workflow, credential_resolver=lambda _: '', emit=silent, message='', completed=None, authorize_model=lambda _: None, knowledge_resolver=None,validate_cached=lambda node,outputs:None,platform_resolver=None):
    errors = validate_workflow(workflow)
    if errors: raise ValueError('\n'.join(errors))
    from .platform_graph import split_graph
    from .agent_runtime import execute_agent
    full_workflow=workflow
    workflow,_,_=split_graph(workflow)
    graph = StateGraph(State)
    context = Context(message=message, resolve_credential=credential_resolver, authorize_model=authorize_model,knowledge=knowledge_resolver,platform=platform_resolver)
    async def invoke_agent(id,text):return await execute_agent(id,text,full_workflow,context,emit)
    context.invoke_agent=invoke_agent
    context.workflow=full_workflow
    for node in sorted(workflow.nodes, key=lambda n: n.id):
        definition = REGISTRY[node.type]
        config = definition.config_model.model_validate(node.config)
        def handler_factory(node, definition, config):
            async def handler(state):
                if completed and node.id in completed:
                    outputs=completed[node.id]
                    validation=validate_cached(node,outputs)
                    if inspect.isawaitable(validation):await validation
                    await emit({'node_id':node.id,'status':'success','outputs':outputs,'cached':True,'duration_ms':0})
                    return {'values':{node.id:outputs}}
                inputs = {key: state['values'][ref.split('.')[0]][ref.split('.')[1]] for key, ref in node.inputs.items()}
                started = time.perf_counter()
                await emit({'node_id': node.id, 'status': 'running', 'inputs': inputs})
                try:
                    outputs = await definition.handler(inputs, config, replace(context,node_id=node.id,node_type=node.type,checkpoint_owner=node.id))
                    if set(outputs) != set(definition.outputs) or any(not isinstance(v, str) for v in outputs.values()):
                        raise ValueError('Node returned outputs that do not match its declared contract.')
                    await emit({'node_id': node.id, 'status': 'success', 'outputs': outputs,
                                'duration_ms': round((time.perf_counter()-started)*1000)})
                    return {'values': {node.id: outputs}}
                except asyncio.CancelledError:
                    await emit({'node_id': node.id, 'status': 'cancelled'}); raise
                except Exception as exc:
                    error = str(exc) if isinstance(exc, ValueError) else 'Node execution failed.'
                    await emit({'node_id': node.id, 'status': 'failed', 'error': error})
                    raise ValueError(error) from None
            return handler
        graph.add_node(node.id, handler_factory(node, definition, config))
    trigger = next(n.id for n in workflow.nodes if n.type in ('chat_input','manual_input'))
    graph.add_edge(START, trigger)
    for node in sorted(workflow.nodes, key=lambda n: n.id):
        edges = sorted((e for e in workflow.edges if e.source == node.id), key=lambda e:e.id)
        if node.type == 'condition':
            def route(state, id=node.id): return state['values'][id]['branch']
            graph.add_conditional_edges(node.id, route, {e.sourceHandle:e.target for e in edges})
        elif node.type == 'response': graph.add_edge(node.id, END)
        else: graph.add_edge(node.id, edges[0].target)
    return CompiledWorkflow(graph.compile(), full_workflow.model_dump(mode='json'))
