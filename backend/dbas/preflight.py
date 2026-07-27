"""Phase-2 DBAS restore PRE-FLIGHT validation.

Bead ``enhancedchannelmanager-0i2vt.18`` (part 1 of 2 — the other half is
``restore_orchestrator``). Dispatcharr has no database transactions (ADR-012),
so the cheapest way to avoid a half-applied restore is to refuse a restore that
is *known* to be inconsistent BEFORE issuing a single upstream write. This module
validates the ENTIRE import plan up front and returns a structured result; the
orchestrator refuses the restore — with ZERO mutation — when the result does not
pass.

What pre-flight checks (all read-only — it never mutates the destination):

* **schema-version gate** — delegates to ``routers.backup.validate_restore_schema_version``
  (bead ``…-0i2vt.17``) so an artifact from a newer ECM build is refused at the
  same chokepoint the upload path uses. This is the security/order guard the
  bead requires invoked at the orchestration entry.
* **FK references resolvable** — every FK reference an entity carries
  (a channel's ``channel_group_id`` / ``stream_profile_id``, a profile
  membership) must resolve to *something* in the plan: either another archived
  entity of that type (it will be created), or — via the
  :class:`~dbas.restore_contracts.IdRemapTable` — an entity already present on
  the destination. A reference to neither is a dangling FK that would fail
  mid-apply; pre-flight catches it now.
* **required-unique names unique** — within a category whose name must be unique
  (M3U accounts, channel groups, channel profiles), a duplicate name in the
  archive is refused: the second create would CONFLICT mid-run.
* **counts within bounds** — a category whose archived count exceeds a sane upper
  bound is refused as a likely-malformed / hostile archive rather than attempting
  a multi-thousand-entity apply that the rollback ledger would then have to undo.

The result is a structured pass/fail with a list of human-readable problems; the
orchestrator turns a failing pre-flight into a refused restore (no importer is
ever called). Pre-flight is PURE and synchronous — no client, no I/O — so it is
exhaustively unit-tested without mocks.

Conventions (``docs/style_guide.md``): ``str, Enum`` self-describing values;
Pydantic v2 ``BaseModel``; ``snake_case``; Google-style docstrings; lazy
``%``-formatted logging; NO secrets in any problem message (we only ever read an
entity's name / id, never its credentials).
"""

from __future__ import annotations

import logging
from enum import Enum

from pydantic import BaseModel, Field

from dbas.restore_contracts import EntityType, IdRemapTable

logger = logging.getLogger(__name__)

# Upper bound per category. A well-formed archive is comfortably under this; a
# count above it is treated as a malformed / hostile archive and refused before
# any write rather than handed to the apply path. Generous on purpose — this is a
# guardrail against absurd inputs, not a product limit.
DEFAULT_MAX_ENTITIES_PER_CATEGORY = 100_000

# Categories whose ``name`` must be unique on the destination. A duplicate name
# inside the archive would make the second create CONFLICT mid-apply, so it is a
# pre-flight failure, not a runtime surprise.
#
# Public (no leading underscore) so ``tasks.dbas_sync_engine`` can import the
# SAME set to degrade a source-side duplicate into a per-item CONFLICT instead
# of sync inheriting backup/restore's all-or-nothing refusal — one definition,
# so the two tolerance models can never drift apart on which categories they
# cover.
NAME_UNIQUE_CATEGORIES = frozenset(
    {EntityType.M3U_ACCOUNT, EntityType.CHANNEL_GROUP, EntityType.CHANNEL_PROFILE}
)


class PreflightProblemKind(str, Enum):
    """Why a pre-flight check failed.

    Self-describing values so a log line or operator-facing problem reads without
    the enum type for context (``docs/style_guide.md`` — naming discipline).
    """

    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    UNRESOLVED_FK_REFERENCE = "unresolved_fk_reference"
    DUPLICATE_UNIQUE_NAME = "duplicate_unique_name"
    COUNT_OUT_OF_BOUNDS = "count_out_of_bounds"


class PreflightProblem(BaseModel):
    """One pre-flight failure, with the category it concerns and a safe message.

    ``message`` is operator-facing and carries NO secrets — only names, ids, and
    counts. ``label`` is the offending entity's operator-facing identifier when
    the problem is entity-scoped (a dangling FK, a duplicate name); ``None`` for
    plan-scoped problems (schema version, count bound).
    """

    kind: PreflightProblemKind
    entity_type: EntityType | None = Field(
        default=None, description="The category the problem concerns; None if plan-scoped."
    )
    label: str | None = Field(
        default=None, description="Operator-facing entity identifier — never a secret."
    )
    message: str = Field(description="Sanitized, operator-facing detail. No secrets.")


