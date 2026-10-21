"""Validate supported graph semantics, then compile deterministically to LangGraph."""
from dataclasses import dataclass,replace
from typing import Annotated, TypedDict
import asyncio
import inspect
import json
import time
from langgraph.graph import StateGraph, START, END
from pydantic import ValidationError
from .models import Workflow
from .request_contracts import is_control, node_routes
from .request_safety import VERIFIERS
from .registry import REGISTRY, RESERVED_PORT_PREFIX, Context, validate_template
from . import exact_json
from .approvals import ApprovalPause
from .execution_policy import (ExecutionIdentity, execute_with_policy, charges_action,
                               accounting_snapshot,
                               retry_delay, MAX_NODE_RETRY_DELAY)


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


def condition_config_error(node_id, error):
    where = '.'.join(str(part) for part in error.get('loc', ()))
    message = error['msg'].removeprefix('Value error, ')
    return f'{node_id}: {where}: {message}' if where else f'{node_id}: {message}'


def binding_guarantees(order, arrivals, nodes):
    """For each node, the upstream nodes that ran on every path to it, and those whose outputs
    exist on every path to it; a node may bind only to the second set.

    The two differ because an error edge leaves a node that ran but failed and recovered with
    no outputs, so it passes on the first set but not the second; so does a node using
    on_error 'continue'. Propagation is per edge, not per parent, because a routed node's
    normal and error edges may both reach the same consumer.
    """
    def yields(edge):
        return edge.sourceHandle != 'error' and nodes[edge.source].on_error != 'continue'
    executed, available = {}, {}
    for id in order:
        edges = arrivals[id]
        executed[id] = set.intersection(*(executed.get(e.source, set()) | {e.source} for e in edges)) if edges else set()
        available[id] = set.intersection(*(available.get(e.source, set()) | ({e.source} if yields(e) else set()) for e in edges)) if edges else set()
    return executed, available


def flow_order(workflow: Workflow):
    """Kahn order over flow edges, with each node's arriving edges. A cycle leaves its nodes out."""
    nodes = {n.id: n for n in workflow.nodes}
    outgoing = {id: [] for id in nodes}
    arrivals = {id: [] for id in nodes}
    for edge in workflow.edges:
        if edge.source in nodes and edge.target in nodes:
            outgoing[edge.source].append(edge); arrivals[edge.target].append(edge)
    degree = {id: len(arrivals[id]) for id in nodes}
    queue = sorted(id for id, d in degree.items() if d == 0)
    order = []
    while queue:
        id = queue.pop(0); order.append(id)
        for edge in outgoing[id]:
            degree[edge.target] -= 1
            if degree[edge.target] == 0: queue.append(edge.target); queue.sort()
    return order, arrivals, nodes


def bindable_outputs(workflow: Workflow) -> dict[str, list[str]]:
    """Every source.port each node may bind, by the same rule validation enforces. The editor
    offers exactly these, so a valid binding is never shown as unset."""
    from .platform_graph import split_graph
    flow, _, _ = split_graph(workflow)
    order, arrivals, nodes = flow_order(flow)
    _, available = binding_guarantees(order, arrivals, nodes)
    result = {}
    for id in order:
        refs = []
        for source in sorted(available[id], key=order.index):
            definition = REGISTRY.get(nodes[source].type)
            if definition:
                refs += [f'{source}.{port}' for port in definition.outputs if not port.startswith(RESERVED_PORT_PREFIX)]
        result[id] = refs
    return result


def locate(messages: list[str], workflow: Workflow) -> list[dict]:
    """Each message with the node it concerns, matched against the workflow's actual node IDs
    (messages about a node start with '<id>: '), so the editor can name and highlight it."""
    ids = {n.id for n in workflow.nodes}
    issues = []
    for message in messages:
        head, separator, rest = message.partition(': ')
        located = separator and head in ids
        issues.append({'node_id': head if located else None, 'message': rest if located else message})
    return issues


