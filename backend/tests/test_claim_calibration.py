from scripts.calibrate_claim_judge import summarize

def test_calibration_keeps_contract_errors_out_of_acceptance_and_reports_coverage():
    rows=[
        {'expected':'supported','judgment':{'status':'judged','claims':[{'verdict':'supported'}]}},
        {'expected':'supported','judgment':{'status':'error','claims':[]}},
        {'expected':'contradicted','judgment':{'status':'judged','claims':[{'verdict':'supported'}]}},
        {'expected':'insufficient_evidence','judgment':{'status':'judged','claims':[{'verdict':'insufficient_evidence'}]}},
    ]
    result=summarize(rows)
    assert result['contract_compliance_rate']==.75
    assert result['false_acceptance_rate']==.5
    assert result['false_rejection_rate']==0
    assert result['errors']==1 and result['supported_label_coverage']==.5
