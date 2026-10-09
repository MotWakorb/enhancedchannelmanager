# What ECM Posts to Discord, and What You Can Change

After this article you will know the exact shape of the messages ECM sends
to a Discord destination, which parts of them you can influence, and which
parts are fixed in code so you stop looking for a setting that does not
exist.

Adding a Discord destination in the first place is covered in
[Notifications & Alert Methods](index.md#add-a-discord-destination). This
article picks up once it is saved.

## The fields, and what ECM checks before storing them

A Discord destination is one row under **Settings → Notification Settings →
Alert Methods**, added with **Add Discord or Telegram**. Besides the name,
severities and categories every destination has, it has two Discord fields:
**Webhook URL** and **Post as**.

ECM POSTs the webhook URL verbatim, so it is validated against a host
allowlist before it is stored. Accepted hosts are `discord.com`,
`discordapp.com`, `canary.discord.com` and `ptb.discord.com`, and the path
must begin `/api/webhooks/`. Anything else is refused. In the dialog the
message is:

```text
Webhook URL must be a discord.com webhook (https://discord.com/api/webhooks/...)
```

Through the API it is:

```text
Invalid Discord webhook URL — must be an https webhook on discord.com / discordapp.com (e.g. https://discord.com/api/webhooks/...).
```

ECM checks the URL again before every send, so a destination restored from
an old backup with a non-Discord URL is never posted to. The skipped send is
logged with an `[ALERTS-DISCORD]` line.

To stop posting to a destination, untick **Enabled** in its dialog or delete
the row.

Remember that a Discord webhook URL **is** a credential. Anyone holding it
can post to that channel. Treat it like a password: do not paste it into a
support thread, a screenshot, or an issue. ECM never shows a saved URL
again: the list and the API return it as `********`, and the **Edit** dialog
starts with the box empty and the placeholder *Saved — leave blank to keep*.

## Testing before you save

In the dialog, **Send test** posts using the URL currently typed in
**Webhook URL**, not a saved one, so you can validate a URL before adding
the destination. When you are adding a destination, the box must have
content first.

The test payload is **not** representative of a real alert. It is a fixed
plain message posted under the webhook username `ECM Test`:

```text
**✓ ECM Discord Test**

Your Discord webhook is configured correctly.
You will receive notifications from Enhanced Channel Manager here.
```

A success toasts `Test message sent successfully`. Failures are mapped to
short, specific messages rather than raw Discord output:

| What went wrong | Message |
|-|-|
| Field left empty when adding | `Webhook URL is required` |
| Host or path is not a Discord webhook | `Webhook URL must be a discord.com webhook (https://discord.com/api/webhooks/...)` |
| Discord rejected the token | `Invalid webhook - unauthorized` |
| Webhook deleted, or the id is wrong | `Webhook not found - may have been deleted` |
| Too many posts too quickly | `Rate limited - try again later` |
| Anything else | `Discord returned error: 500` (the HTTP status) |
| ECM could not reach Discord at all | `Connection error during Discord test` |

**Testing a saved destination.** The **Send test** icon on the row, or
**Send test** in the **Edit** dialog with the URL box left empty, uses the
stored webhook instead. That test is a real alert-shaped embed: titled
`ℹ️ Connection Test`, info-colored, with the footer `Source: ECM Alert
Test`, posted under the destination's **Post as** name. It reports
`Test message sent successfully` or `Failed to send test message`; the
reason for a failure is in the backend log on an `[ALERTS-DISCORD]` line.

## What a real alert looks like

Every Discord alert is posted as an **embed**:

- **Title:** a severity emoji followed by the alert title. The emoji is
  fixed: ℹ️ for info, ✅ for success, ⚠️ for warning, ❌ for error.
- **Description:** the alert message.
- **Color bar:** blue for info, green for success, amber for warning, red
  for error.
- **Timestamp:** when the alert was raised (for a digest, when the digest
  was assembled).
- **Footer:** `Source: <alert source>`.
- **Fields:** the alert's metadata, such as the task's id, name and run
  duration for a scheduled-task alert, as inline fields named after each key
  (for example `Task Name` or `Duration Seconds`). Values are shown as
  stored, so a duration is a plain number of seconds. Empty values are left
  out, and there are at most 25 fields.

Nothing in the alert text is escaped, so markdown characters in a task name
or message render as Discord markdown.

**Digests.** Alerts queued for the same destination in one 30-second window
(counted from the first) are combined into one embed titled like
`ECM Digest (1 succeeded, 2 failed)`, with one line per alert in the
description (`<emoji> <title>: <message>`), the footer
`Source: ECM Digest`, and the color of the most severe alert in it. A single
alert in the window is posted on its own.

**Long messages.** Discord rejects an embed over its size limits (6000
characters in total). ECM trims instead: it drops metadata fields first,
then shortens the description, marking the cut with `…`. A long digest is
shortened rather than lost.

## What you can and cannot change

| Thing | Changeable? |
|-|-|
| Which severities post | Yes, per destination under **Severities** in its dialog, and per task under **Alert Types** in the task editor |
| Which kinds of alert post | Yes, per destination under **Alert categories** |
| Whether a task posts to Discord at all | Yes, per task, under **Notification Channels**. The toggle covers every Discord destination |
| Which Discord channel receives alerts | Each destination's webhook points at one channel. Add a destination per channel |
| The display name | **Post as** in the dialog, `ECM Alerts` by default. Leave it empty to post under the name the webhook has in Discord |
| The avatar | In Discord's own webhook settings, or the `avatar_url` key of the destination's config through the API |
| The embed timestamp | On by default. `include_timestamp: false` in the destination's config through the API turns it off |
| Message wording, emoji, embed colors | No. Fixed in code |
| Mentioning a role or user (`@here`, `<@&id>`) | No. ECM never inserts a mention and provides no field to add one |

The **Edit** dialog keeps `avatar_url` and `include_timestamp` when you save,
even though it does not show them.

There is no way to make an alert ping anyone. If you need paging behavior,
point a destination at a channel your team has set to notify on every
message, and use Discord's own notification settings rather than looking for
an ECM setting.

## Several destinations instead of one shared webhook

Each Discord destination is its own row with its own webhook, severities and
categories, so channel routing happens in ECM: for example one destination
that takes only errors and another that takes everything, or one that leaves
out EPG refresh results. See
[Alert routing patterns](alert-routing-patterns.md) for worked examples.

The **M3U Digest** is the exception. It posts to the first enabled Discord
destination (the one with the lowest id) and ignores that destination's
severity and category filters, because it is a scheduled report rather than
an alert. See [M3U Digest](../settings/m3u-digest.md).

## If you used the old shared webhook

Older versions had a single **Discord Webhook** section shared by every
alert. On the first start after upgrading, ECM moved a saved webhook into a
destination named `Discord`, posting as `ECM Alerts` and receiving every
severity, including Info, and every category. Two differences you will
notice:

- Alerts are embeds now, not plain text messages.
- EPG refresh, M3U refresh and manual stream-probe alerts now reach Discord
  too. The shared webhook never received them. Untick **EPG refreshes**,
  **M3U refreshes** or **Stream probes** on the destination if you do not
  want them.

See [Upgrading from the shared Discord webhook and Telegram
bot](index.md#upgrading-from-the-shared-discord-webhook-and-telegram-bot)
for what was and was not migrated.

## When alerts stop arriving

| Symptom | Check |
|-|-|
| Test succeeds, task alerts never appear | The task's **Send external alerts** toggle and its **Discord** channel toggle, then the destination's **Severities** and **Alert categories** |
| One kind of alert never appears | The destination's **Alert categories**. The row summary reads `N categories off` or `N filtered` when any are filtered |
| Everything stopped at once | The webhook was probably deleted in Discord. Click the row's **Send test**, then look for an `[ALERTS-DISCORD]` line reporting status 404 in the log |
| Alerts arrive late or merged | Expected. Alerts queued in the same 30-second window are sent as one digest |
| Alerts appear under an unexpected name | Expected. The destination's **Post as** value overrides the webhook's name. Clear it to use the webhook's own name |

Failures on the live alert path are logged with an `[ALERTS-DISCORD]` prefix
and include the destination's name and the HTTP status Discord returned. See
[Read the logs](../troubleshooting/read-the-logs.md).

## Going deeper

- [Notifications & Alert Methods](index.md): getting the webhook URL out of
  Discord, the alert categories, and the gates every alert passes.
- [Alert routing patterns](alert-routing-patterns.md): splitting alerts
  across destinations, with worked examples.
- [M3U Digest](../settings/m3u-digest.md): the other feature that posts to
  Discord, on its own schedule.
- [API reference → Alert Methods](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md#alert-methods) (GitHub):
  the Discord config keys, including `avatar_url` and `include_timestamp`,
  and the `alert_sources` filter.
