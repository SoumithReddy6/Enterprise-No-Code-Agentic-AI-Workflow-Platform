import subprocess
import sys
import pytest

@pytest.mark.parametrize('module,args',[
 ('scripts.calibrate_claim_judge',['/nonexistent-calibration.json','--model','llama3.1:latest','--out','unused.json']),
 ('scripts.eval_retrieval',['--generate','llama3.1:latest','--judge','llama3.1:latest','--questions','/nonexistent-questions.json']),
])
def test_retired_json_judge_is_rejected_before_model_calls(module,args):
    result=subprocess.run([sys.executable,'-m',module,*args],capture_output=True,text=True,timeout=10)
    assert result.returncode==2
    assert 'retired' in result.stderr and 'scripts.eval_nli' in result.stderr

def test_reader_experiment_requires_generation():
    result=subprocess.run([sys.executable,'-m','scripts.eval_retrieval','--answerability-reader','/not-used','--questions','/nonexistent-questions.json'],capture_output=True,text=True,timeout=10)
    assert result.returncode==2 and '--answerability-reader requires --generate' in result.stderr
