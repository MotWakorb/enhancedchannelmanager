"""Structure budgets shared by YAML import/restore and the stricter rule editor.

Three independent bounds apply before any Python object is constructed:

* a composed-node ceiling, chosen by each caller for the size of document it
  legitimately accepts;
* an alias-amplification budget (expanded graph weight minus composed nodes),
  the same for every caller, so anchors and aliases cannot multiply work;
* a nesting depth, measured on the expanded graph so aliases cannot hide it.

This complements request, upload and archive byte limits; it is not a general
memory limit. Harmless acyclic aliases and merge keys retain SafeLoader semantics.
"""

import yaml

ALIAS_EXPANSION_BUDGET = 1_000_000
MAX_DEPTH = 64

# Backups carry whole Dispatcharr catalogs. A channel with three embedded
# streams composes to roughly 42 to 105 nodes, so this admits on the order of
# 100k to 240k channels per category document.
BACKUP_MAX_NODES = 10_000_000

# Legacy YAML exports carry ECM configuration and Dispatcharr accounts, groups
# and profiles, but never the channel catalog or logos. A real instance's
# largest legacy-side structures measured tens of thousands of nodes, so this
# leaves more than an order of magnitude of headroom.
LEGACY_MAX_NODES = 2_000_000

# Hand-maintained configuration (tags, normalization, dummy EPG, channel rules).
# Real exports are a few hundred nodes; this matches the rule editor's scale.
IMPORT_MAX_NODES = 100_000

# Character cap for YAML submitted in a JSON request body to the import routes.
# Real exports are a few KiB; 4 MiB stays above IMPORT_MAX_NODES at about 40
# characters per node, so the byte cap does not bind before the node ceiling
# for plausible documents.
IMPORT_MAX_CHARS = 4 * 1024 * 1024


class YamlSafetyError(yaml.YAMLError):
    """A budget refusal of well-formed YAML, never malformed YAML to skip.

    Attributes:
        reason: One of ``"nodes"``, ``"depth"``, ``"aliases"`` or ``"recursive"``.
        limit: The limit that was exceeded, or ``None`` for recursive aliases.
    """

    def __init__(self, reason, limit=None):
        self.reason = reason
        self.limit = limit
        super().__init__("YAML structure exceeds the safety limit (%s)" % self.describe())

    def describe(self):
        """Return a short operator-facing phrase for the exceeded limit."""
        if self.reason == "nodes":
            return "more than {:,} YAML nodes".format(self.limit)
        if self.reason == "depth":
            return "nesting deeper than {:,} levels".format(self.limit)
        if self.reason == "aliases":
            return "alias expansion of more than {:,} nodes".format(self.limit)
        return "recursive aliases"


class BoundedSafeLoader(yaml.SafeLoader):
    """Bound composition and the expanded alias graph before construction."""

    max_nodes = IMPORT_MAX_NODES
    max_depth = MAX_DEPTH
    alias_expansion_budget = ALIAS_EXPANSION_BUDGET
    allow_aliases = True

    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def structure_limit_error(self, mark, reason, limit):
        return YamlSafetyError(reason, limit)

    def compose_node(self, parent, index):
        self.nodes += 1
        self.depth += 1
        try:
            if not self.allow_aliases and self.check_event(yaml.AliasEvent):
                raise yaml.MarkedYAMLError(
                    problem="Aliases are not supported; copy the block instead",
                    problem_mark=self.peek_event().start_mark,
                )
            if self.depth > self.max_depth:
                raise self.structure_limit_error(
                    self.peek_event().start_mark, "depth", self.max_depth
                )
            if self.nodes > self.max_nodes:
                raise self.structure_limit_error(
                    self.peek_event().start_mark, "nodes", self.max_nodes
                )
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def check_graph(self, node):
        """Refuse a composed graph whose alias expansion exceeds the budgets.

        Graph nodes are memoized, but each alias edge contributes its target's
        full expanded weight, so an exponential DAG is refused without walking
        its expansion. Runs before SafeLoader's constructors and merge-key
        flattening can amplify the graph.
        """
        memo = {}
        active = set()
        expansion_ceiling = self.nodes + self.alias_expansion_budget

        def measure(current):
            identity = id(current)
            if identity in active:
                raise YamlSafetyError("recursive")
            if identity in memo:
                return memo[identity]
            active.add(identity)
            if isinstance(current, yaml.MappingNode):
                children = (child for pair in current.value for child in pair)
            elif isinstance(current, yaml.SequenceNode):
                children = iter(current.value)
            else:
                children = iter(())
            height, weight = 1, 1
            for child in children:
                child_height, child_weight = measure(child)
                height = max(height, child_height + 1)
                weight += child_weight
                if height > self.max_depth:
                    raise self.structure_limit_error(
                        current.start_mark, "depth", self.max_depth
                    )
                if weight > expansion_ceiling:
                    raise self.structure_limit_error(
                        current.start_mark, "aliases", self.alias_expansion_budget
                    )
            active.remove(identity)
            memo[identity] = height, weight
            return height, weight

        measure(node)

    def construct_document(self, node):
        self.check_graph(node)
        return super().construct_document(node)


