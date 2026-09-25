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
from .approvals import ApprovalPause


class PersistedNodeCancellation(asyncio.CancelledError):
    """Cancellation observed after a terminal node event committed successfully."""


def is_write_node(node, definition):
    """Writes are refused at validation rather than skipped at runtime, so the author learns now."""
    if not node.type.startswith('tool_'):return False
    from .tool_service import CONFIGS, is_write
    model=CONFIGS.get(node.type)
    if model is None:return False
    try:return bool(is_write(node.type, model.model_validate(node.config)))
    except Exception:return False


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
            if node.type in ('llm','agent','query') and config.provider in ('openai','claude') and not config.credential_id:
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
        elif node.on_error == 'route':
            if len(links) != 2 or {e.sourceHandle for e in links} != {None, 'error'} and {e.sourceHandle for e in links} != {'output', 'error'}:
                errors.append(f'{node.id}: on_error route needs exactly one normal edge and one error edge.')
        elif len(links) != 1:
            errors.append(f'{node.id}: connect exactly one next node; parallel branches are not supported yet.')
        if node.type != 'condition' and node.on_error != 'route' and any(e.sourceHandle not in (None, 'output') for e in links):
            errors.append(f'{node.id}: invalid source handle.')
        if node.on_error != 'route' and any(e.sourceHandle == 'error' for e in links):
            errors.append(f"{node.id}: an error edge requires on_error 'route'.")
        if node.retry.attempts and definition and is_write_node(node, definition):
            errors.append(f'{node.id}: a node that writes externally cannot be retried; a repeated attempt may duplicate a completed write.')
        if node.on_error == 'continue':
            readers=[other.id for other in workflow.nodes
                     for ref in other.inputs.values() if ref.partition('.')[0] == node.id]
            if readers:
                errors.append(f"{node.id}: on_error 'continue' is not allowed while {', '.join(sorted(set(readers)))} reads its output; use 'route' instead.")
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


def type_warnings(workflow: Workflow) -> list[str]:
    """Check declarations only; invalid bindings remain the error validator's job."""
    from .port_types import compatibility
    nodes={node.id:node for node in workflow.nodes}
    warnings=[]
    for node in workflow.nodes:
        target_def=REGISTRY.get(node.type)
        if target_def is None:continue
        for name,ref in node.inputs.items():
            source,separator,port=ref.partition('.')
            source_node=nodes.get(source)
            source_def=REGISTRY.get(source_node.type) if source_node else None
            if not separator or source_def is None or port not in source_def.outputs or name not in target_def.inputs:continue
            source_type=source_def.outputs[port];target_type=target_def.inputs[name]
            result=compatibility(source_type,target_type)
            if result=='ok':continue
            action=('conversion to text would be required' if target_type=='string' else 'parsing or element validation would be required') if result=='coerce' else 'the declared structures are incompatible'
            warnings.append(f"{node.id}: input '{name}' takes {target_type} but {ref} provides {source_type}; {action}. Phase 1 warning only: no conversion or rejection is performed.")
    return warnings


MAX_NODE_RETRY_DELAY=30.0

def retry_delay(attempt,base_delay):
    """Full jitter, matching providers.with_retries: synchronized clients must not retry together."""
    import random
    return random.uniform(0,min(MAX_NODE_RETRY_DELAY,base_delay*2**min(attempt,30)))


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


