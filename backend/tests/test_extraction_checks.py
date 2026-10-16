"""Pure verification: origin and suitability are separate, neither invokes a model."""
import dataclasses
import importlib
import json
from decimal import Decimal

import pytest

from backend.app.models import Node
from backend.app.provenance import EvidenceContext, source_context
from backend.app.request_contracts import ExtractionCheckConfig


def checks():
    return importlib.import_module('backend.app.extraction_checks')


def config(rules=None, required=None, schema=None, cross=None):
    return ExtractionCheckConfig(expected_schema=schema or {'type': 'object'},
        required_fields=required or [], field_rules=rules or [], cross_checks=cross or [])


def source(field='/amount', pointer=None):
    return {'field': field, 'kind': 'source_field', 'source_pointer': pointer or field}


def quoted(field='/amount'):
    return {'field': field, 'kind': 'quoted'}


def run(original, value, settings=None, origins=None, reference=None):
    return checks().verify_extraction(original, value, settings or config([quoted()], ['/amount']),
        origins if origins is not None else source_context('input', 'message', original), reference)


def fields(report):
    return {f.field: (f.status, f.reason) for f in report.fields}


def reference_origins(original, reference, node_type='tool_jira', operation='search', tenant='local', owner='local'):
    from backend.app.provenance import reference_context
    node = Node(id='catalog', type=node_type, config={'operation': operation})
    ref = reference_context(node, 'text', reference, tenant_id=owner)
    return dataclasses.replace(source_context('input', 'message', original), tenant_id=tenant,
        reference_sources=ref.reference_sources)


@pytest.mark.parametrize('original,value,expected', [
    ('How do AI workflows work?', '{"amount":0}', 'needs_review'),
    ('The amount is explicitly $0.', '{"amount":0}', 'needs_review'),
    ('The amount is not stated.', '{"amount":null}', 'clarify'),
    ('The amount is not stated.', '{}', 'clarify'),
])
def test_invented_zero_explicit_zero_and_absence_remain_distinct(original, value, expected):
    report = run(original, value)
    assert report.verdict == expected
    assert fields(report)['/amount'][1] == ('origin_unconfirmed' if 'workflows' in original
        else 'suitability_unverified' if '$0' in original else 'required_absent')


@pytest.mark.parametrize('value', ['0', '0.0', '1e-1000', '1e1000', '1000.00000000000001'])
def test_structured_source_equality_uses_exact_numbers(value):
    original = '{"amount":'+value+'}'
    report = run(original, original, config([source()], ['/amount']))
    assert report.verdict == 'passed' and report.complete
    assert fields(report)['/amount'][0] == 'passed'


@pytest.mark.parametrize('original,value', [('{"amount":1000}', '{"amount":1000.00000000000001}'),
    ('{"amount":true}', '{"amount":1}'), ('{"amount":1}', '{"amount":true}')])
def test_rounded_values_and_boolean_number_equality_never_pass(original, value):
    report = run(original, value, config([source()], ['/amount']))
    assert report.verdict != 'passed'


@pytest.mark.parametrize('original', ['Yes, eligible.', 'No, not eligible.'])
def test_boolean_from_prose_is_not_confirmed_by_enum(original):
    report = run(original, '{"eligible":true}', config([
        {'field': '/eligible', 'kind': 'enum', 'allowed_values': [True, False]}], ['/eligible']))
    assert report.verdict == 'needs_review' and fields(report)['/eligible'][0] == 'unverified'


def test_structured_boolean_with_matching_pointer_passes():
    text = '{"eligible":false}'
    assert run(text, text, config([source('/eligible')], ['/eligible'])).verdict == 'passed'


@pytest.mark.parametrize('value', ['not JSON', '[]', 'null', '{"amount":"0"}', '{"amount":true}',
    '{"amount":-1}', '{"amount":NaN}', '{"amount":1e1001}', '{"amount":0,"amount":1}'])
def test_malformed_values_schema_types_and_forbidden_negatives_clarify(value):
    schema = {'type': 'object', 'properties': {'amount': {'type': 'number', 'minimum': 0}}, 'required': ['amount']}
    report = run('{"amount":0}', value, config([source()], ['/amount'], schema))
    assert report.verdict == 'clarify' and report.verdict != 'passed'


