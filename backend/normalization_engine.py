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

from models import NormalizationRule, NormalizationRuleGroup, TagGroup, Tag

logger = logging.getLogger(__name__)


# Cache for tag groups to avoid repeated database queries
_tag_group_cache: dict[int, list[tuple[str, bool]]] = {}  # group_id -> [(value, case_sensitive), ...]


def invalidate_tag_cache():
    """Clear the global tag caches so the next access reloads from DB."""
    _tag_group_cache.clear()
    NormalizationEngine._tag_group_id_cache.clear()


# Unicode superscript to ASCII mapping for quality tags
# Common patterns: ᴴᴰ (HD), ᶠᴴᴰ (FHD), ᵁᴴᴰ (UHD), ᴿᴬᵂ (RAW), ˢᴰ (SD), etc.
SUPERSCRIPT_MAP = {
    # Uppercase superscripts
    '\u1d2c': 'A',  # ᴬ
    '\u1d2e': 'B',  # ᴮ
    '\u1d30': 'D',  # ᴰ
    '\u1d31': 'E',  # ᴱ
    '\u1d33': 'G',  # ᴳ
    '\u1d34': 'H',  # ᴴ
    '\u1d35': 'I',  # ᴵ
    '\u1d36': 'J',  # ᴶ
    '\u1d37': 'K',  # ᴷ
    '\u1d38': 'L',  # ᴸ
    '\u1d39': 'M',  # ᴹ
    '\u1d3a': 'N',  # ᴺ
    '\u1d3c': 'O',  # ᴼ
    '\u1d3e': 'P',  # ᴾ
    '\u1d3f': 'R',  # ᴿ
    '\u1d40': 'T',  # ᵀ
    '\u1d41': 'U',  # ᵁ
    '\u1d42': 'W',  # ᵂ
    '\u2c7d': 'V',  # ⱽ
    # Lowercase superscripts
    '\u1d43': 'a',  # ᵃ
    '\u1d47': 'b',  # ᵇ
    '\u1d48': 'd',  # ᵈ
    '\u1d49': 'e',  # ᵉ
    '\u1da0': 'f',  # ᶠ
    '\u1d4d': 'g',  # ᵍ
    '\u02b0': 'h',  # ʰ
    '\u2071': 'i',  # ⁱ
    '\u02b2': 'j',  # ʲ
    '\u1d4f': 'k',  # ᵏ
    '\u02e1': 'l',  # ˡ
    '\u1d50': 'm',  # ᵐ
    '\u207f': 'n',  # ⁿ
    '\u1d52': 'o',  # ᵒ
    '\u1d56': 'p',  # ᵖ
    '\u02b3': 'r',  # ʳ
    '\u02e2': 's',  # ˢ
    '\u1d57': 't',  # ᵗ
    '\u1d58': 'u',  # ᵘ
    '\u1d5b': 'v',  # ᵛ
    '\u02b7': 'w',  # ʷ
    '\u02e3': 'x',  # ˣ
    '\u02b8': 'y',  # ʸ
    '\u1dbb': 'z',  # ᶻ
}


def convert_superscripts(text: str) -> str:
    """
    Convert Unicode superscript characters to their ASCII equivalents.
    This allows quality tags like ᴴᴰ, ᵁᴴᴰ, ᴿᴬᵂ to be matched by normal rules.
    """
    result = []
    for char in text:
        result.append(SUPERSCRIPT_MAP.get(char, char))
    return ''.join(result)


