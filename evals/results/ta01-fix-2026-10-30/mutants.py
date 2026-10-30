"""Run each TA01 mutant in its own fresh scratch copy; the repository is never edited."""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path
ROOT = Path(sys.argv[1]); OUT = Path(sys.argv[2]); PY = ROOT / '.venv/bin/python'
TESTS = ['backend/tests/test_providers.py', 'backend/tests/test_token_budget.py',
         'backend/tests/test_request_reader.py', 'backend/tests/test_request_router.py']
OLLAMA_REC = "            record_usage(usage, body.get('prompt_eval_count'), body.get('eval_count'))\n"
def move_after(s, line, anchor):
    assert s.count(line) == 1 and s.count(anchor) == 1
    s = s.replace(line, '', 1)
    return s.replace(anchor, anchor + line, 1)
def adapter(name, start, anchor):
    def apply(s):
        i = s.index(start); j = s.index('record_usage(', i); k = s.index('\n', j) + 1
        line = s[s.rindex('\n', 0, j) + 1:k]
        return move_after(s, line, anchor)
    return apply
MUTANTS = {
  'M1 OpenAI record_usage after content checks': ('backend/app/providers.py', adapter('openai', "body = _json_object(response, 'OpenAI')",
      "                raise ProviderResponseError('OpenAI returned no text.')\n")),
  'M2 Ollama record_usage after message checks': ('backend/app/providers.py', lambda s: move_after(s, OLLAMA_REC,
      "                raise ProviderResponseError('Ollama returned no text. Choose a chat-capable model.')\n")),
  'M3 Claude record_usage after content checks': ('backend/app/providers.py', adapter('claude', "body = _json_object(response, 'Claude')",
      "                raise ProviderResponseError('Claude returned no text.')\n")),
  'M4 no accounting in the except Exception branch': ('backend/app/registry.py', lambda s: s.replace(
      "        if usage:account_usage(ctx,usage,config,error_type=type(exc).__name__);await record_spend(ctx)\n", "        pass\n", 1)),
  'M5 account twice in the except Exception branch': ('backend/app/registry.py', lambda s: s.replace(
      "        if usage:account_usage(ctx,usage,config,error_type=type(exc).__name__);await record_spend(ctx)\n",
      "        if usage:account_usage(ctx,usage,config,error_type=type(exc).__name__);account_usage(ctx,usage,config,error_type=type(exc).__name__);await record_spend(ctx)\n", 1)),
  'M6 account twice on the success path': ('backend/app/registry.py', lambda s: s.replace(
      "    account_usage(ctx,usage,config);await record_spend(ctx);return text\n",
      "    account_usage(ctx,usage,config);account_usage(ctx,usage,config);await record_spend(ctx);return text\n", 1)),
  'M7 persist in the cancellation branch': ('backend/app/registry.py', lambda s: s.replace(
      "        account_usage(ctx,usage,config,error_type='cancelled');raise\n",
      "        account_usage(ctx,usage,config,error_type='cancelled');await record_spend(ctx);raise\n", 1)),
}
results = {}
for name, (path, apply) in MUTANTS.items():
    work = Path(tempfile.mkdtemp(prefix='ta01-mutant-'))
    for part in ('backend', 'examples'):
        shutil.copytree(ROOT / part, work / part, ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    (work / 'frontend/tests/browser').mkdir(parents=True)
    shutil.copy(ROOT / 'frontend/tests/browser/request-router-node.json', work / 'frontend/tests/browser/')
    for extra in ('pytest.ini', 'pyproject.toml', 'setup.cfg', 'conftest.py'):
        if (ROOT / extra).exists(): shutil.copy(ROOT / extra, work / extra)
    target = work / path; before = target.read_text(); after = apply(before)
    assert after != before, f'{name}: mutation did not apply'
    target.write_text(after)
    run = subprocess.run([str(PY), '-m', 'pytest', *TESTS, '-q', '-p', 'no:warnings', '-p', 'no:cacheprovider', '--tb=no'],
                         cwd=work, capture_output=True, text=True)
    failed = sorted(l.split(' ')[1].split('::', 1)[1] for l in run.stdout.splitlines() if l.startswith('FAILED'))
    results[name] = {'killed': run.returncode != 0 and bool(failed), 'failed_tests': failed, 'summary': run.stdout.strip().splitlines()[-1]}
    print(name, '->', 'KILLED' if results[name]['killed'] else 'SURVIVED', len(failed), results[name]['summary'])
    shutil.rmtree(work)
OUT.write_text(json.dumps(results, indent=2) + '\n')
