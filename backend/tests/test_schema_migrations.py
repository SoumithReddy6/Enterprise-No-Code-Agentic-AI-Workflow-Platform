"""Alembic is the single authority for DDL, and adoption never guesses.

Covers the two supported initialisation paths, every startup state, and the invariant
that an unrecognised schema is a refusal rather than a best effort.
"""
import json
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text
from backend.app import schema
from backend.app.storage import Store, Base


def legacy_database(path):
    """A database as it existed before Alembic: tables present, no alembic_version."""
    engine = create_engine(f'sqlite:///{path}')
    with engine.begin() as conn:
        Base.metadata.create_all(conn)
        conn.execute(text("INSERT INTO workflows (id,tenant_id,document,updated_at) "
                          "VALUES ('w1','local',:doc,'2026-01-01')"),
                     {'doc': json.dumps({'version': 1, 'name': 'Kept', 'nodes': [], 'edges': []})})
    engine.dispose()
    return f'sqlite:///{path}'


# --------------------------------------------------------------------------- paths

def test_empty_database_is_created_at_head(tmp_path):
    store = Store(f'sqlite:///{tmp_path}/new.db', Fernet.generate_key())
    names = inspect(store.engine).get_table_names()
    assert 'alembic_version' in names
    with store.engine.begin() as conn:
        assert schema.current_revision(conn) == schema.head_revision()


def test_existing_database_is_verified_then_adopted_without_losing_data(tmp_path):
    url = legacy_database(tmp_path / 'legacy.db')
    store = Store(url, Fernet.generate_key())
    with store.engine.begin() as conn:
        assert schema.current_revision(conn) == schema.head_revision()
    assert [w['name'] for w in store.workflows('local')] == ['Kept']


def test_reopening_a_current_database_is_a_no_op(tmp_path):
    key = Fernet.generate_key()
    Store(f'sqlite:///{tmp_path}/x.db', key)
    store = Store(f'sqlite:///{tmp_path}/x.db', key)
    with store.engine.begin() as conn:
        assert schema.current_revision(conn) == schema.head_revision()


# --------------------------------------------------------------------------- refusals

def test_a_database_that_does_not_match_the_baseline_is_refused(tmp_path):
    """Adoption compares structure. An unexpected object is actionable, not ignored."""
    url = legacy_database(tmp_path / 'odd.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE leftover_v1 (id VARCHAR(8))'))
    engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(url, Fernet.generate_key())
    assert 'unexpected table leftover_v1' in str(exc.value)
    assert 'Back up the database' in str(exc.value)


def test_an_absent_table_is_created_during_adoption(tmp_path):
    """Creating a table an older build never had is lossless, and is exactly what the
    pre-Alembic startup did. Only what that step cannot fix becomes a refusal."""
    url = legacy_database(tmp_path / 'partial.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE agent_memories'))
    engine.dispose()
    store = Store(url, Fernet.generate_key())
    assert 'agent_memories' in inspect(store.engine).get_table_names()
    assert [w['name'] for w in store.workflows('local')] == ['Kept']


def test_a_column_the_adoption_step_cannot_add_is_refused(tmp_path):
    """create_all never alters an existing table, so a column missing here is a genuine
    mismatch: refuse with the exact difference rather than starting on a broken schema."""
    url = legacy_database(tmp_path / 'column.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        # Rebuild agent_memories without one of its declared columns.
        conn.execute(text('DROP TABLE agent_memories'))
        conn.execute(text('CREATE TABLE agent_memories (tenant_id VARCHAR(64), key VARCHAR(120))'))
    engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(url, Fernet.generate_key())
    assert 'missing column agent_memories.messages' in str(exc.value)


def test_an_unknown_revision_refuses_startup(tmp_path):
    key = Fernet.generate_key()
    store = Store(f'sqlite:///{tmp_path}/future.db', key)
    with store.engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num='9999_from_the_future'"))
    store.engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(f'sqlite:///{tmp_path}/future.db', key)
    message = str(exc.value)
    assert '9999_from_the_future' in message and 'newer Relay' in message


def test_behind_head_without_auto_upgrade_names_the_command(tmp_path, monkeypatch):
    """A deployment that does not migrate on boot must say exactly what to run."""
    key = Fernet.generate_key()
    Store(f'sqlite:///{tmp_path}/behind.db', key)
    monkeypatch.setattr(schema, 'head_revision', lambda: '0002_later')
    monkeypatch.setattr(schema, 'known_revisions', lambda: {'0001_baseline', '0002_later'})
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(f'sqlite:///{tmp_path}/behind.db', key, auto_upgrade=False)
    assert 'alembic upgrade head' in str(exc.value)


# --------------------------------------------------------------------------- authority

def test_models_and_migrations_agree(tmp_path):
    """Autogenerate against a freshly migrated database must produce nothing.

    This is the check that catches a table added to the models without a revision.
    """
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    engine = create_engine(f'sqlite:///{tmp_path}/diff.db')
    with engine.begin() as conn:
        schema.upgrade(conn)
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={'compare_type': True})
        diff = compare_metadata(context, schema.target_metadata)
    ignorable = {'add_index', 'remove_index'}
    material = [d for d in diff if (d[0] if isinstance(d, tuple) else d[0][0]) not in ignorable]
    assert not material, material


def test_downgrade_then_reupgrade_restores_head(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/cycle.db')
    with engine.begin() as conn:
        schema.upgrade(conn)
    from alembic import command
    with engine.begin() as conn:
        command.downgrade(schema._config(conn), 'base')
        assert schema.current_revision(conn) is None
    with engine.begin() as conn:
        schema.upgrade(conn)
        assert schema.current_revision(conn) == schema.head_revision()


def test_every_declared_table_is_created_by_a_migration(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path}/all.db')
    with engine.begin() as conn:
        schema.upgrade(conn)
    live = {n for n in inspect(engine).get_table_names() if n != 'alembic_version'}
    assert live == set(schema.target_metadata.tables), live ^ set(schema.target_metadata.tables)
