"""The Calculate node: deterministic arithmetic and checks over a JSON object's fields.

Models read values; code computes with them. A model asked for a total multiplies
decimals unreliably (measured: 7 x $142.86 came back as 999.72), so a workflow extracts
the parts and a Calculate node combines them exactly. Its result is `calculated` when
every value it actually used was trusted (see provenance.py).

The expression language is small and parsed here by hand; nothing reaches eval.

    expression := or
    or         := and ('or' and)*
    and        := not ('and' not)*
    not        := 'not' not | comparison
    comparison := sum (('=='|'!='|'<'|'<='|'>'|'>=') sum)?
    sum        := term (('+'|'-') term)*
    term       := unary (('*'|'/') unary)*
    unary      := '-' unary | primary
    primary    := number | path | function '(' arguments ')' | '(' expression ')'
    function   := min | max | abs | round | coalesce

Numbers are exact decimals: +, - and * never round, and a result that would need more than
50 significant digits is an error rather than an approximation. Field names are letters,
digits and underscores; a hyphen always means subtraction. `null` propagates through arithmetic and functions, so a
missing part yields a null result rather than a guess; `coalesce` picks the first non-null
argument. Comparisons and logic need actual values: null there is a named error. Division
is rounded to 50 significant digits, and `round(x, places)` rounds half up, as money is.
"""
import re
from dataclasses import dataclass
from decimal import Decimal, Inexact, InvalidOperation, Overflow, ROUND_HALF_UP, localcontext

from . import exact_json

MAX_LENGTH = 500
MAX_DEPTH = 20
MAX_TOKENS = 200
FUNCTIONS = {'min': (2, 8), 'max': (2, 8), 'abs': (1, 1), 'round': (2, 2), 'coalesce': (2, 8)}
KEYWORDS = {'and', 'or', 'not'}
COMPARISONS = ('==', '!=', '<=', '>=', '<', '>')
_TOKEN = re.compile(r'\s*(?:(?P<number>\d+(?:\.\d+)?)|(?P<path>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)'
                    r'|(?P<op>==|!=|<=|>=|[-+*/(),<>]))')


class CalculationError(ValueError):
    pass


@dataclass(frozen=True)
class Node:
    kind: str           # number, path, unary, binary, compare, and, or, not, call
    value: object = None
    args: tuple = ()


def tokens(text):
    position, found = 0, []
    while position < len(text):
        if text[position:].strip() == '':
            break
        match = _TOKEN.match(text, position)
        if not match or match.end() == position:
            raise CalculationError(f'unexpected character {text[position:].strip()[:1]!r} at position {position + 1}')
        kind = match.lastgroup
        found.append((kind, match.group(kind)))
        position = match.end()
        if len(found) > MAX_TOKENS:
            raise CalculationError(f'the expression has more than {MAX_TOKENS} parts')
    return found


