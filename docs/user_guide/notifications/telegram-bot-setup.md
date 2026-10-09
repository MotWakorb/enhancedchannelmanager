# Set Up the Telegram Bot

After this article you will have a Telegram bot delivering ECM alerts to a
chat you control, and you will know which Telegram concepts matter to ECM
and which ones do not.

The delivery gates every alert passes are described in
[Notifications & Alert Methods](index.md). This article covers only the two
fields ECM needs and how to fill them correctly.

## The two fields ECM needs

A Telegram destination is one row under **Settings → Notification Settings
→ Alert Methods**. Click **Add Discord or Telegram**, set **Type** to
**Telegram bot**, and the dialog asks for a **Bot token** and a **Chat ID**.
That is the whole Telegram configuration. ECM does not use a Telegram user
account, a phone number, an API id, or an API hash.

Both fields are required to add the destination. With either one empty, the
dialog refuses with `Bot token and chat ID are required`.

The **Bot token** box is a password input, so it masks what you type. The
token grants full control of the bot to anyone holding it. Once saved, ECM
treats both values as secrets: the list and the API show the token and the
chat ID as `********`, and the **Edit** dialog starts with both boxes empty
and the placeholder *Saved — leave blank to keep*. Leave a box blank to keep
the saved value; type in it to replace the value.

## Get the bot token

1. In Telegram, open a chat with **@BotFather**.
2. Send `/newbot` and answer the two prompts: a display name, then a
   username ending in `bot`.
3. BotFather replies with the token. It has the shape
   `<digits>:<letters, digits, underscores and hyphens>`, matching the
   field's own placeholder `123456789:ABC...`.
4. Paste it into **Bot token**.

ECM validates the shape before it will contact Telegram. A token that does
not match `<digits>:<token>` is refused locally, without a network call. In
the dialog the message is `Bot token must look like 123456789:ABC...`;
through the API it is `Invalid bot token format`. This is a deliberate guard
against a malformed token being used to build a request to somewhere other
than Telegram. ECM checks the token again before every send.

## Get the chat ID

Each Telegram destination sends to exactly one chat. Which one depends on
where you want alerts.

**For a direct message to yourself:** message **@userinfobot** or
**@RawDataBot**. Either replies with your numeric user id, which doubles as
your chat id. It is a positive number.

**For a group or supergroup:** add your bot to the group first, then use
**@RawDataBot** in that group to read the chat id. Group chat ids are
negative, as the field's hint says. The field's placeholder,
`-1001234567890`, shows the supergroup shape, which starts `-100`.

Paste the value into **Chat ID**, send a test (below), and click **Add
destination**.

**Result:** a Telegram row appears in the **Alert Methods** list, with a
summary of the severities and categories it receives.

**The bot must already be in the destination chat before it can post
there.** Telegram will not let a bot message a group it has not joined, and
for a direct message the user has to have started a conversation with the
bot first. If either is missing, the test below fails with
`Chat not found - check your chat ID` even though the id is correct.

## Send a test message

**Send test** in the dialog uses the values currently typed, not saved
values, so you can validate a token and chat id before adding the
destination. It needs both: with only one of them typed it refuses with
`Enter both the bot token and the chat ID to test them, or leave both blank
to test the saved bot.`

The test posts a fixed message in Telegram's MarkdownV2 mode:

```text
✓ *ECM Telegram Test*

Your Telegram bot is configured correctly.
You will receive notifications from Enhanced Channel Manager here.
```

It is a real message to a real chat. Do not press it if you have someone
else's chat id in the field.

A success toasts `Test message sent successfully`. Failures map to:

| What went wrong | Message |
|-|-|
| Token or chat ID missing when adding | `Bot token and chat ID are required` |
| Token malformed | `Bot token must look like 123456789:ABC...` |
| Telegram rejected the token | `Invalid bot token - unauthorized` |
| Bot is not in the chat, or the id is wrong | `Chat not found - check your chat ID` |
| Another Telegram validation failure | `Bad request: <Telegram's description>` |
| Too many messages too quickly | `Rate limited - try again later` |
| ECM could not reach Telegram | `Connection error during Telegram test` |

This test performs a single `sendMessage` call. It does not separately
verify the bot, so a token that is well-formed but wrong fails at the
message, not before it.

**Testing a saved destination.** The **Send test** icon on the row, or
**Send test** in the **Edit** dialog with both boxes left empty, uses the
stored token and chat ID. That test calls `getMe` first, so it can tell a
bad token from a bad chat:

