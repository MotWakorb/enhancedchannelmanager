# Set Up Your First Channels

This walkthrough follows one path through the whole tool: add an M3U account,
add an EPG source, choose which stream groups to sync, refresh, then build
channels, channel groups, and stream assignments in Channel Manager. By the
end you'll have a small set of real, working channels and understand where
each later feature (Channel Pipeline, Normalization, EPG matching) picks up.

## Common tasks

### 1. Add an M3U account

1. Open **M3U Manager**.
2. Click **Add M3U Account**.
3. Enter an **Account Name**, choose an **Account Type** (**Standard M3U** for
   a playlist URL/file, **XtreamCodes** if your provider issues a
   username/password, or **HD Homerun** for a local tuner lineup URL), and
   fill in the **M3U URL** (or upload a file / point at a server file path).
4. Click **Create Account**.

![Add M3U Account modal filled in with an account name, Standard M3U selected, and an M3U URL entered](../../images/user_guide/getting-started/1-add-m3u-account.png)

**Result:** The account appears in the M3U Accounts table with a status
badge. ECM performs an initial pull in the background. Give it a few
seconds, then the row shows a stream/group count and a **Ready** status.

![M3U Accounts table showing the new account row alongside existing provider accounts, with a groups count and Ready status](../../images/user_guide/getting-started/2-m3u-account-created.png)

### 2. Add an EPG source

1. Open **EPG Manager**.
2. Click **Add Standard EPG**.
3. Enter a **Name** and the **XMLTV URL** for your EPG feed. Leave **Source
   Type** as **XMLTV (URL)** unless you're connecting a Schedules Direct
   account, which uses a separate flow (see [EPG](../epg/index.md)).
4. Click **Add EPG**.

![Add Standard EPG modal filled in with a name and an XMLTV URL](../../images/user_guide/getting-started/3-add-epg-source.png)

**Result:** The source appears in the EPG Sources table with a channel
count pulled from the feed. This is the count of program-guide channels
available to match against, not ECM channels you've created yet.

![EPG Sources table showing the new source row with its channel count, alongside the existing EPG sources](../../images/user_guide/getting-started/4-epg-source-created.png)

### 3. Choose which stream groups to sync

Most providers organize streams into groups (Sports, News, Movies, and so
on). You rarely want every group synced.

1. Back on **M3U Manager**, find your account's row and click the **Manage
   Groups** (folder) icon.
2. In the **Manage Groups** modal, toggle **Enabled** per group. Turn off any
   placeholder or unwanted groups (a provider's `Default Group` is a common
   one to leave off).
3. Click **Save & Refresh**.

![Manage Groups modal for the account, showing per-group Enabled toggles with one group turned off](../../images/user_guide/getting-started/5-select-groups.png)

**Result:** The modal closes and ECM immediately re-pulls streams for only
the groups you enabled. You don't need a separate manual refresh right
after this step.

### 4. Refresh the M3U account

**Save & Refresh** in the previous step already triggered a pull. Use this
step whenever you want to re-sync later without touching group selection:
click the **Refresh account** (circular arrow) icon on the account's row at
any time to pull the latest stream list from your provider.

![M3U Accounts table row after a refresh, showing an updated stream-processing summary, a reduced groups count, and a fresh Last Updated timestamp](../../images/user_guide/getting-started/6-refresh-m3u.png)

**Result:** The row's status message reports how many streams were created,
updated, or removed, and **Last Updated** advances to the refresh time.

### 5. Create a few channels

Channels are what Dispatcharr actually serves: a channel is a number and a
name that one or more streams attach to.

1. Open **Channel Manager**.
2. Click **Edit Mode** (top right). Channel Manager batches channel changes
   into a staged set so nothing reaches Dispatcharr until you commit.
   Notice the toolbar grows a **Create new channel**, **Create new channel
   group**, and undo/redo cluster once Edit Mode is on.
3. Click **Create new channel** (the **+** icon).
4. Enter a **Channel Name** and a **Starting Channel Number**. Pick a
   number range you know is unused (check the existing channel list first).
   You can also set the **Channel Group** right here if the group already
   exists.
5. Click **Create Channel**. Repeat for each channel you want.

![Channel Manager with Edit Mode just enabled, showing the new Create new channel and Create new channel group icons in the toolbar](../../images/user_guide/getting-started/7-edit-mode-on.png)

![Create Channel dialog scrolled to the Channel Group field, with an existing group selected from the dropdown](../../images/user_guide/getting-started/8-create-channel.png)

**Result:** Each new channel appears in the Channels panel (under
Uncategorized if you didn't set a group) with a **0 streams** warning badge.
This is expected, since you haven't attached any streams yet. The Edit Mode
button shows a pending-changes count.

### 6. Create channel groups

Channel groups organize your Channel Manager list. They're independent
from the provider stream groups you toggled in step 3.

1. Still in Edit Mode, click **Create new channel group** (the folder+
   icon).
2. Enter a **Group Name** and click **Create Group**. Unlike channel
   creation, channel groups are created immediately. They aren't part of
   the staged edit set.
3. Repeat for each group you need. Set a channel's group from the **Create
   Channel** dialog (step 5) when creating it, or drag an existing channel
   row onto a group's header in the Channels panel to move it afterward.

![Create New Channel Group modal with a group name entered](../../images/user_guide/getting-started/9-create-channel-group-modal.png)

**Result:** Your new groups appear in the Channels panel with a live channel
count and number range, and the channels you assigned now sit inside them
instead of Uncategorized.

![Channels panel showing two new channel groups, each expanded to show the channels assigned to them](../../images/user_guide/getting-started/10-channel-groups-created.png)

### 7. Add streams to your channels

A channel with no streams won't play anything. This is the step that
actually wires a channel up to content.

1. In the **Streams** panel (right side), find the stream group your
   provider streams live in and expand it.
2. On the left, expand the channel row you want to attach a stream to. An
   empty channel shows **"No streams assigned. Drag streams here to add."**
3. Drag a stream row from the Streams panel onto that drop zone. Repeat for
   each channel.
4. When you're done creating channels, groups, and stream assignments,
   click **Done** next to Edit Mode. ECM shows a summary of everything
   that's staged (for example, "3 new channels created"). Click **Apply
   All** to push it all to Dispatcharr in one commit, **Keep Editing** to
   go back, or **Discard** to throw the staged changes away.

![Channels panel with a channel expanded showing an assigned stream, its play/preview/remove controls, and a stream-count badge on the parent channel row](../../images/user_guide/getting-started/11-add-streams-to-channels.png)

![Exit Edit Mode dialog listing pending changes with Keep Editing, Discard, and Apply All options](../../images/user_guide/getting-started/12-exit-edit-mode-apply.png)

**Result:** Once you click **Apply All**, the channels, groups, and stream
assignments are committed to Dispatcharr. Your channels are now playable.
Open **Guide** or a media client pointed at ECM/Dispatcharr to confirm.

## Going deeper

- [`docs/user_guide/channels-streams/index.md`](../channels-streams/index.md): day-to-day channel and stream management once your first batch exists.
- [`docs/user_guide/epg/index.md`](../epg/index.md): matching channels to EPG data and the dummy EPG template engine.
- [`docs/user_guide/channel-pipeline/index.md`](../channel-pipeline/index.md): automate channel creation from incoming streams instead of creating them one at a time.
- [`docs/architecture.md`](../../architecture.md): how the M3U → Channel Pipeline → Dispatcharr data flow fits together under the hood.
- [`docs/api.md`](../../api.md): the HTTP endpoints behind every action in this walkthrough, for scripting or automation.