class PreflightResult(BaseModel):
    """The structured outcome of pre-flight validation.

    ``passed`` is the single gate the orchestrator reads: ``True`` lets the apply
    proceed, ``False`` refuses the restore with no mutation. ``problems`` carries
    the reasons so the operator sees *why* a refusal happened.
    """

    passed: bool = Field(description="True only when there are zero problems.")
    problems: list[PreflightProblem] = Field(default_factory=list)

    @classmethod
    def from_problems(cls, problems: list[PreflightProblem]) -> PreflightResult:
        """Build a result whose ``passed`` is derived from the problem list."""
        return cls(passed=not problems, problems=problems)


class PlanCategory(BaseModel):
    """One category's slice of the import plan: the archived entities + selection.

    ``entities`` are the raw archive records (dicts) for this category.
    ``selected`` is the operator opt-in: an unselected category contributes
    nothing to the plan (no creates, so its FKs/names/counts are not validated —
    nothing in it will be applied).
    """

    entity_type: EntityType
    entities: list[dict] = Field(default_factory=list)
    selected: bool = True


class ImportPlan(BaseModel):
    """The whole restore plan handed to pre-flight and the orchestrator.

    ``manifest`` is the parsed artifact manifest (for the schema-version gate).
    ``categories`` is the per-category archive slice. ``existing_remap`` carries
    any source->dest mappings already known before the run (e.g. groups the
    operator pre-created on the destination); pre-flight treats an FK that
    resolves through it as satisfied even when the referenced entity is not in the
    archive.
    """

    manifest: dict = Field(default_factory=dict)
    categories: list[PlanCategory] = Field(default_factory=list)
    existing_remap: IdRemapTable = Field(default_factory=IdRemapTable)

    def category(self, entity_type: EntityType) -> PlanCategory | None:
        """Return the plan slice for ``entity_type``, or None if not in the plan."""
        for cat in self.categories:
            if cat.entity_type == entity_type:
                return cat
        return None

    def selected_categories(self) -> list[PlanCategory]:
        """Return only the categories the operator opted into."""
        return [c for c in self.categories if c.selected]


# FK references a channel record can carry, and the category each points at.
# Used to check every FK in the plan resolves to a will-be-created entity or an
# already-present destination entity. Keyed by the archive field name.
#
# Public (no leading underscore) so ``tasks.dbas_sync_engine`` can import the
# SAME mapping to remap a channel's FK away from a source-side duplicate that
# ``_split_name_conflicts`` excludes, onto the surviving same-named entity —
# one definition, so the FK-check surface and the FK-remap surface can never
# drift apart on which fields/categories they cover (mirrors
# ``NAME_UNIQUE_CATEGORIES`` above).
CHANNEL_FK_FIELDS: dict[str, EntityType] = {
    "channel_group_id": EntityType.CHANNEL_GROUP,
    "stream_profile_id": EntityType.STREAM_PROFILE,
}


def _label(entity: dict) -> str:
    """Operator-facing identifier for an arbitrary archive entity — never a secret."""
    for key in ("name", "username", "title"):
        value = entity.get(key)
        if value:
            return str(value)
    ident = entity.get("id")
    return f"<id={ident}>" if ident is not None else "<unknown>"


def _source_ids_in_plan(plan: ImportPlan, entity_type: EntityType) -> set[int]:
    """Source-export ids of every SELECTED archived entity of a type.

    These are the entities that WILL be created this run, so an FK pointing at one
    is satisfiable. An unselected category contributes nothing (it will not be
    created).
    """
    cat = plan.category(entity_type)
    if cat is None or not cat.selected:
        return set()
    ids: set[int] = set()
    for entity in cat.entities:
        ident = entity.get("id")
        if ident is not None:
            ids.add(int(ident))
    return ids


def _validate_schema_version(plan: ImportPlan, problems: list[PreflightProblem]) -> None:
    """Refuse a restore whose manifest schema_version is unsupported (.17 gate).

    Delegates to ``routers.backup.validate_restore_schema_version`` — the SAME
    comparator the upload chokepoint uses — so there is one version policy. The
    import is local to avoid a router-layer import at module load. The version
    detail is logged by the validator; the operator-facing problem message
    carries no version internals.
    """
    # Local import: the orchestration layer may import preflight before the web
    # layer is fully wired, and we keep the dependency direction one-way.
    from routers.backup import (
        UnsupportedBackupVersionError,
        validate_restore_schema_version,
    )

    try:
        validate_restore_schema_version(plan.manifest)
    except UnsupportedBackupVersionError as exc:
        problems.append(
            PreflightProblem(
                kind=PreflightProblemKind.UNSUPPORTED_SCHEMA_VERSION,
                message=str(exc),
            )
        )


