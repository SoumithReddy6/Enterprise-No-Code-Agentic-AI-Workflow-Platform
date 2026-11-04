import base64
import json
import pytest
from cryptography.fernet import Fernet
from backend.app.kb.management import Management

@pytest.fixture
def manager(tmp_path):
    return Management(f'sqlite:///{tmp_path}/management.db', tmp_path, Fernet.generate_key())

def create(m):
    return m.call('create', 'a', {'name': 'Books', 'config': {'backend': 'faiss'}})['id']

def upload(m, kb, key='one', content=b'hello', **extra):
    return m.call('upload', 'a', {'kb_id': kb, 'filename': 'book.txt', 'content_b64': base64.b64encode(content).decode(), 'idempotency_key': key, **extra})

def claim(m):
    return m.call('claim', '', {})

def publish(m, job):
    return m.call('publish', 'a', {**job, 'job_id': job['id'], 'segment_id': job['attempt_id'], 'chunk_count': 2})

def test_upload_idempotency_and_durable_original(manager):
    kb=create(manager); doc=upload(manager,kb)
    assert upload(manager,kb)['id']==doc['id']
    with pytest.raises(ValueError): upload(manager,kb,content=b'changed')
    other=Management(manager.database_url, manager.root, manager.secret_key)
    assert other.call('download','a',{'kb_id':kb,'document_id':doc['id']})['content_b64']==base64.b64encode(b'hello').decode()
    assert len(other.call('get','a',{'kb_id':kb})['jobs'])==1

def test_failed_build_retains_blob_and_cleanup_and_retry(manager):
    kb=create(manager); doc=upload(manager,kb); job=claim(manager)
    manager.call('fail','a',{'job_id':job['id'],'attempt_id':job['attempt_id'],'error':'chunking failed'})
    detail=manager.call('get','a',{'kb_id':kb})
    assert detail['documents'][0]['status']=='failed'
    assert manager.call('cleanup_list','',{})[0]['segment_id']==job['attempt_id']
    assert manager.call('download','a',{'kb_id':kb,'document_id':doc['id']})
    manager.call('retry','a',{'kb_id':kb,'document_id':doc['id']})
    assert claim(manager)['attempt_id'] != job['attempt_id']

def test_rebuild_keeps_active_and_cancel_fences_publish(manager):
    kb=create(manager); upload(manager,kb); old=claim(manager); publish(manager,old)
    manager.call('rebuild','a',{'kb_id':kb,'config':{'backend':'faiss','chunking':'paragraph'}}); new=claim(manager)
    assert manager.call('resolve','a',{'kb_id':kb})['config']['backend']=='faiss'
    manager.call('cancel','a',{'kb_id':kb})
    with pytest.raises(ValueError): publish(manager,new)
    assert manager.call('resolve','a',{'kb_id':kb})['version']==old['version']
    assert manager.call('build_status','',{'kb_id':kb,'segment_id':new['attempt_id']})=={'allowed':False}

def test_removed_and_foreign_sources_denied_and_delete_fences(manager):
    kb=create(manager); doc=upload(manager,kb); job=claim(manager); publish(manager,job)
    args={'kb_id':kb,'version':job['version'],'document_ids':[doc['id']]}
    assert manager.call('verify','a',args)=={'valid':True}
    with pytest.raises(KeyError): manager.call('verify','b',args)
    manager.call('remove_document','a',{'kb_id':kb,'document_id':doc['id']})
    with pytest.raises(KeyError): manager.call('verify','a',args)
    new=claim(manager); manager.call('delete','a',{'kb_id':kb})
    with pytest.raises(ValueError): publish(manager,new)
    with pytest.raises(KeyError): manager.call('download','a',{'kb_id':kb,'document_id':doc['id']})

def test_connection_encrypted_and_not_public(manager):
    kb=manager.call('create','a',{'name':'Private','config':{'backend':'pinecone'},'connection':{'api_key':'secret-value'}})['id']
    upload(manager,kb)
    assert claim(manager)['connection']['api_key']=='secret-value'
    assert 'secret-value' not in json.dumps(manager.call('get','a',{'kb_id':kb}))
    from pathlib import Path
    assert b'secret-value' not in Path(manager.database_url.removeprefix('sqlite:///')).read_bytes()

