"""Reusable node contracts and execution handlers. No business workflows live here."""
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal
import asyncio
import json
import string
from pydantic import Field, model_validator
from .models import StrictModel
from .conditions import ConditionConfig, evaluate as evaluate_condition
from .calculate import CalculateConfig, calculate_node

class EmptyConfig(StrictModel):
    pass

class PromptConfig(StrictModel):
    template: str = Field(default='{message}', max_length=20000)

class LLMConfig(StrictModel):
    provider: Literal['demo', 'openai', 'claude', 'ollama'] = 'demo'
    model: str = Field(default='gpt-4.1-mini', min_length=1, max_length=100)
    credential_id: str = Field(default='', max_length=128)
    system: str = Field(default='You are a helpful assistant.', max_length=20000)
    temperature: float | None = Field(default=None,ge=0,le=2)
    top_p: float | None = Field(default=None,gt=0,le=1)
    max_tokens: int = Field(default=2048,ge=1,le=16384)

    @model_validator(mode='after')
    def supported_parameters(self):
        if self.provider=='claude':
            if self.temperature is not None and self.temperature>1:raise ValueError('Claude temperature must be between 0 and 1.')
            if self.temperature is not None and self.top_p is not None:raise ValueError('For Claude, set temperature or top-p, not both.')
        return self

class AgentConfig(LLMConfig):
    role: Literal['planner','reasoner','reflection','critic','router','memory','summarizer','extraction','classification']='reasoner'
    user_prompt: str = Field(default='{input}',max_length=20000)
    max_steps: int = Field(default=6,ge=1,le=12)
    memory_key: str = Field(default='default',min_length=1,max_length=120)
    description: str = Field(default='',max_length=500)  # Shown to a parent agent that may delegate here.
    output_schema: dict = Field(default_factory=dict,title='Output JSON schema',description='When set, the answer must be JSON matching this schema; extraction and classification roles always require a JSON object.')

    @model_validator(mode='after')
    def valid_output_schema(self):
        if self.output_schema:
            import jsonschema
            if len(json.dumps(self.output_schema))>20000:raise ValueError('Output schema is too large.')
            try:jsonschema.Draft202012Validator.check_schema(self.output_schema)
            except jsonschema.SchemaError as exc:raise ValueError(f'Output schema is not a valid JSON Schema: {exc.message}') from None
        return self

@dataclass
class Context:
    message: str
    resolve_credential: Callable[[str], str]
    authorize_model: Callable = lambda _: None
    knowledge: Callable | None = None
    platform: Callable | None = None
    invoke_agent: Callable | None = None
    node_id: str = ''
    node_type: str = ''
    checkpoint_owner: str = ''
    execution_identity: object | None = None
    workflow: object = None
    emit: Callable | None = None
    run: dict | None = None  # Shared per run: {'evidence': [passages with unique citation labels]}
    input_origins: object | None = None  # Eligible data for this invocation, not shared run evidence.
    input_label: str | None = None
    input_proofs: dict | None = None  # Runtime-created bindings, never model/handler metadata.

# Ports named with this prefix would collide with run metadata carried in a node's graph
# value (execution_policy.TRUNCATION_KEY), which must never be declared or bound.
RESERVED_PORT_PREFIX='_'


def check_port_names(definition):
    for kind,ports in (('input',definition.inputs),('optional input',definition.optional_inputs),('output',definition.outputs)):
        reserved=sorted(str(name) for name in ports if not isinstance(name,str) or name.startswith(RESERVED_PORT_PREFIX))
        if reserved:
            raise ValueError(f"{definition.type}: {kind} port names cannot start with '{RESERVED_PORT_PREFIX}' "
                             f"({', '.join(reserved)}); that prefix is reserved for run metadata.")
    if set(definition.inputs) & set(definition.optional_inputs):raise ValueError('Required and optional input names must be disjoint.')
    from .request_contracts import route_contract
    route_contract(definition)


@dataclass
class NodeDefinition:
    type: str
    name: str
    category: str
    description: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    config_model: type[StrictModel]
    handler: Callable[..., Awaitable[dict]]
    side_effects: tuple[str, ...] = ()
    hidden: bool = False
    branch_routes: tuple[str, ...] = ()
    optional_inputs: dict[str, str] = field(default_factory=dict)
    # Routes taken from each node's validated settings (Switch); exclusive with branch_routes.
    routes_from_config: Callable[..., tuple[str, ...]] | None = None

    def __post_init__(self):
        check_port_names(self)

    def public(self):
        return {'type': self.type, 'name': self.name, 'version': 1,
                'category': self.category, 'description': self.description,
                'inputs': self.inputs, 'optional_inputs': self.optional_inputs, 'outputs': self.outputs,
                'branch_routes': self.branch_routes,'configurable_routes': self.routes_from_config is not None,
                'config_schema': self.config_model.model_json_schema(),
                'side_effects': self.side_effects,'hidden':self.hidden}

