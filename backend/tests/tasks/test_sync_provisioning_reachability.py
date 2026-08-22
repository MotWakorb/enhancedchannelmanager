"""INV-2 — the one-time provisioning path is UNREACHABLE from a sync cycle.

Bead ``enhancedchannelmanager-wd20y``. ADR-013 amendment 2026-08-22 invariant
**INV-2**; threat model ``docs/security/threat_model_dbas_import.md`` §11.5 row
**D12**, and §11.5.4 item **1**, which names this file as a build gate.

WHY THIS IS A SECURITY CONTROL AND NOT A TIDINESS TEST
------------------------------------------------------
The PO ratified the HARVEST input model (S10) over the architect's recommended
operator-typed values. Under the typed design, making the cycle push credentials
was IMPOSSIBLE without first inventing somewhere to persist them, and INV-3 (no
provider credential persisted on A) would have blocked that. Under the harvest,
the values are already in the cycle's own memory —
``routers.backup._collect_credential_values`` walks the raw gather on every
scheduled run today, because that is what makes ``msqf7``'s literal-match
path-segment rule possible at all.

So making the cycle push credentials is now a CALL EDGE. No missing input, no
redesign, and — because nobody types the value — no operator keystroke whose
absence would make the change conspicuous. A later "auto-heal stale credentials"
convenience, or a refactor that registers provisioning as an importer step,
would silently convert the whole design into the recurring transmission S3
forbids, and the run report would look completely normal.

**INV-3 therefore no longer prevents a recurring push. INV-2 does, and INV-2
alone.** This file is that guarantee.

TWO HALVES, AND THE FIRST IS NOT SUFFICIENT ON ITS OWN
------------------------------------------------------
1. **A registry check** — provisioning is not an ``ImporterStep`` and is absent
   from :func:`tasks.dbas_sync_engine.sync_config_importer_steps`, in the idiom
   of the existing ``SYNC_NEVER_CATEGORIES`` test.
2. **A transitive import/reachability guard** — neither ``tasks.dbas_sync`` nor
   ``tasks.dbas_sync_engine`` can reach the provisioning module by ANY import
   path. A registry check alone is insufficient because the cycle already holds
   the values: a DIRECT CALL bypasses the registry entirely.

WHY A STATIC AST WALK RATHER THAN ``sys.modules``
-------------------------------------------------
A runtime check ("import the task, assert the writer is not in ``sys.modules``")
misses the most likely way this breaks: a LAZY import inside a function body,
which is the house pattern in this very subsystem (``routers/sync_targets.py``
imports the tasks package lazily because routers load first). A lazy import
never appears in ``sys.modules`` at import time and IS a call path. The walk
below therefore parses each module and collects EVERY ``import`` /
``from … import`` statement at any nesting depth, function bodies included.

THE GUARD PROVES ITSELF (engineering-discipline: enforcement code tests itself).
``test_walker_detects_a_lazy_function_level_import`` and
``test_walker_detects_a_transitive_edge`` plant the exact defect this guard
exists to catch, in a synthetic module tree, and assert it is reported. A guard
that passes the dangerous mutant is worse than no guard, because it reads as
coverage.
"""
import ast
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

# The module that WRITES a provider credential to a destination instance.
PROVISIONING_MODULE = "tasks.dbas_sync_provisioning"

# The two cycle entry points. ``tasks.dbas_sync`` is the schedulable/force-
# triggerable TaskScheduler wrapper; ``tasks.dbas_sync_engine`` is the engine it
# calls. Everything a scheduled sync can execute is reachable from one of them.
CYCLE_ROOTS = ("tasks.dbas_sync", "tasks.dbas_sync_engine")

