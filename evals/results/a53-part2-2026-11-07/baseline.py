"""A5.3 Part 2 baseline: the new tests on a clean archive of 4db70a7 (application code = main 3e5ae46).

Usage: python baseline.py <scratch dir> <python> <output file>
The copy gets only the four new test files; node_modules is a symlink to the working tree's.
"""
import os, pathlib, re, subprocess, sys, tempfile
SP = pathlib.Path(sys.argv[1]); PY = sys.argv[2]; OUT = pathlib.Path(sys.argv[3])
TESTS = ['frontend/tests/parallel.test.ts', 'frontend/tests/browser/parallel-branches.spec.ts',
         'frontend/tests/browser/parallel-issues.json', 'backend/tests/test_parallel_editor_contract.py']
base = pathlib.Path(tempfile.mkdtemp(prefix='a53-p2-baseline-', dir=SP))
subprocess.run(f'git archive 4db70a7 | tar -x -C "{base}"', shell=True, check=True)
for f in TESTS: (base / f).write_bytes(pathlib.Path(f).read_bytes())
os.symlink(pathlib.Path('frontend/node_modules').resolve(), base / 'frontend/node_modules')
fe = base / 'frontend'
lines = ['# A5.3 Part 2 baseline: git archive 4db70a7 + the 4 new test files', f'copy: <scratchpad>/{base.name}', '']
r = subprocess.run(['node', '--experimental-strip-types', '--test', '--test-reporter=tap', 'tests/parallel.test.ts'], cwd=fe, capture_output=True, text=True)
lines += ['## Unit (node --test tests/parallel.test.ts)', *re.findall(r'^(?:not )?ok \d+ - .+$', r.stdout, re.M),
          *[l.strip() for l in r.stdout.splitlines() if re.search(r"actual:|expected:|TypeError", l)], '']
r = subprocess.run(['node', 'node_modules/@playwright/test/cli.js', 'test', 'tests/browser/parallel-branches.spec.ts',
                    '--reporter=line', '--timeout=15000'], cwd=fe, capture_output=True, text=True)
out = re.sub(r'\x1b\[[0-9;]*m', '', r.stdout)
keep = [l.rstrip() for l in out.splitlines()
        if re.search(r'^\s+\d+\) |Error:|Expected|Received|waiting for getByRole|^\s+\d+ (passed|failed)', l)]
lines += ['## Browser (playwright tests/browser/parallel-branches.spec.ts, 15 s timeout)', *keep, '']
r = subprocess.run([PY, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '-rA', 'backend/tests/test_parallel_editor_contract.py'],
                   cwd=base, capture_output=True, text=True, env={'PYTHONHASHSEED': '0', 'PATH': '/usr/bin:/bin'})
lines += ['## Backend (pytest backend/tests/test_parallel_editor_contract.py)',
          *[l for l in r.stdout.splitlines() if re.match(r'(PASSED|FAILED|\d+ (passed|failed))', l)]]
OUT.write_text('\n'.join(lines) + '\n'); print(OUT.read_text())
