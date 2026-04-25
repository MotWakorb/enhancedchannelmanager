"""
SQLAlchemy ``Base`` for ORM model declarations.

Split out from ``database`` so model modules (``models``, ``export_models``,
etc.) can import ``Base`` without forming an import cycle with
``database.init_db`` (which imports the model modules to register their
tables). See bead wlvxh for the topology rationale.

Everything else — engine, session factory, migrations, init hooks —
stays in ``database.py``. ``database`` re-exports ``Base`` so existing
``from database import Base`` call sites (e.g. ``ffmpeg_builder``) keep
working without a sweeping rewrite.
"""
from sqlalchemy.orm import declarative_base

Base = declarative_base()

__all__ = ["Base"]
