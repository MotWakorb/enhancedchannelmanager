"""Structure budgets shared by YAML import/restore and the stricter rule editor.

This complements existing request/archive byte limits; it is not a general memory
limit. Harmless acyclic aliases and merge keys retain SafeLoader semantics.
"""

import yaml


class YamlSafetyError(yaml.YAMLError):
    """A fixed-message budget refusal, not malformed YAML to silently skip."""


class BoundedSafeLoader(yaml.SafeLoader):
    """Bound both composition and the expanded alias graph before construction."""

    max_nodes = 1_000_000
    max_depth = 64
    allow_aliases = True

    def __init__(self, stream):
        super().__init__(stream)
        self.depth = 0
        self.nodes = 0

    def structure_limit_error(self, mark):
        return YamlSafetyError("YAML structure exceeds the safety limit")

    def compose_node(self, parent, index):
        self.nodes += 1
        self.depth += 1
        try:
            if not self.allow_aliases and self.check_event(yaml.AliasEvent):
                raise yaml.MarkedYAMLError(
                    problem="Aliases are not supported; copy the block instead",
                    problem_mark=self.peek_event().start_mark,
                )
            if self.depth > self.max_depth or self.nodes > self.max_nodes:
                raise self.structure_limit_error(self.peek_event().start_mark)
            return super().compose_node(parent, index)
        finally:
            self.depth -= 1

    def construct_document(self, node):
        # Memoize graph nodes, but count each alias edge's expanded weight.
        # Reject cycles and exponential DAGs before SafeLoader's merge-key
        # flattening or downstream traversal can amplify them.
        memo = {}
        active = set()

        def measure(current):
            identity = id(current)
            if identity in active:
                raise YamlSafetyError("Recursive YAML aliases are not supported")
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
                if height > self.max_depth or weight > self.max_nodes:
                    raise self.structure_limit_error(current.start_mark)
            active.remove(identity)
            memo[identity] = height, weight
            return height, weight

        measure(node)
        return super().construct_document(node)


def safe_load_bounded(stream):
    """Load one safe YAML document within the import/restore structure budget."""
    try:
        return yaml.load(stream, Loader=BoundedSafeLoader)
    except ValueError as exc:
        # Timestamp/int constructors can raise ValueError rather than YAMLError.
        raise yaml.YAMLError("Invalid YAML scalar") from exc
