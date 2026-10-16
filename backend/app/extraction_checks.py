"""Deterministic shape, origin and suitability checks. No model or network calls.

This is a pure checking layer. Registration, runtime-owned receipts and review pauses
land separately; a report or an origin label is not by itself write authorization.
"""
from decimal import Decimal

from . import calculate, exact_json, provenance
from .request_contracts import ExtractionCheckConfig, FieldResult, VerificationReport, checked_pointer

MAX_DATA_BYTES = 262144
MAX_DEPTH = 32
MAX_VISITS = 4096
MISSING = object()


def at_pointer(value, pointer):
    checked_pointer(pointer)
    for key in pointer[1:].split('/') if pointer else []:
        key = key.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict):value = value.get(key, MISSING)
        elif isinstance(value, list) and (key == '0' or key.isascii() and key.isdigit() and not key.startswith('0')):
            if len(key) > 6:return MISSING
            index = int(key);value = value[index] if index < len(value) else MISSING
        else:return MISSING
        if value is MISSING:return MISSING
    return value


def equal(left, right):
    """JSON equality with exact numbers and no Python boolean/number aliasing."""
    if isinstance(left, bool) or isinstance(right, bool):return type(left) is type(right) and left == right
    if isinstance(left, (int, Decimal)) and isinstance(right, (int, Decimal)):return left == right
    if type(left) is not type(right):return False
    if isinstance(left, dict):return left.keys() == right.keys() and all(equal(left[k], right[k]) for k in left)
    if isinstance(left, list):return len(left) == len(right) and all(equal(a, b) for a, b in zip(left, right))
    return left == right


def _walk(value, parts=(), depth=0, visits=None):
    visits = [0] if visits is None else visits;visits[0] += 1
    if depth > MAX_DEPTH or visits[0] > MAX_VISITS:raise ValueError('coverage_limit')
    if isinstance(value, dict) and value:
        for key, child in value.items():yield from _walk(child, (*parts, key), depth+1, visits)
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):yield from _walk(child, (*parts, index), depth+1, visits)
    else:yield provenance.pointer(parts)


