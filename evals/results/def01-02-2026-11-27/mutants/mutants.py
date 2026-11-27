"""DEF-01/02 mutants. Each mutant is one exact text replacement in product code, applied to a
fresh scratch copy of the fix (git archive of the isolation commit, which is f3771b3). M00
changes nothing and must pass every oracle. An oracle that selects no test stops the run, and
each planned test must be among the results: a kill is real only when that test ran.

Oracle `rw`: backend/tests/test_agent_write_resume.py, test_execution_safety.py,
test_s07_boundaries.py and test_approvals.py (every test's result recorded). Oracle `full`:
the whole backend suite on SQLite, run for M00 (control) and for a mutant that the `rw`
oracle does not kill. A mutant is killed when any test fails; `planned_hit` lists which of
its planned tests failed.
Usage: python mutants.py <git repo of the fix> <out dir> [ids...]
"""
import json, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT, APPROVALS, POLICY = 'backend/app/agent_runtime.py', 'backend/app/approvals.py', 'backend/app/execution_policy.py'
STORAGE, WORKER = 'backend/app/storage.py', 'backend/app/worker.py'
T = 'test_agent_write_resume.py::'
MUTANTS = {
    'M00': (None, None, None, []),
    # The card's five (docs/tasks/01-DEF-01-02-agent-write-resume.md:107) and the sixth in docs/defects.md.
    'M01': (STORAGE, '            if frames:\n                # The Agents on the call path',
            '            s.commit()\n            if frames:\n                # The Agents on the call path',
            [T + 'test_settle_write_saves_the_open_frames_in_its_commit']),
    'M02': (AGENT, '        finally:\n            OPEN_FRAMES.reset(opened)', '        finally:\n            pass',
            [T + 'test_a_specialist_write_saves_a_frame_for_each_agent_on_the_path']),
    'M03': (WORKER, '                        frames=open_frames()', '                        frames={}',
            [T + 'test_def01_a_settled_agent_write_is_not_sent_again_when_its_step_moves',
             T + 'test_settle_write_saves_the_open_frames_in_its_commit']),
    'M04': (STORAGE, "                if any(v.get('checkpoint_owner')==item for v in frames.values()):", '                if False:',
            [T + 'test_a_loop_item_agent_write_resumes_inside_the_item']),
    'M05': (WORKER, '                                if stored_digest is not None and request_digest()!=stored_digest:', '                                if False:',
            [T + 'test_def02_a_stored_write_result_is_not_returned_for_a_different_request']),
    'M06': (POLICY, 'UncertainWriteError,WriteReplayMismatch,asyncio.CancelledError', 'UncertainWriteError,asyncio.CancelledError',
            [T + 'test_a_different_request_at_the_same_invocation_stops_the_run']),
    # Added for this check: the other changed lines.
    'M07': (APPROVALS, "        if frames:run.data={**run.data,'agent_frames':{**run.data.get('agent_frames',{}),**frames}}", '        pass',
            [T + 'test_an_approved_write_saves_frames_at_complete']),
    'M08': (APPROVALS, "'agent_frames':{**run.data.get('agent_frames',{}),**exc.frames}}", "'agent_frames':exc.frames}", []),
    'M09': (WORKER, "**({'payload_sha256':sent_digest} if sent_digest else {})", '**{}',
            [T + 'test_def02_a_stored_write_result_is_not_returned_for_a_different_request']),
    'M10': (WORKER, '                                if stored_digest is not None and request_digest()!=stored_digest:',
            '                                if request_digest()!=stored_digest:',
            [T + 'test_an_old_result_row_without_a_digest_is_served_as_before']),
    'M11': (STORAGE, "                    data={**data,'agent_frames':{k:v for k,v in frames.items() if v.get('checkpoint_owner')!=item}}",
            "                    data={**data,'agent_frames':{}}", []),
}
FILES = ['backend/tests/test_agent_write_resume.py', 'backend/tests/test_execution_safety.py',
         'backend/tests/test_s07_boundaries.py', 'backend/tests/test_approvals.py']


def pytest(copy, out, name, targets):
    r = subprocess.run([sys.executable, '-m', 'pytest', *targets, '-q', '-rA', '-p', 'no:cacheprovider'],
                       cwd=copy, capture_output=True, text=True, timeout=1200)
    (out / f'{name}.txt').write_text(r.stdout + r.stderr)
    results = {m.group(2).split('/')[-1]: m.group(1).lower() for m in re.finditer(r'^(PASSED|FAILED|ERROR|XFAIL|XPASS) (\S+)', r.stdout, re.M)}
    if not results: raise SystemExit(f'{out.name}: the {name} oracle selected no test')
    return results


def run(mid, repo, root):
    path, old, new, planned = MUTANTS[mid]
    copy = Path(tempfile.mkdtemp(prefix=f'd01-{mid}-', dir=root)); out = root / 'results' / mid; out.mkdir(parents=True, exist_ok=True)
    subprocess.run(f'git -C "{repo}" archive HEAD | tar -x -C "{copy}"', shell=True, check=True)
    if path:
        text = (copy / path).read_text()
        if text.count(old) != 1: raise SystemExit(f'{mid}: the mutation point occurs {text.count(old)} times')
        (copy / path).write_text(text.replace(old, new))
    # Some tests read the commit (git rev-parse HEAD), so the copy is a repository too.
    subprocess.run('git init -q && git add -A && git -c user.name=m -c user.email=m@local commit -qm m', shell=True, cwd=copy, check=True)
    results = pytest(copy, out, 'rw', FILES)
    bad = lambda rs: sorted(k for k, v in rs.items() if v in ('failed', 'error', 'xpass'))
    for p in planned:
        if p not in results: raise SystemExit(f'{mid}: the planned test {p} did not run')
    planned_hit = [p for p in planned if results[p] != 'passed']
    killed_rw = bool(bad(results))
    oracle = 'rw'
    if mid == 'M00' or not killed_rw:
        results = pytest(copy, out, 'full', ['backend/tests']); oracle = 'rw+full'
    failed = bad(results)
    killed = (not failed) if mid == 'M00' else (killed_rw or bool(failed))
    result = {'id': mid, 'file': path, 'planned': planned, 'planned_hit': planned_hit, 'oracle': oracle, 'failed': failed, 'killed': killed,
              'ran': len(results)}
    (out / 'result.json').write_text(json.dumps(result, indent=1) + '\n')
    shutil.rmtree(copy)  # A path this run created with mkdtemp.
    print(mid, 'control passes' if mid == 'M00' and killed else ('killed' if killed else 'SURVIVED'), oracle, failed, flush=True)
    return result


if __name__ == '__main__':
    repo, root = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(); ids = sys.argv[3:] or list(MUTANTS)
    with ThreadPoolExecutor(3) as pool: results = list(pool.map(lambda m: run(m, repo, root), ids))
    (root / 'summary.json').write_text(json.dumps({r['id']: {'control_passes' if r['id'] == 'M00' else 'killed': r['killed'],
        'oracle': r['oracle'], 'planned': r['planned'], 'planned_hit': r['planned_hit'], 'failed': r['failed'], 'ran': r['ran']} for r in results}, indent=1) + '\n')
