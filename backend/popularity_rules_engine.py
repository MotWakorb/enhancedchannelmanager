"""
Popularity Rules Engine (v0.11.0)

Evaluates and executes popularity rules against channel scores.
Supports conditions like "score > 80" or "in_top_n 10" and actions
like "notify", "add_to_group", "log".

Features:
- Condition evaluation with AND logic for secondary conditions
- Multiple action types with template variable support
- Dry-run mode for testing rules without executing actions
- Safety limits to prevent runaway actions
- Full journal logging of all executed actions
"""
import logging
import json
from datetime import datetime
from typing import Optional, List, Dict, Any

from database import get_session
from models import PopularityRule, ChannelPopularityScore, Notification
import journal

logger = logging.getLogger(__name__)

# Safety limits
DEFAULT_MAX_ACTIONS_PER_RUN = 100  # Max actions a single rule can execute
DEFAULT_MAX_NOTIFICATIONS_PER_RUN = 50  # Max notifications across all rules


class RuleEvaluationResult:
    """Result of evaluating a rule against channels."""

    def __init__(self, rule: PopularityRule):
        self.rule = rule
        self.matched_channels: List[Dict[str, Any]] = []
        self.actions_executed: List[Dict[str, Any]] = []
        self.actions_skipped: List[Dict[str, Any]] = []
        self.errors: List[str] = []

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule.id,
            "rule_name": self.rule.name,
            "matched_count": len(self.matched_channels),
            "actions_executed": len(self.actions_executed),
            "actions_skipped": len(self.actions_skipped),
            "errors": self.errors,
            "matched_channels": [
                {"channel_id": c["channel_id"], "channel_name": c["channel_name"]}
                for c in self.matched_channels[:10]  # Limit for response size
            ],
        }


