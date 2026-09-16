"""Reusable node contracts and execution handlers. No business workflows live here."""
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal
import asyncio
import json
import string
import httpx
from pydantic import Field, model_validator
from .models import StrictModel

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
    max_steps: int = Field(default=6,ge=1,le=6)
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

class ConditionConfig(StrictModel):
    contains: str = Field(min_length=1, max_length=1000)
    case_sensitive: bool = False

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
    workflow: object = None
    run: dict | None = None  # Shared per run: {'evidence': [passages with unique citation labels]}

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

    def public(self):
        return {'type': self.type, 'name': self.name, 'version': 1,
                'category': self.category, 'description': self.description,
                'inputs': self.inputs, 'outputs': self.outputs,
                'config_schema': self.config_model.model_json_schema(),
                'side_effects': self.side_effects,'hidden':self.hidden}

async def input_node(inputs, config, ctx):
    return {'message': ctx.message}

async def prompt_node(inputs, config, ctx):
    return {'text': config.template.format_map(inputs)}

def account_usage(ctx,usage):
    """Accumulate per-node model usage on the shared run state; the compiler reports it on the node event."""
    if ctx.run is None or not usage:return
    totals=ctx.run.setdefault('usage',{}).setdefault(ctx.node_id or '?',{'calls':0,'prompt_tokens':0,'completion_tokens':0})
    totals['calls']+=1;totals['prompt_tokens']+=usage.get('prompt_tokens',0);totals['completion_tokens']+=usage.get('completion_tokens',0)

async def llm_node(inputs, config, ctx):
    ctx.authorize_model(config)
    if config.provider == 'demo':
        await asyncio.sleep(0.15)
        return {'text': f'[Demo · no model called]\n\nReceived prompt:\n{inputs["prompt"]}', 'provider': 'demo'}
    from .providers import with_retries,ProviderBusy,RETRYABLE_STATUS,record_usage
    params={}
    if config.temperature is not None:params['temperature']=config.temperature
    if config.top_p is not None:params['top_p']=config.top_p
    if config.max_tokens!=2048:params['max_tokens']=config.max_tokens
    usage={}
    if config.provider == 'ollama':
        from .providers import ollama_chat
        text=await with_retries(lambda: ollama_chat(config.model, config.system, inputs['prompt'],usage=usage,**params))
        account_usage(ctx,usage);return {'text': text, 'provider': 'ollama'}
    if config.provider == 'claude':
        from .providers import claude_chat
        text=await with_retries(lambda: claude_chat(config.model, config.system, inputs['prompt'], ctx.resolve_credential(config.credential_id),usage=usage,**params))
        account_usage(ctx,usage);return {'text': text, 'provider': 'claude'}
    if not config.credential_id:
        raise ValueError('Choose an OpenAI credential in the LLM settings.')
    key = ctx.resolve_credential(config.credential_id)
    async def openai_call():
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                response = await client.post('https://api.openai.com/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}'},
                    json={'model': config.model, 'messages': [
                        {'role': 'system', 'content': config.system},
                        {'role': 'user', 'content': inputs['prompt']}], 'max_completion_tokens': config.max_tokens,**{k:v for k,v in params.items() if k!='max_tokens'}})
                if response.status_code in RETRYABLE_STATUS:raise ProviderBusy(f'OpenAI returned HTTP {response.status_code}')
                response.raise_for_status()
                body=response.json();content = body['choices'][0]['message']['content']
                if not isinstance(content, str):
                    raise ValueError('Model did not return text.')
                record_usage(usage,(body.get('usage') or {}).get('prompt_tokens'),(body.get('usage') or {}).get('completion_tokens'))
                return content
            except ProviderBusy:raise
            except httpx.TimeoutException:raise ProviderBusy('OpenAI request timed out') from None
            except httpx.HTTPStatusError as exc:
                raise ValueError(f'OpenAI request failed (HTTP {exc.response.status_code}). Check the credential, model and quota.') from None
            except (httpx.RequestError, KeyError, IndexError, TypeError):
                raise ValueError('The model request failed or returned an unsupported response.') from None
    text=await with_retries(openai_call)
    account_usage(ctx,usage);return {'text': text, 'provider': 'openai'}

