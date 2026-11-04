"""Strict wire contracts, independent of future cache/router/check handlers."""
import importlib
import json

import pytest
from pydantic import ValidationError
from backend.app.models import Workflow


def contracts():
    return importlib.import_module('backend.app.request_contracts')


def workflow(**changes):
    return Workflow.model_validate({'name': 'Old document', 'nodes': [{'id': 'input', 'type': 'chat_input'}], **changes})


def test_old_workflow_defaults_to_contract_one_and_roundtrips():
    value = workflow()
    assert value.contract == 1
    assert Workflow.model_validate_json(value.model_dump_json()) == value
    assert workflow(contract=2).contract == 2


@pytest.mark.parametrize('value', [True, False, '1', '2', 1.0, 0, 3, None])
def test_contract_is_an_actual_supported_integer(value):
    with pytest.raises(ValidationError):
        workflow(contract=value)


def test_configs_roundtrip_and_forbid_unknown_keys():
    c = contracts()
    for model, raw in [(c.GreetingCacheConfig, {'entries': [{'message': 'Hello', 'reply': 'Hi'}]}),
                       (c.RequestRouterConfig, {'purpose': 'Handle support requests'}),
                       (c.ExtractionCheckConfig, {'expected_schema': {'type': 'object'}, 'required_fields': ['/item'],
                                                 'field_rules': [{'field': '/item', 'kind': 'quoted'}]})]:
        value = model.model_validate(raw)
        assert model.model_validate_json(value.model_dump_json()) == value
        with pytest.raises(ValidationError):model.model_validate({**raw, 'confidence': 0.99})


@pytest.mark.parametrize('route,fields,reply,valid', [
    ('proceed', [], '', True), ('clarify', ['item'], 'Which item?', True),
    ('out_of_scope', [], 'This workflow handles purchases.', True),
    ('proceed', ['item'], '', False), ('proceed', [], 'Approved!', False),
    ('clarify', [], '', False), ('out_of_scope', [], '  ', False),
    ('approve', [], '', False), ('clarify', ['item', 'item'], 'Which?', False),
])
def test_router_decision_is_explicit_and_consistent(route, fields, reply, valid):
    raw = {'route': route, 'reason': 'Request classification', 'missing_fields': fields, 'reply': reply}
    if valid:
        result = contracts().RouterDecision.model_validate(raw)
        assert result.branch == ('continue' if route == 'proceed' else 'reply')
        assert set(json.loads(result.model_dump_json())) == set(raw)
    else:
        with pytest.raises(ValidationError):contracts().RouterDecision.model_validate(raw)


@pytest.mark.parametrize('key,value', [('reason', ''), ('reason', ' '), ('reason', 'x'*301),
                                    ('reply', 'x'*2001), ('missing_fields', ['x'*65]),
                                    ('missing_fields', [f'f{i}' for i in range(17)]), ('confidence', 1)])
def test_router_decision_limits(key, value):
    raw = {'route': 'clarify', 'reason': 'Needs details', 'missing_fields': [], 'reply': 'Please clarify'}
    raw[key] = value
    with pytest.raises(ValidationError):contracts().RouterDecision.model_validate(raw)


def test_greeting_limits_and_normalisation_collisions():
    model = contracts().GreetingCacheConfig
    boundary = {'message': 'x'*256, 'reply': 'y'*2000}
    assert len(model(entries=[boundary]).entries[0].reply) == 2000
    assert len(model(entries=[{'message': str(i), 'reply': 'hi'} for i in range(100)]).entries) == 100
    for entries in [[{**boundary, 'message': 'x'*257}], [{**boundary, 'reply': 'x'*2001}],
                    [{'message': ' ', 'reply': 'hi'}], [{'message': 'Hi', 'reply': ' '}],
                    [{'message': str(i), 'reply': 'hi'} for i in range(101)],
                    [{'message': ' Hello  there ', 'reply': 'a'}, {'message': 'hello there', 'reply': 'b'}],
                    [{'message': 'é', 'reply': 'a'}, {'message': 'e\u0301', 'reply': 'b'}]]:
        with pytest.raises(ValidationError):model(entries=entries)


@pytest.mark.parametrize('key', ['accepted_requests', 'exclusions', 'clarification_rules', 'examples'])
def test_router_brief_limits(key):
    model = contracts().RequestRouterConfig
    assert len(getattr(model(purpose='x'*2000, **{key: ['x'*500]*20}), key)) == 20
    for raw in [{'purpose': ' '}, {'purpose': 'x'*2001}, {'purpose': 'work', key: ['x'*501]},
                {'purpose': 'work', key: ['x']*21}, {'purpose': 'work', key: ['']}]:
        with pytest.raises(ValidationError):model.model_validate(raw)


@pytest.mark.parametrize('raw', [
    {'field': '/item', 'kind': 'source_field'}, {'field': '/item', 'kind': 'enum'},
    {'field': '/item', 'kind': 'reference_member', 'reference_list': '/items'},
    {'field': '/item', 'kind': 'quoted', 'source_pointer': '/echo'},
    {'field': '/bad~2escape', 'kind': 'quoted'}, {'field': 'item', 'kind': 'quoted'},
    {'field': '/item', 'kind': 'llm_judge'},
])
def test_field_rules_reject_missing_or_irrelevant_parameters(raw):
    with pytest.raises(ValidationError):contracts().FieldRule.model_validate(raw)


def test_field_rules_and_required_coverage():
    c = contracts()
    for raw in [{'field': '/a~1b', 'kind': 'quoted'},
                {'field': '/item', 'kind': 'source_field', 'source_pointer': '/request/item'},
                {'field': '/item', 'kind': 'enum', 'allowed_values': ['laptop', 'monitor']},
                {'field': '/item', 'kind': 'reference_member', 'reference_list': '/products', 'reference_key': '/name'}]:
        assert c.FieldRule.model_validate_json(c.FieldRule(**raw).model_dump_json()) == c.FieldRule(**raw)
    with pytest.raises(ValidationError):c.ExtractionCheckConfig(expected_schema={}, required_fields=['/amount'])
    with pytest.raises(ValidationError):c.ExtractionCheckConfig(expected_schema={}, cross_checks=['amount + 1'])
    with pytest.raises(ValidationError):c.ExtractionCheckConfig(expected_schema={'type': 'unknown'})


def test_verification_report_cannot_hide_failed_or_omitted_coverage():
    model = contracts().VerificationReport
    good = {'verdict': 'passed', 'complete': True, 'fields': [{'field': '/item', 'status': 'passed', 'reason': 'enum_match'}]}
    assert model.model_validate_json(model(**good).model_dump_json()) == model(**good)
    for raw in [{**good, 'complete': False}, {**good, 'fields': [{'field': '/item', 'status': 'unverified', 'reason': 'quoted_only'}]},
                {**good, 'fields': [{'field': '/item', 'status': 'failed', 'reason': 'wrong_value'}]},
                {**good, 'fields': good['fields']*65}, {**good, 'fields': [{'field': '/x', 'status': 'passed', 'reason': 'x'*301}]},
                {**good, 'confidence': 1}]:
        with pytest.raises(ValidationError):model.model_validate(raw)
    with pytest.raises(ValidationError):model(verdict='needs_review', complete=False,
        fields=[{'field': '/'+str(i), 'status': 'unverified', 'reason': 'é'*300} for i in range(64)])


def test_admission_and_verification_handlers_are_registered():
    from backend.app.registry import REGISTRY
    assert 'request_router' in REGISTRY
    assert 'verify_extraction' in REGISTRY
