"""Alembic owns DDL. This module decides, explicitly, what state a database is in.

Two supported paths:

* empty database          -> upgrade to head creates everything
* existing Relay database -> structural verification -> baseline stamp -> upgrade

A database is never stamped on the strength of a table merely existing. It is compared
against the baseline the application declares, and anything missing, extra or mismatched
is a startup failure naming exactly what differs.

Schema state lives in alembic_version. Application data transformations keep their own
schema_migrations records: the two answer different questions and stay separate.
"""
from pathlib import Path
from sqlalchemy import inspect

BASELINE='0001_baseline'
MIGRATIONS=Path(__file__).resolve().parents[1]/'migrations'


def target_metadata():
    """Every feature table, registered by importing the modules that declare them."""
    from .storage import Base
    from . import readiness, operator_metrics, approvals, tool_service, agent_memory, auth  # noqa: F401
    return Base.metadata


# Alembic's env.py imports this name directly.
target_metadata=target_metadata()


def _config(connection):
    from alembic.config import Config
    config=Config()
    config.set_main_option('script_location',str(MIGRATIONS))
    config.attributes['connection']=connection
    return config


def current_revision(connection):
    from alembic.runtime.migration import MigrationContext
    return MigrationContext.configure(connection).get_current_revision()


def head_revision():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config=Config();config.set_main_option('script_location',str(MIGRATIONS))
    return ScriptDirectory.from_config(config).get_current_head()


def known_revisions():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config=Config();config.set_main_option('script_location',str(MIGRATIONS))
    return {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}


def baseline_metadata():
    """The frozen 0001 schema, never the live models.

    A database being adopted is stamped as 0001, so it must be compared and repaired
    against 0001. Comparing against current metadata would create tables at a later
    shape and then claim they are 0001, and the next migration would collide with them.
    """
    from backend.migrations.baseline_schema import metadata
    return metadata


def _column_signature(column):
    return {'type':type(column['type']).__name__.upper(),'nullable':bool(column['nullable'])}


def _declared_column_signature(column):
    return {'type':type(column.type).__name__.upper(),'nullable':bool(column.nullable)}


def schema_differences(connection,metadata=None):
    """Structural differences between the live database and the frozen baseline.

    Covers tables, columns, types, nullability, primary keys, unique constraints and
    indexes. A missing unique constraint is a correctness problem, not a performance
    one, so it is reported like any other mismatch rather than tolerated.
    """
    metadata=metadata or baseline_metadata()
    inspector=inspect(connection)
    live={name for name in inspector.get_table_names() if name!='alembic_version'}
    declared=set(metadata.tables)
    differences=[]
    for table in sorted(declared-live):differences.append(f'missing table {table}')
    for table in sorted(live-declared):differences.append(f'unexpected table {table}')
    for name in sorted(declared&live):
        table=metadata.tables[name]
        live_columns={column['name']:column for column in inspector.get_columns(name)}
        declared_columns=dict(table.columns.items())
        for column in sorted(set(declared_columns)-set(live_columns)):differences.append(f'missing column {name}.{column}')
        for column in sorted(set(live_columns)-set(declared_columns)):differences.append(f'unexpected column {name}.{column}')
        primary=[c.name for c in table.primary_key]
        for column in sorted(set(declared_columns)&set(live_columns)):
            want=_declared_column_signature(declared_columns[column])
            have=_column_signature(live_columns[column])
            # SQLite reports primary-key columns as nullable; a primary key is NOT NULL
            # by definition, so the key comparison below covers it instead.
            if column not in primary and want['nullable']!=have['nullable']:
                differences.append(f"{name}.{column} nullability is {have['nullable']}, expected {want['nullable']}")
            if not _types_match(want['type'],have['type']):
                differences.append(f"{name}.{column} type is {have['type']}, expected {want['type']}")
        want_pk=[c.name for c in table.primary_key]
        have_pk=list(inspector.get_pk_constraint(name).get('constrained_columns') or [])
        if want_pk!=have_pk:differences.append(f'{name} primary key is {have_pk or "none"}, expected {want_pk}')
        want_unique={tuple(sorted(c.name for c in constraint.columns))
                     for constraint in table.constraints if type(constraint).__name__=='UniqueConstraint'}
        have_unique={tuple(sorted(u['column_names'])) for u in inspector.get_unique_constraints(name)}
        have_unique|={tuple(sorted(i['column_names'])) for i in inspector.get_indexes(name) if i.get('unique')}
        for columns in sorted(want_unique-have_unique):
            differences.append(f'missing unique constraint on {name}({", ".join(columns)})')
        want_index={index.name for index in table.indexes}
        have_index={index['name'] for index in inspector.get_indexes(name)}
        for index in sorted(want_index-have_index):differences.append(f'missing index {index} on {name}')
    return differences


# SQLite reports a narrow set of affinities; treat the known equivalents as a match.
_TYPE_ALIASES={'STRING':{'VARCHAR','TEXT','STRING'},'TEXT':{'TEXT','VARCHAR','STRING'},
               'INTEGER':{'INTEGER','BIGINT','SMALLINT'},'FLOAT':{'FLOAT','REAL','NUMERIC','DOUBLE'},
               'BOOLEAN':{'BOOLEAN','INTEGER'},'JSON':{'JSON','TEXT','VARCHAR'}}


def _types_match(want,have):
    return have in _TYPE_ALIASES.get(want,{want})


def is_empty(connection):
    return not [name for name in inspect(connection).get_table_names() if name!='alembic_version']


def stamp(connection,revision=BASELINE):
    from alembic import command
    command.stamp(_config(connection),revision)


def upgrade(connection,revision='head'):
    from alembic import command
    command.upgrade(_config(connection),revision)


class SchemaStateError(RuntimeError):
    """The database cannot be used as-is, and guessing would risk the data in it."""


def prepare(connection,allow_upgrade=True):
    """Bring the database to head, or refuse with an actionable message.

    Returns the action taken, for the startup journal.
    """
    revision=current_revision(connection)
    head=head_revision()
    if revision is None:
        if is_empty(connection):
            upgrade(connection)
            return 'created'
        differences=schema_differences(connection)  # against the frozen 0001 baseline
        if differences:
            raise SchemaStateError(
                'This database does not match the expected baseline, so it cannot be adopted '
                'automatically. Differences: '+'; '.join(differences[:20])+
                ('; ...' if len(differences)>20 else '')+
                '. Back up the database, reconcile the schema, then retry.')
        stamp(connection)
        if allow_upgrade:upgrade(connection)
        return 'adopted'
    if revision==head:return 'current'
    if revision not in known_revisions():
        raise SchemaStateError(
            f'This database is at schema revision {revision}, which this build does not know. '
            'It was probably written by a newer Relay. Deploy that version, or restore a backup.')
    if not allow_upgrade:
        raise SchemaStateError(
            f'This database is at schema revision {revision} and head is {head}. '
            'Run: .venv/bin/python -m alembic upgrade head')
    upgrade(connection)
    return 'upgraded'
