# M3U Manager

M3U Manager is where you add, refresh, and configure every provider playlist
ECM pulls streams from. It also owns the account-level settings that shape
what those streams look like before they ever reach Channel Manager or
Channel Pipeline: filters, priority, linked accounts, and stream profiles.

## Common tasks

### Add an M3U account

1. Under **Operations**, open **M3U Manager**.
2. Click **Add M3U Account**.
3. Enter an **Account Name** and choose an **Account Type**:

   | Type | Fields |
   |-|-|
   | **Standard M3U** | **M3U URL** (or **Upload M3U File**, or a **Server File Path** on the ECM host) |
   | **XtreamCodes** | **Server URL**, **Username**, **Password** |
   | **HD Homerun** | **HD Homerun IP Address** (ECM builds the `http://<ip>/lineup.m3u` URL for you) |

4. Optionally set:
   - **Server Group**: a label for organizing *this account* in the M3U
     Manager list. This is not the same thing as a stream group (see the
     callout below).
   - **Max Streams**: concurrent connection limit (`0` = unlimited).
   - **Refresh (hours)**: how often ECM automatically re-pulls this
     account's playlist (`0` = manual refresh only). See [Refresh an
     account's playlist](#refresh-an-accounts-playlist).
   - **Stale Days** (default 7): how many days an unseen stream can go
     missing from the provider's playlist before ECM treats it as stale.
   - **Enable VOD** and the three **Auto-enable new groups** toggles
     (Live/VOD/Series): Live is on by default; VOD, Series, and VOD import
     itself are off by default. See [Choose which stream groups
     sync](manage-stream-groups.md#prune-a-newly-added-account-before-you-rely-on-it)
     before relying on the defaults for a provider with hundreds of groups.
5. Click **Create Account**.

**Result:** The account appears in the M3U Accounts list and starts
downloading and parsing its playlist immediately. You don't need a
separate first refresh. Watch the **Status** column.

> **Server Group vs. stream group:** these are two unrelated concepts that
> happen to share the word "group." A **Server Group** (the field above)
> tags the *account itself*, purely to organize a long M3U Manager list.
> Create one from the account list's **M3U setup actions** menu → **Server
> Groups**. A **stream group** (Sports, News, …) is a category *inside* a
> provider's playlist, managed per-account in **Manage Groups**. See
> [Choose which stream groups sync](manage-stream-groups.md).

### Understand account status

| Status | Meaning |
|-|-|
| **Ready** | Playlist loaded successfully |
| **Error** | Connection or parsing failed: check the account's URL/credentials |
| **Downloading** | Fetching the playlist |
| **Processing** | Parsing M3U content |
| **Disabled** | Account turned off (the row's toggle) |

### Refresh an account's playlist

1. To refresh one account now, click **Refresh account** on its row.
2. To refresh every account at once, click **Refresh All** in the toolbar.

**Result:** ECM re-downloads and re-parses the playlist and reports how many
streams were created, updated, marked stale, or removed.

For a recurring refresh you don't have to remember to trigger yourself,
either raise the account's own **Refresh (hours)** field (above, this
account only), or add a schedule for the instance-wide **M3U Refresh** task
under [Settings → Scheduled Tasks](../settings/scheduled-tasks.md), which
refreshes independently of each account's own **Refresh (hours)** setting.

### Set a provider's priority for Smart Sort

1. In the **Priority** column on the account's row, enter a value from
   1–100 (higher = preferred).
2. Click **Save Priorities** (enabled once you've changed at least one
   value).

**Result:** ECM stores the priority, but it has **no effect on stream
ordering by itself**. It only feeds the **M3U Priority** criterion in
Smart Sort, which is off by default. Enable it under [Settings → Channel
Defaults → Set which criteria Smart Sort uses, and in what
order](../settings/channel-defaults.md#set-which-criteria-smart-sort-uses-and-in-what-order)
for this priority to actually change how ECM orders streams within a
channel.

### Filter out streams before they're imported

1. Click **Manage Filters** on the account's row.
2. Click **Add Filter**.
3. Choose a **Filter Type** (Group, Name, or URL) and an **Action**
   (Include matches or Exclude matches).
4. Enter a **Regex Pattern**. For example, `Adult.*` matches anything
   starting with "Adult" and `^PPV` matches strings starting with "PPV".
5. Click **Create Filter**. Drag filters to reorder. They run top to
   bottom.

**Result:** Only streams that pass every filter are imported into ECM at
all. This happens at M3U import time, before any group or Auto-Sync
setting ever sees the stream. Compare this with [Auto-Sync's Channel Name
Filter](manage-stream-groups.md#understand-two-regex-fields-that-look-alike),
which narrows an already-imported group further, at sync time.

### Link accounts from the same provider

Use this when one provider gives you multiple accounts (for example, a
primary and a backup) whose group selections should always match.

1. From the toolbar, click **M3U setup actions** → **Manage Links**.
2. Click **Create Link Group**.
3. Select 2 or more accounts to link, then click **Create Group**.

**Result:** Changing group settings on any one linked account applies the
same change to every account in its link group. You configure groups once
instead of separately per account.

### Assign a stream failover profile to an account

Use this for a provider whose XtreamCodes account has multiple backend
profiles (separate logins or endpoints Dispatcharr load-balances across).
Don't confuse this with the top-level **Stream Profiles** catalog (ffmpeg,
Proxy, Redirect, streamlink, VLC), which is about *playback/transcoding*,
not *provider failover*.

1. Click **Manage Account Profiles** on the account's row.
2. Review the configured profiles: each shows its stream count, whether it
   has a URL-matching pattern configured, and whether it's Active.
3. Click **Add Profile**, or edit/delete an existing one.

**Result:** ECM distributes probe connections and stream lookups across the
account's active profiles, rewriting stream URLs per profile's pattern so
probes go through the correct endpoint.

## Going deeper

- [Choose which stream groups sync](manage-stream-groups.md): the full
  Manage Groups and Auto-Sync Settings reference, including why a Channel
  Pipeline rule can "match nothing" on an unpruned account.
- [Set Up Your First Channels](../getting-started/your-first-channels.md):
  the fastest path from a new account to a working channel.
- [M3U Changes](../m3u-changes/index.md): a read-only log of what a
  provider added or removed since ECM's last refresh.
- [Settings → Scheduled Tasks](../settings/scheduled-tasks.md): the
  instance-wide M3U Refresh task, plus every other recurring job.
- [Settings → Channel Defaults](../settings/channel-defaults.md): Smart
  Sort criteria, including M3U Priority.
- [Settings → M3U Change Digest](../settings/m3u-digest.md): email/Discord
  notifications for M3U changes, instead of checking M3U Changes manually.
- [`docs/api.md`](../../api.md): HTTP endpoints behind every action on
  this page.
