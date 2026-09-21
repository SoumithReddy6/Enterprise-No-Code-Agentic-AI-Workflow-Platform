"""Evaluation scores distinguish substring/document heuristics from semantic correctness."""
import json
from types import SimpleNamespace
import pytest
from scripts import eval_retrieval as evaluation
from scripts.check_grounding_eval import check

def test_adoption_gate_fails_on_regression_missing_compliance_or_incomplete_run():
    rows=[{'kind':'verbatim','cited':['S1']}] * 33 + [{'kind':'unanswerable','cited':[]}] * 20
    summary={'n':53,'answer_substring_match_rate':0.879,'false_abstention':0.061,'citation_rate':0.939,'errors':0,'heuristic_abstention_on_unanswerable':1.0,
             'contract_compliance_rate':{'first_attempt':0.921,'unavailable':0,'attempted':38}}
    report={'generation':{'summary':summary,'rows':rows}}
    assert check(report)['accepted']
    for key,value in [('answer_substring_match_rate',0.80),('citation_rate',0.788),('false_abstention',0.1),('errors',1),('heuristic_abstention_on_unanswerable',0.6)]:
        changed={'generation':{'summary':{**summary,key:value},'rows':rows}}
        assert not check(changed)['accepted']
    for compliance in [{},{'first_attempt':0.89,'unavailable':0,'attempted':38}]:
        changed={'generation':{'summary':{**summary,'contract_compliance_rate':compliance},'rows':rows}}
        assert not check(changed)['accepted']
    assert not check({'generation':{'summary':summary,'rows':rows[:10]}})['accepted']

def test_contract_compliance_has_explicit_denominator_and_failures():
    rows=[{'contract_compliance':s} for s in ['first_attempt','first_attempt','after_repair','failed','not_called']]
    result=evaluation.contract_compliance(rows)
    assert result['first_attempt']==0.5 and result['after_repair']==0.25 and result['failed']==0.25
    assert result['attempted']==4 and result['not_called']==1


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
            {'filename':'wrong.txt','text':'42','citation':'S1','cited':True,'score':.73,'rerank_score':-1.25}])}}}
    monkeypatch.setattr(evaluation,'compile_workflow',lambda *a,**k:SimpleNamespace(graph=SimpleNamespace(ainvoke=invoke)))
    args=SimpleNamespace(generate_mode='keyword',generate='mock',top_k=4)
    questions=[{'question':'What is the answer?','document':'expected.txt','answers':['42'],'kind':'verbatim'}]
    result=await evaluation.evaluate_generation(None,'kb',questions,args)
    assert result['metric_version']==2 and len(result['limitations'])==3
    assert result['summary']['answer_substring_match_rate']==1
    assert result['summary']['citation_document_match']==0
    assert result['summary']['answer_retrieved']==0
    assert 'answer_accuracy' not in result['summary'] and 'citation_faithful' not in result['summary']
    assert result['rows'][0]['passages']==[{'citation':'S1','filename':'wrong.txt','page':None,'text':'42','score':.73,'rerank_score':-1.25}]

@pytest.mark.asyncio
async def test_retrieval_keeps_unanswerable_score_rows_out_of_answer_recall():
    class Services:
        async def retrieve(self,*args):
            return {'sources':[{'filename':'policy.md','page':1,'text':'42','score':.7,'rerank_score':2.}]}
    questions=[{'question':'Known?','document':'policy.md','answers':['42'],'kind':'verbatim'},
               {'question':'Unknown?','document':None,'answers':[],'kind':'unanswerable','split':'holdout'}]
    args=SimpleNamespace(modes=['hybrid'],top_k=4,candidate_k=50,reranker='local_cross_encoder')
    result=(await evaluation.evaluate_retrieval(Services(),'kb',questions,args))['hybrid']
    assert result['all']['n']==1 and result['all']['answer@4']==1
    assert len(result['rows'])==2 and result['rows'][1]['split']=='holdout'
    assert result['rows'][1]['passages'][0]['score']==.7
    assert result['rows'][1]['passages'][0]['rerank_score']==2.

def test_corpus_manifest_excludes_adjacent_evaluation_labels(tmp_path):
    (tmp_path/'policy.md').write_text('The source policy.')
    (tmp_path/'questions.json').write_text('[]')
    (tmp_path/'questions-expanded.json').write_text('[]')
    (tmp_path/'answerability-labels.md').write_text('Answers and annotation rationales.')
    (tmp_path/'corpus-manifest.json').write_text(json.dumps(['policy.md']))
    args=SimpleNamespace(corpus=str(tmp_path),questions=str(tmp_path/'questions-expanded.json'))
    assert evaluation.corpus_files(args)==[tmp_path/'policy.md']
    (tmp_path/'corpus-manifest.json').write_text(json.dumps(['../outside.pdf']))
    with pytest.raises(ValueError,match='manifest'):evaluation.corpus_files(args)

@pytest.mark.asyncio
@pytest.mark.parametrize('decision,failed',[
    ({'status':'scored','answerable':False,'margin':-3.,'threshold':0.},False),
    ({'status':'error','answerable':False,'error':'Model unavailable'},True),
    ({'status':'scored','answerable':'false'},True),
])
async def test_experimental_answerability_rejection_skips_answer_model(monkeypatch,decision,failed):
    from backend.app import registry
    from backend.app.kb import answerability
    from backend.tests.test_agent_grounding import source
    calls=[]
    async def model(*args):calls.append(True);raise AssertionError('Rejected evidence must not reach the answer model')
    monkeypatch.setattr(registry,'llm_node',model)
    class Reader:
        def __init__(self,path):pass
        def score(self,question,passages):return decision
    monkeypatch.setattr(answerability,'AnswerabilityReader',Reader)
    class Services:
        async def retrieve(self,*args):return {'query':'Missing fact?','knowledge_base_id':'kb1','version':1,'sources':[source(1)]}
        async def verify(self,sources,tenant):return sources
    args=SimpleNamespace(generate_mode='hybrid',generate='mock',top_k=4,answerability_reader='fake')
    result=await evaluation.evaluate_generation(Services(),'kb1',[{'question':'Missing fact?','document':None,'answers':[],'kind':'unanswerable'}],args)
    row=result['rows'][0]
    assert calls==[] and bool(row['error'])==failed
    assert row['abstained']==(not failed)
    assert row['passages']==[] and row['cited']==[]
    assert row['pre_generation_passages'][0]['score']==.5
    assert row['answerability']==decision
