"""
Migration script to create built-in normalization rules from existing tag groups.

This creates default rule groups and rules that match the behavior of the
existing tag-based normalization system in the frontend. It also migrates
user settings (disabled_builtin_tags, custom_normalization_tags) to the
new rule-based system.
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session

from models import NormalizationRuleGroup, NormalizationRule

logger = logging.getLogger(__name__)

# Mapping from group name to short key used in disabled_builtin_tags
GROUP_KEY_MAP = {
    "Quality Tags": "quality",
    "Country Tags": "country",
    "Timezone Tags": "timezone",
    "League Tags": "league",
    "Network Tags": "network"
}

# Built-in tag groups matching frontend/src/constants/streamNormalization.ts
BUILTIN_GROUPS = [
    {
        "name": "Quality Tags",
        "description": "Strip quality/resolution suffixes (HD, FHD, UHD, 4K, SD, etc.)",
        "priority": 0,
        "tags": [
            "4K", "UHD", "FHD", "QHD", "HD", "SD",
            "2160P", "1440P", "1080P", "1080I", "720P", "576P", "576I",
            "540P", "480P", "480I", "360P", "HEVC", "H265", "H.265",
            "H264", "H.264", "HDR", "HDR10", "DOLBY VISION", "DV"
        ],
        "mode": "suffix"
    },
    {
        "name": "Country Tags",
        "description": "Strip country prefixes (US, UK, CA, etc.)",
        "priority": 1,
        "tags": [
            "US", "USA", "UK", "GB", "CA", "AU", "NZ", "IE", "IN", "PK",
            "ZA", "NG", "GH", "KE", "PH", "MY", "SG", "HK", "TW", "JP",
            "KR", "TH", "VN", "ID", "BR", "MX", "AR", "CO", "CL", "PE",
            "ES", "FR", "DE", "IT", "PT", "NL", "BE", "AT", "CH", "PL",
            "RU", "UA", "TR", "GR", "SE", "NO", "DK", "FI", "CZ", "RO",
            "HU", "BG", "HR", "RS", "SI", "SK", "EE", "LV", "LT", "IL",
            "AE", "SA", "QA", "KW", "BH", "OM", "EG", "MA", "DZ", "TN"
        ],
        "mode": "prefix"
    },
    {
        "name": "Timezone Tags",
        "description": "Strip timezone abbreviation suffixes (EST, PST, etc.) - excludes East/West which are often part of channel names",
        "priority": 2,
        "tags": [
            "EST", "PST", "CST", "MST", "EDT", "PDT",
            "CDT", "MDT", "ET", "PT", "CT", "MT"
        ],
        "mode": "suffix"
    },
    {
        "name": "League Tags",
        "description": "Strip league/sports prefixes (NFL, NBA, NHL, etc.)",
        "priority": 3,
        "tags": [
            "NFL", "NBA", "NHL", "MLB", "MLS", "NCAA", "NCAAF", "NCAAB",
            "UFC", "WWE", "AEW", "PGA", "LPGA", "ATP", "WTA", "F1",
            "NASCAR", "INDYCAR", "MOTOGP", "EPL", "LALIGA", "BUNDESLIGA",
            "SERIE A", "LIGUE 1", "EREDIVISIE", "PRIMEIRA LIGA", "SPL",
            "A-LEAGUE", "J-LEAGUE", "K-LEAGUE", "CSL", "ISL", "MLS",
            "LIGA MX", "BRASILEIRAO", "CONMEBOL", "CONCACAF", "UEFA",
            "FIFA", "FIBA", "IIHF", "WBC", "IBF", "WBA", "WBO"
        ],
        "mode": "prefix"
    },
    {
        "name": "Network Tags",
        "description": "Strip network prefixes/suffixes (PPV, CHAMP, etc.)",
        "priority": 4,
        "tags": [
            "PPV", "CHAMP", "LIVE", "BACKUP", "ALT", "FEED", "MULTI",
            "RED ZONE", "REDZONE", "GAME MIX", "GOAL ZONE", "GOALZONE",
            "ENGLISH", "SPANISH", "FRENCH", "GERMAN", "ITALIAN",
            "PORTUGUESE", "DUTCH", "POLISH", "RUSSIAN", "ARABIC",
            "HINDI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM", "BENGALI"
        ],
        "mode": "both"
    }
]


def create_builtin_rules(
    db: Session,
    force: bool = False,
    disabled_builtin_tags: Optional[list] = None,
    custom_normalization_tags: Optional[list] = None
) -> dict:
    """
    Create built-in normalization rule groups and rules.

    Also migrates user settings:
    - disabled_builtin_tags: List of "group:tag" strings to mark as disabled
    - custom_normalization_tags: List of {"value": str, "mode": str} to create as custom rules

    Args:
        db: Database session
        force: If True, recreate even if rules already exist
        disabled_builtin_tags: Tags to disable (format: "group:TAG", e.g., "country:US")
        custom_normalization_tags: Custom tags to migrate (list of {"value", "mode"})

    Returns:
        Dict with counts of groups and rules created
    """
    disabled_builtin_tags = disabled_builtin_tags or []
    custom_normalization_tags = custom_normalization_tags or []

    # Parse disabled tags into a set for fast lookup
    # Format: "group:TAG" -> ("group", "TAG")
    disabled_set = set()
    for tag_str in disabled_builtin_tags:
        if ":" in tag_str:
            group_key, tag_value = tag_str.split(":", 1)
            disabled_set.add((group_key.lower(), tag_value.upper()))

    # Check if built-in rules already exist
    existing_builtin = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.is_builtin == True
    ).count()

    if existing_builtin > 0 and not force:
        logger.info(f"Built-in rules already exist ({existing_builtin} groups), skipping migration")
        return {"groups_created": 0, "rules_created": 0, "custom_rules_created": 0, "skipped": True}

    if force and existing_builtin > 0:
        # Delete existing built-in rules
        logger.info(f"Force mode: deleting {existing_builtin} existing built-in groups")
        builtin_groups = db.query(NormalizationRuleGroup).filter(
            NormalizationRuleGroup.is_builtin == True
        ).all()
        for group in builtin_groups:
            db.query(NormalizationRule).filter(
                NormalizationRule.group_id == group.id
            ).delete()
            db.delete(group)
        db.commit()

    groups_created = 0
    rules_created = 0
    rules_disabled = 0

    for group_def in BUILTIN_GROUPS:
        group_name = group_def["name"]
        group_key = GROUP_KEY_MAP.get(group_name, group_name.lower().replace(" ", "_"))

        # Create the group
        group = NormalizationRuleGroup(
            name=group_name,
            description=group_def["description"],
            enabled=True,
            priority=group_def["priority"],
            is_builtin=True
        )
        db.add(group)
        db.flush()  # Get the group ID
        groups_created += 1

        # Determine action type based on mode
        mode = group_def["mode"]
        if mode == "prefix":
            action_type = "strip_prefix"
        elif mode == "suffix":
            action_type = "strip_suffix"
        else:  # both
            action_type = "remove"

        # Get case sensitivity setting (default to False for backward compatibility)
        group_case_sensitive = group_def.get("case_sensitive", False)

        # Create rules for each tag
        for priority, tag in enumerate(group_def["tags"]):
            # Check if this tag is disabled by user settings
            tag_disabled = (group_key, tag.upper()) in disabled_set

            # For "both" mode, we need separate prefix and suffix rules
            if mode == "both":
                # Prefix rule
                prefix_rule = NormalizationRule(
                    group_id=group.id,
                    name=f"Strip {tag} prefix",
                    enabled=not tag_disabled,
                    priority=priority * 2,
                    condition_type="starts_with",
                    condition_value=tag,
                    case_sensitive=group_case_sensitive,
                    action_type="strip_prefix",
                    is_builtin=True
                )
                db.add(prefix_rule)
                rules_created += 1
                if tag_disabled:
                    rules_disabled += 1

                # Suffix rule
                suffix_rule = NormalizationRule(
                    group_id=group.id,
                    name=f"Strip {tag} suffix",
                    enabled=not tag_disabled,
                    priority=priority * 2 + 1,
                    condition_type="ends_with",
                    condition_value=tag,
                    case_sensitive=group_case_sensitive,
                    action_type="strip_suffix",
                    is_builtin=True
                )
                db.add(suffix_rule)
                rules_created += 1
                if tag_disabled:
                    rules_disabled += 1
            else:
                # Single rule for prefix or suffix
                condition_type = "starts_with" if mode == "prefix" else "ends_with"
                rule = NormalizationRule(
                    group_id=group.id,
                    name=f"Strip {tag}",
                    enabled=not tag_disabled,
                    priority=priority,
                    condition_type=condition_type,
                    condition_value=tag,
                    case_sensitive=group_case_sensitive,
                    action_type=action_type,
                    is_builtin=True
                )
                db.add(rule)
                rules_created += 1
                if tag_disabled:
                    rules_disabled += 1

    # Create custom rules from user's custom_normalization_tags
    custom_rules_created = 0
    if custom_normalization_tags:
        # Check if custom group already exists
        custom_group = db.query(NormalizationRuleGroup).filter(
            NormalizationRuleGroup.name == "Custom Tags",
            NormalizationRuleGroup.is_builtin == False
        ).first()

        if not custom_group:
            custom_group = NormalizationRuleGroup(
                name="Custom Tags",
                description="User-defined custom normalization tags (migrated from settings)",
                enabled=True,
                priority=100,  # Run after built-in rules
                is_builtin=False
            )
            db.add(custom_group)
            db.flush()
            groups_created += 1

        for priority, tag_def in enumerate(custom_normalization_tags):
            tag_value = tag_def.get("value", "")
            tag_mode = tag_def.get("mode", "both")

            if not tag_value:
                continue

            # Create rules based on mode
            if tag_mode == "both":
                # Prefix rule
                prefix_rule = NormalizationRule(
                    group_id=custom_group.id,
                    name=f"Strip {tag_value} prefix (custom)",
                    enabled=True,
                    priority=priority * 2,
                    condition_type="starts_with",
                    condition_value=tag_value,
                    case_sensitive=False,
                    action_type="strip_prefix",
                    is_builtin=False
                )
                db.add(prefix_rule)
                custom_rules_created += 1

                # Suffix rule
                suffix_rule = NormalizationRule(
                    group_id=custom_group.id,
                    name=f"Strip {tag_value} suffix (custom)",
                    enabled=True,
                    priority=priority * 2 + 1,
                    condition_type="ends_with",
                    condition_value=tag_value,
                    case_sensitive=False,
                    action_type="strip_suffix",
                    is_builtin=False
                )
                db.add(suffix_rule)
                custom_rules_created += 1
            elif tag_mode == "prefix":
                rule = NormalizationRule(
                    group_id=custom_group.id,
                    name=f"Strip {tag_value} prefix (custom)",
                    enabled=True,
                    priority=priority,
                    condition_type="starts_with",
                    condition_value=tag_value,
                    case_sensitive=False,
                    action_type="strip_prefix",
                    is_builtin=False
                )
                db.add(rule)
                custom_rules_created += 1
            else:  # suffix
                rule = NormalizationRule(
                    group_id=custom_group.id,
                    name=f"Strip {tag_value} suffix (custom)",
                    enabled=True,
                    priority=priority,
                    condition_type="ends_with",
                    condition_value=tag_value,
                    case_sensitive=False,
                    action_type="strip_suffix",
                    is_builtin=False
                )
                db.add(rule)
                custom_rules_created += 1

    db.commit()
    logger.info(
        f"Created {groups_created} groups with {rules_created} built-in rules "
        f"({rules_disabled} disabled) and {custom_rules_created} custom rules"
    )

    return {
        "groups_created": groups_created,
        "rules_created": rules_created,
        "rules_disabled": rules_disabled,
        "custom_rules_created": custom_rules_created,
        "skipped": False
    }


def get_migration_status(db: Session) -> dict:
    """
    Get the current status of the normalization rules migration.

    Returns:
        Dict with counts of builtin and custom groups/rules
    """
    builtin_groups = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.is_builtin == True
    ).count()

    custom_groups = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.is_builtin == False
    ).count()

    builtin_rules = db.query(NormalizationRule).filter(
        NormalizationRule.is_builtin == True
    ).count()

    custom_rules = db.query(NormalizationRule).filter(
        NormalizationRule.is_builtin == False
    ).count()

    return {
        "builtin_groups": builtin_groups,
        "custom_groups": custom_groups,
        "builtin_rules": builtin_rules,
        "custom_rules": custom_rules,
        "total_groups": builtin_groups + custom_groups,
        "total_rules": builtin_rules + custom_rules,
        "migration_complete": builtin_groups > 0
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

    # Find the Timezone Tags group
    timezone_group = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.name == "Timezone Tags",
        NormalizationRuleGroup.is_builtin == True
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
