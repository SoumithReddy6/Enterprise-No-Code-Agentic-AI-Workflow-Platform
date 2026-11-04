"""Where each value came from: the label an automatic decision is judged by.

Labels are assigned by deterministic code, never by a model:

* source      trigger messages, explicit record-read adapters and retrieved passages.
              Arbitrary HTTP/Python results and write responses are not independent.
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
import hashlib
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from . import exact_json

SOURCE, QUOTED, CALCULATED, GUESSED, ABSENT = 'source', 'quoted', 'calculated', 'guessed', 'absent'
LABELS = (SOURCE, QUOTED, CALCULATED, GUESSED, ABSENT)
TRUSTED = frozenset({SOURCE, QUOTED, CALCULATED})
PROVENANCE_KEY = '_provenance'
MAX_FIELDS = 64
MAX_BYTES = 4096
MAX_DEPTH = 32
MAX_LEAVES = 4096
_RANK = {GUESSED: 0, QUOTED: 1, CALCULATED: 1, SOURCE: 2}

# ------------------------------------------------------------------ the quote grammars

# A run of digits, separators and points, optionally signed. Each run is then parsed
# strictly; one that is not a well-formed number (1,35 or 2.0.1) quotes nothing.
_NUMBER_RUN = re.compile(r'(?<![\w.,+-])([-+]?\d[\w,.+-]*)')
_GROUPED = re.compile(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?')
_WORDS = {'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
          'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
          'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19, 'twenty': 20,
          'dozen': 12}
# Not joined to another word by a hyphen: "twenty-one" is 21, which this table cannot say.
_WORD = re.compile(r'(?<![\w-])(' + '|'.join(_WORDS) + r')(?![\w-])', re.IGNORECASE)


def numbers_in(text):
    """Every number written in text, as exact decimals."""
    found = set()
    # Retain the established date-token grammar: dates contain their three written
    # numbers, but a date is not proof of any number's business role.
    dates = re.compile(r'\b(\d{4})-(\d{2})-(\d{2})\b')
    for parts in dates.findall(text): found.update(Decimal(p) for p in parts)
    text = dates.sub(' ', text)
    for digits in _NUMBER_RUN.findall(text):
        digits = digits.rstrip(',.')  # Sentence/clause punctuation is not a decimal separator.
        if not _GROUPED.fullmatch(digits):
            continue
        try:
            value = exact_json.loads(digits.replace(',', '').lstrip('+'))
            if isinstance(value, (Decimal, int)):
                found.add(Decimal(value))
        except (ValueError, InvalidOperation):
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


class IncompleteTraversal(ValueError):
    pass


def _leaves(value, key=None, depth=0, visits=None):
    visits = [0] if visits is None else visits
    visits[0] += 1
    if visits[0] > MAX_LEAVES:
        raise IncompleteTraversal('too many provenance values')
    if depth > MAX_DEPTH:
        raise IncompleteTraversal('provenance nesting limit')
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, k, depth + 1, visits)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v, key, depth + 1, visits)
    else:
        yield key, value


@dataclass(frozen=True)
class ReferenceEvidence:
    """Ephemeral authority supplied by a tenant's record-read adapter, never model data.

    This describes a resolved input binding, not a persisted authorization grant.
    The runtime must construct it after authorizing that read in the current tenant.
    """
    node_id: str
    port: str
    tenant_id: str
    text: str


@dataclass(frozen=True)
class EvidenceContext:
    """Only this invocation's eligible resolved data, never templates or run memory.

    Structured source receipts contain node_id, port, sha256 and value. They are ephemeral
    input context, not another full copy persisted in checkpoints.
    """
    eligible_texts: tuple[str, ...] = ()
    structured_sources: tuple[dict, ...] = ()
    complete: bool = True
    tenant_id: str | None = None
    reference_sources: tuple[ReferenceEvidence, ...] = ()


def reference_context(node, port, text, *, tenant_id):
    """Only explicit record reads can furnish independent reference data.

    Triggers are original requests, not independent catalogues; arbitrary HTTP/Python
    or model text can echo an invention. None of those qualifies here.
    """
    if not (isinstance(tenant_id, str) and 1 <= len(tenant_id) <= 64
            and isinstance(port, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', port)
            and isinstance(text, str)):
        raise ValueError('Reference context needs a tenant, public port and resolved text.')
    independent = node.type not in SOURCE_NODES and independent_source(node)
    if independent:
        from .registry import REGISTRY
        if port not in REGISTRY[node.type].outputs:raise ValueError('Reference port is not a declared record output.')
    records = (ReferenceEvidence(node.id, port, tenant_id, text),) if independent else ()
    return EvidenceContext(tenant_id=tenant_id, reference_sources=records)


def source_context(node_id, port, value):
    text = value if isinstance(value, str) else exact_json.dumps(value)
    try:
        parsed = exact_json.loads(text)
    except (ValueError, RecursionError):
        return EvidenceContext((text,))
    return EvidenceContext((text,), ({'node_id': node_id, 'port': port,
        'sha256': hashlib.sha256(text.encode()).hexdigest(), 'value': parsed},))


class Evidence:
    """The trusted text a model's output values are checked against."""

    def __init__(self, texts):
        context = texts if isinstance(texts, EvidenceContext) else EvidenceContext(tuple(texts))
        texts = context.eligible_texts
        self.complete = context.complete
        self.texts = [t for t in texts if isinstance(t, str) and t]
        self.numbers = set().union(*(numbers_in(t) for t in self.texts)) if self.texts else set()
        self.booleans = set()
        for text in self.texts:
            try:
                parsed = exact_json.loads(text)
            except (ValueError, RecursionError):
                continue
            try:
                leaves = list(_leaves(parsed))
            except IncompleteTraversal:
                self.complete = False
                continue
            self.booleans.update((k, v) for k, v in leaves if isinstance(v, bool))
            self.numbers.update(Decimal(v) for _, v in leaves if isinstance(v, (int, Decimal)) and not isinstance(v, bool))

    def label(self, value, key=None):
        if value is None:
            return ABSENT
        if not self.complete:
            return GUESSED
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
            try:
                return weakest(self.label(v, k) for k, v in _leaves(value, key))
            except IncompleteTraversal:
                return GUESSED
        return GUESSED


