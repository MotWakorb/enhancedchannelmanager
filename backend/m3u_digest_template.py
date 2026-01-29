"""
M3U Digest Email Template.

HTML and plain text templates for M3U change digest emails.
"""
from datetime import datetime
from typing import List, Dict
from collections import defaultdict

from models import M3UChangeLog


class M3UDigestTemplate:
    """
    Template renderer for M3U change digest emails.
    Supports both HTML and plain text formats.
    """

    # Colors for different change types
    COLORS = {
        "group_added": "#22C55E",      # Green
        "group_removed": "#EF4444",     # Red
        "streams_added": "#3B82F6",     # Blue
        "streams_removed": "#F59E0B",   # Orange
    }

    # Labels for change types
    LABELS = {
        "group_added": "Groups Added",
        "group_removed": "Groups Removed",
        "streams_added": "Streams Added",
        "streams_removed": "Streams Removed",
    }

    def get_subject(self, changes: List[M3UChangeLog]) -> str:
        """Generate email subject line."""
        total = len(changes)
        accounts = len(set(c.m3u_account_id for c in changes))

        if accounts == 1:
            return f"[ECM] M3U Digest: {total} change{'s' if total != 1 else ''} detected"
        else:
            return f"[ECM] M3U Digest: {total} change{'s' if total != 1 else ''} across {accounts} accounts"

    def _group_changes(self, changes: List[M3UChangeLog]) -> Dict:
        """Group changes by account and type."""
        by_account = defaultdict(lambda: defaultdict(list))

        for change in changes:
            by_account[change.m3u_account_id][change.change_type].append(change)

        return by_account

    def _get_summary(self, changes: List[M3UChangeLog]) -> Dict:
        """Get summary statistics."""
        summary = {
            "total": len(changes),
            "groups_added": 0,
            "groups_removed": 0,
            "streams_added": 0,
            "streams_removed": 0,
            "accounts": set(),
        }

        for change in changes:
            summary["accounts"].add(change.m3u_account_id)
            if change.change_type == "group_added":
                summary["groups_added"] += 1
            elif change.change_type == "group_removed":
                summary["groups_removed"] += 1
            elif change.change_type == "streams_added":
                summary["streams_added"] += change.count
            elif change.change_type == "streams_removed":
                summary["streams_removed"] += change.count

        summary["account_count"] = len(summary["accounts"])
        return summary

    def render_html(self, changes: List[M3UChangeLog], since: datetime) -> str:
        """Render HTML email content."""
        summary = self._get_summary(changes)
        by_account = self._group_changes(changes)

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 700px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: #ffffff;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #6366F1 0%, #8B5CF6 100%);
            color: white;
            padding: 24px;
            text-align: center;
        }}
        .header h1 {{
            margin: 0;
            font-size: 24px;
            font-weight: 600;
        }}
        .header p {{
            margin: 8px 0 0 0;
            opacity: 0.9;
            font-size: 14px;
        }}
        .summary {{
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            padding: 20px;
            background-color: #f8f9fa;
            border-bottom: 1px solid #e9ecef;
        }}
        .summary-item {{
            flex: 1;
            min-width: 120px;
            text-align: center;
            padding: 12px;
            background: white;
            border-radius: 6px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }}
        .summary-item .number {{
            font-size: 28px;
            font-weight: 700;
            display: block;
        }}
        .summary-item .label {{
            font-size: 12px;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .summary-item.added .number {{ color: #22C55E; }}
        .summary-item.removed .number {{ color: #EF4444; }}
        .content {{
            padding: 20px;
        }}
        .account-section {{
            margin-bottom: 24px;
            border: 1px solid #e9ecef;
            border-radius: 6px;
            overflow: hidden;
        }}
        .account-header {{
            background-color: #f8f9fa;
            padding: 12px 16px;
            font-weight: 600;
            border-bottom: 1px solid #e9ecef;
        }}
        .change-group {{
            padding: 12px 16px;
            border-bottom: 1px solid #f0f0f0;
        }}
        .change-group:last-child {{
            border-bottom: none;
        }}
        .change-type {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
            color: white;
            margin-bottom: 8px;
        }}
        .change-type.group_added {{ background-color: #22C55E; }}
        .change-type.group_removed {{ background-color: #EF4444; }}
        .change-type.streams_added {{ background-color: #3B82F6; }}
        .change-type.streams_removed {{ background-color: #F59E0B; }}
        .change-list {{
            margin: 0;
            padding-left: 20px;
            color: #555;
        }}
        .change-list li {{
            margin: 4px 0;
        }}
        .stream-count {{
            color: #888;
            font-size: 13px;
        }}
        .footer {{
            padding: 16px 20px;
            background-color: #f8f9fa;
            border-top: 1px solid #e9ecef;
            font-size: 12px;
            color: #666;
            text-align: center;
        }}
        .footer a {{
            color: #6366F1;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>M3U Playlist Changes</h1>
            <p>Since {since.strftime('%Y-%m-%d %H:%M UTC')}</p>
        </div>

        <div class="summary">
            <div class="summary-item added">
                <span class="number">+{summary['groups_added']}</span>
                <span class="label">Groups Added</span>
            </div>
            <div class="summary-item removed">
                <span class="number">-{summary['groups_removed']}</span>
                <span class="label">Groups Removed</span>
            </div>
            <div class="summary-item added">
                <span class="number">+{summary['streams_added']}</span>
                <span class="label">Streams Added</span>
            </div>
            <div class="summary-item removed">
                <span class="number">-{summary['streams_removed']}</span>
                <span class="label">Streams Removed</span>
            </div>
        </div>

        <div class="content">
"""

        # Add per-account sections
        for account_id, type_changes in by_account.items():
            html += f"""
            <div class="account-section">
                <div class="account-header">M3U Account #{account_id}</div>
"""
            for change_type, change_list in type_changes.items():
                label = self.LABELS.get(change_type, change_type)
                html += f"""
                <div class="change-group">
                    <span class="change-type {change_type}">{label}</span>
                    <ul class="change-list">
"""
                for change in change_list[:20]:  # Limit to 20 items per type
                    if change.group_name:
                        if change.change_type in ("group_added", "group_removed"):
                            html += f'<li><strong>{change.group_name}</strong> <span class="stream-count">({change.count} streams)</span></li>\n'
                        else:
                            stream_names = change.get_stream_names()
                            if stream_names:
                                names_preview = ", ".join(stream_names[:3])
                                if len(stream_names) > 3:
                                    names_preview += f" and {len(stream_names) - 3} more"
                                html += f'<li><strong>{change.group_name}</strong>: {change.count} streams <span class="stream-count">({names_preview})</span></li>\n'
                            else:
                                html += f'<li><strong>{change.group_name}</strong>: {change.count} streams</li>\n'
                    else:
                        html += f'<li>{change.count} items</li>\n'

                if len(change_list) > 20:
                    html += f'<li><em>... and {len(change_list) - 20} more</em></li>\n'

                html += """
                    </ul>
                </div>
"""
            html += """
            </div>
"""

        html += f"""
        </div>

        <div class="footer">
            Sent from Enhanced Channel Manager<br>
            Generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}
        </div>
    </div>
</body>
</html>
"""
        return html

    def render_plain(self, changes: List[M3UChangeLog], since: datetime) -> str:
        """Render plain text email content."""
        summary = self._get_summary(changes)
        by_account = self._group_changes(changes)

        lines = [
            "=" * 50,
            "M3U PLAYLIST CHANGES",
            f"Since: {since.strftime('%Y-%m-%d %H:%M UTC')}",
            "=" * 50,
            "",
            "SUMMARY",
            "-" * 20,
            f"  Groups Added:    +{summary['groups_added']}",
            f"  Groups Removed:  -{summary['groups_removed']}",
            f"  Streams Added:   +{summary['streams_added']}",
            f"  Streams Removed: -{summary['streams_removed']}",
            f"  Accounts:        {summary['account_count']}",
            "",
        ]

        # Add per-account sections
        for account_id, type_changes in by_account.items():
            lines.append("=" * 50)
            lines.append(f"M3U ACCOUNT #{account_id}")
            lines.append("=" * 50)

            for change_type, change_list in type_changes.items():
                label = self.LABELS.get(change_type, change_type)
                lines.append("")
                lines.append(f"[{label.upper()}]")
                lines.append("-" * 20)

                for change in change_list[:20]:
                    if change.group_name:
                        if change.change_type in ("group_added", "group_removed"):
                            lines.append(f"  * {change.group_name} ({change.count} streams)")
                        else:
                            lines.append(f"  * {change.group_name}: {change.count} streams")
                    else:
                        lines.append(f"  * {change.count} items")

                if len(change_list) > 20:
                    lines.append(f"  ... and {len(change_list) - 20} more")

            lines.append("")

        lines.extend([
            "-" * 50,
            "Sent from Enhanced Channel Manager",
            f"Generated at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        ])

        return "\n".join(lines)
