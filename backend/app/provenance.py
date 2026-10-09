"""Where each value came from: the label an automatic decision is judged by.

Labels are assigned by deterministic code, never by a model:

* source      data that entered the workflow from outside a model: trigger messages,
              tool and HTTP results, retrieved passages.
* quoted      a model's structured output value found written in the model's trusted
              input, by the grammars below.
* calculated  produced by deterministic code from trusted values only.
* guessed     everything else: free model text, values that could not be found, anything
              computed from a guessed value, and unlabelled values from older checkpoints.
* absent      null, an honest "not found". It is not a claim, so it is never guessed.

source, quoted and calculated are trusted. A node's output is as trusted as its least
trusted input. A model's own output text is never a trusted input for another model, so
repeating an invention through a second Agent cannot turn it into a quote. Text an author
wrote into a prompt, system message or example is never searched either.

`quoted` proves a value is written in the input, not that it means what its field name
says. See docs/superpowers/plans/2026-10-08-decision-provenance.md for the rule these
labels serve and its limits.
"""
import json
import re
from decimal import Decimal, InvalidOperation

from . import exact_json

SOURCE, QUOTED, CALCULATED, GUESSED, ABSENT = 'source', 'quoted', 'calculated', 'guessed', 'absent'
LABELS = (SOURCE, QUOTED, CALCULATED, GUESSED, ABSENT)
TRUSTED = frozenset({SOURCE, QUOTED, CALCULATED})
PROVENANCE_KEY = '_provenance'
MAX_FIELDS = 64
MAX_BYTES = 4096
MAX_DEPTH = 32
_RANK = {GUESSED: 0, QUOTED: 1, CALCULATED: 1, SOURCE: 2}

# ------------------------------------------------------------------ the quote grammars

# A run of digits, separators and points, optionally signed. Each run is then parsed
# strictly; one that is not a well-formed number (1,35 or 2.0.1) quotes nothing.
_NUMBER_RUN = re.compile(r'(?<![\w.,])([-+](?=\d))?(\d[\d,]*(?:\.\d+)?)(?![\d,]*\d)(?!\.\d)')
_GROUPED = re.compile(r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?')
_WORDS = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
          'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
          'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
          'dozen': 12}
# Not joined to another word by a hyphen: "twenty-one" is 21, which this table cannot say.
_WORD = re.compile(r'(?<![\w-])(' + '|'.join(_WORDS) + r')(?![\w-])', re.IGNORECASE)


def numbers_in(text):
    """Every number written in text, as exact decimals."""
    found = set()
    for sign, digits in _NUMBER_RUN.findall(text):
        digits = digits.rstrip(',')  # "$300, quantity 4": the comma ends the clause.
        if not _GROUPED.fullmatch(digits):
            continue
        try:
            found.add(Decimal((sign or '') + digits.replace(',', '')))
        except InvalidOperation:
            continue
    found.update(Decimal(_WORDS[word.casefold()]) for word in _WORD.findall(text))
    return found


def _normal(text):
    return ' '.join(text.casefold().split())


def string_quoted(value, text):
    """value appears in text on word boundaries, ignoring case and spacing; 2+ characters."""
    wanted = _normal(value)
    if len(wanted) < 2:
        return False
    return re.search(r'(?<!\w)' + re.escape(wanted) + r'(?!\w)', _normal(text)) is not None


def _leaves(value, key=None, depth=0):
    if depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, k, depth + 1)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v, key, depth + 1)
    else:
        yield key, value


class Evidence:
    """The trusted text a model's output values are checked against."""

    def __init__(self, texts):
        self.texts = [t for t in texts if isinstance(t, str) and t]
        self.numbers = set().union(*(numbers_in(t) for t in self.texts)) if self.texts else set()
        self.booleans = set()
        for text in self.texts:
            try:
                parsed = exact_json.loads(text)
            except (ValueError, RecursionError):
                continue
            self.booleans.update((k, v) for k, v in _leaves(parsed) if isinstance(v, bool))

    def label(self, value, key=None):
        if value is None:
            return ABSENT
        if isinstance(value, bool):
            # Only a structured source quotes a boolean: text such as "yes" never does.
            return QUOTED if (key, value) in self.booleans else GUESSED
        if isinstance(value, (int, Decimal)):
            return QUOTED if Decimal(value) in self.numbers else GUESSED
        if isinstance(value, float):
            return QUOTED if Decimal(repr(value)) in self.numbers else GUESSED
        if isinstance(value, str):
            return QUOTED if any(string_quoted(value, t) for t in self.texts) else GUESSED
        if isinstance(value, (dict, list)):
            return weakest(self.label(v, k) for k, v in _leaves(value, key))
        return GUESSED