def test_schema_judges_exact_value_and_external_schema_refs_are_not_fetched():
    schema = {'type': 'object', 'properties': {'amount': {'maximum': 1000}}}
    assert run('{"amount":1000.00000000000001}', '{"amount":1000.00000000000001}',
        config([source()], ['/amount'], schema)).verdict == 'clarify'
    schema = {'$ref': 'https://example.invalid/schema.json'}
    assert run('{"amount":1}', '{"amount":1}', config([source()], ['/amount'], schema)).verdict != 'passed'


def test_pointer_collisions_escapes_and_array_indices_are_exact():
    original = '{"a.b":0,"a":{"b":1200},"a/b":{"~c":7},"items":[false]}'
    rules = [source(p) for p in ['/a.b', '/a/b', '/a~1b/~0c', '/items/0']]
    report = run(original, original, config(rules, [r['field'] for r in rules]))
    assert report.verdict == 'passed' and set(fields(report)) == {r['field'] for r in rules}
    bad = config([source('/items/00', '/items/0')], ['/items/00'])
    assert run(original, original, bad).verdict != 'passed'


def test_parent_structured_rule_covers_children_but_not_adjacent_pointer_prefix():
    original = '{"items":[{"key":"LAPTOP"}],"items_extra":999}'
    report = run(original, original, config([source('/items')], ['/items']))
    assert fields(report)['/items/0/key'][0] == 'passed'
    assert fields(report)['/items_extra'][0] == 'unverified'
    assert report.verdict == 'needs_review'


@pytest.mark.parametrize('original', ['Hello', 'Order $450'])
def test_real_catalogue_membership_cannot_approve_verb_as_product(original):
    catalogue = '{"products":[{"key":"laptop"},{"key":"monitor"}]}'
    rule = {'field': '/item', 'kind': 'reference_member', 'reference_list': '/products', 'reference_key': '/key'}
    report = run(original, '{"item":"order"}', config([rule], ['/item']),
        reference_origins(original, catalogue), catalogue)
    assert report.verdict == 'needs_review' and fields(report)['/item'][0] == 'failed'


def test_reference_membership_also_requires_origin_in_original():
    original = 'Buy a laptop'
    catalogue = '{"products":[{"key":"laptop"}]}'
    rule = {'field': '/item', 'kind': 'reference_member', 'reference_list': '/products', 'reference_key': '/key'}
    settings = config([rule], ['/item'])
    assert run(original, '{"item":"laptop"}', settings, reference_origins(original, catalogue), catalogue).verdict == 'passed'
    assert run('Hello', '{"item":"laptop"}', settings,
        reference_origins('Hello', catalogue), catalogue).verdict == 'needs_review'


@pytest.mark.parametrize('kind,operation', [('tool_http', 'search'), ('tool_python', 'search'),
    ('agent', 'search'), ('prompt', 'search'), ('chat_input', 'search'), ('tool_jira', 'create_issue')])
def test_echo_model_and_non_record_read_references_are_not_authoritative(kind, operation):
    original = 'Buy a laptop'; catalogue = '{"products":[{"key":"laptop"}]}'
    settings = config([{'field': '/item', 'kind': 'reference_member', 'reference_list': '/products', 'reference_key': '/key'}], ['/item'])
    report = run(original, '{"item":"laptop"}', settings,
        reference_origins(original, catalogue, kind, operation), catalogue)
    assert report.verdict == 'needs_review' and fields(report)['/item'][1] == 'reference_unverified'


@pytest.mark.parametrize('damage', ['tenant', 'binding', 'missing', 'malformed'])
def test_reference_wrong_tenant_binding_or_shape_cannot_pass(damage):
    original = 'Buy a laptop'; catalogue = '{"products":[{"key":"laptop"}]}'
    settings = config([{'field': '/item', 'kind': 'reference_member', 'reference_list': '/products', 'reference_key': '/key'}], ['/item'])
    origins = reference_origins(original, catalogue, owner='other' if damage == 'tenant' else 'local')
    supplied = None if damage == 'missing' else '{"products":[]}' if damage == 'binding' else catalogue
    if damage == 'malformed':origins = dataclasses.replace(origins, reference_sources=({},))
    assert run(original, '{"item":"laptop"}', settings, origins, supplied).verdict != 'passed'


def test_enum_is_type_sensitive_and_does_not_replace_origin_check():
    settings = config([{'field': '/amount', 'kind': 'enum', 'allowed_values': [False]}], ['/amount'])
    assert run('Explicit 0', '{"amount":0}', settings).verdict == 'needs_review'
    assert run('Hello', '{"amount":0}', config([{'field': '/amount', 'kind': 'enum', 'allowed_values': [0]}], ['/amount'])).verdict == 'needs_review'


