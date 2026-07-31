# ECM Retained Task Reference

> **Audience:** Operators who need detailed task instructions while the
> destination-specific `gsnw0` tutorials are still being completed.

This maintained reference preserves the actionable material from ECM's former
single-file guide. Navigation has been updated for the grouped sidebar and
current page names. For the workspace mental model, exact keyboard behavior,
and current screenshots, read [Find Your Way Around the Operator
Workspace](operator-workspace.md) first.

Control labels in **bold** are the current UI labels. A section is marked
**Unsupported in the current UI** only when its former workflow is no longer
shipped; do not infer that label for the other tasks on this page.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [M3U Manager](#m3u-manager)
3. [M3U Change Tracking](#m3u-change-tracking)
4. [Channel Manager](#channel-manager)
5. [EPG Manager](#epg-manager)
6. [TV Guide](#tv-guide)
7. [Logo Manager](#logo-manager)
8. [Stream & Channel Preview](#stream-channel-preview)
9. [Channel Pipeline](#channel-pipeline)
10. [FFMPEG Builder](#ffmpeg-builder)
11. [Journal](#journal)
12. [Stats Dashboard](#stats-dashboard)
13. [Notifications](#notifications)
14. [Settings](#settings)
15. [Authentication & Users](#authentication-users)
16. [CLI Tools](#cli-tools)
17. [Keyboard Shortcuts](#keyboard-shortcuts)
18. [Debug Logging](#debug-logging)
19. [Tips & Best Practices](#tips-best-practices)

---

## Getting Started

Enhanced Channel Manager (ECM) is a web-based interface for managing IPTV channels, EPG data, and stream configurations with Dispatcharr.

### First-Time Setup


**Step 1: Create Your Admin Account**

On first launch, ECM shows a setup wizard to create your administrator account.

1. Enter a **Username**
2. Enter your **Email** address
3. Choose a **Password** (minimum 8 characters, must include uppercase, lowercase, and a number)
4. Click **Create Account**

You'll be logged in automatically after setup.

**Step 2: Open Settings**


1. In the grouped sidebar, choose **System** → **Settings**.
2. Choose **Connections** → **General** in the **Settings sections** list that
   replaces the sidebar groups.

**Step 3: Configure Dispatcharr Connection**


1. Enter your **Server URL** (e.g., `http://192.168.1.100:5000`)
2. Enter your **Username**
3. Enter your **Password**
4. Click **Test Connection**

**Step 4: Verify Connection**


- A green checkmark indicates successful connection
- If connection fails, verify your URL and credentials

**Step 5: Save Settings**

1. Click **Save changes** to store your configuration
2. You're now ready to add M3U accounts

> **Tip:** The application header includes quick-access links to the GitHub repository and this User Guide.

---

## M3U Manager

The M3U Manager is where you add and configure your IPTV provider playlists.

### Overview


The M3U Manager displays:
- List of all configured M3U accounts
- Account status indicators
- Quick action buttons (refresh, manage groups, filters, etc.)

### Adding an M3U Account

**Step 1: Add the account**


1. Under **Operations**, open **M3U Manager**.
2. Select **Add M3U Account**.

**Step 2: Choose Account Type**


Choose from three account types:

| Type | Use Case |
|------|----------|
| **Standard M3U** | Direct URL to M3U playlist |
| **XtreamCodes (XC)** | XtreamCodes portal with login |
| **HD Homerun** | Local HD Homerun device |

**Step 3: Configure Standard M3U Account**


1. Enter an **Account Name**
2. Paste the **M3U URL** from your provider
3. Set **Max Streams** (concurrent connection limit)
4. Select **Create Account**

**Step 3 (Alternative): Configure XtreamCodes Account**


1. Enter an **Account Name**
2. Enter the **Server URL** (base URL without /get.php)
3. Enter your **Username** and **Password**
4. Set **Max Streams**
5. Select **Create Account**

> **Tip:** When editing an existing XtreamCodes account, you can change non-credential settings (name, max streams, etc.) without re-entering the password. Leave the password field empty to keep the existing credentials.

**Step 4: Account Refreshes Automatically**


After saving, the account automatically refreshes to load your channels.

### Understanding Account Status


| Status | Indicator | Meaning |
|--------|------|---------|
| Ready | Green check | Account loaded successfully |
| Error | Error status | Connection or parsing failed |
| Downloading | Spinner | Fetching playlist data |
| Processing | Spinner | Parsing M3U content |
| Disabled | Gray | Account turned off |

### Managing Channel Groups

**Step 1: Open Manage Groups**


1. Click **Manage Groups** on the account you want to configure

**Step 2: Enable/Disable Groups**


1. Toggle groups **on** to make them available in Channel Manager
2. Toggle groups **off** to hide them
3. Use **Hide Disabled** to show only enabled groups

**Step 3: Configure Auto-Sync (Optional)**


For automatic channel creation from a group:

1. Select **Configure auto-sync settings** next to a group
2. Configure auto-sync options:
   - **EPG Source Override**: Force specific EPG source
   - **Channel Group Override**: Place channels in different group
   - **Name Regex Pattern**: Transform channel names
   - **Channel Profile**: Assign default profile
3. Select **Save Settings**, close the auto-sync dialog, then select **Save &
   Refresh** in **Manage Groups**.

### Refreshing M3U Data


- Select **Refresh account** on an account to update its playlist
- Use **Refresh All** in the toolbar to refresh all accounts

### M3U Filters


Filters let you include or exclude streams:

1. Click **Manage Filters** on an account
2. Click **Add Filter**
3. Choose **Type**: Group, Name, or URL
4. Choose **Action**: Include or Exclude
5. Enter a **Regex Pattern**
6. Select **Create Filter**. Existing filters use **Save Changes**.
7. Drag filters to reorder (executed top to bottom)

---

## M3U Change Tracking

The M3U Changes page tracks all changes detected in your M3U playlists over time.

### Overview


Every time an M3U account is refreshed, ECM compares the new data against the previous snapshot and records any differences.

### Summary Statistics

At the top of the page, dashboard cards show:
- **Groups Added** - Total new groups discovered
- **Groups Removed** - Total groups that disappeared
- **Streams Added** - Total new streams found
- **Streams Removed** - Total streams that disappeared

### Filtering Changes

- **M3U Account** - Filter by specific M3U account
- **Change Type** - Group add/remove, stream add/remove
- **Enabled Status** - Filter by whether the affected group was enabled or disabled
- **Time Range** - Last 24 hours, 3 days, 7 days, 30 days, or 90 days
- **Search** - Full-text search across change descriptions
- **Sort** - Sort by time, account, type, group name, count, or enabled status

### Change Details

Click any change row to expand and see full details including individual stream names that were added or removed.

### M3U Change Notifications

Configure email digests for M3U changes in Settings → Alert Methods:
- **Immediate** - Send notification as soon as changes are detected
- **Hourly/Daily/Weekly** - Batched digest notifications
- **Discord** - Send change notifications to Discord webhooks
- **Regex Exclude Filters** - Define regex patterns to suppress noisy groups or streams from digest notifications (e.g., exclude VOD groups or known test streams that change frequently)

---

## Channel Manager

The Channel Manager is where you create and organize your channel lineup.

### Interface Overview


The screen is split into two panes:
- **Left Pane**: Your channel lineup organized by groups
- **Right Pane**: Available streams from M3U providers
- **Divider**: Drag to resize panes

### Creating Channels - Method 1: Drag and Drop

1. Select **Edit Mode**.
2. In **Streams**, use **All Providers**, **All Groups**, or **Search streams**
   to find the source.
3. Drag the source's **Drag inventory stream … to assign it to a channel**
   handle to a channel. To create instead, use the stream-group handle and
   choose a destination from **Create channels in group**.
4. Select **Done**, review **Exit Edit Mode**, then choose **Apply All**.

Keyboard users can focus either drag handle, press `Enter`, choose the
announced channel or group destination, and press `Enter` again. Press
`Escape` to cancel the drag.

### Creating Channels - Method 2: Bulk Creation

**Step 1: Select Stream Groups**

1. Select **Edit Mode**.
2. Expand each source group you want to use.
3. Activate its **Select all streams in group** checkbox. Repeat for each
   group; no modifier key is required.

**Step 2: Open Bulk Creation**

Use **Create** for the selected groups, or **Create in…** to choose an
existing group or **Create in new group…**. The bulk creation dialog opens.

**Step 3: Configure Bulk Creation**


1. Set **Starting Channel Number**
2. Choose **Group Selection**:
   - Same-named group (creates matching group)
   - Select existing group
   - Create new group
3. Select **Channel Profile** (optional)
4. Review the **preview** of channels to be created

**Step 4: Review and Create**


1. Review the channel list
2. Note the **stream count** per channel (merged duplicates)
3. Select **Create _N_ Channels**, where _N_ is the reviewed channel count.

### Smart Stream Merging


ECM automatically:
- **Merges duplicates**: Same channel from different providers
- **Orders by quality**: UHD → 4K → FHD → 1080p → HD → 720p → SD
- **Interleaves providers**: For failover redundancy

### Using Edit Mode

**Step 1: Enter Edit Mode**


1. Select **Edit Mode** to enable staged editing.
2. Changes remain staged until you review and apply them.

**Step 2: Make Changes**


In edit mode you can:
- Edit channel numbers (click to edit)
- Edit channel names (click to edit)
- Add/remove/reorder streams
- Delete channels (recoverable with undo)
- Move channels between groups

**Step 3: Use Undo/Redo**


- Use **Undo** and **Redo** in the Channels pane history controls.

**Step 4: Review, apply, or cancel**

1. Select **Done** to open **Exit Edit Mode**.
2. Review the summary, then choose **Keep Editing**, **Discard**, or **Apply
   All**.
3. The header's **Cancel** action abandons the Edit Mode session; when staged
   changes exist, ECM asks for confirmation before discarding them.

### Multi-Select Operations

**Step 1: Select Multiple Channels**

1. In **Edit Mode**, use each channel's checkbox.
2. To select a whole group, use **Select all channels in group**. Repeat for
   other groups without holding a modifier key.

**Step 2: Use Selection actions**

The bottom **Selection actions** bar appears. Choose a visible action, or
open **More selection actions** and choose **Move to group**. The former
right-click menu is no longer part of this workflow.

### Managing Streams Within Channels


- **Add**: Drag streams from the Streams pane onto a channel.
- **Remove**: Select the assigned row's **Remove stream** button
  (`remove_circle`).
- **Reorder**: Drag assigned streams up or down; the highest row has the
  highest priority.

### Sort & Renumber

Sort and renumber channels within a group:

1. Open the group's actions menu.
2. Choose **Sort & Renumber**
3. Options:
   - **Alphabetical Sort** - Sort channels A-Z
   - **Smart Name Sorting** - Ignores channel number prefixes when sorting (e.g., "101 | Sports" sorts as "Sports")
   - **Sequential Renumber** - Assign sequential numbers starting from any value
4. Preview the result before applying
5. The entire operation can be reversed with the Channels pane **Undo**
   control while the Edit Mode session remains active.

### Copy Channel & Stream URLs

- For a channel, open **Channel actions** and select **Copy URL** to copy its
  Dispatcharr proxy stream URL.
- For an assigned stream, select its inline **Copy stream URL** button to copy
  the direct URL.
- These URLs are useful for testing in external players.

### Channel Profiles

- View and manage stream transcoding profiles
- Set a **default channel profile** in Settings → Channel Defaults
- Select profiles when creating channels (single or bulk)
- Assign profiles to existing channels via the edit modal

### Filtering Channels and Streams


**Channel Filters (Left Pane)**:
- Search by name
- Show/hide specific groups
- Show/hide empty groups
- Show/hide provider groups
- **Missing Data Filters** - Filter by channels missing:
  - Missing Logo
  - Missing TVG-ID
  - Missing EPG Data
  - Missing Gracenote ID
  - Active filter indicator on the filter button

**Stream Filters (Right Pane)**:
- M3U Account dropdown
- Group dropdown
- Search by name
- Hide already-mapped streams

---

## EPG Manager

The EPG Manager configures your Electronic Program Guide data sources.

### Overview


### Adding an EPG Source

**Step 1: Add the source**

1. Under **Operations**, open **EPG Manager**.
2. Select **Add Standard EPG**.

**Step 2: Configure XMLTV Source**


1. Enter a **Name** for the source
2. Paste the **XMLTV URL**
3. Set **Refresh Interval** (hours between updates; set to 0 for manual refresh only)
4. Select **Add EPG**. When editing an existing source, the confirmation is
   **Save Changes**.

**Step 3: Source Refreshes**


The source automatically fetches and parses EPG data.

### Setting Source Priority


1. Drag sources up/down to change priority
2. Higher sources take precedence for channel matching

### Creating Dummy EPG

For channels without upstream guide data, use **Dummy EPG Profiles** at the
bottom of **EPG Manager**. Create a profile, configure its templates, and use
**Add to Dispatcharr** or copy its XMLTV URL. The older workflow that created
a dummy source directly is deprecated; existing legacy sources remain
editable. See [EPG — Dummy EPG: profiles, not legacy
sources](epg/index.md#dummy-epg).

### Bulk EPG Assignment

**Step 1: Select Channels**


1. In **Edit Mode**, select the channels with their checkboxes.
2. In the bottom **Selection actions** bar, choose **Assign EPG**.

**Step 2: Review Matches**


1. Choose the EPG sources, then select **Match _N_ Channels**.
2. Review **Auto-Matched**, **Needs Review**, and unmatched results.
3. For conflicts, choose **Review Changes** or **Accept Best Guesses**.

**Step 3: Resolve Conflicts**


1. For each conflict, select the correct EPG entry or **Skip this channel**.
2. Select **Assign _N_ Channels** when the reviewed assignment count is
   correct.

---

## TV Guide

The Guide page displays your EPG data in a grid format.

### Guide Overview


Features:
- Time header with current time indicator (red line)
- Channel list on left
- Program grid with 6-hour window
- Currently airing programs highlighted

### Navigation


- **Date**: Browse different days
- **Start**: Jump to a starting hour
- **Profile**: Show a specific channel profile
- **Group**: Filter to a group or switch to jump mode

### Viewing Program Details


- Hover over any program to see full details
- Click a channel to edit its settings

### Print Guide


1. Click **Print Guide**
2. Select groups to include
3. Choose display mode
4. Use browser print function

---

## Logo Manager

### Logo Library


- Browse all logos with previews
- Toggle between list and grid view
- **Search** logos by name
- See usage count per logo
- **Pagination** - Choose page size (25, 50, 100, 250) with page navigation
- All logos are automatically loaded by paginating through Dispatcharr's API

### Adding Logos

1. Under **Operations**, open **Logo Manager** and select **Add Logo**.
2. Enter the logo name and either provide its URL or upload an image.
3. Select **Add Logo**. Use **Search logos**, **Unused only**, **List view**,
   and **Grid view** to find it later.

---

## Stream & Channel Preview

Preview streams and channels directly in your browser before assigning them.

### Previewing a Stream

1. On an assigned stream row, select **Preview stream in browser**
   (`visibility`).
2. The preview modal opens with an embedded video player.
3. Stream metadata is displayed (name, TVG-ID, group, M3U provider).

The adjacent **Open in VLC** button uses `play_circle`; it does not open the
browser preview.

### Previewing a Channel

1. Open the channel's **Channel actions** menu and select **Preview**.
2. This tests the actual Dispatcharr proxy stream output.
3. It verifies that the channel works correctly end to end.

### Preview Modes

Configure the preview mode in Settings → Stream Preview:

| Mode | Description |
|------|-------------|
| **Passthrough** | Direct proxy, fastest but may fail on AC-3/DTS audio |
| **Transcode** | FFmpeg transcodes audio to AAC for browser compatibility (recommended) |
| **Video Only** | Strips audio entirely for quick silent preview |

The current mode is shown as an indicator in the preview modal.

### Alternative Options

From the preview modal you can also:
- **Open in VLC** - Launch the stream in VLC media player
- **Download M3U** - Download an M3U playlist file
- **Copy URL** - Copy the direct stream URL

---

## Channel Pipeline

The Channel Pipeline page lets you automate channel creation with a rules-based engine. Define conditions to match streams and actions to create channels, merge streams, and assign metadata automatically.

### Creating a Rule

1. Under **Automation**, open **Channel Pipeline** and select **Create Rule**
2. Enter a **Rule Name** and optional **Description**
3. Configure **Conditions** to match streams
4. Configure **Actions** to define what happens
5. Select **Create Rule**. An existing rule's editor uses its displayed update
   action instead.

### Conditions

Build matching logic using a three-part editor (Field + Operator + Value) with AND/OR connectors:

| Field | Operators |
|-------|-----------|
| **Stream Name** | contains, does not contain, begins with, ends with, matches (regex) |
| **Stream Group** | contains, matches (regex) |
| **TVG ID** | exists, does not exist, matches |
| **Logo** | exists, does not exist |
| **M3U Account** | is, is not (specific M3U account) |
| **Quality** | at least, at most (2160p, 1080p, 720p, 480p, 360p) |
| **Codec** | is, is not (H.264, HEVC, etc.) |
| **Channel Exists** | by name, regex, or group |
| **Normalized Match in Group** | stream's normalized name matches a channel in a specified group |
| **Normalized Name (Global)** | stream's normalized name matches any channel across all groups |
| **Normalized Name (Not In)** | stream's normalized name does NOT match any channel in a specified group |

#### AND/OR Connectors

Between each condition is a clickable **AND/OR toggle**. Click it to switch between AND and OR. Understanding how these work is important for building effective rules.

**AND** means "also require this." All conditions connected by AND must be true together for a match.

**OR** means "or alternatively match this." OR creates a separate group of conditions — if *any* OR-group fully matches, the stream matches the rule.

**Order of operations:** AND binds tighter than OR, just like multiplication before addition in math. Conditions connected by AND are grouped together first, then OR separates those groups.

**Example 1 — Simple AND (all must match):**

```
Stream Name contains "ESPN"  AND  Stream Group contains "US"
```
Matches only streams with "ESPN" in the name that are also in a "US" group. Both must be true.

**Example 2 — Simple OR (either can match):**

```
Stream Name contains "ESPN"  OR  Stream Name contains "Fox Sports"
```
Matches streams with either "ESPN" or "Fox Sports" in the name.

**Example 3 — Mixed AND/OR (order of operations):**

```
Stream Name contains "ESPN"  AND  Quality at least 1080p  OR  Stream Name contains "Fox Sports"  AND  Quality at least 720p
```
This is evaluated as two groups:
- **Group 1:** Stream Name contains "ESPN" **AND** Quality at least 1080p
- **Group 2:** Stream Name contains "Fox Sports" **AND** Quality at least 720p

A stream matches if *either* group fully matches. So "ESPN HD 1080p" matches via Group 1, and "Fox Sports 720p" matches via Group 2, but "ESPN 480p" does not match (fails Group 1's quality requirement, and doesn't match Group 2 at all).

**Example 4 — Common pattern for multi-provider merging:**

```
Normalized Match in Group = "Documentaries"  AND  Stream Group matches "^US"
```
Matches any stream from a US group whose normalized name matches a channel in your Documentaries channel group. Pair this with a `merge_streams(target: auto)` action to automatically merge matching streams into existing channels.

> **Tip:** Think of OR as creating separate "paths to match." Each path (AND-group) is evaluated independently. If you want "match A and B, or match C and D", place AND between A-B and between C-D, with OR between the two groups.

#### Normalized Match in Group

This condition type is particularly useful for merging streams into existing channels. It normalizes both the stream name (stripping country prefixes like "US:") and channel names (stripping number prefixes like "106 |") using the normalization engine, then checks if the normalized stream name matches any channel in the selected group. The group selector only shows channel groups that actually contain channels.

The **Global** variant checks against all channel groups at once, while the **Not In** variant inverts the match — useful for finding streams that don't yet have a corresponding channel.

#### Date Expansion in Regex

Regex conditions support date patterns that automatically expand to match current dates. For example, a pattern like `{date:YYYY-MM-DD}` in a regex condition will expand to match today's date. This is useful for matching streams that include dates in their names (e.g., PPV events). Date expansion supports patterns up to 90 days out to prevent regex overload. Contributed by @lpukatch.

Saving a rule validates the *expanded* pattern, so a date token like `{date+3d}` saves without a regex-validation error — the same expansion the pipeline applies when the rule actually runs.

### Actions

Define what happens when conditions match:

| Action | Description |
|--------|-------------|
| **Create Channel** | Template-based naming using `{stream_name}`, `{stream_group}`, `{quality}`, `{provider}`, etc. |
| **Create Group** | Automatically create a channel group |
| **Merge Streams** | Combine multiple streams into one channel with quality preference; auto-find uses multi-stage lookup (normalized name → core-name → call sign → deparen/word-prefix); optional max streams per provider limit |
| **Assign Logo** | Set channel logo from stream or URL |
| **Assign EPG** | Assign EPG data source |
| **Assign Profile** | Set stream transcoding profile |
| **Set Channel Number** | Auto-assign or specify number/range |
| **Set Variable** | Define reusable variables with regex extraction |
| **Name Transform** | Apply regex find/replace to channel names |
| **Skip / Stop** | Skip stream or stop processing further rules |

When a channel already exists, choose behavior:
- **Skip** - Don't create the channel
- **Merge (create if new)** - Add streams to existing channel, or create a new one if no match found
- **Merge Only (existing only)** - Add streams to existing channel only; skip if no match (never creates new channels)
- **Update** - Update existing channel properties
- **Use Existing** - Use the existing channel without changes

### Rule Options

- **Priority** - Drag rules to reorder execution priority
- **Run on M3U Refresh** - Auto-execute when M3U accounts refresh
- **Stop on First Match** - Stop evaluating further rules when a stream matches
- **Normalize Names** - Apply name normalization during processing
- **Sort Field** - Sort matched streams by name, group, or quality
- **Probe on Sort** - Probe unprobed streams for resolution data before quality sorting

### Execution

**Dry Run** - Click **Dry Run** to preview what changes would occur without applying them. Review the execution results showing channels that would be created, streams that would be merged, and orphans that would be removed.

**Execute** - Click **Run** to apply all rule actions. The execution log shows per-stream details of condition evaluation, rule matching, action results, normalization context, and merge guidance. Use **filter chips** at the top of the log to quickly filter by result type (created, merged, skipped, etc.).

**Run Single Rule** - Execute or dry-run a specific rule in isolation from the rule's menu.

**Rollback** - Undo a completed execution from the execution history to restore the previous state.

**Execution History Summary** - Each execution in the history shows a quick summary: streams matched, channels merged, channels created, and streams skipped. A live "Running" indicator appears while a pipeline is executing.

**Auto-Find for Merge Streams** - When using the `merge_streams` action with `target: auto` and no explicit `find_channel_by`, the engine uses a multi-stage lookup to find existing channels:

1. **Normalized Name** - Strips country prefixes (e.g., "US: Discovery" → "Discovery") and matches against channel names that may have number prefixes (e.g., "113 | Discovery")
2. **Core-Name Fallback** - If no match, strips all tags from the stream name and tries again
3. **Call Sign Fallback** - If still no match, compares against channel call signs from EPG data
4. **Deparen/Word-Prefix** - Strips parenthetical suffixes (e.g., "ESPN (East)" → "ESPN") and tries word-prefix matching

This means you can set up a simple rule with `normalized_name_in_group` + `merge_streams(target: auto)` to automatically merge streams from any provider into your existing channel lineup without manual channel-by-channel mapping.

**Max Streams Per Provider** - The merge_streams action supports an optional `max_streams_per_provider` setting that limits how many streams from a single M3U account can be merged into a channel. This prevents one provider from dominating a channel's stream list and is enforced against both newly-added and existing streams.

### Orphan Reconciliation

When a rule's conditions change and previously-matched streams no longer match, the channels they created become "orphans." Configure per-rule orphan handling:

| Action | Behavior |
|--------|----------|
| **Delete** | Remove orphaned channels entirely |
| **Move to Uncategorized** | Move channels out of managed groups |
| **Delete & Cleanup Groups** | Delete channels and remove empty groups |
| **None** | Preserve all channels, skip reconciliation |

### Global Exclusion Filters

Configure stream exclusion filters under **System** → **Settings** →
**Channel Pipeline**:

- **M3U Group Dropdown** - Select which M3U groups to include in rule evaluation
- **Exclusion Patterns** - Define regex patterns to exclude streams before any rules are evaluated
- Exclusion filters apply globally to all rules, saving you from repeating the same exclusion conditions in every rule

### YAML Import/Export

- **Export** - Download all rules as YAML for backup or sharing
- **Import** - Paste YAML rule definitions to create rules
- Useful for version control and sharing configurations between instances

---

## FFMPEG Builder

> **Unsupported in the current UI:** ECM no longer ships the standalone
> FFMPEG Builder page. The material below is retained only to help operators
> identify configurations created by older ECM versions. Do not expect these
> controls or **Push to Dispatcharr** in the current workspace.

The former FFMPEG Builder provided a visual interface for constructing FFmpeg
transcoding and streaming commands without writing command-line syntax.

### Simple Mode (IPTV Wizard)


Simple mode is the default and is purpose-built for IPTV streaming:

**Step 1: Choose a Preset**

The preset bar at the top offers 8 optimized IPTV templates:

| Preset | Description |
|--------|-------------|
| **Pass-through** | Copy streams without re-encoding (fastest) |
| **IPTV Standard (H.264)** | Software encode for universal compatibility |
| **IPTV HD (NVIDIA)** | Hardware NVENC encoding for NVIDIA GPUs |
| **IPTV HD (Intel QSV)** | Hardware Quick Sync for Intel GPUs |
| **Low-Latency AC3** | Minimal latency with AC3 surround sound |
| **HLS Output** | Segmented HTTP Live Streaming format |
| **1080p / AAC** | Full HD software encode with stereo audio |
| **4K / AC3** | 4K HEVC with 5.1 surround sound |

Click any preset to load its configuration instantly.

**Step 2: Configure Source**

1. Enter the **Source URL** or use `{streamUrl}` for Dispatcharr runtime substitution
2. Choose the **Processing Mode** (codec/hardware)
3. Select the **Audio Codec** (AAC for compatibility, AC3 for surround)
4. Configure audio channels (stereo, 5.1, 7.1)

**Step 3: Set Output**

1. Choose **Output Format**: MPEG-TS (piping to Dispatcharr) or HLS (segmented streaming)
2. Enable/disable **Stream Options** for network resilience (auto-reconnect, buffer sizes)

> **Which should you choose?**
>
> **Choose MPEG-TS if:** You have a wired connection, a very stable ISP, and hate when your live TV is lagging behind the "real-time" broadcast.
>
> **Choose HLS if:** You are on WiFi, your ISP throttles traffic, you experience buffering, or you use catch-up features.
>
> **For Dispatcharr:** If the IPTV provider offers both, try HLS first for better stability. However, if your IPTV provider is solid and you want the fastest possible channel changing, try MPEG-TS.
>
> **Performance tip:** Matching your output format to your provider's source format (e.g., MPEG-TS in → MPEG-TS out) avoids container remuxing, which reduces CPU usage and latency. If your provider delivers MPEG-TS, prefer MPEG-TS output; if they deliver HLS, prefer HLS output.

### Advanced Mode


Switch to Advanced Mode for full control over every FFmpeg parameter. The interface is organized into sections:

#### Input Source
- **Input Type** - URL or Pipe
- **Format Override** - Auto-detect or force (MPEGTS, HLS, MP4, Matroska, FLV)
- **Hardware Acceleration** - CUDA (NVIDIA), QSV (Intel), VAAPI (AMD/Intel), or CPU-only
- **Device Selection** - GPU device path for VAAPI (e.g., `/dev/dri/renderD128`)

#### Video Codec
- **Codec Selection** - Software (libx264, libx265, VP9, AV1) or hardware (NVENC, QSV, VAAPI)
- **Rate Control** - CRF (quality-based), CBR (constant bitrate), VBR (variable), CQ, QP
- **Encoding Parameters** - Preset, profile, level, pixel format, tune
- **Keyframe Control** - GOP size, minimum interval, scene change threshold, B-frames

#### Audio Codec
- **Codec** - Copy (passthrough), AAC, AC3, EAC3
- **Parameters** - Bitrate, sample rate, channels, channel layout, AAC profile

#### Video Filters
Add video processing filters in an ordered chain:
- **Scale** - Resize video resolution
- **FPS** - Change framerate
- **Deinterlace** - Remove interlacing (yadif)
- **Format** - Color format conversion
- **Hardware Upload** - Move frames to GPU memory
- **Custom** - Write custom filter expressions

#### Audio Filters
Add audio processing filters:
- **Volume** - Adjust loudness level
- **Loudness Normalization** - LUFS-based normalization
- **Resample** - Change audio sample rate
- **Custom** - Write custom audio filter expressions

#### Stream Mapping
Select specific tracks from multi-stream inputs:
- Map by type (video:0, audio:0, subtitle:0)
- Or include all streams from the input

#### Output
- **Output Path** - File path or `pipe:1` for Dispatcharr piping
- **Container Format** - MPEG-TS, HLS, or DASH
- **Container Options** - Format-specific settings

### Command Preview


The command preview updates in real-time as you configure settings:

- **Plain View** - Full FFmpeg command text with copy-to-clipboard
- **Annotated View** - Every flag explained in plain English with color coding
- **Interactive Tooltips** - Hover over any flag for detailed explanation
- **Warning Indicators** - Alerts for incompatible settings (e.g., audio filters with "copy" codec)

### Pushing to Dispatcharr

Click **Push to Dispatcharr** in the command preview to create a stream profile directly:

1. The builder configuration is converted to a Dispatcharr stream profile
2. The profile is created in your Dispatcharr instance
3. You can then assign it to channels in Channel Manager

### Saved Profiles

Save your builder configurations for reuse:

1. Configure the builder with your desired settings
2. Click **Save Profile** and enter a name
3. Your profile appears in the "My Profiles" section of the preset bar
4. Click any saved profile to load it instantly
5. Delete profiles you no longer need

### Stream Probing

Probe your input source to see what's available:

1. Enter a source URL in the input section
2. Click **Probe** to analyze the source
3. View detected streams with codec, resolution, framerate, and bitrate
4. Use the probe results to inform your codec and filter decisions

### ECM Integration

Apply builder configurations to your channel system:

- **All Channels** - Apply a profile to every channel
- **By Group** - Apply to channels in a specific group
- **By Channel** - Apply to individual channels
- Enable/disable profiles without deleting them

---

## Journal

### Activity Log


The Journal tracks all changes:
- Channel operations (create, update, delete)
- EPG changes
- M3U operations
- Watch events

### Filtering


- **Category**: Channel, EPG, M3U, Watch
- **Action Type**: Create, Update, Delete, etc.
- **Time Range**: Last hour to all time
- **Search**: Full-text search

### Entry Details


Click any entry to see full details including before/after values.

---

## Stats Dashboard

### Live Statistics


Monitor in real-time:
- Active streaming channels
- FFmpeg speed (color-coded)
- Bitrate and FPS
- Connection counts per M3U account

### Channel Metrics


| Metric | Meaning |
|--------|---------|
| Speed (green) | ≥0.98x - Excellent |
| Speed (yellow) | ≥0.90x - Acceptable |
| Speed (red) | <0.90x - Buffering likely |

### Historical Charts


Click any channel to expand and see:
- Speed over time
- Bandwidth usage trends

### Auto-Refresh


Set refresh interval:
- Manual
- 10 seconds
- 30 seconds
- 1 minute
- 5 minutes

Polling automatically pauses when the browser tab is hidden to save resources.

### Enhanced Stats (Popularity & Analytics)

The Stats page also includes advanced analytics:

**Unique Viewer Tracking**
- Count unique connecting IPs per channel over configurable periods (7, 14, or 30 days)

**Popularity Rankings**
- Channels ranked by a weighted popularity score based on:
  - Watch count (30%)
  - Watch time (30%)
  - Unique viewers (25%)
  - Bandwidth usage (15%)
- Paginated rankings with visual indicators

**Trend Analysis**
- **Trending Up** - Channels gaining popularity (>10% increase)
- **Trending Down** - Channels losing popularity (>10% decrease)
- **Stable** - Channels with consistent viewership
- Visual trend arrows and percentage changes

**Per-Channel Bandwidth**
- Track bandwidth consumption per channel with breakdown by connections and watch time

**Watch History Log**
- Detailed log of all channel viewing sessions with IP addresses and durations

**On-Demand Calculation**
- Manually trigger popularity score recalculation

---

## Notifications

### Notification Center

Open **Notifications** in the header.

- **Unread Badge** - Shows count of unread notifications
- **Notification List** - View past notifications with timestamps
- **Mark as Read** - Mark individual or all notifications as read
- **Delete notification** clears one item. **Clear read notifications** clears
  read items, and **Delete all notifications** clears the list.
- **Color-Coded Types** - Info (blue), Success (green), Warning (yellow), Error (red)

### Alert Methods

Configure external notifications in Settings → Alert Methods:

| Method | Configuration |
|--------|--------------|
| **Discord** | Webhook URL |
| **Telegram** | Bot token + chat ID |
| **Email (SMTP)** | Server, port, credentials, recipients |

Each method supports:
- **Source Filtering** - Control which event types trigger notifications
- **Severity Levels** - Choose which severity levels to receive (info, success, warning, error)
- **Test Alerts** - Send test notifications to verify configuration
- **Failed Stream Details** - Task alerts include names of failed streams

---

## Settings

### Settings Navigation

Open **System** → **Settings**. The sidebar swaps its groups for the
**Settings sections** list — grouped as **Connections**, **Channel
Processing**, **Notifications & Reports**, **Upkeep**, **Workspace**, and (for
administrators) **Administration**. Select **Back** to restore the main
groups. Contextual links elsewhere in ECM can open the correct section
directly. For the group contents, the audited page list, **On this page**
behavior, and exact **Save changes** / **Cancel changes** safeguards, see
[Settings and contextual
links](operator-workspace.md#settings-and-contextual-links).

### Tag-Based Normalization

Configure which tags to strip from stream names during bulk channel creation:

**5 Built-in Tag Groups:**
- **Country** - US, UK, CA, AU, BR, and 60+ country codes
- **League** - NFL, NBA, NHL, MLB, UFC, EPL, and 50+ league abbreviations
- **Network** - PPV, LIVE, BACKUP, VIP, PREMIUM, 24/7, REPLAY
- **Quality** - HD, FHD, UHD, 4K, SD, 1080P, 720P, HEVC, H264, etc.
- **Timezone** - EST, PST, ET, PT, GMT, UTC, and 40+ timezone abbreviations

**Managing Tags:**
1. Click a tag group to expand it
2. Toggle individual tags on/off
3. Add **Custom Tags** with mode selection:
   - **Prefix only** - Strip when tag appears at start of name
   - **Suffix only** - Strip when tag appears at end of name
   - **Any position** - Strip tag wherever it appears
4. See counts of active, disabled, and custom tags per group
5. Use **Reset to Defaults** to restore default configuration

These settings are pre-loaded as defaults in the bulk create modal, adjustable per-operation via the Quick Tag Manager.

### Stream Probing


Configure automated stream health checking:

1. Under **System** → **Settings** → **Maintenance**, configure **Probe
   timeout (seconds)**, **Bitrate measurement duration**, and **Stream fetch
   page limit**.
2. If appropriate for provider limits, enable **Enable parallel probing** and
   set **Max concurrent probes**.
3. Configure when probing runs under **Settings** → **Scheduled Tasks** by
   editing the Stream Probe task. Scheduling is not an implicit save on the
   Maintenance page.

#### Per-Account Ramp-Up

ECM gradually increases probe load per M3U account rather than hitting the provider with full concurrency immediately. This prevents triggering rate limits or connection blocks. The ramp-up starts conservatively and increases over time as probes succeed.

#### Probe Retry Coverage

Probes automatically retry on common transient failures:
- **Transient HTTP 200** - Server returns 200 but with invalid/empty data
- **I/O Errors** - Network timeouts, connection resets, and socket errors
- **"Invalid data found"** - ffprobe reports invalid data (often transient with live streams)

#### Stale Group Alerts

When channel groups have outdated probe data (e.g., probing was disabled or failed for an extended period), ECM generates notifications alerting you to re-probe those groups.

#### Profile-Aware Probing

When an M3U account has multiple profiles (configured in Dispatcharr), ECM automatically distributes probe connections across them. Each profile has its own max connection limit, and ECM rewrites stream URLs using the profile's search/replace patterns so probes go through the correct profile endpoint.

#### Profile Distribution Strategy

If any M3U account has multiple profiles, a **Profile Distribution Strategy** dropdown appears in Settings → Maintenance under "Enable parallel probing":

| Strategy | Behavior |
|----------|----------|
| **Fill First** (default) | Uses the default profile until it reaches its connection limit, then spills over to the next profile. Best when you want to minimize the number of active profiles. |
| **Round Robin** | Rotates across profiles one at a time so each gets an equal share of probe connections. Good for spreading usage evenly. |
| **Least Loaded** | Picks the profile with the most remaining headroom (highest ratio of free connections). Best for maximizing throughput when profiles have different connection limits. |

This setting only affects probing — it does not change how Dispatcharr routes viewer traffic.

### Black Screen Detection

When enabled in **System** → **Settings** → **Maintenance**, ECM runs an
ffmpeg signalstats check after a successful probe to detect dark or blank
content. Channel rows summarize the highest-priority state of their assigned
streams. Detailed black-screen state belongs to an assigned stream row. The
unassigned **Streams** inventory intentionally shows neither probe health nor
strike details.

- **Enable/Disable** — Checkbox in the Stream Probing section
- **Sample Duration** — How long to sample each stream (3-30 seconds, default 5). Longer samples are more accurate but slower.

### Low FPS Detection

ECM flags assigned streams whose framerate falls below the configured
threshold. Channel rows summarize that state; the assigned stream row carries
the probe detail. The **Streams** inventory does not show a low-FPS badge.

- **Threshold** — Configurable in Settings → Maintenance via a dropdown (5, 10, 15, or 20 FPS, default 20)
- **Always On** — No enable/disable toggle since it has zero overhead

Both black screen and low FPS counts appear in probe progress notifications, probe history, and the notification center.

### Stream Strikeout System

The strikeout system helps you identify and clean up streams that consistently fail probe checks.

**How It Works:**
1. Each stream tracks its **consecutive probe failures** — the counter resets when a probe succeeds
2. When a stream exceeds the configurable **strike threshold** (set in Settings → Maintenance), it is flagged as "struck out"
3. **Strike badges** appear on assigned stream rows in Channel Manager,
   showing the failure count. They never appear in the **Streams** inventory.
4. In **Settings** → **Maintenance**, choose **Scan for Struck Out Streams**
   and select the rows you intend to change.
5. Choose **Remove _N_ Stream(s) from All Channels**. This removes only the
   selected assigned streams; it does not delete them from the source
   inventory.

This is useful for cleaning up dead or unreliable streams that accumulate over time, especially after provider changes or M3U updates.

### Stream Sort Priority


1. Drag criteria to set priority order
2. Toggle individual criteria on/off
3. Enable "Deprioritize Failed Streams"

The default-disabled **Catch-up** criterion prefers streams that Dispatcharr
marks as catch-up enabled. Drag it higher or lower to decide when catch-up
availability should win over resolution, bitrate, and the other enabled
criteria.

### Stream Preview Settings

Configure how streams and channels are previewed in the browser:

| Mode | Description |
|------|-------------|
| **Passthrough** | Direct proxy, fastest but may fail on AC-3/E-AC-3/DTS audio |
| **Transcode** | FFmpeg transcodes audio to AAC for browser compatibility (recommended) |
| **Video Only** | Strip audio entirely for silent quick preview |

Change mode anytime; takes effect on the next preview.

### Channel Defaults

Default options pre-loaded when using bulk channel creation:

- **Default Channel Profile** - Stream profile for new channels
- **Auto-Rename on Number Change** - Update channel names when numbers change
- **Include Channel Number in Name** - Add number prefix (e.g., "101 - Sports Channel")
- **Number Separator** - Choose hyphen (-), colon (:), or pipe (|)
- **Remove Country Prefix** - Strip country codes from names (bulk create modal also offers "Keep" with normalized formatting)
- **Timezone Preference** - Default handling for East/West regional variants

These defaults appear in the bulk create modal with a "(from settings)" indicator.

### Appearance

- **Theme** - Dark (default), Light, or High Contrast
- **Show Stream URLs** - Toggle stream URL visibility (hide for screenshots)
- **Hide Auto-Sync Groups** - Auto-hide auto-sync channel groups on load (channels persist in ECM even when auto-sync is later disabled in Dispatcharr)
- **Hide EPG URLs** - Hide EPG source URLs in the EPG Manager
- **Hide M3U URLs** - Hide M3U server URLs in the M3U Manager
- **Gracenote ID Conflict Handling** - Ask, Skip, or Overwrite when assigning conflicting Gracenote IDs
- **Frontend Log Level** - Console logging verbosity (Error, Warn, Info, Debug)

### VLC Integration

Open streams directly in VLC from your browser:

**Behavior Options:**
- **Try VLC Protocol** - Attempt vlc:// protocol, show helper if it fails
- **Fallback to M3U** - Try vlc:// first, download M3U file if it fails
- **Always M3U** - Always download M3U file (most compatible)

**Protocol Handler Setup:**
Download and run the setup script for your OS:
- **Windows** - PowerShell script with registry setup
- **Linux** - Shell script creating .desktop file for xdg-open
- **macOS** - Shell script creating AppleScript handler

### Normalization Engine


Create custom rules to automatically transform stream names:

**Step 1: Create a Rule**

1. Click **Add Rule** to create a new normalization rule
2. Enter a descriptive **Rule Name**
3. Configure the **Condition** (when the rule applies)
4. Configure the **Action** (what transformation to apply)
5. Select **Create Rule**. Existing rules use **Save Changes**.

**Step 2: Configure Conditions**

Available condition types:
- **Contains** - Matches if name contains the text
- **Starts With** - Matches if name starts with the text
- **Ends With** - Matches if name ends with the text
- **Equals** - Matches if name exactly equals the text
- **Regex** - Matches using a regular expression pattern

**Step 3: Configure Actions**

Available action types:
- **Remove Prefix** - Remove text from the beginning
- **Remove Suffix** - Remove text from the end
- **Replace** - Replace matched text with new text
- **Regex Replace** - Replace using regex pattern and replacement
- **Set Value** - Set the entire name to a specific value

**Step 4: Use Compound Conditions**

Build complex logic by combining conditions:
- **AND** - All conditions must match
- **OR** - Any condition can match
- **NOT** - Inverts the condition result

**Step 5: Test Your Rules**

1. Enter sample stream names in the **Test Panel**
2. See real-time preview of how names will be transformed
3. Adjust rules as needed before saving

**Step 6: Enable Auto-Normalization**

Toggle **Normalize on Channel Create** to automatically apply rules when creating new channels.

### Scheduled Tasks


Configure automated tasks:

1. Click **Add Schedule** on a task
2. Choose schedule type (Interval, Daily, Weekly, etc.)
3. Set the timing
4. Enable/disable as needed

### Alert Methods Configuration


Configure SMTP, Discord, or Telegram in the settings above **Alert Methods**,
then save the settings. **Alert Methods** lists the resulting methods. Use
**Send test to _name_** to test one, or **Delete _name_** and the typed
confirmation dialog to remove it.

---

## Authentication & Users

### Login

When authentication is enabled, ECM requires login to access the application.

- For a local account, choose **Local Account** when the provider selector is
  shown, enter **Username** and **Password**, then select **Sign In**.
- For Dispatcharr SSO, choose **Dispatcharr** when the provider selector is
  shown, enter the Dispatcharr **Username** and **Password**, then select
  **Sign in with Dispatcharr**.
- The provider selector appears only when both providers are enabled. If only
  Dispatcharr is enabled, the Dispatcharr sign-in form is shown directly.
- Sessions are maintained with automatic token refresh.

### Authentication Settings

Administrators configure authentication in **Settings** → **Authentication**:

- **Require Authentication** - Enable or disable login requirement
- **Enable local authentication** - Allow locally stored username/password
  accounts
- **Minimum Password Length** - Set the local minimum from 6 to 32 characters
- **Enable Dispatcharr SSO** - Allow sign-in with Dispatcharr credentials
- **Auto-create Users** - Create an ECM account on a Dispatcharr user's first
  successful sign-in

Select **Save Authentication Settings** after changing these controls. There
is no primary-auth-mode selector; the enabled providers determine which login
choices are available.

### User Management (Admin)

Administrators manage accounts in **Settings** → **User Management**. The list
shows **Username**, **Email**, **Provider**, **Status**, **Role**, and
**Actions**.

- Select **Edit** to change **Email**, **Active**, or **Admin**, then select
  **Save** or **Cancel**.
- Select the **Active** or **Inactive** status button to deactivate or activate
  another account.
- Select **Delete** to permanently delete another account after confirming the
  browser prompt.
- ECM disables deactivation and deletion for the currently signed-in account.
- **Admin** users can open these settings and manage other accounts. **User**
  accounts cannot.

There is no create-user control in **User Management**. Dispatcharr users can
be provisioned by **Auto-create Users**; the initial local administrator is
created by the first-run **Create Account** flow.

### Profile and Local Password

Open the signed-in user menu:

- **Edit Profile** changes **Display Name** and **Email**; select **Save
  Changes**.
- Local accounts also have **Change Password**. Enter **Current Password**,
  **New Password**, and **Confirm New Password**, then select **Change
  Password**.
- Dispatcharr-authenticated accounts do not receive the local **Change
  Password** action.

### Local Password Reset

**Via email (SMTP required):**

1. On the local sign-in form, select **Forgot password?**.
2. Enter **Email Address** and select **Send Reset Link**.
3. Open the emailed link, enter **New Password** and **Confirm Password**, then
   select **Reset Password**. The link is valid for one hour.

**Via Command Line (No SMTP Needed):**
See [CLI Tools](#cli-tools) below.

---

## CLI Tools

### Password Reset

When locked out or SMTP is not configured, reset passwords from the command line:

```bash
# Interactive mode — lists users, prompts for everything
docker exec -it enhancedchannelmanager python /app/reset_password.py

# Non-interactive — specify username and password
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'NewPass123'

# Semi-interactive — specify username, prompt for password securely
docker exec -it enhancedchannelmanager python /app/reset_password.py -u admin

# Skip password strength validation
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'simple' --force
```

Interactive mode displays a table of all users showing username, email, admin status, active status, and auth provider.

---

## Keyboard Shortcuts

ECM does not document a global select-all or modifier-click selection
contract. Use the visible channel, stream, and group checkboxes; their
accessible names announce what will be selected or cleared. Use the Channels
pane **Undo** and **Redo** controls for staged history.

For keyboard navigation, press `Tab` or `Shift+Tab` to reach a control and
`Enter` or `Space` to activate it. Menus describe their supported arrow keys;
`Escape` closes the active menu or cancels a keyboard drag. For the two-pane
separator's exact keys, see [Channel Manager mental
model](operator-workspace.md#channel-manager-mental-model).

---

## Debug Logging

ECM uses structured log prefixes in square brackets to identify which subsystem produced each log message. When you enable debug logging (Settings > General > Log Level), these tags help you quickly filter and understand log output.

### How to Read Log Lines

Each log line follows this format:

```
2026-02-19 00:48:40,031 - auto_creation_engine - INFO - [AUTO-CREATE-ENGINE] Evaluating 15771 streams against 1 rules
^                         ^                      ^      ^                    ^
timestamp                 Python module           level  subsystem tag        message
```

The **subsystem tag** (e.g., `[AUTO-CREATE-ENGINE]`) tells you exactly which part of ECM generated the message. Use these tags to filter logs with `grep` or your log viewer.

### Log Prefix Reference

#### Core Infrastructure

| Prefix | Description |
|-|-|
| `[MAIN]` | App startup, shutdown, middleware, WebSocket lifecycle |
| `[DATABASE]` | Database connections, schema migrations, queries |
| `[CONFIG]` | Configuration loading from environment variables |
| `[CACHE]` | In-memory cache operations (hits, misses, evictions) |
| `[REQUEST]` | HTTP request timing (method, path, duration, status) |
| `[SLOW-REQUEST]` | Requests exceeding the slow-request threshold |
| `[RAPID-POLLING]` | Detects clients polling the same endpoint too frequently |
| `[VALIDATION-ERROR]` | Request validation failures (malformed input) |

#### Authentication

| Prefix | Description |
|-|-|
| `[AUTH]` | Login, logout, token validation, session management |
| `[AUTH-ADMIN]` | Admin user creation, deletion, password changes |
| `[AUTH-DISPATCHARR]` | Dispatcharr SSO/OAuth authentication provider |
| `[AUTH-SETTINGS]` | Auth configuration changes (provider type, credentials) |
| `[RESET-PASSWORD]` | Password reset flow |

#### Dispatcharr Integration

| Prefix | Description |
|-|-|
| `[DISPATCHARR]` | All Dispatcharr API requests (auth, token refresh, endpoints) |

#### M3U Management

| Prefix | Description |
|-|-|
| `[M3U]` | M3U account management (add, update, delete, refresh) |
| `[M3U-REFRESH]` | M3U data refresh operations |
| `[M3U-CHANGE]` | Detecting changes between M3U refreshes (new/removed streams) |
| `[M3U-DIGEST]` | M3U content digest computation and change detection |

#### Channels & Groups

| Prefix | Description |
|-|-|
| `[CHANNELS]` | Individual channel CRUD operations |
| `[CHANNELS-BULK]` | Bulk channel operations (mass update, delete, reorder) |
| `[CHANNELS-CSV]` | CSV import and export of channel data |
| `[CHANNELS-LOGO]` | Logo fetching and assignment to channels |
| `[GROUPS]` | Channel group CRUD and reordering |
| `[GROUPS-ORPHAN]` | Handling channels not assigned to any group |

#### Streams

| Prefix | Description |
|-|-|
| `[STREAMS]` | Stream listing and management |
| `[PREVIEW]` | Stream preview and test playback |
| `[BANDWIDTH]` | Per-stream bandwidth usage tracking |
| `[POPULARITY]` | Stream popularity scoring and rankings |

#### EPG

| Prefix | Description |
|-|-|
| `[EPG]` | EPG source management (add, update, delete) |
| `[EPG-REFRESH]` | EPG data refresh operations |
| `[EPG-LCN]` | Logical channel number assignment from EPG data |

#### Channel Pipeline

| Prefix | Description |
|-|-|
| `[AUTO-CREATE]` | Auto-creation rule management (CRUD via API) |
| `[AUTO-CREATE-ENGINE]` | Core pipeline — stream fetching, rule matching, sorting, execution |
| `[AUTO-CREATE-EVAL]` | Per-condition evaluation (which streams match which rules) |
| `[AUTO-CREATE-EXEC]` | Action execution (channel creation, merging, priority changes) |
| `[AUTO-CREATE-SCHEMA]` | Rule schema validation (conditions and actions) |
| `[AUTO-CREATE-YAML]` | YAML import and export of auto-creation rules |
| `[AUTO-CREATION]` | Background task wrapper for scheduled auto-creation runs |

#### Stream Probing & Stats

| Prefix | Description |
|-|-|
| `[STREAM-PROBE]` | Active probing of stream URLs for health and metadata |
| `[STREAM-PROBE-M3U]` | M3U-specific stream probe operations |
| `[STREAM-PROBE-SORT]` | Sorting and prioritizing probe results |
| `[STREAM-STATS]` | Stream statistics API endpoints |
| `[STREAM-STATS-PROBE]` | Probe-based statistics collection |
| `[STREAM-STATS-SORT]` | Sorting streams by statistics data |

#### Normalization

| Prefix | Description |
|-|-|
| `[NORMALIZE]` | Name normalization rule evaluation and application |
| `[NORMALIZE-MIGRATE]` | Normalization rule format migration on startup |

#### FFmpeg

| Prefix | Description |
|-|-|
| `[FFMPEG]` | FFmpeg profile and preset management |
| `[FFMPEG-EXEC]` | FFmpeg process execution |
| `[FFPROBE]` | Running ffprobe to inspect stream metadata |

#### Notifications & Alerts

| Prefix | Description |
|-|-|
| `[NOTIFY]` | Notification API endpoints |
| `[NOTIFY-SVC]` | Core notification dispatch service |
| `[ALERTS]` | Alert method registry and dispatch |
| `[ALERTS-SMTP]` | Email (SMTP) alert delivery |
| `[ALERTS-TELEGRAM]` | Telegram alert delivery |
| `[ALERTS-DISCORD]` | Discord webhook alert delivery |

#### Tasks & Scheduling

| Prefix | Description |
|-|-|
| `[TASKS]` | Task management API endpoints |
| `[TASK-ENGINE]` | Background task execution engine |
| `[TASK-REGISTRY]` | Registry of available task types |
| `[TASK-SCHEDULER]` | Task scheduling and next-run calculation |
| `[CRON]` | Cron expression parsing for task schedules |
| `[SCHEDULER]` | Schedule calculation (next run times) |

#### TLS / HTTPS

| Prefix | Description |
|-|-|
| `[TLS]` | TLS certificate API and storage |
| `[TLS-ACME]` | ACME (Let's Encrypt) certificate issuance |
| `[TLS-RENEWAL]` | Automatic certificate renewal |
| `[TLS-SERVER]` | HTTPS server lifecycle |
| `[TLS-STORAGE]` | Certificate storage on disk |
| `[TLS-SETTINGS]` | TLS configuration management |
| `[TLS-ROUTE53]` | AWS Route53 DNS challenge for ACME |
| `[TLS-CLOUDFLARE]` | Cloudflare DNS challenge for ACME |

#### Other

| Prefix | Description |
|-|-|
| `[SETTINGS]` | Application settings CRUD |
| `[SETTINGS-TEST]` | Testing connectivity for configured integrations |
| `[PROFILES]` | FFmpeg/stream profile management |
| `[TAGS]` | Channel tag management |
| `[STATS]` | Aggregate statistics endpoints |
| `[JOURNAL]` | Audit and activity journal logging |
| `[MODELS]` | SQLAlchemy model events |

### Filtering Logs

To view logs from a specific subsystem, use `grep` with the tag:

```bash
# View only auto-creation engine logs
docker logs ecm-ecm-1 2>&1 | grep "\[AUTO-CREATE-ENGINE\]"

# View all authentication-related logs
docker logs ecm-ecm-1 2>&1 | grep "\[AUTH"

# View slow requests
docker logs ecm-ecm-1 2>&1 | grep "\[SLOW-REQUEST\]"

# Follow logs in real time, filtered
docker logs -f ecm-ecm-1 2>&1 | grep "\[M3U\]"
```

---

## Tips & Best Practices

### Organizing Channels
- Use descriptive group names ("Sports", "News", "Movies")
- Set logical channel number ranges (100-199 Sports, 200-299 News)
- Keep related channels together

### Managing Providers
- Link accounts from the same provider
- Use smart merging for duplicate channels
- Set stream priorities (best quality first)

### Maintaining EPG
- Prioritize most reliable EPG sources
- Use dummy EPG for channels without guide data
- Schedule EPG refreshes during off-peak hours

### Monitoring Health
- Enable stream probing
- Configure alerts for failures
- Check Stats dashboard regularly

---
