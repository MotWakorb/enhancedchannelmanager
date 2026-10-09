# Notifications & Alert Methods

This index covers the operator workflow end-to-end: configuring each channel and wiring it into scheduled tasks. Per-channel deep dives are linked below and in the Articles table.

## Section purpose

Show operators how to configure ECM's external notification channels: **Email (SMTP)**, **Discord**, **Telegram**, and **ntfy**. Also show how those channels actually get used by **Scheduled Tasks**.

The most common operator question this section answers: *"I enabled email alerts on a scheduled task and nothing arrived. Why?"* The short answer is that two things must both be configured: the channel itself (under **Settings → Notification Settings**), and the task's per-channel toggle (under **Settings → Scheduled Tasks → Edit**). The rest of this page walks through both.

## Articles

| Article | Purpose |
|-|-|
| [Manage the Email Alert Recipients List](email-recipients-deep-dive.md) | RFC 5322 edge cases, paste normalization rules, the Alert Methods data model behind the scenes, migrating from older free-text recipient fields. |
| [What ECM Posts to Discord, and What You Can Change](discord-webhook-customization.md) | Embed formatting, mentioning roles in alerts, splitting alerts across several Discord destinations. |
| [Set Up the Telegram Bot](telegram-bot-setup.md) | Step-by-step BotFather walk-through, locating chat IDs in groups vs. channels vs. supergroups, bot privacy mode caveats. |
| [Route Alerts to the Right Channel](alert-routing-patterns.md) | Worked examples: "errors only to one Discord channel, everything to Telegram," "stop EPG refresh noise on one destination," "info alerts for one task only," etc. |

## How alerts get delivered (the short version)

A scheduled task fires an alert (success / warning / error / info). Whether anything reaches an external destination depends on **three independent gates** on the task, evaluated in order, and then on each destination's own filters:

1. **The task's `send_alerts` master toggle.** If off, no external alert is sent regardless of channel configuration.
2. **The task's per-severity toggle** (`alert_on_success`, `alert_on_warning`, `alert_on_error`, `alert_on_info`). The fired alert's severity must be enabled.
3. **Channel routing.** Email, Discord, and Telegram each have a task-level channel toggle, and each toggle covers every destination of that type. ntfy has no per-task toggle.

If gate 1 or 2 blocks, nothing is sent anywhere. If gate 3 blocks for a type, every destination of that type is skipped; others still fire.

