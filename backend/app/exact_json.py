"""JSON that keeps numbers exactly as written.

Python's json module turns every non-integer number into a binary float, so
1000.00000000000001 becomes 1000.0 and a comparison against 1000 flips. Relay parses
model and user JSON with Decimal instead, validates schemas against those exact values,
and writes them back without passing through float.

Numbers are accepted only within a stated range, so every later comparison, integer check
and multiple test stays exact and cheap. NaN and Infinity are not JSON, and a number
outside the range is refused with a named error rather than an arithmetic exception.
"""
import json
import math
import re
from decimal import Decimal, InvalidOperation
from fractions import Fraction

# Nonzero magnitudes from 1e-1000 to 1e1000 inclusive. Far beyond any amount, measurement or identifier a
# workflow handles, and small enough that exact arithmetic on them is immediate.
MAX_EXPONENT = 1000


class UnsupportedNumber(ValueError):
    """A JSON number Relay refuses to carry: not finite, or outside the supported range."""


class NonFiniteNumber(UnsupportedNumber):
    pass


def _refuse(constant):
    raise NonFiniteNumber(f'{constant} is not a JSON number')


def _out_of_range(text):
    shown = text if len(text) <= 40 else text[:40] + '...'
    return UnsupportedNumber(f'{shown} is outside the supported number range (magnitudes 1e-{MAX_EXPONENT} to 1e{MAX_EXPONENT})')


LARGEST = Decimal(f'1e{MAX_EXPONENT}')
SMALLEST = Decimal(f'1e-{MAX_EXPONENT}')


def _decimal(text):
    try:
        value = Decimal(text)
    except InvalidOperation:  # An exponent beyond what Decimal itself can represent.
        raise _out_of_range(text) from None
    # copy_abs and comparison are exact; abs() would round to the context precision.
    if not value.is_zero() and not SMALLEST <= value.copy_abs() <= LARGEST:
        raise _out_of_range(text)
    return value


def _integer(text):
    # The length check comes first so an enormous token is never converted at all.
    if len(text.lstrip('-')) > MAX_EXPONENT + 1 or abs(number := int(text)) > 10 ** MAX_EXPONENT:
        raise _out_of_range(text)
    return number


def loads(text):
    return json.loads(text, parse_float=_decimal, parse_int=_integer, parse_constant=_refuse)


# Integers beyond this many digits are refused rather than expanded when sending.
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


def _exact_numbers(value):
    """Decimals as int when integral, else Fraction: types every jsonschema validator, in
    every dialect and every resource it resolves, already compares and divides exactly."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else Fraction(value)
    if isinstance(value, dict):
        return {k: _exact_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_exact_numbers(v) for v in value]
    return value


def _readable(match):
    """A Fraction from a decimal literal, written back as its decimal digits."""
    numerator, denominator = int(match[1]), int(match[2])
    places = 0
    # Fractions from decimals have denominators of the form 2^a * 5^b, which divide a power of ten.
    while (10 ** places) % denominator and places <= 2 * MAX_EXPONENT:
        places += 1
    if (10 ** places) % denominator:
        return f'{numerator}/{denominator}'
    return str(Decimal(f'{numerator * (10 ** places // denominator)}E-{places}'))


def validate(value, schema):
    """Validate the exact value against the schema, with the schema's own numbers read
    exactly too, so bounds, const, enum, integer and multipleOf judge the number that is
    returned rather than a rounded copy. Raises jsonschema.ValidationError.

    Exactness lives in the values, not in a custom validator class: jsonschema picks a
    fresh class whenever a referenced resource declares its own $schema, and that class
    must be just as exact."""
    import jsonschema
    from jsonschema import validators
    exact_schema = _exact_numbers(loads(json.dumps(schema)))
    validator = validators.validator_for(exact_schema)(exact_schema)
    error = jsonschema.exceptions.best_match(validator.iter_errors(_exact_numbers(value)))
    if error is not None:
        # Messages reach the model's repair prompt and the run error: show numbers as written.
        message = re.sub(r'Fraction\((-?\d+), (\d+)\)', _readable, error.message)
        error.message = re.sub(r'(?<![\w.\'"/])(-?\d+)/(\d+)(?![\w./])', _readable, message)
        raise error
