# M3U Change Digest

M3U Change Digest, under **Notifications & Reports** in the Settings
navigation, is a scheduled report layered on top of the transport
(SMTP/Discord) configured on the **Notification Settings** page. This
article covers the digest's own settings: frequency, filters, and
recipients. For setting up the SMTP server or Discord webhook the digest
sends through, see [Notifications & Alert
Methods](../notifications/index.md). That's configured once and shared
with scheduled-task alerts.

## Common tasks

### Turn on the digest

1. Configure SMTP and/or a Discord webhook first, under **Settings →
   Notification Settings** ([full walkthrough](../notifications/index.md)).
   This page warns you inline if either is missing.
2. Go to **Settings → M3U Digest**.
3. Under **Digest Notifications**, check **Enable M3U digest emails**.
4. Under **Frequency**, choose how often to send (Immediate sends right
   after each M3U refresh) and, optionally, a minimum-changes threshold so
   a quiet week doesn't produce an empty email.
5. Under **Email Recipients**, add at least one address. This list is
   separate from the Email Alert Recipients used by scheduled-task alerts,
   because a digest audience is often different from an alerting audience.
6. Save.

**Result:** The digest is delivered on the configured schedule once
qualifying changes occur. Click **Send Test Digest** to verify delivery
immediately without waiting for the schedule.

### Keep noisy groups or streams out of the digest

1. Go to **Settings → M3U Digest**.
2. Under **Exclude Patterns**, add regex patterns under **Group Exclude
   Patterns** or **Stream Exclude Patterns** (case-insensitive). For
   example, `ESPN\+` to exclude a noisy PPV-style group, or `PPV.*` to
   exclude individual pay-per-view stream names.
3. Save.

**Result:** Changes in matching groups or streams are omitted from future
digests. Change history is still logged for every account regardless of
this setting. Exclude patterns only scope what gets emailed or posted to
Discord.

### Scope the digest to specific M3U accounts

1. Go to **Settings → M3U Digest**.
2. Under **Account Filter**, choose which M3U accounts to include (default
   is all accounts).
3. Save.

**Result:** Only changes from the selected accounts appear in future
digests.

### Post the digest to Discord

1. Configure a Discord webhook first, under **Settings → Notification
   Settings**. M3U Digest reuses that shared webhook rather than having
   its own.
2. Go to **Settings → M3U Digest**.
3. Under **Discord Notification**, check **Send digest to Discord**.
4. Save.

**Result:** The digest posts to the configured Discord channel on the same
schedule as the email version (or instead of it, if you haven't configured
email recipients).

## Going deeper

- [Notifications & Alert Methods](../notifications/index.md): configuring SMTP, Discord, and Telegram; how scheduled-task alerts differ from this digest.
- [`docs/api.md`](../../api.md): API reference for the M3U digest settings endpoints.
