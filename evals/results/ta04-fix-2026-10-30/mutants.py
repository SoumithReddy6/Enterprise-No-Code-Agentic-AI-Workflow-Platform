"""Run each TA04 mutant in its own fresh scratch copy; the repository is never edited.

Usage: python -I mutants.py <repo root> <out.json> <scratch dir>
"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(sys.argv[1]); OUT = Path(sys.argv[2]); SCRATCH = Path(sys.argv[3]); PY = ROOT / '.venv/bin/python'
NEW = 'backend/tests/test_token_budget_recovery.py'
TESTS = [NEW, 'backend/tests/test_budget_recovery.py', 'backend/tests/test_run_causes.py',
         'backend/tests/test_token_budget.py', 'backend/tests/test_loop_completeness.py', 'backend/tests/test_iteration.py']
POLICY, REGISTRY, AGENT, LOOP = ('backend/app/execution_policy.py', 'backend/app/registry.py',
                                 'backend/app/agent_runtime.py', 'backend/app/iteration.py')


def replace(old, new):
    def apply(s):
        assert s.count(old) == 1, old[:80]
        return s.replace(old, new, 1)
    return apply


def chain(*steps):
    def apply(s):
        for step in steps: s = step(s)
        return s
    return apply


def drop_policy_catch(s):
    start = s.index('        except token_budget.TokenBudgetExhausted as exc:\n')
    end = s.index('        except Exception as exc:\n', start)
    return s[:start] + s[end:]


STRUCTURED_CHECK = '            if out_of_tokens and structured:raise token_budget.TokenBudgetExhausted(reason)\n'
B_AT_STEP = '            if out_of_tokens:return await budget_abstention(ctx,reason,evidence,truncations,provider)\n'
BOUNDARY = """        try:final,sources,grounding=await finalize_grounded_answer(final,evidence,repair,config.output_schema if structured else None)
        except token_budget.TokenBudgetExhausted as exc:
            # The last tokens went on an answer that needs repair. Same rule as at a step.
            if structured:raise
            truncations.append({'node_id':node_id,'reason':str(exc)})
            if not evidence:return exhausted_abstention(depth,str(exc),truncations)
            return await budget_abstention(ctx,str(exc),evidence,truncations,provider)
