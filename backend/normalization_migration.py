"""
Migration script to create demo normalization rules using tag groups.

This creates editable demo rule groups that use tag group-based conditions.
Each rule matches against an entire tag group (e.g., all Quality Tags) with
a single rule, instead of creating individual rules per tag.

Rules are disabled by default so users can enable the ones they want.
All rules are editable (is_builtin=False).
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session

from models import NormalizationRuleGroup, NormalizationRule, TagGroup

logger = logging.getLogger(__name__)

# Demo rules configuration - references built-in tag groups
# Each entry creates one rule group with one or more tag-group-based rules
DEMO_RULE_CONFIGS = [
    {
        "rule_group_name": "Strip Quality Suffixes",
        "rule_group_description": "Remove quality/resolution indicators from end of channel names",
        "priority": 0,
        "tag_group_name": "Quality Tags",
        "match_position": "suffix",
        "action_type": "strip_suffix"
    },
    {
        "rule_group_name": "Strip Country Prefixes",
        "rule_group_description": "Remove country codes from start of channel names",
        "priority": 1,
        "tag_group_name": "Country Tags",
        "match_position": "prefix",
        "action_type": "strip_prefix"
    },
    {
        "rule_group_name": "Strip Timezone Suffixes",
        "rule_group_description": "Remove timezone abbreviations from end of channel names",
        "priority": 2,
        "tag_group_name": "Timezone Tags",
        "match_position": "suffix",
        "action_type": "strip_suffix"
    },
    {
        "rule_group_name": "Strip League Prefixes",
        "rule_group_description": "Remove sports league abbreviations from start of channel names",
        "priority": 3,
        "tag_group_name": "League Tags",
        "match_position": "prefix",
        "action_type": "strip_prefix",
        # bd-0emgo.2: the league strip must fire only on a STRONG delimiter
        # ("NFL: Buffalo Bills" -> "Buffalo Bills"), not a bare space, so brands
        # like "NFL RedZone" / "NFL Network" / "NFL Sunday Ticket" are preserved.
        "require_delimiter": True
    },
    {
        "rule_group_name": "Strip State/Province Tags",
        "rule_group_description": "Remove US state and Canadian province abbreviations from channel names",
        "priority": 4,
        "tag_group_name": "State/Province Tags",
        "match_position": "contains",
        "action_type": "remove"
    },
    {
        "rule_group_name": "Strip Network Tags",
        "rule_group_description": "Remove network/stream type indicators from channel names",
        "priority": 5,
        "tag_group_name": "Network Tags",
        "match_position": "suffix",  # Default to suffix; users can duplicate for prefix if needed
        "action_type": "strip_suffix"
    },
    {
        "rule_group_name": "Strip Provider Tags",
        "rule_group_description": "Remove provider source indicators like (S), (H), (A) from channel names",
        "priority": 6,
        "tag_group_name": "Provider Tags",
        "match_position": "suffix",
        "action_type": "strip_suffix"
    }
]


def create_demo_rules(
    db: Session,
    force: bool = False,
    custom_normalization_tags: Optional[list] = None
) -> dict:
    """
    Create demo normalization rules using tag group-based conditions.

    Each demo rule uses a tag group (e.g., "Quality Tags") as its condition,
    matching ANY tag in that group with a single rule. This is much simpler
    than creating individual rules for each tag.

    Rules are DISABLED by default so users can enable the ones they want.
    All rules are editable (is_builtin=False).

    Args:
        db: Database session
        force: If True, recreate even if rules already exist
        custom_normalization_tags: Custom tags to migrate (list of {"value", "mode"})

    Returns:
        Dict with counts of groups and rules created
    """
    custom_normalization_tags = custom_normalization_tags or []

    # Check if demo rules already exist (by checking for any rule groups)
    existing_groups = db.query(NormalizationRuleGroup).count()

    if existing_groups > 0 and not force:
        logger.info("[NORMALIZE-MIGRATE] Rule groups already exist (%s), skipping demo creation", existing_groups)
        return {"groups_created": 0, "rules_created": 0, "custom_rules_created": 0, "skipped": True}

    if force and existing_groups > 0:
        # Delete all existing rule groups and rules
        logger.info("[NORMALIZE-MIGRATE] Force mode: deleting %s existing groups", existing_groups)
        db.query(NormalizationRule).delete()
        db.query(NormalizationRuleGroup).delete()
        db.commit()

    # Build a map of tag group names to IDs
    tag_groups = db.query(TagGroup).all()
    tag_group_map = {tg.name: tg.id for tg in tag_groups}

    groups_created = 0
    rules_created = 0

    for config in DEMO_RULE_CONFIGS:
        tag_group_name = config["tag_group_name"]
        tag_group_id = tag_group_map.get(tag_group_name)

        if not tag_group_id:
            logger.warning("[NORMALIZE-MIGRATE] Tag group '%s' not found, skipping rule", tag_group_name)
            continue

        # Create the rule group - DISABLED by default, editable
        rule_group = NormalizationRuleGroup(
            name=config["rule_group_name"],
            description=config["rule_group_description"],
            enabled=False,  # Disabled by default - users enable what they want
            priority=config["priority"],
            is_builtin=False  # Editable
        )
        db.add(rule_group)
        db.flush()  # Get the group ID
        groups_created += 1

        # Create a single rule that uses the tag group condition
        rule = NormalizationRule(
            group_id=rule_group.id,
            name=f"Match {tag_group_name}",
            description=f"Matches any tag from '{tag_group_name}' and removes it",
            enabled=True,  # Enabled within group (group controls activation)
            priority=0,
            condition_type="tag_group",
            tag_group_id=tag_group_id,
            tag_match_position=config["match_position"],
            action_type=config["action_type"],
            # bd-0emgo.2: per-rule strong-delimiter requirement (league strip).
            require_delimiter=config.get("require_delimiter", False),
            is_builtin=False  # Editable
        )
        db.add(rule)
        rules_created += 1

        logger.info("[NORMALIZE-MIGRATE] Created rule group '%s' with tag group condition", config['rule_group_name'])

    # Create Title Case rule group — runs last (high priority number) to title-case
    # whatever remains after all stripping rules have run
    title_group = NormalizationRuleGroup(
        name="Title Case",
        description="Convert channel names to title case, preserving short abbreviations (ESPN, HBO, etc.)",
        enabled=False,
        priority=99,  # Run after all stripping rules
        is_builtin=False
    )
    db.add(title_group)
    db.flush()
    groups_created += 1

    title_rule = NormalizationRule(
        group_id=title_group.id,
        name="Smart Title Case",
        description="Title-cases text while preserving short all-caps words (likely abbreviations)",
        enabled=True,
        priority=0,
        condition_type="always",
        action_type="capitalize",
        action_value="title",
        is_builtin=False
    )
    db.add(title_rule)
    rules_created += 1
    logger.info("[NORMALIZE-MIGRATE] Created 'Title Case' rule group")

    # Create custom rules from user's custom_normalization_tags (legacy migration)
    custom_rules_created = 0
    if custom_normalization_tags:
        custom_group = NormalizationRuleGroup(
            name="Custom Tags",
            description="User-defined custom normalization tags (migrated from settings)",
            enabled=True,
            priority=100,  # Run after demo rules
            is_builtin=False
        )
        db.add(custom_group)
        db.flush()
        groups_created += 1

        for priority, tag_def in enumerate(custom_normalization_tags):
            tag_value = tag_def.get("value", "")
            tag_mode = tag_def.get("mode", "suffix")

            if not tag_value:
                continue

            # Determine condition and action based on mode
            if tag_mode == "prefix":
                condition_type = "starts_with"
                action_type = "strip_prefix"
            else:  # suffix or both (default to suffix)
                condition_type = "ends_with"
                action_type = "strip_suffix"

            rule = NormalizationRule(
                group_id=custom_group.id,
                name=f"Strip {tag_value}",
                enabled=True,
                priority=priority,
                condition_type=condition_type,
                condition_value=tag_value,
                case_sensitive=False,
                action_type=action_type,
                is_builtin=False
            )
            db.add(rule)
            custom_rules_created += 1

    db.commit()
    logger.info(
        "[NORMALIZE-MIGRATE] Created %s demo groups (disabled by default) with %s tag-group rules "
        "and %s custom rules",
        groups_created, rules_created, custom_rules_created
    )

    return {
        "groups_created": groups_created,
        "rules_created": rules_created,
        "custom_rules_created": custom_rules_created,
        "skipped": False
    }


def get_migration_status(db: Session) -> dict:
    """
    Get the current status of the normalization rules.

    Returns:
        Dict with counts of groups and rules
    """
    total_groups = db.query(NormalizationRuleGroup).count()
    enabled_groups = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.enabled == True
    ).count()
    total_rules = db.query(NormalizationRule).count()
    enabled_rules = db.query(NormalizationRule).filter(
        NormalizationRule.enabled == True
    ).count()

    return {
        "total_groups": total_groups,
        "enabled_groups": enabled_groups,
        "total_rules": total_rules,
        "enabled_rules": enabled_rules,
        "has_rules": total_groups > 0
    }


def fix_timezone_tags_remove_directional(db: Session) -> dict:
    """
    Ensure EAST and WEST are both present in Timezone Tags for consistent
    normalization. Both should be stripped so channel names like
    "Discovery EAST HD" and "Discovery WEST HD" normalize the same way.

    Returns:
        Dict with count of tags added
    """
    from models import TagGroup, Tag

    # Tags that must exist for consistent normalization
    required_tags = ["EAST", "WEST"]

    # Find the Timezone Tags tag group
    timezone_group = db.query(TagGroup).filter(
        TagGroup.name == "Timezone Tags"
    ).first()

    if not timezone_group:
        logger.info("[NORMALIZE-MIGRATE] Timezone Tags tag group not found, skipping fix")
        return {"tags_added": 0, "skipped": True}

    # Ensure each required tag exists
    added = 0
    for tag_value in required_tags:
        existing = db.query(Tag).filter(
            Tag.group_id == timezone_group.id,
            Tag.value == tag_value
        ).first()
        if not existing:
            db.add(Tag(
                group_id=timezone_group.id,
                value=tag_value,
                case_sensitive=False,
                enabled=True,
                is_builtin=True
            ))
            added += 1

    if added > 0:
        db.commit()
        logger.info("[NORMALIZE-MIGRATE] Added %s missing tags to Timezone Tags (EAST/WEST)", added)
    else:
        logger.info("[NORMALIZE-MIGRATE] Timezone Tags already has EAST and WEST")

    return {"tags_added": added, "skipped": False}


def fix_tag_group_action_types(db: Session) -> dict:
    """
    Fix action types for tag-group-based rules.

    Rules using tag_group conditions with match_position should use
    strip_prefix/strip_suffix instead of 'remove' to properly handle
    separator characters (: | - /).

    Returns:
        Dict with count of rules updated
    """
    updated = 0

    # Find rules with tag_group condition that use 'remove' action
    rules = db.query(NormalizationRule).filter(
        NormalizationRule.condition_type == "tag_group",
        NormalizationRule.action_type == "remove"
    ).all()

    for rule in rules:
        if rule.tag_match_position == "prefix":
            rule.action_type = "strip_prefix"
            updated += 1
        elif rule.tag_match_position == "suffix":
            rule.action_type = "strip_suffix"
            updated += 1
        # 'contains' position keeps 'remove' since there's no directional strip

    if updated > 0:
        db.commit()
        logger.info("[NORMALIZE-MIGRATE] Updated %s tag-group rules to use strip_prefix/strip_suffix actions", updated)
    else:
        logger.info("[NORMALIZE-MIGRATE] No tag-group rules needed action type fixes")

    return {"rules_updated": updated}


def ensure_provider_tags_rule(db: Session) -> dict:
    """
    Ensure the 'Strip Provider Tags' normalization rule group exists.

    For existing installations that already have normalization rules,
    this adds the Provider Tags rule group if it's missing. The tag group
    itself is created by _seed_builtin_tags in database.py.

    Returns:
        Dict with created status
    """
    # Check if the rule group already exists
    existing = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.name == "Strip Provider Tags"
    ).first()

    if existing:
        logger.info("[NORMALIZE-MIGRATE] Strip Provider Tags rule group already exists")
        return {"created": False}

    # Check if we have any rule groups at all (if none, create_demo_rules handles it)
    total_groups = db.query(NormalizationRuleGroup).count()
    if total_groups == 0:
        logger.info("[NORMALIZE-MIGRATE] No rule groups exist yet, skipping (create_demo_rules will handle)")
        return {"created": False}

    # Find the Provider Tags tag group
    provider_group = db.query(TagGroup).filter(
        TagGroup.name == "Provider Tags"
    ).first()

    if not provider_group:
        logger.warning("[NORMALIZE-MIGRATE] Provider Tags tag group not found, skipping rule creation")
        return {"created": False}

    # Create the rule group - disabled by default like other demo rules
    rule_group = NormalizationRuleGroup(
        name="Strip Provider Tags",
        description="Remove provider source indicators like (S), (H), (A) from channel names",
        enabled=False,
        priority=5,
        is_builtin=False
    )
    db.add(rule_group)
    db.flush()

    # Create the tag-group-based rule
    rule = NormalizationRule(
        group_id=rule_group.id,
        name="Match Provider Tags",
        description="Matches any tag from 'Provider Tags' and removes it",
        enabled=True,
        priority=0,
        condition_type="tag_group",
        tag_group_id=provider_group.id,
        tag_match_position="suffix",
        action_type="strip_suffix",
        is_builtin=False
    )
    db.add(rule)
    db.commit()

    logger.info("[NORMALIZE-MIGRATE] Created 'Strip Provider Tags' rule group with tag group condition")
    return {"created": True}


def ensure_state_province_tags_rule(db: Session) -> dict:
    """
    Ensure the 'Strip State/Province Suffixes' normalization rule group exists.

    For existing installations that already have normalization rules,
    this adds the State/Province rule group if it's missing.

    Returns:
        Dict with created status
    """
    # Check for new name
    existing_new = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.name == "Strip State/Province Tags"
    ).first()

    if existing_new:
        logger.info("[NORMALIZE-MIGRATE] Strip State/Province Tags rule group already exists")
        return {"created": False}

    # Upgrade old suffix-only rule to contains/remove
    existing_old = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.name == "Strip State/Province Suffixes"
    ).first()

    if existing_old:
        existing_old.name = "Strip State/Province Tags"
        existing_old.description = "Remove US state and Canadian province abbreviations from channel names"
        # Update the rule inside the group too
        old_rule = db.query(NormalizationRule).filter(
            NormalizationRule.group_id == existing_old.id
        ).first()
        if old_rule:
            old_rule.tag_match_position = "contains"
            old_rule.action_type = "remove"
        db.commit()
        logger.info("[NORMALIZE-MIGRATE] Upgraded 'Strip State/Province Suffixes' to 'Strip State/Province Tags' (contains/remove)")
        return {"created": False, "upgraded": True}

    total_groups = db.query(NormalizationRuleGroup).count()
    if total_groups == 0:
        logger.info("[NORMALIZE-MIGRATE] No rule groups exist yet, skipping (create_demo_rules will handle)")
        return {"created": False}

    state_group = db.query(TagGroup).filter(
        TagGroup.name == "State/Province Tags"
    ).first()

    if not state_group:
        logger.warning("[NORMALIZE-MIGRATE] State/Province Tags tag group not found, skipping rule creation")
        return {"created": False}

    rule_group = NormalizationRuleGroup(
        name="Strip State/Province Tags",
        description="Remove US state and Canadian province abbreviations from channel names",
        enabled=False,
        priority=4,
        is_builtin=False
    )
    db.add(rule_group)
    db.flush()

    rule = NormalizationRule(
        group_id=rule_group.id,
        name="Match State/Province Tags",
        description="Matches any tag from 'State/Province Tags' and removes it",
        enabled=True,
        priority=0,
        condition_type="tag_group",
        tag_group_id=state_group.id,
        tag_match_position="contains",
        action_type="remove",
        is_builtin=False
    )
    db.add(rule)
    db.commit()

    logger.info("[NORMALIZE-MIGRATE] Created 'Strip State/Province Tags' rule group with tag group condition")
    return {"created": True}


def ensure_title_case_rule(db: Session) -> dict:
    """
    Ensure the 'Title Case' normalization rule group exists.

    For existing installations that already have normalization rules,
    this adds the Title Case rule group if it's missing.

    Returns:
        Dict with created status
    """
    existing = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.name == "Title Case"
    ).first()

    if existing:
        logger.info("[NORMALIZE-MIGRATE] Title Case rule group already exists")
        return {"created": False}

    total_groups = db.query(NormalizationRuleGroup).count()
    if total_groups == 0:
        logger.info("[NORMALIZE-MIGRATE] No rule groups exist yet, skipping (create_demo_rules will handle)")
        return {"created": False}

    rule_group = NormalizationRuleGroup(
        name="Title Case",
        description="Convert channel names to title case, preserving short abbreviations (ESPN, HBO, etc.)",
        enabled=False,
        priority=99,
        is_builtin=False
    )
    db.add(rule_group)
    db.flush()

    rule = NormalizationRule(
        group_id=rule_group.id,
        name="Smart Title Case",
        description="Title-cases text while preserving short all-caps words (likely abbreviations)",
        enabled=True,
        priority=0,
        condition_type="always",
        action_type="capitalize",
        action_value="title",
        is_builtin=False
    )
    db.add(rule)
    db.commit()

    logger.info("[NORMALIZE-MIGRATE] Created 'Title Case' rule group")
    return {"created": True}


# Map a strip rule group's name to the tag group it should match against.
# Derived from DEMO_RULE_CONFIGS (the canonical wiring create_demo_rules uses)
# so this stays in sync if the demo configs change. Legacy group names that
# create_demo_rules has since renamed (e.g. the suffix-only State/Province
# variant) are aliased explicitly.
RULE_GROUP_TO_TAG_GROUP = {
    cfg["rule_group_name"]: cfg["tag_group_name"] for cfg in DEMO_RULE_CONFIGS
}
RULE_GROUP_TO_TAG_GROUP["Strip State/Province Suffixes"] = "State/Province Tags"

# Map a directional action_type to its tag_match_position. The strip rules
# already encode position in their action_type (strip_prefix -> prefix,
# strip_suffix -> suffix); 'remove' has no direction so it matches anywhere
# (contains). This is the robust derivation requested by znc76.3 — position is
# inferred from the rule, not hardcoded by index.
ACTION_TYPE_TO_POSITION = {
    "strip_prefix": "prefix",
    "strip_suffix": "suffix",
    "remove": "contains",
}


def backfill_tag_group_rule_ids(db: Session) -> dict:
    """
    Repair tag_group rules whose tag_group_id was never wired (CONFIG drift).

    For pre-existing installations, the strip rule groups were created before
    create_demo_rules learned to wire tag_group_id via a name->id map. Those
    rules have condition_type='tag_group' but tag_group_id IS NULL, so the
    engine matcher short-circuits to no-match (normalization_engine.py
    _match_condition) and the strips never fire. This backfills the missing
    tag_group_id (and tag_match_position) by matching each rule's parent group
    to its tag group BY NAME — reusing the exact name->id approach
    create_demo_rules uses.

    Strictly idempotent:
    - Only rules with condition_type='tag_group' AND tag_group_id IS NULL are
      touched. Already-wired rules are left untouched, so re-running is a no-op.
    - tag_match_position is derived from the rule's action_type (strip_prefix ->
      prefix, strip_suffix -> suffix, remove -> contains), falling back to the
      DEMO_RULE_CONFIGS match_position for the rule's group when action_type is
      ambiguous. Position is only set when currently NULL.

    Conservative: if a rule's parent group name does not map to a known tag
    group, the rule is skipped (logged) rather than guessed.

    Returns:
        Dict with counts of rules wired and rules skipped.
    """
    # Find every tag_group rule that was never wired to a tag group.
    null_rules = db.query(NormalizationRule).filter(
        NormalizationRule.condition_type == "tag_group",
        NormalizationRule.tag_group_id.is_(None)
    ).all()

    if not null_rules:
        logger.info("[NORMALIZE-MIGRATE] No tag_group rules with NULL tag_group_id; backfill is a no-op")
        return {"rules_wired": 0, "rules_skipped": 0}

    # Build the name -> id map exactly like create_demo_rules does.
    tag_group_map = {tg.name: tg.id for tg in db.query(TagGroup).all()}

    # Cache parent group names so we can map rule -> rule group -> tag group.
    group_name_by_id = {
        g.id: g.name for g in db.query(NormalizationRuleGroup).all()
    }

    # Cache the demo match_position fallback keyed by tag group name.
    demo_position_by_tag_group = {
        cfg["tag_group_name"]: cfg["match_position"] for cfg in DEMO_RULE_CONFIGS
    }

    wired = 0
    skipped = 0

    for rule in null_rules:
        parent_group_name = group_name_by_id.get(rule.group_id)
        tag_group_name = RULE_GROUP_TO_TAG_GROUP.get(parent_group_name)

        if not tag_group_name:
            logger.warning(
                "[NORMALIZE-MIGRATE] Rule id=%s in group '%s' has no known tag-group "
                "mapping; skipping (conservative)",
                rule.id, parent_group_name
            )
            skipped += 1
            continue

        tag_group_id = tag_group_map.get(tag_group_name)
        if not tag_group_id:
            logger.warning(
                "[NORMALIZE-MIGRATE] Tag group '%s' not found for rule id=%s; skipping",
                tag_group_name, rule.id
            )
            skipped += 1
            continue

        rule.tag_group_id = tag_group_id

        # Only set position when it's missing; derive from action_type first,
        # then fall back to the demo config for this tag group, then 'contains'.
        if not rule.tag_match_position:
            position = ACTION_TYPE_TO_POSITION.get(rule.action_type)
            if not position:
                position = demo_position_by_tag_group.get(tag_group_name, "contains")
            rule.tag_match_position = position

        wired += 1
        logger.info(
            "[NORMALIZE-MIGRATE] Wired rule id=%s ('%s') -> tag group '%s' (id=%s, position=%s)",
            rule.id, parent_group_name, tag_group_name, tag_group_id, rule.tag_match_position
        )

    if wired > 0:
        db.commit()
        logger.info(
            "[NORMALIZE-MIGRATE] Backfilled tag_group_id on %s rule(s); %s skipped",
            wired, skipped
        )
    else:
        logger.info(
            "[NORMALIZE-MIGRATE] No tag_group rules wired (%s skipped)", skipped
        )

    return {"rules_wired": wired, "rules_skipped": skipped}


def set_league_strip_require_delimiter(db: Session) -> dict:
    """Set ``require_delimiter=True`` on the existing league-strip rule (bd-0emgo.2).

    The "Strip League Prefixes" rule strips a league tag (NFL/MLB/NHL/...) off
    the START of a channel name. Without a delimiter requirement it also strips
    on a BARE SPACE, which corrupts brands: "NFL RedZone" -> "RedZone",
    "NFL Network" -> "Network". The fix requires a STRONG delimiter (':', '-',
    '|', '/') after the tag so "NFL: Buffalo Bills" / "NFL | X" still strip
    while "NFL RedZone" is preserved.

    Fresh installs get this via create_demo_rules (the league DEMO_RULE_CONFIGS
    entry carries ``require_delimiter: True``). This boot migration flips it on
    for EXISTING operators upgrading to the fixed release.

    Robust identification (do NOT rely on rule-group name alone — operators may
    have renamed it): a rule qualifies iff it is a ``strip_prefix`` tag_group
    rule whose ``tag_group_id`` resolves to the "League Tags" tag group. This
    keys off the tag group's identity, which the operator cannot rename (it's a
    built-in group name), rather than the user-editable rule/group name.

    Strictly idempotent and safe if absent:
    - No "League Tags" group -> no-op (returns updated=0).
    - No matching strip_prefix rule -> no-op.
    - Rules already True are left untouched; only rows flipped False->True are
      counted and committed, so re-running is a no-op.

    Returns:
        Dict with count of rules updated.
    """
    league_group = db.query(TagGroup).filter(
        TagGroup.name == "League Tags"
    ).first()

    if not league_group:
        logger.info(
            "[NORMALIZE-MIGRATE] No 'League Tags' tag group; league delimiter "
            "fix is a no-op"
        )
        return {"updated": 0}

    # A league strip is a strip_prefix tag_group rule pointed at League Tags.
    # (strip_suffix / contains league rules are not the brand-corruption vector
    # this fix targets, so they are intentionally left alone.)
    candidates = db.query(NormalizationRule).filter(
        NormalizationRule.condition_type == "tag_group",
        NormalizationRule.tag_group_id == league_group.id,
        NormalizationRule.action_type == "strip_prefix",
    ).all()

    updated = 0
    for rule in candidates:
        # Only flip rows that are currently False/NULL -> idempotent re-run.
        if not rule.require_delimiter:
            rule.require_delimiter = True
            updated += 1
            logger.info(
                "[NORMALIZE-MIGRATE] Set require_delimiter=True on league strip "
                "rule id=%s ('%s')",
                rule.id, rule.name
            )

    if updated > 0:
        db.commit()
        logger.info(
            "[NORMALIZE-MIGRATE] Enabled strong-delimiter requirement on %s "
            "league strip rule(s)",
            updated
        )
    else:
        logger.info(
            "[NORMALIZE-MIGRATE] League strip rule(s) already require a strong "
            "delimiter (or none present); no-op"
        )

    return {"updated": updated}


# Acronyms that title-casing must preserve. "US" and "EU" contain a vowel and
# are short, so the engine's structural heuristic would lowercase them
# (US -> Us) unless they're in the Abbreviation Tags group. "UK" is vowel-free
# and already preserved, but we ensure it for consistency / explicitness.
REQUIRED_ABBREVIATION_ACRONYMS = ["US", "UK", "EU"]


def ensure_abbreviation_tags_acronyms(db: Session) -> dict:
    """
    Ensure 'US', 'UK', 'EU' exist in the 'Abbreviation Tags' tag group so the
    Title Case rule preserves them instead of lowercasing (US -> Us).

    The Abbreviation Tags seed (database._seed_builtin_tags) includes "USA"
    but not "US"/"UK"/"EU", so country acronyms left after stripping get
    corrupted by Smart Title Case. This adds only the missing acronyms.

    Strictly idempotent: only acronyms that are missing are added. After adding,
    the engine's abbreviation cache is invalidated so the next normalization
    reload picks them up.

    Returns:
        Dict with count of tags added.
    """
    from models import Tag

    abbr_group = db.query(TagGroup).filter(
        TagGroup.name == "Abbreviation Tags"
    ).first()

    if not abbr_group:
        logger.warning(
            "[NORMALIZE-MIGRATE] Abbreviation Tags tag group not found; skipping acronym fix"
        )
        return {"tags_added": 0, "skipped": True}

    added = 0
    for value in REQUIRED_ABBREVIATION_ACRONYMS:
        existing = db.query(Tag).filter(
            Tag.group_id == abbr_group.id,
            Tag.value == value
        ).first()
        if not existing:
            db.add(Tag(
                group_id=abbr_group.id,
                value=value,
                case_sensitive=False,
                enabled=True,
                is_builtin=True
            ))
            added += 1

    if added > 0:
        db.commit()
        # Drop the cached abbreviation set so the engine reloads the new tags.
        try:
            from normalization_engine import clear_abbreviation_cache
            clear_abbreviation_cache()
        except Exception as e:  # pragma: no cover - defensive
            logger.debug("[NORMALIZE-MIGRATE] Could not clear abbreviation cache: %s", e)
        logger.info(
            "[NORMALIZE-MIGRATE] Added %s missing acronym(s) to Abbreviation Tags (US/UK/EU)",
            added
        )
    else:
        logger.info("[NORMALIZE-MIGRATE] Abbreviation Tags already has US/UK/EU")

    return {"tags_added": added, "skipped": False}