def _validate_flow(workflow: Workflow) -> list[str]:
    errors = []
    nodes = {n.id: n for n in workflow.nodes}
    if len(nodes) != len(workflow.nodes): errors.append('Node IDs must be unique.')
    if len({e.id for e in workflow.edges}) != len(workflow.edges): errors.append('Edge IDs must be unique.')
    incoming = {id: [] for id in nodes}
    outgoing = {id: [] for id in nodes}
    arrivals = {id: [] for id in nodes}
    for edge in workflow.edges:
        if edge.source not in nodes or edge.target not in nodes:
            errors.append(f'Edge {edge.id} references a missing node.'); continue
        incoming[edge.target].append(edge.source)
        outgoing[edge.source].append(edge)
        arrivals[edge.target].append(edge)
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
            if node.type in ('llm','agent','query','request_router','request_reader') and config.provider in ('openai','claude') and not config.credential_id:
                errors.append(f'{node.id}: choose a credential for {config.provider}.')
        except ValidationError as exc:
            if node.type in ('condition', 'calculate', 'switch'):
                # Condition, Calculate and Switch settings hold no credentials, so their messages are
                # shown as written: an invalid comparison or expression says what is wrong.
                errors.extend(condition_config_error(node.id, error) for error in exc.errors())
            else:
                errors.append(f'{node.id}: invalid configuration. Check required fields and template syntax; inline credentials are not allowed.')
        except ValueError:
            errors.append(f'{node.id}: invalid configuration. Check required fields and template syntax; inline credentials are not allowed.')
        try:routes = node_routes(node, definition)
        except ValueError as exc:
            errors.append(f'{node.id}: invalid control routes. {exc}');continue
        if is_control(definition) and not routes:continue  # Its settings error is already reported.
        if not set(definition.inputs) <= set(node.inputs) or set(node.inputs) - (set(definition.inputs) | set(definition.optional_inputs)):
            errors.append(f'{node.id}: required inputs are {", ".join(definition.inputs) or "none"}.')
        links = outgoing[node.id]
        if node.type in ('chat_input', 'manual_input') and incoming[node.id]:
            errors.append(f'{node.id}: trigger cannot have incoming edges.')
        if node.type == 'response':
            if links: errors.append(f'{node.id}: response must be a terminal node.')
        elif routes:
            if len(links) != len(routes) or {e.sourceHandle for e in links} != set(routes):
                errors.append(f'{node.id}: {node.type} needs exactly one edge for each control route: {", ".join(routes)}.')
            # Routing reads the branch, so recovering without one leaves no path to take.
            if node.on_error != 'fail':
                errors.append(f"{node.id}: a {node.type} cannot use on_error '{node.on_error}'; its branch is required to choose the next node.")
        elif node.on_error == 'route':
            if len(links) != 2 or {e.sourceHandle for e in links} != {None, 'error'} and {e.sourceHandle for e in links} != {'output', 'error'}:
                errors.append(f'{node.id}: on_error route needs exactly one normal edge and one error edge.')
        elif len(links) != 1:
            errors.append(f'{node.id}: connect exactly one next node; parallel branches are not supported yet.')
        if not routes and node.on_error != 'route' and any(e.sourceHandle not in (None, 'output') for e in links):
            errors.append(f'{node.id}: invalid source handle.')
        if node.on_error != 'route' and any(e.sourceHandle == 'error' for e in links):
            errors.append(f"{node.id}: an error edge requires on_error 'route'.")
        if node.retry.attempts and definition and is_write_node(node, definition):
            errors.append(f'{node.id}: a node that writes externally cannot be retried; a repeated attempt may duplicate a completed write.')
        if node.type == 'for_each':
            # Body attachment is validated in split_graph, which sees non-flow edges.
            # A list input is structural, not a conversion: text has nothing to iterate over.
            for name, ref in node.inputs.items():
                source, separator, port = ref.partition('.')
                source_def = REGISTRY.get(nodes[source].type) if source in nodes else None
                if separator and source_def and port in source_def.outputs:
                    provided = source_def.outputs[port]
                    schema=nodes[source].config.get('expected_schema')
                    verified_array=nodes[source].type in VERIFIERS and port=='text' and isinstance(schema,dict) and schema.get('type')=='array'
                    if not provided.startswith('array') and not verified_array:
                        errors.append(f'{node.id}: items must come from a list; {ref} provides {provided}.')
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
    executed, available = binding_guarantees(order, arrivals, nodes)
    for id in order:
        ran, has = executed[id], available[id]
        for name, ref in nodes[id].inputs.items():
            source, sep, port = ref.partition('.')
            source_def = REGISTRY.get(nodes[source].type) if source in nodes else None
            # A reserved port is never bindable, even if a definition was altered to declare one.
            declared=bool(sep and source_def and port in source_def.outputs and not port.startswith(RESERVED_PORT_PREFIX))
            if declared and source in has:
                continue
            if declared and source in ran:
                if nodes[source].on_error == 'continue':
                    continue  # Reported above with the fix: use 'route'.
                errors.append(f'{id}: invalid binding {name} = {ref}; {ref} is unavailable on a possible error path, '
                              f'where {source} fails and is routed on without outputs. Bind a value that exists on every path to {id}.')
            else:
                errors.append(f'{id}: invalid binding {name} = {ref}; use a declared output from a guaranteed upstream node.')
    if not any(n.type == 'response' for n in workflow.nodes): errors.append('Add a response output node.')
    return errors