def _validate_unique_names(plan: ImportPlan, problems: list[PreflightProblem]) -> None:
    """Refuse a category that carries a duplicate required-unique name.

    Within a name-unique category (M3U accounts, channel groups, channel
    profiles), two archived entities with the same case-insensitive, trimmed name
    would make the second create CONFLICT mid-apply. Caught here as a pre-flight
    failure so no partial state is created.
    """
    for cat in plan.selected_categories():
        if cat.entity_type not in NAME_UNIQUE_CATEGORIES:
            continue
        seen: set[str] = set()
        for entity in cat.entities:
            raw = entity.get("name")
            if not isinstance(raw, str):
                continue
            key = raw.strip().lower()
            if not key:
                continue
            if key in seen:
                problems.append(
                    PreflightProblem(
                        kind=PreflightProblemKind.DUPLICATE_UNIQUE_NAME,
                        entity_type=cat.entity_type,
                        label=_label(entity),
                        message=(
                            f"Duplicate {cat.entity_type.value} name in archive: "
                            f"'{raw}'. Names must be unique within this category."
                        ),
                    )
                )
            seen.add(key)


def _validate_counts(
    plan: ImportPlan,
    problems: list[PreflightProblem],
    max_per_category: int,
) -> None:
    """Refuse a category whose archived entity count exceeds the sane upper bound."""
    for cat in plan.selected_categories():
        count = len(cat.entities)
        if count > max_per_category:
            problems.append(
                PreflightProblem(
                    kind=PreflightProblemKind.COUNT_OUT_OF_BOUNDS,
                    entity_type=cat.entity_type,
                    message=(
                        f"{cat.entity_type.value} count {count} exceeds the maximum "
                        f"of {max_per_category}; refusing as a likely-malformed archive."
                    ),
                )
            )


def _validate_fk_references(plan: ImportPlan, problems: list[PreflightProblem]) -> None:
    """Refuse a restore whose plan carries an unresolvable FK reference.

    Currently validates channel FK references (``channel_group_id`` /
    ``stream_profile_id``). A reference is satisfied when its source id is either
    (a) the source id of a will-be-created entity of that type in the plan, or
    (b) resolvable through the plan's ``existing_remap`` (already present on the
    destination). Anything else is a dangling FK that would fail at apply time.

    The seam is deliberately keyed on a per-category FK-field map so additional
    categories' FK references (settings, DVR rules) register here as their
    importers land, without changing the orchestrator.
    """
    channels = plan.category(EntityType.CHANNEL)
    if channels is None or not channels.selected:
        return

    for archive_channel in channels.entities:
        for field, target_type in CHANNEL_FK_FIELDS.items():
            ref = archive_channel.get(field)
            if ref is None:
                continue
            ref_id = int(ref)
            in_plan = ref_id in _source_ids_in_plan(plan, target_type)
            on_dest = plan.existing_remap.resolve(target_type, ref_id) is not None
            if not in_plan and not on_dest:
                problems.append(
                    PreflightProblem(
                        kind=PreflightProblemKind.UNRESOLVED_FK_REFERENCE,
                        entity_type=EntityType.CHANNEL,
                        label=_label(archive_channel),
                        message=(
                            f"Channel '{_label(archive_channel)}' references "
                            f"{target_type.value} id={ref_id}, which is neither in "
                            f"the archive nor present on the destination."
                        ),
                    )
                )


def run_preflight(
    plan: ImportPlan,
    *,
    max_entities_per_category: int = DEFAULT_MAX_ENTITIES_PER_CATEGORY,
) -> PreflightResult:
    """Validate the entire import plan BEFORE any mutation; return pass/problems.

    Runs every check (schema version, FK resolvability, unique names, count
    bounds) and accumulates ALL problems rather than short-circuiting on the
    first — the operator sees every reason a restore was refused in one pass.

    Args:
        plan: The restore plan (manifest + per-category archive slices + any
            pre-known source->dest remap).
        max_entities_per_category: Upper bound per category; a count above it is a
            ``COUNT_OUT_OF_BOUNDS`` problem.

    Returns:
        A :class:`PreflightResult`. ``passed`` is ``True`` only when there are
        zero problems; the orchestrator refuses the restore (no mutation) when it
        is ``False``.
    """
    problems: list[PreflightProblem] = []
    _validate_schema_version(plan, problems)
    _validate_counts(plan, problems, max_entities_per_category)
    _validate_unique_names(plan, problems)
    _validate_fk_references(plan, problems)

    result = PreflightResult.from_problems(problems)
    if result.passed:
        logger.info("[DBAS-PREFLIGHT] Pre-flight passed; %d categor(ies) in plan.", len(plan.categories))
    else:
        logger.warning(
            "[DBAS-PREFLIGHT] Pre-flight FAILED with %d problem(s); restore will be refused with no mutation.",
            len(problems),
        )
    return result