# THE ONE PERMITTED IMPORTER, and the exception is narrower than it looks.
#
# ``routers.sync_targets`` is the operator-facing HTTP surface whose entire
# purpose is to expose the provisioning action, so of course it imports the
# writer. It appears in the cycle's import CLOSURE only because
# ``tasks.dbas_sync_engine`` imports ``routers.backup`` for the shipped redactor
# and gather, which loads the ``routers`` package, whose ``__init__`` aggregates
# every router into ``all_routers`` for ``main.py``. That is FastAPI application
# wiring, not a call path from a sync run.
#
# The exemption is kept honest by two tests below, not by this comment:
# ``test_the_allowed_importer_set_is_minimal_and_is_not_the_cycle`` refuses any
# entry that is not an HTTP surface or that no longer imports the writer, and
# ``test_no_cycle_module_imports_the_provisioning_route_module_directly`` turns
# red the moment a task, importer or engine module reaches this route module
# directly — which WOULD be a call path.
ALLOWED_IMPORTERS = frozenset({"routers.sync_targets"})


def _module_path(dotted: str) -> Path:
    """Resolve a dotted backend module name to its file, or ``None``."""
    candidate = BACKEND_DIR / Path(*dotted.split("."))
    if candidate.with_suffix(".py").is_file():
        return candidate.with_suffix(".py")
    package_init = candidate / "__init__.py"
    if package_init.is_file():
        return package_init
    return None


def _package_of(module: str, path: Path) -> str:
    """The package a relative import inside ``module`` resolves against."""
    return module if path.name == "__init__.py" else module.rpartition(".")[0]


def _imports_of(path: Path, module: str = "") -> set[str]:
    """Every dotted module name ``path`` imports, at ANY nesting depth.

    ``ast.walk`` descends into function and class bodies, so a lazy import
    inside a function — the house pattern in this subsystem, and the most
    likely way a forbidden edge would actually be written — is collected the
    same as a module-level one.

    RELATIVE IMPORTS ARE RESOLVED, not skipped. The ``auth`` package uses them
    (``from .dependencies import …``), and a walker that dropped them would have
    a silent blind spot exactly where a future edge could hide. ``module`` is the
    importing module's dotted name; when it is omitted (the synthetic red-proof
    fixtures) a relative import is recorded verbatim so it is still visible
    rather than vanishing.

    A ``from X import Y`` records BOTH ``X`` and ``X.Y``: when ``Y`` is a
    submodule (``from tasks import dbas_sync_provisioning``) the module name
    lives in the alias, not in ``node.module``. ``X.Y`` for a plain symbol
    import simply never resolves to a file and is dropped by the walk.
    """
    found: set[str] = set()
    tree = ast.parse(path.read_text())
    package = _package_of(module, path) if module else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                if not package:
                    found.add("." * node.level + base)
                    continue
                parts = package.split(".")
                trimmed = parts[: len(parts) - (node.level - 1)]
                base = ".".join([*trimmed, base]) if base else ".".join(trimmed)
                if not base:
                    continue
            if base:
                found.add(base)
                for alias in node.names:
                    found.add(f"{base}.{alias.name}")
    return found


def import_closure(
    roots, *, path_resolver=_module_path, importer=_imports_of
) -> dict[str, list[str]]:
    """Every backend module transitively importable from ``roots``.

    Returns a mapping of module -> the import chain that reached it, so a
    failure can name the actual edge rather than only the endpoint. Modules that
    do not resolve to a file in this backend (stdlib, third-party) are recorded
    as reached but not descended into.

    ``path_resolver`` / ``importer`` are seams so the guard can be run against a
    synthetic module tree in its own red-proof tests.
    """
    chains: dict[str, list[str]] = {}
    queue: list[tuple[str, list[str]]] = [(root, [root]) for root in roots]
    while queue:
        module, chain = queue.pop()
        if module in chains:
            continue
        chains[module] = chain
        path = path_resolver(module)
        if path is None:
            continue
        for imported in sorted(_call_importer(importer, path, module)):
            if imported in chains:
                continue
            queue.append((imported, [*chain, imported]))
    return chains


def _call_importer(importer, path: Path, module: str) -> set[str]:
    """Call ``importer`` with the module name when it accepts one.

    The synthetic red-proof fixtures supply a one-argument importer; the real
    walker takes the module name so it can resolve relative imports.
    """
    try:
        return importer(path, module)
    except TypeError:
        return importer(path)


