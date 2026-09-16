"""Exercise a running Relay frontend/API without a paid model call."""
import json
import os
from pathlib import Path
import time
import httpx

workflow=json.loads((Path(__file__).resolve().parents[1]/'examples/ai-workflow.json').read_text())
with httpx.Client(base_url='http://127.0.0.1:3000',timeout=15) as client:
    assert client.get('/').status_code==200
    assert client.get('/api/health').json()['status']=='ok'
    if client.get('/api/auth/status').json()['enabled']:
        email,password=os.environ.get('RELAY_TEST_EMAIL'),os.environ.get('RELAY_TEST_PASSWORD')
        if not email or not password:raise SystemExit('Set RELAY_TEST_EMAIL and RELAY_TEST_PASSWORD for an existing test account. This script never creates or claims your workspace.')
        login=client.post('/api/auth/login',json={'email':email,'password':password})
        assert login.status_code==200,'Test-account login failed.'
    definitions=client.get('/api/nodes').json()
    assert len([n for n in definitions if not n.get('hidden')])==13
    check=client.post('/api/validate',json=workflow)
    assert check.json()['valid'],check.text
    result=client.post('/api/runs',json={'workflow':workflow,'message':'How do AI workflows work?'})
    assert result.status_code==201,result.text
    id=result.json()['id']
    for _ in range(100):
        run=client.get('/api/runs/'+id).json()
        if run['status'] not in ('queued','running'):break
        time.sleep(.1)
    assert run['status']=='success',run
    assert '[Demo · no model called]' in run['output']
    assert 'How do AI workflows work?' in run['output']
    assert [e['node_id'] for e in run['events'] if e.get('node_id') and e['status']=='success']==['input','prompt','model','response']
    assert 'event: done' in client.get('/api/runs/'+id+'/events').text
    print('Live smoke passed: frontend → same-origin API → validation → LangGraph → four successful nodes → persisted response and SSE history.')
