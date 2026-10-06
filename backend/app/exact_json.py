"""JSON that keeps numbers exactly as written.

Python's json module turns every non-integer number into a binary float, so
1000.00000000000001 becomes 1000.0 and a comparison against 1000 flips. Relay parses
model and user JSON with Decimal instead, and writes it back without passing through
float. NaN and Infinity are not JSON, so they are refused rather than carried forward.
"""
import json
import math
from decimal import Decimal


class NonFiniteNumber(ValueError):
    pass


def _refuse(constant):
    raise NonFiniteNumber(f'{constant} is not a JSON number')


def loads(text):
    return json.loads(text, parse_float=Decimal, parse_constant=_refuse)


# Integers beyond this many digits are refused rather than expanded: an exponent such as
# 1e999999999 would otherwise become a billion-digit integer.
MAX_INTEGER_DIGITS = 300


def plain(value):
    """value with every Decimal replaced by an int or float carrying exactly the same number,
    for code that serializes through the standard json module, such as an HTTP request body.

    A number a binary float cannot represent exactly raises instead of being sent rounded:
    the receiver must get the value the workflow decided on, or nothing.
    """
    if isinstance(value, Decimal):
        if value.adjusted() < MAX_INTEGER_DIGITS and value == value.to_integral_value():
            return int(value)
        number = float(value)
        if math.isfinite(number) and Decimal(repr(number)) == value:
            return number
        raise ValueError(f'{value} cannot be sent as a JSON number without rounding; send it as a string')
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def dumps(value):
    """json.dumps(value, ensure_ascii=False), writing Decimals as their exact digits."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return '{' + ', '.join(f'{json.dumps(k, ensure_ascii=False)}: {dumps(v)}' for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(dumps(v) for v in value) + ']'
    return json.dumps(value, ensure_ascii=False)
