"""Alembic environment per Chess Lab.

Note importanti:
- L'URL del DB e il target_metadata sono presi da ``backend.db`` (unica fonte
  di verità dello schema), così ``CHESS_LAB_DB`` viene rispettata anche qui.
- ``render_as_batch=True`` è attivo fin da ora: qualsiasi MIGRATION FUTURA che
  faccia ALTER/DROP su queste tabelle DEVE girare in batch mode, perché il
  supporto ALTER TABLE di SQLite è molto limitato. Configurandolo adesso, le
  fasi successive non devono ricordarsi di aggiungerlo.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.db import DATABASE_URL, Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override dell'URL dal placeholder in alembic.ini con quello reale di db.py.
config.set_main_option("sqlalchemy.url", DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # obbligatorio per gli ALTER futuri su SQLite
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # obbligatorio per gli ALTER futuri su SQLite
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
