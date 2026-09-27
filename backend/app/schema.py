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


def schema_differences(connection):
    """Structural differences between the live database and the declared baseline.

    Compares table and column names only. Type comparison belongs to alembic's
    autogenerate check in CI, which has the full dialect context this does not.
    """
    inspector=inspect(connection)
    live={name for name in inspector.get_table_names() if name!='alembic_version'}
    declared=set(target_metadata.tables)
    differences=[]
    for table in sorted(declared-live):differences.append(f'missing table {table}')
    for table in sorted(live-declared):differences.append(f'unexpected table {table}')
    for table in sorted(declared&live):
        live_columns={column['name'] for column in inspector.get_columns(table)}
        declared_columns=set(target_metadata.tables[table].columns.keys())
        for column in sorted(declared_columns-live_columns):differences.append(f'missing column {table}.{column}')
        for column in sorted(live_columns-declared_columns):differences.append(f'unexpected column {table}.{column}')
    return differences


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
        differences=schema_differences(connection)
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
