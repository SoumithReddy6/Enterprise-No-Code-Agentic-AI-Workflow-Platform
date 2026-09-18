"""Evaluation scores distinguish substring/document heuristics from semantic correctness."""
import json
from types import SimpleNamespace
import pytest
from scripts import eval_retrieval as evaluation


def test_evidence_coverage_requires_each_expected_document():
    question={
        'question':'What is the combined daily travel limit?',
        'evidence':[
            {'document':'meals.md','answers':['$65']},
            {'document':'hotels.md','answers':['$220']},
        ],
        'kind':'reasoning',
    }
    only_meals=[{'filename':'meals.md','text':'Meals are capped at $65.'}]
    complete=only_meals+[{'filename':'hotels.md','text':'Hotels are capped at $220.'}]

    assert evaluation.evidence_coverage(question,only_meals)==0.5
    assert evaluation.evidence_coverage(question,complete)==1.0


def test_evidence_coverage_rejects_answer_text_from_wrong_document():
    question={'question':'What is the cap?','document':'policy.md','answers':['$220'],'kind':'verbatim'}
    sources=[{'filename':'unrelated.md','text':'A different allowance is $220.'}]

    assert evaluation.evidence_coverage(question,sources)==0.0


def test_answer_matches_requires_every_compound_answer_group():
    question={
        'question':'What severity is it, and when is the postmortem due?',
        'answers':['SEV1','5 business days'],
        'answer_groups':[['SEV1'],['5 business days','five business days']],
        'kind':'reasoning',
    }

    assert evaluation.answer_matches(question,'This is SEV1.') is False
    assert evaluation.answer_matches(question,'This is SEV1 and the postmortem is due within five business days.') is True


def test_abstention_detection_ignores_notes_about_other_passages():
    answer='Forklift certification is renewed every 24 months [S1].\n\nNote: The other passages do not mention forklift certification.'

    assert evaluation.is_abstention(answer) is False


def test_abstention_detection_marks_mixed_and_concluding_abstentions():
    mixed='The evidence is insufficient to answer the question.\n\nHowever, passage [S2] suggests five retries.'
    concluding='The policy gives a 30-day deadline.\n\nTherefore, the evidence is insufficient to determine whether 45 days is on time.'

    assert evaluation.is_abstention(mixed) is True
    assert evaluation.is_abstention(concluding) is True

@pytest.mark.asyncio
async def test_generation_report_labels_heuristics_and_requires_expected_document(monkeypatch):
    async def invoke(state):
        return {'values':{'out':{'text':'The answer is not 42 [S1].'},'agent':{'sources':json.dumps([
            {'filename':'wrong.txt','text':'42','citation':'S1','cited':True}])}}}
    monkeypatch.setattr(evaluation,'compile_workflow',lambda *a,**k:SimpleNamespace(graph=SimpleNamespace(ainvoke=invoke)))
    args=SimpleNamespace(generate_mode='keyword',generate='mock',top_k=4)
    questions=[{'question':'What is the answer?','document':'expected.txt','answers':['42'],'kind':'verbatim'}]
    result=await evaluation.evaluate_generation(None,'kb',questions,args)
    assert result['metric_version']==2 and len(result['limitations'])==3
    assert result['summary']['answer_substring_match_rate']==1
    assert result['summary']['citation_document_match']==0
    assert result['summary']['answer_retrieved']==0
    assert 'answer_accuracy' not in result['summary'] and 'citation_faithful' not in result['summary']