async def input_node(inputs, config, ctx):
    return {'message': ctx.message}

async def prompt_node(inputs, config, ctx):
    return {'text': config.template.format_map(inputs)}

async def record_spend(ctx):
    """Make model spend durable the moment the provider returns.

    Tokens are only known after the response, so unlike actions they cannot be reserved
    first. Recording them immediately shrinks the unaccounted window to the gap between
    the response arriving and this write. The write is best-effort: the call has already
    been paid for, and failing the node here would only cause it to be paid for again.
    """
    persist=(ctx.run or {}).get('persist_spend')
    if persist is None:return
    from .execution_policy import accounting_snapshot
    try:
        await persist(accounting_snapshot(ctx.run))
    except Exception as exc:
        # Keep the paid response. The spend stays in memory and is written by the next
        # successful persistence point; losing the answer would only mean paying again.
        # Cancellation and a lost lease are BaseException or re-raised elsewhere and are
        # deliberately not caught here.
        from .observability import journal
        journal(event='accounting.persist_failed',node_id=ctx.node_id,
                reason_code='post_spend',error_type=exc.__class__.__name__)


def account_usage(ctx,usage,config,error_type=None):
    """Accumulate per-node model usage on the shared run state; the compiler reports it on the node event.

    error_type marks usage from a response that was rejected or interrupted. The tokens
    count the same either way; the journal line keeps the two apart.
    """
    if ctx.run is None or not usage:return
    def accumulate(totals):
        totals['calls']+=1;totals['prompt_tokens']+=usage.get('prompt_tokens',0);totals['completion_tokens']+=usage.get('completion_tokens',0)
        groups=totals.setdefault('by_model',[])
        row=next((r for r in groups if r['provider']==config.provider and r['model']==config.model),None)
        if row is None:
            row={'provider':config.provider,'model':config.model,'calls':0,'prompt_tokens':0,'completion_tokens':0};groups.append(row)
        row['calls']+=1;row['prompt_tokens']+=usage.get('prompt_tokens',0);row['completion_tokens']+=usage.get('completion_tokens',0)
    empty=lambda:{'calls':0,'prompt_tokens':0,'completion_tokens':0}
    totals=ctx.run.setdefault('usage',{}).setdefault(ctx.checkpoint_owner or ctx.node_id or '?',empty())
    accumulate(totals)
    invocation=getattr(ctx.execution_identity,'invocation_id',None)
    if invocation:
        invocation_totals=ctx.run.setdefault('usage_by_invocation',{}).setdefault(invocation,empty())
        accumulate(invocation_totals)
    from .observability import journal
    journal(event='model.usage',node_id=ctx.node_id,provider=config.provider,model=config.model,prompt_tokens=usage.get('prompt_tokens'),completion_tokens=usage.get('completion_tokens'),
            **({'error_type':error_type} if error_type else {}))


async def call_model(ctx,config,call):
    """One provider call through the retry policy. Usage is counted for every response the
    provider reported, used or rejected: a rejected answer was still paid for. A call that
    returned no usage (an HTTP error, a timeout) adds nothing; its real consumption is unknown."""
    from .providers import with_retries
    usage={}
    try:
        text=await with_retries(lambda:call(usage),provider=config.provider,model=config.model)
    except asyncio.CancelledError:
        # Keep the spend in memory; the next persistence point writes it. Awaiting a write
        # here would delay the cancellation.
        account_usage(ctx,usage,config,error_type='cancelled');raise
    except Exception as exc:
        if usage:account_usage(ctx,usage,config,error_type=type(exc).__name__);await record_spend(ctx)
        raise
    account_usage(ctx,usage,config);await record_spend(ctx);return text