"""

# name: (file, mutation, tests the plan says must catch it)
MUTANTS = {
  'M1 policy does not catch TokenBudgetExhausted': (POLICY, drop_policy_catch, ['T1', 'T2', 'T5']),
  'M2 llm_node raises a plain ValueError': (REGISTRY, replace(
      'if tokens_exhausted(ctx.run):raise TokenBudgetExhausted(token_reason(ctx.run))',
      'if tokens_exhausted(ctx.run):raise ValueError(token_reason(ctx.run))'), ['T1', 'T2', 'T11']),
  'M3 no re-raise in finalize_grounded_answer': (AGENT, replace(
      '    except token_budget.TokenBudgetExhausted:raise\n    except ValueError as exc:raise GroundedContractError',
      '    except ValueError as exc:raise GroundedContractError'), ['T8', 'T9b', 'T9c']),
  'M4 no re-raise in the structured-only repair': (AGENT, replace(
      "        except token_budget.TokenBudgetExhausted:raise\n        except ValueError as exc:raise ValueError(f'Agent output",
      "        except ValueError as exc:raise ValueError(f'Agent output"), ['T9']),
  'M5 a token refusal never counts its attempt': (POLICY, replace(
      'executed=debited or _model_calls(run)>calls', 'executed=False'), ['T7', 'T8']),
  'M6 a token refusal always counts its attempt': (POLICY, replace(
      'executed=debited or _model_calls(run)>calls', 'executed=True'), ['T1']),
  'M7 agent with evidence calls the model again at the step': (AGENT, replace(B_AT_STEP, ''), ['T10']),
  'M8 agent boundary does not catch a refused repair': (AGENT, replace(BOUNDARY,
      '        final,sources,grounding=await finalize_grounded_answer(final,evidence,repair,config.output_schema if structured else None)\n'),
      ['T9c']),
  'M9 budget_abstention uses the no-evidence text': (AGENT, replace(
      "'text':BUDGET_STOPPED+", "'text':NO_EVIDENCE+"), ['T9c', 'T10']),
  'M10 replay ignores budget_halt': (LOOP, replace(
      '            entry=budget.admit(saved)\n            halt=budget_halt(entry,index)\n',
      '            entry=budget.admit(saved)\n'), ['T7b']),
  'M11 compact drops budget_halt': (LOOP, replace(
      "    if entry.get('budget_halt') in HALT_REASONS:short['budget_halt']=entry['budget_halt']\n", ''), ['T7c']),
  'M12 budget_abstention skips the source check': (AGENT, replace(
      "    sources=[{**p,'cited':False} for p in evidence]\n    if ctx.platform:await ctx.platform('verify_vector_sources',[{k:v for k,v in s.items() if k not in ('citation','cited')} for s in sources])\n",
      "    sources=[{**p,'cited':False} for p in evidence]\n"), ['T14']),
  'M13 B also applied to structured agents': (AGENT, chain(replace(STRUCTURED_CHECK, ''),
      replace('            if structured:raise\n', '')), ['T9b', 'T10b']),
  'M14 summary reason comes from the item error text': (LOOP, replace(
      "return ('budget',index+1,HALT_REASONS[kind]) if kind in HALT_REASONS else None",
      "return ('budget',index+1,entry.get('error')) if kind in HALT_REASONS else None"), ['T7c']),
  'M15 structured check after the no-evidence branch': (AGENT, chain(replace(STRUCTURED_CHECK, ''),
      replace(B_AT_STEP, STRUCTURED_CHECK + B_AT_STEP)), ['T10b']),
  'M16 refused repair without evidence returns the budget abstention': (AGENT, replace(
      '            if not evidence:return exhausted_abstention(depth,str(exc),truncations)\n', ''), ['T9d']),
}


def label(test):
    """'test_t10b_...[x]' -> 'T10b'."""
    return 'T' + test.split('::')[-1].split('_')[1][1:] if test.split('::')[-1].startswith('test_t') else test


results = {}
for name, (path, apply, expected) in MUTANTS.items():
    work = Path(tempfile.mkdtemp(prefix='ta04-mutant-', dir=SCRATCH))
    for part in ('backend', 'examples'):
        shutil.copytree(ROOT / part, work / part, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    (work / 'frontend/tests/browser').mkdir(parents=True)
    shutil.copy(ROOT / 'frontend/tests/browser/request-router-node.json', work / 'frontend/tests/browser/')
    target = work / path; before = target.read_text(); after = apply(before)
    assert after != before, f'{name}: mutation did not apply'
    target.write_text(after)
    run = subprocess.run([str(PY), '-m', 'pytest', *TESTS, '-q', '-p', 'no:warnings', '-p', 'no:cacheprovider',
                          '-p', 'no:logging', '--tb=no'], cwd=work, capture_output=True, text=True)
    failed = sorted(l.split(' ')[1] for l in run.stdout.splitlines() if l.startswith('FAILED') or l.startswith('ERROR'))
    caught = sorted({label(t) for t in failed if t.startswith(NEW)})
    others = sorted({t for t in failed if not t.startswith(NEW)})
    missing = sorted(set(expected) - set(caught))
    results[name] = {'killed': run.returncode != 0 and bool(failed), 'expected_killers': expected,
                     'expected_missing': missing, 'new_tests_failed': caught, 'other_tests_failed': others,
                     'failed_tests': failed, 'summary': run.stdout.strip().splitlines()[-1]}
    print(name, '->', 'KILLED' if results[name]['killed'] else 'SURVIVED', 'by', caught,
          'missing', missing, 'others', len(others), '|', results[name]['summary'])
    shutil.rmtree(work)
OUT.write_text(json.dumps(results, indent=2) + '\n')