def test_cross_checks_reject_inconsistent_total_and_allow_exact_derived_total():
    original = '{"quantity":3,"unit_price":450}'
    rules = [source('/quantity'), source('/unit_price'), quoted('/total')]
    settings = config(rules, ['/quantity', '/unit_price', '/total'], cross=['total == quantity * unit_price'])
    wrong = run(original, '{"quantity":3,"unit_price":450,"total":999}', settings)
    assert wrong.verdict == 'needs_review' and any(f.reason == 'cross_check_failed' for f in wrong.fields)
    right = run(original, '{"quantity":3,"unit_price":450,"total":1350}', settings)
    assert right.verdict == 'passed' and fields(right)['/total'][1] == 'calculated_checked'


def test_correct_math_does_not_prove_operand_roles_or_override_stated_total():
    value = '{"quantity":3,"unit_price":450,"total":1350}'
    rules = [quoted(p) for p in ('/quantity', '/unit_price', '/total')]
    assert run('3 laptops at $450 each', value, config(rules, cross=['total == quantity * unit_price'])).verdict == 'needs_review'
    original = '{"quantity":3,"unit_price":450,"total":999}'
    assert run(original, value, config([source(p) for p in ('/quantity', '/unit_price', '/total')],
        cross=['total == quantity * unit_price'])).verdict != 'passed'


def test_cross_check_reads_actual_short_circuit_fields_only():
    original = '{"amount":5}'
    settings = config([source()], ['/amount'], cross=['amount == coalesce(amount, unbound)'])
    assert run(original, original, settings).verdict == 'passed'


@pytest.mark.parametrize('origins', [EvidenceContext(), EvidenceContext(('0',), complete=False), EvidenceContext(('Unrelated 0',))])
def test_unrelated_or_incomplete_origins_cannot_ground_original(origins):
    assert run('{"amount":0}', '{"amount":0}', config([source()], ['/amount']), origins).verdict != 'passed'


def test_omitted_rule_empty_object_and_uncovered_fields_never_pass():
    with pytest.raises(ValueError):config([], ['/amount'])
    assert run('{"amount":1,"extra":2}', '{"amount":1,"extra":2}', config([source()], ['/amount'])).verdict == 'needs_review'
    assert run('{}', '{}', config()).verdict != 'passed'


def test_large_coverage_deep_nesting_and_report_bytes_are_bounded():
    original = json.dumps({('é'*100)+str(i): i for i in range(100)})
    report = run(original, original, config())
    assert report.verdict == 'needs_review' and not report.complete
    assert len(report.fields) <= 64 and len(report.model_dump_json().encode()) <= 8192
    nested = '{"a":'*40 + '0' + '}'*40
    report = run(nested, nested, config())
    assert report.verdict != 'passed' and not report.complete


def test_checks_call_no_model_and_leave_origin_labels_unchanged(monkeypatch):
    from backend.app import providers
    def forbidden(*a, **k):raise AssertionError('A deterministic verifier cannot call a judge')
    for name in ('ollama_chat', 'openai_chat', 'claude_chat'):monkeypatch.setattr(providers, name, forbidden)
    original = '{"amount":0}';origins = source_context('input', 'message', original)
    before = dataclasses.asdict(origins)
    assert run(original, original, config([source()], ['/amount']), origins).verdict == 'passed'
    assert dataclasses.asdict(origins) == before


def test_policy_digest_covers_every_rule_reference_pointer_and_tenant():
    from backend.app.verification_receipts import verification_policy_digest
    original = '{"amount":0}'; origins = dataclasses.replace(source_context('input', 'message', original), tenant_id='local')
    settings = config([source()], ['/amount']);digest = verification_policy_digest(settings, origins)
    changed = config([source(pointer='/other')], ['/amount'])
    assert verification_policy_digest(changed, origins) != digest
    assert verification_policy_digest(settings, dataclasses.replace(origins, tenant_id='other')) != digest


def test_verifier_is_registered_with_explicit_continue_reply_routes():
    from backend.app.registry import REGISTRY
    assert REGISTRY['verify_extraction'].branch_routes==('continue','reply')