class PopularityRulesEngine:
    """
    Engine for evaluating and executing popularity rules.

    Usage:
        engine = PopularityRulesEngine(dry_run=True)
        results = engine.evaluate_all_rules()

        # Or evaluate specific rules
        results = engine.evaluate_rules([rule_id_1, rule_id_2])
    """

    def __init__(
        self,
        dry_run: bool = False,
        max_actions_per_rule: int = DEFAULT_MAX_ACTIONS_PER_RUN,
        max_notifications: int = DEFAULT_MAX_NOTIFICATIONS_PER_RUN,
    ):
        """
        Initialize the rules engine.

        Args:
            dry_run: If True, evaluate rules but don't execute actions
            max_actions_per_rule: Safety limit for actions per rule
            max_notifications: Safety limit for total notifications
        """
        self.dry_run = dry_run
        self.max_actions_per_rule = max_actions_per_rule
        self.max_notifications = max_notifications
        self._notifications_sent = 0

    def evaluate_all_rules(self) -> Dict[str, Any]:
        """
        Evaluate all enabled rules against current popularity scores.

        Returns:
            dict with overall results and per-rule details
        """
        session = get_session()
        try:
            # Get all enabled rules, ordered by priority
            rules = session.query(PopularityRule).filter(
                PopularityRule.enabled == True
            ).order_by(PopularityRule.priority.asc()).all()

            if not rules:
                logger.info("No enabled popularity rules to evaluate")
                return {
                    "success": True,
                    "dry_run": self.dry_run,
                    "rules_evaluated": 0,
                    "total_matches": 0,
                    "total_actions": 0,
                    "results": [],
                }

            # Get all popularity scores
            scores = session.query(ChannelPopularityScore).all()
            if not scores:
                logger.info("No popularity scores available for rule evaluation")
                return {
                    "success": True,
                    "dry_run": self.dry_run,
                    "rules_evaluated": len(rules),
                    "total_matches": 0,
                    "total_actions": 0,
                    "results": [],
                }

            # Convert scores to dicts for evaluation
            score_dicts = [self._score_to_dict(s) for s in scores]

            # Evaluate each rule
            results = []
            total_matches = 0
            total_actions = 0

            for rule in rules:
                result = self._evaluate_rule(rule, score_dicts, session)
                results.append(result)
                total_matches += len(result.matched_channels)
                total_actions += len(result.actions_executed)

                # Update rule's last run info
                rule.last_run_at = datetime.utcnow()
                rule.last_matched_count = len(result.matched_channels)

            session.commit()

            logger.info(
                f"Rules evaluation complete: {len(rules)} rules, "
                f"{total_matches} matches, {total_actions} actions"
                f"{' (dry run)' if self.dry_run else ''}"
            )

            return {
                "success": True,
                "dry_run": self.dry_run,
                "rules_evaluated": len(rules),
                "total_matches": total_matches,
                "total_actions": total_actions,
                "results": [r.to_dict() for r in results],
            }

        except Exception as e:
            logger.error(f"Rules evaluation failed: {e}")
            session.rollback()
            return {
                "success": False,
                "dry_run": self.dry_run,
                "error": str(e),
                "rules_evaluated": 0,
                "total_matches": 0,
                "total_actions": 0,
                "results": [],
            }
        finally:
            session.close()

    def evaluate_rules(self, rule_ids: List[int]) -> Dict[str, Any]:
        """
        Evaluate specific rules by ID.

        Args:
            rule_ids: List of rule IDs to evaluate

        Returns:
            dict with evaluation results
        """
        session = get_session()
        try:
            rules = session.query(PopularityRule).filter(
                PopularityRule.id.in_(rule_ids)
            ).order_by(PopularityRule.priority.asc()).all()

            scores = session.query(ChannelPopularityScore).all()
            score_dicts = [self._score_to_dict(s) for s in scores]

            results = []
            total_matches = 0
            total_actions = 0

            for rule in rules:
                result = self._evaluate_rule(rule, score_dicts, session)
                results.append(result)
                total_matches += len(result.matched_channels)
                total_actions += len(result.actions_executed)

                rule.last_run_at = datetime.utcnow()
                rule.last_matched_count = len(result.matched_channels)

            session.commit()

            return {
                "success": True,
                "dry_run": self.dry_run,
                "rules_evaluated": len(rules),
                "total_matches": total_matches,
                "total_actions": total_actions,
                "results": [r.to_dict() for r in results],
            }

        except Exception as e:
            logger.error(f"Rules evaluation failed: {e}")
            session.rollback()
            return {
                "success": False,
                "dry_run": self.dry_run,
                "error": str(e),
            }
        finally:
            session.close()

    def _score_to_dict(self, score: ChannelPopularityScore) -> Dict[str, Any]:
        """Convert a ChannelPopularityScore to a dict for evaluation."""
        return {
            "channel_id": score.channel_id,
            "channel_name": score.channel_name,
            "score": score.score,
            "rank": score.rank,
            "watch_count_7d": score.watch_count_7d,
            "watch_time_7d": score.watch_time_7d,
            "unique_viewers_7d": score.unique_viewers_7d,
            "bandwidth_7d": score.bandwidth_7d,
            "trend": score.trend,
            "trend_percent": score.trend_percent,
        }

    def _evaluate_rule(
        self,
        rule: PopularityRule,
        scores: List[Dict[str, Any]],
        session,
    ) -> RuleEvaluationResult:
        """
        Evaluate a single rule against all channel scores.

        Args:
            rule: The rule to evaluate
            scores: List of channel score dicts
            session: Database session

        Returns:
            RuleEvaluationResult with matches and actions
        """
        result = RuleEvaluationResult(rule)

        logger.debug(f"Evaluating rule '{rule.name}' (id={rule.id})")

        # Find matching channels
        for score in scores:
            if self._check_conditions(rule, score):
                result.matched_channels.append(score)

        logger.debug(f"Rule '{rule.name}' matched {len(result.matched_channels)} channels")

        # Execute actions for matched channels
        actions_count = 0
        for channel in result.matched_channels:
            if actions_count >= self.max_actions_per_rule:
                result.actions_skipped.append({
                    "channel_id": channel["channel_id"],
                    "reason": "max_actions_limit_reached",
                })
                continue

            action_result = self._execute_action(rule, channel, session)
            if action_result.get("executed"):
                result.actions_executed.append(action_result)
                actions_count += 1
            elif action_result.get("skipped"):
                result.actions_skipped.append(action_result)
            elif action_result.get("error"):
                result.errors.append(action_result["error"])

        return result

    def _check_conditions(self, rule: PopularityRule, score: Dict[str, Any]) -> bool:
        """
        Check if a channel score matches the rule's conditions.

        Args:
            rule: The rule with conditions
            score: Channel score dict

        Returns:
            True if all conditions match
        """
        # Check primary condition
        if not self._check_single_condition(
            score,
            rule.condition_metric,
            rule.condition_operator,
            rule.condition_threshold,
        ):
            return False

        # Check secondary condition if present (AND logic)
        if rule.condition_metric_2 and rule.condition_operator_2:
            if not self._check_single_condition(
                score,
                rule.condition_metric_2,
                rule.condition_operator_2,
                rule.condition_threshold_2,
            ):
                return False

        return True

    def _check_single_condition(
        self,
        score: Dict[str, Any],
        metric: str,
        operator: str,
        threshold: float,
    ) -> bool:
        """
        Check a single condition against a channel score.

        Args:
            score: Channel score dict
            metric: The metric to check (score, rank, etc.)
            operator: Comparison operator (gt, gte, lt, lte, eq, in_top_n, etc.)
            threshold: Value to compare against

        Returns:
            True if condition matches
        """
        # Get metric value from score
        value = score.get(metric)
        if value is None:
            logger.warning(f"Unknown metric '{metric}' in rule condition")
            return False

        # Handle special operators
        if operator == "in_top_n":
            # Channel is in top N by rank
            return score.get("rank", float("inf")) <= threshold

        elif operator == "in_bottom_n":
            # Would need total count to calculate bottom N
            # For now, use rank > (total - threshold) approximation
            # This requires knowing total channels, skip for now
            return False

        elif operator == "trending_up":
            # Trend is "up" and trend_percent >= threshold
            return (
                score.get("trend") == "up" and
                score.get("trend_percent", 0) >= threshold
            )

        elif operator == "trending_down":
            # Trend is "down" and abs(trend_percent) >= threshold
            return (
                score.get("trend") == "down" and
                abs(score.get("trend_percent", 0)) >= threshold
            )

        # Standard comparison operators
        elif operator == "gt":
            return value > threshold
        elif operator == "gte":
            return value >= threshold
        elif operator == "lt":
            return value < threshold
        elif operator == "lte":
            return value <= threshold
        elif operator == "eq":
            return value == threshold

        else:
            logger.warning(f"Unknown operator '{operator}' in rule condition")
            return False

    def _execute_action(
        self,
        rule: PopularityRule,
        channel: Dict[str, Any],
        session,
    ) -> Dict[str, Any]:
        """
        Execute the rule's action for a matched channel.

        Args:
            rule: The rule with action configuration
            channel: The matched channel score dict
            session: Database session

        Returns:
            dict with execution result
        """
        action_type = rule.action_type
        action_value = rule.get_action_value()

        # Dry run - don't actually execute
        if self.dry_run:
            return {
                "executed": True,
                "dry_run": True,
                "action_type": action_type,
                "channel_id": channel["channel_id"],
                "channel_name": channel["channel_name"],
            }

        # Execute based on action type
        try:
            if action_type == "notify":
                return self._action_notify(rule, channel, action_value, session)

            elif action_type == "log":
                return self._action_log(rule, channel, action_value)

            elif action_type == "add_to_group":
                return self._action_add_to_group(rule, channel, action_value, session)

            elif action_type == "remove_from_group":
                return self._action_remove_from_group(rule, channel, action_value, session)

            elif action_type == "set_channel_number":
                return self._action_set_channel_number(rule, channel, action_value, session)

            else:
                logger.warning(f"Unknown action type '{action_type}'")
                return {
                    "skipped": True,
                    "reason": f"unknown_action_type: {action_type}",
                    "channel_id": channel["channel_id"],
                }

        except Exception as e:
            logger.error(f"Action execution failed: {e}")
            return {
                "error": str(e),
                "action_type": action_type,
                "channel_id": channel["channel_id"],
            }

    def _format_template(self, template: str, channel: Dict[str, Any], rule: PopularityRule) -> str:
        """
        Format a template string with channel and rule variables.

        Supported variables:
        - {channel_name}: Channel name
        - {channel_id}: Channel ID
        - {score}: Popularity score
        - {rank}: Current rank
        - {trend}: Trend direction
        - {trend_percent}: Trend percentage
        - {rule_name}: Rule name
        """
        if not template:
            return ""

        return template.format(
            channel_name=channel.get("channel_name", "Unknown"),
            channel_id=channel.get("channel_id", ""),
            score=channel.get("score", 0),
            rank=channel.get("rank", 0),
            trend=channel.get("trend", "stable"),
            trend_percent=channel.get("trend_percent", 0),
            rule_name=rule.name,
            watch_count=channel.get("watch_count_7d", 0),
            watch_time=channel.get("watch_time_7d", 0),
            unique_viewers=channel.get("unique_viewers_7d", 0),
        )

    def _action_notify(
        self,
        rule: PopularityRule,
        channel: Dict[str, Any],
        action_value: Any,
        session,
    ) -> Dict[str, Any]:
        """Send a notification for the matched channel."""
        # Check notification limit
        if self._notifications_sent >= self.max_notifications:
            return {
                "skipped": True,
                "reason": "max_notifications_limit_reached",
                "channel_id": channel["channel_id"],
            }

        # Format message template
        if isinstance(action_value, dict):
            message = self._format_template(
                action_value.get("message", "Channel {channel_name} matched rule {rule_name}"),
                channel,
                rule,
            )
            title = action_value.get("title", f"Popularity Rule: {rule.name}")
            notification_type = action_value.get("type", "info")
        else:
            message = self._format_template(
                action_value or "Channel {channel_name} matched rule {rule_name}",
                channel,
                rule,
            )
            title = f"Popularity Rule: {rule.name}"
            notification_type = "info"

        # Create notification
        notification = Notification(
            type=notification_type,
            title=title,
            message=message,
            source="popularity_rules",
            source_id=f"rule_{rule.id}_{channel['channel_id']}",
        )
        session.add(notification)
        self._notifications_sent += 1

        # Log to journal
        journal.log_entry(
            category="popularity_rule",
            action_type="notify",
            entity_name=channel["channel_name"],
            description=f"Rule '{rule.name}' sent notification for channel '{channel['channel_name']}'",
            entity_id=rule.id,
            user_initiated=False,
        )

        return {
            "executed": True,
            "action_type": "notify",
            "channel_id": channel["channel_id"],
            "channel_name": channel["channel_name"],
            "message": message,
        }

    def _action_log(
        self,
        rule: PopularityRule,
        channel: Dict[str, Any],
        action_value: Any,
    ) -> Dict[str, Any]:
        """Log an entry to the journal for the matched channel."""
        message = self._format_template(
            action_value or "Channel {channel_name} matched rule {rule_name} (score: {score}, rank: {rank})",
            channel,
            rule,
        )

        journal.log_entry(
            category="popularity_rule",
            action_type="rule_match",
            entity_name=channel["channel_name"],
            description=message,
            entity_id=rule.id,
            after_value={
                "channel_id": channel["channel_id"],
                "score": channel.get("score"),
                "rank": channel.get("rank"),
                "trend": channel.get("trend"),
            },
            user_initiated=False,
        )

        return {
            "executed": True,
            "action_type": "log",
            "channel_id": channel["channel_id"],
            "channel_name": channel["channel_name"],
            "message": message,
        }

    def _action_add_to_group(
        self,
        rule: PopularityRule,
        channel: Dict[str, Any],
        action_value: Any,
        session,
    ) -> Dict[str, Any]:
        """
        Add the channel to a group.

        Note: This is a placeholder - actual implementation depends on
        how channel groups are managed in the system.
        """
        group_name = action_value if isinstance(action_value, str) else action_value.get("group")

        if not group_name:
            return {
                "skipped": True,
                "reason": "no_group_specified",
                "channel_id": channel["channel_id"],
            }

        # Log the action (actual group management would go here)
        journal.log_entry(
            category="popularity_rule",
            action_type="add_to_group",
            entity_name=channel["channel_name"],
            description=f"Rule '{rule.name}' added channel '{channel['channel_name']}' to group '{group_name}'",
            entity_id=rule.id,
            after_value={"group": group_name},
            user_initiated=False,
        )

        logger.info(f"Would add channel '{channel['channel_name']}' to group '{group_name}'")

        return {
            "executed": True,
            "action_type": "add_to_group",
            "channel_id": channel["channel_id"],
            "channel_name": channel["channel_name"],
            "group": group_name,
        }

    def _action_remove_from_group(
        self,
        rule: PopularityRule,
        channel: Dict[str, Any],
        action_value: Any,
        session,
    ) -> Dict[str, Any]:
        """
        Remove the channel from a group.

        Note: This is a placeholder - actual implementation depends on
        how channel groups are managed in the system.
        """
        group_name = action_value if isinstance(action_value, str) else action_value.get("group")

        if not group_name:
            return {
                "skipped": True,
                "reason": "no_group_specified",
                "channel_id": channel["channel_id"],
            }

        # Log the action
        journal.log_entry(
            category="popularity_rule",
            action_type="remove_from_group",
            entity_name=channel["channel_name"],
            description=f"Rule '{rule.name}' removed channel '{channel['channel_name']}' from group '{group_name}'",
            entity_id=rule.id,
            after_value={"group": group_name},
            user_initiated=False,
        )

        logger.info(f"Would remove channel '{channel['channel_name']}' from group '{group_name}'")

        return {
            "executed": True,
            "action_type": "remove_from_group",
            "channel_id": channel["channel_id"],
            "channel_name": channel["channel_name"],
            "group": group_name,
        }

    def _action_set_channel_number(
        self,
        rule: PopularityRule,
        channel: Dict[str, Any],
        action_value: Any,
        session,
    ) -> Dict[str, Any]:
        """
        Set the channel number.

        Note: This is a placeholder - actual implementation would update
        the channel's number in the database.
        """
        channel_number = action_value

        if channel_number == "auto":
            # Auto-assign based on rank
            channel_number = channel.get("rank", 0) * 10

        # Log the action
        journal.log_entry(
            category="popularity_rule",
            action_type="set_channel_number",
            entity_name=channel["channel_name"],
            description=f"Rule '{rule.name}' set channel number for '{channel['channel_name']}' to {channel_number}",
            entity_id=rule.id,
            after_value={"channel_number": channel_number},
            user_initiated=False,
        )

        logger.info(f"Would set channel '{channel['channel_name']}' number to {channel_number}")

        return {
            "executed": True,
            "action_type": "set_channel_number",
            "channel_id": channel["channel_id"],
            "channel_name": channel["channel_name"],
            "channel_number": channel_number,
        }


# Convenience functions

def evaluate_all_rules(dry_run: bool = False) -> Dict[str, Any]:
    """
    Evaluate all enabled popularity rules.

    Args:
        dry_run: If True, don't execute actions

    Returns:
        Evaluation results dict
    """
    engine = PopularityRulesEngine(dry_run=dry_run)
    return engine.evaluate_all_rules()


def evaluate_rules(rule_ids: List[int], dry_run: bool = False) -> Dict[str, Any]:
    """
    Evaluate specific popularity rules.

    Args:
        rule_ids: List of rule IDs to evaluate
        dry_run: If True, don't execute actions

    Returns:
        Evaluation results dict
    """
    engine = PopularityRulesEngine(dry_run=dry_run)
    return engine.evaluate_rules(rule_ids)
