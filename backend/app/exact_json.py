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

# Magnitudes from 1e-1000 to 1e1000. Far beyond any amount, measurement or identifier a
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


def _decimal(text):
    try:
        value = Decimal(text)
    except InvalidOperation:  # An exponent beyond what Decimal itself can represent.
        raise _out_of_range(text) from None
    if not value.is_zero() and not -MAX_EXPONENT <= value.adjusted() <= MAX_EXPONENT:
        raise _out_of_range(text)
    return value


def _integer(text):
    if len(text.lstrip('-')) > MAX_EXPONENT + 1:
        raise _out_of_range(text)
    return int(text)


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


def _is_integer(checker, value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, Decimal):
        return value == value.to_integral_value()
    return isinstance(value, float) and value.is_integer()


def validate(value, schema):
    """Validate the exact value against the schema, with the schema's own numbers read
    exactly too, so bounds, const, enum, integer and multipleOf judge the number that is
    returned rather than a rounded copy. Raises jsonschema.ValidationError."""
    import jsonschema
    from jsonschema import validators
    exact_schema = loads(json.dumps(schema))
    base = validators.validator_for(exact_schema)

    def multiple_of(validator, divisor, instance, _):
        if validator.is_type(instance, 'number') and (Fraction(instance) / Fraction(divisor)).denominator != 1:
            yield jsonschema.ValidationError(f'{instance} is not a multiple of {divisor}')

    checker = base.TYPE_CHECKER.redefine('integer', _is_integer)
    exact = validators.extend(base, validators={'multipleOf': multiple_of}, type_checker=checker)
    error = jsonschema.exceptions.best_match(exact(exact_schema).iter_errors(value))
    if error is not None:
        # Messages reach the model's repair prompt and the run error: show numbers as written.
        error.message = re.sub(r"Decimal\('([^']*)'\)", r'\1', error.message)
        raise error