def test_retry_preserves_failed_rebuild_configuration(manager):
    kb=create(manager); doc=upload(manager,kb); publish(manager,claim(manager))
    manager.call('rebuild','a',{'kb_id':kb,'config':{'backend':'faiss','chunking':'paragraph'}})
    j=claim(manager)
    manager.call('fail','a',{'job_id':j['id'],'attempt_id':j['attempt_id'],'error':'offline'})
    manager.call('retry','a',{'kb_id':kb,'document_id':doc['id']})
    assert claim(manager)['config']['chunking']=='paragraph'

def test_superseding_upload_preserves_pending_config(manager):
    kb=create(manager); upload(manager,kb); publish(manager,claim(manager))
    manager.call('rebuild','a',{'kb_id':kb,'config':{'backend':'faiss','chunking':'paragraph'}})
    upload(manager,kb,key='two')
    assert claim(manager)['config']['chunking']=='paragraph'

def test_expired_lease_retries_with_new_attempt_and_fences_original(manager,monkeypatch):
    kb=create(manager); upload(manager,kb); old=claim(manager)
    import backend.app.kb.management as module
    current=module.time.time()
    monkeypatch.setattr(module.time,'time',lambda:current+31)
    new=claim(manager)
    assert new['attempt_id']!=old['attempt_id']
    with pytest.raises(ValueError): publish(manager,old)
    assert old['attempt_id'] in [x['segment_id'] for x in manager.call('cleanup_list','',{})]
    publish(manager,new)
    assert manager.call('resolve','a',{'kb_id':kb})['version']==new['version']

def test_cleanup_repeats_tombstone_after_success(manager,monkeypatch):
    kb=create(manager); upload(manager,kb); old=claim(manager)
    manager.call('cancel','a',{'kb_id':kb})
    manager.call('cleanup_result','',{'segment_id':old['attempt_id'],'ok':False,'error':'network'})
    assert manager.call('get','a',{'kb_id':kb})['jobs'][0]['cleanup_status']=='failed'
    manager.call('cleanup_result','',{'segment_id':old['attempt_id'],'ok':True})
    assert manager.call('cleanup_list','',{})==[]
    import backend.app.kb.management as module
    current=module.time.time()
    monkeypatch.setattr(module.time,'time',lambda:current+301)
    assert manager.call('cleanup_list','',{})==[{'kb_id':kb,'tenant_id':'a','segment_id':old['attempt_id'],'retain_provenance':False}]

def test_retained_provenance_survives_new_version(manager):
    kb=create(manager); doc=upload(manager,kb); old=claim(manager); publish(manager,old)
    upload(manager,kb,key='two'); publish(manager,claim(manager))
    assert manager.call('verify','a',{'kb_id':kb,'version':old['version'],'document_ids':[doc['id']]})=={'valid':True}

def test_worker_operations_are_tenant_bound(manager):
    kb=create(manager); doc=upload(manager,kb); j=claim(manager)
    for action,p in [('artifact',{'document_id':doc['id']}),('renew',{}),('progress',{'stage':'indexing'}),('fail',{'error':'oops'}),('publish',{'segment_id':j['attempt_id'],'chunk_count':1})]:
        with pytest.raises(KeyError):
            manager.call(action,'foreign',{'job_id':j['id'],'attempt_id':j['attempt_id'],**p})

@pytest.mark.parametrize('config',[{'chunk_size':99},{'chunk_size':8001},{'chunk_overlap':4001,'chunk_size':6000},{'storage_path':'x'*65},{'index_method':'invalid'},{'search_defaults':{'top_k':21}}])
def test_configuration_bounds(manager,config):
    with pytest.raises(ValueError): manager.call('create','a',{'name':'Invalid','config':config})

