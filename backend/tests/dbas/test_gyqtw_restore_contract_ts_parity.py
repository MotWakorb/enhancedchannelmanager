"""The TypeScript restore-contract mirrors must declare the fields their
Pydantic sources declare — in both directions (bead ``…-gyqtw``).

THE INVARIANT UNDER TEST, and ``RestoreReport`` is the EXAMPLE that provoked it,
not the specification:

    Every Pydantic model in ``dbas/restore_contracts.py`` that is mirrored by a
    hand-written ``export interface`` in ``frontend/src/services/api.ts``
    declares exactly the field set that interface declares. Neither side may
    carry a field the other does not.

WHY IT EXISTS. ``api.ts`` mirrors these contracts by hand — there is no
generator, and nothing derives one file from the other. The ``RestoreReport``
mirror silently fell **five fields and two interfaces** behind its Pydantic
source across three separate branches before anyone noticed
(``credential_reentry_details``, ``stream_url_redaction_details``,
``epg_link_miss_details``, ``profile_membership_drift_details`` and
``channel_group_drift_details``, plus the ``LogoMissChannel`` and
``ProviderGroupSelectionDetail`` interfaces). Drift in this direction is
invisible to ``tsc``: a response field the interface omits is simply
unreachable from the frontend, so the surface that was supposed to render it
renders nothing and every gate stays green. Commit ``35a49d84`` closed that
particular gap; this suite is what stops the fourth recurrence.

WHY IT ASSERTS SET EQUALITY AND NOT A COUNT. A field count passes while a field
is swapped for another, and it has to be edited on every legitimate addition —
which is how a guard becomes an obstacle and then gets deleted. Set equality in
both directions needs no edit when a field is added to both sides, and fails
naming the offender when it is added to only one.

WHY THE INVENTORY BELOW IS EXPLICIT. Deriving "is this model mirrored?" from a
name match would disarm itself the moment an interface is renamed: the model
would silently reclassify as backend-only and stop being checked. Two of the
fourteen mirrors are already named differently on the two sides
(``SkipDetail`` → ``RestoreSkipDetail``). So the mapping is written down, and
``test_every_contract_model_is_classified`` plus
``test_backend_only_models_have_no_typescript_mirror`` are what keep it honest
in both directions — a new model cannot appear unclassified, and a
backend-only model cannot quietly grow a mirror that skips the check.

RED-PROVEN by deleting ``epg_links_unrestored`` from the ``RestoreReport``
interface in ``api.ts`` and confirming
``test_mirrors_declare_the_same_fields`` fails naming that field.
"""
import inspect
import re
from pathlib import Path

from pydantic import BaseModel

import dbas.restore_contracts as restore_contracts

# backend/tests/dbas/... -> parents[2] = backend/, parents[3] = repo root.
_API_TS_PATH = (
    Path(__file__).resolve().parents[3] / "frontend" / "src" / "services" / "api.ts"
)

# Pydantic model name -> the ``export interface`` that mirrors it in api.ts.
# Most mirrors share the model's name; the two ``Restore``-prefixed entries are
# the reason this is a mapping and not a name-match rule.
_MIRRORS: dict[str, str] = {
    "RestoreReport": "RestoreReport",
    "EntityCategoryReport": "EntityCategoryReport",
    "SkipDetail": "RestoreSkipDetail",
    "FailureDetail": "RestoreFailureDetail",
    "LogoMissChannel": "LogoMissChannel",
    "LogoMissDetail": "LogoMissDetail",
    "CredentialReentryDetail": "CredentialReentryDetail",
    "ReattachPopulation": "ReattachPopulation",
    "StreamReattachDetail": "StreamReattachDetail",
    "StreamUrlRedactionDetail": "StreamUrlRedactionDetail",
    "EpgLinkMissDetail": "EpgLinkMissDetail",
    "ProfileMembershipDriftDetail": "ProfileMembershipDriftDetail",
    "ChannelGroupDriftDetail": "ChannelGroupDriftDetail",
    "ProviderGroupSelectionDetail": "ProviderGroupSelectionDetail",
    "AccountFieldDriftDetail": "AccountFieldDriftDetail",
}

# Models the restore engine never puts on the wire, so the frontend has no
# reason to describe them. Listed rather than inferred so that one of them
# GAINING a mirror is a test failure instead of a silent exemption.
_BACKEND_ONLY: frozenset[str] = frozenset(
    {
        "IdRemapTable",
        "LedgerEntry",
        "RollbackLedger",
    }
)


def _read_api_ts() -> str:
    # A missing file is a hard failure, not a skip: the whole point of this
    # suite is that it cannot go quiet while the mirrors drift.
    assert _API_TS_PATH.is_file(), (
        f"api.ts not found at {_API_TS_PATH} — if the frontend API client "
        f"moved, update this test's path so the parity gate keeps running"
    )
    return _API_TS_PATH.read_text(encoding="utf-8")


