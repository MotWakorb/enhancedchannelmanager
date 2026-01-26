"""
Migration script to create built-in normalization rules from existing tag groups.

This creates default rule groups and rules that match the behavior of the
existing tag-based normalization system in the frontend.
"""
import logging
from sqlalchemy.orm import Session

from models import NormalizationRuleGroup, NormalizationRule

logger = logging.getLogger(__name__)

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
        "description": "Strip timezone suffixes (EAST, WEST, EST, PST, etc.)",
        "priority": 2,
        "tags": [
            "EAST", "WEST", "EST", "PST", "CST", "MST", "EDT", "PDT",
            "CDT", "MDT", "ET", "PT", "CT", "MT", "EASTERN", "WESTERN",
            "CENTRAL", "MOUNTAIN", "PACIFIC"
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


def create_builtin_rules(db: Session, force: bool = False) -> dict:
    """
    Create built-in normalization rule groups and rules.

    Args:
        db: Database session
        force: If True, recreate even if rules already exist

    Returns:
        Dict with counts of groups and rules created
    """
    # Check if built-in rules already exist
    existing_builtin = db.query(NormalizationRuleGroup).filter(
        NormalizationRuleGroup.is_builtin == True
    ).count()

    if existing_builtin > 0 and not force:
        logger.info(f"Built-in rules already exist ({existing_builtin} groups), skipping migration")
        return {"groups_created": 0, "rules_created": 0, "skipped": True}

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

    for group_def in BUILTIN_GROUPS:
        # Create the group
        group = NormalizationRuleGroup(
            name=group_def["name"],
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

        # Create rules for each tag
        for priority, tag in enumerate(group_def["tags"]):
            # For "both" mode, we need separate prefix and suffix rules
            if mode == "both":
                # Prefix rule
                prefix_rule = NormalizationRule(
                    group_id=group.id,
                    name=f"Strip {tag} prefix",
                    enabled=True,
                    priority=priority * 2,
                    condition_type="starts_with",
                    condition_value=tag,
                    case_sensitive=False,
                    action_type="strip_prefix",
                    is_builtin=True
                )
                db.add(prefix_rule)
                rules_created += 1

                # Suffix rule
                suffix_rule = NormalizationRule(
                    group_id=group.id,
                    name=f"Strip {tag} suffix",
                    enabled=True,
                    priority=priority * 2 + 1,
                    condition_type="ends_with",
                    condition_value=tag,
                    case_sensitive=False,
                    action_type="strip_suffix",
                    is_builtin=True
                )
                db.add(suffix_rule)
                rules_created += 1
            else:
                # Single rule for prefix or suffix
                condition_type = "starts_with" if mode == "prefix" else "ends_with"
                rule = NormalizationRule(
                    group_id=group.id,
                    name=f"Strip {tag}",
                    enabled=True,
                    priority=priority,
                    condition_type=condition_type,
                    condition_value=tag,
                    case_sensitive=False,
                    action_type=action_type,
                    is_builtin=True
                )
                db.add(rule)
                rules_created += 1

    db.commit()
    logger.info(f"Created {groups_created} built-in groups with {rules_created} rules")

    return {
        "groups_created": groups_created,
        "rules_created": rules_created,
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
