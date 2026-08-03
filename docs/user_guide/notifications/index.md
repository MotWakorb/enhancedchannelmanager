# Notifications & Alert Methods

This index covers the operator workflow end-to-end: configuring each channel and wiring it into scheduled tasks. Per-channel deep dives are linked below and in the Articles table.

## Section purpose

Show operators how to configure ECM's three external notification channels: **Email (SMTP)**, **Discord**, and **Telegram**. Also show how those channels actually get used by **Scheduled Tasks**.

The most common operator question this section answers: *"I enabled email alerts on a scheduled task and nothing arrived. Why?"* The short answer is that two things must both be configured: the channel itself (under **Settings → Notification Settings**), and the task's per-channel toggle (under **Settings → Scheduled Tasks → Edit**). The rest of this page walks through both.

## Articles

| Article | Purpose |
|-|-|
| [Manage the Email Alert Recipients List](email-recipients-deep-dive.md) | RFC 5322 edge cases, paste normalization rules, the Alert Methods data model behind the scenes, migrating from older free-text recipient fields. |
| [What ECM Posts to Discord, and What You Can Change](discord-webhook-customization.md) | Embed formatting, mentioning roles in alerts, channel routing strategies if you outgrow a single shared webhook. |
| [Set Up the Telegram Bot](telegram-bot-setup.md) | Step-by-step BotFather walk-through with screenshots, locating chat IDs in groups vs. channels vs. supergroups, bot privacy mode caveats. |
| [Route Alerts to the Right Channel](alert-routing-patterns.md) | Worked examples: "send only errors to Discord, all severities to email," "info alerts for one task only," etc. |

## How alerts get delivered (the short version)

A scheduled task fires an alert (success / warning / error / info). Whether anything reaches an external channel depends on **three independent gates**, evaluated in order:

1. **The task's `send_alerts` master toggle.** If off, no external alert is sent regardless of channel configuration.
2. **The task's per-severity toggle** (`alert_on_success`, `alert_on_warning`, `alert_on_error`, `alert_on_info`). The fired alert's severity must be enabled.
3. **The task's per-channel toggle** (`send_to_email`, `send_to_discord`, `send_to_telegram`) AND the corresponding **Alert Method** must be configured under **Settings → Notification Settings**.

If gate 1 or 2 blocks, nothing is sent anywhere. If gate 3 blocks for a channel, that one channel is skipped; others still fire.

The in-app **Notification Center** (the bell icon) is governed by a separate `show_notifications` toggle on the task; it is independent of the three gates above.