_API_TS = _read_api_ts()


def _strip_comments(body: str) -> str:
    """Drop ``/* ... */`` and ``// ...`` so prose cannot look like a field."""
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    return re.sub(r"//[^\n]*", "", body)


def _interface_header(interface: str) -> re.Pattern[str]:
    """Matches the opening line of ``export interface <interface> { ...``."""
    return re.compile(
        r"^export interface %s\s*(?P<extends>extends [^{]+)?\{" % re.escape(interface),
        flags=re.MULTILINE,
    )


def _ts_interface_fields(interface: str) -> set[str]:
    """Field names declared by ``export interface <interface> { ... }``."""
    matches = list(_interface_header(interface).finditer(_API_TS))
    assert len(matches) == 1, (
        f"expected exactly one 'export interface {interface}' in {_API_TS_PATH.name}, "
        f"found {len(matches)} — a rename or a duplicate declaration would leave "
        f"this parity check reading the wrong (or no) interface"
    )
    match = matches[0]
    assert not match.group("extends"), (
        f"'export interface {interface}' now extends another interface; this "
        f"parser reads only fields declared in the body, so inherited fields "
        f"would read as missing. Teach it inheritance before merging."
    )

    open_brace = _API_TS.index("{", match.start())
    depth = 0
    close_brace = -1
    for index in range(open_brace, len(_API_TS)):
        char = _API_TS[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                close_brace = index
                break
    assert close_brace != -1, (
        f"unbalanced braces while reading 'export interface {interface}'"
    )

    body = _strip_comments(_API_TS[open_brace + 1 : close_brace])
    fields = set(
        re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\??\s*:", body, flags=re.MULTILINE)
    )
    assert fields, (
        f"parsed zero fields out of 'export interface {interface}' — the parser "
        f"is broken, not the interface; a silently empty field set would make "
        f"this gate pass on nothing"
    )
    return fields


def _contract_models() -> dict[str, type[BaseModel]]:
    """Every Pydantic model DEFINED in ``dbas/restore_contracts.py``."""
    return {
        name: obj
        for name, obj in inspect.getmembers(restore_contracts, inspect.isclass)
        if issubclass(obj, BaseModel)
        and obj is not BaseModel
        and obj.__module__ == restore_contracts.__name__
    }


def test_every_contract_model_is_classified() -> None:
    """A new restore-contract model must declare whether the frontend mirrors it.

    Without this, adding a model that api.ts also describes would create an
    unguarded mirror — exactly the state RestoreReport was in for three
    branches.
    """
    classified = set(_MIRRORS) | _BACKEND_ONLY
    declared = set(_contract_models())

    unclassified = sorted(declared - classified)
    assert not unclassified, (
        f"restore_contracts.py declares model(s) {unclassified} that this parity "
        f"gate knows nothing about. Add each to _MIRRORS (with the name of its "
        f"'export interface' in api.ts) or to _BACKEND_ONLY."
    )

    stale = sorted(classified - declared)
    assert not stale, (
        f"this parity gate names model(s) {stale} that restore_contracts.py no "
        f"longer declares — remove them from _MIRRORS / _BACKEND_ONLY"
    )


def test_backend_only_models_have_no_typescript_mirror() -> None:
    """A backend-only model that grows a mirror must join the guarded set."""
    grew_a_mirror = sorted(
        name for name in _BACKEND_ONLY if _interface_header(name).search(_API_TS)
    )
    assert not grew_a_mirror, (
        f"api.ts now declares interface(s) {grew_a_mirror} for model(s) listed as "
        f"backend-only. Move them from _BACKEND_ONLY into _MIRRORS so their "
        f"fields are checked."
    )


def test_mirrors_declare_the_same_fields() -> None:
    """Both sides of every mirrored contract declare the same field set.

    Checked in BOTH directions: a field the frontend cannot see is a surface
    that renders nothing, and a field the backend never sends is a renderer
    reading ``undefined``. Reported in one aggregate so a drifted commit shows
    every offender at once rather than one per run.
    """
    models = _contract_models()
    drift: list[str] = []

    for model_name, interface_name in sorted(_MIRRORS.items()):
        python_fields = set(models[model_name].model_fields)
        typescript_fields = _ts_interface_fields(interface_name)

        missing_from_ts = sorted(python_fields - typescript_fields)
        missing_from_python = sorted(typescript_fields - python_fields)
        if missing_from_ts:
            drift.append(
                f"  {model_name} -> interface {interface_name}: declared in "
                f"Pydantic but MISSING FROM api.ts: {missing_from_ts}"
            )
        if missing_from_python:
            drift.append(
                f"  {model_name} -> interface {interface_name}: declared in "
                f"api.ts but MISSING FROM Pydantic: {missing_from_python}"
            )

    assert not drift, (
        "the hand-written TypeScript restore-contract mirrors have drifted from "
        "their Pydantic sources (bead …-gyqtw):\n" + "\n".join(drift)
    )
