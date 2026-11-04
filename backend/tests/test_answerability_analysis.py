import pytest
from scripts.analyze_answerability import analyze

def row(kind,score,split='calibration'):
    return {'kind':kind,'split':split,'question':'q','passages':[{'score':score}]}

def test_overlap_is_reported_without_selecting_an_unsafe_threshold():
    r=analyze([row('verbatim',.2),row('verbatim',.8),row('unanswerable',.7)],'score')
    assert r['selected_threshold'] is None
    assert r['feasible_threshold_count']==0

def test_selection_uses_calibration_not_holdout_labels():
    rows=[row('verbatim',.8),row('unanswerable',.1),row('unanswerable',.9,'holdout')]
    r=analyze(rows,'score')
    assert r['selected_threshold'] is not None
    assert r['calibration']['unanswerable_rejection_rate']==1
    assert r['holdout']['unanswerable_rejection_rate']==0

def test_missing_scores_are_not_replaced_with_zero():
    with pytest.raises(ValueError,match='missing'):
        analyze([{'kind':'unanswerable','passages':[{}]}],'score')
