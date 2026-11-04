"""Alembic environment. The application's metadata is the single source of schema truth."""
from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.app.schema import target_metadata  # Imports every feature table.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline():
    context.configure(url=config.get_main_option('sqlalchemy.url'),
                      target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = config.attributes.get('connection', None)
    if connectable is None:
        connectable = engine_from_config(config.get_section(config.config_ini_section, {}),
                                         prefix='sqlalchemy.', poolclass=pool.NullPool)
        with connectable.connect() as connection:
            _run(connection)
    else:
        _run(connectable)


def _run(connection):
    # render_as_batch lets SQLite rewrite tables for ALTER operations it cannot perform.
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
