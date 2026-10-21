"""Strict interfaces for request admission. Handlers and enforcement land separately.

Origin labels and router decisions are not verification receipts. These models describe
configuration/results only; constructing one does not authorise a downstream action.
"""
import json
import re
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictStr, field_validator, model_validator
from .models import StrictModel

Purpose = Annotated[StrictStr, Field(min_length=1, max_length=2000)]
Brief = Annotated[StrictStr, Field(min_length=1, max_length=500)]
Name = Annotated[StrictStr, Field(min_length=1, max_length=64)]
Reason = Annotated[StrictStr, Field(min_length=1, max_length=300)]
Reply = Annotated[StrictStr, Field(max_length=2000)]
Pointer = Annotated[StrictStr, Field(max_length=128)]


def nonblank(value):
    if not value.strip():raise ValueError('Text must not be blank.')
    return value


def checked_pointer(value):
    if value and (not value.startswith('/') or re.search(r'~(?![01])', value)):
        raise ValueError('Use a JSON Pointer with only ~0 and ~1 escapes.')
    return value


def bounded_json(value, limit, name):
    try:size = len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))
    except (ValueError, TypeError):raise ValueError(f'{name} must be finite JSON.') from None
    if size > limit:raise ValueError(f'{name} exceeds {limit} serialized bytes.')