def safe_load_bounded(stream, *, max_nodes, alias_expansion_budget=None):
    """Load one safe YAML document within the structure budgets.

    Args:
        stream: YAML text or bytes.
        max_nodes: The composed-node ceiling for this caller, normally
            ``IMPORT_MAX_NODES``, ``LEGACY_MAX_NODES`` or ``BACKUP_MAX_NODES``.
        alias_expansion_budget: Override for the shared alias budget. Only for
            text ECM generated itself in-process; external input never sets it.

    Returns:
        The constructed document.

    Raises:
        YamlSafetyError: The document exceeds a structure budget.
        yaml.YAMLError: The document is malformed, including invalid scalars.
    """
    loader = BoundedSafeLoader(stream)
    loader.max_nodes = max_nodes
    if alias_expansion_budget is not None:
        loader.alias_expansion_budget = alias_expansion_budget
    try:
        return loader.get_single_data()
    except ValueError as exc:
        # Timestamp/int constructors can raise ValueError rather than YAMLError.
        raise yaml.YAMLError("Invalid YAML scalar") from exc
    finally:
        loader.dispose()


def measure_bounded(stream, *, max_nodes):
    """Apply the same structure refusals as ``safe_load_bounded`` without loading.

    Composes the document and checks its alias graph, but constructs no Python
    objects. Used to prove a document ECM writes is one it would accept back.

    Args:
        stream: YAML text or bytes.
        max_nodes: The composed-node ceiling the eventual reader applies.

    Returns:
        The number of composed nodes.

    Raises:
        YamlSafetyError: The document exceeds a structure budget.
        yaml.YAMLError: The document is malformed.
    """
    loader = BoundedSafeLoader(stream)
    loader.max_nodes = max_nodes
    try:
        node = loader.get_single_node()
        if node is not None:
            loader.check_graph(node)
        return loader.nodes
    finally:
        loader.dispose()


def limit_message(subject, scope, exc):
    """Operator-facing refusal text, e.g. "Tag import exceeds the import size limit".

    Args:
        subject: What was refused: a route's payload, a section or a category.
        scope: ``"import"`` or ``"backup"``.
        exc: The ``YamlSafetyError`` that refused it.

    Returns:
        A message that never calls well-formed YAML invalid.
    """
    return "%s exceeds the %s size limit (%s)" % (subject, scope, exc.describe())


def _dumped_as_alias(obj):
    """Whether ``yaml.dump`` would emit a repeat of ``obj`` as an alias.

    Mirrors ``SafeRepresenter.ignore_aliases``: ``None``, the empty tuple,
    ``str``, ``bytes``, ``bool``, ``int`` and ``float`` are always written in
    full; every other object repeated by identity becomes an anchor and aliases.
    """
    if obj is None or (isinstance(obj, tuple) and obj == ()):
        return False
    return not isinstance(obj, (str, bytes, bool, int, float))


def measure_object(obj, *, max_nodes):
    """Apply ``measure_bounded``'s refusals to an object before it is dumped.

    Walks the object ``yaml.dump`` is about to serialize and counts exactly the
    nodes restore will compose from the result: one per mapping, sequence and
    scalar (mapping keys included), and one per alias for a container repeated
    by identity. Expanded weight and depth are computed the same way as
    :meth:`BoundedSafeLoader.check_graph`. Much cheaper than dumping and
    composing, and equivalence with ``measure_bounded`` is pinned by tests.

    Args:
        obj: The Python object about to be passed to ``yaml.dump``.
        max_nodes: The composed-node ceiling the eventual reader applies.

    Returns:
        The number of nodes restore will compose.

    Raises:
        YamlSafetyError: The dumped document would exceed a structure budget.
    """
    memo = {}
    active = set()
    composed = 0

    def children(current):
        if isinstance(current, dict):
            for key, value in current.items():
                yield key
                yield value
        elif isinstance(current, (set, frozenset)):
            for item in current:
                yield item
                yield None
        elif isinstance(current, (list, tuple)):
            yield from current

    def walk(current, depth):
        nonlocal composed
        composed += 1
        if depth > MAX_DEPTH:
            raise YamlSafetyError("depth", MAX_DEPTH)
        if composed > max_nodes:
            raise YamlSafetyError("nodes", max_nodes)
        aliasable = _dumped_as_alias(current)
        identity = id(current)
        if aliasable:
            if identity in active:
                raise YamlSafetyError("recursive")
            if identity in memo:
                return memo[identity]
            active.add(identity)
        height, weight = 1, 1
        for child in children(current):
            child_height, child_weight = walk(child, depth + 1)
            height = max(height, child_height + 1)
            weight += child_weight
        if aliasable:
            active.remove(identity)
            memo[identity] = height, weight
        return height, weight

    height, weight = walk(obj, 1)
    if height > MAX_DEPTH:
        raise YamlSafetyError("depth", MAX_DEPTH)
    if weight > composed + ALIAS_EXPANSION_BUDGET:
        raise YamlSafetyError("aliases", ALIAS_EXPANSION_BUDGET)
    return composed
