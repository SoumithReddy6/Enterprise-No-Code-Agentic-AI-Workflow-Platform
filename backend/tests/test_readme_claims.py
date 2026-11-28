"""W03: the counts that the README states agree with the repository (docs/gap-audit.md §2.2).

The checks live in scripts/check_readme_claims.py, which CI also runs.
"""
from scripts import check_readme_claims as claims


def test_backend_test_count_in_readme_matches_collection():
    assert claims.backend_test_count_errors() == []


def test_scenario_count_in_readme_matches_the_gate():
    # 34 S-rows at 24e659c (S01–S29 with variants); README.md said 27 in §5.
    assert claims.scenario_count_errors() == []


def test_ci_job_count_matches_the_workflow():
    assert claims.job_count_errors() == []


def test_stack_line_separates_live_and_fixture_tested():
    assert claims.stack_errors(claims.documents()['README.md']) == []


def test_checker_refuses_a_document_that_states_no_count():
    texts = {'README.md': 'Relay has tests.'}
    assert claims.count_errors(claims.TEST_COUNT, 10, 'backend tests', texts) == ['no document states a backend tests count']
    assert claims.stack_errors('| **Stack** | Ollama |') == ['README has no stack row labelled "run live"']


def test_checker_reads_thousands_and_number_words():
    texts = {'README.md': '2,198 backend tests · five GitHub Actions jobs · 34 deployed-process production scenarios'}
    assert claims.stated(claims.TEST_COUNT, texts) == [('README.md', 1, 2198)]
    assert claims.stated(claims.JOB_COUNT, texts) == [('README.md', 1, 5)]
    assert claims.stated(claims.SCENARIO_COUNT, texts) == [('README.md', 1, 34)]
    assert claims.count_errors(claims.TEST_COUNT, 2199, 'backend tests', texts) == [
        'README.md:1 states 2198 backend tests; the repository has 2199']
    rows = '| **Stack, run live** | Ollama |\n| **Stack, adapter, tested with fixtures** | OpenAI, Claude |'
    assert claims.stack_errors(rows) == []
    assert claims.stack_errors(rows + '\n| **Stack, run live too** | Pinecone |') != []