def compile_workflow(workflow: Workflow, credential_resolver=lambda _: '', emit=silent, message='', completed=None, authorize_model=lambda _: None, knowledge_resolver=None,validate_cached=lambda node,outputs:None,platform_resolver=None,citation_counter=0,agent_frames=None):
    errors = validate_workflow(workflow)
    if errors: raise ValueError('\n'.join(errors))
    from .platform_graph import split_graph
    from .agent_runtime import execute_agent,evidence_from_outputs
    from .evidence_registry import prepare_checkpoint_labels,emit_evidence_notice
    from .observability import journal
    full_workflow=workflow
    workflow,_,_=split_graph(workflow)
    graph = StateGraph(State)
    run_state={'evidence':[],'citation_counter':citation_counter,'agent_frames':agent_frames or {}}  # Every passage retrieved in this run, under a run-unique citation label.
    prepare_checkpoint_labels(run_state,completed)
    context = Context(message=message, resolve_credential=credential_resolver, authorize_model=authorize_model,knowledge=knowledge_resolver,platform=platform_resolver,run=run_state,emit=emit)
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
                    evidence_from_outputs(run_state,outputs)  # Restored evidence keeps its original labels.
                    await emit_evidence_notice(run_state,emit,node.id)
                    await emit({'node_id':node.id,'status':'success','outputs':outputs,'cached':True,'duration_ms':0})
                    return {'values':{node.id:outputs}}
                inputs = {key: state['values'][ref.split('.')[0]][ref.split('.')[1]] for key, ref in node.inputs.items()}
                started = time.perf_counter()
                await emit({'node_id': node.id, 'status': 'running', 'inputs': inputs})
                attempts=node.retry.attempts+1
                for attempt in range(attempts):
                  try:
                      # Legacy prompt/LLM nodes must not bypass an upstream guard.
                      def guarded_dependency(identifier,seen):
                          if identifier in seen:return False
                          seen.add(identifier)
                          from .answerability_guard import from_outputs
                          decision=from_outputs(state['values'].get(identifier,{}))
                          if decision and decision['decision']=='abstain':return True
                          upstream=next((n for n in workflow.nodes if n.id==identifier),None)
                          return bool(upstream and any(guarded_dependency(ref.split('.')[0],seen) for ref in upstream.inputs.values()))
                      if node.type in ('llm','agent','query') and any(guarded_dependency(ref.split('.')[0],set()) for ref in node.inputs.values()):
                          from .agent_runtime import immediate_abstention
                          refusal=immediate_abstention({'decision':'abstain'})
                          outputs={'text':refusal['text'],'provider':'none'} if node.type=='llm' else refusal
                      else:
                          outputs = await definition.handler(inputs, config, replace(context,node_id=node.id,node_type=node.type,checkpoint_owner=node.id))
                      if set(outputs) != set(definition.outputs) or any(not (isinstance(value,list) and all(isinstance(item,dict) for item in value)) if definition.outputs[name]=='array<object>' else not isinstance(value,str) for name,value in outputs.items()):
                          raise ValueError('Node returned outputs that do not match its declared contract.')
                      evidence_from_outputs(run_state,outputs)
                      await emit_evidence_notice(run_state,emit,node.id)
                      event={'node_id': node.id, 'status': 'success', 'outputs': outputs,'duration_ms': round((time.perf_counter()-started)*1000)}
                      event.update(run_state.get('tool_output_metadata',{}).get(node.id,{}))
                      usage=run_state.get('usage',{}).get(node.id)
                      if usage:event['usage']=usage  # Model calls made by this node (an agent's tool loop counts as one node).
                      await emit(event)
                      return {'values': {node.id: outputs}}
                  except ApprovalPause as exc:
                      usage=run_state.get('usage',{}).get(node.id)
                      if usage:await emit({'node_id':node.id,'status':'paused','transient':True,'usage':usage})
                      raise
                  except asyncio.CancelledError as exc:
                      if isinstance(exc,PersistedNodeCancellation):
                          # Python 3.12 asyncio.timeout recognizes the exact base type.
                          raise asyncio.CancelledError() from None
                      await emit({'node_id': node.id, 'status': 'cancelled',**({'usage':run_state['usage'][node.id]} if run_state.get('usage',{}).get(node.id) else {})})
                      raise
                  except Exception as exc:
                      from .tool_service import TransientToolError, UncertainWriteError
                      error = str(exc) if isinstance(exc, ValueError) else 'Node execution failed.'
                      usage_fields={'usage':run_state['usage'][node.id]} if run_state.get('usage',{}).get(node.id) else {}
                      # An uncertain write is never retried: the outcome is unknown, so a second
                      # attempt could duplicate a completed external mutation.
                      transient=isinstance(exc,TransientToolError) and not isinstance(exc,UncertainWriteError)
                      if transient and attempt<attempts-1:
                          delay=retry_delay(attempt,node.retry.base_delay)
                          await emit({'node_id':node.id,'status':'retrying','transient':True,'error':error,
                                      'attempt':attempt+1,'delay_seconds':round(delay,3),**usage_fields})
                          journal(event='node.retry',node_id=node.id,attempt=attempt+1,
                                  delay_seconds=delay,error_type=type(exc).__name__)
                          await asyncio.sleep(delay)
                          continue
                      failure={'node_id': node.id, 'status': 'failed', 'error': error,**usage_fields}
                      if attempts>1:failure['attempt']=attempt+1
                      if node.on_error=='fail':
                          await emit(failure)
                          raise ValueError(error) from None
                      # continue and route both keep the run alive; the graph decides where it goes.
                      run_state.setdefault('failed_nodes',set()).add(node.id)
                      await emit({**failure,'recovered':True})
                      journal(event='node.recovered',node_id=node.id,reason_code=node.on_error)
                      return {'values': {node.id: {}}}
            return handler
        graph.add_node(node.id, handler_factory(node, definition, config))
    trigger = next(n.id for n in workflow.nodes if n.type in ('chat_input','manual_input'))
    graph.add_edge(START, trigger)
    for node in sorted(workflow.nodes, key=lambda n: n.id):
        edges = sorted((e for e in workflow.edges if e.source == node.id), key=lambda e:e.id)
        if node.type == 'condition':
            def route(state, id=node.id): return state['values'][id]['branch']
            graph.add_conditional_edges(node.id, route, {e.sourceHandle:e.target for e in edges})
        elif node.on_error == 'route':
            # Recovery is control flow, not data: the branch is chosen by whether the node
            # recorded a failure, and the failed node exposes no bindable error value.
            def recover(state, id=node.id): return 'error' if id in run_state.get('failed_nodes',()) else 'output'
            targets={('error' if e.sourceHandle=='error' else 'output'):e.target for e in edges}
            graph.add_conditional_edges(node.id, recover, targets)
        elif node.type == 'response': graph.add_edge(node.id, END)
        else: graph.add_edge(node.id, edges[0].target)
    return CompiledWorkflow(graph.compile(), full_workflow.model_dump(mode='json'))
