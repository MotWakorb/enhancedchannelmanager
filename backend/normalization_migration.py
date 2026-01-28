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
        "action_type": "strip_prefix"
    },
    {
        "rule_group_name": "Strip Network Tags",
        "rule_group_description": "Remove network/stream type indicators from channel names",
        "priority": 4,
        "tag_group_name": "Network Tags",
        "match_position": "suffix",  # Default to suffix; users can duplicate for prefix if needed
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
        logger.info(f"Rule groups already exist ({existing_groups}), skipping demo creation")
        return {"groups_created": 0, "rules_created": 0, "custom_rules_created": 0, "skipped": True}

    if force and existing_groups > 0:
        # Delete all existing rule groups and rules
        logger.info(f"Force mode: deleting {existing_groups} existing groups")
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
            logger.warning(f"Tag group '{tag_group_name}' not found, skipping rule")
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
            is_builtin=False  # Editable
        )
        db.add(rule)
        rules_created += 1

        logger.info(f"Created rule group '{config['rule_group_name']}' with tag group condition")

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
        f"Created {groups_created} demo groups (disabled by default) with {rules_created} tag-group rules "
        f"and {custom_rules_created} custom rules"
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
    Remove East/West and other directional suffixes from Timezone Tags rules.

    These suffixes (EAST, WEST, EASTERN, WESTERN, CENTRAL, MOUNTAIN, PACIFIC)
    are often part of legitimate channel names (e.g., "Cinemax East",
    "SportsNet Pacific") and indicate timezone-shifted EPG content. Stripping
    them would incorrectly merge channels with different content/timing.

    Only timezone abbreviations (EST, PST, ET, PT, etc.) are safe to strip.

    Returns:
        Dict with count of rules deleted
    """
    # Tags to remove - these are directional/regional, not abbreviations
    tags_to_remove = ["EAST", "WEST", "EASTERN", "WESTERN", "CENTRAL", "MOUNTAIN", "PACIFIC"]

    # Find the Timezone Tags group by name
    timezone_group = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.name == "Timezone Tags"
    ).first()

    if not timezone_group:
        logger.info("Timezone Tags group not found, skipping fix")
        return {"rules_deleted": 0, "skipped": True}

    # Update the group description to reflect the change
    new_description = "Strip timezone abbreviation suffixes (EST, PST, ET, PT, etc.)"
    if timezone_group.description != new_description:
        timezone_group.description = new_description

    # Delete rules for the directional tags
    deleted = 0
    for tag in tags_to_remove:
        count = db.query(NormalizationRule).filter(
            NormalizationRule.group_id == timezone_group.id,
            NormalizationRule.condition_value == tag
        ).delete()
        deleted += count

    # Also revert any case_sensitive changes we made earlier
    db.query(NormalizationRule).filter(
        NormalizationRule.group_id == timezone_group.id,
        NormalizationRule.case_sensitive == True
    ).update({"case_sensitive": False})

    db.commit()

    if deleted > 0:
        logger.info(f"Removed {deleted} directional suffix rules from Timezone Tags (EAST, WEST, etc.)")
    else:
        logger.info("No directional suffix rules to remove")

    return {"rules_deleted": deleted, "skipped": False}


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
        logger.info(f"Updated {updated} tag-group rules to use strip_prefix/strip_suffix actions")
    else:
        logger.info("No tag-group rules needed action type fixes")

    return {"rules_updated": updated}