def test_schema_keyword_names_in_properties_are_data_and_need_no_regex():
    text = '{"pattern":"abc"}'
    schema = {'type':'object', 'properties':{'pattern':{'type':'string'}}}
    assert run(text, text, config([source('/pattern')], ['/pattern'], schema)).verdict == 'passed'


def test_config_mutated_after_validation_cannot_omit_required_acceptance_rule():
    settings = config([source()], ['/amount']);settings.required_fields.append('/other')
    with pytest.raises(ValueError, match='configuration'):
        run('{"amount":0,"other":1}', '{"amount":0,"other":1}', settings)


def test_original_duplicate_json_cannot_turn_ambiguous_number_into_enum_pass():
    original = '{"amount":0,"amount":1}'
    settings = config([{'field':'/amount','kind':'enum','allowed_values':[0]}], ['/amount'])
    assert run(original, '{"amount":0}', settings).verdict != 'passed'


def test_schema_patterns_and_remote_references_never_start_a_network_or_unbounded_regex(monkeypatch):
    from backend.app import exact_json
    def forbidden(*a, **k):raise AssertionError('Unsafe schema must not execute')
    monkeypatch.setattr(exact_json, 'validate', forbidden)
    for schema in [{'properties':{'amount':{'pattern':'(a+)+$'}}}, {'$ref':'https://example.invalid/x'}]:
        assert run('{"amount":0}', '{"amount":0}', config([source()], ['/amount'], schema)).verdict != 'passed'


@pytest.mark.parametrize('target', [{'$ref':'https://example.invalid/no-network'}, {'pattern':'(a+)+$'}])
def test_local_reference_cannot_hide_remote_resolution_or_regex(target, monkeypatch):
    import urllib.request
    calls = []
    def forbidden(url, *a, **k):
        calls.append(url);raise AssertionError('Pure verifier must not attempt remote retrieval')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    schema = {'$ref':'#/custom','custom':target}
    assert checks()._schema_safe(schema) is False
    report = run('{"amount":0}', '{"amount":0}', config([source()], ['/amount'], schema))
    assert report.verdict != 'passed' and calls == []


def test_validator_itself_refuses_retrieval_even_if_precheck_misses_a_reference(monkeypatch):
    import urllib.request
    calls = []
    def forbidden(url, *a, **k):calls.append(url);raise AssertionError('No network')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(checks(), '_schema_safe', lambda _: True)
    schema = {'$ref':'#/custom','custom':{'$ref':'https://example.invalid/no-network'}}
    assert run('{"amount":0}', '{"amount":0}', config([source()], ['/amount'], schema)).verdict != 'passed'
    assert calls == []


def test_local_reference_normal_schema_and_escaped_target_still_work():
    schema = {'$ref':'#/custom~1schema','custom/schema':{'type':'object', 'properties':{'amount':{'type':'number'}}}}
    assert run('{"amount":0}', '{"amount":0}', config([source()], ['/amount'], schema)).verdict == 'passed'


def test_reference_context_requires_an_actual_declared_record_output():
    from backend.app.provenance import reference_context
    with pytest.raises(ValueError):reference_context(Node(id='read',type='tool_jira'), 'nonexistent', '{}', tenant_id='local')


def test_boolean_enum_origin_is_scoped_to_the_exact_pointer_not_a_leaf_name():
    settings = config([{'field':'/eligible','kind':'enum','allowed_values':[True,False]}], ['/eligible'])
    assert run('{"unrelated":{"eligible":true}}','{"eligible":true}',settings).verdict == 'needs_review'


def test_boolean_enum_origin_under_escaped_pointer_is_found_correctly():
    text = '{"a/b":{"~eligible":true}}'
    pointer = '/a~1b/~0eligible'
    settings = config([{'field':pointer,'kind':'enum','allowed_values':[True,False]}], [pointer])
    assert run(text,text,settings).verdict == 'passed'


# ------------------------------------------------- required groups and accept_quoted

def accept(field):
    return {'field': field, 'kind': 'accept_quoted'}


PURCHASE_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'item': {'type': ['string', 'null']}, 'quantity': {'type': ['number', 'null']},
    'unit_price': {'type': ['number', 'null']}, 'total': {'type': ['number', 'null']}}}


def purchase(**overrides):
    settings = dict(expected_schema=PURCHASE_SCHEMA, required_fields=['/item'],
        required_any=[['/total'], ['/quantity', '/unit_price']],
        field_rules=[accept('/item'), accept('/quantity'), accept('/unit_price'), accept('/total')],
        cross_checks=['total == quantity * unit_price'])
    return ExtractionCheckConfig(**{**settings, **overrides})