def _read(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_DATA_BYTES:raise ValueError('data_limit')
    parsed = exact_json.loads(text, reject_duplicates=True)
    list(_walk(parsed))  # Bound all subsequent equality/schema/reference traversals too.
    return parsed


def _bounded_report(results, *, complete=True, clarify=False):
    entries = [FieldResult(field=path, status=status, reason=reason) for path, (status, reason) in results.items()
        if len(path) <= 128]
    if len(entries) != len(results) or len(entries) > 64:complete = False
    entries = entries[:64]
    while True:
        selected = entries if complete else [e for e in entries[:63] if e.field != ''] + [
            FieldResult(field='', status='unverified', reason='coverage_limit')]
        verdict = 'clarify' if clarify else 'passed' if complete and any(e.status == 'passed' for e in selected) and all(
            e.status == 'passed' or (e.status, e.reason) == ('absent', 'value_absent') for e in selected) else 'needs_review'
        try:return VerificationReport(verdict=verdict, complete=complete, fields=selected)
        except ValueError:
            if not entries:raise
            entries.pop();complete = False


def _schema_safe(schema):
    # Remote resolution can make a pure check perform a network request. Arbitrary
    # regexes have no deadline in Python and belong to neither this verifier nor rules.
    from urllib.parse import unquote
    pending = [(schema, schema)]; seen = set()
    maps = ('properties', '$defs', 'definitions', 'dependentSchemas', 'dependencies')
    children = ('allOf', 'anyOf', 'oneOf', 'prefixItems', 'items', 'additionalProperties',
        'unevaluatedProperties', 'unevaluatedItems', 'contains', 'propertyNames', 'if', 'then', 'else', 'not', 'additionalItems')
    while pending:
        current, resource = pending.pop()
        if not isinstance(current, dict):continue
        if '$id' in current:resource = current
        identity = (id(current), id(resource))
        if identity in seen:continue
        seen.add(identity)
        if len(seen) > MAX_VISITS:return False
        if any(k in current for k in ('pattern', 'patternProperties')):return False
        for keyword in ('$ref', '$dynamicRef', '$recursiveRef'):
            if keyword not in current:continue
            ref = current[keyword]
            if not isinstance(ref, str) or not ref.startswith('#'):return False
            try:fragment = unquote(ref[1:], errors='strict')
            except UnicodeError:return False
            if not fragment or fragment.startswith('/'):
                try:target = at_pointer(resource, fragment)
                except ValueError:return False
                if target is MISSING:return False
                pending.append((target, resource))
            else:
                # Anchors can be beneath arbitrary keys too. Inspect every candidate,
                # bounded and cycle-aware, rather than assuming known keyword ancestry.
                objects = [resource]; found = False; visits = 0
                while objects:
                    obj = objects.pop();visits += 1
                    if visits > MAX_VISITS:return False
                    if isinstance(obj, dict):
                        if obj.get('$anchor') == fragment or obj.get('$dynamicAnchor') == fragment:
                            pending.append((obj, resource));found = True
                        objects.extend(obj.values())
                    elif isinstance(obj, list):objects.extend(obj)
                if not found:return False
        for key in maps:
            if isinstance(current.get(key), dict):pending.extend((v, resource) for v in current[key].values() if isinstance(v, dict))
        for key in children:
            child = current.get(key)
            if isinstance(child, list):pending.extend((v, resource) for v in child)
            elif isinstance(child, dict):pending.append((child, resource))
    return True


def _reference(reference, origins):
    if not isinstance(origins, provenance.EvidenceContext) or not origins.complete or not origins.tenant_id:return MISSING
    if not any(isinstance(r, provenance.ReferenceEvidence) and r.tenant_id == origins.tenant_id
               and r.text == reference for r in origins.reference_sources):return MISSING
    try:return _read(reference)
    except (ValueError, TypeError, RecursionError, UnicodeError):return MISSING


def verify_extraction(original: str, value: str, config: ExtractionCheckConfig,
                      origins: provenance.EvidenceContext, reference: str | None = None) -> VerificationReport:
    try:config = ExtractionCheckConfig.model_validate(config.model_dump(mode='json'))
    except (ValueError, TypeError, RecursionError):raise ValueError('Invalid extraction checking configuration.') from None
    try:parsed = _read(value)
    except (ValueError, TypeError, RecursionError, UnicodeError) as exc:
        limited = str(exc) in ('coverage_limit', 'data_limit')
        return _bounded_report({'': ('unverified' if limited else 'failed',
            'coverage_limit' if limited else 'value_shape_invalid')}, complete=not limited, clarify=not limited)
    if not isinstance(parsed, (dict,list)):return _bounded_report({'': ('failed', 'value_shape_invalid')}, clarify=True)
    if not _schema_safe(config.expected_schema):return _bounded_report({'': ('unverified', 'schema_unavailable')}, complete=False)
    from referencing import Registry
    # An empty Registry has a fail-to-retrieve resolver. This independently prevents
    # network access even if a future safety traversal misses a schema indirection.
    try:exact_json.validate(parsed, config.expected_schema, registry=Registry())
    except Exception:
        # Schema/resolution failures are not model content and never grant a pass.
        return _bounded_report({'': ('failed', 'schema_invalid')}, clarify=True)
    actual = list(_walk(parsed)); paths = list(dict.fromkeys([*actual, *config.required_fields,
        *(f for group in config.required_any for f in group), *(rule.field for rule in config.field_rules)]))
    if not parsed and not config.required_fields and not config.required_any:return _bounded_report({'': ('unverified', 'no_fields')}, complete=False)
    eligible = isinstance(origins, provenance.EvidenceContext) and origins.complete and original in origins.eligible_texts
    try:original_value = _read(original) if eligible else MISSING
    except (ValueError, TypeError, RecursionError, UnicodeError):
        original_value = MISSING
        if isinstance(original, str) and original.lstrip().startswith(('{', '[')):
            return _bounded_report({'': ('unverified', 'original_shape_invalid')}, complete=False)
    evidence = provenance.Evidence(provenance.EvidenceContext((original,), complete=eligible))
    catalogue = _reference(reference, origins)
    results = {}; hard_failure = set(); derived_candidates = set()
    for path in paths:
        candidate = at_pointer(parsed, path)
        if candidate is MISSING or candidate is None:
            results[path] = ('absent', 'required_absent' if path in config.required_fields else 'value_absent');continue
        applicable = [r for r in config.field_rules if r.field == path]
        # Structured equality for a parent is proof for its own descendants only.
        inherited = [r for r in config.field_rules if r.kind == 'source_field' and path.startswith(r.field+'/')]
        if not applicable and not inherited:
            results[path] = ('unverified', 'rule_missing');continue
        origin = eligible and evidence.label(candidate, path.rsplit('/', 1)[-1]) in provenance.TRUSTED
        if isinstance(candidate, bool):
            origin = eligible and original_value is not MISSING and equal(at_pointer(original_value, path), candidate)
        suitable = False; failure = None; unknown = None
        for rule in [*applicable, *inherited]:
            checked_value = at_pointer(parsed, rule.field)
            if rule.kind == 'quoted':continue
            if rule.kind == 'accept_quoted':
                # Opt-in: the exact value is written in the trusted original. This proves
                # where the value is written, not its role; cross-checks address the role.
                if origin:suitable = True
                continue
            if rule.kind == 'source_field':
                observed = at_pointer(original_value, rule.source_pointer) if original_value is not MISSING else MISSING
                if observed is MISSING:unknown = 'source_unavailable'
                elif not equal(checked_value, observed):failure = 'source_mismatch'
                else:origin = suitable = eligible
            elif rule.kind == 'enum':
                allowed = exact_json.loads(exact_json.dumps(rule.allowed_values), reject_duplicates=True)
                if not any(equal(candidate, item) for item in allowed):failure = 'enum_mismatch'
                else:suitable = True
            elif rule.kind == 'reference_member':
                records = at_pointer(catalogue, rule.reference_list) if catalogue is not MISSING else MISSING
                if not isinstance(records, list):unknown = 'reference_unverified';continue
                keys = [at_pointer(row, rule.reference_key) for row in records]
                if any(k is MISSING for k in keys):unknown = 'reference_unverified'
                elif not any(equal(candidate, k) for k in keys):failure = 'reference_mismatch'
                else:suitable = True
        if failure:
            results[path] = ('failed', failure);hard_failure.add(path)
        elif unknown:results[path] = ('unverified', unknown)
        elif not origin:results[path] = ('unverified', 'origin_unconfirmed');derived_candidates.add(path)
        elif not suitable:results[path] = ('unverified', 'suitability_unverified')
        else:results[path] = ('passed', 'source_checked')
    group_met = any(all(results.get(p, (None,))[0] != 'absent' for p in group) for group in config.required_any)
    if config.required_any and not group_met:
        for path in (f for group in config.required_any for f in group):
            if results.get(path, (None,))[0] == 'absent':results[path] = ('absent', 'required_absent')
    for expression in config.cross_checks:
        # A check over a field the request does not state has nothing to compare.
        if any(results.get(provenance.pointer(p.split('.')), (None,))[0] == 'absent'
               for p in calculate.paths(calculate.parse(expression))):continue
        try:
            passed, used = calculate.run(expression, value, 'check')
            used_paths = list(dict.fromkeys(provenance.pointer(p.split('.')) for p in used))
        except calculate.CalculationError:
            results[''] = ('unverified', 'cross_check_unavailable');continue
        if passed is not True:
            for path in used_paths or ['']:results[path] = ('failed', 'cross_check_failed');hard_failure.add(path)
            continue
        tree = calculate.parse(expression)
        if tree.kind == 'compare' and tree.value == '==' and tree.args[0].kind == 'path':
            target = provenance.pointer(tree.args[0].value.split('.'))
            operands = [p for p in used_paths if p != target]
            # A quote cannot establish quantity/price roles. Only independently checked
            # operands can establish a missing calculated target; never override a lie.
            if (target in derived_candidates and target not in hard_failure and operands
                    and tree.args[0].value not in calculate.paths(tree.args[1])
                    and all(results.get(p, (None,))[0] == 'passed' for p in operands)):
                results[target] = ('passed', 'calculated_checked')
    # accept_quoted proves where a value is written, not its role. A reading that leaves
    # out a stated number can hide a contradiction ("3 at $450, total $1,000" read without
    # the 3), so every number in the request must appear among the read values.
    accepted = {r.field for r in config.field_rules if r.kind == 'accept_quoted'}
    if eligible and '' not in results and any(results.get(p, (None,))[0] == 'passed' for p in accepted):
        read = set()
        for path in actual:
            leaf = at_pointer(parsed, path)
            if isinstance(leaf, (int, Decimal)) and not isinstance(leaf, bool):read.add(Decimal(leaf))
            elif isinstance(leaf, str):read |= provenance.numbers_in(leaf)
        if provenance.numbers_in(original) - read:results[''] = ('unverified', 'unread_number')
    missing_required = any(results.get(p, (None,))[0] == 'absent' for p in config.required_fields) or bool(config.required_any and not group_met)
    return _bounded_report(results, clarify=missing_required)