def validate_workflow(workflow: Workflow) -> list[str]:
    from .platform_graph import split_graph
    flow,deps,errors=split_graph(workflow)
    if len({n.id for n in workflow.nodes})!=len(workflow.nodes):errors.append('Node IDs must be unique.')
    if len({e.id for e in workflow.edges})!=len(workflow.edges):errors.append('Edge IDs must be unique.')
    errors += _validate_flow(flow)
    errors += request_contract_errors(workflow, flow, deps)
    return errors


def request_contract_errors(workflow, flow, deps):
    """Structural prerequisites only. Value-bound runtime eligibility is a later boundary.

    Availability is outcome-aware (including error routes). A checked value is a data
    dependency, not permission granted by a guard's mere presence elsewhere in the DAG.
    """
    guards = {'greeting_cache', 'request_router', *VERIFIERS}
    if workflow.contract == 1:
        return [f'{n.id}: request admission nodes require workflow contract 2.' for n in workflow.nodes if n.type in guards] + [
            f'{n.id}: Switch requires a contract-2 workflow.' for n in workflow.nodes if n.type == 'switch']
    errors = []
    order, arrivals, nodes = flow_order(flow)
    _, available = binding_guarantees(order, arrivals, nodes)
    full = {n.id: n for n in workflow.nodes}
    outgoing = {id: [e for e in flow.edges if e.source == id] for id in nodes}
    triggers = [n for n in flow.nodes if n.type in ('chat_input', 'manual_input')]
    if len(triggers) != 1:return ['Contract 2 requires exactly one entry trigger.']
    trigger = triggers[0]
    original = trigger.id + '.message'
    first = outgoing[trigger.id]
    cache = nodes.get(first[0].target) if len(first) == 1 else None
    router_edges = [e for e in outgoing.get(cache.id, []) if e.sourceHandle == 'continue'] if cache else []
    router = nodes.get(router_edges[0].target) if len(router_edges) == 1 else None
    # The reader entry: Input → Request reader, optionally through independent record reads
    # that supply its reference. No model and no transformation runs before the reader.
    reader, step, seen = None, cache, set()
    while step is not None and step.type != 'request_reader' and independent_reference(step.id + '.text', nodes):
        # A cycle of reads never reaches a reader; the flow validator reports the cycle.
        if step.id in seen:step = None;break
        seen.add(step.id)
        nxt = outgoing.get(step.id, [])
        step = nodes.get(nxt[0].target) if len(nxt) == 1 else None
    if step is not None and step.type == 'request_reader':reader = step
    legacy = bool(cache and cache.type == 'greeting_cache' and router and router.type == 'request_router')
    if not legacy and not reader:
        errors.append('Contract 2 entry must be Input → Request reader (or Input → Greeting cache → Request router on continue).')
    for node in flow.nodes:
        if node.type in guards:
            for edge in outgoing[node.id]:
                if edge.sourceHandle == 'reply' and nodes.get(edge.target) and nodes[edge.target].type != 'response':
                    errors.append(f'{node.id}: reply must terminate at a Response; it cannot reach an action.')
        if node.type in ('greeting_cache', 'request_router', 'request_reader') and node.inputs.get('message') != original:
            errors.append(f'{node.id}: bind the original trigger message; admission text is not extraction evidence.')
        if node.type == 'request_router' and (not router or node.id != router.id):
            errors.append(f'{node.id}: request router must be the declared entry router.')
        if node.type == 'greeting_cache' and (not cache or node.id != cache.id):
            errors.append(f'{node.id}: greeting cache must be the declared entry cache.')
        if node.type == 'request_reader' and (not reader or node.id != reader.id):
            errors.append(f'{node.id}: the request reader must be the declared entry reader.')
        if node.type in VERIFIERS:
            if node.type == 'verify_extraction' and node.inputs.get('original') != original:errors.append(f'{node.id}: verification requires the original trigger message.')
            reference = node.inputs.get('reference')
            rules = node.config.get('field_rules', [])
            if isinstance(rules, list) and any(r.get('kind') == 'reference_member' for r in rules if isinstance(r, dict)) and not reference:
                errors.append(f'{node.id}: reference_member rules require a reference binding.')
            if reference and not independent_reference(reference, nodes):
                errors.append(f'{node.id}: reference must come from an independent read, not a model, echo or transformation.')
        if node.type == 'switch':
            from .provenance import STATIC_TRUSTED, static_label
            # The existing trust rule, not a new list: a verifier's checked text (or a Prompt or
            # Calculate built only from it), or independent source data such as the trigger.
            value = node.inputs.get('value', '')
            if not (checked_dependency(value, nodes) or static_label(full, value) == STATIC_TRUSTED):
                errors.append(f'{node.id}: Route on checked values, or use a Condition, which supports review.')
        if node.type == 'agent' and node.config.get('role') in ('extraction', 'classification') and node.inputs.get('input') != original:
            errors.append(f'{node.id}: extraction must bind the original trigger message.')
        model_inputs = any(unchecked_model_input(ref, full, deps) for ref in node.inputs.values())
        consequential = is_write_node(node, REGISTRY.get(node.type)) or (model_inputs and (node.type == 'condition' or (node.type == 'calculate' and node.config.get('mode') == 'check')))
        if consequential:
            if not any(nodes[id].type in VERIFIERS for id in available.get(node.id, set())):
                errors.append(f'{node.id}: consequential execution requires successful verification on every path.')
            if model_inputs:
                errors.append(f'{node.id}: bind verified values; a raw model output cannot bypass extraction checking.')
            if is_write_node(node, REGISTRY.get(node.type)) and not any(checked_dependency(ref, nodes) for ref in node.inputs.values()):
                errors.append(f'{node.id}: an external write must bind a verified value or its deterministic calculation.')
        attached = set()
        def visit(id):
            for dep in deps.get(id, []):
                if dep not in attached:attached.add(dep);visit(dep)
        visit(node.id)
        if any(is_write_node(full[id], REGISTRY.get(full[id].type)) for id in attached if id in full):
            if not any(nodes[id].type in VERIFIERS for id in available.get(node.id, set())) or not any(checked_dependency(ref, nodes) for ref in node.inputs.values()):
                errors.append(f'{node.id}: attached writes require verification before their caller; router acceptance is insufficient.')
    return errors