def importers_of(target: str, roots=CYCLE_ROOTS) -> dict[str, list[str]]:
    """Every module in ``roots``' closure that imports ``target`` directly.

    Returns ``{importing module: the chain that reached it}``. This is the
    diagnostic half of the guard: naming WHICH module opened the edge is what
    turns a failure into a fix.
    """
    chains = import_closure(roots)
    offenders: dict[str, list[str]] = {}
    for module, chain in chains.items():
        path = _module_path(module)
        if path is None:
            continue
        if target in _imports_of(path, module):
            offenders[module] = chain
    return offenders


class TestProvisioningIsNotAnImporterStep:
    """Half one: the step registry contains no provisioning step."""

    def test_registry_has_no_provisioning_step(self):
        from tasks.dbas_sync_engine import sync_config_importer_steps

        steps = sync_config_importer_steps()
        offenders = [
            step
            for step in steps
            if "provision"
            in getattr(step.importer, "__qualname__", "").lower()
            or "provision" in getattr(step.importer, "__module__", "").lower()
        ]
        assert not offenders, (
            "a provisioning step is registered in the per-cycle importer "
            "registry — the one-time credential path has become recurring "
            "(ADR-013 INV-2 / threat model D12): %r" % offenders
        )

    def test_registry_entity_types_are_the_ratified_config_set(self):
        """A provisioning step could only enter as a NEW entity type; pin the set.

        Stated as the property rather than as the reproduction: the registry's
        entity types are exactly the ratified per-cycle set, so ANY addition —
        provisioning or otherwise — turns this red and has to be argued against
        S9 rather than slipped in.
        """
        from dbas.restore_contracts import EntityType
        from tasks.dbas_sync_engine import sync_config_importer_steps

        assert [s.entity_type for s in sync_config_importer_steps()] == [
            EntityType.USER_AGENT,
            EntityType.M3U_ACCOUNT,
            EntityType.EPG_SOURCE,
            EntityType.CHANNEL_GROUP,
            EntityType.CHANNEL_PROFILE,
            EntityType.STREAM_PROFILE,
            EntityType.CHANNEL,
            EntityType.LOGO,
        ]