def test_concurrent_duplicate_uploads_create_one_document(manager):
    from concurrent.futures import ThreadPoolExecutor
    kb=create(manager)
    other=Management(manager.database_url,manager.root,manager.secret_key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(upload,manager,kb); b=pool.submit(upload,other,kb)
        assert a.result()['id']==b.result()['id']
    detail=manager.call('get','a',{'kb_id':kb})
    assert detail['document_count']==1 and len(detail['jobs'])==1

def test_claim_exhaustion_is_bounded_and_visible(manager,monkeypatch):
    import backend.app.kb.management as module
    kb=create(manager); upload(manager,kb); j=claim(manager)
    start=module.time.time()
    for n in range(1,4):
        monkeypatch.setattr(module.time,'time',lambda n=n:start+n*31)
        j=claim(manager)
    assert j is None
    detail=manager.call('get','a',{'kb_id':kb})
    assert detail['status']=='failed' and detail['documents'][0]['status']=='failed'
    assert len(manager.call('cleanup_list','',{}))==3

def test_failed_replacement_validation_does_not_remove_original(manager):
    kb=create(manager); doc=upload(manager,kb)
    with pytest.raises(ValueError): upload(manager,kb,key='replacement',content=b'',replace_document_id=doc['id'])
    assert manager.call('download','a',{'kb_id':kb,'document_id':doc['id']})
    assert manager.call('get','a',{'kb_id':kb})['document_count']==1

def test_publish_replay_is_safe_and_conflict_rejected(manager):
    kb=create(manager); upload(manager,kb); j=claim(manager)
    assert publish(manager,j)==publish(manager,j)=={'ok':True}
    with pytest.raises(ValueError): manager.call('publish','a',{'job_id':j['id'],'attempt_id':j['attempt_id'],'segment_id':j['attempt_id'],'chunk_count':3})

def test_read_operations_do_not_issue_updates(manager):
    from sqlalchemy import event
    kb=create(manager); statements=[]
    event.listen(manager.engine,'before_cursor_execute',lambda conn,cursor,statement,parameters,context,executemany:statements.append(statement))
    manager.call('get','a',{'kb_id':kb}); manager.call('list','a',{})
    assert not any(x.lstrip().upper().startswith('UPDATE') for x in statements)

def test_failed_replacement_keeps_active_original_and_retry_uses_new(manager):
    kb=create(manager); old=upload(manager,kb); first=claim(manager); publish(manager,first)
    new=upload(manager,kb,key='replace',content=b'new',replace_document_id=old['id'])
    pending=claim(manager)
    assert [d['id'] for d in pending['documents']]==[new['id']]
    assert [d['id'] for d in manager.call('resolve','a',{'kb_id':kb})['documents']]==[old['id']]
    assert manager.call('download','a',{'kb_id':kb,'document_id':old['id']})
    manager.call('fail','a',{'job_id':pending['id'],'attempt_id':pending['attempt_id'],'error':'offline'})
    docs={d['id']:d for d in manager.call('get','a',{'kb_id':kb})['documents']}
    assert docs[old['id']]['status']=='ready' and docs[new['id']]['status']=='failed'
    assert manager.call('verify','a',{'kb_id':kb,'version':first['version'],'document_ids':[old['id']]})=={'valid':True}
    manager.call('retry','a',{'kb_id':kb,'document_id':new['id']}); retried=claim(manager)
    assert [d['id'] for d in retried['documents']]==[new['id']]
    publish(manager,retried)
    assert [d['id'] for d in manager.call('resolve','a',{'kb_id':kb})['documents']]==[new['id']]
    with pytest.raises(KeyError): manager.call('download','a',{'kb_id':kb,'document_id':old['id']})
    with pytest.raises(KeyError): manager.call('verify','a',{'kb_id':kb,'version':first['version'],'document_ids':[old['id']]})

@pytest.mark.parametrize('remove_old',[False,True])
def test_removing_pending_replacement_or_original(manager,remove_old):
    kb=create(manager); old=upload(manager,kb); publish(manager,claim(manager))
    new=upload(manager,kb,key='replace',content=b'new',replace_document_id=old['id'])
    stale=claim(manager)
    manager.call('remove_document','a',{'kb_id':kb,'document_id':old['id'] if remove_old else new['id']})
    with pytest.raises(ValueError): publish(manager,stale)
    pending=claim(manager)
    assert [d['id'] for d in pending['documents']]==([] if remove_old else [old['id']])
    if not remove_old: assert manager.call('download','a',{'kb_id':kb,'document_id':old['id']})
    else:
        with pytest.raises(KeyError): manager.call('download','a',{'kb_id':kb,'document_id':new['id']})

def test_cancel_replacement_preserves_old(manager):
    kb=create(manager); old=upload(manager,kb); publish(manager,claim(manager))
    new=upload(manager,kb,key='replace',content=b'new',replace_document_id=old['id'])
    manager.call('cancel','a',{'kb_id':kb})
    docs={d['id']:d for d in manager.call('get','a',{'kb_id':kb})['documents']}
    assert docs[old['id']]['status']=='ready' and docs[new['id']]['status']=='failed'
    assert manager.call('download','a',{'kb_id':kb,'document_id':old['id']})

def test_filename_matches_search_limit(manager):
    kb=create(manager)
    with pytest.raises(ValueError): manager.call('upload','a',{'kb_id':kb,'filename':'a'*237+'.txt','content_b64':'aGk=','idempotency_key':'long'})

def test_failed_addition_and_rebuild_keep_published_documents_ready(manager):
    kb=create(manager); old=upload(manager,kb); publish(manager,claim(manager))
    new=upload(manager,kb,key='second',content=b'new')
    j=claim(manager)
    manager.call('fail','a',{'job_id':j['id'],'attempt_id':j['attempt_id'],'error':'embedding offline'})
    detail=manager.call('get','a',{'kb_id':kb}); docs={d['id']:d for d in detail['documents']}
    assert detail['status']=='degraded' and docs[old['id']]['status']=='ready' and docs[new['id']]['status']=='failed'
    manager.call('remove_document','a',{'kb_id':kb,'document_id':new['id']}); publish(manager,claim(manager))
    manager.call('rebuild','a',{'kb_id':kb,'config':{'backend':'faiss','chunking':'paragraph'}}); j=claim(manager)
    manager.call('fail','a',{'job_id':j['id'],'attempt_id':j['attempt_id'],'error':'offline'})
    assert manager.call('get','a',{'kb_id':kb})['documents'][0]['status']=='ready'

@pytest.mark.parametrize('target_pending',[False,True])
def test_superseding_a_replacement_keeps_original_until_publish(manager,target_pending):
    kb=create(manager); old=upload(manager,kb); publish(manager,claim(manager))
    pending=upload(manager,kb,key='replacement1',content=b'pending1',replace_document_id=old['id']); stale=claim(manager)
    latest=upload(manager,kb,key='replacement2',content=b'pending2',replace_document_id=pending['id'] if target_pending else old['id'])
    assert latest['replaces_document_id']==old['id']
    assert manager.call('download','a',{'kb_id':kb,'document_id':old['id']})
    with pytest.raises(ValueError): publish(manager,stale)
    with pytest.raises(KeyError): manager.call('download','a',{'kb_id':kb,'document_id':pending['id']})
    job=claim(manager)
    assert [d['id'] for d in job['documents']]==[latest['id']]
    publish(manager,job)
    assert manager.call('get','a',{'kb_id':kb})['document_count']==1


def test_cleanup_backoff_persists_and_delete_resets_schedule(manager,monkeypatch):
    import backend.app.kb.management as module
    kb=create(manager); upload(manager,kb); job=claim(manager); publish(manager,job)
    upload(manager,kb,key='second'); publish(manager,claim(manager))
    clock=[module.time.time()]
    monkeypatch.setattr(module.time,'time',lambda:clock[0])
    delays=[]
    for _ in range(9):
        assert manager.call('cleanup_list','',{})
        manager.call('cleanup_result','',{'segment_id':job['attempt_id'],'ok':False,'error':'offline'})
        reopened=Management(manager.database_url,manager.root,manager.secret_key)
        assert reopened.call('cleanup_list','',{})==[]
        detail=reopened.call('get','a',{'kb_id':kb})
        due=detail['jobs'][0]['next_cleanup_at'];delays.append(due-clock[0])
        clock[0]=due
    assert delays[0]>0 and delays[1]>delays[0] and max(delays)==300
    manager.call('cleanup_result','',{'segment_id':job['attempt_id'],'ok':True})
    assert manager.call('cleanup_list','',{})==[]
    manager.call('delete','a',{'kb_id':kb})
    due=manager.call('cleanup_list','',{})
    assert len(due)==2 and all(not x['retain_provenance'] for x in due)


def test_retired_chroma_rejected_with_recovery_message(manager):
    with pytest.raises(ValueError, match='Chroma.*FAISS'):
        manager.call('create','a',{'name':'Retired','config':{'backend':'chroma'}})
