"""channel_watch_stats_v

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-13 11:30:00.000000

Creates the ``channel_watch_stats_v`` SQL view — a read-compat surface over
``session_telemetry`` that exposes the column subset which faithfully maps
from per-poll telemetry rows to the legacy ``channel_watch_stats`` aggregate
shape.

Why a SCOPED-DOWN view (bd-skqln.3 step (b) decision):

The legacy ``channel_watch_stats`` table has five user-visible columns:

  channel_id, channel_name, watch_count, total_watch_seconds, last_watched

Of those, three faithfully map from ``session_telemetry`` and two do not:

* **Map faithfully**:
  - ``channel_id`` — direct passthrough (both are Dispatcharr UUID strings
    post-migration-0007).
  - ``last_watched`` — ``MAX(observed_at)`` converted from ms-since-epoch
    to a DATETIME-comparable string via ``datetime(observed_at/1000,
    'unixepoch')``. Matches the legacy ``DateTime`` column shape for
    ``WHERE last_watched >= ?`` queries.
  - ``total_watch_seconds`` — sum of distinct poll intervals per channel.
    Legacy ``_update_watch_time`` adds ``self.poll_interval`` seconds once
    per still-active channel per poll, regardless of client count. The
    naive ``SUM(poll_interval_ms) / 1000 GROUP BY channel_id`` over
    ``session_telemetry`` would multiply by client count (one row per
    connection per poll), so we first DISTINCT-fy by
    ``(channel_id, observed_at)`` inside a subquery before summing.

* **Do NOT map faithfully — deliberately omitted from the view**:
  - ``channel_name`` — ``session_telemetry`` does not store the channel
    name. Synthesising NULL/empty here would break consumers that filter
    or display by name. Step (d) (which repoints the popularity calculator
    onto this view) will fetch names from ``channels``/``Channel`` separately
    or via a join — that is a step-(d) refactor problem, not a view problem.
  - ``watch_count`` — legacy semantic is a *state-transition counter*:
    ``_update_watch_counts`` increments by 1 each time a channel goes from
    inactive→active across two polls. That is fundamentally not derivable
    from a per-poll observation stream (per-poll rows don't carry the
    "newly-active" bit). ``COUNT(DISTINCT session_id)`` would approximate
    "distinct viewing sessions on this channel" but that is a different
    metric — using it to populate ``watch_count`` would produce silently
    different scores in the popularity calculator. Step (d) reworks the
    popularity formula to use poll-derived metrics directly; until then,
    consumers that need state-transition ``watch_count`` continue reading
    the legacy ``channel_watch_stats`` table.

The scoped-down approach is the **honest** read-equivalence: the view
exposes only what genuinely maps, and the step-(b) regression test
asserts equivalence on those columns only. The PM accepted this trade-off
when the (a)→(b) handoff surfaced the schema-modeling debt (bd-skqln.3
step-(a) post-amend report).

SQLite VIEW reversibility:

``CREATE VIEW`` / ``DROP VIEW`` are supported in SQLite (no ALTER VIEW;
the view is stateless so we recreate it on every upgrade). The view has
no row data of its own — it is a saved query — so up/down round-trip is
trivially safe.

Bead: ``enhancedchannelmanager-skqln.3`` step (b).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


# SQL for the view. Held as a module-level constant so the upgrade DDL and
# the regression-test SQL can reference the same string — drift between
# the two is the most common source of "the view works in the migration
# test but not in the equivalence test" failure modes.
CHANNEL_WATCH_STATS_V_SQL = """
CREATE VIEW channel_watch_stats_v AS
SELECT
    per_poll.channel_id AS channel_id,
    CAST(SUM(per_poll.poll_interval_ms) / 1000 AS INTEGER) AS total_watch_seconds,
    datetime(MAX(per_poll.observed_at) / 1000, 'unixepoch') AS last_watched
FROM (
    -- DISTINCT-fy by (channel_id, observed_at) so a channel with N
    -- concurrent clients in one poll contributes only one poll interval
    -- to total_watch_seconds — matches legacy _update_watch_time which
    -- adds self.poll_interval once per channel per still-active poll
    -- regardless of client count.
    SELECT
        channel_id,
        observed_at,
        MAX(poll_interval_ms) AS poll_interval_ms
    FROM session_telemetry
    GROUP BY channel_id, observed_at
) AS per_poll
GROUP BY per_poll.channel_id
"""


def upgrade() -> None:
    """Create the channel_watch_stats_v view."""
    op.execute(CHANNEL_WATCH_STATS_V_SQL)


def downgrade() -> None:
    """Drop the channel_watch_stats_v view."""
    op.execute("DROP VIEW IF EXISTS channel_watch_stats_v")