def order(item='laptops', quantity=None, unit_price=None, total=None):
    return json.dumps({'item': item, 'quantity': quantity, 'unit_price': unit_price, 'total': total})


@pytest.mark.parametrize('original,value', [
    ('3 laptops at $450 each.', order(quantity=3, unit_price=450)),
    ('three laptops at $450 each', order(quantity=3, unit_price=450)),
    ('laptops for a total of $1,350', order(total=1350)),
    ('3 laptops at $450 each, $1,350 in total', order(quantity=3, unit_price=450, total=1350)),
])
def test_one_complete_group_of_quoted_facts_passes(original, value):
    report = run(original, value, purchase())
    assert report.verdict == 'passed' and report.complete, fields(report)
    assert all(status in ('passed', 'absent') for status, _ in fields(report).values())
    assert {reason for status, reason in fields(report).values() if status == 'absent'} <= {'value_absent'}


@pytest.mark.parametrize('value,absent', [
    (order(), ['/quantity', '/unit_price', '/total']),
    (order(quantity=3), ['/unit_price', '/total']),
])
def test_no_complete_group_asks_for_the_missing_facts(value, absent):
    report = run('I need 3 laptops', value, purchase())
    assert report.verdict == 'clarify'
    assert [p for p, (s, r) in fields(report).items() if (s, r) == ('absent', 'required_absent')] == absent


def test_a_missing_required_field_still_asks_when_a_group_is_complete():
    report = run('3 at $450 each', order(item=None, quantity=3, unit_price=450), purchase())
    assert report.verdict == 'clarify' and fields(report)['/item'] == ('absent', 'required_absent')


@pytest.mark.parametrize('value,field', [
    (order(quantity=4, unit_price=450), '/quantity'),       # invented number
    (order(item='desktops', quantity=3, unit_price=450), '/item'),  # invented text
])
def test_accept_quoted_never_passes_a_value_not_written_in_the_request(value, field):
    report = run('3 laptops at $450 each.', value, purchase())
    assert report.verdict == 'needs_review'
    assert fields(report)[field] == ('unverified', 'origin_unconfirmed')


def test_a_quoted_total_that_disagrees_with_the_cross_check_fails():
    report = run('3 laptops at $450 each, $1,000 in total', order(quantity=3, unit_price=450, total=1000), purchase())
    assert report.verdict == 'needs_review'
    assert fields(report)['/total'] == ('failed', 'cross_check_failed')


def test_accept_quoted_on_a_boolean_needs_a_structured_source():
    settings = ExtractionCheckConfig(expected_schema={'type': 'object'}, required_fields=['/eligible'],
        field_rules=[accept('/eligible')])
    assert run('Yes, eligible.', '{"eligible":true}', settings).verdict == 'needs_review'
    assert run('{"eligible":true}', '{"eligible":true}', settings).verdict == 'passed'


def test_quoted_rules_keep_their_meaning():
    # quoted proves origin only; it never makes a field pass on its own.
    report = run('3 laptops at $450 each.', order(quantity=3, unit_price=450),
        purchase(field_rules=[quoted('/item'), quoted('/quantity'), quoted('/unit_price'), quoted('/total')]))
    assert report.verdict == 'needs_review'
    assert fields(report)['/quantity'] == ('unverified', 'suitability_unverified')


def test_absent_optional_fields_alone_never_pass():
    settings = ExtractionCheckConfig(expected_schema={'type': 'object'}, field_rules=[accept('/amount')])
    report = run('No amount here.', '{"amount":null}', settings)
    assert report.verdict == 'needs_review'


def test_a_report_cannot_claim_a_pass_over_missing_or_unchecked_fields():
    from backend.app.request_contracts import VerificationReport
    ok = {'field': '/item', 'status': 'passed', 'reason': 'source_checked'}
    VerificationReport(verdict='passed', complete=True, fields=[ok, {'field': '/total', 'status': 'absent', 'reason': 'value_absent'}])
    for other in ({'field': '/total', 'status': 'absent', 'reason': 'required_absent'},
                  {'field': '/total', 'status': 'unverified', 'reason': 'rule_missing'}):
        with pytest.raises(ValueError):
            VerificationReport(verdict='passed', complete=True, fields=[ok, other])
    with pytest.raises(ValueError):
        VerificationReport(verdict='passed', complete=True, fields=[{'field': '/total', 'status': 'absent', 'reason': 'value_absent'}])