def weakest(labels):
    """The least trusted label of several; absent only if every one is absent."""
    present = [label for label in labels if label != ABSENT]
    if not present:
        return ABSENT
    return min(present, key=lambda label: _RANK.get(label, 0))


def pointer(parts):
    return ''.join('/' + str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def field_labels(value, evidence):
    """Labels for an object's fields by JSON Pointer: nested objects are walked, and a list
    is labelled as a whole. At most MAX_FIELDS paths; any beyond are reported as truncated
    and count as guessed."""
    labels, truncated = {}, False
    def walk(obj, prefix, depth):
        nonlocal truncated
        for key, item in obj.items():
            parts = (*prefix, key)
            path = pointer(parts)
            if isinstance(item, dict) and item and depth < MAX_DEPTH:
                walk(item, parts, depth + 1)
            elif len(labels) < MAX_FIELDS and len(path) <= 128 and depth <= MAX_DEPTH:
                labels[path] = evidence.label(item, key)
            else:
                truncated = True
    walk(value, (), 0)
    # Leave headroom for port labels, truncation and the bounded decision receipt.
    while len(json.dumps(labels, ensure_ascii=False).encode()) > MAX_BYTES - 768:
        labels.popitem(); truncated = True
    return labels, truncated


# ------------------------------------------------------------------ labels per node

SOURCE_NODES = {'chat_input', 'manual_input'}
READ_OPERATIONS = {'tool_jira': {'search', 'get_issue'}, 'tool_confluence': {'search', 'get_page'},
                   'tool_github': {'get_issue', 'list_issues'}}
MODEL_NODES = {'agent', 'llm', 'query'}
DETERMINISTIC_NODES = {'prompt', 'response', 'condition'}


def independent_source(node):
    # Only adapters with an explicit record-read operation establish independence.
    # Arbitrary Python/HTTP results and write responses may echo a guessed request.
    return node.type in SOURCE_NODES or (node.config or {}).get('operation',
        'list_issues' if node.type == 'tool_github' else 'search') in READ_OPERATIONS.get(node.type, set())


def evidence_context(node, inputs, state_values):
    contexts = []
    for name, ref in node.inputs.items():
        source, _, port = ref.partition('.')
        record = (state_values.get(source) or {}).get(PROVENANCE_KEY) or {}
        if name not in inputs:
            continue
        if port in record.get('passage_ports', []):
            # The question, citation labels, page numbers and filenames are wrappers,
            # not independent passage facts. Only the returned text is eligible.
            try:
                parsed = exact_json.loads(inputs[name])
                passages = parsed.get('passages', []) if isinstance(parsed, dict) else parsed
                if not isinstance(passages, list): continue
                for passage in passages:
                    if isinstance(passage, dict) and isinstance(passage.get('text'), str):
                        contexts.append(source_context(source, port, passage['text']))
            except (ValueError, TypeError, RecursionError):
                continue
            continue
        if port_label(state_values, ref) not in TRUSTED:
            continue
        # A template's literals and a model's metadata are not data evidence. The
        # explicit eligible port list also prevents old opaque calculated text laundering.
        if port not in record.get('eligible_ports', []):
            continue
        contexts.append(source_context(source, port, inputs[name]))
    return EvidenceContext(tuple(t for c in contexts for t in c.eligible_texts),
                           tuple(s for c in contexts for s in c.structured_sources),
                           all(c.complete for c in contexts))


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
        wanted = pointer(field.split('.')) if record['v'] == 2 else field
        # v1 dotted paths were ambiguous. Such old records cannot establish the
        # identity of a nested value when a literal dotted key exists in that object.
        if record['v'] == 1:
            try:
                value = exact_json.loads(state_values[source][port])
                if ambiguous_keys(value):
                    return GUESSED, 'unrecorded_field'
            except (ValueError, TypeError, RecursionError):
                return GUESSED, 'unrecorded_field'
        path = wanted if wanted in fields else next((p for p in fields if wanted.startswith(p + ('/' if record['v'] == 2 else '.'))), None)
        if path is None:
            return GUESSED, 'unrecorded_field' if record.get('truncated') else 'missing_field'
        label = fields[path]
    else:
        label = record.get('ports', {}).get(port, GUESSED)
    return label, ('guessed' if label == GUESSED else None)


def ambiguous_keys(value, depth=0, visits=None):
    """Old dotted identities cannot be trusted if ANY object key contains a dot."""
    visits = [0] if visits is None else visits
    visits[0] += 1
    if depth > MAX_DEPTH or visits[0] > MAX_LEAVES:
        raise IncompleteTraversal('cannot verify legacy field identity')
    if isinstance(value, dict):
        return any('.' in key or ambiguous_keys(v, depth + 1, visits) for key, v in value.items())
    if isinstance(value, list):
        return any(ambiguous_keys(v, depth + 1, visits) for v in value)
    return False


def for_node(node, definition, inputs, outputs, state_values, body_type=None, origins=None, input_label=None):
    """The _provenance record for a node's successful outputs."""
    ports = [p for p in definition.outputs]
    input_labels = [input_label] if input_label is not None else [port_label(state_values, ref) for ref in node.inputs.values()]
    derived = CALCULATED if all(label in TRUSTED for label in input_labels) else GUESSED
    record = {'v': 2}
    if independent_source(node):
        record['ports'] = {p: SOURCE for p in ports}
        record['eligible_ports'] = ports
    elif node.type == 'retrieve':
        echoed = input_label if input_label is not None else port_label(state_values, node.inputs['query']) if 'query' in node.inputs else GUESSED
        record['ports'] = {p: (echoed if p == 'query' else CALCULATED if p == 'context' and echoed in TRUSTED
                              else GUESSED if p == 'context' else SOURCE) for p in ports}
        record['v'] = 2
        record['fields'] = {'context': {'/question': echoed, '/passages': SOURCE}}
        record['passage_ports'] = [p for p in ('context', 'sources') if p in ports]
        eligible = origins if origins is not None else evidence_context(node, inputs, state_values)
        if eligible.complete and inputs.get('query') in eligible.eligible_texts:
            record['eligible_ports'] = ['query']
    elif node.type == 'verify_extraction':
        # Verification grants eligibility separately; echo the extraction's origins.
        source,_,port=node.inputs['value'].partition('.')
        previous=(state_values.get(source) or {}).get(PROVENANCE_KEY) or {}
        record['ports']={p:(port_label(state_values,node.inputs['value']) if p=='text' else CALCULATED) for p in ports}
        if port in previous.get('fields',{}):record['fields']={'text':previous['fields'][port]}
    elif node.type == 'switch':
        # The route is computed in code from a value that runtime eligibility admitted.
        record['ports'] = {p: CALCULATED for p in ports}
    elif node.type == 'request_reader':
        # The values are the model's reading, labelled field by field against the original
        # message only. The receipt, not this label, grants eligibility downstream.
        reply = GUESSED if outputs.get('reading') and outputs.get('reply') else CALCULATED
        record['ports'] = {p: (reply if p == 'reply' else GUESSED if p in ('text', 'reading') else CALCULATED) for p in ports}
        try:
            parsed = exact_json.loads(outputs.get('text') or '{}')
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, dict) and parsed:
            message = inputs.get('message')
            labels, truncated = field_labels(parsed, Evidence(EvidenceContext((message,)) if isinstance(message, str) else EvidenceContext(complete=False)))
            record['fields'] = {'text': labels}
            if truncated:
                record['truncated'] = True
    elif node.type == 'request_router':
        # Admission remains a model judgment, including its branch and explanation.
        # Nothing here can become evidence for extraction or a business decision.
        record['ports'] = {p: GUESSED for p in ports}
    elif node.type in MODEL_NODES:
        # Model text is a guess; the other ports are Relay's own records of the call.
        record['ports'] = {p: (GUESSED if p in ('text', 'grounding') else SOURCE) for p in ports}
        if node.type == 'agent' and structured(node):
            try:
                parsed = exact_json.loads(outputs.get('text', ''))
            except (ValueError, RecursionError):
                parsed = None
            if isinstance(parsed, dict):
                labels, truncated = field_labels(parsed, Evidence(origins if origins is not None else evidence_context(node, inputs, state_values)))
                record['v'] = 2
                record['fields'] = {'text': labels}
                if truncated:
                    record['truncated'] = True
    elif node.type == 'for_each':
        results = weakest(item_label(item) for item in outputs.get('results', []))
        record['ports'] = {p: (results if p == 'results' else CALCULATED) for p in ports}
    elif node.type == 'calculate':
        if '_decision_review' in outputs:label=GUESSED
        else:label, _ = calculation_label(node, inputs, state_values)
        record['ports'] = {'text': label}
        record['v'] = 2
        record['fields'] = {'text': {'/result': label}}
        if label in TRUSTED: record['eligible_ports'] = ['text']
    elif node.type in DETERMINISTIC_NODES:
        record['ports'] = {p: (SOURCE if p == 'sources' else derived) for p in ports}
    else:
        record['ports'] = {p: GUESSED for p in ports}
    return record


