"""Alembic environment for Enhanced Channel Manager.

Loads ECM's SQLAlchemy metadata (``models.py``, ``export_models.py``,
``ffmpeg_builder.persistence``) and the runtime SQLite engine so that
``alembic upgrade head`` and ``alembic revision --autogenerate`` operate
against the same schema the application itself uses.

See ``docs/database_migrations.md`` for authoring conventions, including
the SQLite batch-mode rule for column alters.
"""
from __future__ import annotations

import logging
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Path setup: ensure ``backend/`` is importable whether alembic is invoked
# from the repo root or from the container working directory.
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Default CONFIG_DIR to the in-container location if not set so that
# ``database.JOURNAL_DB_FILE`` resolves correctly when alembic is invoked
# from CI or local shells that haven't exported the value.
os.environ.setdefault("CONFIG_DIR", "/config")

# Import after path is set. These modules register every ORM table on the
# shared ``database.Base.metadata`` object via import side effects.
import database  # noqa: E402  pylint: disable=wrong-import-position
import models  # noqa: E402,F401  pylint: disable=wrong-import-position
import export_models  # noqa: E402,F401  pylint: disable=wrong-import-position
from ffmpeg_builder import persistence as _ffmpeg_persistence  # noqa: E402,F401

# The combined metadata Alembic compares against live DB for autogenerate.
target_metadata = database.Base.metadata

# ---------------------------------------------------------------------------
# Alembic config plumbing
# ---------------------------------------------------------------------------
config = context.config

# Allow override via env var (CI, tests) then fall back to the runtime DB URL.
env_url = os.environ.get("ALEMBIC_DATABASE_URL")
if env_url:
    config.set_main_option("sqlalchemy.url", env_url)
elif not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", database.get_database_url())

if config.config_file_name is not None:
    # Skip re-loading logging config when the app has already installed its
    # own. ``fileConfig`` (even with ``disable_existing_loggers=False``)
    # resets the root logger level to whatever is declared in
    # ``alembic.ini`` (WARNING), which silences every log line below
    # WARNING emitted during and after the migration — including the
    # request-end INFO log line produced by the observability middleware
    # on ``ecm.access``. The symptom surfaces in tests as missing
    # ``trace_id`` entries in captured JSON output (see
    # ``tests/routers/test_observability_middleware.py::TestTraceIdMiddleware``),
    # but the same override would dampen production logs whenever
    # ``database.init_db()`` runs ``command.upgrade(...)`` at startup.
    #
    # Heuristic: if the root logger already has handlers attached, assume
    # the embedding application has configured logging and we should not
    # clobber it. Standalone ``alembic`` CLI invocations start with no root
    # handlers, so they still pick up the ini file's formatting.
    if not logging.getLogger().handlers:
        fileConfig(config.config_file_name, disable_existing_loggers=False)


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection (``alembic upgrade --sql``)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the configured database connection."""
    # ``database`` module already registers a global SQLAlchemy ``connect``
    # listener that applies ``PRAGMA foreign_keys=ON`` and ``journal_mode=WAL``
    # on every SQLite connection (see ``docs/database_migrations.md``). That
    # listener attaches to the ``Engine`` class, so engines created here
    # inherit the PRAGMAs automatically — no per-env.py listener needed.
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