async def condition_node(inputs, config, ctx):
    value, needle = inputs['value'], config.contains
    if not config.case_sensitive:
        value, needle = value.casefold(), needle.casefold()
    return {'branch': 'true' if needle in value else 'false'}

async def response_node(inputs, config, ctx):
    """The response boundary: any [S#] label must name evidence this run actually retrieved."""
    from .agent_runtime import ground_answer
    evidence=(ctx.run or {}).get('evidence',[])
    text,sources=ground_answer(inputs['text'],evidence) if evidence else (inputs['text'],[])
    return {'text': text, 'sources': json.dumps(sources,ensure_ascii=False)}

REGISTRY: dict[str, NodeDefinition] = {}

def register(definition: NodeDefinition):
    if definition.type in REGISTRY:
        raise ValueError(f'Duplicate node type: {definition.type}')
    REGISTRY[definition.type] = definition

for definition in [
    NodeDefinition('chat_input', 'Chat input', 'Input', 'Start with a message from the run panel.', {}, {'message':'string'}, EmptyConfig, input_node),
    NodeDefinition('manual_input', 'Manual trigger', 'Input', 'Start a workflow on demand with a text payload.', {}, {'message':'string'}, EmptyConfig, input_node),
    NodeDefinition('prompt', 'Prompt template', 'Transform', 'Compose a prompt with {message}.', {'message':'string'}, {'text':'string'}, PromptConfig, prompt_node),
    NodeDefinition('llm', 'Language model', 'AI', 'Generate text with a model, or test with demo mode.', {'prompt':'string'}, {'text':'string','provider':'string'}, LLMConfig, llm_node, ('external_model_request',)),
    NodeDefinition('condition', 'Condition', 'Control', 'Route to true or false when text contains a phrase.', {'value':'string'}, {'branch':'string'}, ConditionConfig, condition_node),
    NodeDefinition('response', 'Response', 'Output', 'Finish this path and return text.', {'text':'string'}, {'text':'string','sources':'string'}, EmptyConfig, response_node),
]:
    register(definition)


def validate_template(template: str):
    for _, name, spec, conversion in string.Formatter().parse(template):
        if name is not None and (name != 'message' or spec or conversion):
            raise ValueError('Templates support only {message}; use {{ and }} for literal braces.')

from .agent_runtime import agent_node,tool_node
register(NodeDefinition('agent','Agent node','Agent','Configure a role, attach tools and delegate to specialist agents.',{'input':'string'},{'text':'string','provider':'string','sources':'string'},AgentConfig,agent_node))
# Prompt template and Language model stay loadable for saved workflows and the LLM-free example, but off the palette.
for legacy in ('prompt','llm'):REGISTRY[legacy].hidden=True

from .tool_service import CONFIGS
for type,name in [('tool_http','HTTP / REST API'),('tool_email','Email'),('tool_jira','Jira'),('tool_confluence','Confluence'),('tool_github','GitHub'),('tool_python','Python')]:
    register(NodeDefinition(type,name,'Tools','Execute a configured tool with explicit operation and connection.',{'input':'string'},{'text':'string'},CONFIGS[type],tool_node,('configured_tool_request',)))

from .kb.options import RetrievalOptions
class SearchConfig(RetrievalOptions):
    knowledge_base_id: str = Field(default='',max_length=64)
    description: str = Field(default='',max_length=500)  # Shown to an agent that may call this node as a tool.
    # Retired with the vector-resource stack; accepted and ignored so saved workflows keep loading.
    storage_path: str = Field(default='',max_length=64)
class QueryConfig(LLMConfig,SearchConfig):
    pass
from .platform_nodes import retrieve_node,query_node
register(NodeDefinition('retrieve','Retrieve','Retrieval','Search a named knowledge base with configurable retrieval and fusion.',{'query':'string'},{'query':'string','context':'string','sources':'string'},SearchConfig,retrieve_node))
register(NodeDefinition('query','Query','Retrieval','Retrieve evidence and generate a sourced answer using an enabled model.',{'query':'string'},{'text':'string','provider':'string','sources':'string'},QueryConfig,query_node,('external_model_request',)))