def independent_reference(ref, nodes):
    source, sep, port = ref.partition('.')
    node = nodes.get(source)
    # Read configuration and binding are checked normally too. Independence of the
    # returned records still requires the runtime evidence/receipt checks in Task 5.
    return bool(sep and node and node.type in ('tool_jira', 'tool_confluence', 'tool_github', 'retrieve') and not is_write_node(node, REGISTRY.get(node.type)))


def unchecked_model_input(ref, nodes, deps=None, seen=None):
    source, sep, port = ref.partition('.')
    node = nodes.get(source)
    seen = set() if seen is None else seen
    if not sep or not node or source in seen:return False
    seen = seen | {source}
    if node.type in VERIFIERS and port == 'text':return False
    if node.type in ('agent', 'llm', 'query', 'request_router', 'request_reader'):return True
    # A loop's flow inputs do not describe what its attached body produces. Walk
    # callable dependencies as well, including specialist/tool chains. Otherwise an
    # attached Agent's guessed result would look like a deterministic Jira list.
    dependencies = deps or {}
    def calls_model(id, visited):
        if id in visited or id not in nodes:return False
        if nodes[id].type in ('agent', 'llm', 'query', 'request_router', 'request_reader'):return True
        return any(calls_model(child, visited | {id}) for child in dependencies.get(id, []))
    return any(calls_model(id, seen) for id in dependencies.get(source, [])) or any(
        unchecked_model_input(value, nodes, dependencies, seen) for value in node.inputs.values())


def checked_dependency(ref, nodes, seen=None):
    source, sep, port = ref.partition('.')
    node = nodes.get(source)
    seen = set() if seen is None else seen
    if not sep or not node or source in seen:return False
    if node.type in VERIFIERS:return port == 'text'
    if node.type not in ('prompt', 'calculate'):return False
    return bool(node.inputs) and all(checked_dependency(value, nodes, seen | {source}) for value in node.inputs.values())


