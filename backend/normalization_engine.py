"""
Normalization Rule Engine

Processes stream names through configurable rules to normalize them for
channel creation and matching. Supports regex patterns, multiple condition
types, and various transformation actions.

The engine loads rules from the database, organized into groups with
priority ordering. Rules execute in order until a match with stop_processing
is found, or all rules have been evaluated.
"""
import re
import logging
from typing import Optional
from dataclasses import dataclass

from sqlalchemy.orm import Session

from models import NormalizationRule, NormalizationRuleGroup

logger = logging.getLogger(__name__)


@dataclass
class RuleMatch:
    """Result of a rule match attempt."""
    matched: bool
    match_start: int = -1
    match_end: int = -1
    groups: tuple = ()  # Captured groups for regex


@dataclass
class NormalizationResult:
    """Result of normalizing a stream name."""
    original: str
    normalized: str
    rules_applied: list  # List of rule IDs that were applied
    transformations: list  # List of (rule_id, before, after) tuples


class NormalizationEngine:
    """
    Rule-based stream name normalization engine.

    Loads rules from the database and applies them in priority order.
    Groups are processed in priority order (lower first), and within
    each group, rules are processed in priority order.
    """

    def __init__(self, db: Session):
        self.db = db
        self._rules_cache: Optional[list] = None
        self._groups_cache: Optional[list] = None

    def invalidate_cache(self):
        """Clear cached rules to force reload from database."""
        self._rules_cache = None
        self._groups_cache = None

    def _load_rules(self) -> list[tuple[NormalizationRuleGroup, list[NormalizationRule]]]:
        """
        Load all enabled rules from database, organized by group.
        Returns list of (group, rules) tuples ordered by group priority.
        """
        if self._rules_cache is not None and self._groups_cache is not None:
            return list(zip(self._groups_cache, self._rules_cache))

        # Load enabled groups ordered by priority
        groups = (
            self.db.query(NormalizationRuleGroup)
            .filter(NormalizationRuleGroup.enabled == True)
            .order_by(NormalizationRuleGroup.priority)
            .all()
        )

        result = []
        all_groups = []
        all_rules = []

        for group in groups:
            # Load enabled rules for this group, ordered by priority
            rules = (
                self.db.query(NormalizationRule)
                .filter(
                    NormalizationRule.group_id == group.id,
                    NormalizationRule.enabled == True
                )
                .order_by(NormalizationRule.priority)
                .all()
            )
            result.append((group, rules))
            all_groups.append(group)
            all_rules.append(rules)

        self._groups_cache = all_groups
        self._rules_cache = all_rules

        return result

    def _match_single_condition(
        self,
        text: str,
        condition_type: str,
        pattern: str,
        case_sensitive: bool = False
    ) -> RuleMatch:
        """
        Check if text matches a single condition.
        Returns RuleMatch with match details.
        """
        # Prepare text for matching
        match_text = text if case_sensitive else text.lower()
        match_pattern = pattern if case_sensitive else pattern.lower()

        if condition_type == "always":
            return RuleMatch(matched=True, match_start=0, match_end=len(text))

        elif condition_type == "contains":
            idx = match_text.find(match_pattern)
            if idx >= 0:
                return RuleMatch(
                    matched=True,
                    match_start=idx,
                    match_end=idx + len(pattern)
                )
            return RuleMatch(matched=False)

        elif condition_type == "starts_with":
            if match_text.startswith(match_pattern):
                # Check that pattern is followed by separator or end of string
                # This prevents "ES" from matching "ESPN" - it should only match "ES: ..." or "ES | ..."
                remaining = match_text[len(match_pattern):]
                if not remaining or re.match(r'^[\s:\-|/]', remaining):
                    return RuleMatch(
                        matched=True,
                        match_start=0,
                        match_end=len(pattern)
                    )
            return RuleMatch(matched=False)

        elif condition_type == "ends_with":
            if match_text.endswith(match_pattern):
                # Check that pattern is preceded by separator or start of string
                # This prevents "HD" from matching "ADHD" - it should only match "... HD" or "...|HD"
                prefix_len = len(text) - len(pattern)
                if prefix_len == 0 or re.search(r'[\s:\-|/]$', text[:prefix_len]):
                    return RuleMatch(
                        matched=True,
                        match_start=prefix_len,
                        match_end=len(text)
                    )
            return RuleMatch(matched=False)

        elif condition_type == "regex":
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                match = re.search(pattern, text, flags)
                if match:
                    return RuleMatch(
                        matched=True,
                        match_start=match.start(),
                        match_end=match.end(),
                        groups=match.groups()
                    )
            except re.error as e:
                logger.warning(f"Invalid regex pattern: {e}")
            return RuleMatch(matched=False)

        else:
            logger.warning(f"Unknown condition type: {condition_type}")
            return RuleMatch(matched=False)

    def _match_condition(self, text: str, rule: NormalizationRule) -> RuleMatch:
        """
        Check if text matches the rule's condition(s).
        Supports both legacy single conditions and compound conditions.
        Returns RuleMatch with match details.
        """
        # Check for compound conditions first
        conditions = rule.get_conditions()
        if conditions:
            return self._match_compound_conditions(text, conditions, rule.condition_logic)

        # Fall back to legacy single condition
        return self._match_single_condition(
            text,
            rule.condition_type or "always",
            rule.condition_value or "",
            rule.case_sensitive
        )

    def _match_compound_conditions(
        self,
        text: str,
        conditions: list,
        logic: str = "AND"
    ) -> RuleMatch:
        """
        Match text against multiple conditions with AND/OR logic.
        The first condition's match info is used for the action (primary condition).
        """
        if not conditions:
            return RuleMatch(matched=False)

        results = []
        primary_match = None  # Match info from first condition for action application

        for i, cond in enumerate(conditions):
            cond_type = cond.get("type", "always")
            cond_value = cond.get("value", "")
            cond_case_sensitive = cond.get("case_sensitive", False)
            cond_negate = cond.get("negate", False)

            match = self._match_single_condition(text, cond_type, cond_value, cond_case_sensitive)

            # Apply negation
            if cond_negate:
                matched = not match.matched
            else:
                matched = match.matched

            results.append(matched)

            # Store the first non-negated match as the primary match for action application
            if i == 0 and match.matched and not cond_negate:
                primary_match = match

        # Combine results based on logic
        if logic == "OR":
            final_matched = any(results)
        else:  # AND (default)
            final_matched = all(results)

        if final_matched:
            # Return primary match info if available, otherwise generic match
            if primary_match:
                return primary_match
            return RuleMatch(matched=True, match_start=0, match_end=len(text))

        return RuleMatch(matched=False)

    def _apply_action(self, text: str, rule: NormalizationRule, match: RuleMatch) -> str:
        """
        Apply the rule's action to transform the text.
        """
        action_type = rule.action_type
        action_value = rule.action_value or ""
        pattern = rule.condition_value or ""
        case_sensitive = rule.case_sensitive

        if action_type == "remove":
            # Remove the matched portion
            return text[:match.match_start] + text[match.match_end:]

        elif action_type == "replace":
            # Replace matched portion with action_value
            return text[:match.match_start] + action_value + text[match.match_end:]

        elif action_type == "regex_replace":
            # Use regex substitution
            if rule.condition_type != "regex":
                logger.warning(f"regex_replace requires regex condition in rule {rule.id}")
                return text
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                return re.sub(pattern, action_value, text, flags=flags)
            except re.error as e:
                logger.warning(f"Regex replace error in rule {rule.id}: {e}")
                return text

        elif action_type == "strip_prefix":
            # Remove pattern from start, including any following separator
            # Handles patterns like "US: " or "US | " or "US-"
            if match.match_start == 0:
                result = text[match.match_end:]
                # Also strip common separators that might follow
                result = re.sub(r'^[\s:\-|/]+', '', result)
                return result.strip()
            return text

        elif action_type == "strip_suffix":
            # Remove pattern from end, including any preceding separator
            # Handles patterns like " HD" or " - HD" or " | HD"
            if match.match_end == len(text) or match.match_end == len(text.rstrip()):
                result = text[:match.match_start]
                # Also strip common separators that might precede
                result = re.sub(r'[\s:\-|/]+$', '', result)
                return result.strip()
            return text

        elif action_type == "normalize_prefix":
            # Keep the prefix but standardize its format
            # e.g., "US:" -> "US | " or "US-" -> "US | "
            if match.match_start == 0:
                # Extract just the prefix (the matched content)
                prefix = text[match.match_start:match.match_end]
                # Remove any trailing separators from prefix
                prefix = re.sub(r'[\s:\-|/]+$', '', prefix)
                # Get the rest of the text
                rest = text[match.match_end:]
                rest = re.sub(r'^[\s:\-|/]+', '', rest)
                # Use action_value as the separator format, default to " | "
                separator = action_value if action_value else " | "
                return f"{prefix}{separator}{rest}"
            return text

        else:
            logger.warning(f"Unknown action type: {action_type}")
            return text

    def normalize(self, name: str) -> NormalizationResult:
        """
        Apply all enabled rules to normalize a stream name.

        Rules are applied in priority order by group, then by rule within group.
        Processing stops for a group if a rule with stop_processing matches.

        Args:
            name: The stream name to normalize

        Returns:
            NormalizationResult with original, normalized name, and applied rules
        """
        result = NormalizationResult(
            original=name,
            normalized=name,
            rules_applied=[],
            transformations=[]
        )

        current = name.strip()
        grouped_rules = self._load_rules()

        for group, rules in grouped_rules:
            for rule in rules:
                match = self._match_condition(current, rule)

                if match.matched:
                    before = current
                    current = self._apply_action(current, rule, match)

                    # Track what changed
                    if before != current:
                        result.rules_applied.append(rule.id)
                        result.transformations.append((rule.id, before, current))

                        logger.debug(
                            f"Rule {rule.id} ({rule.name}): '{before}' -> '{current}'"
                        )

                    # Stop processing if rule says so
                    if rule.stop_processing:
                        break

        # Final cleanup - normalize whitespace
        current = re.sub(r'\s+', ' ', current).strip()

        result.normalized = current
        return result

    def test_rule(
        self,
        text: str,
        condition_type: str,
        condition_value: str,
        case_sensitive: bool,
        action_type: str,
        action_value: str = "",
        conditions: Optional[list] = None,
        condition_logic: str = "AND"
    ) -> dict:
        """
        Test a rule configuration against sample text without saving.

        Args:
            text: Sample text to test
            condition_type: Rule condition type (legacy single condition)
            condition_value: Pattern to match (legacy single condition)
            case_sensitive: Case sensitivity flag (legacy single condition)
            action_type: Action to apply
            action_value: Replacement value for replace actions
            conditions: Compound conditions list (takes precedence if set)
            condition_logic: "AND" or "OR" for combining conditions

        Returns:
            Dict with matched, before, after, match_details
        """
        import json

        # Create a temporary rule object for testing
        rule = NormalizationRule(
            id=0,
            group_id=0,
            name="Test Rule",
            condition_type=condition_type,
            condition_value=condition_value,
            case_sensitive=case_sensitive,
            action_type=action_type,
            action_value=action_value,
            conditions=json.dumps(conditions) if conditions else None,
            condition_logic=condition_logic
        )

        match = self._match_condition(text, rule)

        result = {
            "matched": match.matched,
            "before": text,
            "after": text,
            "match_start": match.match_start if match.matched else None,
            "match_end": match.match_end if match.matched else None,
        }

        if match.matched:
            result["after"] = self._apply_action(text, rule, match)
            # Final cleanup
            result["after"] = re.sub(r'\s+', ' ', result["after"]).strip()

        return result

    def test_rules_batch(self, texts: list[str]) -> list[NormalizationResult]:
        """
        Test all enabled rules against multiple sample texts.

        Args:
            texts: List of sample texts to normalize

        Returns:
            List of NormalizationResult objects
        """
        return [self.normalize(text) for text in texts]

    def get_all_rules(self) -> list[dict]:
        """
        Get all rules organized by group for display.

        Returns:
            List of group dicts with their rules
        """
        groups = (
            self.db.query(NormalizationRuleGroup)
            .order_by(NormalizationRuleGroup.priority)
            .all()
        )

        result = []
        for group in groups:
            rules = (
                self.db.query(NormalizationRule)
                .filter(NormalizationRule.group_id == group.id)
                .order_by(NormalizationRule.priority)
                .all()
            )
            result.append({
                **group.to_dict(),
                "rules": [rule.to_dict() for rule in rules]
            })

        return result


def get_normalization_engine(db: Session) -> NormalizationEngine:
    """Factory function to get a NormalizationEngine instance."""
    return NormalizationEngine(db)
