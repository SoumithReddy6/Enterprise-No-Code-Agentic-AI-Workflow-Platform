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
    """A database as it existed before Alembic: tables present, no alembic_version.

    Built from the frozen 0001 snapshot rather than live models, because that is what a
    pre-Alembic database actually contains - and because a test that simulates a later
    revision must not accidentally build its objects into the 'old' database.
    """
    engine = create_engine(f'sqlite:///{path}')
    with engine.begin() as conn:
        schema.baseline_metadata().create_all(conn)
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


# --------------------------------------------------------------------------- frozen baseline

def write_revision_0002(path):
    """A realistic second revision: one new table and one new column."""
    path.write_text('''"""later

Revision ID: 0002_later
Revises: 0001_baseline
"""
from alembic import op
import sqlalchemy as sa

revision = '0002_later'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('run_budgets',
        sa.Column('run_id', sa.String(length=64), primary_key=True),
        sa.Column('tokens', sa.Integer(), nullable=False))
    with op.batch_alter_table('runs') as batch:
        batch.add_column(sa.Column('budget_note', sa.String(length=64), nullable=True))


def downgrade():
    with op.batch_alter_table('runs') as batch:
        batch.drop_column('budget_note')
    op.drop_table('run_budgets')
''')


@pytest.fixture
def with_second_revision(tmp_path, monkeypatch):
    """Simulates a real 0002: both the migration and the model change it describes.

    Adding only the migration would be a toothless test - live metadata and the frozen
    baseline would still agree, so repairing from the wrong one would go unnoticed.

    The migration environment is copied into tmp_path first. Writing a revision into the
    repository's own versions directory would make a second pytest process, a scenario
    run or a live API briefly observe a 0002 that does not exist.
    """
    import shutil
    import sqlalchemy as sa
    sandbox = tmp_path / 'migrations'
    shutil.copytree(schema.MIGRATIONS, sandbox,
                    ignore=shutil.ignore_patterns('__pycache__'))
    monkeypatch.setattr(schema, 'MIGRATIONS', sandbox)
    target = sandbox / 'versions' / '0002_later.py'
    write_revision_0002(target)
    live = Base.metadata
    table = sa.Table('run_budgets', live,
                     sa.Column('run_id', sa.String(length=64), primary_key=True),
                     sa.Column('tokens', sa.Integer(), nullable=False))
    runs = live.tables['runs']
    note = sa.Column('budget_note', sa.String(length=64), nullable=True)
    runs.append_column(note)
    try:
        yield
    finally:
        live.remove(table)
        runs._columns.remove(note)


def test_legacy_adoption_survives_a_later_revision(tmp_path, with_second_revision):
    """The blocker this design exists for.

    Adoption must repair to the frozen 0001 shape and stamp 0001, so that 0002 then
    applies cleanly. Repairing with live metadata would create 0002's table early and
    the migration would collide on it.
    """
    assert schema.head_revision() == '0002_later', 'fixture did not take effect'
    url = legacy_database(tmp_path / 'v1.db')
    store = Store(url, Fernet.generate_key())
    with store.engine.begin() as conn:
        assert schema.current_revision(conn) == '0002_later'
    names = inspect(store.engine).get_table_names()
    assert 'run_budgets' in names, '0002 did not apply'
    assert 'budget_note' in {c['name'] for c in inspect(store.engine).get_columns('runs')}
    assert [w['name'] for w in store.workflows('local')] == ['Kept']


def test_the_frozen_baseline_does_not_drift_with_the_models(with_second_revision):
    """0001's snapshot must not acquire 0002's objects."""
    frozen = set(schema.baseline_metadata().tables)
    assert 'run_budgets' not in frozen
    assert 'budget_note' not in schema.baseline_metadata().tables['runs'].columns


# --------------------------------------------------------------------------- structure

def test_a_missing_unique_constraint_is_refused(tmp_path):
    """Uniqueness is correctness, not performance: adoption must not paper over it."""
    url = legacy_database(tmp_path / 'nouniq.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE allowed_models'))
        conn.execute(text('CREATE TABLE allowed_models (id VARCHAR(64) PRIMARY KEY, '
                          'tenant_id VARCHAR(64) NOT NULL, provider VARCHAR(24) NOT NULL, '
                          'model VARCHAR(100) NOT NULL, credential_id VARCHAR(128) NOT NULL, '
                          'enabled BOOLEAN NOT NULL)'))
    engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(url, Fernet.generate_key())
    assert 'missing unique constraint on allowed_models' in str(exc.value)


def test_a_wrong_column_type_is_refused(tmp_path):
    url = legacy_database(tmp_path / 'wrongtype.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE agent_memories'))
        conn.execute(text('CREATE TABLE agent_memories (tenant_id VARCHAR(64), '
                          'key VARCHAR(120), messages INTEGER NOT NULL)'))
    engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(url, Fernet.generate_key())
    assert 'agent_memories.messages type is INTEGER' in str(exc.value)


def test_a_missing_index_is_repaired_during_adoption(tmp_path):
    """Non-unique indexes are additive and lossless, so adoption adds them."""
    url = legacy_database(tmp_path / 'noindex.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('DROP INDEX ix_runs_status'))
    engine.dispose()
    store = Store(url, Fernet.generate_key())
    assert 'ix_runs_status' in {i['name'] for i in inspect(store.engine).get_indexes('runs')}


# --------------------------------------------------------------------------- parity

def test_the_frozen_snapshot_matches_revision_0001_itself(tmp_path):
    """Without this, the snapshot and the fixtures built from it could drift together.

    Build a database by running 0001, then compare its real structure against the frozen
    metadata. Anything that differs means the snapshot no longer describes the revision
    it claims to, and every adoption decision made from it would be wrong.
    """
    engine = create_engine(f'sqlite:///{tmp_path}/rev0001.db')
    with engine.begin() as conn:
        schema.upgrade(conn, '0001_baseline')
    with engine.connect() as conn:
        assert schema.current_revision(conn) == '0001_baseline'
        assert schema.schema_differences(conn) == []


def test_a_wrong_index_column_is_refused(tmp_path):
    """The right name over the wrong column indexes nothing useful."""
    url = legacy_database(tmp_path / 'badindex.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('DROP INDEX ix_runs_status'))
        conn.execute(text('CREATE INDEX ix_runs_status ON runs (name)'))
    engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(url, Fernet.generate_key())
    assert "index ix_runs_status on runs covers ['name']" in str(exc.value)


def test_an_unexpected_unique_constraint_is_refused(tmp_path):
    """An extra uniqueness rule changes what the application may store."""
    url = legacy_database(tmp_path / 'extrauniq.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('CREATE UNIQUE INDEX ux_runs_name ON runs (name)'))
    engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(url, Fernet.generate_key())
    assert 'unexpected unique constraint on runs(name)' in str(exc.value)


def test_a_shorter_column_is_refused(tmp_path):
    """Declared length is part of the contract: a narrower column silently truncates."""
    url = legacy_database(tmp_path / 'short.db')
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE agent_memories'))
        conn.execute(text('CREATE TABLE agent_memories (tenant_id VARCHAR(8), '
                          'key VARCHAR(120), messages JSON NOT NULL)'))
    engine.dispose()
    with pytest.raises(schema.SchemaStateError) as exc:
        Store(url, Fernet.generate_key())
    assert 'agent_memories.tenant_id type is VARCHAR(8)' in str(exc.value)