def type_warnings(workflow: Workflow) -> list[str]:
    """Check declarations only; invalid bindings remain the error validator's job."""
    from .port_types import compatibility
    nodes={node.id:node for node in workflow.nodes}
    warnings=[]
    for node in workflow.nodes:
        target_def=REGISTRY.get(node.type)
        if target_def is None:continue
        input_types={**target_def.inputs,**target_def.optional_inputs}
        for name,ref in node.inputs.items():
            source,separator,port=ref.partition('.')
            source_node=nodes.get(source)
            source_def=REGISTRY.get(source_node.type) if source_node else None
            if not separator or source_def is None or port not in source_def.outputs or name not in input_types:continue
            source_type=source_def.outputs[port];target_type=input_types[name]
            result=compatibility(source_type,target_type)
            if result=='ok':continue
            action=('conversion to text would be required' if target_type=='string' else 'parsing or element validation would be required') if result=='coerce' else 'the declared structures are incompatible'
            warnings.append(f"{node.id}: input '{name}' takes {target_type} but {ref} provides {source_type}; {action}. Phase 1 warning only: no conversion or rejection is performed.")
    return warnings


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


def restored_tokens(accounting):
    """Tokens spent by earlier attempts. Older snapshots predate tokens_spent, so fall back
    to summing their usage totals rather than restarting the ceiling at zero."""
    value=(accounting or {}).get('tokens_spent')
    if type(value) is int and value>=0:return value
    from .token_budget import tokens_spent
    return tokens_spent({'usage':(accounting or {}).get('usage') or {}})