def output_label(outputs, port='text'):
    record = outputs.get(PROVENANCE_KEY) or {}
    fields = record.get('fields', {}).get(port)
    if fields is not None:
        return GUESSED if record.get('truncated') else weakest(fields.values())
    return record.get('ports', {}).get(port, GUESSED)


def item_label(item):
    if item.get('status') != 'success' or item.get('value_dropped') or item.get('details_omitted'):
        return GUESSED
    return output_label(item)


def restored_record(node, record):
    """Keep current receipts; pre-fix derived labels cannot justify a NEW decision.

    v1 lacked the corrected quote grammar, field identity and origin policy. Public
    values and completed decision receipts remain unchanged; only consumption of
    old derived labels becomes conservative. Triggers and explicit record reads
    still establish source data independently of those old checks.
    """
    checked(record)
    if record['v'] == 2 or independent_source(node):
        return record
    conservative={**record, 'ports': {port: GUESSED for port in record['ports']},
        **({'fields': {port: {path: GUESSED for path in fields}
                      for port, fields in record['fields'].items()}} if 'fields' in record else {}),
        }
    conservative.pop('eligible_ports',None)
    conservative.pop('passage_ports',None)
    # "guessed" is longer than old labels such as "source". Downgrading a valid
    # near-cap record must not turn observation into a replay failure.
    while len(json.dumps(conservative,ensure_ascii=False).encode()) > MAX_BYTES:
        fields=next((paths for paths in conservative.get('fields',{}).values() if paths),None)
        if fields is not None:
            fields.popitem();conservative['truncated']=True
        else:
            conservative['ports']={};conservative.pop('fields',None)
    return checked(conservative)