class _Parser:
    def __init__(self, text):
        self.items, self.at, self.depth = tokens(text), 0, 0

    def peek(self):
        return self.items[self.at] if self.at < len(self.items) else (None, None)

    def take(self, value=None):
        kind, token = self.peek()
        if token is None or (value is not None and token != value):
            raise CalculationError(f'expected {value!r}' if value else 'the expression ends too early')
        self.at += 1
        return kind, token

    def nested(self, parse):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise CalculationError(f'the expression nests more than {MAX_DEPTH} levels')
        try:
            return parse()
        finally:
            self.depth -= 1

    def expression(self):
        return self.nested(self.disjunction)

    def disjunction(self):
        node = self.conjunction()
        while self.peek() == ('path', 'or'):
            self.take(); node = Node('or', args=(node, self.conjunction()))
        return node

    def conjunction(self):
        node = self.negation()
        while self.peek() == ('path', 'and'):
            self.take(); node = Node('and', args=(node, self.negation()))
        return node

    def negation(self):
        if self.peek() == ('path', 'not'):
            self.take()
            return Node('not', args=(self.nested(self.negation),))
        return self.comparison()

    def comparison(self):
        node = self.sum()
        if self.peek()[1] in COMPARISONS:
            _, op = self.take()
            node = Node('compare', op, (node, self.sum()))
            if self.peek()[1] in COMPARISONS:
                raise CalculationError('chain comparisons with and, for example a < b and b < c')
        return node

    def sum(self):
        node = self.term()
        while self.peek()[1] in ('+', '-'):
            _, op = self.take(); node = Node('binary', op, (node, self.term()))
        return node

    def term(self):
        node = self.unary()
        while self.peek()[1] in ('*', '/'):
            _, op = self.take(); node = Node('binary', op, (node, self.unary()))
        return node

    def unary(self):
        if self.peek()[1] == '-':
            self.take()
            return Node('unary', '-', (self.nested(self.unary),))
        return self.primary()

    def primary(self):
        kind, token = self.take()
        if kind == 'number':
            return Node('number', Decimal(token))
        if token == '(':
            node = self.expression(); self.take(')')
            return node
        if kind == 'path':
            if token in KEYWORDS:
                raise CalculationError(f'{token!r} needs a value on each side')
            if token in FUNCTIONS and self.peek()[1] == '(':
                self.take('(')
                args = [self.expression()]
                while self.peek()[1] == ',':
                    self.take(); args.append(self.expression())
                self.take(')')
                low, high = FUNCTIONS[token]
                if not low <= len(args) <= high:
                    raise CalculationError(f'{token} takes {low if low == high else f"{low} to {high}"} arguments')
                if token == 'round' and args[1].kind != 'number':
                    raise CalculationError('round needs a whole number of places, for example round(x, 2)')
                return Node('call', token, tuple(args))
            return Node('path', token)
        raise CalculationError(f'unexpected {token!r}')


def parse(text):
    if not isinstance(text, str) or not text.strip():
        raise CalculationError('the expression is empty')
    if len(text) > MAX_LENGTH:
        raise CalculationError(f'the expression is longer than {MAX_LENGTH} characters')
    parser = _Parser(text)
    node = parser.expression()
    if parser.at != len(parser.items):
        raise CalculationError(f'unexpected {parser.peek()[1]!r} after a complete expression')
    return node


def is_check(node):
    """Whether the expression yields true/false rather than a number."""
    return node.kind in ('compare', 'and', 'or', 'not')


def paths(node):
    """Every field path the expression can read, in order."""
    found = [node.value] if node.kind == 'path' else []
    for arg in node.args:
        found += [p for p in paths(arg) if p not in found]
    return found


def _field(value, path):
    current = value
    for key in path.split('.'):
        if not isinstance(current, dict) or key not in current:
            raise CalculationError(f'field {path} is missing')
        current = current[key]
    return current


def evaluate(node, value, used=None):
    """The expression's result on a parsed JSON object. Fields actually read are appended
    to `used`, so a result's trust can follow the branch coalesce took."""
    used = [] if used is None else used
    with localcontext() as context:
        context.prec = 50
        context.Emax, context.Emin = exact_json.MAX_EXPONENT * 3, -exact_json.MAX_EXPONENT * 3
        try:
            result = _evaluate(node, value, used)
        except Overflow:
            raise CalculationError('the result is outside the supported number range') from None
        except InvalidOperation:
            raise CalculationError('the calculation is not defined for these values') from None
    if isinstance(result, Decimal) and not result.is_zero() and not (
            exact_json.SMALLEST <= result.copy_abs() <= exact_json.LARGEST):
        raise CalculationError('the result is outside the supported number range')
    return result


def _number(value, what):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        kind = ('true/false' if isinstance(value, bool) else 'text' if isinstance(value, str)
                else 'a list' if isinstance(value, list) else 'an object' if isinstance(value, dict) else 'not a number')
        raise CalculationError(f'{what} is {kind}, not a number')
    return Decimal(value)


def _what(node, otherwise):
    return f'field {node.value}' if node.kind == 'path' else otherwise