def weakest(labels):
    """The least trusted label of several; absent only if every one is absent."""
    present = [label for label in labels if label != ABSENT]
    if not present:
        return ABSENT
    return min(present, key=lambda label: _RANK.get(label, 0))


def field_labels(value, evidence):
    """Labels for an object's fields by dotted path: nested objects are walked, and a list
    is labelled as a whole. At most MAX_FIELDS paths; any beyond are reported as truncated
    and count as guessed."""
    labels, truncated = {}, False
    def walk(obj, prefix, depth):
        nonlocal truncated
        for key, item in obj.items():
            path = f'{prefix}.{key}' if prefix else str(key)
            if isinstance(item, dict) and item and depth < MAX_DEPTH:
                walk(item, path, depth + 1)
            elif len(labels) < MAX_FIELDS and len(path) <= 128:
                labels[path] = evidence.label(item, key)
            else:
                truncated = True
    walk(value, '', 0)
    return labels, truncated


# ------------------------------------------------------------------ labels per node

SOURCE_NODES = {'chat_input', 'manual_input', 'tool_http', 'tool_email', 'tool_jira', 'tool_confluence',
                'tool_github', 'tool_python'}
MODEL_NODES = {'agent', 'llm', 'query'}
DETERMINISTIC_NODES = {'prompt', 'response', 'condition'}


def calculation_label(node, inputs, state_values):
    """(label, result) for a Calculate node: calculated only if every field it actually read
    was trusted. coalesce reads only the branch it takes, so an unused guess does not count."""
    from .calculate import run
    config = node.config or {}
    result, used = run(config['expression'], inputs['value'], config.get('mode', 'compute'))
    if result is None:
        return ABSENT, result
    ref = node.inputs['value']
    labels = [value_label(state_values, ref, path)[0] for path in used]
    # Null fields that a fallback replaced do not weaken it: coalesce(total, 0) is calculated.
    label = weakest(labels) if labels else CALCULATED
    return (CALCULATED if label in TRUSTED or label == ABSENT else GUESSED), result


def structured(node):
    config = node.config or {}
    return bool(config.get('output_schema')) or config.get('role') in ('extraction', 'classification')


def port_label(state_values, ref):
    """The label of an upstream port; an unlabelled one (an older checkpoint) is guessed."""
    source, _, port = ref.partition('.')
    record = (state_values.get(source) or {}).get(PROVENANCE_KEY)
    return (record or {}).get('ports', {}).get(port, GUESSED)


def value_label(state_values, ref, field=''):
    """The label of the value a decision reads, and a reason code when it is guessed: a
    structured field when a field path is given and labelled, otherwise the whole port."""
    source, _, port = ref.partition('.')
    record = (state_values.get(source) or {}).get(PROVENANCE_KEY)
    if not record:
        return GUESSED, 'unlabelled'
    fields = record.get('fields', {}).get(port) if field else None
    if fields is not None:
        # The field itself, or the labelled object or list that contains it.
        path = field if field in fields else next((p for p in fields if field.startswith(p + '.')), None)
        if path is None:
            return GUESSED, 'unrecorded_field' if record.get('truncated') else 'missing_field'
        label = fields[path]
    else:
        label = record.get('ports', {}).get(port, GUESSED)
    return label, ('guessed' if label == GUESSED else None)


def for_node(node, definition, inputs, outputs, state_values, body_type=None):
    """The _provenance record for a node's successful outputs."""
    ports = [p for p in definition.outputs]
    input_labels = [port_label(state_values, ref) for ref in node.inputs.values()]
    derived = CALCULATED if all(label in TRUSTED for label in input_labels) else GUESSED
    record = {'v': 1}
    if node.type in SOURCE_NODES:
        record['ports'] = {p: SOURCE for p in ports}
    elif node.type == 'retrieve':
        echoed = port_label(state_values, node.inputs['query']) if 'query' in node.inputs else GUESSED
        record['ports'] = {p: (echoed if p == 'query' else SOURCE) for p in ports}
    elif node.type in MODEL_NODES:
        # Model text is a guess; the other ports are Relay's own records of the call.
        record['ports'] = {p: (GUESSED if p == 'text' else SOURCE) for p in ports}
        if node.type == 'agent' and structured(node):
            try:
                parsed = exact_json.loads(outputs.get('text', ''))
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, dict):
                trusted = [inputs[name] for name, ref in node.inputs.items()
                           if port_label(state_values, ref) in TRUSTED and isinstance(inputs.get(name), str)]
                labels, truncated = field_labels(parsed, Evidence(trusted))
                record['fields'] = {'text': labels}
                if truncated:
                    record['truncated'] = True
    elif node.type == 'for_each':
        results = SOURCE if body_type in SOURCE_NODES else GUESSED
        record['ports'] = {p: (results if p == 'results' else CALCULATED) for p in ports}
    elif node.type == 'calculate':
        label, _ = calculation_label(node, inputs, state_values)
        record['ports'] = {'text': label}
        record['fields'] = {'text': {'result': label}}
    elif node.type in DETERMINISTIC_NODES:
        record['ports'] = {p: (SOURCE if p == 'sources' else derived) for p in ports}
    else:
        record['ports'] = {p: GUESSED for p in ports}
    return record