| Outcome | Message |
|-|-|
| Sent | `Test message sent successfully via @<bot username>` |
| Telegram rejected the token | `Bot verification failed: <Telegram's description>` |
| Token is good, the chat is not | `Bot @<bot username> verified but failed to send a message to the configured chat. Check the chat ID and that the bot is in that chat.` |

## What a real alert looks like

Alerts are sent with `parse_mode: HTML` by default and are assembled as:

```text
<emoji> <b><alert title></b>

<alert message>

<b><Metadata Key>:</b> <value>

<i>📍 <alert source> | 🕐 <YYYY-MM-DD HH:MM:SS> UTC</i>
```

The emoji is fixed by severity: ℹ️ for info, ✅ for success, ⚠️ for
warning, ❌ for error. The metadata lines carry details such as a scheduled
task's id, name and run duration, one line per key, labeled with the key
(for example `Task Name`), up to 25 lines. They appear only when the alert
carries metadata.

The title, message and metadata are HTML-escaped, so `<`, `>` or `&` in a
task name, a source name, or an error string does not break the message.

**Digests.** Alerts queued for the same destination in one 30-second window
(counted from the first) are combined into one message titled like
`ECM Digest (1 succeeded, 2 failed)`, with one line per alert. A single
alert in the window is sent on its own.

**Long messages.** Telegram rejects a message longer than 4096 characters.
ECM caps the title, metadata and source and trims the message body to fit,
marking the cut with `…`, so a long digest is shortened rather than lost.

Nothing in the alert wording is configurable. Three optional settings exist
on a Telegram destination, with no field in the dialog: `parse_mode`
(`HTML`, `Markdown` or `MarkdownV2`), `disable_notification` for a silent
post, and `disable_web_page_preview` (on by default). Set them in the
destination's config through the
[API](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md#alert-methods);
the **Edit** dialog keeps them when you save.

## Telegram concepts that do not apply to ECM

Worth stating plainly, because they cost time to chase:

- **Privacy mode makes no difference.** BotFather's privacy setting
  controls which messages a bot can *read* in a group. ECM never reads
  anything: the only Telegram endpoints it calls are `sendMessage` and, when
  testing a saved destination, `getMe`. Leave privacy mode at its default.
- **Topics and threads are not addressable.** ECM sends a plain
  `sendMessage` carrying a chat id and no thread id, so it cannot target a
  specific topic in a forum-style group.
- **One chat per destination.** Each Telegram destination has one Chat ID
  field. To reach several chats, add one destination per chat, or use a
  group rather than trying to list ids.

## If you used the old shared Telegram bot

Older versions had a single **Telegram Bot** section shared by every alert.
On the first start after upgrading, ECM moved a saved token and chat ID into
a destination named `Telegram`, receiving every severity, including Info,
and every category. It now also receives EPG refresh, M3U refresh and manual
stream-probe alerts, which the shared bot never did. Untick those categories
with **Edit** if you do not want them.

A token without a chat ID (or the other way round), or a malformed token,
was not migrated, because it could never have delivered anything. Add the
destination again. See [Upgrading from the shared Discord webhook and
Telegram
bot](index.md#upgrading-from-the-shared-discord-webhook-and-telegram-bot).

## When alerts stop arriving

| Symptom | Check |
|-|-|
| Test succeeds, task alerts never appear | The task's **Send external alerts** toggle and its **Telegram** channel toggle, then the destination's **Severities** and **Alert categories** |
| Test fails with `Chat not found` | The bot is not in the chat, or the id lost its leading `-` |
| It worked, then stopped | The bot was removed from the group, or the token was revoked in BotFather |
| Only some alerts arrive | The destination's **Severities** and **Alert categories**; the row summary shows any filtering. Then look for `[ALERTS-TELEGRAM]` lines in the log, which carry Telegram's own description of a rejection |
| Alerts arrive late or merged | Expected. Alerts queued in the same 30-second window are sent as one digest |

See [Read the logs](../troubleshooting/read-the-logs.md) for getting at
those lines.

## Going deeper

- [Notifications & Alert Methods](index.md): the delivery gates, the alert
  categories, and the rest of the Notification Settings page.
- [Alert routing patterns](alert-routing-patterns.md): choosing which
  severities, which kinds of alert, and which tasks reach Telegram.
- [API reference → Alert Methods](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md#alert-methods) (GitHub): the
  Telegram alert method and its optional settings.
