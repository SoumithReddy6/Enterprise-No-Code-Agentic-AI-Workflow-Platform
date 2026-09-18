import json
import pytest
from scripts import eval_claims

@pytest.mark.asyncio
@pytest.mark.parametrize('verdict',['supported','contradicted','insufficient_evidence'])
async def test_judgments_preserve_quote_evidence_and_rationale(monkeypatch,verdict):
    async def model(*args,**kwargs):
        assert '42 days' in args[2]
        return json.dumps({'claims':[{'claim':'It takes 42 days.','citations':['S1'],'verdict':verdict,'rationale':'Compare duration to the passage.'}]})
    monkeypatch.setattr(eval_claims,'ollama_chat',model)
    r=await eval_claims.judge_claims('It takes 42 days. [S1]',[{'citation':'S1','text':'42 days'}],'mock')
    assert r['status']=='judged' and r['claims'][0]['verdict']==verdict and r['review_required']

@pytest.mark.asyncio
@pytest.mark.parametrize('change',[{'claim':'invented claim'},{'citations':['S9']},{'citations':[]},{'verdict':'maybe'}])
async def test_invalid_judgments_never_count_as_passes(monkeypatch,change):
    claim={'claim':'42 days','citations':['S1'],'verdict':'supported','rationale':'Matches'};claim.update(change)
    async def model(*args,**kwargs):return json.dumps({'claims':[claim]})
    monkeypatch.setattr(eval_claims,'ollama_chat',model)
    r=await eval_claims.judge_claims('42 days [S1]',[{'citation':'S1','text':'42 days'}],'mock')
    assert r['status']=='error' and r['supported_fraction'] is None

@pytest.mark.asyncio
async def test_no_claims_is_not_a_perfect_score(monkeypatch):
    async def model(*args,**kwargs):return '{"claims":[]}'
    monkeypatch.setattr(eval_claims,'ollama_chat',model)
    r=await eval_claims.judge_claims('I cannot answer.',[],'mock')
    assert r['status']=='no_claims' and r['supported_fraction'] is None
