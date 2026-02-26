# Enhanced Channel Manager (ECM) User Guide

A comprehensive guide to using Enhanced Channel Manager for IPTV channel management with Dispatcharr.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [M3U Manager](#m3u-manager)
3. [M3U Change Tracking](#m3u-change-tracking)
4. [Channel Manager](#channel-manager)
5. [EPG Manager](#epg-manager)
6. [TV Guide](#tv-guide)
7. [Logo Manager](#logo-manager)
8. [Stream & Channel Preview](#stream--channel-preview)
9. [Auto-Creation Pipeline](#auto-creation-pipeline)
10. [FFMPEG Builder](#ffmpeg-builder)
11. [Journal](#journal)
12. [Stats Dashboard](#stats-dashboard)
13. [Notifications](#notifications)
14. [Settings](#settings)
15. [Authentication & Users](#authentication--users)
16. [CLI Tools](#cli-tools)
17. [Keyboard Shortcuts](#keyboard-shortcuts)
18. [Debug Logging](#debug-logging)
19. [Tips & Best Practices](#tips--best-practices)
20. [Screenshot Checklist](#screenshot-checklist)

---

## Getting Started

Enhanced Channel Manager (ECM) is a web-based interface for managing IPTV channels, EPG data, and stream configurations with Dispatcharr.

### First-Time Setup

![Application Overview](docs/images/01-app-overview.png)
<!-- Screenshot: Full application window showing the tab navigation bar at top with all tabs visible -->

**Step 1: Create Your Admin Account**

On first launch, ECM shows a setup wizard to create your administrator account.

1. Enter a **Username**
2. Enter your **Email** address
3. Choose a **Password** (minimum 8 characters, must include uppercase, lowercase, and a number)
4. Click **Create Account**

You'll be logged in automatically after setup.

**Step 2: Open Settings**

![Settings Navigation](docs/images/02-settings-nav.png)
<!-- Screenshot: Click on the Settings tab in the navigation bar, showing the settings sidebar -->

1. Click **Settings** in the top navigation bar
2. The settings sidebar will appear on the left

**Step 3: Configure Dispatcharr Connection**

![Dispatcharr Connection Settings](docs/images/03-dispatcharr-settings.png)
<!-- Screenshot: The General Settings section showing Server URL, Username, Password fields, and Test Connection button -->

1. Enter your **Server URL** (e.g., `http://192.168.1.100:5000`)
2. Enter your **Username**
3. Enter your **Password**
4. Click **Test Connection**

**Step 4: Verify Connection**

![Connection Success](docs/images/04-connection-success.png)
<!-- Screenshot: Show the green checkmark or "Connection verified" indicator after successful test -->

- A green checkmark indicates successful connection
- If connection fails, verify your URL and credentials

**Step 5: Save Settings**

1. Click **Save** to store your configuration
2. You're now ready to add M3U accounts

> **Tip:** The application header includes quick-access links to the GitHub repository and this User Guide.

---

## M3U Manager

The M3U Manager is where you add and configure your IPTV provider playlists.

### Overview

![M3U Manager Tab](docs/images/05-m3u-manager-overview.png)
<!-- Screenshot: M3U Manager tab showing 2-3 accounts in different states (Ready, Disabled, etc.) with the Add Account button visible -->

The M3U Manager displays:
- List of all configured M3U accounts
- Account status indicators
- Quick action buttons (refresh, manage groups, filters, etc.)

### Adding an M3U Account

**Step 1: Click Add Account**

![Add Account Button](docs/images/06-add-account-button.png)
<!-- Screenshot: Highlight the "Add Account" button in the M3U Manager toolbar -->

1. Click the **Add Account** button in the top toolbar

**Step 2: Choose Account Type**

![Account Type Selection](docs/images/07-account-type-modal.png)
<!-- Screenshot: The Add Account modal showing the three account type options: Standard M3U, XtreamCodes, HD Homerun -->

Choose from three account types:

| Type | Use Case |
|------|----------|
| **Standard M3U** | Direct URL to M3U playlist |
| **XtreamCodes (XC)** | XtreamCodes portal with login |
| **HD Homerun** | Local HD Homerun device |

**Step 3: Configure Standard M3U Account**

![Standard M3U Form](docs/images/08-standard-m3u-form.png)
<!-- Screenshot: The form fields for Standard M3U: Name, URL, Max Streams, Refresh Interval -->

1. Enter a **Name** for the account
2. Paste the **M3U URL** from your provider
3. Set **Max Streams** (concurrent connection limit)
4. Set **Refresh Interval** (how often to update, default 24 hours; set to 0 for manual refresh only)
5. Click **Save**

**Step 3 (Alternative): Configure XtreamCodes Account**

![XtreamCodes Form](docs/images/09-xtreamcodes-form.png)
<!-- Screenshot: The form fields for XtreamCodes: Name, Server URL, Username, Password, Max Streams -->

1. Enter a **Name** for the account
2. Enter the **Server URL** (base URL without /get.php)
3. Enter your **Username** and **Password**
4. Set **Max Streams**
5. Click **Save**

> **Tip:** When editing an existing XtreamCodes account, you can change non-credential settings (name, max streams, etc.) without re-entering the password. Leave the password field empty to keep the existing credentials.

**Step 4: Account Refreshes Automatically**

![Account Refreshing](docs/images/10-account-refreshing.png)
<!-- Screenshot: Account row showing "Downloading" or "Processing" status with spinner -->

After saving, the account automatically refreshes to load your channels.

### Understanding Account Status

![Account Status Indicators](docs/images/11-account-statuses.png)
<!-- Screenshot: Multiple accounts showing different statuses: Ready (green), Error (red), Downloading, Disabled -->

| Status | Icon | Meaning |
|--------|------|---------|
| Ready | Green check | Account loaded successfully |
| Error | Red X | Connection or parsing failed |
| Downloading | Spinner | Fetching playlist data |
| Processing | Spinner | Parsing M3U content |
| Disabled | Gray | Account turned off |

### Managing Channel Groups

**Step 1: Open Manage Groups**

![Manage Groups Button](docs/images/12-manage-groups-button.png)
<!-- Screenshot: Highlight the "Manage Groups" button on an M3U account row -->

1. Click **Manage Groups** on the account you want to configure

**Step 2: Enable/Disable Groups**

![Manage Groups Modal](docs/images/13-manage-groups-modal.png)
<!-- Screenshot: The Manage Groups modal showing a list of groups with toggle switches, some enabled, some disabled -->

1. Toggle groups **on** to make them available in Channel Manager
2. Toggle groups **off** to hide them
3. Use **Hide Disabled** to show only enabled groups

**Step 3: Configure Auto-Sync (Optional)**

![Auto-Sync Settings](docs/images/14-auto-sync-settings.png)
<!-- Screenshot: Expanded auto-sync settings for a group showing EPG override, name regex, channel profile options -->

For automatic channel creation from a group:

1. Click the **gear icon** next to a group
2. Configure auto-sync options:
   - **EPG Source Override**: Force specific EPG source
   - **Channel Group Override**: Place channels in different group
   - **Name Regex Pattern**: Transform channel names
   - **Channel Profile**: Assign default profile
3. Click **Save**

### Refreshing M3U Data

![Refresh Button](docs/images/15-refresh-button.png)
<!-- Screenshot: Highlight the refresh icon button on an account row -->

- Click the **refresh icon** on any account to update its playlist
- Use **Refresh All** in the toolbar to refresh all accounts

### M3U Filters

![Filters Modal](docs/images/16-m3u-filters.png)
<!-- Screenshot: The Filters modal showing a list of filters with Type (Group/Name/URL), Action (Include/Exclude), and Pattern columns -->

Filters let you include or exclude streams:

1. Click **Manage Filters** on an account
2. Click **Add Filter**
3. Choose **Type**: Group, Name, or URL
4. Choose **Action**: Include or Exclude
5. Enter a **Regex Pattern**
6. Drag filters to reorder (executed top to bottom)

---

## M3U Change Tracking

The M3U Changes tab tracks all changes detected in your M3U playlists over time.

### Overview

![M3U Changes Tab](docs/images/61-m3u-changes-overview.png)
<!-- Screenshot: M3U Changes tab showing summary cards and change list -->

Every time an M3U account is refreshed, ECM compares the new data against the previous snapshot and records any differences.

### Summary Statistics

At the top of the tab, dashboard cards show:
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

![Channel Manager Overview](docs/images/17-channel-manager-overview.png)
<!-- Screenshot: Full Channel Manager view showing left pane (channels by group) and right pane (streams), with the divider visible -->

The screen is split into two panes:
- **Left Pane**: Your channel lineup organized by groups
- **Right Pane**: Available streams from M3U providers
- **Divider**: Drag to resize panes

### Creating Channels - Method 1: Drag and Drop

**Step 1: Find a Stream**

![Stream List](docs/images/18-stream-list.png)
<!-- Screenshot: Right pane showing stream list with provider filter dropdown and search box -->

1. Use the **M3U Account** dropdown to filter by M3U account
2. Use **Search** to find specific streams

**Step 2: Drag to Create Channel**

![Drag to Create](docs/images/19-drag-to-create.png)
<!-- Screenshot: Show a stream being dragged from right pane toward a channel group header, with drop indicator visible -->

1. Click and drag a stream from the right pane
2. Drop it on a **group header** in the left pane
3. A new channel is created with the stream's name and logo

### Creating Channels - Method 2: Bulk Creation

**Step 1: Select Stream Groups**

![Select Stream Groups](docs/images/20-select-stream-groups.png)
<!-- Screenshot: Right pane showing multiple stream groups selected (highlighted) with Ctrl+Click -->

1. Click a stream group header to select it
2. **Ctrl+Click** to select additional groups

**Step 2: Drag to Left Pane**

![Bulk Create Drag](docs/images/21-bulk-create-drag.png)
<!-- Screenshot: Multiple selected groups being dragged to left pane, showing drop zone indicator -->

1. Drag the selected groups to the left pane
2. The bulk creation modal appears

**Step 3: Configure Bulk Creation**

![Bulk Create Modal](docs/images/22-bulk-create-modal.png)
<!-- Screenshot: Bulk creation modal showing: Starting Number field, Group Selection dropdown, Channel Profile dropdown, and preview list of channels to be created -->

1. Set **Starting Channel Number**
2. Choose **Group Selection**:
   - Same-named group (creates matching group)
   - Select existing group
   - Create new group
3. Select **Channel Profile** (optional)
4. Review the **preview** of channels to be created

**Step 4: Review and Create**

![Bulk Create Preview](docs/images/23-bulk-create-preview.png)
<!-- Screenshot: The preview section showing list of channels with their assigned streams and quality indicators -->

1. Review the channel list
2. Note the **stream count** per channel (merged duplicates)
3. Click **Create Channels**

### Smart Stream Merging

![Merged Streams](docs/images/24-merged-streams.png)
<!-- Screenshot: An expanded channel showing multiple streams from different providers, ordered by quality -->

ECM automatically:
- **Merges duplicates**: Same channel from different providers
- **Orders by quality**: UHD → 4K → FHD → 1080p → HD → 720p → SD
- **Interleaves providers**: For failover redundancy

### Using Edit Mode

**Step 1: Enter Edit Mode**

![Edit Mode Button](docs/images/25-edit-mode-button.png)
<!-- Screenshot: Highlight the "Edit Mode" toggle button in the Channel Manager toolbar -->

1. Click **Edit Mode** to enable staged editing
2. All changes are now local until you commit

**Step 2: Make Changes**

![Edit Mode Active](docs/images/26-edit-mode-active.png)
<!-- Screenshot: Channel Manager in edit mode showing: modified indicator on channels, undo/redo buttons visible, pending changes count -->

In edit mode you can:
- Edit channel numbers (click to edit)
- Edit channel names (click to edit)
- Add/remove/reorder streams
- Delete channels (recoverable with undo)
- Move channels between groups

**Step 3: Use Undo/Redo**

![Undo Redo](docs/images/27-undo-redo.png)
<!-- Screenshot: Close-up of undo/redo buttons with tooltip showing "Undo: Delete channel ESPN" -->

- Press **Ctrl+Z** to undo
- Press **Ctrl+Shift+Z** to redo

**Step 4: Exit and Commit**

![Exit Edit Mode Dialog](docs/images/28-exit-edit-dialog.png)
<!-- Screenshot: The exit edit mode dialog showing summary of all changes: X channels modified, Y streams added, Z channels deleted, etc. -->

1. Click **Exit Edit Mode**
2. Review the summary of all changes
3. Click **Commit** to save or **Discard** to cancel

### Multi-Select Operations

**Step 1: Select Multiple Channels**

![Multi Select](docs/images/29-multi-select.png)
<!-- Screenshot: Multiple channels selected (checkboxes checked, rows highlighted) -->

- **Ctrl+Click**: Toggle individual selection
- **Shift+Click**: Select range
- **Ctrl+A**: Select all visible

**Step 2: Right-Click for Options**

![Context Menu](docs/images/30-context-menu.png)
<!-- Screenshot: Right-click context menu showing: Move to Group (with submenu of groups), Create New Group & Move -->

1. Right-click on selected channels
2. Choose **Move to Group** and select destination
3. Or choose **Create New Group & Move**

### Managing Streams Within Channels

![Channel Streams](docs/images/31-channel-streams.png)
<!-- Screenshot: Expanded channel showing its streams with: drag handles for reordering, X buttons for removal, quality badges -->

- **Add**: Drag streams from right pane onto channel
- **Remove**: Click the **X** on a stream
- **Reorder**: Drag streams up/down (higher = higher priority)

### Sort & Renumber

Sort and renumber channels within a group:

1. Right-click a group header (or use the group menu)
2. Choose **Sort & Renumber**
3. Options:
   - **Alphabetical Sort** - Sort channels A-Z
   - **Smart Name Sorting** - Ignores channel number prefixes when sorting (e.g., "101 | Sports" sorts as "Sports")
   - **Sequential Renumber** - Assign sequential numbers starting from any value
4. Preview the result before applying
5. The entire operation undoes as one action with Ctrl+Z

### Copy Channel & Stream URLs

- Click the **copy icon** on any channel to copy its Dispatcharr proxy stream URL
- Click the **copy icon** on any stream to copy its direct URL
- Useful for testing streams in external players

### Channel Profiles

- View and manage stream transcoding profiles
- Set a **default channel profile** in Settings → Channel Defaults
- Select profiles when creating channels (single or bulk)
- Assign profiles to existing channels via the edit modal

### Filtering Channels and Streams

![Filter Options](docs/images/32-filter-options.png)
<!-- Screenshot: Filter panel expanded showing: Group checkboxes, "Hide Empty Groups", "Hide Provider Groups", Search box -->

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

![EPG Manager Overview](docs/images/33-epg-manager-overview.png)
<!-- Screenshot: EPG Manager tab showing list of EPG sources with status, channel count, last updated, and action buttons -->

### Adding an EPG Source

**Step 1: Click Add Source**

![Add EPG Source](docs/images/34-add-epg-source.png)
<!-- Screenshot: Add Source button highlighted -->

1. Click **Add Source** in the toolbar

**Step 2: Configure XMLTV Source**

![XMLTV Form](docs/images/35-xmltv-form.png)
<!-- Screenshot: Add EPG Source modal with Name field and URL field for XMLTV -->

1. Enter a **Name** for the source
2. Paste the **XMLTV URL**
3. Set **Refresh Interval** (hours between updates; set to 0 for manual refresh only)
4. Click **Save**

**Step 3: Source Refreshes**

![EPG Refreshing](docs/images/36-epg-refreshing.png)
<!-- Screenshot: EPG source showing "Fetching" or "Parsing" status with progress -->

The source automatically fetches and parses EPG data.

### Setting Source Priority

![EPG Priority](docs/images/37-epg-priority.png)
<!-- Screenshot: EPG sources with drag handles, showing one being dragged to reorder -->

1. Drag sources up/down to change priority
2. Higher sources take precedence for channel matching

### Creating Dummy EPG

![Dummy EPG Form](docs/images/38-dummy-epg-form.png)
<!-- Screenshot: Dummy EPG configuration form showing: Name source, Title template, Description template, Duration, Timezone fields -->

For channels without guide data:

1. Add a **Dummy EPG** source
2. Configure:
   - **Name Source**: Channel or stream name
   - **Title Template**: Use `{name}` placeholder
   - **Duration**: Program length in minutes
   - **Timezone**: Event timezone
3. Click **Save**

### Bulk EPG Assignment

**Step 1: Select Channels**

![Select for EPG](docs/images/39-select-for-epg.png)
<!-- Screenshot: Channel Manager with multiple channels selected -->

1. In Channel Manager, select channels needing EPG
2. Click **Assign EPG** in the toolbar

**Step 2: Review Matches**

![EPG Matching](docs/images/40-epg-matching.png)
<!-- Screenshot: Bulk EPG assignment modal showing: auto-matched channels (green), conflicts requiring review, unmatched channels -->

1. Auto-matched channels show in green
2. Conflicts show multiple options to choose from
3. Unmatched channels need manual assignment

**Step 3: Resolve Conflicts**

![EPG Conflict](docs/images/41-epg-conflict.png)
<!-- Screenshot: Conflict resolution card showing multiple EPG options for a channel, with radio buttons to select -->

1. For each conflict, select the correct EPG entry
2. Click **Apply** when done

---

## TV Guide

The Guide tab displays your EPG data in a grid format.

### Guide Overview

![Guide Overview](docs/images/42-guide-overview.png)
<!-- Screenshot: Full TV Guide showing: time header, channel list on left, program grid, red "now" line, currently playing highlighted -->

Features:
- Time header with current time indicator (red line)
- Channel list on left
- Program grid with 6-hour window
- Currently airing programs highlighted

### Navigation

![Guide Navigation](docs/images/43-guide-navigation.png)
<!-- Screenshot: Guide header showing: date picker, hour selector buttons, profile filter dropdown -->

- **Date Picker**: Browse different days
- **Hour Buttons**: Jump to specific times
- **Profile Filter**: Show specific channel profiles

### Viewing Program Details

![Program Hover](docs/images/44-program-hover.png)
<!-- Screenshot: Hovering over a program showing tooltip with: full title, subtitle, time range, description -->

- Hover over any program to see full details
- Click a channel to edit its settings

### Print Guide

![Print Guide](docs/images/45-print-guide.png)
<!-- Screenshot: Print Guide modal showing group selection checkboxes and display mode options -->

1. Click **Print Guide**
2. Select groups to include
3. Choose display mode
4. Use browser print function

---

## Logo Manager

### Logo Library

![Logo Manager](docs/images/46-logo-manager.png)
<!-- Screenshot: Logo Manager showing grid of logos with search box, pagination, and usage counts -->

- Browse all logos with previews
- Toggle between list and grid view
- **Search** logos by name
- See usage count per logo
- **Pagination** - Choose page size (25, 50, 100, 250) with page navigation
- All logos are automatically loaded by paginating through Dispatcharr's API

### Adding Logos

![Add Logo](docs/images/47-add-logo.png)
<!-- Screenshot: Add Logo modal with URL input field or file upload option -->

- **From URL**: Enter image URL
- **Upload**: Upload image files directly to Dispatcharr

---

## Stream & Channel Preview

Preview streams and channels directly in your browser before assigning them.

### Previewing a Stream

1. Click the **play icon** on any stream in the streams pane
2. The preview modal opens with embedded video player
3. Stream metadata is displayed (name, TVG-ID, group, M3U provider)

### Previewing a Channel

1. Click the **play icon** on any channel
2. This tests the actual Dispatcharr proxy stream output
3. Verifies the channel is working correctly end-to-end

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

## Auto-Creation Pipeline

The Auto-Creation tab lets you automate channel creation with a rules-based engine. Define conditions to match streams and actions to create channels, merge streams, and assign metadata automatically.

### Creating a Rule

1. Click **Add Rule** in the Auto-Creation tab
2. Enter a **Rule Name** and optional **Description**
3. Configure **Conditions** to match streams
4. Configure **Actions** to define what happens
5. Click **Save**

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
| **EPG Title (Today)** | contains, matches (regex) |
| **EPG Description (Today)** | contains, matches (regex) |
| **EPG Today (Any)** | contains, matches (regex) - Checks both Title and Description |
| **EPG Source** | is, is not - Filter to specific Dispatcharr EPG provider |
| **Any Field (Name/EPG)** | contains, matches (regex) - Checks Name, EPG Title, and Description |
| **Channel Exists** | by name, regex, or group |
| **Normalized Match in Group** | stream's normalized name matches a channel in a specified group |
| **Normalized Name (Global)** | stream's normalized name matches any channel across all groups |
| **Normalized Name (Not In)** | stream's normalized name does NOT match any channel in a specified group |

#### EPG-Based Matching (Today)

EPG conditions evaluate against all programs scheduled for the current UTC day (00:00 to 23:59). When a match is found via EPG, specific program metadata is captured and made available for naming templates.

**EPG Source Filtering:** Use the **EPG Source** condition to restrict matches to a specific provider. This is highly recommended when using keyword-based EPG searches to avoid false positives from different guide sources.

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

### Actions

Define what happens when conditions match:

| Action | Description |
|--------|-------------|
| **Create Channel** | Template-based naming with support for primary and fallback templates |
| **Create Group** | Automatically create a channel group with fallback support |
| **Merge Streams** | Combine multiple streams into one channel with quality preference; auto-find uses multi-stage lookup (normalized name → core-name → call sign → deparen/word-prefix); optional max streams per provider limit |
| **Assign Logo** | Set channel logo from stream or URL |
| **Assign EPG** | Assign EPG data source |
| **Assign Profile** | Set stream transcoding profile |
| **Set Channel Number** | Auto-assign or specify number/range |
| **Set Variable** | Define reusable variables with regex extraction |
| **Name Transform** | Apply regex find/replace to channel names |
| **Skip / Stop** | Skip stream or stop processing further rules |

#### Naming Templates and Variables

Actions like **Create Channel** and **Create Group** support dynamic naming using placeholders:

- `{stream_name}` - Original name of the matched stream
- `{stream_group}` - Original M3U group name
- `{quality}` - Stream resolution (e.g., 1080p)
- `{provider}` - M3U account name
- `{epg_match_title}` - Title of the specific program that triggered an EPG match
- `{epg_match_desc}` - Description of the matched program
- `{epg_match_start}` - Start time of the matched program (`HH:MM`)
- `{epg_match_stop}` - Stop time of the matched program (`HH:MM`)

**Fallback Name Template:** When using multi-field conditions (like **Any Field**), a stream might match via its original name instead of an EPG program. The **Fallback Name Template** allows you to specify a different naming format for these cases (e.g., use `{stream_name}` if no EPG match is found, but use `{epg_match_start} - {epg_match_title}` if it was an EPG match).

- **Smart Fallback:** If you use the **EPG Source** condition, the engine will automatically capture the program *currently airing* on that source even if your keyword didn't match it. This ensures your `{epg_match_*}` variables still have valid data if the stream was matched via its name.

#### Debugging Rules and False Positives

When a rule matches a stream, you can click the **Check** icon in the **Execution Log** to see exactly why it matched. 

- **Match Segments:** For Regex and Substring matches, the log will show exactly which part of the text matched (e.g., `Matched 'Rugby'`). This is extremely helpful for finding typos in long regex strings that might cause false positives (like matching "UEFA" because of a stray character).

**Group-Aware Matching:** The pipeline is fully group-aware. If you target a specific group for channel creation, the engine will only check for existing channels within that group, allowing you to have duplicate channel names in different parts of your lineup without conflicts.

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

Configure stream exclusion filters in the Auto-Creation settings (gear icon at the top of the Auto-Creation tab):

- **M3U Group Dropdown** - Select which M3U groups to include in rule evaluation
- **Exclusion Patterns** - Define regex patterns to exclude streams before any rules are evaluated
- Exclusion filters apply globally to all rules, saving you from repeating the same exclusion conditions in every rule

### YAML Import/Export

- **Export** - Download all rules as YAML for backup or sharing
- **Import** - Paste YAML rule definitions to create rules
- Useful for version control and sharing configurations between instances

---

## FFMPEG Builder

The FFMPEG Builder tab provides a visual interface for constructing FFmpeg transcoding and streaming commands without writing command-line syntax. It's designed for IPTV streaming workflows but supports any FFmpeg use case.

### Simple Mode (IPTV Wizard)

![FFMPEG Simple Mode](docs/images/76-ffmpeg-simple-mode.png)
<!-- Screenshot: FFMPEG Builder in Simple Mode showing the three-step wizard: Source, Processing, Output -->

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

![FFMPEG Advanced Mode](docs/images/77-ffmpeg-advanced-mode.png)
<!-- Screenshot: FFMPEG Builder in Advanced Mode showing all configuration sections expanded -->

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

![FFMPEG Command Preview](docs/images/78-ffmpeg-command-preview.png)
<!-- Screenshot: Command Preview panel showing annotated view with color-coded flags and tooltips -->

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

![Journal Overview](docs/images/48-journal-overview.png)
<!-- Screenshot: Journal tab showing activity list with: color-coded entries, category icons, timestamps, expand buttons -->

The Journal tracks all changes:
- Channel operations (create, update, delete)
- EPG changes
- M3U operations
- Watch events

### Filtering

![Journal Filters](docs/images/49-journal-filters.png)
<!-- Screenshot: Journal filter bar showing: Category dropdown, Action Type dropdown, Time Range dropdown, Search box -->

- **Category**: Channel, EPG, M3U, Watch
- **Action Type**: Create, Update, Delete, etc.
- **Time Range**: Last hour to all time
- **Search**: Full-text search

### Entry Details

![Journal Entry](docs/images/50-journal-entry.png)
<!-- Screenshot: Expanded journal entry showing before/after values in JSON format -->

Click any entry to see full details including before/after values.

---

## Stats Dashboard

### Live Statistics

![Stats Overview](docs/images/51-stats-overview.png)
<!-- Screenshot: Stats tab showing: active channels list with metrics, connection counts, bandwidth chart -->

Monitor in real-time:
- Active streaming channels
- FFmpeg speed (color-coded)
- Bitrate and FPS
- Connection counts per M3U account

### Channel Metrics

![Channel Metrics](docs/images/52-channel-metrics.png)
<!-- Screenshot: Close-up of channel stats showing: speed indicator (green/yellow/red), FPS, bitrate, duration -->

| Metric | Meaning |
|--------|---------|
| Speed (green) | ≥0.98x - Excellent |
| Speed (yellow) | ≥0.90x - Acceptable |
| Speed (red) | <0.90x - Buffering likely |

### Historical Charts

![Stats Charts](docs/images/53-stats-charts.png)
<!-- Screenshot: Expanded channel showing speed and bandwidth charts over time -->

Click any channel to expand and see:
- Speed over time
- Bandwidth usage trends

### Auto-Refresh

![Refresh Settings](docs/images/54-refresh-settings.png)
<!-- Screenshot: Auto-refresh dropdown showing options: Manual, 10s, 30s, 1m, 5m -->

Set refresh interval:
- Manual
- 10 seconds
- 30 seconds
- 1 minute
- 5 minutes

Polling automatically pauses when the browser tab is hidden to save resources.

### Enhanced Stats (Popularity & Analytics)

The Stats tab also includes advanced analytics:

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

Access the notification center from the **bell icon** in the header bar.

- **Unread Badge** - Shows count of unread notifications
- **Notification List** - View past notifications with timestamps
- **Mark as Read** - Mark individual or all notifications as read
- **Delete** - Clear individual or all notifications
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

![Settings Sidebar](docs/images/55-settings-sidebar.png)
<!-- Screenshot: Settings page showing sidebar navigation with all sections -->

The Settings sidebar contains the following sections:
- **General** - Dispatcharr connection
- **Tag-Based Normalization** - Tag stripping for stream name cleanup
- **Normalization Engine** - Rule-based name transformations
- **Stream Probing** - Automated stream health checks
- **Stream Sort Priority** - Configure stream ordering criteria
- **Stream Preview** - Preview mode configuration
- **Channel Defaults** - Default options for bulk channel creation
- **Appearance** - Theme, visibility toggles, log level
- **VLC Integration** - VLC protocol handler setup
- **Scheduled Tasks** - Automated background tasks
- **Alert Methods** - External notification configuration
- **Maintenance** - Stream strikeout management and bulk operations
- **Authentication** - Login requirements and providers
- **Users** - User account management (admin only)

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

![Probe Settings](docs/images/56-probe-settings.png)
<!-- Screenshot: Stream Probing settings showing: Enable toggle, Schedule (start time, interval), Performance limits, Channel group filter -->

Configure automated stream health checking:

1. **Enable** stream probing
2. Set **Start Time** (e.g., 03:00 for off-peak)
3. Set **Interval** (hours between probes)
4. Configure **Batch Size** and **Timeout**
5. Select **Channel Groups** to probe
6. Configure **Retry Settings** - Set how many times to retry failed probes before marking a stream as failed

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

### Stream Strikeout System

The strikeout system helps you identify and clean up streams that consistently fail probe checks.

**How It Works:**
1. Each stream tracks its **consecutive probe failures** — the counter resets when a probe succeeds
2. When a stream exceeds the configurable **strike threshold** (set in Settings → Maintenance), it is flagged as "struck out"
3. **Strike badges** appear on struck-out streams in the Channel Manager, showing the failure count
4. Review all struck-out streams in **Settings → Maintenance** with details about each stream and its failure history
5. Use **Bulk Remove** to remove all struck-out streams from every channel they're assigned to in one click

This is useful for cleaning up dead or unreliable streams that accumulate over time, especially after provider changes or M3U updates.

### Stream Sort Priority

![Sort Priority](docs/images/57-sort-priority.png)
<!-- Screenshot: Sort Priority settings showing draggable criteria: Resolution, Bitrate, Framerate with toggle switches -->

1. Drag criteria to set priority order
2. Toggle individual criteria on/off
3. Enable "Deprioritize Failed Streams"

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

![Normalization Engine](docs/images/60-normalization-engine.png)
<!-- Screenshot: Normalization Engine settings showing rule list with conditions, actions, and test panel -->

Create custom rules to automatically transform stream names:

**Step 1: Create a Rule**

1. Click **Add Rule** to create a new normalization rule
2. Enter a descriptive **Rule Name**
3. Configure the **Condition** (when the rule applies)
4. Configure the **Action** (what transformation to apply)

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

![Scheduled Tasks](docs/images/58-scheduled-tasks.png)
<!-- Screenshot: Scheduled Tasks section showing task list with schedules, enable toggles, and Run Now buttons -->

Configure automated tasks:

1. Click **Add Schedule** on a task
2. Choose schedule type (Interval, Daily, Weekly, etc.)
3. Set the timing
4. Enable/disable as needed

### Alert Methods Configuration

![Alert Methods](docs/images/59-alert-methods.png)
<!-- Screenshot: Alert Methods section showing Discord, Telegram, Email options with configuration fields -->

1. Click **Add Method**
2. Choose type (Discord, Telegram, Email)
3. Enter configuration (webhook URL, bot token, SMTP settings)
4. Select notification types to receive
5. **Test** the configuration

---

## Authentication & Users

### Login

When authentication is enabled, ECM requires login to access the application.

- Enter your **Username** and **Password**
- Or click **Login with Dispatcharr** to use your Dispatcharr credentials (SSO)
- Sessions are maintained with automatic token refresh

### Authentication Settings

Configure in Settings → Authentication:

- **Require Authentication** - Enable or disable login requirement
- **Primary Auth Mode** - Choose Local or Dispatcharr as the primary method
- **Local Authentication** - Enable/disable username/password login
- **Dispatcharr Authentication** - Enable/disable Dispatcharr SSO

### User Management (Admin)

Administrators can manage users in Settings → Users:

- **View Users** - See all accounts with username, email, provider, status, and role
- **Edit Users** - Modify email, display name, admin status, and active status
- **Activate/Deactivate** - Toggle user account status
- **Delete Users** - Remove accounts (soft delete)

### Account Linking

Users can link multiple authentication methods to a single account (e.g., local password + Dispatcharr SSO). This allows logging in with either method.

### Password Reset

**Via Email (SMTP Required):**
1. Click "Forgot password?" on the login page
2. Enter your email address
3. Check email for a reset link (valid 1 hour)

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

| Shortcut | Action |
|----------|--------|
| `Ctrl+Z` / `Cmd+Z` | Undo |
| `Ctrl+Shift+Z` / `Cmd+Shift+Z` | Redo |
| `Ctrl+A` / `Cmd+A` | Select all visible items |
| `Shift+Click` | Select range |
| `Ctrl+Click` / `Cmd+Click` | Toggle selection |
| `Escape` | Clear selection / Close menu |
| `Enter` | Save inline edit |

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

#### Auto-Creation Pipeline

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

## Screenshot Checklist

Use this checklist to capture all screenshots for the guide:

### Getting Started (4 screenshots)
- [ ] `01-app-overview.png` - Full app with all tabs visible
- [ ] `02-settings-nav.png` - Settings tab in navigation
- [ ] `03-dispatcharr-settings.png` - Connection settings form
- [ ] `04-connection-success.png` - Successful connection indicator

### M3U Manager (12 screenshots)
- [ ] `05-m3u-manager-overview.png` - M3U tab with multiple accounts
- [ ] `06-add-account-button.png` - Add Account button
- [ ] `07-account-type-modal.png` - Account type selection
- [ ] `08-standard-m3u-form.png` - Standard M3U form fields
- [ ] `09-xtreamcodes-form.png` - XtreamCodes form fields
- [ ] `10-account-refreshing.png` - Account in refreshing state
- [ ] `11-account-statuses.png` - Various account statuses
- [ ] `12-manage-groups-button.png` - Manage Groups button
- [ ] `13-manage-groups-modal.png` - Groups list with toggles
- [ ] `14-auto-sync-settings.png` - Auto-sync configuration
- [ ] `15-refresh-button.png` - Refresh button on account
- [ ] `16-m3u-filters.png` - Filters modal

### Channel Manager (16 screenshots)
- [ ] `17-channel-manager-overview.png` - Full split-pane view
- [ ] `18-stream-list.png` - Right pane stream list
- [ ] `19-drag-to-create.png` - Dragging stream to create channel
- [ ] `20-select-stream-groups.png` - Multiple groups selected
- [ ] `21-bulk-create-drag.png` - Dragging groups to left pane
- [ ] `22-bulk-create-modal.png` - Bulk creation options
- [ ] `23-bulk-create-preview.png` - Preview of channels to create
- [ ] `24-merged-streams.png` - Channel with merged streams
- [ ] `25-edit-mode-button.png` - Edit Mode toggle
- [ ] `26-edit-mode-active.png` - Active edit mode indicators
- [ ] `27-undo-redo.png` - Undo/redo buttons
- [ ] `28-exit-edit-dialog.png` - Exit edit mode summary
- [ ] `29-multi-select.png` - Multiple channels selected
- [ ] `30-context-menu.png` - Right-click menu
- [ ] `31-channel-streams.png` - Channel's stream list
- [ ] `32-filter-options.png` - Filter panel

### EPG Manager (9 screenshots)
- [ ] `33-epg-manager-overview.png` - EPG sources list
- [ ] `34-add-epg-source.png` - Add Source button
- [ ] `35-xmltv-form.png` - XMLTV source form
- [ ] `36-epg-refreshing.png` - Source refreshing
- [ ] `37-epg-priority.png` - Dragging to reorder sources
- [ ] `38-dummy-epg-form.png` - Dummy EPG configuration
- [ ] `39-select-for-epg.png` - Selecting channels for EPG
- [ ] `40-epg-matching.png` - Bulk EPG matching results
- [ ] `41-epg-conflict.png` - Conflict resolution

### TV Guide (4 screenshots)
- [ ] `42-guide-overview.png` - Full guide grid
- [ ] `43-guide-navigation.png` - Date/time navigation
- [ ] `44-program-hover.png` - Program details tooltip
- [ ] `45-print-guide.png` - Print guide modal

### Logo Manager (2 screenshots)
- [ ] `46-logo-manager.png` - Logo grid view
- [ ] `47-add-logo.png` - Add logo modal

### Journal (3 screenshots)
- [ ] `48-journal-overview.png` - Journal activity list
- [ ] `49-journal-filters.png` - Filter bar
- [ ] `50-journal-entry.png` - Expanded entry details

### Stats (4 screenshots)
- [ ] `51-stats-overview.png` - Stats dashboard
- [ ] `52-channel-metrics.png` - Channel metric details
- [ ] `53-stats-charts.png` - Historical charts
- [ ] `54-refresh-settings.png` - Auto-refresh dropdown

### M3U Change Tracking (1 screenshot)
- [ ] `61-m3u-changes-overview.png` - M3U Changes tab with summary cards and change list

### Stream & Channel Preview (1 screenshot)
- [ ] `62-stream-preview.png` - Stream preview modal with video player

### Auto-Creation Pipeline (3 screenshots)
- [ ] `63-auto-creation-overview.png` - Rules list with statistics
- [ ] `64-auto-creation-rule-builder.png` - Rule builder with conditions and actions
- [ ] `65-auto-creation-execution-log.png` - Execution results with per-stream log

### Notifications (1 screenshot)
- [ ] `66-notification-center.png` - Notification bell and dropdown

### Settings (12 screenshots)
- [ ] `55-settings-sidebar.png` - Settings navigation
- [ ] `56-probe-settings.png` - Stream probing config
- [ ] `57-sort-priority.png` - Sort priority settings
- [ ] `58-scheduled-tasks.png` - Task scheduler
- [ ] `59-alert-methods.png` - Alert configuration
- [ ] `60-normalization-engine.png` - Normalization rules and test panel
- [ ] `67-tag-normalization.png` - Tag-based normalization with tag groups
- [ ] `68-channel-defaults.png` - Channel default settings
- [ ] `69-appearance.png` - Theme and visibility settings
- [ ] `70-vlc-integration.png` - VLC protocol handler setup
- [ ] `71-authentication-settings.png` - Authentication configuration
- [ ] `72-user-management.png` - User list and management

### FFMPEG Builder (3 screenshots)
- [ ] `76-ffmpeg-simple-mode.png` - Simple Mode with IPTV wizard (Source, Processing, Output)
- [ ] `77-ffmpeg-advanced-mode.png` - Advanced Mode with all configuration sections
- [ ] `78-ffmpeg-command-preview.png` - Command Preview with annotated view and tooltips

### Authentication (2 screenshots)
- [ ] `73-login-page.png` - Login page with local and Dispatcharr options
- [ ] `74-setup-wizard.png` - First-run admin account setup

### CLI Tools (1 screenshot)
- [ ] `75-password-reset-cli.png` - Interactive password reset terminal output

**Total: 78 screenshots**

---

*Enhanced Channel Manager - Professional IPTV Channel Management*
