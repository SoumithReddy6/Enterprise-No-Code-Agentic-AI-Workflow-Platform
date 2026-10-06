"""JSON that keeps numbers exactly as written.

Python's json module turns every non-integer number into a binary float, so
1000.00000000000001 becomes 1000.0 and a comparison against 1000 flips. Relay parses
model and user JSON with Decimal instead, and writes it back without passing through
float. NaN and Infinity are not JSON, so they are refused rather than carried forward.
"""
import json
from decimal import Decimal


class NonFiniteNumber(ValueError):
    pass


def _refuse(constant):
    raise NonFiniteNumber(f'{constant} is not a JSON number')


def loads(text):
    return json.loads(text, parse_float=Decimal, parse_constant=_refuse)


def dumps(value):
    """json.dumps(value, ensure_ascii=False), writing Decimals as their exact digits."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(k, ensure_ascii=False)}: {dumps(v)}' for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(dumps(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)
