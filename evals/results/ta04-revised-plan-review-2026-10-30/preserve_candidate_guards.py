"""Demonstrate the two recommended Agent guards in the disposable candidate only.

For this demonstration, strict structured Agents retain a typed budget failure;
no arbitrary schema-compatible business object is fabricated. Free-text Agents
use B after source revalidation. This is a proposed contract choice, not app code.
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
assert str(root).startswith('/private/tmp/relay-ta04-audit-')
assert not (root / '.git').exists()
p = root / 'backend/app/agent_runtime.py'
text = p.read_text()
old = 'def budget_abstention(reason, evidence, truncations):\n'
assert text.count(old) == 1
text = text.replace(old, '''async def budget_abstention(reason, evidence, truncations, ctx, structured):
    if structured:raise TokenBudgetExhausted(reason)
    if ctx.platform and evidence:
        await ctx.platform('verify_vector_sources',
            [{k:v for k,v in s.items() if k not in ('citation','cited')} for s in evidence])
''', 1)
old = 'return budget_abstention(reason,evidence,truncations)'
assert text.count(old) == 2
text = text.replace(old, 'return await budget_abstention(reason,evidence,truncations,ctx,structured)')
p.write_text(text)
print('Preserved source validation and refused schema-incompatible fallback in', root)