@dataclass
class RuleMatch:
    """Result of a rule match attempt."""
    matched: bool
    match_start: int = -1
    match_end: int = -1
    groups: tuple = ()  # Captured groups for regex
    matched_tag: str = ""  # The tag value that matched (for tag_group conditions)


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
        # Also clear tag group cache
        global _tag_group_cache
        _tag_group_cache.clear()

    def _load_tag_group(self, tag_group_id: int) -> list[tuple[str, bool]]:
        """
        Load tags from a tag group with caching.

        Args:
            tag_group_id: ID of the tag group to load

        Returns:
            List of (tag_value, case_sensitive) tuples for enabled tags
        """
        global _tag_group_cache

        if tag_group_id in _tag_group_cache:
            return _tag_group_cache[tag_group_id]

        # Load from database
        tags = (
            self.db.query(Tag)
            .filter(Tag.group_id == tag_group_id, Tag.enabled == True)
            .all()
        )

        # Convert superscripts in tag values (so tags with ᴿᴬᵂ match RAW)
        tag_list = [(convert_superscripts(tag.value), tag.case_sensitive) for tag in tags]
        _tag_group_cache[tag_group_id] = tag_list

        return tag_list

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
        # Convert superscripts in pattern (so rules with ᴿᴬᵂ match RAW)
        pattern = convert_superscripts(pattern)

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
                # Check that pattern is followed by separator (NOT end of string)
                # This prevents "ES" from matching "ESPN" - it should only match "ES: ..." or "ES | ..."
                # Also prevents matching if the pattern IS the entire string (nothing would remain)
                remaining = match_text[len(match_pattern):]
                if remaining and re.match(r'^[\s:\-|/]', remaining):
                    return RuleMatch(
                        matched=True,
                        match_start=0,
                        match_end=len(pattern)
                    )
            return RuleMatch(matched=False)

        elif condition_type == "ends_with":
            if match_text.endswith(match_pattern):
                # Check that pattern is preceded by separator (NOT start of string)
                # This prevents "HD" from matching "ADHD" - it should only match "... HD" or "...|HD"
                # Also prevents matching if the pattern IS the entire string (nothing would remain)
                prefix_len = len(text) - len(pattern)
                if prefix_len > 0 and re.search(r'[\s:\-|/]$', text[:prefix_len]):
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
                logger.warning("[NORMALIZE] Invalid regex pattern: %s", e)
            return RuleMatch(matched=False)

        else:
            logger.warning("[NORMALIZE] Unknown condition type: %s", condition_type)
            return RuleMatch(matched=False)

    def _match_tag_group(
        self,
        text: str,
        tag_group_id: int,
        position: str = "contains"
    ) -> RuleMatch:
        """
        Check if text matches any tag from a tag group.

        Args:
            text: Text to match against
            tag_group_id: ID of the tag group
            position: 'prefix', 'suffix', or 'contains' (default)

        Returns:
            RuleMatch with match details and matched_tag
        """
        tags = self._load_tag_group(tag_group_id)

        for tag_value, case_sensitive in tags:
            match_text = text if case_sensitive else text.lower()
            match_tag = tag_value if case_sensitive else tag_value.lower()

            if position == "prefix":
                # Match at start with separator check
                # Requires something after the tag (don't match if tag IS the entire string)
                if match_text.startswith(match_tag):
                    remaining = match_text[len(match_tag):]
                    if remaining and re.match(r'^[\s:\-|/]', remaining):
                        return RuleMatch(
                            matched=True,
                            match_start=0,
                            match_end=len(tag_value),
                            matched_tag=tag_value
                        )

            elif position == "suffix":
                # Match at end with separator check
                # Requires something before the tag (don't match if tag IS the entire string)
                if match_text.endswith(match_tag):
                    prefix_len = len(text) - len(tag_value)
                    if prefix_len > 0 and re.search(r'[\s:\-|/]$', text[:prefix_len]):
                        return RuleMatch(
                            matched=True,
                            match_start=prefix_len,
                            match_end=len(text),
                            matched_tag=tag_value
                        )

                # Also check for parenthesized version: (TAG)
                # Common pattern in stream names like "Channel Name (HD)" or "Movie (NA)"
                paren_tag = f"({match_tag})"
                if match_text.endswith(paren_tag):
                    prefix_len = len(text) - len(paren_tag)
                    # Parenthesized suffixes typically have a space before them
                    if prefix_len > 0 and text[prefix_len - 1] == ' ':
                        return RuleMatch(
                            matched=True,
                            match_start=prefix_len - 1,  # Include the space before
                            match_end=len(text),
                            matched_tag=tag_value
                        )

            else:  # contains
                idx = match_text.find(match_tag)
                if idx >= 0:
                    return RuleMatch(
                        matched=True,
                        match_start=idx,
                        match_end=idx + len(tag_value),
                        matched_tag=tag_value
                    )

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

        # Handle tag_group condition type
        if rule.condition_type == "tag_group":
            if rule.tag_group_id:
                return self._match_tag_group(
                    text,
                    rule.tag_group_id,
                    rule.tag_match_position or "contains"
                )
            return RuleMatch(matched=False)

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
                logger.warning("[NORMALIZE] regex_replace requires regex condition in rule %s", rule.id)
                return text
            try:
                flags = 0 if case_sensitive else re.IGNORECASE
                return re.sub(pattern, action_value, text, flags=flags)
            except re.error as e:
                logger.warning("[NORMALIZE] Regex replace error in rule %s: %s", rule.id, e)
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
                result = result.rstrip(' \t\n\r:-|/')
                return result.strip()
            return text

        elif action_type == "normalize_prefix":
            # Keep the prefix but standardize its format
            # e.g., "US:" -> "US | " or "US-" -> "US | "
            if match.match_start == 0:
                # Extract just the prefix (the matched content)
                prefix = text[match.match_start:match.match_end]
                # Remove any trailing separators from prefix
                prefix = prefix.rstrip(' \t\n\r:-|/')
                # Get the rest of the text
                rest = text[match.match_end:]
                rest = rest.lstrip(' \t\n\r:-|/')
                # Use action_value as the separator format, default to " | "
                separator = action_value if action_value else " | "
                return f"{prefix}{separator}{rest}"
            return text

        elif action_type == "capitalize":
            mode = action_value.lower() if action_value else "title"
            if mode == "upper":
                return text.upper()
            elif mode == "lower":
                return text.lower()
            elif mode == "sentence":
                return text[0].upper() + text[1:].lower() if text else text
            else:
                # Smart title case: preserve short all-caps words (likely acronyms)
                words = text.split()
                result = []
                for word in words:
                    alpha = ''.join(c for c in word if c.isalpha())
                    if alpha.isupper() and len(alpha) <= 4:
                        result.append(word)
                    else:
                        result.append(word.title())
                return ' '.join(result)

        else:
            logger.warning("[NORMALIZE] Unknown action type: %s", action_type)
            return text

    def _apply_else_action(self, text: str, rule: NormalizationRule) -> str:
        """
        Apply the rule's else action when the condition does NOT match.
        Only applies if else_action_type is set.

        Args:
            text: The current text
            rule: The rule with else action configuration

        Returns:
            Transformed text or original if no else action
        """
        if not rule.else_action_type:
            return text

        action_type = rule.else_action_type
        action_value = rule.else_action_value or ""

        if action_type == "remove":
            # Remove doesn't make sense without a specific match
            # In else context, this would clear the entire text - probably not intended
            logger.warning("[NORMALIZE] Rule %s: 'remove' as else_action has no effect (no match to remove)", rule.id)
            return text

        elif action_type == "replace":
            # Replace entire text with else_action_value
            return action_value

        elif action_type == "regex_replace":
            # For else, apply the regex pattern to the whole text
            if rule.condition_value:
                try:
                    flags = 0 if rule.case_sensitive else re.IGNORECASE
                    return re.sub(rule.condition_value, action_value, text, flags=flags)
                except re.error as e:
                    logger.warning("[NORMALIZE] Regex replace error in else action of rule %s: %s", rule.id, e)
            return text

        elif action_type == "strip_prefix":
            # Strip any leading separators and whitespace
            result = text.lstrip(' \t\n\r:-|/')
            return result.strip()

        elif action_type == "strip_suffix":
            # Strip any trailing separators and whitespace
            result = text.rstrip(' \t\n\r:-|/')
            return result.strip()

        elif action_type == "normalize_prefix":
            # No specific prefix matched, so can't normalize
            logger.warning("[NORMALIZE] Rule %s: 'normalize_prefix' as else_action has no effect (no match)", rule.id)
            return text

        elif action_type == "capitalize":
            mode = action_value.lower() if action_value else "title"
            if mode == "upper":
                return text.upper()
            elif mode == "lower":
                return text.lower()
            elif mode == "sentence":
                return text[0].upper() + text[1:].lower() if text else text
            else:
                # Smart title case: preserve short all-caps words (likely acronyms)
                words = text.split()
                result = []
                for word in words:
                    alpha = ''.join(c for c in word if c.isalpha())
                    if alpha.isupper() and len(alpha) <= 4:
                        result.append(word)
                    else:
                        result.append(word.title())
                return ' '.join(result)

        else:
            logger.warning("[NORMALIZE] Unknown else action type: %s", action_type)
            return text

    def normalize(self, name: str) -> NormalizationResult:
        """
        Apply all enabled rules to normalize a stream name.

        Rules are applied in multiple passes until no more changes occur.
        This handles cases like "4K/UHD" (both quality tags) or "HD (NA)"
        where stripping one suffix reveals another that should also be stripped.

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

        # Convert Unicode superscripts to ASCII (e.g., ᴴᴰ -> HD, ᵁᴴᴰ -> UHD, ᴿᴬᵂ -> RAW)
        current = convert_superscripts(current)

        grouped_rules = self._load_rules()

        # Multi-pass normalization: keep applying rules until no changes occur
        max_passes = 10  # Safety limit to prevent infinite loops
        for pass_num in range(max_passes):
            before_pass = current

            # Apply database rules
            current = self._apply_rules_single_pass(current, grouped_rules, result)

            # Apply legacy custom_normalization_tags from settings
            current = self._apply_legacy_custom_tags(current, result)

            # Normalize whitespace between passes
            current = re.sub(r'\s+', ' ', current).strip()

            # If nothing changed this pass, we're done
            if current == before_pass:
                break

            logger.debug("[NORMALIZE] Normalization pass %s: '%s' -> '%s'", pass_num + 1, before_pass, current)

        result.normalized = current
        return result

    def _apply_rules_single_pass(
        self,
        text: str,
        grouped_rules: list,
        result: NormalizationResult
    ) -> str:
        """Apply all database rules once through the text."""
        current = text

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
                            "[NORMALIZE] Rule %s (%s): '%s' -> '%s'",
                            rule.id, rule.name, before, current
                        )

                    # Stop processing if rule says so
                    if rule.stop_processing:
                        break

                elif rule.else_action_type:
                    # Condition didn't match but rule has an else action
                    before = current
                    current = self._apply_else_action(current, rule)

                    # Track what changed
                    if before != current:
                        result.rules_applied.append(rule.id)
                        result.transformations.append((rule.id, before, current))

                        logger.debug(
                            "[NORMALIZE] Rule %s (%s) [ELSE]: '%s' -> '%s'",
                            rule.id, rule.name, before, current
                        )

                    # Stop processing applies to else branch too
                    if rule.stop_processing:
                        break

        return current

    def _apply_legacy_custom_tags(self, text: str, result: NormalizationResult) -> str:
        """
        Apply custom_normalization_tags from settings.json for backward compatibility.
        These are user-defined tags that predate the database-based tag system.
        """
        try:
            from config import get_settings
            settings = get_settings()
            custom_tags = settings.custom_normalization_tags or []
        except Exception:
            return text

        current = text
        for tag_config in custom_tags:
            tag_value = tag_config.get("value", "")
            mode = tag_config.get("mode", "both")  # prefix, suffix, or both

            if not tag_value:
                continue

            before = current

            # Handle suffix mode
            if mode in ("suffix", "both"):
                # Check for plain suffix with separator
                lower_current = current.lower()
                lower_tag = tag_value.lower()

                # Check if ends with tag (with separator before it)
                if lower_current.endswith(lower_tag):
                    prefix_len = len(current) - len(tag_value)
                    if prefix_len > 0 and current[prefix_len - 1] in ' :-|/':
                        current = current[:prefix_len].rstrip(' :-|/')
                        continue

                # Check for parenthesized suffix: (TAG)
                paren_tag = f"({tag_value})"
                lower_paren = paren_tag.lower()
                if lower_current.endswith(lower_paren):
                    prefix_len = len(current) - len(paren_tag)
                    if prefix_len > 0:
                        current = current[:prefix_len].rstrip()
                        continue

            # Handle prefix mode
            if mode in ("prefix", "both"):
                lower_current = current.lower()
                lower_tag = tag_value.lower()

                if lower_current.startswith(lower_tag):
                    remaining = current[len(tag_value):]
                    if remaining and remaining[0] in ' :-|/':
                        current = remaining.lstrip(' :-|/')

            # Track if changed
            if before != current:
                result.transformations.append(("legacy_tag", before, current))
                logger.debug("[NORMALIZE] Legacy tag '%s': '%s' -> '%s'", str(tag_value).replace('\n', ''), str(before).replace('\n', ''), str(current).replace('\n', ''))

        return current

    # =================================================================
    # Core Name Extraction (for merge_streams fallback matching)
    # =================================================================

    _tag_group_id_cache: dict[str, Optional[int]] = {}

    def _get_tag_group_id_by_name(self, name: str) -> Optional[int]:
        """Get a TagGroup's ID by its display name, with caching."""
        if name in self._tag_group_id_cache:
            return self._tag_group_id_cache[name]

        group = self.db.query(TagGroup).filter(TagGroup.name == name).first()
        gid = group.id if group else None
        self._tag_group_id_cache[name] = gid
        return gid

    def extract_core_name(self, name: str) -> str:
        """
        Strip country prefix and quality suffix from a name using tag groups
        DIRECTLY — does NOT depend on normalization rules being enabled.

        Used by merge_streams core-name fallback when normalize_names=true.

        Returns the core name (never empty; falls back to input).
        """
        current = name.strip()
        if not current:
            return current

        # Convert Unicode superscripts (ᴴᴰ -> HD, etc.)
        current = convert_superscripts(current)

        # Strip leading channel-number prefix: "107 | Name", "107 - Name"
        current = re.sub(r'^\d+\s*[|:\-]\s*', '', current).strip()
        if not current:
            return name.strip()

        country_id = self._get_tag_group_id_by_name("Country Tags")
        quality_id = self._get_tag_group_id_by_name("Quality Tags")

        # Multi-pass: keep stripping until stable (handles stacked tags)
        for _ in range(5):
            before = current

            # Strip country prefix
            if country_id:
                match = self._match_tag_group(current, country_id, "prefix")
                if match.matched and match.match_start == 0:
                    result = current[match.match_end:]
                    result = re.sub(r'^[\s:\-|/]+', '', result).strip()
                    if result:
                        current = result

            # Strip quality suffix
            if quality_id:
                match = self._match_tag_group(current, quality_id, "suffix")
                if match.matched:
                    if match.match_end == len(current) or match.match_end == len(current.rstrip()):
                        result = current[:match.match_start]
                        result = re.sub(r'[\s:|\-/]+$', '',result).strip()
                        if result:
                            current = result

            # Normalize whitespace between passes
            current = re.sub(r'\s+', ' ', current).strip()

            if current == before:
                break

        return current if current else name.strip()

    # =================================================================
    # Call Sign Extraction (for merge_streams local affiliate matching)
    # =================================================================

    # FCC call signs: W/K + 2-3 uppercase letters
    # Parenthesized form: "(WFTS)", "(KABC)"
    # Bare form at end of name: "ABC 28 Tampa WFTS"
    _CALLSIGN_FALSE_POSITIVES = frozenset({"WWE", "WEST", "KIDZ", "KIDS", "WNBA", "WPT"})
    _CALLSIGN_PAREN_RE = re.compile(r'\(([WK][A-Z]{2,3})\)')
    _CALLSIGN_BARE_RE = re.compile(r'\b([WK][A-Z]{2,3})\b')

    # Broadcast networks — bare call sign extraction requires one of these
    # (or a channel number) to be present, preventing false positives on
    # random English words like WAVE, KIDS, WAR
    _BROADCAST_NETWORKS = frozenset({
        "ABC", "CBS", "NBC", "FOX", "PBS", "CW", "MY", "ION",
        "UPN", "WB", "MNT", "UNIVISION", "TELEMUNDO",
    })

    # Prefixes that disqualify a name from call sign extraction —
    # these are content categories, not local station streams/channels
    _CALLSIGN_EXCLUDED_PREFIXES = ("TEAMS:",)

    @staticmethod
    def extract_call_sign(name: str) -> Optional[str]:
        """
        Extract an FCC call sign (W/K + 2-3 uppercase letters) from a name.

        Prefers parenthesized call signs like "(WFTS)" over bare ones.
        For bare form, requires a broadcast network name or channel number
        nearby to prevent false positives on common words.
        Returns None if no call sign found or if it's a known false positive.

        Used by merge_streams call-sign fallback when normalize_names=true.
        """
        if not name:
            return None

        upper = name.upper()

        # Skip names with disqualifying prefixes (e.g., "Teams: CBS Texans (KENS)")
        # Strip leading channel numbers like "2072 | " before checking prefix
        stripped = re.sub(r'^\d+\s*\|\s*', '', upper)
        if any(stripped.startswith(p) for p in NormalizationEngine._CALLSIGN_EXCLUDED_PREFIXES):
            return None

        # Prefer parenthesized: "(WFTS)", "(KABC)"
        m = NormalizationEngine._CALLSIGN_PAREN_RE.search(upper)
        if m:
            cs = m.group(1)
            if cs not in NormalizationEngine._CALLSIGN_FALSE_POSITIVES:
                return cs

        # Bare form: only attempt if name contains a broadcast network or
        # channel number — this prevents matching random words in names
        # like "(MC Radio) New Wave" or "DOCUBOX: MILITARY AND WAR"
        has_network = any(
            re.search(r'\b' + net + r'\b', upper)
            for net in NormalizationEngine._BROADCAST_NETWORKS
        )
        has_channel_num = bool(re.search(r'\b\d{1,2}\b', upper))

        if not has_network and not has_channel_num:
            return None

        # Take the LAST match — call signs come after city/state names
        # e.g., "CBS: TX WACO KWTX" → want KWTX not WACO
        last_cs = None
        for m in NormalizationEngine._CALLSIGN_BARE_RE.finditer(upper):
            cs = m.group(1)
            if cs not in NormalizationEngine._CALLSIGN_FALSE_POSITIVES:
                last_cs = cs
        return last_cs

    def test_rule(
        self,
        text: str,
        condition_type: str,
        condition_value: str,
        case_sensitive: bool,
        action_type: str,
        action_value: str = "",
        conditions: Optional[list] = None,
        condition_logic: str = "AND",
        tag_group_id: Optional[int] = None,
        tag_match_position: str = "contains",
        else_action_type: Optional[str] = None,
        else_action_value: Optional[str] = None
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
            tag_group_id: Tag group ID for tag_group condition type
            tag_match_position: Position for tag matching ('prefix', 'suffix', 'contains')
            else_action_type: Action to apply when condition doesn't match
            else_action_value: Value for else action

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
            tag_group_id=tag_group_id,
            tag_match_position=tag_match_position,
            action_type=action_type,
            action_value=action_value,
            else_action_type=else_action_type,
            else_action_value=else_action_value,
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
            "matched_tag": match.matched_tag if match.matched_tag else None,
            "else_applied": False,
        }

        if match.matched:
            result["after"] = self._apply_action(text, rule, match)
            # Final cleanup
            result["after"] = re.sub(r'\s+', ' ', result["after"]).strip()
        elif else_action_type:
            # Condition didn't match, apply else action
            result["after"] = self._apply_else_action(text, rule)
            result["after"] = re.sub(r'\s+', ' ', result["after"]).strip()
            result["else_applied"] = True

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
