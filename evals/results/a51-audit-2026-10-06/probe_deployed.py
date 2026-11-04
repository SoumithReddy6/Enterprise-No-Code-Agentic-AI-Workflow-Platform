"""Independent API/worker/HTTP-receiver probe. All accounts and writes are throwaway."""
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.check_production_scenarios import Lab, flow_nodes, input_node, response


def main():
    output = Path(__file__).resolve().parent
    rows = []
    with tempfile.TemporaryDirectory(prefix='relay-a51-audit-') as directory:
        lab = Lab(directory, output)
        try:
            lab.boot()
            for literal, operator, target, expected_writes in [
                ('1000.00000000000001', 'gt', '1000', 1),
                ('0.100000000000000005', 'eq', '0.1', 0),
            ]:
                path = '/deliver/audit-' + operator
                w = flow_nodes([input_node(), {'id': 'check', 'type': 'condition',
                    'config': {'operator': operator, 'compare_to': target,
                               'compare_as': 'number', 'field': 'amount'},
                    'inputs': {'value': 'input.message'}}])
                w['nodes'] += [{**lab.tool(path, True, False), 'id': 'send'},
                               response('send.text', 'sent'), response('input.message', 'kept')]
                w['edges'] += [
                    {'id': 't', 'source': 'check', 'target': 'send', 'sourceHandle': 'true'},
                    {'id': 'f', 'source': 'check', 'target': 'kept', 'sourceHandle': 'false'},
                    {'id': 'd', 'source': 'send', 'target': 'sent'},
                ]
                message = '{"amount":' + literal + '}'
                identifier = lab.submit(w, message)
                run = lab.wait(identifier)
                actual = lab.provider.count(path)
                rows.append({'message': message, 'operator': operator, 'target': target,
                             'run_id': identifier, 'status': run['status'],
                             'branch': run.get('checkpoints', {}).get('check', {}).get('branch'),
                             'expected_writes': expected_writes, 'actual_writes': actual,
                             'passed': actual == expected_writes})
            requests = list(lab.provider.calls)
        finally:
            lab.close()
    report = {'deployment': 'isolated API process + worker process + local HTTP receiver',
              'external_writes': 'synthetic localhost only', 'rows': rows,
              'requests': requests, 'passed': all(r['passed'] for r in rows)}
    (output / 'deployed-results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
