"""W03 mutants for scripts/check_readme_claims.py and the README claims it checks.

Each mutant is one text replacement in a scratch copy of the working tree (never the
repository itself). Oracle: backend/tests/test_readme_claims.py, plus the CI command
`python -m scripts.check_readme_claims` (exit status).

M00 is the no-change control: the oracle must pass on it. A guard fails the run when pytest
reports no test run, so a kill cannot come from a missing test.

    python evals/results/w03-2026-11-27/mutants/mutants.py <repo> <scratch> <out.json>
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

NEW_FILES = ['scripts/check_readme_claims.py', 'backend/tests/test_readme_claims.py', 'docs/design.md']
SCRIPT = 'scripts/check_readme_claims.py'
MUTANTS = [
    ('M00', None, None, None),
    ('M01', SCRIPT, 'return int(match.group(1))', 'return int(match.group(1)) + 1'),
    ('M02', 'README.md', '| Unit and integration | 2,204 backend tests', '| Unit and integration | 2,205 backend tests'),
    ('M03', SCRIPT, r'''SCENARIO_ROW = re.compile(r"\(\s*'(S\d{2}[a-z]?)'\s*,")''', r'''SCENARIO_ROW = re.compile(r"\(\s*'(S\d{2})'\s*,")'''),
    ('M04', 'README.md', '| Deployed processes | 34 scenarios', '| Deployed processes | 27 scenarios'),
    ('M05', SCRIPT, '    rows = stack_rows(text)\n    errors = []', '    rows = stack_rows(text)\n    return []\n    errors = []'),
    ('M06', 'README.md', '| **Stack, run live** | React + React Flow', '| **Stack, run live** | OpenAI · React + React Flow'),
    ('M07', 'README.md', 'five GitHub Actions jobs', 'six GitHub Actions jobs'),
    ('M08', SCRIPT, "        return [f'no document states a {what} count']", '        return []'),
    ('M09', SCRIPT, r"JOB_COUNT = re.compile(r'\b(\d+|' + '|'.join(WORDS) + r')", r"JOB_COUNT = re.compile(r'\b(\d+|' + 'zzz' + r')"),
    ('M10', '.github/workflows/ci.yml', '  postgres-conformance:\n', '  extra-job:\n    runs-on: ubuntu-latest\n    steps:\n      - run: "true"\n  postgres-conformance:\n'),
    ('M11', SCRIPT, "raw = match.group(1).lower().replace(',', '')", "raw = match.group(1).lower().replace(',', '0')"),
    ('M12', 'README.md', '| **Verification** | 2,204 backend tests', '| **Verification** | the backend tests'),
    ('M14', 'README.md', '| Backend tests | 2,204 backend tests collected', '| Backend tests | 2,203 backend tests collected'),
    ('M13', SCRIPT, "        if FIXTURE_LABEL in label:\n            continue\n", ''),
]


def files(repo):
    listed = subprocess.run(['git', 'ls-files', '-z'], cwd=repo, capture_output=True, check=True).stdout.split(b'\0')
    names = [n.decode() for n in listed if n] + NEW_FILES
    return [n for n in names if (repo / n).is_file()]


def copy(repo, dest):
    if dest.exists():
        shutil.rmtree(dest)
    for name in files(repo):
        target = dest / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / name, target)


def oracle(root, python):
    test = subprocess.run([python, '-m', 'pytest', 'backend/tests/test_readme_claims.py', '-q', '-p', 'no:cacheprovider'],
                          cwd=root, capture_output=True, text=True)
    cli = subprocess.run([python, '-m', 'scripts.check_readme_claims'], cwd=root, capture_output=True, text=True)
    last = test.stdout.strip().splitlines()[-1] if test.stdout.strip() else ''
    if 'no tests ran' in test.stdout or ('passed' not in last and 'failed' not in last):
        raise SystemExit(f'guard: no test ran in {root}: {last!r}')
    return {'pytest_exit': test.returncode, 'pytest_summary': last, 'cli_exit': cli.returncode,
            'cli_output': cli.stdout.strip().splitlines()[:3]}


def main(repo, scratch, out):
    repo, scratch = Path(repo).resolve(), Path(scratch).resolve()
    python = str(repo / '.venv/bin/python')
    rows = []
    for mid, path, old, new in MUTANTS:
        dest = scratch / mid
        copy(repo, dest)
        if path:
            target = dest / path
            text = target.read_text()
            assert text.count(old) == 1, (mid, old)
            target.write_text(text.replace(old, new))
        result = oracle(dest, python)
        killed = result['pytest_exit'] != 0 or result['cli_exit'] != 0
        rows.append({'id': mid, 'file': path, 'killed': killed, **result})
        print(mid, 'control' if mid == 'M00' else ('killed' if killed else 'SURVIVED'), result['pytest_summary'], 'cli', result['cli_exit'], flush=True)
        shutil.rmtree(dest)
    control = rows[0]
    if control['killed']:
        raise SystemExit('M00 control failed: the oracle does not pass on the unmutated copy')
    Path(out).write_text(json.dumps(rows, indent=2) + '\n')


if __name__ == '__main__':
    main(*sys.argv[1:4])
