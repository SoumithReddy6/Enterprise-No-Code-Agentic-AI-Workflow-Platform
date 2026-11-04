import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from backend.app.kb.management import Management
from backend.app.kb.rpc import Client, app_for
from backend.app.kb_gateway import KnowledgeServices, install_kb_routes

@pytest.fixture
def manager(tmp_path):
    return Management(f'sqlite:///{tmp_path}/names.db', tmp_path, Fernet.generate_key())

@pytest.mark.parametrize('name', [' books  AND\tNotes ', 'BOOKS AND NOTES', 'Books\nAnd Notes'])
def test_normalized_duplicates_are_conflicts(manager, name):
    manager.call('create', 'a', {'name': 'Books and Notes'})
    with pytest.raises(ValueError) as caught:
        manager.call('create', 'a', {'name': name})
    assert caught.value.status_code == 409
    assert caught.value.detail['name'] == name


def test_rename_is_atomic_and_tenant_scoped_and_deleted_name_reusable(manager):
    first = manager.call('create', 'a', {'name': 'Straße'})
    second = manager.call('create', 'a', {'name': 'Other'})
    manager.call('create', 'b', {'name': 'STRASSE'})
    with pytest.raises(ValueError):
        manager.call('update', 'a', {'kb_id': second['id'], 'name': 'STRASSE', 'description': 'changed'})
    assert manager.call('get', 'a', {'kb_id': second['id']})['description'] == ''
    manager.call('update', 'a', {'kb_id': first['id'], 'name': 'STRASSE'})
    manager.call('delete', 'a', {'kb_id': first['id']})
    manager.call('update', 'a', {'kb_id': second['id'], 'name': 'Straße'})
    manager.call('delete', 'a', {'kb_id': second['id']})
    manager.call('create', 'a', {'name': 'STRASSE'})


def test_database_constraint_and_concurrent_creates(manager):
    other = Management(manager.database_url, manager.root, manager.secret_key)
    barrier = Barrier(2)
    def create(m):
        barrier.wait()
        try:
            m.call('create', 'a', {'name': 'Concurrent'})
            return 201
        except ValueError as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(create, [manager, other])) == [201, 409]
    with pytest.raises(IntegrityError), manager.engine.begin() as connection:
        connection.execute(text('INSERT INTO kb_management_bases (id, tenant_id, name_key, state) VALUES (:id, :tenant, :name, :state)'), {'id': 'bypass', 'tenant': 'a', 'name': 'concurrent', 'state': '{}'})


def legacy_database(tmp_path, names):
    url = f'sqlite:///{tmp_path}/legacy.db'
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE kb_management_bases (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(255) NOT NULL, state JSON NOT NULL)'))
        for ident, name, status in names:
            connection.execute(text('INSERT INTO kb_management_bases VALUES (:id, :tenant, :state)'), {'id': ident, 'tenant': 'a', 'state': json.dumps({'name': name, 'status': status})})
    engine.dispose()
    return url


def test_existing_database_migrates_and_enforces_constraint(tmp_path):
    url = legacy_database(tmp_path, [('one', '  Books\tAND Notes ', 'empty'), ('gone', 'books and notes', 'deleted')])
    manager = Management(url, tmp_path, Fernet.generate_key())
    with manager.engine.connect() as connection:
        assert dict(connection.execute(text('SELECT id, name_key FROM kb_management_bases')).all()) == {'one': 'books and notes', 'gone': None}
    with pytest.raises(IntegrityError), manager.engine.begin() as connection:
        connection.execute(text("INSERT INTO kb_management_bases VALUES ('duplicate', 'a', '{}', 'books and notes')"))


def test_legacy_duplicates_refuse_without_deleting_data(tmp_path):
    url = legacy_database(tmp_path, [('one', 'Books', 'empty'), ('two', 'BOOKS', 'empty')])
    with pytest.raises(RuntimeError, match='one.*two') as caught:
        Management(url, tmp_path, Fernet.generate_key())
    assert 'rename' in str(caught.value).lower()
    with create_engine(url).connect() as connection:
        assert connection.execute(text('SELECT COUNT(*) FROM kb_management_bases')).scalar() == 2


def test_rpc_and_gateway_preserve_conflict_and_submitted_name(manager):
    manager.call('create', 'a', {'name': 'Books'})
    other = manager.call('create', 'a', {'name': 'Other'})
    rpc = TestClient(app_for(manager, 'management', manager.ACTIONS, key='secret'))
    decoder = Client('management')
    class Transport:
        async def call(self, action, tenant, payload):
            return decoder.decode(rpc.post('/rpc', headers={'X-Relay-Service-Key': 'secret'}, json={'action': action, 'tenant': tenant, 'payload': payload}))
    app = FastAPI()
    install_kb_routes(app, None, lambda: 'a', KnowledgeServices(management=Transport()))
    with TestClient(app) as client:
        responses = [client.post('/api/knowledge-bases', json={'name': '  BOOKS  '}),
                     client.put('/api/knowledge-bases/' + other['id'], json={'name': '  BOOKS  '})]
    for response in responses:
        assert response.status_code == 409
        assert response.json()['detail']['name'] == '  BOOKS  '


def test_concurrent_renames_have_one_winner(manager):
    first = manager.call('create', 'a', {'name': 'First'})
    second = manager.call('create', 'a', {'name': 'Second'})
    other = Management(manager.database_url, manager.root, manager.secret_key)
    barrier = Barrier(2)
    def rename(item):
        service, kb = item
        barrier.wait()
        try:
            service.call('update', 'a', {'kb_id': kb['id'], 'name': 'Shared'})
            return 200
        except ValueError as exc:
            return exc.status_code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(rename, [(manager, first), (other, second)])) == [200, 409]
    names = [kb['name'] for kb in manager.call('list', 'a', {})]
    assert names.count('Shared') == 1
    assert len(set(names)) == 2