async def llm_node(inputs, config, ctx):
    ctx.authorize_model(config)
    if config.provider == 'demo':
        await asyncio.sleep(0.15)
        return {'text': f'[Demo · no model called]\n\nReceived prompt:\n{inputs["prompt"]}', 'provider': 'demo'}
    from .token_budget import TokenBudgetExhausted,exhausted as tokens_exhausted,reason as token_reason
    # A plain model node has no partial-answer contract; refuse before spending more.
    if tokens_exhausted(ctx.run):raise TokenBudgetExhausted(token_reason(ctx.run))
    params={}
    if config.temperature is not None:params['temperature']=config.temperature
    if config.top_p is not None:params['top_p']=config.top_p
    if config.max_tokens!=2048:params['max_tokens']=config.max_tokens
    if config.provider == 'ollama':
        from .providers import ollama_chat
        text=await call_model(ctx,config,lambda usage: ollama_chat(config.model, config.system, inputs['prompt'],usage=usage,**params))
        return {'text': text, 'provider': 'ollama'}
    if config.provider == 'claude':
        from .providers import claude_chat
        text=await call_model(ctx,config,lambda usage: claude_chat(config.model, config.system, inputs['prompt'], ctx.resolve_credential(config.credential_id),usage=usage,**params))
        return {'text': text, 'provider': 'claude'}
    from .providers import openai_chat
    text=await call_model(ctx,config,lambda usage: openai_chat(config.model, config.system, inputs['prompt'], ctx.resolve_credential(config.credential_id),usage=usage,**params))
    return {'text': text, 'provider': 'openai'}

async def condition_node(inputs, config, ctx):
    return {'branch': 'true' if evaluate_condition(inputs['value'], config) else 'false'}

async def response_node(inputs, config, ctx):
    """The response boundary: any [S#] label must name evidence this run actually retrieved,
    and every source behind the answer, listed or reused, must still be valid now."""
    from .agent_runtime import ground_answer
    from .platform_nodes import reused_dependencies,verify_published
    from .request_safety import UnsafeExecutionError
    evidence=(ctx.run or {}).get('evidence',[])
    text,sources=ground_answer(inputs['text'],evidence)
    try:reused=reused_dependencies(ctx.run)
    except ValueError:
        raise UnsafeExecutionError(f'{ctx.node_id}: saved knowledge sources of reused work are malformed. No answer was published. Start a new run.') from None
    await verify_published(ctx,ctx.node_id,sources+reused)
    return {'text': text, 'sources': json.dumps(sources,ensure_ascii=False)}

REGISTRY: dict[str, NodeDefinition] = {}

class ForEachConfig(StrictModel):
    body: str = Field(default='', max_length=64)
    max_items: int = Field(default=100, ge=1, le=1000)
    on_item_error: Literal['continue','stop'] = 'continue'

def register(definition: NodeDefinition):
    check_port_names(definition)  # Again: a definition's dicts can change after construction.
    if definition.type in REGISTRY:
        raise ValueError(f'Duplicate node type: {definition.type}')
    REGISTRY[definition.type] = definition

for definition in [
    NodeDefinition('chat_input', 'Chat input', 'Input', 'Start with a message from the run panel.', {}, {'message':'string'}, EmptyConfig, input_node),
    NodeDefinition('manual_input', 'Manual trigger', 'Input', 'Start a workflow on demand with a text payload.', {}, {'message':'string'}, EmptyConfig, input_node),
    NodeDefinition('prompt', 'Prompt template', 'Transform', 'Compose a prompt with {message}.', {'message':'string'}, {'text':'string'}, PromptConfig, prompt_node),
    NodeDefinition('llm', 'Language model', 'AI', 'Generate text with a model, or test with demo mode.', {'prompt':'string'}, {'text':'string','provider':'string'}, LLMConfig, llm_node, ('external_model_request',)),
    NodeDefinition('condition', 'Condition', 'Control', 'Route to true or false by comparing a value: text, number, membership or empty.', {'value':'string'}, {'branch':'string'}, ConditionConfig, condition_node, branch_routes=('true','false')),
    NodeDefinition('calculate', 'Calculate', 'Transform', 'Compute or check numbers from JSON fields exactly, in code rather than a model.', {'value':'string'}, {'text':'string'}, CalculateConfig, calculate_node),
    NodeDefinition('response', 'Response', 'Output', 'Finish this path and return text.', {'text':'string'}, {'text':'string','sources':'string'}, EmptyConfig, response_node),
]:
    register(definition)


def validate_template(template: str):
    for _, name, spec, conversion in string.Formatter().parse(template):
        if name is not None and (name != 'message' or spec or conversion):
            raise ValueError('Templates support only {message}; use {{ and }} for literal braces.')

