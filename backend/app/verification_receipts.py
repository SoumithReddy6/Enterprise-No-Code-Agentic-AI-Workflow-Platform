"""Bounded audit bindings for a runtime-owned verification result.

Checksums detect stale context and accidental damage; they are not signatures. Models
must never supply this record. Task 6 owns creation, restore and review authorization.
"""
import hashlib
import json
import re

from .provenance import EvidenceContext, ReferenceEvidence
from .request_contracts import VerificationReport

VERIFICATION_KEY = '_verification'
MAX_RECEIPT_BYTES = 8192
KEYS = frozenset({'v', 'run_id', 'node_id', 'input_digest', 'value_digest', 'policy_digest',
                 'report', 'report_digest', 'review_id'})


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def digest(text):
    if not isinstance(text, str):raise ValueError('Verification digests require exact text.')
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def verification_policy_digest(config, origins: EvidenceContext):
    """All rules/pointers plus the tenant and exact resolved reference bindings."""
    if not isinstance(origins, EvidenceContext):raise ValueError('Verification needs a resolved evidence context.')
    references = []
    for record in origins.reference_sources:
        if not isinstance(record, ReferenceEvidence):raise ValueError('Malformed reference evidence.')
        references.append({'node_id': record.node_id, 'port': record.port, 'tenant_id': record.tenant_id,
            'sha256': digest(record.text)})
    return digest(_json({'config': config.model_dump(mode='json'), 'tenant_id': origins.tenant_id,
                         'references': references}))


def checked_receipt(record: object) -> dict:
    try:
        if not isinstance(record, dict) or set(record) != KEYS or type(record['v']) is not int or record['v'] != 1:
            raise ValueError('shape')
        if not (isinstance(record['run_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', record['run_id'])
                and isinstance(record['node_id'], str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', record['node_id'])
                and (record['review_id'] is None or isinstance(record['review_id'], str)
                     and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', record['review_id']))):raise ValueError('scope')
        if not all(isinstance(record[k], str) and re.fullmatch(r'[a-f0-9]{64}', record[k])
                   for k in ('input_digest', 'value_digest', 'policy_digest', 'report_digest')):raise ValueError('digest')
        report = VerificationReport.model_validate(record['report'])
        if digest(_json(report.model_dump(mode='json'))) != record['report_digest']:raise ValueError('report changed')
        if record['review_id'] is not None and report.verdict == 'clarify':raise ValueError('invalid confirmation')
        if len(_json(record).encode('utf-8')) > MAX_RECEIPT_BYTES:raise ValueError('size')
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise ValueError('Malformed or oversized verification receipt.') from None
    return record


def make_receipt(run_id: str, node_id: str, original: str, value: str, policy_digest: str,
                 report: VerificationReport, review_id: str | None = None) -> dict:
    # Revalidate mutable report instances; no assumed prior validation.
    result = VerificationReport.model_validate(report.model_dump(mode='json')).model_dump(mode='json')
    if result['verdict'] == 'passed':
        from .extraction_checks import _read, _walk, at_pointer, MISSING
        try:
            parsed = _read(value)
            covered = [f['field'] for f in result['fields']]
            # A passed field must hold a value; an absent optional field must hold none.
            for f in result['fields']:
                if (at_pointer(parsed, f['field']) in (MISSING, None)) != (f['status'] == 'absent'):raise ValueError('missing checked field')
            if any(not any(p == c or p.startswith(c+'/') for c in covered) for p in _walk(parsed)):
                raise ValueError('omitted coverage')
        except (ValueError, TypeError, RecursionError, UnicodeError):
            raise ValueError('Passed verification receipt must cover the exact value.') from None
    record = {'v': 1, 'run_id': run_id, 'node_id': node_id, 'input_digest': digest(original),
        'value_digest': digest(value), 'policy_digest': policy_digest, 'report': result,
        'report_digest': digest(_json(result)), 'review_id': review_id}
    while len(_json(record).encode('utf-8')) > MAX_RECEIPT_BYTES:
        remaining = [f for f in result['fields'] if f['field'] != '']
        if not remaining:raise ValueError('Verification receipt scope exceeds its size limit.')
        result = {'verdict': 'needs_review', 'complete': False, 'fields': remaining[:-1] + [
            {'field': '', 'status': 'unverified', 'reason': 'receipt_limit'}]}
        record.update(report=result, report_digest=digest(_json(result)), review_id=None)
    return checked_receipt(record)


def receipt_matches(record: dict, *, run_id: str, original: str, value: str, policy_digest: str,
                    node_id: str | None = None) -> bool:
    try:
        checked_receipt(record)
        same = (record['run_id'] == run_id and (node_id is None or record['node_id'] == node_id)
                and record['input_digest'] == digest(original)
                and record['value_digest'] == digest(value) and record['policy_digest'] == policy_digest)
        report = record['report']
        return same and ((report['verdict'] == 'passed' and report['complete'])
                        or record['review_id'] is not None and report['verdict'] == 'needs_review')
    except (ValueError, TypeError, UnicodeError):return False
