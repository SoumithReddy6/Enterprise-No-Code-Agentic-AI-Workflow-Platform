"""Run-wide token ceiling. Enforcement uses provider-reported counts, never an estimated price.

Tokens are the only figure a provider states exactly, so the hard limit is denominated in
tokens; monetary conversion stays a reporting concern with its own stale-price caveats.
"""
import os

DEFAULT_RUN_TOKENS=2_000_000
MAX_RUN_TOKENS=100_000_000


def run_token_limit():
    """Tokens one run may consume across every model call. 0 disables the ceiling."""
    raw=os.getenv('RELAY_RUN_TOKEN_LIMIT','').strip()
    if not raw:return DEFAULT_RUN_TOKENS
    try:value=int(raw)
    except ValueError:raise ValueError(f'RELAY_RUN_TOKEN_LIMIT must be an integer between 0 and {MAX_RUN_TOKENS}') from None
    if not 0<=value<=MAX_RUN_TOKENS:raise ValueError(f'RELAY_RUN_TOKEN_LIMIT must be an integer between 0 and {MAX_RUN_TOKENS}')
    return value


def tokens_spent(run):
    """Provider-reported tokens accumulated so far this run. Missing usage counts as zero."""
    if not isinstance(run,dict):return 0
    total=0
    for node in (run.get('usage') or {}).values():
        if not isinstance(node,dict):continue
        for field in ('prompt_tokens','completion_tokens'):
            value=node.get(field)
            if type(value) is int and value>0:total+=value
    return total


def exhausted(run,limit=None):
    """True once the run has reached its ceiling. An unlimited or absent run never exhausts."""
    if run is None:return False
    limit=run_token_limit() if limit is None else limit
    return bool(limit) and tokens_spent(run)>=limit


def reason(run,limit=None):
    limit=run_token_limit() if limit is None else limit
    return (f'Run token budget exhausted ({tokens_spent(run):,} of {limit:,} provider-reported '
            'tokens; raise RELAY_RUN_TOKEN_LIMIT). Token counts are not a monetary cost.')
