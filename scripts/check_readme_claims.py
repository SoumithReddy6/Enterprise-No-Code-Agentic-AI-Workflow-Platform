"""Check the counts that the README states against the repository.

Each check reads a number from the README (and from docs/design.md, which holds the
failure-mode narrative) and compares it with its source:

- backend tests:  `pytest --collect-only` over backend/tests
- scenarios:      the S-rows of the deployed gate (scripts/check_production_scenarios.py
                  and scripts/scenarios/*.py)
- CI jobs:        the jobs in .github/workflows/ci.yml
- stack:          OpenAI, Claude, Elasticsearch and Pinecone appear only in the stack row
                  "adapter, tested with fixtures", because no live run of them exists
                  (docs/gap-audit.md §2.2 row 5). Move a name to the "run live" row only
                  together with the evidence of a live run.

Each check also fails when the document states no count at all, so a deleted claim
cannot pass by accident. Exit status 1 on any failure.

    python -m scripts.check_readme_claims
"""
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = ('README.md', 'docs/design.md')
FIXTURE_ONLY = ('OpenAI', 'Claude', 'Elasticsearch', 'Pinecone')
FIXTURE_LABEL = 'adapter, tested with fixtures'
LIVE_LABEL = 'run live'
WORDS = {
    'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7,
    'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11, 'twelve': 12,
}

TEST_COUNT = re.compile(r'\b(\d{1,3}(?:,\d{3})*|\d+) backend tests\b')
SCENARIO_COUNT = re.compile(r'\b(\d+) (?:deployed-process |deployed )?(?:production )?scenarios\b')
JOB_COUNT = re.compile(r'\b(\d+|' + '|'.join(WORDS) + r') (?:GitHub Actions |CI )jobs\b', re.IGNORECASE)
SCENARIO_ROW = re.compile(r"\(\s*'(S\d{2}[a-z]?)'\s*,")


def documents(root=ROOT):
    """{relative path: text} for each document that exists."""
    return {name: (root / name).read_text() for name in DOCUMENTS if (root / name).exists()}


def stated(pattern, texts):
    """[(document, line number, value)] for every match of pattern."""
    found = []
    for name, text in texts.items():
        for number, line in enumerate(text.splitlines(), 1):
            for match in pattern.finditer(line):
                raw = match.group(1).lower().replace(',', '')
                found.append((name, number, WORDS[raw] if raw in WORDS else int(raw)))
    return found


def collected_backend_tests(root=ROOT):
    """The count that `pytest --collect-only -q` prints for backend/tests."""
    out = subprocess.run(
        [sys.executable, '-m', 'pytest', 'backend/tests', '--collect-only', '-q', '-p', 'no:cacheprovider'],
        cwd=root, capture_output=True, text=True, check=False,
    )
    match = re.search(r'^(\d+) tests? collected', out.stdout, re.MULTILINE)
    if out.returncode != 0 or not match:
        raise RuntimeError('pytest --collect-only failed:\n' + out.stdout[-2000:] + out.stderr[-2000:])
    return int(match.group(1))


def scenario_ids(root=ROOT):
    """The sorted, distinct S-row ids of the deployed scenario gate."""
    files = [root / 'scripts/check_production_scenarios.py', *sorted((root / 'scripts/scenarios').glob('*.py'))]
    ids = {match for path in files for match in SCENARIO_ROW.findall(path.read_text())}
    return sorted(ids)


def ci_jobs(root=ROOT):
    """The job names of .github/workflows/ci.yml."""
    return list(yaml.safe_load((root / '.github/workflows/ci.yml').read_text())['jobs'])


def stack_rows(text):
    """[(label, cells)] for each `| **Stack…** | … |` table row of the README."""
    rows = []
    for line in text.splitlines():
        match = re.match(r'^\|\s*\*\*(Stack[^*]*)\*\*\s*\|(.*)\|\s*$', line)
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def count_errors(pattern, actual, what, texts):
    claims = stated(pattern, texts)
    if not claims:
        return [f'no document states a {what} count']
    return [f'{name}:{line} states {value} {what}; the repository has {actual}'
            for name, line, value in claims if value != actual]


def backend_test_count_errors(root=ROOT, texts=None):
    return count_errors(TEST_COUNT, collected_backend_tests(root), 'backend tests', texts or documents(root))


def scenario_count_errors(root=ROOT, texts=None):
    return count_errors(SCENARIO_COUNT, len(scenario_ids(root)), 'scenarios', texts or documents(root))


def job_count_errors(root=ROOT, texts=None):
    return count_errors(JOB_COUNT, len(ci_jobs(root)), 'CI jobs', texts or documents(root))


def stack_errors(text):
    rows = stack_rows(text)
    errors = []
    if not any(LIVE_LABEL in label for label, _ in rows):
        errors.append(f'README has no stack row labelled "{LIVE_LABEL}"')
    for label, cells in rows:
        if FIXTURE_LABEL in label:
            continue
        for name in FIXTURE_ONLY:
            if re.search(r'\b' + name + r'\b', cells):
                errors.append(f'stack row "{label}" names {name}, which has no live run; '
                              f'list it under "{FIXTURE_LABEL}"')
    return errors


def main(root=ROOT):
    texts = documents(root)
    errors = [*backend_test_count_errors(root, texts), *scenario_count_errors(root, texts),
              *job_count_errors(root, texts), *stack_errors(texts.get('README.md', ''))]
    for error in errors:
        print('FAIL', error)
    if not errors:
        print('README claims match: backend tests, scenarios, CI jobs, stack')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
