"""Whether a run's cumulative token accounting can be trusted, and whether it is saved.

A run created under the accounting contract starts from an explicit zero snapshot, and
every commit that publishes reusable work carries the snapshot it was produced under.
Older runs carry no such guarantee: their saved results stay usable, but no further model
request is started on their behalf.

Deliberately free of storage and approval imports: both of those modules use it.
"""
from copy import deepcopy

CONTRACT=1
FIELDS=('action_budget','tokens_spent','usage','usage_by_invocation')

LEGACY_MODEL=('Past token usage cannot be verified for this older run. No further model request was started. '
              'Saved results were kept.')
LEGACY_APPROVED_WRITE=('Past token usage cannot be verified for this older run. The unstarted approved action was '
                       'not sent by this attempt. Saved results were kept.')
PENDING=('Run accounting could not be saved; no further model request was started. Saved results were kept. '
         'Resume after storage recovers.')
INVALID=('Saved token accounting is missing or malformed. No further model request was started. '
         'Saved results were kept.')
STALE='The accounting write was refused because it would lower tokens_spent.'
PAUSE='Current accounting is unavailable; the approval pause could not be confirmed. Saved results were kept.'
MISSING='This result was not saved because its token accounting was not supplied.'


class AccountingUnavailable(ValueError):
    """Known spend cannot be shown to be durable, so no further model request may start.

    Control flow, not a node failure: retry and on_error recovery must not turn it into
    another provider call. It is not TokenBudgetExhausted, because the budget is not known
    to be spent. It stays a ValueError so every message path shows its text.
    """


def zero_snapshot():
    return {'action_budget':{},'tokens_spent':0,'usage':{},'usage_by_invocation':{}}


def checked_snapshot(value):
    """A detached copy of a well-formed snapshot, or AccountingUnavailable. Never repaired."""
    if not isinstance(value,dict) or any(key not in value for key in FIELDS):raise AccountingUnavailable(INVALID)
    total=value['tokens_spent']
    if type(total) is not int or total<0:raise AccountingUnavailable(INVALID)
    if any(not isinstance(value[key],dict) for key in ('action_budget','usage','usage_by_invocation')):
        raise AccountingUnavailable(INVALID)
    return deepcopy(value)


def restored_tokens(accounting):
    """Tokens spent by earlier attempts. Older snapshots predate tokens_spent, so fall back
    to summing their usage totals rather than restarting the ceiling at zero."""
    value=(accounting or {}).get('tokens_spent')
    if type(value) is int and value>=0:return value
    from .token_budget import tokens_spent
    return tokens_spent({'usage':(accounting or {}).get('usage') or {}})


def accounting_trusted(run):
    """True for a run under the contract with a well-formed snapshot; False for an older run.

    A marked run whose snapshot is missing or malformed raises: it is never treated as a
    legacy run, and never given an inferred zero baseline.
    """
    marker=(run or {}).get('accounting_contract')
    if marker is None:return False
    if type(marker) is not int or marker!=CONTRACT:raise AccountingUnavailable(INVALID)
    checked_snapshot(run.get('accounting'))
    return True


class AccountingDurability:
    """Which version of this attempt's known spend has an acknowledged durable copy.

    In memory only; not a call identity or a ledger. Every accumulated provider usage makes
    a new generation. Only a confirmed commit of a snapshot captured at a generation
    acknowledges it, and acknowledging an older generation never clears a newer one.
    """
    def __init__(self,trusted):
        self.trusted=trusted;self.generation=0;self.acknowledged=0

    @property
    def pending(self):return self.acknowledged<self.generation

    def changed(self):self.generation+=1

    def capture(self,snapshot):return self.generation,deepcopy(snapshot)

    def acknowledge(self,generation):self.acknowledged=max(self.acknowledged,generation)


async def ensure_accounting_ready(run):
    """Before a new provider request: the run's known spend is trusted and durably saved.

    Pending spend is saved through the strict persistence callback, which charges no
    action. A false or missing acknowledgment, or an ordinary storage failure, refuses the
    request. A lost lease and cancellation keep their own control flow.
    """
    durability=(run or {}).get('durability')
    if durability is None:return  # Compiler-only execution keeps no durable accounting.
    if not durability.trusted:raise AccountingUnavailable(LEGACY_MODEL)
    if not durability.pending:return
    persist=run.get('persist_accounting')
    if persist is None:raise AccountingUnavailable(PENDING)
    from .execution_policy import CONTROL_FLOW,accounting_snapshot
    generation,snapshot=durability.capture(accounting_snapshot(run))
    try:saved=await persist(snapshot)
    except CONTROL_FLOW:raise
    except Exception:raise AccountingUnavailable(PENDING) from None
    if saved is not True:raise AccountingUnavailable(PENDING)
    durability.acknowledge(generation)
