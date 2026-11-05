"""TA07 mutants M1-M9: each applied to a fresh copy of the isolation base; its named oracle must fail.

A failure from an import, collection or fixture error is not a kill: the oracle tests must
fail by assertion, and the module must collect all 10 cases.
Usage: python mutants.py <file holding the isolation path> <python> <output file>
"""
import pathlib, re, shutil, subprocess, sys, tempfile
BASE = pathlib.Path(open(sys.argv[1]).read().strip()); PY = sys.argv[2]; OUT = sys.argv[3]
NEW = 'backend/tests/test_cache_input_tokens.py'
PR, TB, OB = 'backend/app/providers.py', 'backend/app/token_budget.py', 'backend/app/observability.py'
TOTAL = "    return (sum(known) if known else None), {k: v for k, v in cache.items() if _count(v)}, complete"
COMPLETE = "    complete = _count(base) and all(value is None or _count(value) for value in cache.values())"
OPENAI = "            record_usage(usage, usage_data.get('prompt_tokens'), usage_data.get('completion_tokens'), cache)"
OLLAMA = "            record_usage(usage, body.get('prompt_eval_count'), body.get('eval_count'),"
M = {
    # The Claude total is input_tokens only (the original finding).
    'M1': ([(PR, TOTAL, TOTAL.replace("(sum(known) if known else None)", "(base if _count(base) else None)"))],
           ['test_c01_', 'test_c03_']),
    # The breakdown is added to a total that already includes it (OpenAI and Ollama).
    'M2': ([(PR, OPENAI, OPENAI.replace("usage_data.get('prompt_tokens'), usage_data", "usage_data.get('prompt_tokens') + sum(cache.values()), usage_data")),
            (PR, OLLAMA, OLLAMA.replace("body.get('prompt_eval_count'), body.get('eval_count')", "body.get('prompt_eval_count') + (body.get('prompt_eval_cached_count') or 0), body.get('eval_count')"))],
           ['test_c04_', 'test_c06_']),
    # An absent or null Claude cache field is treated as invalid.
    'M3': ([(PR, COMPLETE, "    complete = _count(base) and all(_count(value) for value in cache.values())")],
           ['test_c02_claude_without_cache_tokens_is_unchanged[absent]', 'test_c02_claude_without_cache_tokens_is_unchanged[null]']),
    # An invalid Claude cache field is treated as 0, and the call stays reported.
    'M4': ([(PR, COMPLETE, "    complete = _count(base)")], ['test_c03_']),
    # The subset check is removed.
    'M5': ([(PR, "    return parts if sum(parts.values()) <= total else {}", "    return parts")], ['test_c05_']),
    # tokens_spent sums the breakdown too.
    'M6': ([(TB, "        for field in ('prompt_tokens','completion_tokens'):", "        for field in ('prompt_tokens','completion_tokens','cache_read_tokens','cache_write_tokens'):")],
           ['test_c01_', 'test_c04_']),
    # The TA06 journal status check is reverted.
    'M7': ([(OB, "    if not (type(data.get('usage_status')) is str and data['usage_status'] in USAGE_STATUSES):data.pop('usage_status',None)",
                 "    if data.get('usage_status',None) is not None and data['usage_status'] not in USAGE_STATUSES:data.pop('usage_status')")],
           ['test_c07_']),
    # Absent Claude defaults fabricate prompt_tokens 0 (TA07-D01 counterexample).
    'M8': ([(PR, TOTAL, TOTAL.replace("(sum(known) if known else None)", "sum(known)"))], ['test_c08_']),
    # A missing OpenAI write detail drops the read (TA07-D02 counterexample).
    'M9': ([(PR, "    if not _count(total) or not all(value is None or _count(value) for value in parts.values()):",
                 "    if not _count(total) or not all(_count(value) for value in parts.values()):")],
           ['test_c04_openai_cache_details_are_parts_of_the_total[read-only]']),
}
BAD = re.compile(r'ImportError|ModuleNotFoundError|SyntaxError|NameError|errors? during collection|fixture .* not found')
lines = []; ok = True
for name, (edits, planned) in M.items():
    work = pathlib.Path(tempfile.mkdtemp(prefix=f'ta07-{name}-', dir=BASE.parent))
    shutil.copytree(BASE, work / 'repo', ignore=shutil.ignore_patterns('.git')); repo = work / 'repo'
    for f, a, b in edits:
        p = repo / f; s = p.read_text(); assert s.count(a) == 1, (name, f, a[:60]); p.write_text(s.replace(a, b))
    r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-p', 'no:randomly', '-rf', NEW], cwd=repo,
                       capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
    failed = sorted(set(re.findall(r'^FAILED \S+::(\S+?)(?: - |\s*$)', r.stdout, re.M)))
    summary = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    counts = {k: int(v) for v, k in re.findall(r'(\d+) (passed|failed)', summary)}
    collected = sum(counts.values()) == 10 and 'error' not in summary
    killed = {t: [f for f in failed if f.startswith(t)] for t in planned}
    clean = not BAD.search(r.stdout) and collected
    good = all(killed.values()) and clean; ok &= good
    lines.append(f"{name}: {'KILLED' if good else 'SURVIVED'} by planned tests {planned} | {summary}"
                 f"{'' if clean else ' | REJECTED: import/collection/fixture error'}\n  failed: {', '.join(failed)}")
    shutil.rmtree(work)
open(OUT, 'w').write('\n'.join(lines) + f"\n\nALL PLANNED KILLS: {ok}\n"); print(open(OUT).read())