@pytest.mark.parametrize('settings', [
    {'required_any': [['/total'], ['/missing_rule']]},
    {'required_any': [['']]},
    {'required_any': [['/total', '/total']]},
    {'required_any': [['/total'], ['/total']]},
    {'required_any': [['/total']] * 9},
    {'required_any': [[]]},
    {'field_rules': [{'field': '/total', 'kind': 'accept_quoted', 'source_pointer': '/total'}]},
])
def test_invalid_required_groups_and_rules_are_refused(settings):
    with pytest.raises(ValueError):
        purchase(**settings)


@pytest.mark.parametrize('original,value,expected', [
    # The stated quantity is left out, so the contradiction with the total is hidden.
    ('Buy 3 laptops at $450, total $1,000.', order(unit_price=450, total=1000), 'needs_review'),
    ('Buy 3 laptops at $450 each, $1,350 total.', order(unit_price=450, total=1350), 'needs_review'),
    ('Buy 3 laptops at $450 each, $1,350 total.', order(quantity=3, unit_price=450, total=1350), 'passed'),
    ('three laptops at $450 each', order(quantity=3, unit_price=450), 'passed'),
    ('3 laptops at $450 each for project 2027', order(quantity=3, unit_price=450), 'needs_review'),
    # A number inside a read text value is accounted for.
    ('3 laptops 15 inch at $450 each', order(item='laptops 15 inch', quantity=3, unit_price=450), 'passed'),
])
def test_every_stated_number_must_be_read_when_quotes_are_accepted(original, value, expected):
    report = run(original, value, purchase())
    assert report.verdict == expected, fields(report)
    if expected == 'needs_review':
        assert fields(report)[''] == ('unverified', 'unread_number')


def test_the_unread_number_rule_needs_an_accept_quoted_pass():
    settings = config([source()], ['/amount'])
    original = '{"amount":5,"note":"order 7"}'
    assert run(original, '{"amount":5}', settings).verdict == 'passed'


def discounted(**changes):
    schema = {'type': 'object', 'properties': {f: {'type': ['number', 'null']} for f in ('quantity', 'unit_price', 'discounted_price', 'total')}}
    settings = dict(expected_schema=schema, required_fields=['/quantity', '/unit_price', '/total'],
        field_rules=[accept('/' + f) for f in ('quantity', 'unit_price', 'discounted_price', 'total')],
        cross_checks=['total == quantity * coalesce(discounted_price, unit_price)'])
    return ExtractionCheckConfig(**{**settings, **changes})


@pytest.mark.parametrize('original,value,expected', [
    # coalesce falls back to unit_price: the unused null never disables the check.
    ('3 at $450 each, total $1,000', {'quantity': 3, 'unit_price': 450, 'discounted_price': None, 'total': 1000}, 'needs_review'),
    ('3 at $450 each, total $1,350', {'quantity': 3, 'unit_price': 450, 'discounted_price': None, 'total': 1350}, 'passed'),
    ('3 at $450, discounted $400, total $1,200', {'quantity': 3, 'unit_price': 450, 'discounted_price': 400, 'total': 1200}, 'passed'),
])
def test_a_cross_check_follows_the_fields_it_actually_reads(original, value, expected):
    report = run(original, json.dumps(value), discounted())
    assert report.verdict == expected, fields(report)
    if expected == 'needs_review':
        assert fields(report)['/total'] == ('failed', 'cross_check_failed')


def test_a_cross_check_is_skipped_only_when_a_field_it_reads_is_not_stated():
    settings = discounted(required_fields=['/quantity', '/unit_price'],
                          cross_checks=['total == quantity * unit_price'])
    report = run('3 at $450 each', '{"quantity":3,"unit_price":450,"discounted_price":null,"total":null}', settings)
    assert report.verdict == 'passed', fields(report)


def test_a_check_that_fails_for_another_reason_is_never_skipped_for_an_unread_null():
    # coalesce never reaches discounted_price, so its null cannot excuse the division error.
    settings = discounted(cross_checks=['total / quantity == coalesce(unit_price, discounted_price)'])
    report = run('total $1,000 for 0 items at $450', '{"quantity":0,"unit_price":450,"discounted_price":null,"total":1000}', settings)
    assert report.verdict == 'needs_review' and fields(report)[''] == ('unverified', 'cross_check_unavailable')
