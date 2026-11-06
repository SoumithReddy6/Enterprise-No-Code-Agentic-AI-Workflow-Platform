"""Build the A5.3 Part 1 isolation copy: git archive 89129ca + the changed files, one commit."""
import hashlib, pathlib, shutil, subprocess, sys, tempfile
SP = pathlib.Path(sys.argv[1]); OUT = pathlib.Path(sys.argv[2])
FILES = """.github/workflows/ci.yml README.md backend/app/accounting_state.py backend/app/compiler.py
backend/app/registry.py backend/app/worker.py backend/tests/test_binding_contract.py docs/operations.md
docs/platform-reference.md frontend/lib/workflow.ts frontend/tests/browser/request-nodes.json
frontend/tests/fixtures/bindable-outputs.json scripts/check_milestone_storage.py scripts/scenarios/milestone_worker.py
backend/tests/test_parallel_accounting.py backend/tests/test_parallel_branches.py""".split()
iso = pathlib.Path(tempfile.mkdtemp(prefix='a53-iso-', dir=SP))
subprocess.run(f'git archive 89129ca | tar -x -C "{iso}"', shell=True, check=True)
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for f in FILES:
    (iso / f).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(f, iso / f)
git = lambda *a: subprocess.run(['git', *a], cwd=iso, check=True, capture_output=True, text=True).stdout.strip()
git('init', '-q'); git('add', '-A'); git('-c', 'user.email=iso@local', '-c', 'user.name=iso', 'commit', '-qm', 'a53 part1 isolation')
(SP / 'iso-path').write_text(str(iso))
other = [p for p in subprocess.run(['git', 'diff', '--name-only', '89129ca', '--', '.', ':!evals', ':!docs/reviews'],
         capture_output=True, text=True, check=True).stdout.split() if p not in FILES]
lines = [f'# A5.3 Part 1 isolation copy: git archive 89129ca + the {len(FILES)} changed files, git init + one commit',
         f'copy: <scratchpad>/{iso.name}', f'commit: {git("rev-parse", "HEAD")}', '', '## Files copied (sha256; copy == working tree)']
lines += [f'{sha(pathlib.Path(f))} {f} {"match" if sha(pathlib.Path(f)) == sha(iso / f) else "DIFFER"}' for f in FILES]
lines += ['', f'## Tracked files that differ from 89129ca, outside the {len(FILES)} (evals/ and docs/reviews/ excluded)', *(other or ['none'])]
OUT.write_text('\n'.join(lines) + '\n'); print(OUT.read_text())