> **Going deeper:** see API reference → Alert Methods ([`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md), in the repository, not part of this published guide) for the request/response shapes and API reference → Scheduled Tasks (same file) for the task-update endpoints.

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

## Settings → Notification Settings → Discord Webhook

Discord uses an **incoming webhook URL**: no bot, no API key, no OAuth.

**To get the webhook URL from Discord:**

1. In Discord, open the server you want alerts to post to.
2. **Server Settings → Integrations → Webhooks → New Webhook**.
3. Choose the destination channel and copy the **Webhook URL**. The URL looks like `https://discord.com/api/webhooks/<id>/<token>`.

Paste it into the **Webhook URL** field in **Settings → Notification Settings → Discord Webhook** and save.

**Verifying it works:** click **Send Test Message**. A successful test posts a short test message to the configured Discord channel and surfaces a success toast. Failures surface the Discord API error verbatim: most commonly *"Unknown Webhook"* (URL is wrong or the webhook was deleted).

The webhook URL is shared across **all** features that use Discord: M3U Digest, scheduled-task alerts, etc. There is no per-feature webhook.

## Settings → Notification Settings → Telegram Bot

Telegram requires two pieces of information: a **bot token** and a **chat ID**.

**To get the bot token:**

1. In Telegram, open a chat with **@BotFather**.
2. `/newbot` and follow the prompts (name and username for the bot).
3. BotFather replies with the **bot token**: a long string like `123456789:ABCdefGHIjklMNOpqrsTUVwxyz...`. Copy it.

**To get the chat ID:**

- For a **personal chat**, message **@userinfobot** or **@RawDataBot**. They reply with your numeric chat ID.
- For a **group**, add the bot to the group, then use **@RawDataBot** or the Telegram API's `getUpdates` to read the group's chat ID. Group chat IDs are **negative numbers** (e.g., `-1001234567890`).

Paste the token into **Bot Token** and the chat ID into **Chat ID**, then save.

**Verifying it works:** click **Send Test Message**. A successful test posts a test message to the chat and surfaces a success toast. The most common failure is *"chat not found"* (bot is not in the group, or chat ID is wrong) or *"unauthorized"* (token is wrong).

## Settings → Notification Settings → Alert Methods

Below the SMTP, Discord Webhook, and Telegram Bot sections, the **Alert
Methods** list shows every alert method those settings have created, one row
per method: its name, its type badge (**Email (SMTP)**, **Discord**,
**Telegram**), an **Enabled/Disabled** badge, a send icon (sends a test
message to that method) and a delete icon. When no alert methods exist yet,
it reads *"No alert methods configured yet. Configure SMTP, Discord, or
Telegram above to create one."*

> **This list does not live-update.** Saving the Discord Webhook or the
> Telegram Bot creates or updates the underlying alert method immediately on
> the backend, but the Alert Methods list on the same page keeps showing its
> previous state, including "No alert methods configured yet," until you
> reload the page. **Email works differently:** saving SMTP settings alone
> does not create the Email (SMTP) alert method. The row appears only after
> you've also saved at least one address in Email Alert Recipients, and then
> reloaded the page. Reload after saving a channel for the first time to see
> its row (and the send/delete icons) appear.

## How scheduled tasks dispatch external alerts

Each scheduled task has its own copy of the four notify-on-* gates and the three per-channel toggles. They live on the task, not on the alert method.

**Per-task settings (Settings → Scheduled Tasks → Edit task):**

| Setting | What it controls |
|-|-|
| `send_alerts` | Master switch. Off = no external alert is ever sent for this task. |
| `alert_on_success` | Send alerts for successful runs. |
| `alert_on_warning` | Send alerts when the task completes with warnings. |
| `alert_on_error` | Send alerts when the task fails. |
| `alert_on_info` | Send informational alerts (rare; default off). |
| `send_to_email` | Use the Email alert channel for this task's alerts. Requires SMTP + at least one recipient. |
| `send_to_discord` | Use the Discord alert channel. Requires Discord Webhook configured. |
| `send_to_telegram` | Use the Telegram alert channel. Requires Telegram Bot configured. |
| `show_notifications` | Show alerts in the in-app Notification Center (independent of the external channels). |

**Decision flow at fire time:**

```
task fires alert (severity=error, say)
  │
  ├─ send_alerts? ───── no ──> stop (no external dispatch)
  │
  ├─ alert_on_error? ── no ──> stop
  │
  ├─ for each channel where send_to_<channel> is true:
  │     ├─ Email    → resolved via Email Alert Recipients (above)
  │     ├─ Discord  → resolved via Discord Webhook (above)
  │     └─ Telegram → resolved via Telegram Bot (above)
  │
  └─ show_notifications? ── yes ──> also append to Notification Center
```

**Common reasons an alert isn't delivered:**

1. The task's `send_alerts` master toggle is off. (Check first: it's the most common silent block.)
2. The task's per-severity toggle for the fired severity is off. *Success alerts especially are often disabled by default to reduce noise.*
3. The per-channel toggle is on, but the channel itself is **Unconfigured** (badge is grey). Configure the channel under Notification Settings.
4. **Email-specific:** SMTP is configured but the **Email Alert Recipients** list is empty. The hint under the recipients field warns about this; the toast/notification on save also calls it out.
5. The destination provider rejected the message. This surfaces in the backend logs as `[ALERTS-SMTP]`, `[ALERTS-DISCORD]`, or `[ALERTS-TELEGRAM]` warnings/errors. Check the logs (see [Troubleshooting](../troubleshooting/index.md)) when channels are configured but alerts still go missing.

## Going deeper

- API reference → Alert Methods ([`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md), in the repository, not part of this published guide): endpoints for listing/creating/testing alert methods programmatically.
- API reference → Scheduled Tasks (same file): endpoints for updating per-task alert configuration without using the UI.
- [Troubleshooting](../troubleshooting/index.md): when an alert channel is configured but alerts still aren't arriving, start here for log inspection and the support-information checklist.
