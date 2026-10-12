"""Exercise pure checks against a record downloaded by the real API/worker adapter.

No verifier handler or continuation gate is enabled yet. This measures checking with
an authorized record read, not end-to-end write authorization (Step 6).
"""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import time

from backend.app.extraction_checks import verify_extraction
from backend.app.models import Node
from backend.app.provenance import source_context, reference_context
from backend.app.request_contracts import ExtractionCheckConfig
from backend.app.verification_receipts import make_receipt, receipt_matches, verification_policy_digest
from scripts.check_production_scenarios import Lab, ROOT, flow_nodes, input_node, response


def run(output):
    output.mkdir(parents=True, exist_ok=True);started = time.time()
    files = [*sorted((ROOT/'backend/app').rglob('*.py')), Path(__file__)]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    rows = []
    with tempfile.TemporaryDirectory(prefix='relay-extraction-lab-') as temp:
        lab = Lab(temp, output)
        try:
            lab.boot()
            read = {'id':'catalog','type':'tool_jira','config':{'connection_id':lab.jira,'operation':'search','jql':'2'},
                'inputs':{'input':'input.message'}}
            run_id = lab.submit(flow_nodes([input_node(), read, response('catalog.text')]), 'Inspect LAB-0')
            result = lab.wait(run_id);assert result['status'] == 'success', result.get('error')
            catalogue = result['checkpoints']['catalog']['text']
            assert lab.provider.count('/rest/api/3/search/jql') == 1
            policy = ExtractionCheckConfig(expected_schema={'type':'object'}, required_fields=['/item'],
                field_rules=[{'field':'/item','kind':'reference_member','reference_list':'/issues','reference_key':'/key'}])
            def check(name, original, value, expected, owner=None, kind='tool_jira'):
                node = Node(id='catalog',type=kind,config={'operation':'search'})
                resolved = reference_context(node, 'text', catalogue, tenant_id=owner or lab.tenant)
                origins = replace(source_context('input','message',original),tenant_id=lab.tenant,
                    reference_sources=resolved.reference_sources)
                report = verify_extraction(original, value, policy, origins, catalogue)
                assert report.verdict == expected, (name,report.model_dump())
                rows.append({'name':name,'verdict':report.verdict,'fields':report.model_dump()['fields'],'status':'passed'})
                return origins,report
            origins,report = check('actual downloaded record and written key','Inspect LAB-0','{"item":"LAB-0"}','passed')
            check('verb is not a catalogue member','order $450','{"item":"order"}','needs_review')
            check('catalogue does not replace original evidence','Hello','{"item":"LAB-0"}','needs_review')
            check('another tenant cannot furnish the reference','Inspect LAB-0','{"item":"LAB-0"}','needs_review',owner='other')
            check('model echo of the same record is not authority','Inspect LAB-0','{"item":"LAB-0"}','needs_review',kind='agent')
            digest = verification_policy_digest(policy,origins)
            saved = make_receipt(run_id,'verify','Inspect LAB-0','{"item":"LAB-0"}',digest,report)
            assert receipt_matches(saved,run_id=run_id,node_id='verify',original='Inspect LAB-0',value='{"item":"LAB-0"}',policy_digest=digest)
            assert not receipt_matches(saved,run_id=run_id,node_id='verify',original='Inspect LAB-0',value='{"item":"LAB-1"}',policy_digest=digest)
            rows.append({'name':'changed exact interpretation invalidates receipt','status':'passed'})
            assert not any(c['method']!='GET' for c in lab.provider.calls)
        finally:lab.close()
    changed = [name for name,d in hashes.items() if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=d]
    report = {'started_at':started,'completed_at':time.time(),'source_sha256':hashes,'source_files_changed':changed,
        'scope':'real separate API/worker, authorized Jira HTTP read, isolated SQLite, pure checker invoked after read; runtime gate is NOT enabled',
        'rows':rows,'receiver_writes':0,'passed':len(rows)==6 and all(r['status']=='passed' for r in rows) and not changed}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default='evals/results/extraction-checks-step5/source-read')
    raise SystemExit(run(Path(parser.parse_args().out).resolve()))