def compile_workflow(workflow: Workflow, credential_resolver=lambda _: '', emit=silent, message='', completed=None, authorize_model=lambda _: None, knowledge_resolver=None,validate_cached=lambda node,outputs:None,platform_resolver=None,citation_counter=0,agent_frames=None,loop_progress=None,accounting=None,accounting_sink=None,persist_accounting=None,persist_spend=None,action_settled=None,run_id=None,tenant_id='local'):
    errors = validate_workflow(workflow)
    if errors: raise ValueError('\n'.join(errors))
    from .platform_graph import split_graph
    from .agent_runtime import execute_agent,evidence_from_outputs,run_budget
    from .evidence_registry import prepare_checkpoint_labels,emit_evidence_notice
    from .observability import journal
    from . import request_safety,provenance
    from .storage import new_id
    full_workflow=workflow
    workflow,_,_=split_graph(workflow)
    graph = StateGraph(State)
    ceiling=run_budget()
    # Budgets are run-wide, so a resumed run continues spending where it stopped. A fresh
    # counter here would let an interrupted run replenish its ceiling by being resumed,
    # and would restart the invocation counters that key approval idempotency.
    spent=dict(accounting or {})
    budget=dict(spent.get('action_budget') or {'remaining':ceiling,'counter':0,'ceiling':ceiling})
    budget['ceiling']=ceiling
    budget['remaining']=min(budget.get('remaining',ceiling),max(0,ceiling-budget.get('counter',0)))
    run_state={'run_id':run_id or new_id(),'tenant_id':tenant_id,'contract':full_workflow.contract,
               'evidence':[],'citation_counter':citation_counter,'agent_frames':agent_frames or {},
               'action_budget':budget,
               # Reporting starts empty each attempt; the ceiling carries the cumulative total.
               'usage':{},
               'usage_by_invocation':{},
               'token_baseline':restored_tokens(spent),
               'accounting_sink':accounting_sink,
               'persist_accounting':persist_accounting,
               'persist_spend':persist_spend,
               'action_settled':action_settled}  # Every passage retrieved in this run, under a run-unique citation label.
    prepare_checkpoint_labels(run_state,completed)
    run_state['loop_progress']=dict(loop_progress or {})
    context = Context(message=message, resolve_credential=credential_resolver, authorize_model=authorize_model,knowledge=knowledge_resolver,platform=platform_resolver,run=run_state,emit=emit)
    async def invoke_agent(id,text,identity=None):
        child=replace(context,node_id=id,node_type='agent',checkpoint_owner=id,
                      execution_identity=identity)
        return await execute_agent(id,text,full_workflow,child,emit,
                                   budget=run_state['action_budget'])
    context.invoke_agent=invoke_agent
    context.workflow=full_workflow
    for node in sorted(workflow.nodes, key=lambda n: n.id):
        definition = REGISTRY[node.type]
        config = definition.config_model.model_validate(node.config)
        def handler_factory(node, definition, config):
            async def request_emit(event):
                await emit({**event,'request_stage':node.type} if node.type in ('greeting_cache','request_router',*request_safety.VERIFIERS) else event)
            async def handler(state):
                emit=request_emit
                identity=ExecutionIdentity(node.id,node.id,node.id)
                inputs = {key: state['values'][ref.split('.')[0]][ref.split('.')[1]] for key, ref in node.inputs.items()}
                node_ctx=replace(context,node_id=node.id,node_type=node.type,
                    checkpoint_owner=node.id,execution_identity=identity,
                    input_origins=provenance.evidence_context(node,inputs,state['values']),
                    input_label=provenance.weakest(provenance.port_label(state['values'],ref) for ref in node.inputs.values()),
                    input_proofs=request_safety.resolved_proofs(run_state,node.inputs,inputs))
                if completed and node.id in completed:
                    outputs=completed[node.id]
                    if is_control(definition) and (not isinstance(outputs, dict) or outputs.get('branch') not in node_routes(node, definition)):
                        raise ValueError(f'{node.id} cannot be restored: its branch is not a declared control route.')
                    if node.type=='request_router':
                        from .request_router import checked_router_checkpoint
                        source,_,port=node.inputs['message'].partition('.')
                        original=state['values'].get(source,{}).get(port)
                        try:checked_router_checkpoint(outputs,original,config)
                        except ValueError:
                            raise ValueError(f'{node.id} cannot be restored: its routing checkpoint no longer matches the original input, policy or decision. Start a new run.') from None
                    if node.type=='request_reader':
                        from .request_reader import checked_reading_checkpoint
                        source,_,port=node.inputs['message'].partition('.')
                        try:checked_reading_checkpoint(outputs,state['values'].get(source,{}).get(port),config)
                        except ValueError:
                            raise ValueError(f'{node.id} cannot be restored: its reading checkpoint no longer matches the original input, policy or reading. Start a new run.') from None
                    from .execution_policy import TRUNCATION_KEY,checked_truncation
                    if isinstance(outputs,dict) and TRUNCATION_KEY in outputs:
                        try:checked_truncation(outputs[TRUNCATION_KEY])
                        except ValueError:
                            raise ValueError(f'{node.id} cannot be restored: its checkpoint carries malformed truncation metadata. Start a new run.') from None
                    from .provenance import PROVENANCE_KEY,restored_record
                    try:
                        if isinstance(outputs,dict) and PROVENANCE_KEY in outputs:
                            outputs={**outputs,PROVENANCE_KEY:restored_record(node,outputs[PROVENANCE_KEY])}
                        if node.type=='for_each' and isinstance(outputs.get('results'),list):
                            body=next(n for n in full_workflow.nodes if n.id==config.body)
                            entries=[{**entry,PROVENANCE_KEY:restored_record(body,entry[PROVENANCE_KEY])}
                                     if isinstance(entry,dict) and PROVENANCE_KEY in entry else entry
                                     for entry in outputs['results']]
                            outputs={**outputs,'results':entries}
                    except ValueError:
                        raise ValueError(f'{node.id} cannot be restored: its checkpoint carries malformed provenance metadata. Start a new run.') from None
                    if node.type=='for_each' and 'summary' not in outputs:
                        # Checkpointed by a release without the summary output.
                        from .iteration import reconstructed_summary
                        source,_,port=node.inputs['items'].partition('.')
                        items=state['values'].get(source,{}).get(port)
                        body=next((n.type for n in full_workflow.nodes if n.id==config.body),None)
                        outputs={**outputs,'summary':json.dumps(reconstructed_summary(node.id,items,config,outputs,body))}
                    validation=validate_cached(node,outputs)
                    if inspect.isawaitable(validation):await validation
                    outputs=await request_safety.finalize(node_ctx,node,inputs,outputs,config,cached=True)
                    evidence_from_outputs(run_state,outputs)  # Restored evidence keeps its original labels.
                    await emit_evidence_notice(run_state,emit,node.id)
                    receipt=(outputs.get(PROVENANCE_KEY) or {}).get('decision')
                    await emit({'node_id':node.id,'status':'success','outputs':outputs,'cached':True,'duration_ms':0,
                                **({'decision':receipt} if receipt is not None else {})})
                    return {'values':{node.id:outputs}}
                started = time.perf_counter()
                await emit({'node_id': node.id, 'status': 'running', 'inputs': inputs})
                # Legacy prompt/LLM nodes must not bypass an upstream guard.
                def guarded_dependency(identifier,seen):
                    if identifier in seen:return False
                    seen.add(identifier)
                    from .answerability_guard import from_outputs
                    decision=from_outputs(state['values'].get(identifier,{}))
                    if decision and decision['decision']=='abstain':return True
                    upstream=next((n for n in workflow.nodes if n.id==identifier),None)
                    return bool(upstream and any(guarded_dependency(ref.split('.')[0],seen) for ref in upstream.inputs.values()))
                async def invoke():
                      # Only the runtime verifier can issue eligibility; a swapped registry
                      # handler must not mint its own continue branch or receipt.
                      implementation={'verify_extraction':request_safety.verify_extraction_node,
                                      'request_reader':request_safety.request_reader_node}.get(node.type,definition.handler)
                      outputs = await implementation(inputs,config,node_ctx)
                      # Run metadata may travel beside the declared ports under the reserved key.
                      from .execution_policy import TRUNCATION_KEY,checked_truncation
                      ports={name:value for name,value in outputs.items() if name!=TRUNCATION_KEY}
                      if set(ports) != set(definition.outputs) or any(not (isinstance(value,list) and all(isinstance(item,dict) for item in value)) if definition.outputs[name]=='array<object>' else not isinstance(value,str) for name,value in ports.items()):
                          raise ValueError('Node returned outputs that do not match its declared contract.')
                      if is_control(definition) and outputs.get('branch') not in node_routes(node, definition):
                          raise ValueError('Node returned a branch outside its declared control routes.')
                      if TRUNCATION_KEY in outputs:
                          try:checked_truncation(outputs[TRUNCATION_KEY])
                          except ValueError:raise ValueError('Node returned outputs that do not match its declared contract.') from None
                      return outputs
                async def guard_refusal():
                    """A guarded dependency refuses without a model call, so it must resolve
                    before the token check: an exhausted run must still be able to decline."""
                    if node.type not in ('llm','agent','query'):return None
                    if not any(guarded_dependency(ref.split('.')[0],set()) for ref in node.inputs.values()):return None
                    from .agent_runtime import immediate_abstention
                    refusal=immediate_abstention({'decision':'abstain'})
                    return {'text':refusal['text'],'provider':'none'} if node.type=='llm' else refusal
                try:
                      outcome=await execute_with_policy(
                          invoke,identity,node.retry,run_state,emit,
                          free_outcome=guard_refusal,
                          eligibility=lambda:request_safety.guard(node_ctx,node,inputs),
                          action_budget=run_state.get('action_budget'),
                          charge_first_attempt=charges_action(node.type),
                          include_invocation_in_retry=False,
                          delay_for=retry_delay)
                      # Spend is published into the caller's sink rather than the event
                      # stream: it is bookkeeping, not part of the node event contract.
                      if accounting_sink is not None:accounting_sink.update(accounting_snapshot(run_state))
                      if outcome.status=='failed':
                          failure={'node_id':node.id,'status':'failed','error':outcome.error}
                          if outcome.usage:failure['usage']=outcome.usage
                          if node.retry.attempts:failure['attempt']=outcome.attempts
                          if outcome.budget_exhausted:failure['budget_exhausted']=True
                          if node.on_error=='fail':
                              await emit(failure)
                              raise ValueError(outcome.error) from None
                          run_state.setdefault('failed_nodes',set()).add(node.id)
                          await emit({**failure,'recovered':True})
                          journal(event='node.recovered',node_id=node.id,reason_code=node.on_error)
                          # An ordinary recovered failure is the workflow's chosen path. Work
                          # skipped because the budget ran out is not: the run is incomplete.
                          # Recovered nodes are not checkpointed, so on resume this node runs
                          # again against the persisted budget and reports the same.
                          from .execution_policy import TRUNCATION_KEY,budget_truncation
                          value={TRUNCATION_KEY:budget_truncation(node.id,outcome)} if outcome.budget_exhausted else {}
                          return {'values':{node.id:value}}
                      outputs=outcome.outputs
                      outputs=await request_safety.finalize(node_ctx,node,inputs,outputs,config)
                      if node.type=='request_router':
                          from .request_router import ROUTING_KEY,routing_record
                          # Runtime-created audit binding, never metadata supplied by the model/handler.
                          outputs={**outputs,ROUTING_KEY:routing_record(outputs,inputs['message'],config)}
                      if node.type=='request_reader':
                          from .request_reader import READING_KEY,reading_record
                          outputs={**outputs,READING_KEY:reading_record(outputs,inputs['message'],config)}
                      body_type=next((n.type for n in full_workflow.nodes if n.id==getattr(config,'body',None)),None) if node.type=='for_each' else None
                      record=provenance.for_node(node,definition,inputs,outputs,state['values'],body_type)
                      decision=None
                      if node.type=='condition':
                          # Origins remain visible; contract 2 grants eligibility separately.
                          label,reason=provenance.value_label(state['values'],node.inputs['value'],config.field)
                          review=label==provenance.GUESSED
                          decision={'label':label,'would_review':review,**({'reason':reason} if review else {})}
                          record['ports']['branch']=provenance.GUESSED if review else provenance.CALCULATED
                      elif node.type=='calculate' and config.mode=='check':
                          # A human choice is not proof that the original check succeeded.
                          label=record['ports']['text']
                          passed=exact_json.loads(outputs['text'])['result'] is True
                          human=run_state.get('decision_reviews',{}).get(identity.invocation_id)
                          if human:passed=human['payload']['check']['proposed_choice'] is True
                          reason=None if passed and label!=provenance.GUESSED else ('check_failed' if not passed else 'guessed')
                          decision={'label':label,'passed':passed,'would_review':reason is not None,**({'reason':reason} if reason else {})}
                      if decision is not None:
                          human=outputs.get(request_safety.DECISION_KEY)
                          if human:decision={**decision,'manual':True,'review_id':human['id']}
                          elif full_workflow.contract==2 and await request_safety.binding_valid((node_ctx.input_proofs or {}).get('value'),inputs.get('value'),node_ctx):
                              # Eligibility is separate from the model's origin label.
                              decision={k:v for k,v in decision.items() if k!='reason'}
                              decision['would_review']=False
                          record['decision']=decision
                          if decision['would_review']:journal(event='decision.would_review',node_id=node.id,reason_code=decision.get('reason','guessed'))
                      outputs={**outputs,provenance.PROVENANCE_KEY:provenance.checked(record)}
                      evidence_from_outputs(run_state,outputs)
                      await emit_evidence_notice(run_state,emit,node.id)
                      event={'node_id': node.id, 'status': 'success', 'outputs': outputs,'duration_ms': round((time.perf_counter()-started)*1000)}
                      if decision is not None:event['decision']=decision
                      event.update(run_state.get('tool_output_metadata',{}).get(node.id,{}))
                      if outcome.usage:event['usage']=outcome.usage
                      await emit(event)
                      return {'values': {node.id: outputs}}
                except ApprovalPause as exc:
                      from .execution_policy import usage_for
                      paused={'node_id':node.id,'status':'paused','transient':True}
                      usage=usage_for(run_state,identity)
                      if usage:paused['usage']=usage
                      await emit(paused)
                      raise
                except asyncio.CancelledError as exc:
                      if isinstance(exc,PersistedNodeCancellation):
                          # Python 3.12 asyncio.timeout recognizes the exact base type.
                          raise asyncio.CancelledError() from None
                      await emit({'node_id': node.id, 'status': 'cancelled',**({'usage':run_state['usage'][node.id]} if run_state.get('usage',{}).get(node.id) else {})})
                      raise
                except Exception as exc:
                      from .tool_service import UncertainWriteError
                      error = str(exc) if isinstance(exc, ValueError) else 'Node execution failed.'
                      usage_fields={'usage':run_state['usage'][node.id]} if run_state.get('usage',{}).get(node.id) else {}
                      if isinstance(exc,UncertainWriteError):
                          failure={'node_id':node.id,'status':'failed','error':error,**usage_fields}
                          await emit(failure)
                          raise ValueError(error) from None
                      if isinstance(exc,request_safety.UnsafeExecutionError):
                          await emit({'node_id':node.id,'status':'failed','error':error,**usage_fields})
                      raise
            return handler
        graph.add_node(node.id, handler_factory(node, definition, config))
    trigger = next(n.id for n in workflow.nodes if n.type in ('chat_input','manual_input'))
    graph.add_edge(START, trigger)
    for node in sorted(workflow.nodes, key=lambda n: n.id):
        edges = sorted((e for e in workflow.edges if e.source == node.id), key=lambda e:e.id)
        if is_control(REGISTRY[node.type]):
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