def _checked_routes(routes) -> tuple[str, ...]:
    if not isinstance(routes, tuple) or any(not isinstance(r, str) for r in routes) or len(set(routes)) != len(routes):
        raise ValueError('Control routes must be unique handles.')
    if routes and (len(routes) < 2 or len(routes) > 16 or any(
            not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', r) or r == 'error' for r in routes)):
        raise ValueError('Declare 2–16 public control routes; error is reserved for recovery.')
    return routes


def route_contract(definition) -> tuple[str, ...]:
    """Type-level check at registration: static routes, or routes from each node's settings.

    It returns the static routes only. A node instance's routes come from node_routes."""
    routes = _checked_routes(definition.branch_routes)
    configured = definition.routes_from_config is not None
    if routes and configured:raise ValueError('Declare static routes or configured routes, not both.')
    if (routes or configured) and definition.outputs.get('branch') != 'string':
        raise ValueError('A control requires a string branch output.')
    return routes


def is_control(definition) -> bool:
    return bool(definition and (definition.branch_routes or definition.routes_from_config is not None))


def node_routes(node, definition=None) -> tuple[str, ...]:
    """The routes of one node: the only source of routes for validation, compilation,
    fresh outputs, checkpoint restore and the editor. Only invalid settings give no routes
    (the settings error is reported instead). Valid settings must give 2–16 valid routes."""
    from .registry import REGISTRY
    definition = definition or REGISTRY.get(node.type)
    if definition is None:return ()
    if definition.routes_from_config is None:return route_contract(definition)
    try:config = definition.config_model.model_validate(node.config)
    except (ValueError, TypeError):return ()
    routes = definition.routes_from_config(config)
    # A string or a mapping would iterate into the wrong handles ("go" -> g, o).
    if not isinstance(routes, (tuple, list)):raise ValueError('Configured routes must be a list of handles.')
    if len(routes) < 2:raise ValueError('A control needs 2–16 routes.')
    return _checked_routes(tuple(routes))


class CommonReply(StrictModel):
    message: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    reply: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    _text = field_validator('message', 'reply')(nonblank)


class GreetingCacheConfig(StrictModel):
    entries: list[CommonReply] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_messages(self):
        from .common_replies import normalise_common_message
        keys = [normalise_common_message(e.message) for e in self.entries]
        if len(set(keys)) != len(keys):raise ValueError('Prepared messages collide after lookup normalization.')
        return self


class RequestRouterConfig(StrictModel):
    purpose: Purpose
    accepted_requests: list[Brief] = Field(default_factory=list, max_length=20)
    exclusions: list[Brief] = Field(default_factory=list, max_length=20)
    clarification_rules: list[Brief] = Field(default_factory=list, max_length=20)
    examples: list[Brief] = Field(default_factory=list, max_length=20)
    provider: Literal['demo', 'openai', 'claude', 'ollama'] = 'demo'
    model: Annotated[StrictStr, Field(min_length=1, max_length=100)] = 'gpt-4.1-mini'
    credential_id: Annotated[StrictStr, Field(max_length=128)] = ''
    max_tokens: Annotated[int, Field(strict=True, ge=1, le=16384)] = 2048
    _purpose = field_validator('purpose')(nonblank)

    @field_validator('accepted_requests', 'exclusions', 'clarification_rules', 'examples')
    @classmethod
    def nonempty_entries(cls, values):
        for value in values:nonblank(value)
        return values


class RouterDecision(StrictModel):
    route: Literal['proceed', 'clarify', 'out_of_scope']
    reason: Reason
    missing_fields: list[Name] = Field(max_length=16)
    reply: Reply
    _reason = field_validator('reason')(nonblank)

    @model_validator(mode='after')
    def consistent(self):
        if len(set(self.missing_fields)) != len(self.missing_fields):raise ValueError('Missing field names must be unique.')
        for value in self.missing_fields:nonblank(value)
        if self.route == 'proceed':
            if self.missing_fields or self.reply:raise ValueError('Proceed requires no missing fields and an empty reply.')
        else:nonblank(self.reply)
        return self

    @property
    def branch(self):
        return 'continue' if self.route == 'proceed' else 'reply'


class FieldRule(StrictModel):
    field: Pointer
    kind: Literal['quoted', 'accept_quoted', 'source_field', 'enum', 'reference_member']
    source_pointer: Pointer | None = None
    allowed_values: list[str | int | float | bool | None] | None = Field(default=None, min_length=1, max_length=100)
    reference_list: Pointer | None = None
    reference_key: Pointer | None = None
    _pointers = field_validator('field', 'source_pointer', 'reference_list', 'reference_key')(lambda v: checked_pointer(v) if v is not None else v)

    @model_validator(mode='after')
    def parameters(self):
        expected = {'quoted': set(), 'accept_quoted': set(), 'source_field': {'source_pointer'}, 'enum': {'allowed_values'},
                    'reference_member': {'reference_list', 'reference_key'}}[self.kind]
        provided = {name for name in ('source_pointer', 'allowed_values', 'reference_list', 'reference_key') if getattr(self, name) is not None}
        if provided != expected:raise ValueError(f'{self.kind} requires exactly {sorted(expected)} parameters.')
        if self.field == '':raise ValueError('A field rule requires a non-root field Pointer.')
        bounded_json(self.model_dump(), 20000, 'Field rule')
        return self


class ExtractionCheckConfig(StrictModel):
    expected_schema: dict
    required_fields: list[Pointer] = Field(default_factory=list, max_length=64)
    # Alternatives: the request is complete when every field of at least one group is present.
    required_any: list[Annotated[list[Pointer], Field(min_length=1, max_length=8)]] = Field(default_factory=list, max_length=8)
    field_rules: list[FieldRule] = Field(default_factory=list, max_length=64)
    cross_checks: list[Brief] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def rules_are_complete(self):
        import jsonschema
        from .calculate import CalculateConfig
        bounded_json(self.model_dump(), 20000, 'Extraction configuration')
        try:jsonschema.Draft202012Validator.check_schema(self.expected_schema)
        except jsonschema.SchemaError:raise ValueError('Expected schema is not valid JSON Schema.') from None
        for field in self.required_fields:
            checked_pointer(field)
            if field == '':raise ValueError('Required fields must use non-root Pointers.')
        if len(set(self.required_fields)) != len(self.required_fields):raise ValueError('Required fields must be unique.')
        if set(self.required_fields) - {rule.field for rule in self.field_rules}:raise ValueError('Each required field needs an acceptance rule.')
        for group in self.required_any:
            for field in group:
                checked_pointer(field)
                if field == '':raise ValueError('Required groups must use non-root Pointers.')
            if len(set(group)) != len(group):raise ValueError('Fields in a required group must be unique.')
            if set(group) - {rule.field for rule in self.field_rules}:raise ValueError('Each field in a required group needs an acceptance rule.')
        if len({tuple(sorted(g)) for g in self.required_any}) != len(self.required_any):raise ValueError('Required groups must be unique.')
        encoded = [rule.model_dump_json() for rule in self.field_rules]
        if len(set(encoded)) != len(encoded):raise ValueError('Duplicate field rules are not allowed.')
        for expression in self.cross_checks:CalculateConfig(mode='check', expression=expression)
        return self


class FieldResult(StrictModel):
    field: Pointer
    status: Literal['passed', 'failed', 'unverified', 'absent']
    reason: Reason
    _pointer = field_validator('field')(checked_pointer)
    _reason = field_validator('reason')(nonblank)


class VerificationReport(StrictModel):
    verdict: Literal['passed', 'clarify', 'needs_review']
    complete: StrictBool
    fields: list[FieldResult] = Field(max_length=64)

    @model_validator(mode='after')
    def honest_coverage(self):
        if len({f.field for f in self.fields}) != len(self.fields):raise ValueError('Report field Pointers must be unique.')
        # An optional field the request does not state may be absent, never unchecked.
        if self.verdict == 'passed' and (not self.complete or not any(f.status == 'passed' for f in self.fields)
                or any(not f.field or f.status != 'passed' and (f.status, f.reason) != ('absent', 'value_absent') for f in self.fields)):
            raise ValueError('Incomplete or unsuccessful coverage cannot pass.')
        bounded_json(self.model_dump(), 8192, 'Verification report')
        return self