class TestProvisioningIsUnreachableFromTheCycle:
    """Half two, and the load-bearing one: no call path, transitively."""

    def test_cycle_roots_resolve(self):
        """Smoke-test the instrument before trusting its silence.

        A guard that scans nothing reports clean. If either root stops
        resolving to a file — a rename, a package move — the walk below would
        silently cover nothing and pass forever.
        """
        for root in CYCLE_ROOTS:
            assert _module_path(root) is not None, (
                "cycle root %s does not resolve to a file; the INV-2 "
                "reachability guard would scan nothing and pass vacuously" % root
            )
        assert _module_path(PROVISIONING_MODULE) is not None, (
            "%s does not resolve to a file — the guard is asserting the "
            "absence of a module that does not exist" % PROVISIONING_MODULE
        )

    def test_no_sync_module_imports_the_provisioning_writer(self):
        """The property: nothing in the cycle opens an edge to the writer.

        Stated as an invariant rather than as one reproduction. Every module
        reachable from the cycle is checked, so the guard fires on a direct
        edge, a lazy function-level edge, and an edge hidden three helper
        modules deep — not only on the shape someone thought of.

        The ONE permitted importer is :data:`ALLOWED_IMPORTERS`: the HTTP
        surface that exists to expose the action. That exception is itself
        constrained by the two tests below — it must be minimal, and it must not
        be reachable from the cycle by anything except FastAPI app assembly.
        """
        offenders = {
            module: chain
            for module, chain in importers_of(PROVISIONING_MODULE).items()
            if module not in ALLOWED_IMPORTERS
        }
        assert not offenders, (
            "the one-time credential-provisioning writer is imported from "
            "inside the sync cycle:\n%s\n\n"
            "ADR-013 INV-2 / threat model D12: the cycle already holds every "
            "value a push would need (the harvest reads them on every run), so "
            "an import edge is the last structural thing preventing a recurring "
            "credential transmission. This is a security control, not a "
            "layering preference."
            % "\n".join(
                "  %s  (reached by %s)" % (m, " -> ".join(c))
                for m, c in sorted(offenders.items())
            )
        )

    def test_the_allowed_importer_set_is_minimal_and_is_not_the_cycle(self):
        """The exception may not grow, and may not be a cycle module.

        Two properties, both of which a stale or widened allowlist would break:

        * every entry must ACTUALLY import the writer (the stale-baseline idiom
          from ``test_ssrf_chokepoint_guard.py`` — a stale entry silently
          exempts a module that later regresses);
        * no entry may be a task, an importer or the engine. The exception
          exists for the operator-facing HTTP surface only. A ``tasks.*`` module
          on this list would BE the violation, wearing the exemption.
        """
        for module in ALLOWED_IMPORTERS:
            path = _module_path(module)
            assert path is not None, "allowed importer %s does not exist" % module
            assert PROVISIONING_MODULE in _imports_of(path, module), (
                "%s no longer imports the provisioning writer — prune it from "
                "ALLOWED_IMPORTERS, or it silently exempts a module that could "
                "regress" % module
            )
            assert module.startswith("routers."), (
                "%s is exempted from INV-2 but is not an HTTP surface. The "
                "exception exists for the route that exposes the action; a "
                "task, importer or engine module on this list IS the violation "
                "INV-2 forbids." % module
            )

    def test_no_cycle_module_imports_the_provisioning_route_module_directly(self):
        """The exempt route is reached only by FastAPI app assembly.

        This is what makes the exception honest rather than a hole. The route
        module is in the cycle's import closure ONLY because
        ``tasks.dbas_sync_engine`` imports ``routers.backup``, which loads the
        ``routers`` package, whose ``__init__`` aggregates every router into
        ``all_routers`` for ``main.py``. That is application wiring, not a call
        path from a sync run.

        If a task, importer or engine module ever imports the route module
        DIRECTLY, that is a real call path to the writer and this turns red.
        """
        for module in ALLOWED_IMPORTERS:
            direct = {
                importer
                for importer, _ in importers_of(module).items()
                if importer.split(".")[0] in ("tasks", "dbas")
            }
            assert not direct, (
                "%s is imported directly by cycle module(s) %r — that is a call "
                "path from a sync run to the provisioning writer, not FastAPI "
                "app assembly (ADR-013 INV-2)" % (module, sorted(direct))
            )

    def test_the_closure_actually_covered_the_cycle(self):
        """The walk must reach real cycle internals, or its silence means nothing.

        ``A check must be able to fail while the thing is broken``: a closure
        that resolved nothing would satisfy the assertion above trivially.
        """
        chains = import_closure(CYCLE_ROOTS)
        for expected in (
            "tasks.dbas_sync_engine",
            "tasks.dbas_sync_client",
            "dbas.restore_orchestrator",
            "dbas.importers.m3u_accounts",
            "routers.backup",
        ):
            assert expected in chains, (
                "INV-2 closure did not reach %s — the walk is not covering the "
                "cycle and its clean result proves nothing" % expected
            )

    def test_no_unresolved_relative_import_remains_in_the_closure(self):
        """Every relative import was RESOLVED, so the walk has no blind spot.

        The ``auth`` package imports relatively (``from .dependencies import …``)
        and is inside this closure. An unresolved entry (one still starting with
        a dot) would mean the walk stopped at a package boundary — and an edge
        hidden behind one would be invisible while the guard reported clean.
        """
        chains = import_closure(CYCLE_ROOTS)
        unresolved = sorted(m for m in chains if m.startswith("."))
        assert not unresolved, (
            "unresolved relative imports in the sync-cycle closure — the INV-2 "
            "walker stopped at a package boundary and would miss an edge hidden "
            "behind one: %r" % unresolved
        )
        # And the resolution actually produced real modules, not just silence.
        assert "auth.dependencies" in chains, (
            "relative-import resolution produced nothing reachable; the walk is "
            "passing vacuously"
        )


