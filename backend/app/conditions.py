"""Typed condition operators: the comparison a condition node uses to choose its branch.

A legacy condition - {contains, case_sensitive} with no operator - is the contains operator
and behaves exactly as before. Every other operator states what it compares: ordering
operators compare numbers; eq, ne and in compare text unless compare_as is number; empty
accepts any value. A value or operand that cannot be compared that way is an error naming
the problem, never a silent false branch.

Numbers are compared as exact decimals, so 0.1 + 0.2 style float error and integers beyond
2**53 cannot flip a branch. Text comparison ignores surrounding whitespace, which model
output often carries, and is case-insensitive unless case_sensitive is set.
"""
import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Literal
from pydantic import Field, model_validator
from .models import StrictModel
from . import exact_json

ORDERING = ('gt', 'gte', 'lt', 'lte')
NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')
MAX_NUMBER_TEXT = 100
# Object keys only: a list in the path is an error rather than an implicit index.
FIELD = re.compile(r'[A-Za-z0-9_-]{1,64}(?:\.[A-Za-z0-9_-]{1,64}){0,7}')


class ConditionConfig(StrictModel):
    operator: Literal['contains', 'eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'empty'] = Field(
        default='contains', title='Operator',
        description='contains: text includes a phrase. eq / ne: equal or not equal. gt, gte, lt, lte: numeric comparison. in: equals one of several options. empty: nothing there.')
    field: str = Field(default='', max_length=520, title='Field',
                       description='Optional. Read this key, or a dotted path such as order.amount, from a JSON object value - for example the output of an extraction agent.')
    contains: str | None = Field(default=None, min_length=1, max_length=1000, title='Contains')
    compare_to: str | None = Field(default=None, min_length=1, max_length=1000, title='Compare to')
    options: list[str] | None = Field(default=None, min_length=1, max_length=100, title='Options',
                                      description='A JSON list, for example ["high", "urgent"].')
    compare_as: Literal['text', 'number'] = Field(default='text', title='Compare as',
                                                  description='For eq, ne and in. Ordering operators always compare numbers.')
    case_sensitive: bool = False

    @model_validator(mode='after')
    def operands_match_operator(self):
        used = {'contains': 'contains', 'in': 'options', 'empty': None}.get(self.operator, 'compare_to')
        if used and getattr(self, used) is None:
            raise ValueError(f'The {self.operator} operator needs {used}.')
        for name in ('contains', 'compare_to', 'options'):
            if name != used and getattr(self, name) is not None:
                raise ValueError(f'{name} is not used by the {self.operator} operator; remove it.')
        if self.field and not FIELD.fullmatch(self.field):
            raise ValueError('field must be an object key or a dotted path of keys (letters, digits, _ and -), such as order.amount.')
        if self.compare_as == 'number' and self.operator in ('contains', 'empty'):
            raise ValueError(f'compare_as number does not apply to the {self.operator} operator.')
        numeric = self.operator in ORDERING or self.compare_as == 'number'
        if numeric and self.compare_to is not None and parse_number(self.compare_to) is None:
            raise ValueError(f'{self.operator} compares numbers, but compare_to {preview(self.compare_to)} is not a number.')
        if self.options is not None:
            if any(len(option) > 1000 for option in self.options):
                raise ValueError('Each option must be at most 1000 characters.')
            if numeric:
                invalid = [option for option in self.options if parse_number(option) is None]
                if invalid:
                    raise ValueError(f'in compares numbers, but option {preview(invalid[0])} is not a number.')
        return self


def preview(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return repr(text if len(text) <= 40 else text[:40] + '...')


def parse_number(text):
    text = text.strip()
    if len(text) > MAX_NUMBER_TEXT or not NUMBER.fullmatch(text):
        return None
    try:
        return Decimal(text)
    except InvalidOperation:  # An exponent beyond what Decimal can represent.
        return None


def describe(value):
    if value is None: return 'null'
    if isinstance(value, bool): return 'a boolean'
    if isinstance(value, (int, float)): return 'a number'
    if isinstance(value, str): return 'text'
    if isinstance(value, list): return 'a list'
    if isinstance(value, dict): return 'an object'
    return type(value).__name__


def read_field(value, path):
    if not path:
        return value
    if isinstance(value, str):
        try:
            value = exact_json.loads(value)
        except exact_json.NonFiniteNumber as exc:
            raise ValueError(f'Condition field {path}: {exc}.') from None
        except ValueError:
            raise ValueError(f'Condition field {path}: the value is not JSON, so it has no fields.') from None
    walked = []
    for key in path.split('.'):
        if not isinstance(value, dict):
            raise ValueError(f"Condition field {path}: {'.'.join(walked) or 'the value'} is {describe(value)}, not an object.")
        if key not in value:
            raise ValueError(f'Condition field {path}: {key!r} is missing.')
        value = value[key]
        walked.append(key)
    return value


def as_number(value, what):
    if isinstance(value, bool):
        raise ValueError(f'{what} is a boolean, not a number.')
    if isinstance(value, Decimal):
        return value  # Parsed from JSON text: already exact and finite.
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f'{what} is not a finite number.')
        return Decimal(repr(value))
    if isinstance(value, str):
        number = parse_number(value)
        if number is None:
            raise ValueError(f'{what} {preview(value)} is not a number.')
        return number
    raise ValueError(f'{what} is {describe(value)}, not a number.')


def as_text(value, what):
    if isinstance(value, str): return value
    if isinstance(value, bool): return 'true' if value else 'false'
    if isinstance(value, Decimal): return str(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool): return json.dumps(value)
    hint = '; use the empty operator, or choose a field' if isinstance(value, (list, dict)) else '; use the empty operator to test for it'
    raise ValueError(f'{what} is {describe(value)}, not text{hint}.')


def is_empty(value):
    if value is None: return True
    if isinstance(value, str): return not value.strip()
    if isinstance(value, (list, dict)): return not value
    return False


def evaluate(value, config: ConditionConfig) -> bool:
    what = f'Condition field {config.field}' if config.field else 'Condition value'
    value = read_field(value, config.field)
    if config.operator == 'empty':
        return is_empty(value)
    if config.operator == 'contains':
        # For text this is the original condition, unchanged: casefolded substring match.
        text, needle = as_text(value, what), config.contains
        if not config.case_sensitive:
            text, needle = text.casefold(), needle.casefold()
        return needle in text
    if config.operator in ORDERING or config.compare_as == 'number':
        number = as_number(value, what)
        if config.operator == 'in':
            return any(number == parse_number(option) for option in config.options)
        target = parse_number(config.compare_to)
        return {'eq': number == target, 'ne': number != target, 'gt': number > target,
                'gte': number >= target, 'lt': number < target, 'lte': number <= target}[config.operator]
    fold = (lambda text: text.strip()) if config.case_sensitive else (lambda text: text.strip().casefold())
    text = fold(as_text(value, what))
    if config.operator == 'in':
        return any(text == fold(option) for option in config.options)
    return (text == fold(config.compare_to)) == (config.operator == 'eq')
