import pytest
from backend.app.kb.reranking import rerank,load

@pytest.mark.asyncio
async def test_reranking_scores_full_context_and_preserves_sources(monkeypatch):
    from backend.app.kb import reranking
    sources=[{'id':'a','text':'Heading A\nIrrelevant'},{'id':'b','text':'Heading B\nRelevant'}]
    def score(query,passages):
        assert query=='Question' and passages==[s['text'] for s in sources]
        return [-2.,5.]
    monkeypatch.setattr(reranking,'score_pairs',score)
    ranked=await rerank('Question',sources)
    assert ranked==[{**sources[1],'rerank_score':5.},{**sources[0],'rerank_score':-2.}]
    assert all('rerank_score' not in s for s in sources)

def test_missing_assets_fail_actionably_without_download(tmp_path):
    with pytest.raises(ValueError,match='not installed'):load(str(tmp_path))