from .agent_runtime import agent_node,tool_node
from .iteration import for_each_node as for_each_handler
register(NodeDefinition('for_each','For each','Control','Run one attached callable once per element of a list.',{'items':'array'},{'results':'array<object>','failed':'string','summary':'string'},ForEachConfig,for_each_handler))
register(NodeDefinition('agent','Agent node','Agent','Configure a role, attach tools and delegate to specialist agents.',{'input':'string'},{'text':'string','provider':'string','sources':'string','grounding':'string'},AgentConfig,agent_node))
# Prompt template and Language model stay loadable for saved workflows and the LLM-free example, but off the palette.
for legacy in ('prompt','llm'):REGISTRY[legacy].hidden=True

from .tool_service import CONFIGS
# tool_type, not type: a module-level loop variable named `type` shadows the builtin for the
# rest of this module, and a later type(exc) in an error handler would itself raise.
for tool_type,name in [('tool_http','HTTP / REST API'),('tool_email','Email'),('tool_jira','Jira'),('tool_confluence','Confluence'),('tool_github','GitHub'),('tool_python','Python')]:
    register(NodeDefinition(tool_type,name,'Tools','Execute a configured tool with explicit operation and connection.',{'input':'string'},({'text':'string','items':'array<object>'} if tool_type=='tool_jira' else {'text':'string'}),CONFIGS[tool_type],tool_node,side_effects=('external_write','configured_tool_request') if tool_type!='tool_python' else ('sandbox_execution',)))

from .kb.options import RetrievalOptions
class SearchConfig(RetrievalOptions):
    answerability_guard:bool=Field(default=False,description="Opt in to the local answerability reader before generation. Missing weights skip the check; inspect guard decisions in run events.")
    knowledge_base_id: str = Field(default='',max_length=64)
    description: str = Field(default='',max_length=500)  # Shown to an agent that may call this node as a tool.
    # Retired with the vector-resource stack; accepted and ignored so saved workflows keep loading.
    storage_path: str = Field(default='',max_length=64)
class QueryConfig(LLMConfig,SearchConfig):
    pass
from .platform_nodes import retrieve_node,query_node
register(NodeDefinition('retrieve','Retrieve','Retrieval','Search a named knowledge base with configurable retrieval and fusion.',{'query':'string'},{'query':'string','context':'string','sources':'string'},SearchConfig,retrieve_node))
register(NodeDefinition('query','Query','Retrieval','Retrieve evidence and generate a sourced answer using an enabled model.',{'query':'string'},{'text':'string','provider':'string','sources':'string','grounding':'string'},QueryConfig,query_node,('external_model_request',)))

from .common_replies import greeting_cache_node
from .request_contracts import GreetingCacheConfig
register(NodeDefinition('greeting_cache','Greeting cache','Control',
    'Reply to configured whole messages without a model; other requests continue unchanged.',
    {'message':'string'},{'branch':'string','reply':'string','receipt':'string'},
    GreetingCacheConfig,greeting_cache_node,branch_routes=('continue','reply')))

from .request_router import request_router_node
from .request_contracts import RequestRouterConfig
register(NodeDefinition('request_router','Request router','Control',
    'Classify the original request using saved workflow rules; acceptance still requires extraction checks.',
    {'message':'string'},{'branch':'string','reply':'string','decision':'string'},
    RequestRouterConfig,request_router_node,side_effects=('external_model_request',),
    branch_routes=('continue','reply')))

from .request_safety import verify_extraction_node
from .request_contracts import ExtractionCheckConfig
register(NodeDefinition('verify_extraction','Verify extraction','Control',
    'Check exact facts against original input and configured rules; uncertain interpretations require review.',
    {'original':'string','value':'string'},{'text':'string','branch':'string','reply':'string','report':'string'},
    ExtractionCheckConfig,verify_extraction_node,branch_routes=('continue','reply'),optional_inputs={'reference':'string'}))

from .switch import SwitchConfig, routes as switch_routes, switch_node
register(NodeDefinition('switch','Switch','Control',
    'Route to one of several named paths: cases are checked in order, the first match wins, otherwise Default. Only checked or trusted values.',
    {'value':'string'},{'branch':'string'},SwitchConfig,switch_node,routes_from_config=switch_routes))

from .request_safety import request_reader_node
from .request_reader import RequestReaderConfig
register(NodeDefinition('request_reader','Request reader','Control',
    'Reply to prepared messages, read the request with one model call, then check every value in code. Code chooses the branch.',
    {'message':'string'},{'text':'string','branch':'string','reply':'string','report':'string','reading':'string'},
    RequestReaderConfig,request_reader_node,side_effects=('external_model_request',),
    branch_routes=('continue','reply'),optional_inputs={'reference':'string'}))