class TestTheGuardCatchesTheDangerousMutant:
    """Red-proof: plant the exact defect and confirm the walker reports it."""

    @staticmethod
    def _synthetic(graph):
        """Build (path_resolver, importer) over an in-memory module graph."""

        def resolver(name):
            return Path(name) if name in graph else None

        def importer(path):
            return set(graph[str(path)])

        return resolver, importer

    def test_walker_detects_a_direct_edge(self):
        graph = {
            "tasks.dbas_sync_engine": ["tasks.dbas_sync_provisioning"],
            "tasks.dbas_sync_provisioning": [],
        }
        resolver, importer = self._synthetic(graph)
        chains = import_closure(
            ["tasks.dbas_sync_engine"], path_resolver=resolver, importer=importer
        )
        assert PROVISIONING_MODULE in chains

    def test_walker_detects_a_transitive_edge(self):
        """Three hops — the shape a 'harmless helper module' refactor produces."""
        graph = {
            "tasks.dbas_sync": ["tasks.dbas_sync_engine"],
            "tasks.dbas_sync_engine": ["dbas.some_shared_helper"],
            "dbas.some_shared_helper": ["tasks.dbas_sync_provisioning"],
            "tasks.dbas_sync_provisioning": [],
        }
        resolver, importer = self._synthetic(graph)
        chains = import_closure(
            ["tasks.dbas_sync"], path_resolver=resolver, importer=importer
        )
        assert PROVISIONING_MODULE in chains
        assert chains[PROVISIONING_MODULE] == [
            "tasks.dbas_sync",
            "tasks.dbas_sync_engine",
            "dbas.some_shared_helper",
            "tasks.dbas_sync_provisioning",
        ]

    def test_walker_detects_a_lazy_function_level_import(self, tmp_path):
        """The realistic defect: a lazy import inside an auto-heal helper.

        This is the case a ``sys.modules`` check cannot see, and it is the house
        pattern in this subsystem — ``routers/sync_targets.py`` imports the tasks
        package lazily on purpose. Parsed from real source, not from a graph
        literal, so the AST walk itself is what is under test.
        """
        module = tmp_path / "engine.py"
        module.write_text(
            "import logging\n"
            "\n"
            "\n"
            "async def _auto_heal_stale_credentials(target):\n"
            "    from tasks.dbas_sync_provisioning import "
            "provision_target_credentials\n"
            "    return await provision_target_credentials(target)\n"
        )
        assert PROVISIONING_MODULE in _imports_of(module), (
            "the INV-2 walker missed a lazy, function-level import of the "
            "provisioning writer — the exact shape an 'auto-heal' convenience "
            "would take, and the one a sys.modules check cannot see"
        )

    def test_walker_detects_a_from_package_import_alias(self, tmp_path):
        """``from tasks import dbas_sync_provisioning`` names it in the alias."""
        module = tmp_path / "engine.py"
        module.write_text("from tasks import dbas_sync_provisioning\n")
        assert PROVISIONING_MODULE in _imports_of(module)

    def test_walker_is_not_vacuously_true(self, tmp_path):
        """A module that does NOT import the writer must not be reported."""
        module = tmp_path / "engine.py"
        module.write_text("from tasks.dbas_sync_client import make_remote_client\n")
        assert PROVISIONING_MODULE not in _imports_of(module)


class TestTheReverseEdgeIsAllowed:
    """The dependency runs one way, and the permitted way must actually work."""

    def test_provisioning_may_import_the_cycles_helpers(self):
        """Provisioning -> cycle is fine; cycle -> provisioning is not.

        Pinned so a future reader does not "fix" INV-2 by severing the wrong
        direction: reusing ``_build_create_payload`` and the shipped redactor is
        exactly what makes INV-6 true by construction.
        """
        chains = import_closure([PROVISIONING_MODULE])
        assert "dbas.importers.m3u_accounts" in chains
        assert "routers.backup" in chains