def checked(record):
    """Validate a _provenance record from a node or a checkpoint; raise ValueError."""
    if not isinstance(record, dict) or type(record.get('v')) is not int or record['v'] not in (1, 2) or not set(record) <= {'v', 'ports', 'fields', 'truncated', 'eligible_ports', 'passage_ports', 'decision'}:
        raise ValueError('malformed provenance record')
    ports = record.get('ports')
    if not isinstance(ports, dict) or not all(isinstance(k, str) and isinstance(v, str) and v in LABELS for k, v in ports.items()):
        raise ValueError('malformed provenance ports')
    fields = record.get('fields', {})
    if not isinstance(fields, dict) or not all(
            isinstance(port, str) and isinstance(paths, dict) and len(paths) <= MAX_FIELDS
            and all(isinstance(p, str) and len(p) <= 128 and isinstance(v, str) and v in LABELS
                    and (record['v'] == 1 or p.startswith('/')) for p, v in paths.items())
            for port, paths in fields.items()):
        raise ValueError('malformed provenance fields')
    if 'truncated' in record and record['truncated'] is not True:
        raise ValueError('malformed provenance truncation flag')
    if 'eligible_ports' in record and not (isinstance(record['eligible_ports'], list) and
            len(record['eligible_ports']) <= len(ports) and all(isinstance(p, str) and ports.get(p) in TRUSTED for p in record['eligible_ports'])):
        raise ValueError('malformed provenance eligible ports')
    if 'passage_ports' in record and not (isinstance(record['passage_ports'], list) and
            len(record['passage_ports']) <= 2 and all(isinstance(p, str) and p in ('context', 'sources') and p in ports for p in record['passage_ports'])):
        raise ValueError('malformed provenance passage ports')
    if 'decision' in record: checked_decision(record['decision'])
    if len(json.dumps(record, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError('provenance record exceeds its size limit')
    return record


def checked_decision(decision):
    if not (isinstance(decision, dict) and set(decision) <= {'label', 'would_review', 'passed', 'reason','manual','review_id'}
            and isinstance(decision.get('label'), str) and decision['label'] in LABELS
            and type(decision.get('would_review')) is bool
            and ('passed' not in decision or type(decision['passed']) is bool)
            and ('manual' not in decision or decision['manual'] is True)
            and (('manual' in decision)==('review_id' in decision))
            and ('review_id' not in decision or isinstance(decision['review_id'],str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',decision['review_id']))
            and ('reason' not in decision or decision['reason'] in ('guessed', 'unlabelled', 'unrecorded_field', 'missing_field', 'check_failed'))):
        raise ValueError('malformed provenance decision')
    return decision


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
    if independent_source(node):
        return STATIC_TRUSTED
    if node.type == 'retrieve':
        query = static_label(nodes, node.inputs['query'], '', seen) if 'query' in node.inputs else STATIC_GUESSED
        if port == 'query' or port == 'context' and field == 'question': return query
        if port == 'context' and not field: return query
        return STATIC_TRUSTED
    if node.type == 'request_reader':
        return STATIC_RUNTIME if port == 'text' and field else STATIC_GUESSED if port in ('text', 'reading', 'reply') else STATIC_TRUSTED
    if node.type in MODEL_NODES:
        if port not in ('text', 'grounding'):
            return STATIC_TRUSTED
        return STATIC_RUNTIME if port == 'text' and field and node.type == 'agent' and structured(node) else STATIC_GUESSED
    if node.type == 'calculate':
        from .calculate import CalculationError, parse, dependencies
        try:
            used, conditional = dependencies(parse((node.config or {}).get('expression', '')))
        except CalculationError:
            return STATIC_GUESSED
        value = node.inputs.get('value')
        label = _static_weakest(static_label(nodes, value, path, seen) for path in used) if value else STATIC_GUESSED
        # A compute expression can return null for a missing operand. That outcome
        # is absent, so "always guessed / every run reviews" would be false.
        nullable = (node.config or {}).get('mode', 'compute') == 'compute' and bool(used)
        return STATIC_RUNTIME if (conditional or nullable) and label == STATIC_GUESSED else label
    if node.type == 'for_each':
        if port != 'results':
            return STATIC_TRUSTED
        body = nodes.get((node.config or {}).get('body', ''))
        if body is None: return STATIC_GUESSED
        return STATIC_GUESSED if body.type in MODEL_NODES and not structured(body) else STATIC_RUNTIME
    if node.type in DETERMINISTIC_NODES:
        return STATIC_TRUSTED if port == 'sources' else _static_weakest(inputs())
    return STATIC_GUESSED


def decision_warnings(workflow):
    """Located warnings distinguish certain guesses from nullable calculations."""
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
            elif static_label(nodes, ref, field) == STATIC_RUNTIME:
                source = nodes.get(ref.partition('.')[0])
                if source and source.type == 'calculate':
                    from .calculate import CalculationError, parse, dependencies
                    try:
                        used, _ = dependencies(parse(source.config.get('expression', '')))
                        raw = source.inputs.get('value')
                        if raw and any(static_label(nodes, raw, path) == STATIC_GUESSED for path in used):
                            warnings.append(f'{node.id}: decides on {reading}, which may use unconfirmed model values when present. '
                                            'Use fields of a structured Agent output to check them against the input.')
                    except CalculationError:
                        pass  # The config validator reports invalid expressions.
        elif node.type == 'calculate' and config.get('mode') == 'check':
            if static_label(nodes, f'{node.id}.text', 'result') == STATIC_GUESSED:
                warnings.append(f'{node.id}: checks values that are always a model\'s guess, so the check cannot confirm them. '
                                'When enforcement is on, every run will pause here for a person. Use fields of a structured Agent output.')
    return warnings