def _evaluate(node, value, used):
    kind = node.kind
    if kind == 'number':
        return node.value
    if kind == 'path':
        used.append(node.value)
        found = _field(value, node.value)
        if isinstance(found, bool):
            return found
        return _number(found, f'field {node.value}')
    if kind == 'unary':
        operand = _number(_evaluate(node.args[0], value, used), _what(node.args[0], 'the operand of -'))
        return None if operand is None else -operand
    if kind == 'binary':
        left = _number(_evaluate(node.args[0], value, used), _what(node.args[0], f'the left side of {node.value}'))
        right = _number(_evaluate(node.args[1], value, used), _what(node.args[1], f'the right side of {node.value}'))
        if left is None or right is None:
            return None
        if node.value == '/':
            if right.is_zero():
                raise CalculationError('division by zero')
            return left / right
        with localcontext() as exact:
            exact.traps[Inexact] = True
            try:
                return {'+': left + right, '-': left - right, '*': left * right}[node.value]
            except Inexact:
                raise CalculationError('the exact result needs more than 50 significant digits') from None
    if kind == 'call':
        name = node.value
        if name == 'coalesce':
            for arg in node.args:
                candidate = _evaluate(arg, value, used)
                if candidate is not None:
                    return _number(candidate, 'a coalesce argument')
            return None
        args = [_number(_evaluate(arg, value, used), _what(arg, f'an argument of {name}')) for arg in node.args]
        if any(arg is None for arg in args):
            return None
        if name == 'abs':
            return abs(args[0])
        if name == 'round':
            places = args[1]
            if places != places.to_integral_value() or not 0 <= places <= 20:
                raise CalculationError('round needs 0 to 20 places')
            return args[0].quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
        return (min if name == 'min' else max)(args)
    if kind == 'compare':
        left, right = (_evaluate(arg, value, used) for arg in node.args)
        if left is None or right is None:
            raise CalculationError(f'cannot compare with a missing value ({node.value})')
        if isinstance(left, bool) != isinstance(right, bool):
            raise CalculationError(f'cannot compare true/false with a number ({node.value})')
        if isinstance(left, bool) and node.value not in ('==', '!='):
            raise CalculationError(f'true/false values can only be compared with == or !=')
        return {'==': left == right, '!=': left != right, '<': left < right, '<=': left <= right,
                '>': left > right, '>=': left >= right}[node.value]
    if kind in ('and', 'or', 'not'):
        def truth(arg):
            result = _evaluate(arg, value, used)
            if not isinstance(result, bool):
                raise CalculationError(f'{kind} needs true/false values, not a number or a missing value')
            return result
        if kind == 'not':
            return not truth(node.args[0])
        left = truth(node.args[0])
        if kind == 'and' and not left:
            return False
        if kind == 'or' and left:
            return True
        return truth(node.args[1])
    raise CalculationError(f'unsupported expression part {kind}')


def run(expression, text, mode):
    """(result, used fields) for the node: text is the bound JSON object."""
    node = parse(expression)
    if is_check(node) != (mode == 'check'):
        raise CalculationError('a check needs a comparison, such as total == quantity * unit_price' if mode == 'check'
                               else 'compute needs a number, not a comparison; use check mode for true/false')
    try:
        value = exact_json.loads(text)
    except exact_json.UnsupportedNumber as exc:
        raise CalculationError(f'the input has {exc}') from None
    except ValueError:
        raise CalculationError('the input is not JSON, so it has no fields') from None
    if not isinstance(value, dict):
        raise CalculationError('the input must be a JSON object')
    used = []
    return evaluate(node, value, used), used


from typing import Literal
from pydantic import Field, model_validator
from .models import StrictModel


class CalculateConfig(StrictModel):
    expression: str = Field(min_length=1, max_length=MAX_LENGTH, title='Expression',
                            description='Fields of the bound JSON object with + - * / ( ), min, max, abs, round(x, places) and coalesce. '
                                        'Check mode compares: == != < <= > >=, and, or, not.')
    mode: Literal['compute', 'check'] = Field(default='compute', title='Mode',
                                              description='compute returns a number; check returns whether the comparison holds.')

    @model_validator(mode='after')
    def parses(self):
        try:
            node = parse(self.expression)
        except CalculationError as exc:
            raise ValueError(f'expression: {exc}.') from None
        if is_check(node) != (self.mode == 'check'):
            raise ValueError('check mode needs a comparison, such as total == quantity * unit_price.' if self.mode == 'check'
                             else 'compute mode needs a number, not a comparison; use check mode for true/false.')
        return self


async def calculate_node(inputs, config, ctx):
    result, _ = run(config.expression, inputs['value'], config.mode)
    return {'text': exact_json.dumps({'result': result})}
