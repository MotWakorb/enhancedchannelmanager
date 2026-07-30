"""drop the lookup_tables table (Lookup Tables feature retired)

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-30 12:00:00.000000

Bead: enhancedchannelmanager-70u0r.1 (epic 70u0r, PO decision D2 — full
removal of Lookup Tables including the ``|lookup:<table>`` template pipe).

WHAT IS BEING DROPPED
    ``lookup_tables`` — operator-authored named key→value tables. Its only
    producer was the ``/api/lookup-tables`` CRUD router (deleted) driven by
    the *Settings → Lookup Tables* UI (deleted); its only consumer was the
    dummy-EPG template engine's ``|lookup:<table>`` pipe (deleted).

*** THIS MIGRATION IS DESTRUCTIVE AND THE DATA IS OPERATOR-AUTHORED. ***

    The Lookup Tables destination shipped as a fully reachable Settings page
    in every release from v0.16.0 (2026-05-12) through v0.18.0 (2026-07-26),
    so ANY instance upgrading across this revision may hold rows. Rows are
    NOT recoverable by ``downgrade`` — see "Reversibility" below. The
    operator-facing pre-upgrade export instructions live in
    ``docs/user_guide/upgrade-notes.md``.

    As a safety net for operators who upgrade without reading the note, this
    migration writes any rows it is about to delete to a JSON file beside the
    database (``<db_dir>/lookup_tables_dropped_0041.json``) and logs the path
    at WARNING. The dump is BEST-EFFORT: a read-only or full config volume
    makes it fail, and a failed dump must never block the schema change, so
    every failure is caught and logged rather than raised. Treat the dump as a
    convenience, not as the backup — the backup is the operator's pre-upgrade
    export or their DBAS/ZIP backup.

WHY THE PIPE WENT WITH IT (the divergence that justified full removal)
    ``_resolve_lookups`` in ``routers/dummy_epg.py`` was called from exactly
    two places — ``POST /api/dummy-epg/preview`` and ``.../preview/batch``.
    ``generate_xmltv()`` neither accepted nor forwarded a lookups dict: it
    called ``generate_channel_xml`` with six positional args, leaving the
    seventh (``lookups``) at its ``None`` default. So a template containing
    ``{x|lookup:y}`` resolved correctly in PREVIEW while the generated XMLTV
    took ``render_template``'s ``TemplateSyntaxError`` fallback and emitted the
    RAW template text verbatim as the programme ``<title>``/``<desc>``.
    Measured, not assumed.

    Consequence worth stating for upgrade planning: a grandfathered profile
    still holding ``{x|lookup:y}`` produces the SAME generated XMLTV after
    this change as before it (raw-template fallback, now via "unknown
    transform" instead of "unknown lookup table"). Only the preview changes —
    it now agrees with the XMLTV instead of contradicting it.

Idempotency
    Both directions are inspect-guarded, so re-running either is a no-op. This
    matters for installs that drifted forward via ``create_all()`` (whose ORM
    no longer declares the model, so the table is never re-materialised).

Reversibility
    ``downgrade()`` recreates the table and its ``idx_lookup_table_name``
    index with the exact baseline (revision 0001) schema, so the schema is
    fully reversible — but the ROWS ARE NOT RESTORED. This follows the
    house precedent set by revision 0029 (FFMPEG-Builder / Export-tab table
    drops). A downgrade leaves an empty table; recovering the data means
    re-importing the JSON dump above, a pre-upgrade export, or a DBAS backup.
"""
import json
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

# revision identifiers, used by Alembic.
revision: str = "0041"
down_revision: Union[str, Sequence[str], None] = "0040"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "lookup_tables"
INDEX = "idx_lookup_table_name"
DUMP_BASENAME = "lookup_tables_dropped_0041.json"


def _dump_path(connection) -> "str | None":
    """Return a filesystem path for the pre-drop JSON dump, or None.

    Derived from the live SQLite connection's own database file so the dump
    always lands beside the DB being migrated (the real ``/config`` on a
    deployed instance, a tmp dir under test) rather than at a guessed
    ``CONFIG_DIR``. Returns None for non-file databases (``:memory:``), where
    there is nowhere meaningful to write.
    """
    import os

    url = connection.engine.url
    db_path = url.database
    if not db_path or db_path == ":memory:":
        return None
    directory = os.path.dirname(os.path.abspath(db_path))
    if not os.path.isdir(directory):
        return None
    return os.path.join(directory, DUMP_BASENAME)


def _dump_rows_before_drop(connection) -> None:
    """Best-effort JSON dump of every ``lookup_tables`` row, then log.

    Never raises: a failed dump must not block the schema change (see the
    module docstring). Emits WARNING when rows exist so the loss is visible in
    the upgrade log even for an operator who never read the upgrade note.
    """
    try:
        rows = connection.execute(
            text(
                "SELECT id, name, description, entries, created_at, updated_at "
                f"FROM {TABLE} ORDER BY id"
            )
        ).mappings().all()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[0041] Could not read %s before dropping it: %s", TABLE, exc)
        return

    if not rows:
        logger.info("[0041] %s is empty — dropping with no data loss.", TABLE)
        return

    payload = []
    for row in rows:
        # ``entries`` is a JSON-encoded dict[str, str]; decode it so the dump is
        # directly re-importable. Keep the raw text when it will not parse.
        raw_entries = row["entries"]
        try:
            entries = json.loads(raw_entries) if raw_entries else {}
        except (ValueError, TypeError):
            entries = None
        payload.append({
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "entries": entries,
            "entries_raw": raw_entries if entries is None else None,
            "created_at": str(row["created_at"]) if row["created_at"] else None,
            "updated_at": str(row["updated_at"]) if row["updated_at"] else None,
        })

    path = _dump_path(connection)
    if path is None:
        logger.warning(
            "[0041] Dropping %s with %d row(s); no on-disk database to write a "
            "dump beside, so the rows are NOT being preserved.",
            TABLE, len(payload),
        )
        return

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {"revision": revision, "table": TABLE, "rows": payload},
                handle, indent=2, ensure_ascii=False, sort_keys=False,
            )
    except OSError as exc:
        logger.warning(
            "[0041] Dropping %s with %d row(s); the pre-drop dump to %s FAILED "
            "(%s). The rows are being deleted and are recoverable only from a "
            "pre-upgrade export or backup.",
            TABLE, len(payload), path, exc,
        )
        return

    logger.warning(
        "[0041] Dropping %s (%d row(s)). The Lookup Tables feature is retired "
        "(bead enhancedchannelmanager-70u0r.1). A JSON copy of the deleted rows "
        "was written to %s — nothing in ECM reads it; keep it if you want the "
        "data. See docs/user_guide/upgrade-notes.md.",
        TABLE, len(payload), path,
    )


def upgrade() -> None:
    """Dump then drop ``lookup_tables`` (inspect-guarded, idempotent)."""
    conn = op.get_bind()
    if not inspect(conn).has_table(TABLE):
        return
    _dump_rows_before_drop(conn)
    op.drop_table(TABLE)


def downgrade() -> None:
    """Recreate ``lookup_tables`` with its baseline (0001) schema.

    Schema only — the dropped rows are gone (see module docstring). Guarded so
    re-running is a no-op.
    """
    conn = op.get_bind()
    if inspect(conn).has_table(TABLE):
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("entries", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.create_index(INDEX, ["name"], unique=False)
