"""Build only the proposed Agent changes in a disposable git-archive export.

Not an implementation of TA04; it makes the new early-return paths reviewable.
Refuses any checkout with .git. All replacements require exactly one match.
Run: .venv/bin/python <file> /private/tmp/<archive-export>
"""
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()
assert str(root).startswith('/private/tmp/relay-ta04-audit-')
assert not (root / '.git').exists()


def change(path, old, new):
    p = root / path
    text = p.read_text()
    assert text.count(old) == 1, (path, old[:100], text.count(old))
    p.write_text(text.replace(old, new, 1))


p = root / 'backend/app/token_budget.py'
p.write_text(p.read_text() + '\n\nclass TokenBudgetExhausted(ValueError):\n    pass\n')
change('backend/app/registry.py',
       'if tokens_exhausted(ctx.run):raise ValueError(token_reason(ctx.run))',
       'if tokens_exhausted(ctx.run):raise __import__(__package__+".token_budget",fromlist=["TokenBudgetExhausted"]).TokenBudgetExhausted(token_reason(ctx.run))')
change('backend/app/agent_runtime.py', 'from . import token_budget\n',
       'from . import token_budget\nfrom .token_budget import TokenBudgetExhausted\n')
change('backend/app/agent_runtime.py',
       '    except ValueError as exc:raise GroundedContractError(',
       '    except TokenBudgetExhausted:raise\n    except ValueError as exc:raise GroundedContractError(')
change('backend/app/agent_runtime.py',
       '        except ValueError as exc:raise ValueError(f\'Agent output did not match',
       '        except TokenBudgetExhausted:raise\n        except ValueError as exc:raise ValueError(f\'Agent output did not match')

helper = '''def budget_abstention(reason, evidence, truncations):
    return {
        'text': 'The run token budget ran out before an answer could be written. '
                'The sources collected so far are listed but were not used to answer.'
                +'\\n\\nReason: '+reason,
        'provider': 'none',
        'sources': json.dumps([{**p,'cited':False} for p in evidence]),
        'grounding': json.dumps({'answer':'','citations':[],'abstain':True,
            'reason':reason,'compliance':'budget_exhausted','truncated':True,
            'truncation_reason':reason,'truncations':truncations})}

'''
change('backend/app/agent_runtime.py',
       'from .evidence_registry import remember_evidence,emit_evidence_notice',
       helper + 'from .evidence_registry import remember_evidence,emit_evidence_notice')
change('backend/app/agent_runtime.py',
       '            if not evidence:\n                if depth:raise AgentBudgetExhausted',
       '            if out_of_tokens and evidence:return budget_abstention(reason,evidence,truncations)\n'
       '            if not evidence:\n                if depth:raise AgentBudgetExhausted')

p = root / 'backend/app/agent_runtime.py'
text = p.read_text()
start = text.index('    if grounded:\n        final,sources,grounding=await finalize_grounded_answer')
end = text.index("    if config.role=='memory' and ctx.platform:\n", start)
block = text[start:end]
text = text[:start] + '    try:\n' + ''.join('    '+line for line in block.splitlines(keepends=True)) + '''    except TokenBudgetExhausted as exc:
        reason=str(exc)
        truncations.append({'node_id':node_id,'reason':reason})
        return budget_abstention(reason,evidence,truncations)
''' + text[end:]
p.write_text(text)
print('Prepared Agent-only early-return candidate in', root)
