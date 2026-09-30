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
    return {'type':type(column['type']).__name__.upper(),'nullable':bool(column['nullable']),
            'length':getattr(column['type'],'length',None)}


def _declared_column_signature(column):
    return {'type':type(column.type).__name__.upper(),'nullable':bool(column.nullable),
            'length':getattr(column.type,'length',None)}


def _describe(signature):
    return signature['type']+(f"({signature['length']})" if signature['length'] else '')


# Tables earlier Relay releases created and later retired: the legacy knowledge-base and
# VectorDB stores, whose models were removed in 4ec3f05 (2026-09-16). Databases created
# before then still contain them, some with user rows. Adoption recognises each one by
# its exact column set, taken from the removed models, and leaves it untouched: nothing
# is dropped, and a same-named table of any other shape is still refused.
RETIRED_TABLES={
    'knowledge_bases':frozenset({'id','tenant_id','name'}),
    'knowledge_documents':frozenset({'id','tenant_id','base_id','filename','content','status','error','pages','owner','lease_until','created'}),
    'knowledge_chunks':frozenset({'id','tenant_id','base_id','document_id','page','text'}),
    'vector_resources':frozenset({'id','tenant_id','name','config','dimensions'}),
    'vector_files':frozenset({'id','tenant_id','base_id','filename','content','status','error','index_key','pages','owner','lease_until','created'}),
    'vector_chunks':frozenset({'id','tenant_id','base_id','document_id','ordinal','page','text'}),
    'vector_segments':frozenset({'id','tenant_id','resource_id','document_id','next_cleanup'}),
}


def retired_tables(connection):
    """Live tables that exactly match a retired Relay table, and so are kept as they are."""
    inspector=inspect(connection)
    live=set(inspector.get_table_names())
    return sorted(name for name,columns in RETIRED_TABLES.items()
                  if name in live and {c['name'] for c in inspector.get_columns(name)}==columns)


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
    retired=set(retired_tables(connection))
    for table in sorted(live-declared-retired):
        if table in RETIRED_TABLES:
            have=sorted(c['name'] for c in inspector.get_columns(table))
            differences.append(f"table {table} has a retired Relay table's name but columns {have}, "
                               f"expected {sorted(RETIRED_TABLES[table])}")
        else:differences.append(f'unexpected table {table}')
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
            if not _types_match(want,have,connection.dialect.name):
                differences.append(f"{name}.{column} type is {_describe(have)}, expected {_describe(want)}")
        want_pk=[c.name for c in table.primary_key]
        have_pk=list(inspector.get_pk_constraint(name).get('constrained_columns') or [])
        if want_pk!=have_pk:differences.append(f'{name} primary key is {have_pk or "none"}, expected {want_pk}')
        want_unique={tuple(sorted(c.name for c in constraint.columns))
                     for constraint in table.constraints if type(constraint).__name__=='UniqueConstraint'}
        have_unique={tuple(sorted(u['column_names'])) for u in inspector.get_unique_constraints(name)}
        have_unique|={tuple(sorted(i['column_names'])) for i in inspector.get_indexes(name) if i.get('unique')}
        for columns in sorted(want_unique-have_unique):
            differences.append(f'missing unique constraint on {name}({", ".join(columns)})')
        # An unexpected uniqueness rule changes what the application may store, so it is
        # reported. An unexpected ordinary index only costs write time and is tolerated.
        for columns in sorted(have_unique-want_unique):
            differences.append(f'unexpected unique constraint on {name}({", ".join(columns)})')
        # Compare signatures, not names: an index with the right name over the wrong
        # column satisfies a name check while indexing nothing useful.
        want_index={(index.name,tuple(c.name for c in index.columns),bool(index.unique)) for index in table.indexes}
        have_index={(index['name'],tuple(index['column_names']),bool(index.get('unique'))) for index in inspector.get_indexes(name)}
        for signature in sorted(want_index-have_index):
            actual=next((h for h in have_index if h[0]==signature[0]),None)
            if actual is None:differences.append(f'missing index {signature[0]} on {name}')
            else:differences.append(f'index {signature[0]} on {name} covers {list(actual[1])} unique={actual[2]}, '
                                    f'expected {list(signature[1])} unique={signature[2]}')
    return differences


# SQLite stores by affinity and reports a narrow set of names, so JSON and Boolean
# columns come back as TEXT and INTEGER. PostgreSQL reports precisely, so it gets no
# such latitude: accepting TEXT where JSON is declared would hide a real mismatch.
_SQLITE_ALIASES={'STRING':{'VARCHAR','TEXT'},'TEXT':{'TEXT','VARCHAR'},
                 'INTEGER':{'INTEGER','BIGINT','SMALLINT'},'FLOAT':{'FLOAT','REAL','NUMERIC'},
                 'BOOLEAN':{'BOOLEAN','INTEGER'},'JSON':{'JSON','TEXT'}}
_ANSI_ALIASES={'STRING':{'VARCHAR','CHARACTER VARYING'},'TEXT':{'TEXT'},
               'INTEGER':{'INTEGER'},'FLOAT':{'FLOAT','DOUBLE PRECISION'},
               'BOOLEAN':{'BOOLEAN'},'JSON':{'JSON','JSONB'}}


def _types_match(want,have,dialect):
    aliases=_SQLITE_ALIASES if dialect=='sqlite' else _ANSI_ALIASES
    if have['type'] not in aliases.get(want['type'],{want['type']}):return False
    # A declared length is part of the contract; SQLite often reports none, which is
    # not a mismatch, but a different reported length is.
    if want['length'] and have['length'] and want['length']!=have['length']:return False
    return True


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
