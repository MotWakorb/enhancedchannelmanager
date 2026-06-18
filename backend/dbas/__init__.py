"""DBAS (Dispatcharr Backup And Sync) restore subsystem.

This package houses the Phase-2 restore building blocks that absorb the
standalone DBAS product into ECM (see ``docs/adr/ADR-012-dbas-absorption-approach.md``).

The first thing to land here are the *shared restore contracts*
(``restore_contracts.py``) — the three cross-bead data shapes that every
Phase-2 importer and every restore UX surface agrees on:

1. ``RestoreReport`` — the one response schema for dry-run, apply, and summary.
2. ``IdRemapTable`` — source-export-id -> destination-id, keyed by entity type.
3. ``RollbackLedger`` — the durable record of created entities for compensation.

See ``docs/dbas_restore_contracts.md`` for the design rationale and the
who-writes / who-reads / lifetime contracts.
"""