Each destination that is left then applies its own filters, set with the **Edit** button on its row under [Alert Methods](#settings-notification-settings-alert-methods): its **Severities** and its **Alert categories**. Alerts that pass are batched: ECM holds them for 30 seconds from the first one queued, then sends each destination everything it collected as one digest message (a single alert is sent on its own). Alerts still waiting when ECM shuts down are sent before it exits.

Alerts that are not scheduled-task results, such as per-source EPG refresh results, per-account M3U refresh results and manual stream probes, have no task toggles to pass. They go straight to each destination's filters.

The in-app **Notification Center** (the bell icon) is governed by a separate `show_notifications` toggle on the task; it is independent of the gates above.

> **Going deeper:** see API reference → Alert Methods ([`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md), in the repository, not part of this published guide) for the request/response shapes and API reference → Scheduled Tasks (same file) for the task-update endpoints.

## Settings → Email → Public Base URL

**Set this.** It is the address your operators reach ECM at, and it is what ECM puts into links it emails out. Today that means the password-reset link.

Enter the scheme and host only, with no path: `https://ecm.example.com`, or `http://192.168.1.10:6100` if you reach ECM by IP and port. A trailing slash is accepted and removed for you. A value with a path, a query string, a fragment, or embedded credentials is refused when you save, and so is a value with no `http://` or `https://` in front of it.

While the field is empty, ECM falls back to building reset links from the `Host` and `X-Forwarded-Host` headers on the incoming request. Those headers are set by whoever sent the request, not by you, so anyone who knows one of your users' email addresses can make ECM email that user a genuine reset link that points at a host of the attacker's choosing. If the user opens it, their live reset token goes to the attacker. ECM logs a warning at startup while the field is unset.

The **Configured / Not set** badge next to the heading tells you which of the two modes your install is in.

Changing the value is an admin action, like the outbound connection and notification settings. It takes effect on the next reset email; no restart is needed.

## Settings → Notification Settings → SMTP

SMTP is configured **once, globally**. Both M3U Digest reports and per-task email alerts use the same server.

**Required fields:**

| Field | What goes here | Example |
|-|-|-|
| **SMTP Host** | Your provider's outbound server | `smtp.gmail.com` |
| **SMTP Port** | 587 (TLS), 465 (SSL), or 25 (unencrypted) | `587` |
| **Security** | TLS, SSL, or None; TLS is the default | `TLS (STARTTLS)` |
| **Username** | Optional. Usually your full email address | `you@example.com` |
| **Password** | Optional. Use an app password for Gmail / OAuth providers | (kept hidden) |
| **From Email** | The sender address recipients see | `noreply@example.com` |
| **From Name** | Display name in the From header | `ECM Alerts` |

**Verifying it works:**

1. Fill in the SMTP fields above. This page has several save controls: a **Save Recipients** button (for the recipients list below), a page-level **Save Settings** control, and a floating **Save changes** bar. Use the floating **Save changes** bar to save the SMTP fields; it's the verified working path.
2. Scroll to **Test Connection**.
3. Enter a recipient address you control and click **Send Test Email**.
4. A successful test produces a toast that reads *"Test email sent to `<recipient>`"* (for example, "Test email sent to you@example.com") and an email in the destination inbox within a few seconds. The Subject is *"ECM SMTP Test"*.
5. A failed test surfaces the SMTP error verbatim (auth failure, connection refused, TLS handshake error, etc.). Read the error before changing settings.

The **Configured / Unconfigured** badge next to the SMTP heading reflects whether SMTP Host and From Email are both populated. It is independent of whether the test has been run.

## Settings → Notification Settings → Email Alert Recipients

This is where you tell ECM **who receives** scheduled-task email alerts. It is separate from the SMTP server settings above and from the M3U Digest's own recipients list.

> **Why two recipient lists?** The **M3U Digest** has its own recipients field (under **M3U Digest → Email Recipients**) because digests are an opt-in report sent on a digest schedule, often to a different audience than scheduled-task alerts. The list documented here is the one consumed by the **Email** alert channel that scheduled tasks dispatch through.

**To add recipients:**

1. Make sure SMTP (above) is configured first. The hint under the recipients field says so explicitly.
2. Type one or more email addresses in the **Email alert recipients** field. Comma-separated. Pasting a list with semicolons or newlines is normalized to commas automatically.
3. Click **Save Recipients**.

**Validation behavior:**

- Each address is validated against **RFC 5322** at save time. The first invalid address produces an inline error like *"`bad@@example` is not a valid email address. Use a comma-separated list."* Fix it and re-save.
- **Duplicates are removed** automatically. If two addresses normalize to the same value, only one is kept and a toast reports how many were removed, singular- or plural-aware: *"Removed 1 duplicate recipient"* for exactly one, *"Removed N duplicate recipients"* for more than one.
- **Pasted lists** with semicolons (`;`), newlines (`\n`), or carriage returns (`\r`) are normalized to commas at paste time. You can paste from a spreadsheet or another mail client without manual cleanup.
- Whitespace around addresses is trimmed.

**Verifying it works:**

The **Configured / Unconfigured** badge next to the *Email Alert Recipients* heading turns green once at least one recipient is saved. The empty-state hint disappears. A *"Saved at HH:MM"* timestamp confirms the last save.

> **Note:** there is no "send a test alert email" button on the recipients section. The Send Test Email button up in SMTP Configuration only exercises the SMTP server, not the recipients list. To verify recipients end-to-end, run a scheduled task manually (Settings → Scheduled Tasks → Run Now) with a non-empty result and confirm the email arrives.

## Settings → Notification Settings → Alert Methods

The **Alert Methods** section lists every external destination ECM sends alerts to, one row per destination: Email (SMTP), Discord, Telegram, and ntfy. You can have as many Discord and Telegram destinations as you want, for example one Discord channel that receives only errors and another that receives everything. Each destination chooses which severities and which alert categories it receives.

How each type gets into the list:

| Type | How you add it |
|-|-|
| Discord | The **Add Discord or Telegram** button in the section header |
| Telegram | The same button |
| Email (SMTP) | Saving at least one address in **Email Alert Recipients** (above). This creates one row, named `Email` |
| ntfy | The **Add ntfy target** form below the section header |

### Add a Discord destination

Discord uses an **incoming webhook URL**: no bot, no API key, no OAuth.

**To get the webhook URL from Discord:**

1. In Discord, open the server you want alerts to post to.
2. **Server Settings → Integrations → Webhooks → New Webhook**.
3. Choose the destination channel and copy the **Webhook URL**. The URL looks like `https://discord.com/api/webhooks/<id>/<token>`.

**To add it in ECM:**

1. Under **Alert Methods**, click **Add Discord or Telegram**. The **Add alert destination** dialog opens.
2. Enter a **Name** you will recognize in the list, for example `Errors to #alerts`.
3. Leave **Type** on **Discord webhook**.
4. Paste the URL into **Webhook URL**. Optionally change **Post as**, the display name the messages are posted under. It defaults to `ECM Alerts`; an empty box posts under the webhook's own name from Discord.
5. Choose **Severities** and **Alert categories** ([see below](#choose-what-each-destination-receives)) and leave **Enabled** ticked.
6. Click **Send test**, then **Add destination**.

The webhook URL must be on `discord.com`, `discordapp.com`, `canary.discord.com` or `ptb.discord.com`, under `/api/webhooks/`. Anything else is refused when you test, add, or edit the destination, and ECM checks it again before every send.

**Verifying it works:** **Send test** posts a short test message, using the URL you typed, to the Discord channel and surfaces a success toast. Failures surface a short reason: most commonly *"Webhook not found - may have been deleted"* (the URL is wrong or the webhook was deleted in Discord).

### Add a Telegram destination

Telegram requires two pieces of information: a **bot token** and a **chat ID**.

**To get the bot token:**

1. In Telegram, open a chat with **@BotFather**.
2. `/newbot` and follow the prompts (name and username for the bot).
3. BotFather replies with the **bot token**: a long string like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz...`. Copy it.

**To get the chat ID:**

- For a **personal chat**, message **@userinfobot** or **@RawDataBot**. They reply with your numeric chat ID.
- For a **group**, add the bot to the group, then use **@RawDataBot** or the Telegram API's `getUpdates` to read the group's chat ID. Group chat IDs are **negative numbers** (e.g., `-1001234567890`).

**To add it in ECM:**

1. Under **Alert Methods**, click **Add Discord or Telegram**, enter a **Name**, and set **Type** to **Telegram bot**.
2. Paste the token into **Bot token** and the chat ID into **Chat ID**.
3. Choose **Severities** and **Alert categories** and leave **Enabled** ticked.
4. Click **Send test**, then **Add destination**.

The bot token must have the shape `<digits>:<letters, digits, _ or ->`. Anything else is refused before ECM contacts Telegram.

**Verifying it works:** **Send test** posts a test message to the chat and surfaces a success toast. The most common failures are *"Chat not found - check your chat ID"* (the bot is not in the group, or the chat ID is wrong) and *"Invalid bot token - unauthorized"* (the token is wrong).

### Choose what each destination receives

The same two controls appear in the dialog for every destination.

**Severities:** **Errors**, **Warnings**, **Successes**, and **Info**. A new Discord or Telegram destination starts with everything except Info.

**Alert categories:** every alert ECM raises carries one category. Untick a category to stop it reaching this destination. Three categories take an extra filter.

| Category | What it covers | Extra filter |
|-|-|-|
| **EPG refreshes** | EPG source refresh results, failures and timeouts | **All**, **Only the selected**, or **All except the selected** EPG sources |
| **M3U refreshes** | M3U account refresh results, failures and timeouts | The same choice, over M3U accounts |
| **Stream probes** | Stream probe runs, including the scheduled **Stream Probe** and **Failed Stream Re-probe** tasks | *Alert on a probe with failures only when at least N streams failed* (default 1). A probe with no failures always alerts |
| **Scheduled task results** | Start and completion alerts from the other scheduled tasks | None |
| **Channel pipeline** | Auto-creation runs, caps, and event sync warnings, including the Channel Pipeline scheduled task's results | None |
| **DBAS backup and restore** | Backup runs, cloud uploads and restores, including the DBAS Backup and DBAS Restore scheduled tasks' results | None |
| **Cross-instance sync** | Skipped or failed cross-instance sync runs | None |
| **Emby** | Emby logo clean-up progress and failures | None |
| **Profile conflict review** | Channel profile conflicts that need a decision | None |
| **Other** | Anything not in a category above, including alerts posted through the API | None |

The categories sit underneath the per-task toggles; they do not replace them. A scheduled task's **Discord** toggle still decides for every Discord destination before any destination's categories are checked.

### Edit, test, or delete a destination

Each row shows the destination's name, type, an **Enabled/Disabled** badge, and a one-line summary of what it receives, for example `Errors, warnings, successes · all categories` or `All severities · 1 category off · 1 filtered`. Each row has three icon buttons:

- **Edit** opens the same dialog, titled **Edit** followed by the destination's name. Secrets are never shown: the **Webhook URL**, **Bot token** and **Chat ID** boxes start empty with the placeholder *Saved — leave blank to keep*. Leaving a box blank keeps the stored value; typing in it replaces the value. For **Email (SMTP)** and **ntfy** rows the dialog edits the name, **Enabled**, severities and categories only. Email recipients are still set in **Email Alert Recipients**, and an ntfy target's server, topic and token cannot be edited: delete it and add it again.
- **Send test** sends a real test message to the stored destination. In the dialog, **Send test** tests what you have typed (the webhook URL, or both Telegram fields) before you save; with nothing typed on an existing destination, it tests the saved destination. For Telegram, typing only one of the token and the chat ID is refused: enter both, or neither.
- **Delete** asks you to type the destination's name to confirm.

The list updates as soon as you add, edit or delete a destination, and after you add an ntfy target.

> **Email works differently.** Saving SMTP settings alone does not create the
> Email (SMTP) alert method. The row appears only after you've also saved at
> least one address in Email Alert Recipients, and then reloaded the page.

### Add an ntfy target

Below the section header, **Add ntfy target**
creates an ntfy alert method. Enter a descriptive **Name**, the ntfy **Server
URL**, and a **Topic**. Use `https://ntfy.sh` for the public service, or the
absolute `http://` or `https://` base URL of a self-hosted server. The URL must
not contain embedded credentials, a query, or a fragment. Topics contain 1-64
ASCII letters, numbers, hyphens, or underscores. Add an **Access token** only
when the server or topic requires bearer authentication. Tokens require an
`https://` URL; tokenless `http://` is available only for intentionally isolated
self-hosted servers.

New ntfy methods are enabled for success, warning, and error alerts; info is
off. Change the severities and alert categories with the row's **Edit** button.
The server URL, topic and token cannot be changed after creation; delete the
target and add it again.

Click the send icon on the new ntfy row to publish a real test notification to
its stored server URL and topic. A success toast confirms delivery was accepted;
a failure tells you to check the URL, topic, token, and server availability.
The list and API mask both the ntfy topic and access token after saving them.

## Upgrading from the shared Discord webhook and Telegram bot

Older versions of ECM configured Discord and Telegram once, in a single **Discord Webhook** section and a single **Telegram Bot** section, shared by every alert. Those sections are gone. On the first start after upgrading, ECM moves them into Alert Methods:

- An existing shared Discord webhook becomes a Discord destination named `Discord`, still posting as `ECM Alerts`.
- An existing Telegram bot token and chat ID become a Telegram destination named `Telegram`.

Migrated destinations receive **every severity, including Info**, and every category, so nothing that was delivered before stops arriving. Adjust them afterwards with **Edit**. The old settings fields are then cleared. If an identical Discord or Telegram destination already exists, no duplicate is created.

A shared webhook on a non-Discord host, or a half-filled Telegram pair (a token without a chat ID or the other way round, or a malformed token), is not migrated, because it could never have delivered anything. It is cleared, with a warning in the backend log.

The same migration runs after restoring a backup (ZIP, YAML, or a DBAS ECM-settings restore) that still contains the old fields. Restoring a backup taken before the upgrade replaces the alert methods with the backup's, so destinations added after that backup was taken are not in it, as with any other alert method.

## How scheduled tasks dispatch external alerts

Each scheduled task has its own copy of the four notify-on-* gates and the three per-type channel toggles. They live on the task, not on the alert method. ntfy does not add a fourth task-level channel toggle.

**Per-task settings (Settings → Scheduled Tasks → Edit task):**

| Setting | What it controls |
|-|-|
| `send_alerts` | Master switch. Off = no external alert is ever sent for this task. |
| `alert_on_success` | Send alerts for successful runs. |
| `alert_on_warning` | Send alerts when the task completes with warnings. |
| `alert_on_error` | Send alerts when the task fails. |
| `alert_on_info` | Send informational alerts (rare; default off). |
| `send_to_email` | Send this task's alerts to Email destinations. Requires SMTP + at least one recipient. |
| `send_to_discord` | Send this task's alerts to every enabled Discord destination in Alert Methods. |
| `send_to_telegram` | Send this task's alerts to every enabled Telegram destination in Alert Methods. |
| ntfy | There is no per-task ntfy setting. Every enabled ntfy method is considered after the task master and severity gates pass. |
| `show_notifications` | Show alerts in the in-app Notification Center (independent of the external channels). |

In the task editor the three channel toggles sit under **Notification Channels (all destinations of each type)**.

TaskEngine owns scheduled `stream_probe` and `failed_stream_reprobe` completion
alerts. They follow this task-level flow and each destination's **Stream
probes** filter once, without duplicate delivery. Manual and other
non-scheduled probe runs keep their direct completion-alert behavior.

**Decision flow at fire time:**

```
task fires alert (severity=error, say)
  │
  ├─ send_alerts? ───── no ──> stop (no external dispatch)
  │
  ├─ alert_on_error? ── no ──> stop
  │
  ├─ for each enabled destination in Alert Methods:
  │     ├─ task toggle for its type on? ── no ──> skip this destination
  │     │     (send_to_email / send_to_discord / send_to_telegram;
  │     │      ntfy has no task toggle)
  │     ├─ its Errors severity ticked? ── no ──> skip this destination
  │     ├─ its alert categories let the alert through? ── no ──> skip
  │     └─ hold for 30 s, then send one digest per destination
  │
  └─ show_notifications? ── yes ──> also append to Notification Center
```

**Common reasons an alert isn't delivered:**

1. The task's `send_alerts` master toggle is off. (Check first: it's the most common silent block.)
2. The task's per-severity toggle for the fired severity is off. *Success alerts especially are often disabled by default to reduce noise.*
3. The task's toggle for that type is on, but there is no enabled destination of that type under **Alert Methods**, or (for Email) SMTP is **Unconfigured**.
4. **Email-specific:** SMTP is configured but the **Email Alert Recipients** list is empty. The hint under the recipients field warns about this; the toast/notification on save also calls it out.
5. **The destination's own severity is off.** The fired severity is unticked under that destination's **Severities**. Destinations created in the UI start with Info off.
6. **The destination's alert categories exclude the alert.** The alert's category is unticked, its EPG source or M3U account is filtered out, or a probe had fewer failures than the destination's threshold. The row summary reads `N categories off` or `N filtered` when any of this applies.
7. **ntfy-specific:** the ntfy method is disabled, or its server URL, topic, or token is no longer valid.
8. The destination provider rejected the message. This surfaces in the backend logs as `[ALERTS-SMTP]`, `[ALERTS-DISCORD]`, `[ALERTS-TELEGRAM]`, or `[ALERTS-NTFY]` warnings/errors. Check the logs (see [Troubleshooting](../troubleshooting/index.md)) when channels are configured but alerts still go missing.

## Going deeper

- API reference → Alert Methods ([`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md), in the repository, not part of this published guide): endpoints for listing/creating/testing alert methods programmatically, and the `alert_sources` category filter.
- API reference → Scheduled Tasks (same file): endpoints for updating per-task alert configuration without using the UI.
- [Troubleshooting](../troubleshooting/index.md): when an alert channel is configured but alerts still aren't arriving, start here for log inspection and the support-information checklist.
