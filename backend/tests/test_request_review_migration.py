from sqlalchemy import create_engine,text,inspect
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from backend.app import schema
import pytest
from sqlalchemy.exc import IntegrityError


def test_kind_migration_backfills_write_and_downgrades_without_losing_approval(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'migration.db'))
    with engine.begin() as conn:
        schema.upgrade(conn,'0003_loop_storage')
        conn.execute(text("INSERT INTO run_approvals (id,run_id,tenant_id,node_id,checkpoint_owner,invocation,payload,digest,execution,status,created_at,expires_at,owns_marker) VALUES ('a','r','local','send','send','send','{}','digest','encrypted','pending',1,100,0)"))
        schema.upgrade(conn)
        assert conn.execute(text('SELECT kind,invocation FROM run_approvals')).one()==('write','send')
        assert inspect(conn).get_columns('run_approvals')[0]
        assert compare_metadata(MigrationContext.configure(conn),schema.target_metadata)==[]
        command.downgrade(schema._config(conn),'0003_loop_storage')
        assert 'kind' not in {c['name'] for c in inspect(conn).get_columns('run_approvals')}
        assert conn.execute(text('SELECT id FROM run_approvals')).scalar()=='a'
        schema.upgrade(conn)
        assert conn.execute(text('SELECT kind FROM run_approvals')).scalar()=='write'
    engine.dispose()


def test_database_itself_rejects_unknown_approval_kinds(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'kind.db'))
    with engine.begin() as conn:
        schema.upgrade(conn)
        with conn.begin_nested():
            with pytest.raises(IntegrityError,match='ck_run_approval_kind'):
                conn.execute(text("INSERT INTO run_approvals (id,run_id,tenant_id,node_id,checkpoint_owner,invocation,kind,payload,digest,execution,status,created_at,expires_at,owns_marker) VALUES ('a','r','local','verify','verify','v','unknown','{}','digest','encrypted','pending',1,100,0)"))
    engine.dispose()


@pytest.mark.parametrize('kind,invocation',[('verification','verify:review:verification'),('decision','check:review:decision'),('write','x'*301)])
def test_downgrade_refuses_to_reinterpret_or_truncate_approval_records(tmp_path,kind,invocation):
    engine=create_engine('sqlite:///'+str(tmp_path/'refuse.db'))
    with engine.begin() as conn:
        schema.upgrade(conn,'0004_approval_kind')
        conn.execute(text("INSERT INTO run_approvals (id,run_id,tenant_id,node_id,checkpoint_owner,invocation,kind,payload,digest,execution,status,created_at,expires_at,owns_marker) VALUES ('a','r','local','verify','verify',:invocation,:kind,'{}','digest','encrypted','pending',1,100,0)"),{'invocation':invocation,'kind':kind})
    with engine.begin() as conn:
        with pytest.raises(RuntimeError,match='Export/archive'):command.downgrade(schema._config(conn),'0003_loop_storage')
        assert schema.current_revision(conn)=='0004_approval_kind'
        assert conn.execute(text('SELECT kind,invocation FROM run_approvals')).one()==(kind,invocation)
    engine.dispose()