def checked(record):
    """Validate a _provenance record from a node or a checkpoint; raise ValueError."""
    if not isinstance(record, dict) or record.get('v') != 1 or not set(record) <= {'v', 'ports', 'fields', 'truncated'}:
        raise ValueError('malformed provenance record')
    ports = record.get('ports')
    if not isinstance(ports, dict) or not all(isinstance(k, str) and v in LABELS for k, v in ports.items()):
        raise ValueError('malformed provenance ports')
    fields = record.get('fields', {})
    if not isinstance(fields, dict) or not all(
            isinstance(port, str) and isinstance(paths, dict) and len(paths) <= MAX_FIELDS
            and all(isinstance(p, str) and len(p) <= 128 and v in LABELS for p, v in paths.items())
            for port, paths in fields.items()):
        raise ValueError('malformed provenance fields')
    if 'truncated' in record and record['truncated'] is not True:
        raise ValueError('malformed provenance truncation flag')
    if len(json.dumps(record, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError('provenance record exceeds its size limit')
    return record


# ------------------------------------------------------------------ before a run

# What a value can be, known from the graph alone: always trusted, decided per run by the
# quote check, or always a guess. Ordered from least to most trusted.
STATIC_GUESSED, STATIC_RUNTIME, STATIC_TRUSTED = 'guessed', 'runtime', 'trusted'
_STATIC_RANK = {STATIC_GUESSED: 0, STATIC_RUNTIME: 1, STATIC_TRUSTED: 2}


def _static_weakest(labels):
    labels = list(labels)
    return min(labels, key=_STATIC_RANK.get) if labels else STATIC_TRUSTED


def static_label(nodes, ref, field='', seen=None):
    """What the value at `ref` (and `field` within it) can be, before any run."""
    seen = set() if seen is None else seen
    source, _, port = ref.partition('.')
    node = nodes.get(source)
    if node is None or ref in seen or len(seen) > len(nodes) * 4:
        return STATIC_GUESSED
    seen = seen | {ref}
    inputs = lambda: [static_label(nodes, r, '', seen) for r in node.inputs.values()]
    if node.type in SOURCE_NODES:
        return STATIC_TRUSTED
    if node.type == 'retrieve':
        return static_label(nodes, node.inputs['query'], '', seen) if port == 'query' and 'query' in node.inputs else STATIC_TRUSTED
    if node.type in MODEL_NODES:
        if port != 'text':
            return STATIC_TRUSTED
        return STATIC_RUNTIME if field and node.type == 'agent' and structured(node) else STATIC_GUESSED
    if node.type == 'calculate':
        from .calculate import CalculationError, parse, paths
        try:
            used = paths(parse((node.config or {}).get('expression', '')))
        except CalculationError:
            return STATIC_GUESSED
        value = node.inputs.get('value')
        return _static_weakest(static_label(nodes, value, path, seen) for path in used) if value else STATIC_GUESSED
    if node.type == 'for_each':
        if port != 'results':
            return STATIC_TRUSTED
        body = nodes.get((node.config or {}).get('body', ''))
        return STATIC_TRUSTED if body is not None and body.type in SOURCE_NODES else STATIC_GUESSED
    if node.type in DETERMINISTIC_NODES:
        return STATIC_TRUSTED if port == 'sources' else _static_weakest(inputs())
    return STATIC_GUESSED


def decision_warnings(workflow):
    """A located warning for each decision whose value is always a model's guess: once
    enforcement is on, every run would pause there for a person."""
    nodes = {n.id: n for n in workflow.nodes}
    warnings = []
    for node in workflow.nodes:
        config = node.config or {}
        ref = node.inputs.get('value')
        if not ref:
            continue
        if node.type == 'condition':
            field = config.get('field', '')
            reading = f"{ref}{' field ' + field if field else ''}"
            if static_label(nodes, ref, field) == STATIC_GUESSED:
                warnings.append(f'{node.id}: decides on {reading}, which is always a model\'s guess: free model text cannot be '
                                'checked against the input. When enforcement is on, every run will pause here for a person. '
                                'Decide on a field of a structured Agent output, or calculate the value from such fields.')
        elif node.type == 'calculate' and config.get('mode') == 'check':
            if static_label(nodes, f'{node.id}.text', 'result') == STATIC_GUESSED:
                warnings.append(f'{node.id}: checks values that are always a model\'s guess, so the check cannot confirm them. '
                                'When enforcement is on, every run will pause here for a person. Use fields of a structured Agent output.')
    return warnings
